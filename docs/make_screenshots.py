"""Portfolio screenshot driver for IP CHANGER.

Runs the real application widgets against a synthetic adapter set and an
isolated data directory, so nothing from the operator's real network shows up
in the images and the shots are reproducible.

    python docs/make_screenshots.py <project_root> <output_dir> <temp_appdata>
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

PROJECT = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
APPDATA = Path(sys.argv[3]).resolve()

# Redirect every writable location (db, settings, logs) before app import.
if APPDATA.exists():
    shutil.rmtree(APPDATA, ignore_errors=True)
APPDATA.mkdir(parents=True, exist_ok=True)
os.environ["LOCALAPPDATA"] = str(APPDATA)
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import QMimeData, QPoint, Qt, QTimer
from PySide6.QtGui import QDragEnterEvent, QFont, QIcon
from PySide6.QtWidgets import QApplication

from app.models.adapter import Adapter, IPv4Address, OperStatus
from app.models.configuration import IPConfiguration

IF_ETHERNET, IF_WIFI = 6, 71


def build_adapters() -> list[Adapter]:
    """Synthetic adapters using documentation-range addresses only."""
    return [
        Adapter(
            guid="{8F2C41A0-1D3B-4E77-9C5A-0A1B2C3D4E5F}",
            if_index=12,
            friendly_name="Ethernet",
            description="Intel(R) Ethernet Connection I219-LM",
            mac="02-1A-2B-3C-4D-5E",
            if_type=IF_ETHERNET,
            oper_status=OperStatus.UP,
            dhcp_enabled=False,
            ipv4=[IPv4Address("192.168.10.25", 24, "Manual")],
            ipv6=["2001:db8:4::21a:2bff:fe3c:4d5e"],
            gateways=["192.168.10.1"],
            dns_servers=["192.168.10.1", "192.168.10.2"],
            mtu=1500,
            link_speed=1_000_000_000,
        ),
        Adapter(
            guid="{B71E9D42-6A88-4C10-8E3F-77D6C9A0B412}",
            if_index=18,
            friendly_name="Wi-Fi",
            description="Intel(R) Wi-Fi 6 AX201 160MHz",
            mac="02-9F-8E-7D-6C-5B",
            if_type=IF_WIFI,
            oper_status=OperStatus.UP,
            dhcp_enabled=True,
            ipv4=[IPv4Address("10.14.3.87", 24, "Dhcp")],
            ipv6=["2001:db8:9::14e:3ff:fe57:2a1c"],
            gateways=["10.14.3.1"],
            dns_servers=["10.14.3.1"],
            dhcp_server="10.14.3.1",
            mtu=1500,
            link_speed=866_000_000,
        ),
        Adapter(
            guid="{4C0D77E1-33AB-49F2-B8D5-1E6F0A9C2277}",
            if_index=24,
            friendly_name="Ethernet 2",
            description="Realtek PCIe GbE Family Controller",
            mac="02-44-55-66-77-88",
            if_type=IF_ETHERNET,
            oper_status=OperStatus.DOWN,
            dhcp_enabled=True,
            mtu=1500,
        ),
        Adapter(
            guid="{D9A17C55-2B4E-4F80-A6C3-90BE55D14C09}",
            if_index=31,
            friendly_name="vEthernet (WSL)",
            description="Hyper-V Virtual Ethernet Adapter",
            mac="02-15-5D-01-02-03",
            if_type=IF_ETHERNET,
            oper_status=OperStatus.UP,
            dhcp_enabled=False,
            ipv4=[IPv4Address("172.20.16.1", 20, "Manual")],
            mtu=1500,
        ),
    ]


DEMO = build_adapters()

from app.gui.dialogs import (
    AboutDialog,
    ConfirmDialog,
    DryRunDialog,
    FolderDialog,
    PresetDialog,
    ResultDialog,
)
from app.gui.main_window import MainWindow
from app.network.ip_manager import NetworkManager
from app.storage.database import Database
from app.storage.history import HistoryStore
from app.storage.presets import PresetStore
from app.storage.settings import Settings

# The engine never touches the machine during a screenshot run.
NetworkManager.list_adapters = lambda self, include_loopback=False: list(DEMO)
NetworkManager.get_configuration = lambda self, adapter: adapter

app = QApplication(sys.argv)
app.setApplicationName("IP CHANGER")
app.setFont(QFont("Segoe UI", 10))
icon = PROJECT / "assets" / "ip_changer.ico"
if icon.exists():
    app.setWindowIcon(QIcon(str(icon)))

database = Database()
presets = PresetStore(database)
history = HistoryStore(database)
settings = Settings()
manager = NetworkManager()

eth, wifi = DEMO[0], DEMO[1]

presets.create(
    "PLC NETWORK",
    IPConfiguration.static("192.168.10.10", "255.255.255.0", "192.168.10.1"),
    adapter=eth,
    description="Siemens S7-1500, line 3 control cabinet",
    folder="Line 3",
)
presets.create(
    "VISION RIG",
    IPConfiguration.static("192.168.1.100", "255.255.255.0"),
    adapter=eth,
    description="GigE machine-vision camera, isolated segment",
    folder="Line 3",
)
presets.create(
    "DRIVE COMMISSIONING",
    IPConfiguration.static("10.10.10.5", "255.255.255.0", "10.10.10.1"),
    adapter=eth,
    description="Servo drive start-up subnet",
    folder="Line 7",
)
presets.create(
    "OFFICE (DHCP)",
    IPConfiguration.dhcp(),
    adapter=eth,
    description="Back to the corporate network",
)

# Inserted directly rather than through record(), which always stamps "now":
# a believable History panel needs the entries spread over several days.
from datetime import datetime, timedelta, timezone

_base = datetime.now(timezone.utc)
for minutes_ago, name, prev, new, status, msg in [
    (34, "Ethernet", "10.10.10.5 / 255.255.255.0", "192.168.10.25 / 255.255.255.0",
     "success", "Applied and verified"),
    (52, "Ethernet", "192.168.1.100 / 255.255.255.0", "10.10.10.5 / 255.255.255.0",
     "rolled_back", "Verification failed - previous configuration restored"),
    (185, "Wi-Fi", "10.14.3.87 / 255.255.255.0", "DHCP (automatic)",
     "success", "Applied and verified"),
    (1_512, "Ethernet", "192.168.10.10 / 255.255.255.0", "192.168.1.100 / 255.255.255.0",
     "success", "Applied and verified"),
    (2_940, "Ethernet", "DHCP (automatic)", "192.168.10.10 / 255.255.255.0",
     "success", "Applied and verified"),
]:
    stamp = (_base - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
    database.execute(
        """
        INSERT INTO history
            (timestamp, adapter_name, adapter_guid, action,
             previous_state, new_state, status, message)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (stamp, name, eth.guid, "apply", prev, new, status, msg),
    )

