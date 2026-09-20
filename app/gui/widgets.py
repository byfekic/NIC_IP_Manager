"""Reusable presentation widgets.

Status is always communicated with an icon *and* text, never colour alone
(specification section 44).
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.gui.styles import Palette, status_color

# Status glyphs, chosen to render with the default Windows UI font.
ICON_OK = "✓"        # check mark
ICON_FAIL = "✕"      # ballot X
ICON_WARN = "⚠"      # warning sign
ICON_ACTIVE = "●"    # filled circle
ICON_INACTIVE = "○"  # hollow circle
ICON_REFRESH = "↻"   # clockwise open circle arrow
ICON_MENU = "⋮"      # vertical ellipsis
ICON_ARROW = "→"     # rightwards arrow
ICON_RESTORE = "↺"   # anticlockwise open circle arrow
ICON_EXPANDED = "▾"  # black down-pointing small triangle
ICON_COLLAPSED = "▸" # black right-pointing small triangle


class Card(QFrame):
    """A titled surface. The building block of the whole interface."""

    def __init__(
        self,
        title: str = "",
        parent: Optional[QWidget] = None,
        flat: bool = False,
        spacing: int = 12,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("CardFlat" if flat else "Card")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(20, 18, 20, 18)
        self._layout.setSpacing(spacing)

        self.header: Optional[QHBoxLayout] = None
        if title:
            self.header = QHBoxLayout()
            self.header.setSpacing(8)
            label = QLabel(title.upper())
            label.setObjectName("SectionLabel")
            self.header.addWidget(label)
            self.header.addStretch(1)
            self._layout.addLayout(self.header)

    def body(self) -> QVBoxLayout:
        return self._layout

    def add(self, widget: QWidget) -> QWidget:
        self._layout.addWidget(widget)
        return widget

    def add_layout(self, layout) -> None:
        self._layout.addLayout(layout)

    def add_header_widget(self, widget: QWidget) -> QWidget:
        """Place a control on the right-hand side of the card title row."""
        if self.header is not None:
            self.header.addWidget(widget)
        else:
            self._layout.addWidget(widget)
        return widget


class StatusIndicator(QWidget):
    """An icon plus a text label describing a state."""

    def __init__(self, palette: Palette, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._palette = palette
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.icon = QLabel(ICON_INACTIVE)
        icon_font = QFont()
        icon_font.setPointSize(11)
        self.icon.setFont(icon_font)

        self.label = QLabel("Unknown")
        self.label.setObjectName("MutedLabel")

        layout.addWidget(self.icon)
        layout.addWidget(self.label)
        layout.addStretch(1)

    def set_status(self, text: str, role: str = "muted", icon: str = ICON_ACTIVE) -> None:
        colour = status_color(self._palette, role)
        self.icon.setText(icon)
        self.icon.setStyleSheet(f"color: {colour};")
        self.label.setText(text)
        self.label.setStyleSheet(
            f"color: {colour}; font-size: 14px; font-weight: 600;"
            if role != "muted"
            else ""
        )

    def apply_palette(self, palette: Palette) -> None:
        self._palette = palette


class KeyValueGrid(QFrame):
    """A read-only two-column display of configuration values."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("CardFlat")
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(16, 14, 16, 14)
        self._grid.setHorizontalSpacing(18)
        self._grid.setVerticalSpacing(9)
        self._grid.setColumnStretch(1, 1)
        self._rows: dict[str, QLabel] = {}

    def set_rows(self, rows: list[tuple[str, str]]) -> None:
        """Replace the displayed rows."""
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Reparent immediately: deleteLater() is deferred to the next
                # event loop pass and the old row would stay painted until then.
                widget.setParent(None)
                widget.deleteLater()
        self._rows.clear()

        for index, (key, value) in enumerate(rows):
            key_label = QLabel(key)
            key_label.setObjectName("FieldLabel")
            key_label.setMinimumWidth(120)
            value_label = QLabel(value)
            value_label.setObjectName("ValueLabel")
            value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            value_label.setWordWrap(True)
            self._grid.addWidget(key_label, index, 0, Qt.AlignTop | Qt.AlignLeft)
            # No AlignTop on the value: a wrapped value needs the row to grow,
            # and an aligned cell is given only its unwrapped height.
            self._grid.addWidget(value_label, index, 1)
            self._rows[key] = value_label

    def set_value(self, key: str, value: str) -> None:
        if key in self._rows:
            self._rows[key].setText(value)


class ValidatedLineEdit(QWidget):
    """A labelled input that reports its own validity underneath itself."""

    textChanged = Signal(str)
    editingFinished = Signal()

    def __init__(
        self,
        label: str,
        placeholder: str = "",
        parent: Optional[QWidget] = None,
        validator=None,
    ) -> None:
        super().__init__(parent)
        self._validator = validator
        self._required = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        self.label = QLabel(label)
        self.label.setObjectName("FieldLabel")

        self.field = QLineEdit()
        self.field.setPlaceholderText(placeholder)
        self.field.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # A stylesheet min-height is advisory; an explicit minimum stops a
        # crowded layout from squeezing the field and clipping the address.
        self.field.setMinimumHeight(38)
        self.field.textChanged.connect(self._on_text_changed)
        self.field.editingFinished.connect(self.editingFinished.emit)

        self.message = QLabel("")
        self.message.setObjectName("ErrorLabel")
        self.message.setVisible(False)
        self.message.setWordWrap(True)

        layout.addWidget(self.label)
        layout.addWidget(self.field)
        layout.addWidget(self.message)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        # label + field + spacing; keeps the input legible even when a parent
        # layout is under pressure.
        self.setMinimumHeight(63)

    # ------------------------------------------------------------ contents
    def text(self) -> str:
        return self.field.text().strip()

    def set_text(self, value: str) -> None:
        self.field.setText(value or "")

    def clear(self) -> None:
        self.field.clear()
        self.set_error("")

    def set_enabled(self, enabled: bool) -> None:
        self.field.setEnabled(enabled)
        self.label.setEnabled(enabled)
        if not enabled:
            self.set_error("")

    def set_required(self, required: bool) -> None:
        self._required = required

    # ------------------------------------------------------------ validity
    def _on_text_changed(self, text: str) -> None:
        self.validate(live=True)
        self.textChanged.emit(text)

    def validate(self, live: bool = False) -> bool:
        """Re-run the field validator. In live mode an empty field is quiet."""
        if not self.field.isEnabled():
            self.set_error("")
            return True
        value = self.text()
        if not value:
            if live or not self._required:
                self.set_error("")
                return not self._required
            self.set_error("Required.")
            return False
        if self._validator is None:
            self.set_error("")
            return True
        ok, message = self._validator(value)
        self.set_error("" if ok else message)
        return ok

    @property
    def is_valid(self) -> bool:
        value = self.text()
        if not value:
            return not self._required
        if self._validator is None:
            return True
        return self._validator(value)[0]

    def set_error(self, message: str) -> None:
        self.message.setText(message)
        self.message.setVisible(bool(message))
        self.field.setProperty("invalid", "true" if message else "false")
        # Re-polish so the property selector in the stylesheet takes effect.
        style = self.field.style()
        style.unpolish(self.field)
        style.polish(self.field)

    def set_warning(self, message: str) -> None:
        self.message.setObjectName("WarningLabel" if message else "ErrorLabel")
        self.message.setText(message)
        self.message.setVisible(bool(message))


class Divider(QFrame):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("Divider")
        self.setFixedHeight(1)


def section_label(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setObjectName("SectionLabel")
    return label


def muted_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("MutedLabel")
    label.setWordWrap(True)
    return label
