"""Visual language: colour tokens and the Qt stylesheet (sections 5, 51).

Colour is used only to communicate status, and every status is also carried by
an icon and a text label so the interface never depends on colour alone
(section 44).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    name: str
    window: str
    surface: str
    surface_raised: str
    border: str
    border_strong: str
    text: str
    text_muted: str
    text_faint: str
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_text: str
    success: str
    warning: str
    danger: str
    danger_hover: str
    info: str
    field: str
    field_focus: str
    selection: str


DARK = Palette(
    name="dark",
    window="#0f1419",
    surface="#161c23",
    surface_raised="#1d252e",
    border="#2a3541",
    border_strong="#3a4753",
    text="#e6edf3",
    text_muted="#9aa8b5",
    text_faint="#6b7885",
    accent="#2f81f7",
    accent_hover="#4a94f8",
    accent_pressed="#1f6feb",
    accent_text="#ffffff",
    success="#3fb950",
    warning="#d29922",
    danger="#f85149",
    danger_hover="#ff6b63",
    info="#58a6ff",
    field="#0d1117",
    field_focus="#0d1117",
    selection="#1f4b7f",
)

LIGHT = Palette(
    name="light",
    window="#f4f6f8",
    surface="#ffffff",
    surface_raised="#ffffff",
    border="#d8dee4",
    border_strong="#c2cbd3",
    text="#1c2430",
    text_muted="#5a6773",
    text_faint="#8b96a1",
    accent="#0969da",
    accent_hover="#1a7ae8",
    accent_pressed="#0757ba",
    accent_text="#ffffff",
    success="#1a7f37",
    warning="#9a6700",
    danger="#cf222e",
    danger_hover="#e0333f",
    info="#0969da",
    field="#ffffff",
    field_focus="#ffffff",
    selection="#cfe3fb",
)

PALETTES = {"dark": DARK, "light": LIGHT}

# Status colours are resolved through this map so status semantics stay in one
# place rather than being spelled out at each call site.
STATUS_ROLES = ("success", "warning", "danger", "info", "muted")


def status_color(palette: Palette, role: str) -> str:
    return {
        "success": palette.success,
        "warning": palette.warning,
        "danger": palette.danger,
        "info": palette.info,
        "muted": palette.text_muted,
    }.get(role, palette.text_muted)


FONT_STACK = '"Segoe UI Variable Text", "Segoe UI", Inter, system-ui, sans-serif'
MONO_STACK = '"Cascadia Mono", Consolas, "Courier New", monospace'


def build_stylesheet(palette: Palette) -> str:
    """Produce the application-wide Qt stylesheet for a palette."""
    p = palette
    return f"""
* {{
    font-family: {FONT_STACK};
}}

QWidget {{
    color: {p.text};
    font-size: 14px;
}}

/* Only real top-level surfaces paint the window colour. Giving every QWidget
   a background makes labels sitting on a card paint the window colour over
   the card, which reads as a stray highlight box behind the text. */
QMainWindow, QDialog, QStatusBar {{
    background-color: {p.window};
}}

QLabel, QRadioButton, QCheckBox {{
    background: transparent;
}}

/* Layout containers are see-through so the window colour shows through and
   only cards paint a surface. Without this they fall back to the default
   palette, which paints white in the dark theme. */
QScrollArea,
QScrollArea > QWidget,
QScrollArea > QWidget > QWidget,
QSplitter,
QTabWidget,
QTabWidget > QWidget,
QStackedWidget {{
    background: transparent;
}}

QSplitter::handle {{
    background: transparent;
}}

QToolTip {{
    background-color: {p.surface_raised};
    color: {p.text};
    border: 1px solid {p.border_strong};
    padding: 6px 8px;
    border-radius: 6px;
}}

/* ------------------------------------------------------------- cards */
QFrame#Card {{
    background-color: {p.surface};
    border: 1px solid {p.border};
    border-radius: 12px;
}}

QFrame#CardFlat {{
    background-color: {p.surface_raised};
    border: 1px solid {p.border};
    border-radius: 10px;
}}

QFrame#Divider {{
    background-color: {p.border};
    border: none;
    max-height: 1px;
    min-height: 1px;
}}

/* ---------------------------------------------------------- typography */
QLabel#SectionLabel {{
    color: {p.text_faint};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1.2px;
}}