window = MainWindow(manager, presets, history, settings)
window.resize(1280, 1010)
window.show()


def settle(times: int = 4) -> None:
    for _ in range(times):
        app.processEvents()


def save(widget, name: str) -> None:
    settle()
    widget.grab().save(str(OUT / name))
    print("saved", name)


def select(adapter: Adapter) -> None:
    """Pick an adapter by GUID; the visible label carries an icon prefix."""
    box = window.adapter_panel.selector
    for i in range(box.count()):
        if box.itemData(i) == adapter.guid:
            box.setCurrentIndex(i)
            break
    else:
        raise RuntimeError(f"{adapter.friendly_name} not in the selector")
    settle()


steps: list = []


def step(fn):
    steps.append(fn)
    return fn


@step
def s01_dark_static():
    window.apply_theme("dark")
    select(eth)
    p = window.adapter_panel
    p.static_radio.setChecked(True)
    p.ip_field.set_text("192.168.10.10")
    p.mask_field.set_text("255.255.255.0")
    p.gateway_field.set_text("192.168.10.1")
    window._update_apply_state()
    save(window, "01-main-dark.png")


@step
def s02_light_static():
    window.apply_theme("light")
    save(window, "02-main-light.png")


@step
def s03_dhcp_wifi():
    window.apply_theme("dark")
    select(wifi)
    window.adapter_panel.dhcp_radio.setChecked(True)
    window._update_apply_state()
    save(window, "03-dhcp-wifi-dark.png")


@step
def s04_validation():
    select(eth)
    p = window.adapter_panel
    p.static_radio.setChecked(True)
    p.ip_field.set_text("192.168.10.999")
    p.mask_field.set_text("255.255.255.0")
    p.gateway_field.set_text("10.0.0.1")
    p.ip_field.editingFinished.emit()
    window._update_apply_state()
    save(window, "04-validation-dark.png")


@step
def s05_history():
    p = window.adapter_panel
    p.ip_field.set_text("192.168.10.10")
    p.gateway_field.set_text("192.168.10.1")
    window._update_apply_state()
    window.side_tabs.setCurrentIndex(1)
    save(window, "05-history-dark.png")


