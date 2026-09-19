"""Dialogs: results, errors, presets, progress, recovery and diagnostics.

Applying a configuration is deliberately not confirmed with a dialog: pressing
APPLY applies. The adapter, address and any warnings are therefore shown
continuously in the main panel, together with the connection-loss caution
(specification sections 15 and 37), so the operator sees them without having
to click through anything.

Destructive actions that are *not* the main workflow - deleting a saved
configuration, clearing history - are still confirmed.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app import __app_name__, __description__, __version__
from app.gui.styles import Palette, status_color
from app.gui.widgets import (
    ICON_FAIL,
    ICON_OK,
    ICON_WARN,
    Card,
    Divider,
    KeyValueGrid,
    ValidatedLineEdit,
    muted_label,
)
from app.models.adapter import Adapter
from app.models.configuration import IPConfiguration
from app.network.validator import validate_ip_text, validate_mask_text


class BaseDialog(QDialog):
    """Common chrome: title, escape-to-cancel, sensible sizing."""

    def __init__(self, parent: Optional[QWidget], title: str, width: int = 520) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(width)
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(26, 24, 26, 22)
        self._root.setSpacing(16)

    def add_title(self, text: str, icon: str = "", role: str = "", palette: Optional[Palette] = None) -> QLabel:
        label = QLabel(f"{icon}  {text}" if icon else text)
        label.setObjectName("TitleLabel")
        label.setWordWrap(True)
        if role and palette is not None:
            label.setStyleSheet(f"color: {status_color(palette, role)};")
        self._root.addWidget(label)
        return label

    def layout_root(self) -> QVBoxLayout:
        return self._root


# ---------------------------------------------------------------------------
# Result dialogs (section 13 step 8)
# ---------------------------------------------------------------------------
class ResultDialog(BaseDialog):
    """Reports the outcome and offers rollback when appropriate."""

    RESTORE = 2

    def __init__(
        self,
        parent: Optional[QWidget],
        palette: Palette,
        success: bool,
        message: str,
        detail: str,
        technical: str = "",
        offer_restore: bool = False,
        conflict: str = "",
    ) -> None:
        super().__init__(parent, "Result", width=520)
        icon = ICON_OK if success else ICON_FAIL
        role = "success" if success else "danger"
        self.add_title(message, icon=icon, role=role, palette=palette)

        if detail:
            body = Card(flat=True)
            value = QLabel(detail)
            value.setObjectName("ValueLabel")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            body.add(value)
            self._root.addWidget(body)

        if conflict:
            self._root.addWidget(muted_label(conflict))

        if not success and offer_restore:
            self._root.addWidget(
                muted_label("The previous configuration can be restored.")
            )

        if technical:
            self._technical = QPlainTextEdit(technical)
            self._technical.setReadOnly(True)
            self._technical.setVisible(False)
            self._technical.setMaximumHeight(140)

            toggle = QPushButton("Show technical details")
            toggle.setObjectName("SubtleButton")

            def _toggle() -> None:
                visible = not self._technical.isVisible()
                self._technical.setVisible(visible)
                toggle.setText("Hide technical details" if visible else "Show technical details")
                self.adjustSize()

            toggle.clicked.connect(_toggle)
            self._root.addWidget(toggle)
            self._root.addWidget(self._technical)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        if not success and offer_restore:
            restore = QPushButton("RESTORE PREVIOUS CONFIGURATION")
            restore.setObjectName("DangerButton")
            restore.clicked.connect(lambda: self.done(self.RESTORE))
            buttons.addWidget(restore)
        close = QPushButton("CLOSE")
        close.setObjectName("PrimaryButton" if success else "SubtleButton")
        close.clicked.connect(self.accept)
        close.setDefault(True)
        buttons.addWidget(close)
        self._root.addLayout(buttons)
        close.setFocus()


class ErrorDialog(BaseDialog):
    """User-friendly error reporting; raw detail stays hidden by default."""

    def __init__(
        self,
        parent: Optional[QWidget],
        palette: Palette,
        message: str,
        detail: str = "",
        technical: str = "",
    ) -> None:
        super().__init__(parent, "Problem", width=500)
        self.add_title(message, icon=ICON_FAIL, role="danger", palette=palette)
        if detail:
            self._root.addWidget(muted_label(detail))

        if technical:
            self._technical = QPlainTextEdit(technical)
            self._technical.setReadOnly(True)
            self._technical.setVisible(False)
            self._technical.setMaximumHeight(150)
            toggle = QPushButton("Show technical details")
            toggle.setObjectName("SubtleButton")

            def _toggle() -> None:
                visible = not self._technical.isVisible()
                self._technical.setVisible(visible)
                toggle.setText("Hide technical details" if visible else "Show technical details")
                self.adjustSize()

            toggle.clicked.connect(_toggle)
            self._root.addWidget(toggle)
            self._root.addWidget(self._technical)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        self._root.addWidget(buttons)


class ConfirmDialog(BaseDialog):
    """Generic confirmation, used for destructive actions such as delete."""

    def __init__(
        self,
        parent: Optional[QWidget],
        palette: Palette,
        title: str,
        message: str,
        confirm_text: str = "CONFIRM",
        dangerous: bool = False,
    ) -> None:
        super().__init__(parent, title, width=460)
        self.add_title(
            title,
            icon=ICON_WARN if dangerous else "",
            role="warning" if dangerous else "",
            palette=palette,
        )
        self._root.addWidget(muted_label(message))

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("CANCEL")
        cancel.setObjectName("SubtleButton")
        cancel.clicked.connect(self.reject)
        confirm = QPushButton(confirm_text)
        confirm.setObjectName("DangerButton" if dangerous else "PrimaryButton")
        confirm.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(confirm)
        self._root.addLayout(buttons)
        cancel.setFocus()  # destructive actions never default to the danger button


# ---------------------------------------------------------------------------
# Progress (section 26)
# ---------------------------------------------------------------------------
class ProgressDialog(BaseDialog):
    """Non-cancellable progress display shown while an operation runs."""

    def __init__(self, parent: Optional[QWidget], title: str = "Applying configuration…") -> None:
        super().__init__(parent, "Working", width=440)
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        self.add_title(title)

        self.bar = QProgressBar()
        self.bar.setRange(0, 0)  # indeterminate
        self.bar.setTextVisible(False)
        self._root.addWidget(self.bar)

        self.steps = QLabel("")
        self.steps.setObjectName("MutedLabel")
        self.steps.setWordWrap(True)
        self.steps.setMinimumHeight(72)
        self.steps.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._root.addWidget(self.steps)

        self._lines: list[str] = []

    def add_step(self, text: str) -> None:
        self._lines.append(f"●  {text}")
        self.steps.setText("\n".join(self._lines[-4:]))

    def keyPressEvent(self, event) -> None:
        # Escape must not abort a half-finished network change.
        if event.key() == Qt.Key_Escape:
            event.ignore()
            return
        super().keyPressEvent(event)


# ---------------------------------------------------------------------------
# Preset editing (sections 16, 18)
# ---------------------------------------------------------------------------
class PresetDialog(BaseDialog):
    """Create or edit a named configuration."""

    def __init__(
        self,
        parent: Optional[QWidget],
        palette: Palette,
        adapter: Optional[Adapter] = None,
        configuration: Optional[IPConfiguration] = None,
        name: str = "",
        description: str = "",
        title: str = "Save configuration",
    ) -> None:
        super().__init__(parent, title, width=470)
        self._palette = palette
        self.add_title(title)

        self.name_field = ValidatedLineEdit("Name", "PLC NETWORK")
        self.name_field.field.setFont(QFont())
        self.name_field.set_text(name)
        self._root.addWidget(self.name_field)

        self.description_field = ValidatedLineEdit("Description (optional)", "")
        self.description_field.set_required(False)
        self.description_field.set_text(description)
        self._root.addWidget(self.description_field)

        summary = Card("Configuration", flat=True)
        grid = KeyValueGrid()
        config = configuration or IPConfiguration.dhcp()
        rows = [("Adapter", adapter.friendly_name if adapter else "not linked")]
        if config.is_dhcp:
            rows.append(("Mode", "DHCP"))
        else:
            rows.extend(
                [
                    ("Mode", "Static"),
                    ("IP address", config.ip_address),
                    ("Subnet mask", config.subnet_mask),
                    ("Gateway", config.gateway or "none"),
                ]
            )
        grid.set_rows(rows)
        summary.add(grid)
        self._root.addWidget(summary)

        if adapter is not None:
            self.link_adapter = QCheckBox(
                f"Remember this adapter ({adapter.friendly_name})"
            )
            self.link_adapter.setChecked(True)
            self.link_adapter.setToolTip(
                "The adapter is remembered by its hardware identity, not only by name."
            )
            self._root.addWidget(self.link_adapter)
        else:
            self.link_adapter = None

        self.error = QLabel("")
        self.error.setObjectName("ErrorLabel")
        self.error.setVisible(False)
        self.error.setWordWrap(True)
        self._root.addWidget(self.error)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("CANCEL")
        cancel.setObjectName("SubtleButton")
        cancel.clicked.connect(self.reject)
        save = QPushButton("SAVE")
        save.setObjectName("PrimaryButton")
        save.setDefault(True)
        save.clicked.connect(self._on_save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        self._root.addLayout(buttons)
        self.name_field.field.setFocus()

    def _on_save(self) -> None:
        if not self.name_field.text():
            self.error.setText("Enter a name for this configuration.")
            self.error.setVisible(True)
            self.name_field.field.setFocus()
            return
        self.accept()

    @property
    def preset_name(self) -> str:
        return self.name_field.text()

    @property
    def preset_description(self) -> str:
        return self.description_field.text()

    @property
    def remember_adapter(self) -> bool:
        return self.link_adapter is not None and self.link_adapter.isChecked()


class EditPresetDialog(BaseDialog):
    """Edit the addressing of an existing preset."""

    def __init__(
        self,
        parent: Optional[QWidget],
        palette: Palette,
        name: str,
        configuration: IPConfiguration,
        description: str = "",
    ) -> None:
        super().__init__(parent, "Edit configuration", width=470)
        self.add_title("Edit configuration")

        self.name_field = ValidatedLineEdit("Name", "")
        self.name_field.set_text(name)
        self._root.addWidget(self.name_field)

        self.is_dhcp = configuration.is_dhcp
        self.mode_note = muted_label(
            "This is a DHCP configuration." if self.is_dhcp else "Static configuration."
        )
        self._root.addWidget(self.mode_note)

        self.ip_field = ValidatedLineEdit("IP address", "192.168.10.100", validator=validate_ip_text)
        self.mask_field = ValidatedLineEdit("Subnet mask", "255.255.255.0", validator=validate_mask_text)
        self.gateway_field = ValidatedLineEdit("Gateway (optional)", "192.168.10.1", validator=validate_ip_text)
        self.gateway_field.set_required(False)

        if not self.is_dhcp:
            self.ip_field.set_text(configuration.ip_address)
            self.mask_field.set_text(configuration.subnet_mask)
            self.gateway_field.set_text(configuration.gateway)
            self._root.addWidget(self.ip_field)
            self._root.addWidget(self.mask_field)
            self._root.addWidget(self.gateway_field)

        self.description_field = ValidatedLineEdit("Description (optional)", "")
        self.description_field.set_required(False)
        self.description_field.set_text(description)
        self._root.addWidget(self.description_field)

        self.error = QLabel("")
        self.error.setObjectName("ErrorLabel")
        self.error.setVisible(False)
        self.error.setWordWrap(True)
        self._root.addWidget(self.error)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("CANCEL")
        cancel.setObjectName("SubtleButton")
        cancel.clicked.connect(self.reject)
        save = QPushButton("SAVE")
        save.setObjectName("PrimaryButton")
        save.setDefault(True)
        save.clicked.connect(self._on_save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        self._root.addLayout(buttons)

    def _on_save(self) -> None:
        if not self.name_field.text():
            self._fail("Enter a name for this configuration.")
            return
        if not self.is_dhcp:
            if not self.ip_field.validate() or not self.mask_field.validate():
                self._fail("Correct the highlighted fields.")
                return
            if self.gateway_field.text() and not self.gateway_field.validate():
                self._fail("Correct the highlighted fields.")
                return
        self.accept()

    def _fail(self, message: str) -> None:
        self.error.setText(message)
        self.error.setVisible(True)

    @property
    def result_configuration(self) -> IPConfiguration:
        if self.is_dhcp:
            return IPConfiguration.dhcp()
        return IPConfiguration.static(
            self.ip_field.text(), self.mask_field.text(), self.gateway_field.text()
        )

    @property
    def preset_name(self) -> str:
        return self.name_field.text()

    @property
    def preset_description(self) -> str:
        return self.description_field.text()


class RenameDialog(BaseDialog):
    def __init__(self, parent: Optional[QWidget], current_name: str) -> None:
        super().__init__(parent, "Rename", width=400)
        self.add_title("Rename configuration")
        self.field = QLineEdit(current_name)
        self.field.selectAll()
        self._root.addWidget(self.field)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._root.addWidget(buttons)
        self.field.setFocus()

    @property
    def new_name(self) -> str:
        return self.field.text().strip()


# ---------------------------------------------------------------------------
# Crash recovery (section 54)
# ---------------------------------------------------------------------------
class RecoveryDialog(BaseDialog):
    """Offers - never performs automatically - recovery after a crash."""

    CHECK = 2
    RESTORE = 3

    def __init__(self, parent: Optional[QWidget], palette: Palette, pending) -> None:
        super().__init__(parent, "Incomplete operation", width=520)
        self.add_title(
            "Previous network operation did not complete",
            icon=ICON_WARN,
            role="warning",
            palette=palette,
        )
        self._root.addWidget(
            muted_label(
                "The application closed while a network change was in progress. "
                "Nothing has been changed automatically."
            )
        )

        grid = KeyValueGrid()
        grid.set_rows(
            [
                ("Adapter", pending.adapter_name),
                ("Started", pending.started_local),
                ("Previous", pending.previous_description),
                ("Requested", pending.requested_description),
            ]
        )
        self._root.addWidget(grid)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        dismiss = QPushButton("DISMISS")
        dismiss.setObjectName("SubtleButton")
        dismiss.clicked.connect(self.reject)
        check = QPushButton("CHECK CURRENT CONFIGURATION")
        check.setObjectName("SubtleButton")
        check.clicked.connect(lambda: self.done(self.CHECK))
        restore = QPushButton("RESTORE PREVIOUS CONFIGURATION")
        restore.setObjectName("PrimaryButton")
        restore.clicked.connect(lambda: self.done(self.RESTORE))
        buttons.addWidget(dismiss)
        buttons.addWidget(check)
        buttons.addWidget(restore)
        self._root.addLayout(buttons)
        check.setFocus()


# ---------------------------------------------------------------------------
# About / diagnostics (sections 49, 50)
# ---------------------------------------------------------------------------
class AboutDialog(BaseDialog):
    """Version information plus a copyable diagnostics report."""

    def __init__(
        self,
        parent: Optional[QWidget],
        palette: Palette,
        diagnostics: str,
        is_admin: bool,
        windows_version: str,
    ) -> None:
        super().__init__(parent, "About", width=620)
        title = QLabel(__app_name__)
        title.setObjectName("AppTitle")
        self._root.addWidget(title)
        self._root.addWidget(muted_label(f"Version {__version__}"))
        self._root.addWidget(muted_label(__description__))
        self._root.addWidget(Divider())

        summary = KeyValueGrid()
        summary.set_rows(
            [
                ("Windows", windows_version),
                ("Administrator", "Yes" if is_admin else "No"),
            ]
        )
        self._root.addWidget(summary)

        self._root.addWidget(muted_label("Diagnostics"))
        self.report = QPlainTextEdit(diagnostics)
        self.report.setReadOnly(True)
        self.report.setMinimumHeight(280)
        font = QFont("Cascadia Mono")
        font.setStyleHint(QFont.Monospace)
        font.setPointSize(9)
        self.report.setFont(font)
        self._root.addWidget(self.report)

        buttons = QHBoxLayout()
        self.copy_button = QPushButton("Copy diagnostic information")
        self.copy_button.setObjectName("SubtleButton")
        self.copy_button.clicked.connect(self._copy)
        buttons.addWidget(self.copy_button)
        buttons.addStretch(1)
        close = QPushButton("CLOSE")
        close.setObjectName("PrimaryButton")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        self._root.addLayout(buttons)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.report.toPlainText())
        self.copy_button.setText("Copied")


class DryRunDialog(BaseDialog):
    """Shows what a change would do, without doing it (section 38)."""

    def __init__(self, parent: Optional[QWidget], report: str) -> None:
        super().__init__(parent, "Dry run", width=560)
        self.add_title("Dry run - nothing will be changed")
        view = QPlainTextEdit(report)
        view.setReadOnly(True)
        view.setMinimumHeight(280)
        font = QFont("Cascadia Mono")
        font.setStyleHint(QFont.Monospace)
        font.setPointSize(10)
        view.setFont(font)
        self._root.addWidget(view)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        self._root.addWidget(buttons)


class AdapterMissingDialog(BaseDialog):
    """Shown when a preset's adapter cannot be identified (section 19)."""

    def __init__(self, parent: Optional[QWidget], palette: Palette, explanation: str) -> None:
        super().__init__(parent, "Adapter not found", width=470)
        self.add_title("Adapter not found", icon=ICON_WARN, role="warning", palette=palette)
        self._root.addWidget(muted_label(explanation))
        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        self._root.addWidget(buttons)
