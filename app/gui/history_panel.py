"""Recent activity list (specification section 24)."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.gui.styles import Palette, status_color
from app.gui.widgets import muted_label
from app.storage.history import HistoryEntry


class HistoryRow(QFrame):
    def __init__(self, entry: HistoryEntry, palette: Palette, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("CardFlat")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)

        role = {
            "success": "success",
            "rolled_back": "info",
            "dry_run": "muted",
        }.get(entry.status, "danger")

        top = QHBoxLayout()
        top.setSpacing(8)
        icon = QLabel(entry.icon)
        icon.setStyleSheet(
            f"color: {status_color(palette, role)}; font-size: 14px; font-weight: 700;"
        )
        moment = QLabel(entry.local_time)
        moment.setObjectName("ValueLabel")
        adapter = QLabel(entry.adapter_name)
        adapter.setStyleSheet("font-weight: 600;")
        top.addWidget(icon)
        top.addWidget(moment)
        top.addWidget(adapter)
        top.addStretch(1)
        date = QLabel(entry.local_date)
        date.setObjectName("FaintLabel")
        top.addWidget(date)
        layout.addLayout(top)

        if entry.transition:
            transition = QLabel(entry.transition)
            transition.setObjectName("FaintLabel")
            transition.setWordWrap(True)
            layout.addWidget(transition)

        if entry.message:
            message = QLabel(entry.message)
            message.setObjectName("FaintLabel")
            message.setWordWrap(True)
            layout.addWidget(message)


class HistoryPanel(QWidget):
    clearRequested = Signal()

    def __init__(self, palette: Palette, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._palette = palette

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("RECENT ACTIVITY")
        title.setObjectName("SectionLabel")
        header.addWidget(title)
        header.addStretch(1)
        self.clear_button = QPushButton("Clear")
        self.clear_button.setObjectName("SubtleButton")
        self.clear_button.setToolTip("Remove all history entries")
        self.clear_button.clicked.connect(self.clearRequested.emit)
        header.addWidget(self.clear_button)
        root.addLayout(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.container = QWidget()
        self.list_layout = QVBoxLayout(self.container)
        self.list_layout.setContentsMargins(0, 0, 6, 0)
        self.list_layout.setSpacing(8)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.container)
        root.addWidget(self.scroll, 1)

        self.empty_label = muted_label("No activity recorded yet.")
        self.empty_label.setAlignment(Qt.AlignTop)
        root.addWidget(self.empty_label)

    def set_entries(self, entries: list[HistoryEntry]) -> None:
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        self.empty_label.setVisible(not entries)
        self.scroll.setVisible(bool(entries))
        self.clear_button.setEnabled(bool(entries))

        for index, entry in enumerate(entries):
            self.list_layout.insertWidget(index, HistoryRow(entry, self._palette))

    def apply_palette(self, palette: Palette) -> None:
        self._palette = palette
