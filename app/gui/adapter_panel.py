"""Adapter selection, current configuration and the new-configuration form.

The adapter selector distinguishes active from inactive adapters and shows the
address alongside the name, so the operator never has to guess which interface
they are about to change (specification sections 7 and 37).
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from app.gui.styles import Palette, status_color
from app.gui.widgets import (
    ICON_ACTIVE,
    ICON_INACTIVE,
    ICON_REFRESH,
    ICON_WARN,
    Card,
    KeyValueGrid,
    StatusIndicator,
    ValidatedLineEdit,
    muted_label,
)
from app.models.adapter import Adapter
from app.models.configuration import ConfigMode, IPConfiguration, mask_to_prefix
from app.network.validator import validate_ip_text, validate_mask_text


class AdapterPanel(QWidget):
    """The primary working area: pick an adapter, see it, change it."""

    adapterChanged = Signal(object)      # Adapter or None
    refreshRequested = Signal()
    applyRequested = Signal()
    savePresetRequested = Signal()
    dryRunRequested = Signal()
    configurationEdited = Signal()

    def __init__(self, palette: Palette, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._adapters: list[Adapter] = []
        self._current: Optional[Adapter] = None
        self._loading = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)

        root.addWidget(self._build_adapter_card())
        root.addWidget(self._build_current_card())
        root.addWidget(self._build_new_card())
        root.addStretch(1)

        # Built here but deliberately not added to this layout: the window pins
        # it below the scroll area so APPLY is always reachable.
        self.actions_widget = self._build_actions()

    # ------------------------------------------------------------- building
    def _build_adapter_card(self) -> Card:
        card = Card("Network adapter")

        self.refresh_button = QPushButton(f"{ICON_REFRESH}  Refresh")
        self.refresh_button.setObjectName("SubtleButton")
        self.refresh_button.setToolTip("Re-read all network adapters (F5)")
        self.refresh_button.clicked.connect(self.refreshRequested.emit)
        card.add_header_widget(self.refresh_button)

        self.selector = QComboBox()
        self.selector.setToolTip("Select the adapter to configure")
        self.selector.currentIndexChanged.connect(self._on_selection_changed)
        card.add(self.selector)

        self.status = StatusIndicator(self._palette)
        card.add(self.status)

        self.adapter_note = QLabel("")
        self.adapter_note.setObjectName("WarningLabel")
        self.adapter_note.setWordWrap(True)
        self.adapter_note.setVisible(False)
        card.add(self.adapter_note)
        return card

    def _build_current_card(self) -> Card:
        card = Card("Current configuration")
        self.current_grid = KeyValueGrid()
        self.current_grid.set_rows(
            [
                ("IP address", "-"),
                ("Subnet mask", "-"),
                ("Gateway", "-"),
                ("DHCP", "-"),
            ]
        )
        card.add(self.current_grid)

        self.extra_addresses = QLabel("")
        self.extra_addresses.setObjectName("FaintLabel")
        self.extra_addresses.setWordWrap(True)
        self.extra_addresses.setVisible(False)
        card.add(self.extra_addresses)
        return card

    def _build_new_card(self) -> Card:
        card = Card("New configuration")

        modes = QHBoxLayout()
        modes.setSpacing(28)
        self.dhcp_radio = QRadioButton("DHCP")
        self.dhcp_radio.setToolTip("Obtain an IP address automatically")
        self.static_radio = QRadioButton("Static")
        self.static_radio.setToolTip("Use a fixed IP address")
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.dhcp_radio)
        self.mode_group.addButton(self.static_radio)
        self.static_radio.setChecked(True)
        self.dhcp_radio.toggled.connect(self._on_mode_changed)
        modes.addWidget(self.dhcp_radio)
        modes.addWidget(self.static_radio)
        modes.addStretch(1)
        card.add_layout(modes)

        fields = QGridLayout()
        fields.setHorizontalSpacing(16)
        fields.setVerticalSpacing(12)

        self.ip_field = ValidatedLineEdit(
            "IP address", "192.168.1.100", validator=validate_ip_text
        )
        self.mask_field = ValidatedLineEdit(
            "Subnet mask", "255.255.255.0", validator=validate_mask_text
        )
        self.gateway_field = ValidatedLineEdit(
            "Gateway (optional)", "192.168.1.1", validator=validate_ip_text
        )
        self.gateway_field.set_required(False)

        for widget in (self.ip_field, self.mask_field, self.gateway_field):
            widget.textChanged.connect(lambda _=None: self.configurationEdited.emit())

        fields.addWidget(self.ip_field, 0, 0)
        fields.addWidget(self.mask_field, 0, 1)
        fields.addWidget(self.gateway_field, 1, 0)
        fields.setColumnStretch(0, 1)
        fields.setColumnStretch(1, 1)
        card.add_layout(fields)

        self.hint = QLabel("")
        self.hint.setObjectName("WarningLabel")
        self.hint.setWordWrap(True)
        self.hint.setVisible(False)
        card.add(self.hint)
        return card

    def _build_actions(self) -> QWidget:
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        self.dry_run_button = QPushButton("Dry run")
        self.dry_run_button.setObjectName("SubtleButton")
        self.dry_run_button.setToolTip("Show what would change, without changing anything")
        self.dry_run_button.clicked.connect(self.dryRunRequested.emit)

        self.save_preset_button = QPushButton("Save as Preset")
        self.save_preset_button.setToolTip("Save this configuration for reuse")
        self.save_preset_button.clicked.connect(self.savePresetRequested.emit)

        self.apply_button = QPushButton("APPLY CONFIGURATION")
        self.apply_button.setObjectName("PrimaryButton")
        self.apply_button.setToolTip(
            "Apply this configuration immediately.\n"
            "The network connection may be interrupted."
        )
        self.apply_button.clicked.connect(self.applyRequested.emit)

        # There is no confirmation dialog, so the connection-loss caution has
        # to be visible before the button is pressed rather than after.
        self.caution = QLabel(
            f"{ICON_WARN}  Applies immediately - the connection may drop"
        )
        self.caution.setObjectName("WarningLabel")
        self.caution.setToolTip(
            "Pressing APPLY changes the adapter straight away. This can "
            "disconnect remote sessions, PLC connections and SCADA systems.\n\n"
            "The previous configuration is saved first and can be restored."
        )

        row.addWidget(self.dry_run_button)
        row.addWidget(self.save_preset_button)
        row.addStretch(1)
        row.addWidget(self.caution)
        row.addWidget(self.apply_button)
        return container

    # -------------------------------------------------------------- adapters
    def set_adapters(self, adapters: list[Adapter], select_guid: str = "") -> None:
        """Repopulate the selector, preserving the selection where possible."""
        previous = select_guid or (self._current.guid if self._current else "")
        self._adapters = adapters
        self._loading = True
        self.selector.clear()

        for adapter in adapters:
            icon = ICON_ACTIVE if adapter.oper_status.is_up else ICON_INACTIVE
            label = (
                f"{icon}  {adapter.friendly_name}"
                f"   ·   {adapter.oper_status.label}"
                f"   ·   {adapter.display_address}"
            )
            self.selector.addItem(label, adapter.guid)
            index = self.selector.count() - 1
            self.selector.setItemData(
                index,
                f"{adapter.description}\n{adapter.kind.value} adapter\nMAC {adapter.mac or 'n/a'}",
                Qt.ToolTipRole,
            )

        self._loading = False

        if not adapters:
            self._current = None
            self._show_no_adapters()
            self.adapterChanged.emit(None)
            return

        target = 0
        for index, adapter in enumerate(adapters):
            if previous and adapter.guid.lower() == previous.lower():
                target = index
                break
        self.selector.setCurrentIndex(target)
        self._on_selection_changed(target)

    def _show_no_adapters(self) -> None:
        self.status.set_status("No network adapters found", "danger", ICON_WARN)
        self.current_grid.set_rows(
            [("IP address", "-"), ("Subnet mask", "-"), ("Gateway", "-"), ("DHCP", "-")]
        )
        self.set_form_enabled(False)

    def _on_selection_changed(self, index: int) -> None:
        if self._loading or index < 0 or index >= len(self._adapters):
            return
        self._current = self._adapters[index]
        self._display(self._current)
        self.adapterChanged.emit(self._current)

    # --------------------------------------------------------------- display
    def _display(self, adapter: Adapter, prefill: bool = True) -> None:
        """Refresh the read-only display for an adapter.

        ``prefill`` seeds the editable form from the adapter's live values. It
        is off when only the presentation is being refreshed (a theme change),
        so a redraw never discards what the operator has typed.
        """
        role = "success" if adapter.oper_status.is_up else "muted"
        icon = ICON_ACTIVE if adapter.oper_status.is_up else ICON_INACTIVE
        self.status.set_status(adapter.oper_status.label, role, icon)

        primary = adapter.primary_ipv4
        rows = [
            ("IP address", primary.address if primary else "No IP address"),
            ("Subnet mask", primary.subnet_mask if primary else "-"),
            ("Gateway", adapter.primary_gateway or "none"),
            ("DHCP", "Enabled" if adapter.effective_dhcp else "Disabled"),
        ]
        if adapter.ipv6:
            rows.append(("IPv6", adapter.ipv6[0]))
        dns = [d for d in adapter.dns_servers if ":" not in d]
        if dns:
            rows.append(("DNS", ", ".join(dns[:2])))
        self.current_grid.set_rows(rows)

        # Multiple addresses must be visible, never silently hidden (section 8).
        extra = [str(a) for a in adapter.routable_ipv4[1:]]
        if extra:
            self.extra_addresses.setText(
                f"This adapter has {len(adapter.routable_ipv4)} IPv4 addresses. "
                f"Additional: {', '.join(extra)}. They will be preserved."
            )
            self.extra_addresses.setVisible(True)
        else:
            self.extra_addresses.setVisible(False)

        notes: list[str] = []
        if not adapter.is_configurable:
            notes.append("This adapter type cannot be configured.")
        if adapter.kind.value in ("Virtual", "VPN"):
            notes.append(
                f"{adapter.kind.value} adapter - changing it may disrupt "
                "virtual machines or VPN connectivity."
            )
        if primary is not None and primary.is_apipa:
            notes.append(
                "The current address is a Windows automatic address (APIPA). "
                "No DHCP server responded."
            )
        if notes:
            self.adapter_note.setText(f"{ICON_WARN}  " + "  ".join(notes))
            self.adapter_note.setVisible(True)
        else:
            self.adapter_note.setVisible(False)

        self.set_form_enabled(adapter.is_configurable)
        if prefill:
            self.prefill_from(adapter)

    def prefill_from(self, adapter: Adapter) -> None:
        """Seed the form with the adapter's present configuration."""
        if adapter.effective_dhcp:
            self.dhcp_radio.setChecked(True)
        else:
            self.static_radio.setChecked(True)

        primary = adapter.primary_ipv4
        if primary is not None and not primary.is_apipa:
            self.ip_field.set_text(primary.address)
            self.mask_field.set_text(primary.subnet_mask)
        else:
            self.ip_field.set_text("")
            self.mask_field.set_text("255.255.255.0")
        self.gateway_field.set_text(adapter.primary_gateway or "")
        self._on_mode_changed()

    def load_configuration(self, config: IPConfiguration) -> None:
        """Load a preset's values into the form."""
        if config.is_dhcp:
            self.dhcp_radio.setChecked(True)
        else:
            self.static_radio.setChecked(True)
            self.ip_field.set_text(config.ip_address)
            self.mask_field.set_text(config.subnet_mask)
            self.gateway_field.set_text(config.gateway)
        self._on_mode_changed()

    # ----------------------------------------------------------------- mode
    def _on_mode_changed(self, *_args) -> None:
        static = self.static_radio.isChecked()
        self.ip_field.set_enabled(static)
        self.mask_field.set_enabled(static)
        self.gateway_field.set_enabled(static)
        self.configurationEdited.emit()

    def set_form_enabled(self, enabled: bool) -> None:
        self.dhcp_radio.setEnabled(enabled)
        self.static_radio.setEnabled(enabled)
        static = enabled and self.static_radio.isChecked()
        self.ip_field.set_enabled(static)
        self.mask_field.set_enabled(static)
        self.gateway_field.set_enabled(static)
        self.save_preset_button.setEnabled(enabled)
        self.dry_run_button.setEnabled(enabled)

    def set_busy(self, busy: bool) -> None:
        """Lock the controls while a network operation is running."""
        self.selector.setEnabled(not busy)
        self.refresh_button.setEnabled(not busy)
        self.apply_button.setEnabled(not busy and self.is_ready)
        self.save_preset_button.setEnabled(not busy)
        self.dry_run_button.setEnabled(not busy)
        self.dhcp_radio.setEnabled(not busy)
        self.static_radio.setEnabled(not busy)
        static = not busy and self.static_radio.isChecked()
        self.ip_field.set_enabled(static)
        self.mask_field.set_enabled(static)
        self.gateway_field.set_enabled(static)

    # -------------------------------------------------------------- reading
    @property
    def current_adapter(self) -> Optional[Adapter]:
        return self._current

    @property
    def mode(self) -> ConfigMode:
        return ConfigMode.DHCP if self.dhcp_radio.isChecked() else ConfigMode.STATIC

    def configuration(self) -> Optional[IPConfiguration]:
        """Build a configuration from the form, or None if it cannot be built."""
        if self.mode is ConfigMode.DHCP:
            return IPConfiguration.dhcp()
        try:
            return IPConfiguration(
                mode=ConfigMode.STATIC,
                ip_address=self.ip_field.text(),
                prefix_length=mask_to_prefix(self.mask_field.text()),
                gateway=self.gateway_field.text(),
            )
        except (ValueError, TypeError):
            return None

    @property
    def is_ready(self) -> bool:
        """Whether Apply may be enabled (section 26)."""
        if self._current is None or not self._current.is_configurable:
            return False
        if self.mode is ConfigMode.DHCP:
            return True
        if not self.ip_field.is_valid or not self.mask_field.is_valid:
            return False
        if self.gateway_field.text() and not self.gateway_field.is_valid:
            return False
        return bool(self.ip_field.text() and self.mask_field.text())

    def set_hint(self, message: str) -> None:
        self.hint.setText(f"{ICON_WARN}  {message}" if message else "")
        self.hint.setVisible(bool(message))

    def apply_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.status.apply_palette(palette)
        if self._current is not None:
            self._display(self._current, prefill=False)
