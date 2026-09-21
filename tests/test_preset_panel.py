"""The saved-configuration panel: grouping and drag-and-drop.

Qt runs on the offscreen platform here, so these exercise the real widgets
without needing a desktop. They assert what the panel *reports* - the panel
never moves a preset itself; it emits and the main window decides.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt
from PySide6.QtGui import QDropEvent

from app.gui.preset_panel import (
    PRESET_MIME,
    FolderSection,
    PresetCard,
    PresetPanel,
    decode_preset_id,
    encode_preset_id,
)
from app.gui.styles import PALETTES
from app.models.configuration import ConfigMode
from app.storage.presets import Preset


def make_preset(preset_id: int, name: str, folder: str = "") -> Preset:
    return Preset(
        id=preset_id,
        name=name,
        adapter_identifier="",
        adapter_mac="",
        adapter_name="Ethernet",
        mode=ConfigMode.DHCP,
        ip_address="",
        subnet_mask="",
        gateway="",
        description="",
        created_at="2026-01-01",
        updated_at="2026-01-01",
        folder=folder,
    )


PRESETS = [
    make_preset(1, "PLC", "Line 3"),
    make_preset(2, "VISION", "Line 3"),
    make_preset(3, "DRIVE", "Line 7"),
    make_preset(4, "OFFICE"),
]


@pytest.fixture
def panel(qt_app):
    widget = PresetPanel(PALETTES["dark"])
    widget.set_presets(PRESETS, [])
    yield widget
    widget.deleteLater()


def sections(panel: PresetPanel) -> dict:
    """The FolderSection for each folder, keyed by folder name."""
    found = {}
    for index in range(panel.list_layout.count()):
        widget = panel.list_layout.itemAt(index).widget()
        if isinstance(widget, FolderSection):
            found[widget.folder] = widget
    return found


# QDropEvent does not take ownership of its QMimeData, it keeps a bare
# pointer. Letting the payload go out of scope leaves the event pointing at
# freed memory, which takes the interpreter down with an access violation
# instead of failing a test. Holding a reference keeps it alive.
_LIVE_PAYLOADS: list[QMimeData] = []


def drop_event(payload: bytes, mime_type: str = PRESET_MIME) -> QDropEvent:
    mime = QMimeData()
    mime.setData(mime_type, payload)
    _LIVE_PAYLOADS.append(mime)
    return QDropEvent(
        QPointF(5, 5), Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier
    )


# --------------------------------------------------------------- payloads
def test_a_preset_id_survives_the_round_trip():
    assert decode_preset_id(encode_preset_id(42)) == 42


@pytest.mark.parametrize("payload", [b"", b"not-a-number", b"\xff\xfe", b"7.5"])
def test_a_payload_that_is_not_a_preset_id_is_refused(payload):
    assert decode_preset_id(payload) is None


# --------------------------------------------------------------- grouping
def test_every_group_is_its_own_drop_target(panel):
    found = sections(panel)

    assert set(found) == {"Line 3", "Line 7", ""}
    for section in found.values():
        assert section.acceptDrops(), "the whole group must accept a drop"


def test_the_cards_live_inside_their_group(panel):
    line_3 = sections(panel)["Line 3"]
    names = [c.preset.name for c in line_3.findChildren(PresetCard)]
    assert names == ["PLC", "VISION"]


def test_a_collapsed_folder_keeps_its_heading_as_a_drop_target(panel):
    panel.set_collapsed_folders(["Line 7"])
    panel.set_presets(PRESETS, [])

    line_7 = sections(panel)["Line 7"]
    assert line_7.acceptDrops()
    assert line_7.findChildren(PresetCard) == [], "collapsed, so no cards are shown"


# ------------------------------------------------------------------ drops
def test_dropping_a_preset_on_a_folder_reports_the_move(panel):
    reported = []
    panel.presetDropped.connect(lambda pid, folder: reported.append((pid, folder)))

    sections(panel)["Line 7"].dropEvent(drop_event(encode_preset_id(1)))

    assert reported == [(1, "Line 7")]


def test_dropping_on_ungrouped_reports_no_folder(panel):
    reported = []
    panel.presetDropped.connect(lambda pid, folder: reported.append((pid, folder)))

    sections(panel)[""].dropEvent(drop_event(encode_preset_id(1)))

    assert reported == [(1, "")], "dropping on Ungrouped takes it out of its folder"


def test_a_drop_carrying_something_else_is_ignored(panel):
    reported = []
    panel.presetDropped.connect(lambda pid, folder: reported.append((pid, folder)))

    event = drop_event(b"C:/somewhere/else.txt", mime_type="text/uri-list")
    sections(panel)["Line 7"].dropEvent(event)

    assert reported == [], "only this application's own drags may be accepted"


def test_a_drop_with_a_corrupt_payload_is_ignored(panel):
    reported = []
    panel.presetDropped.connect(lambda pid, folder: reported.append((pid, folder)))

    sections(panel)["Line 7"].dropEvent(drop_event(b"not-a-number"))

    assert reported == []


def test_only_this_applications_drags_are_accepted_on_entry(panel):
    line_7 = sections(panel)["Line 7"]

    mine = drop_event(encode_preset_id(1))
    line_7.dragEnterEvent(mine)
    assert mine.isAccepted()

    foreign = drop_event(b"anything", mime_type="text/plain")
    foreign.setAccepted(False)
    line_7.dragEnterEvent(foreign)
    assert not foreign.isAccepted()