@step
def s06_dry_run():
    window.side_tabs.setCurrentIndex(0)
    config = IPConfiguration.static("192.168.10.10", "255.255.255.0", "192.168.10.1")
    dialog = DryRunDialog(window, manager.dry_run(eth, config).detail)
    dialog.show()
    save(dialog, "06-dry-run.png")
    dialog.close()


@step
def s07_result():
    dialog = ResultDialog(
        window,
        window.palette,
        True,
        "Configuration applied",
        "Adapter:  Ethernet\n"
        "Mode:     Static\n"
        "IP:       192.168.10.10 / 255.255.255.0\n"
        "Gateway:  192.168.10.1\n\n"
        "Read back from Windows and verified.",
    )
    dialog.show()
    save(dialog, "07-result.png")
    dialog.close()


@step
def s08_save_preset():
    dialog = PresetDialog(
        window,
        window.palette,
        adapter=eth,
        configuration=IPConfiguration.static("192.168.10.10", "255.255.255.0", "192.168.10.1"),
        name="PLC NETWORK",
        description="Siemens S7-1500, line 3 control cabinet",
        folders=presets.folders(),
        folder="Line 3",
    )
    dialog.show()
    save(dialog, "08-save-preset.png")
    dialog.close()


@step
def s09_confirm():
    dialog = ConfirmDialog(
        window,
        window.palette,
        "Apply to a live adapter?",
        "Ethernet is carrying traffic. Applying a static address drops the "
        "connection for a moment; the change is read back and verified before "
        "it is kept.",
        confirm_text="APPLY",
        dangerous=True,
    )
    dialog.show()
    save(dialog, "09-confirm.png")
    dialog.close()


@step
def s10_about():
    # Real diagnostics output, with the machine-specific paths anonymised so the
    # image can go on a public page.
    diagnostics = window._build_diagnostics()
    diagnostics = diagnostics.replace(
        str(APPDATA / "IP_CHANGER" / "logs"),
        r"C:\Users\operator\AppData\Local\IP_CHANGER\logs",
    ).replace(
        str(APPDATA / "IP_CHANGER"),
        r"C:\Users\operator\AppData\Local\IP_CHANGER",
    )
    dialog = AboutDialog(
        window,
        window.palette,
        diagnostics,
        True,
        "Windows 11 Pro (build 26200)",
    )
    dialog.show()
    save(dialog, "10-about.png")
    dialog.close()


@step
def s11b_folders_collapsed():
    window.apply_theme("dark")
    window.side_tabs.setCurrentIndex(0)
    window.preset_panel.set_collapsed_folders(["Line 7"])
    window.refresh_presets()
    save(window, "12-folders-collapsed-dark.png")
    window.preset_panel.set_collapsed_folders([])
    window.refresh_presets()


@step
def s11c_move_to_folder():
    dialog = FolderDialog(
        window,
        "Move to folder",
        "Choose a folder for 'VISION RIG', or clear the field to take it out "
        "of its folder.",
        folders=presets.folders(),
        folder="Line 3",
    )
    dialog.show()
    save(dialog, "13-move-to-folder.png")
    dialog.close()


@step
def s11d_drop_target():
    """Show a folder highlighted as a drag hovers over it."""
    from app.gui.preset_panel import PRESET_MIME, FolderSection

    window.apply_theme("dark")
    window.side_tabs.setCurrentIndex(0)

    # QDragEnterEvent keeps a bare pointer to its payload, so the QMimeData
    # has to outlive the call.
    global _drag_payload
    _drag_payload = QMimeData()
    _drag_payload.setData(PRESET_MIME, b"1")

    for section in window.preset_panel.findChildren(FolderSection):
        if section.folder == "Line 7":
            section.dragEnterEvent(
                QDragEnterEvent(
                    QPoint(10, 10),
                    Qt.MoveAction,
                    _drag_payload,
                    Qt.LeftButton,
                    Qt.NoModifier,
                )
            )
    save(window, "14-drag-drop-target.png")


@step
def s11_light_history():
    window.apply_theme("light")
    window.side_tabs.setCurrentIndex(1)
    save(window, "11-history-light.png")


def run(i: int = 0) -> None:
    if i >= len(steps):
        QTimer.singleShot(150, app.quit)
        return
    try:
        steps[i]()
    except Exception as exc:
        print("STEP", steps[i].__name__, "FAILED:", type(exc).__name__, exc)
    QTimer.singleShot(350, lambda: run(i + 1))


QTimer.singleShot(900, run)
code = app.exec()
database.close()
print("done", code)
