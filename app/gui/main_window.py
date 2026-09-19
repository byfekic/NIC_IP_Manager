"""The main application window: wiring the GUI to the network engine.

The window owns the operation lock and guarantees that only one network
operation is ever in flight (specification sections 26 and 27). It never
executes PowerShell itself; everything goes through NetworkManager.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app import __app_name__, __version__
from app.gui.adapter_panel import AdapterPanel
from app.gui.dialogs import (
    AboutDialog,
    AdapterMissingDialog,
    ConfirmDialog,
    DryRunDialog,
    EditPresetDialog,
    ErrorDialog,
    PresetDialog,
    ProgressDialog,
    RecoveryDialog,
    RenameDialog,
    ResultDialog,
)
from app.gui.history_panel import HistoryPanel
from app.gui.preset_panel import PresetPanel
from app.gui.styles import PALETTES, Palette, build_stylesheet, status_color
from app.gui.widgets import ICON_ACTIVE, ICON_WARN, Divider
from app.gui.workers import OperationLock, TaskRunner
from app.models.adapter import Adapter
from app.models.configuration import (
    IPConfiguration,
    OperationResult,
    ResultStatus,
)
from app.network.ip_manager import NetworkManager
from app.network.validator import validate
from app.security.elevation import (
    elevation_status_text,
    is_elevated,
    relaunch_as_admin,
    windows_version,
)
from app.storage.history import HistoryStore
from app.storage.presets import PresetStore, resolve_preset_adapter
from app.storage.settings import Settings
from app.utils.errors import IPChangerError
from app.utils.logging_setup import get_logger
from app.utils.paths import data_dir, log_dir

log = get_logger(__name__)


class MainWindow(QMainWindow):
    """Primary window. One screen, one task: configure the selected adapter."""

    def __init__(
        self,
        manager: NetworkManager,
        presets: PresetStore,
        history: HistoryStore,
        settings: Settings,
    ) -> None:
        super().__init__()
        self.manager = manager
        self.presets = presets
        self.history = history
        self.settings = settings

        self.tasks = TaskRunner()
        self.lock = OperationLock()
        self._adapters: list[Adapter] = []
        self._progress_dialog: Optional[ProgressDialog] = None
        self._last_snapshot = None

        self.palette_name = settings.get("theme", "dark")
        self.palette: Palette = PALETTES.get(self.palette_name, PALETTES["dark"])

        self.setWindowTitle(f"{__app_name__}  {__version__}")
        self.setMinimumSize(1020, 720)
        self.resize(
            int(settings.get("window_width", 1120)),
            int(settings.get("window_height", 820)),
        )
        x, y = int(settings.get("window_x", -1)), int(settings.get("window_y", -1))
        if x >= 0 and y >= 0:
            self.move(x, y)

        self._build_ui()
        self._install_shortcuts()
        self.apply_theme(self.palette_name)

        QTimer.singleShot(0, self.refresh_adapters)
        QTimer.singleShot(120, self._check_pending_operation)

    # =====================================================================
    # Construction
    # =====================================================================
    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(24, 18, 24, 14)
        root.setSpacing(14)

        root.addLayout(self._build_header())
        root.addWidget(Divider())

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(18)

        self.adapter_panel = AdapterPanel(self.palette)
        self.adapter_panel.refreshRequested.connect(self.refresh_adapters)
        self.adapter_panel.applyRequested.connect(self.apply_configuration)
        self.adapter_panel.savePresetRequested.connect(self.save_preset)
        self.adapter_panel.dryRunRequested.connect(self.show_dry_run)
        self.adapter_panel.adapterChanged.connect(self._on_adapter_changed)
        self.adapter_panel.configurationEdited.connect(self._update_apply_state)

        # The working column scrolls. Without this, a short window squeezes the
        # address fields below their minimum height and clips the text.
        left = QScrollArea()
        left.setObjectName("WorkColumn")
        left.setWidgetResizable(True)
        left.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left.setFrameShape(QFrame.NoFrame)
        left_content = QWidget()
        left_layout = QVBoxLayout(left_content)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.addWidget(self.adapter_panel)
        left.setWidget(left_content)

        left_column = QWidget()
        left_column_layout = QVBoxLayout(left_column)
        left_column_layout.setContentsMargins(0, 0, 0, 0)
        left_column_layout.setSpacing(14)
        left_column_layout.addWidget(left, 1)
        left_column_layout.addWidget(self.adapter_panel.actions_widget)

        self.side_tabs = QTabWidget()
        self.preset_panel = PresetPanel(self.palette)
        self.preset_panel.applyRequested.connect(self.apply_preset)
        self.preset_panel.loadRequested.connect(self.load_preset)
        self.preset_panel.editRequested.connect(self.edit_preset)
        self.preset_panel.renameRequested.connect(self.rename_preset)
        self.preset_panel.duplicateRequested.connect(self.duplicate_preset)
        self.preset_panel.deleteRequested.connect(self.delete_preset)
        self.preset_panel.importRequested.connect(self.import_presets)
        self.preset_panel.exportRequested.connect(self.export_presets)

        self.history_panel = HistoryPanel(self.palette)
        self.history_panel.clearRequested.connect(self.clear_history)

        presets_container = QWidget()
        presets_layout = QVBoxLayout(presets_container)
        presets_layout.setContentsMargins(6, 12, 2, 2)
        presets_layout.addWidget(self.preset_panel)

        history_container = QWidget()
        history_layout = QVBoxLayout(history_container)
        history_layout.setContentsMargins(6, 12, 2, 2)
        history_layout.addWidget(self.history_panel)

        self.side_tabs.addTab(presets_container, "Presets")
        self.side_tabs.addTab(history_container, "History")

        splitter.addWidget(left_column)
        splitter.addWidget(self.side_tabs)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([640, 440])
        root.addWidget(splitter, 1)

        self.setCentralWidget(central)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setSpacing(12)

        title = QLabel(__app_name__)
        title.setObjectName("AppTitle")
        header.addWidget(title)

        version = QLabel(f"v{__version__}")
        version.setObjectName("FaintLabel")
        header.addWidget(version)
        header.addStretch(1)

        self.admin_label = QLabel()
        self.admin_label.setToolTip(
            "Administrator privileges are required to change network settings."
        )
        header.addWidget(self.admin_label)

        self.elevate_button = QPushButton("Restart as Administrator")
        self.elevate_button.setObjectName("SubtleButton")
        self.elevate_button.clicked.connect(self.request_elevation)
        self.elevate_button.setVisible(not is_elevated())
        header.addWidget(self.elevate_button)

        self.theme_button = QPushButton("Light" if self.palette_name == "dark" else "Dark")
        self.theme_button.setObjectName("SubtleButton")
        self.theme_button.setToolTip("Switch between the dark and light theme")
        self.theme_button.clicked.connect(self.toggle_theme)
        header.addWidget(self.theme_button)

        self.about_button = QPushButton("About")
        self.about_button.setObjectName("SubtleButton")
        self.about_button.setToolTip("Version information and diagnostics (F1)")
        self.about_button.clicked.connect(self.show_about)
        header.addWidget(self.about_button)
        return header

    def _install_shortcuts(self) -> None:
        QShortcut(QKeySequence("F5"), self, activated=self.refresh_adapters)
        QShortcut(QKeySequence("F1"), self, activated=self.show_about)
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self.refresh_adapters)
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self.save_preset)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.apply_configuration)
        QShortcut(QKeySequence("Ctrl+D"), self, activated=self.show_dry_run)
        QShortcut(QKeySequence("Ctrl+Q"), self, activated=self.close)

    # =====================================================================
    # Theme
    # =====================================================================
    def apply_theme(self, name: str) -> None:
        self.palette_name = name if name in PALETTES else "dark"
        self.palette = PALETTES[self.palette_name]
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(build_stylesheet(self.palette))
        self.theme_button.setText("Light" if self.palette_name == "dark" else "Dark")
        self.adapter_panel.apply_palette(self.palette)
        self.preset_panel.apply_palette(self.palette)
        self.history_panel.apply_palette(self.palette)
        self._update_admin_label()
        self.refresh_presets()
        self.refresh_history()

    def toggle_theme(self) -> None:
        self.apply_theme("light" if self.palette_name == "dark" else "dark")
        self.settings.set("theme", self.palette_name)
        self.settings.save()

    def _update_admin_label(self) -> None:
        elevated = is_elevated()
        icon = ICON_ACTIVE if elevated else ICON_WARN
        colour = status_color(self.palette, "success" if elevated else "warning")
        self.admin_label.setText(f"{icon}  {elevation_status_text()}")
        self.admin_label.setStyleSheet(
            f"color: {colour}; font-weight: 600; font-size: 13px;"
        )

    # =====================================================================
    # Elevation
    # =====================================================================
    def request_elevation(self) -> None:
        if is_elevated():
            return
        if relaunch_as_admin():
            log.info("Elevated instance started; closing this one")
            QApplication.quit()
        else:
            ErrorDialog(
                self,
                self.palette,
                "Administrator privileges were not granted.",
                "Network configuration cannot be changed without Administrator "
                "privileges. You can keep viewing adapter information.",
            ).exec()

    # =====================================================================
    # Adapter discovery
    # =====================================================================
    def refresh_adapters(self) -> None:
        if self.lock.busy:
            return
        self.status_bar.showMessage("Reading network adapters…")
        self.tasks.submit(
            self.manager.list_adapters,
            on_result=self._on_adapters_loaded,
            on_error=self._on_background_error,
        )

    def _on_adapters_loaded(self, adapters: list[Adapter]) -> None:
        self._adapters = adapters
        remembered = self.settings.get("last_adapter_guid", "")
        self.adapter_panel.set_adapters(adapters, select_guid=remembered)
        self.refresh_presets()
        active = sum(1 for a in adapters if a.oper_status.is_up)
        self.status_bar.showMessage(
            f"{len(adapters)} adapters   ·   {active} connected", 6000
        )

    def _on_adapter_changed(self, adapter: Optional[Adapter]) -> None:
        if adapter is not None:
            self.settings.set("last_adapter_guid", adapter.guid)
            self.settings.save()
            log.info(
                "Adapter selected: %s (%s, %s)",
                adapter.friendly_name,
                adapter.kind.value,
                adapter.oper_status.label,
            )
        self.refresh_presets()
        self._update_apply_state()

    def _update_apply_state(self) -> None:
        """Keep Apply disabled unless the request is complete and valid."""
        ready = self.adapter_panel.is_ready and not self.lock.busy and is_elevated()
        self.adapter_panel.apply_button.setEnabled(ready)

        config = self.adapter_panel.configuration()
        adapter = self.adapter_panel.current_adapter
        if config is None or adapter is None or config.is_dhcp:
            self.adapter_panel.set_hint("")
            return
        if not self.adapter_panel.is_ready:
            self.adapter_panel.set_hint("")
            return
        result = validate(config, adapter)
        gateway_warnings = [
            w.message for w in result.warnings if w.field in ("gateway", "ip", "mask")
        ]
        self.adapter_panel.set_hint(gateway_warnings[0] if gateway_warnings else "")

    # =====================================================================
    # Presets and history display
    # =====================================================================
    def refresh_presets(self) -> None:
        try:
            entries = self.presets.list_all()
        except IPChangerError as exc:
            log.warning("Presets unavailable: %s", exc.message)
            entries = []
        self.preset_panel.set_presets(
            entries, self._adapters, self.adapter_panel.current_adapter
        )

    def refresh_history(self) -> None:
        self.history_panel.set_entries(self.history.recent(30))

    def clear_history(self) -> None:
        dialog = ConfirmDialog(
            self,
            self.palette,
            "Clear history?",
            "All recorded activity will be removed. Saved configurations are not affected.",
            confirm_text="CLEAR",
            dangerous=True,
        )
        if dialog.exec() == ConfirmDialog.Accepted:
            self.history.clear()
            self.refresh_history()

    # =====================================================================
    # Applying a configuration
    # =====================================================================
    def apply_configuration(self) -> None:
        adapter = self.adapter_panel.current_adapter
        if adapter is None:
            return
        config = self.adapter_panel.configuration()
        if config is None:
            self._show_error(
                "The configuration is incomplete.",
                "Check the IP address and subnet mask.",
            )
            return
        self._begin_apply(adapter, config)

    def _begin_apply(
        self, adapter: Adapter, config: IPConfiguration, source: str = "manual"
    ) -> None:
        if not is_elevated():
            self.request_elevation()
            return

        # Validate before anything else happens. Errors still stop the
        # operation: they are not a confirmation prompt, they mean Windows
        # would reject the configuration.
        result = validate(config, adapter)
        if not result.is_valid:
            self._show_error(
                "The configuration is not valid.",
                result.error_text(),
            )
            return

        # No confirmation step: pressing APPLY applies. The adapter, address
        # and any warnings are shown continuously in the panel instead, and
        # the previous configuration is snapshotted so it can be restored.
        log.info(
            "Applying without confirmation on %s: %s (warnings: %s)",
            adapter.friendly_name,
            config.describe(),
            "; ".join(w.message for w in result.warnings) or "none",
        )

        if not self.lock.acquire(f"apply:{adapter.guid}"):
            self._show_error(
                "Another network operation is already running.",
                "Wait for it to finish before starting another one.",
            )
            return

        self._set_busy(True)
        self._progress_dialog = ProgressDialog(self, "Applying configuration…")
        self._progress_dialog.show()

        previous_state = self._describe_adapter(adapter)
        self.tasks.submit(
            self.manager.apply_configuration,
            adapter,
            config,
            check_conflict=bool(self.settings.get("check_ip_conflicts", True)),
            on_result=lambda r: self._on_apply_finished(r, adapter, config, previous_state),
            on_error=self._on_operation_error,
            on_progress=self._on_progress,
        )

    def _on_progress(self, text: str) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.add_step(text)
        self.status_bar.showMessage(text)

    def _on_apply_finished(
        self,
        result: OperationResult,
        adapter: Adapter,
        config: IPConfiguration,
        previous_state: str,
    ) -> None:
        self._close_progress()
        self.lock.release()
        self._set_busy(False)
        self._last_snapshot = result.snapshot

        self.history.record(
            adapter_name=adapter.friendly_name,
            adapter_guid=adapter.guid,
            action="dhcp" if config.is_dhcp else "static",
            previous_state=previous_state,
            new_state=config.describe(),
            status=result.status.value,
            message="" if result.ok else result.message,
        )
        self.refresh_history()

        dialog = ResultDialog(
            self,
            self.palette,
            success=result.ok,
            message=result.message if result.message else "Configuration applied",
            detail=result.detail,
            technical=result.technical,
            offer_restore=result.can_rollback,
            conflict=result.conflict,
        )
        outcome = dialog.exec()
        self.refresh_adapters()

        if outcome == ResultDialog.RESTORE and result.snapshot is not None:
            self.restore_snapshot(result.snapshot)

    def _on_operation_error(self, error: IPChangerError) -> None:
        self._close_progress()
        self.lock.release()
        self._set_busy(False)
        self._show_error(error.message, error.detail, error.technical)
        self.refresh_adapters()

    def _on_background_error(self, error: IPChangerError) -> None:
        self.status_bar.showMessage("Could not read the network adapters", 8000)
        self._show_error(error.message, error.detail, error.technical)

    # =====================================================================
    # Rollback
    # =====================================================================
    def restore_snapshot(self, snapshot) -> None:
        if not self.lock.acquire("restore"):
            return
        self._set_busy(True)
        self._progress_dialog = ProgressDialog(self, "Restoring previous configuration…")
        self._progress_dialog.show()
        self.tasks.submit(
            self.manager.restore_configuration,
            snapshot,
            on_result=lambda r: self._on_restore_finished(r, snapshot),
            on_error=self._on_operation_error,
            on_progress=self._on_progress,
        )

    def _on_restore_finished(self, result: OperationResult, snapshot) -> None:
        self._close_progress()
        self.lock.release()
        self._set_busy(False)

        restored = result.status is ResultStatus.ROLLED_BACK
        self.history.record(
            adapter_name=snapshot.friendly_name,
            adapter_guid=snapshot.guid,
            action="restore",
            previous_state="",
            new_state=snapshot.describe(),
            status="rolled_back" if restored else "failed",
            message="" if restored else result.message,
        )
        self.refresh_history()

        ResultDialog(
            self,
            self.palette,
            success=restored,
            message=result.message,
            detail=result.detail,
            technical=result.technical,
            offer_restore=False,
        ).exec()
        self.refresh_adapters()

    # =====================================================================
    # Dry run
    # =====================================================================
    def show_dry_run(self) -> None:
        adapter = self.adapter_panel.current_adapter
        config = self.adapter_panel.configuration()
        if adapter is None or config is None:
            self._show_error(
                "Nothing to preview.",
                "Select an adapter and enter a valid configuration first.",
            )
            return
        result = self.manager.dry_run(adapter, config)
        DryRunDialog(self, result.detail).exec()

    # =====================================================================
    # Preset actions
    # =====================================================================
    def save_preset(self) -> None:
        adapter = self.adapter_panel.current_adapter
        config = self.adapter_panel.configuration()
        if config is None:
            self._show_error(
                "The configuration is incomplete.",
                "Enter a valid IP address and subnet mask before saving.",
            )
            return
        dialog = PresetDialog(self, self.palette, adapter, config)
        if dialog.exec() != PresetDialog.Accepted:
            return
        try:
            self.presets.create(
                dialog.preset_name,
                config,
                adapter if dialog.remember_adapter else None,
                dialog.preset_description,
            )
        except IPChangerError as exc:
            self._show_error(exc.message, exc.detail, exc.technical)
            return
        self.refresh_presets()
        self.status_bar.showMessage(f"Saved '{dialog.preset_name}'", 5000)

    def load_preset(self, preset_id: int) -> None:
        preset = self.presets.get(preset_id)
        if preset is None:
            return
        self.adapter_panel.load_configuration(preset.configuration)
        self._update_apply_state()
        self.status_bar.showMessage(f"Loaded '{preset.name}' into the form", 5000)

    def apply_preset(self, preset_id: int) -> None:
        preset = self.presets.get(preset_id)
        if preset is None:
            return

        # Never silently apply a preset to a different adapter (section 19).
        target: Optional[Adapter] = None
        if preset.adapter_identifier or preset.adapter_mac:
            target, note = resolve_preset_adapter(preset, self._adapters)
            if target is None:
                AdapterMissingDialog(self, self.palette, note).exec()
                return
            if note:
                self.status_bar.showMessage(note, 8000)
        else:
            target = self.adapter_panel.current_adapter
            if target is None:
                self._show_error(
                    "No adapter selected.",
                    "This configuration is not linked to a specific adapter. "
                    "Select the adapter to configure first.",
                )
                return

        # Make the selection visible before asking for confirmation.
        self.adapter_panel.set_adapters(self._adapters, select_guid=target.guid)
        self.adapter_panel.load_configuration(preset.configuration)
        self._begin_apply(target, preset.configuration, source=f"preset:{preset.name}")

    def edit_preset(self, preset_id: int) -> None:
        preset = self.presets.get(preset_id)
        if preset is None:
            return
        dialog = EditPresetDialog(
            self, self.palette, preset.name, preset.configuration, preset.description
        )
        if dialog.exec() != EditPresetDialog.Accepted:
            return
        try:
            self.presets.update(
                preset_id,
                dialog.preset_name,
                dialog.result_configuration,
                description=dialog.preset_description,
                keep_adapter=True,
            )
        except IPChangerError as exc:
            self._show_error(exc.message, exc.detail, exc.technical)
            return
        self.refresh_presets()

    def rename_preset(self, preset_id: int) -> None:
        preset = self.presets.get(preset_id)
        if preset is None:
            return
        dialog = RenameDialog(self, preset.name)
        if dialog.exec() != RenameDialog.Accepted or not dialog.new_name:
            return
        try:
            self.presets.rename(preset_id, dialog.new_name)
        except IPChangerError as exc:
            self._show_error(exc.message, exc.detail, exc.technical)
            return
        self.refresh_presets()

    def duplicate_preset(self, preset_id: int) -> None:
        try:
            self.presets.duplicate(preset_id)
        except IPChangerError as exc:
            self._show_error(exc.message, exc.detail, exc.technical)
            return
        self.refresh_presets()

    def delete_preset(self, preset_id: int) -> None:
        preset = self.presets.get(preset_id)
        if preset is None:
            return
        dialog = ConfirmDialog(
            self,
            self.palette,
            "Delete this configuration?",
            f"'{preset.name}' will be permanently removed.\n\n"
            "This does not change any network settings.",
            confirm_text="DELETE",
            dangerous=True,
        )
        if dialog.exec() != ConfirmDialog.Accepted:
            return
        try:
            self.presets.delete(preset_id)
        except IPChangerError as exc:
            self._show_error(exc.message, exc.detail, exc.technical)
            return
        self.refresh_presets()
        self.status_bar.showMessage(f"Deleted '{preset.name}'", 5000)

    def export_presets(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export configurations",
            str(data_dir() / "ip_changer_presets.json"),
            "JSON files (*.json)",
        )
        if not path:
            return
        try:
            count = self.presets.export_to_file(path)
        except IPChangerError as exc:
            self._show_error(exc.message, exc.detail, exc.technical)
            return
        self.status_bar.showMessage(f"Exported {count} configurations", 6000)

    def import_presets(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import configurations", str(data_dir()), "JSON files (*.json)"
        )
        if not path:
            return
        try:
            imported, skipped, problems = self.presets.import_from_file(path)
        except IPChangerError as exc:
            self._show_error(exc.message, exc.detail, exc.technical)
            return

        self.refresh_presets()
        summary = f"Imported {imported} configurations."
        if skipped:
            summary += f" {skipped} already existed and were skipped."
        if problems:
            ResultDialog(
                self,
                self.palette,
                success=imported > 0,
                message=summary,
                detail="Some entries were not imported:\n\n" + "\n".join(problems[:12]),
            ).exec()
        else:
            self.status_bar.showMessage(summary, 6000)

    # =====================================================================
    # Crash recovery (section 54)
    # =====================================================================
    def _check_pending_operation(self) -> None:
        pending = self.manager.journal.pending()
        if pending is None:
            return
        dialog = RecoveryDialog(self, self.palette, pending)
        outcome = dialog.exec()
        if outcome == RecoveryDialog.RESTORE:
            self.manager.journal.complete()
            self.restore_snapshot(pending.snapshot)
        elif outcome == RecoveryDialog.CHECK:
            self.manager.journal.complete()
            self.refresh_adapters()
            self.adapter_panel.set_adapters(
                self._adapters, select_guid=pending.adapter_guid
            )
        else:
            self.manager.journal.complete()

    # =====================================================================
    # About / diagnostics
    # =====================================================================
    def show_about(self) -> None:
        AboutDialog(
            self,
            self.palette,
            self._build_diagnostics(),
            is_elevated(),
            windows_version(),
        ).exec()

    def _build_diagnostics(self) -> str:
        lines = [
            f"{__app_name__} {__version__}",
            f"Windows           : {windows_version()}",
            f"Administrator     : {'yes' if is_elevated() else 'no'}",
            f"Data directory    : {data_dir()}",
            f"Log directory     : {log_dir()}",
            f"Adapters detected : {len(self._adapters)}",
            "",
            "=" * 64,
        ]
        for adapter in self._adapters:
            lines.extend(
                [
                    "",
                    f"{adapter.friendly_name}",
                    f"  Description  : {adapter.description}",
                    f"  Type         : {adapter.kind.value}",
                    f"  Status       : {adapter.oper_status.label}",
                    f"  GUID         : {adapter.guid}",
                    f"  MAC          : {adapter.mac or 'n/a'}",
                    f"  Index        : {adapter.if_index}",
                    f"  DHCP         : {'enabled' if adapter.effective_dhcp else 'disabled'}",
                ]
            )
            if adapter.ipv4:
                for address in adapter.ipv4:
                    marker = "  (APIPA)" if address.is_apipa else ""
                    lines.append(
                        f"  IPv4         : {address.address} / {address.subnet_mask}{marker}"
                    )
            else:
                lines.append("  IPv4         : none")
            lines.append(f"  Gateway      : {', '.join(adapter.ipv4_gateways) or 'none'}")
            dns = [d for d in adapter.dns_servers if ':' not in d]
            lines.append(f"  DNS          : {', '.join(dns) or 'none'}")
            if adapter.ipv6:
                lines.append(f"  IPv6         : {', '.join(adapter.ipv6[:3])}")
        return "\n".join(lines)

    # =====================================================================
    # Helpers
    # =====================================================================
    @staticmethod
    def _describe_adapter(adapter: Adapter) -> str:
        if adapter.effective_dhcp:
            return "DHCP"
        primary = adapter.primary_ipv4
        return primary.address if primary else "no address"

    def _set_busy(self, busy: bool) -> None:
        self.adapter_panel.set_busy(busy)
        self.preset_panel.set_busy(busy)
        self.side_tabs.setEnabled(not busy)
        if not busy:
            self._update_apply_state()

    def _close_progress(self) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.accept()
            self._progress_dialog.deleteLater()
            self._progress_dialog = None

    def _show_error(self, message: str, detail: str = "", technical: str = "") -> None:
        ErrorDialog(self, self.palette, message, detail, technical).exec()

    # =====================================================================
    # Window lifecycle
    # =====================================================================
    def closeEvent(self, event) -> None:
        if self.lock.busy:
            dialog = ConfirmDialog(
                self,
                self.palette,
                "A network operation is running",
                "Closing now may leave the adapter in an unknown state. "
                "Close anyway?",
                confirm_text="CLOSE ANYWAY",
                dangerous=True,
            )
            if dialog.exec() != ConfirmDialog.Accepted:
                event.ignore()
                return

        self.settings.update(
            window_width=self.width(),
            window_height=self.height(),
            window_x=max(0, self.x()),
            window_y=max(0, self.y()),
            theme=self.palette_name,
        )
        self.settings.save()
        self.tasks.wait(3000)
        log.info("Application closing")
        event.accept()
