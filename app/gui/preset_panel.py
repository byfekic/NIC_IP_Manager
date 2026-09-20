"""Saved configurations list (specification sections 16, 18, 19).

Presets carry an optional folder name, and the list groups them under
collapsible headers. Folders are labels rather than paths: one level, no
nesting, which is what a technician organising by line or by customer needs
and keeps the panel readable on a laptop screen.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.gui.styles import Palette, status_color
from app.gui.widgets import (
    ICON_COLLAPSED,
    ICON_EXPANDED,
    ICON_MENU,
    ICON_OK,
    ICON_WARN,
    muted_label,
)
from app.models.adapter import Adapter
from app.storage.presets import Preset

# The key the ungrouped section uses in the collapsed set. Presets store the
# empty string for "no folder", so it doubles as the sentinel here.
UNGROUPED = ""


class PresetCard(QFrame):
    """One saved configuration, with Apply and an overflow menu."""

    applyRequested = Signal(int)
    editRequested = Signal(int)
    renameRequested = Signal(int)
    duplicateRequested = Signal(int)
    deleteRequested = Signal(int)
    loadRequested = Signal(int)
    moveRequested = Signal(int)

    def __init__(
        self,
        preset: Preset,
        palette: Palette,
        matches_current: bool = False,
        adapter_available: bool = True,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("CardFlat")
        self.preset = preset
        self._palette = palette

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(8)
        name = QLabel(preset.name)
        name.setStyleSheet("font-size: 15px; font-weight: 700;")
        name.setWordWrap(True)
        top.addWidget(name)
        top.addStretch(1)

        if matches_current:
            badge = QLabel(f"{ICON_OK} Active")
            badge.setStyleSheet(
                f"color: {status_color(palette, 'success')}; font-size: 12px; font-weight: 700;"
            )
            badge.setToolTip("The selected adapter currently matches this configuration")
            top.addWidget(badge)
        elif not adapter_available:
            badge = QLabel(f"{ICON_WARN} Adapter missing")
            badge.setStyleSheet(
                f"color: {status_color(palette, 'warning')}; font-size: 12px; font-weight: 700;"
            )
            badge.setToolTip("The adapter this configuration was saved for was not found")
            top.addWidget(badge)
        layout.addLayout(top)

        adapter_line = QLabel(preset.adapter_name or "Not linked to a specific adapter")
        adapter_line.setObjectName("FaintLabel")
        layout.addWidget(adapter_line)

        summary = QLabel(preset.summary)
        summary.setObjectName("ValueLabel")
        layout.addWidget(summary)

        if preset.mode.value == "static" and preset.gateway:
            gateway = QLabel(preset.gateway_summary)
            gateway.setObjectName("FaintLabel")
            layout.addWidget(gateway)

        if preset.description:
            description = QLabel(preset.description)
            description.setObjectName("FaintLabel")
            description.setWordWrap(True)
            layout.addWidget(description)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addStretch(1)

        load = QPushButton("Load")
        load.setObjectName("SubtleButton")
        load.setToolTip("Copy these values into the form without applying them")
        load.clicked.connect(lambda: self.loadRequested.emit(preset.id))

        apply_button = QPushButton("APPLY")
        apply_button.setToolTip("Apply this configuration now")
        apply_button.clicked.connect(lambda: self.applyRequested.emit(preset.id))

        menu_button = QPushButton(ICON_MENU)
        menu_button.setObjectName("IconButton")
        menu_button.setToolTip("More actions")
        menu_button.clicked.connect(lambda: self._show_menu(menu_button))

        actions.addWidget(load)
        actions.addWidget(apply_button)
        actions.addWidget(menu_button)
        layout.addLayout(actions)

    def _show_menu(self, anchor: QWidget) -> None:
        menu = QMenu(self)
        menu.addAction("Edit", lambda: self.editRequested.emit(self.preset.id))
        menu.addAction("Rename", lambda: self.renameRequested.emit(self.preset.id))
        menu.addAction("Move to folder…", lambda: self.moveRequested.emit(self.preset.id))
        menu.addAction("Duplicate", lambda: self.duplicateRequested.emit(self.preset.id))
        menu.addSeparator()
        menu.addAction("Delete", lambda: self.deleteRequested.emit(self.preset.id))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))


class FolderHeader(QFrame):
    """Collapsible heading for one group of saved configurations."""

    toggled = Signal(str)
    renameRequested = Signal(str)

    def __init__(
        self,
        folder: str,
        label: str,
        count: int,
        collapsed: bool,
        can_rename: bool = True,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("FolderHeader")
        self.folder = folder

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(6)

        arrow = ICON_COLLAPSED if collapsed else ICON_EXPANDED
        self.toggle = QPushButton(f"{arrow}  {label}   ({count})")
        self.toggle.setObjectName("FolderToggle")
        self.toggle.setCursor(Qt.PointingHandCursor)
        self.toggle.setToolTip("Show or hide the configurations in this folder")
        self.toggle.clicked.connect(lambda: self.toggled.emit(self.folder))
        layout.addWidget(self.toggle)
        layout.addStretch(1)

        if can_rename:
            menu_button = QPushButton(ICON_MENU)
            menu_button.setObjectName("IconButton")
            menu_button.setToolTip("Folder actions")
            menu_button.clicked.connect(lambda: self._show_menu(menu_button))
            layout.addWidget(menu_button)

    def _show_menu(self, anchor: QWidget) -> None:
        menu = QMenu(self)
        menu.addAction("Rename folder…", lambda: self.renameRequested.emit(self.folder))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))


class PresetPanel(QWidget):
    """Scrollable list of saved configurations plus import/export."""

    applyRequested = Signal(int)
    editRequested = Signal(int)
    renameRequested = Signal(int)
    duplicateRequested = Signal(int)
    deleteRequested = Signal(int)
    loadRequested = Signal(int)
    moveRequested = Signal(int)
    renameFolderRequested = Signal(str)
    collapsedFoldersChanged = Signal(list)
    importRequested = Signal()
    exportRequested = Signal()

    def __init__(self, palette: Palette, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._collapsed: set[str] = set()
        self._presets: list[Preset] = []
        self._adapters: list[Adapter] = []
        self._current_adapter: Optional[Adapter] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("SAVED CONFIGURATIONS")
        title.setObjectName("SectionLabel")
        header.addWidget(title)
        header.addStretch(1)

        self.import_button = QPushButton("Import")
        self.import_button.setObjectName("SubtleButton")
        self.import_button.setToolTip("Import configurations from a JSON file")
        self.import_button.clicked.connect(self.importRequested.emit)
        self.export_button = QPushButton("Export")
        self.export_button.setObjectName("SubtleButton")
        self.export_button.setToolTip("Export all configurations to a JSON file")
        self.export_button.clicked.connect(self.exportRequested.emit)
        header.addWidget(self.import_button)
        header.addWidget(self.export_button)
        root.addLayout(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.container = QWidget()
        self.list_layout = QVBoxLayout(self.container)
        self.list_layout.setContentsMargins(0, 0, 6, 0)
        self.list_layout.setSpacing(10)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.container)
        root.addWidget(self.scroll, 1)

        self.empty_label = muted_label(
            "No saved configurations yet.\n\n"
            "Set up a configuration on the left, then choose "
            "'Save as Preset' to store it here."
        )
        self.empty_label.setAlignment(Qt.AlignTop)
        root.addWidget(self.empty_label)

    # ------------------------------------------------------------- folders
    def set_collapsed_folders(self, folders: list[str]) -> None:
        """Restore which folders were collapsed, without redrawing."""
        self._collapsed = {str(f) for f in folders}

    def collapsed_folders(self) -> list[str]:
        return sorted(self._collapsed)

    def _toggle_folder(self, folder: str) -> None:
        if folder in self._collapsed:
            self._collapsed.discard(folder)
        else:
            self._collapsed.add(folder)
        self.collapsedFoldersChanged.emit(self.collapsed_folders())
        self.set_presets(self._presets, self._adapters, self._current_adapter)

    @staticmethod
    def _group(presets: list[Preset]) -> list[tuple[str, list[Preset]]]:
        """Bucket by folder: named folders alphabetically, ungrouped last."""
        groups: dict[str, list[Preset]] = {}
        for preset in presets:
            groups.setdefault(preset.folder, []).append(preset)
        return sorted(groups.items(), key=lambda item: (item[0] == UNGROUPED, item[0].lower()))

    # ------------------------------------------------------------ contents
    def set_presets(
        self,
        presets: list[Preset],
        adapters: list[Adapter],
        current_adapter: Optional[Adapter] = None,
    ) -> None:
        """Rebuild the list, marking active and unresolvable entries."""
        self._presets = presets
        self._adapters = adapters
        self._current_adapter = current_adapter

        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        self.empty_label.setVisible(not presets)
        self.scroll.setVisible(bool(presets))

        groups = self._group(presets)
        # Someone who has never made a folder should see exactly what they saw
        # before: a plain list, with no "Ungrouped" heading above it.
        has_folders = any(folder for folder, _ in groups)

        index = 0
        for folder, entries in groups:
            if folder or has_folders:
                header = FolderHeader(
                    folder,
                    folder or "Ungrouped",
                    len(entries),
                    collapsed=folder in self._collapsed,
                    can_rename=bool(folder),
                )
                header.toggled.connect(self._toggle_folder)
                header.renameRequested.connect(self.renameFolderRequested.emit)
                self.list_layout.insertWidget(index, header)
                index += 1
                if folder in self._collapsed:
                    continue

            for preset in entries:
                self.list_layout.insertWidget(index, self._build_card(preset, adapters, current_adapter))
                index += 1

    def _build_card(
        self,
        preset: Preset,
        adapters: list[Adapter],
        current_adapter: Optional[Adapter],
    ) -> PresetCard:
        available = True
        if preset.adapter_identifier or preset.adapter_mac:
            available = any(preset.matches_adapter(a) for a in adapters)

        card = PresetCard(
            preset,
            self._palette,
            matches_current=self._matches_current(preset, current_adapter),
            adapter_available=available,
        )
        card.applyRequested.connect(self.applyRequested.emit)
        card.editRequested.connect(self.editRequested.emit)
        card.renameRequested.connect(self.renameRequested.emit)
        card.duplicateRequested.connect(self.duplicateRequested.emit)
        card.deleteRequested.connect(self.deleteRequested.emit)
        card.loadRequested.connect(self.loadRequested.emit)
        card.moveRequested.connect(self.moveRequested.emit)
        return card

    @staticmethod
    def _matches_current(preset: Preset, adapter: Optional[Adapter]) -> bool:
        """True when the selected adapter already has this configuration."""
        if adapter is None or not preset.matches_adapter(adapter):
            return False
        if preset.mode.value == "dhcp":
            return adapter.effective_dhcp
        if adapter.effective_dhcp:
            return False
        primary = adapter.primary_ipv4
        if primary is None:
            return False
        return (
            primary.address == preset.ip_address
            and primary.subnet_mask == preset.subnet_mask
        )

    def set_busy(self, busy: bool) -> None:
        self.setEnabled(not busy)

    def apply_palette(self, palette: Palette) -> None:
        self._palette = palette