QLabel#TitleLabel {{
    font-size: 20px;
    font-weight: 700;
    letter-spacing: 0.5px;
}}

QLabel#AppTitle {{
    font-size: 17px;
    font-weight: 700;
    letter-spacing: 2px;
}}

QLabel#FieldLabel {{
    color: {p.text_muted};
    font-size: 13px;
}}

QLabel#ValueLabel {{
    font-family: {MONO_STACK};
    font-size: 14px;
    color: {p.text};
}}

QLabel#MutedLabel {{
    color: {p.text_muted};
    font-size: 13px;
}}

QLabel#FaintLabel {{
    color: {p.text_faint};
    font-size: 12px;
}}

QLabel#ErrorLabel {{
    color: {p.danger};
    font-size: 12px;
}}

QLabel#WarningLabel {{
    color: {p.warning};
    font-size: 12px;
}}

/* -------------------------------------------------------------- inputs */
QLineEdit, QPlainTextEdit, QTextEdit {{
    background-color: {p.field};
    border: 1px solid {p.border_strong};
    border-radius: 8px;
    padding: 9px 11px;
    min-height: 20px;
    color: {p.text};
    font-family: {MONO_STACK};
    font-size: 14px;
    selection-background-color: {p.selection};
    selection-color: {p.text};
}}

QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border: 2px solid {p.accent};
    padding: 8px 10px;
}}

QLineEdit:disabled, QPlainTextEdit:disabled {{
    background-color: {p.surface};
    color: {p.text_faint};
    border-color: {p.border};
}}

QLineEdit[invalid="true"] {{
    border: 2px solid {p.danger};
    padding: 8px 10px;
}}

QLineEdit#SearchField {{
    font-family: {FONT_STACK};
}}

/* ------------------------------------------------------------ combobox */
QComboBox {{
    background-color: {p.field};
    border: 1px solid {p.border_strong};
    border-radius: 8px;
    padding: 10px 12px;
    color: {p.text};
    font-size: 14px;
    min-height: 22px;
}}

QComboBox:hover {{
    border-color: {p.accent};
}}

QComboBox:focus {{
    border: 2px solid {p.accent};
    padding: 9px 11px;
}}

QComboBox:disabled {{
    color: {p.text_faint};
    background-color: {p.surface};
}}

QComboBox::drop-down {{
    border: none;
    width: 28px;
}}

QComboBox QAbstractItemView {{
    background-color: {p.surface_raised};
    border: 1px solid {p.border_strong};
    border-radius: 8px;
    padding: 4px;
    outline: none;
    selection-background-color: {p.selection};
    selection-color: {p.text};
}}

QComboBox QAbstractItemView::item {{
    padding: 8px 10px;
    border-radius: 6px;
    min-height: 34px;
}}

/* ------------------------------------------------------------- buttons */
QPushButton {{
    background-color: {p.surface_raised};
    border: 1px solid {p.border_strong};
    border-radius: 8px;
    padding: 10px 18px;
    color: {p.text};
    font-size: 14px;
    font-weight: 600;
    min-height: 20px;
}}

QPushButton:hover {{
    border-color: {p.accent};
    background-color: {p.surface};
}}

QPushButton:pressed {{
    background-color: {p.window};
}}

QPushButton:focus {{
    border: 2px solid {p.accent};
    padding: 9px 17px;
}}

QPushButton:disabled {{
    color: {p.text_faint};
    border-color: {p.border};
    background-color: {p.surface};
}}

QPushButton#PrimaryButton {{
    background-color: {p.accent};
    border: 1px solid {p.accent};
    color: {p.accent_text};
    font-size: 15px;
    font-weight: 700;
    padding: 13px 26px;
    letter-spacing: 0.6px;
}}

QPushButton#PrimaryButton:hover {{
    background-color: {p.accent_hover};
    border-color: {p.accent_hover};
}}

QPushButton#PrimaryButton:pressed {{
    background-color: {p.accent_pressed};
}}

QPushButton#PrimaryButton:focus {{
    border: 2px solid {p.text};
    padding: 12px 25px;
}}

QPushButton#PrimaryButton:disabled {{
    background-color: {p.surface_raised};
    border-color: {p.border};
    color: {p.text_faint};
}}

QPushButton#DangerButton {{
    background-color: {p.danger};
    border-color: {p.danger};
    color: #ffffff;
}}

QPushButton#DangerButton:hover {{
    background-color: {p.danger_hover};
    border-color: {p.danger_hover};
}}

QPushButton#SubtleButton {{
    background-color: transparent;
    border: 1px solid {p.border};
    color: {p.text_muted};
    font-weight: 600;
}}

QPushButton#SubtleButton:hover {{
    color: {p.text};
    border-color: {p.accent};
}}

QPushButton#IconButton {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 6px 10px;
    color: {p.text_muted};
    font-size: 15px;
    font-weight: 700;
}}

QPushButton#IconButton:hover {{
    background-color: {p.surface_raised};
    border-color: {p.border_strong};
    color: {p.text};
}}

/* --------------------------------------------------------- folder rows */
QFrame#FolderHeader {{
    background: transparent;
    border: none;
}}

QPushButton#FolderToggle {{
    background: transparent;
    border: none;
    padding: 4px 2px;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.6px;
    color: {p.text_muted};
    text-align: left;
}}

QPushButton#FolderToggle:hover {{
    color: {p.accent};
}}

/* ------------------------------------------------------- radio buttons */
QRadioButton {{
    spacing: 10px;
    padding: 6px 2px;
    font-size: 15px;
    font-weight: 600;
}}

/* width/height are the content box, so the border is added on top of them.
   Both states must sum to the same 22px or the indicator changes size when it
   is selected and squeezes the label next to it. */
QRadioButton::indicator {{
    width: 18px;
    height: 18px;
    border-radius: 11px;
    border: 2px solid {p.border_strong};
    background-color: {p.field};
}}

QRadioButton::indicator:hover {{
    border-color: {p.accent};
}}

QRadioButton::indicator:checked {{
    width: 10px;
    height: 10px;
    border: 6px solid {p.accent};
    background-color: {p.field};
}}

QRadioButton:focus {{
    color: {p.accent};
}}

QCheckBox {{
    spacing: 9px;
    font-size: 13px;
}}

QCheckBox::indicator {{
    width: 17px;
    height: 17px;
    border-radius: 4px;
    border: 2px solid {p.border_strong};
    background-color: {p.field};
}}

QCheckBox::indicator:hover {{
    border-color: {p.accent};
}}

QCheckBox::indicator:checked {{
    background-color: {p.accent};
    border-color: {p.accent};
}}

/* --------------------------------------------------------------- tabs */
QTabWidget::pane {{
    border: none;
    background: transparent;
}}

QTabBar::tab {{
    background: transparent;
    color: {p.text_muted};
    padding: 9px 18px;
    margin-right: 4px;
    border: 1px solid transparent;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 600;
}}

QTabBar::tab:selected {{
    color: {p.text};
    background-color: {p.surface_raised};
    border-color: {p.border};
}}

QTabBar::tab:hover:!selected {{
    color: {p.text};
}}

QTabBar:focus {{
    border: none;
}}

/* ------------------------------------------------------------ scroll */
QScrollArea {{
    background: transparent;
    border: none;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}

QScrollBar::handle:vertical {{
    background: {p.border_strong};
    border-radius: 5px;
    min-height: 30px;
}}

QScrollBar::handle:vertical:hover {{
    background: {p.text_faint};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}

QScrollBar::handle:horizontal {{
    background: {p.border_strong};
    border-radius: 5px;
    min-width: 30px;
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}

/* ------------------------------------------------------------ dialogs */
QDialog {{
    background-color: {p.window};
}}

QProgressBar {{
    border: 1px solid {p.border};
    border-radius: 6px;
    background-color: {p.field};
    height: 6px;
    text-align: center;
}}

QProgressBar::chunk {{
    background-color: {p.accent};
    border-radius: 5px;
}}

QMenu {{
    background-color: {p.surface_raised};
    border: 1px solid {p.border_strong};
    border-radius: 8px;
    padding: 6px;
}}

QMenu::item {{
    padding: 8px 26px 8px 14px;
    border-radius: 6px;
    color: {p.text};
}}

QMenu::item:selected {{
    background-color: {p.selection};
}}

QMenu::separator {{
    height: 1px;
    background: {p.border};
    margin: 5px 8px;
}}

QStatusBar {{
    background-color: {p.surface};
    color: {p.text_muted};
    border-top: 1px solid {p.border};
}}

QStatusBar::item {{
    border: none;
}}
"""
