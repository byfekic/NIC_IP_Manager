# IP CHANGER

**Professional Network Configuration Utility** — version 1.0.0

A small, reliable Windows tool for changing an adapter's IPv4 configuration.
Built for commissioning technicians working next to PLCs, SCADA systems and
industrial Ethernet networks, where a wrong or half-applied change is expensive.

The design priority is **reliability over convenience**: the application
validates before it acts, snapshots before it changes, verifies after it
changes, and can always put the previous configuration back.

---

## Contents

- [What it does](#what-it-does)
- [Requirements](#requirements)
- [Installation](#installation)
- [Running](#running)
- [Administrator privileges](#administrator-privileges)
- [Using the application](#using-the-application)
  - [Changing an IP address](#changing-an-ip-address)
  - [Switching to DHCP](#switching-to-dhcp)
  - [Creating and applying presets](#creating-and-applying-presets)
  - [Import and export](#import-and-export)
  - [Rollback](#rollback)
  - [Dry run](#dry-run)
- [How a change is applied](#how-a-change-is-applied)
- [Architecture](#architecture)
- [Where data is stored](#where-data-is-stored)
- [Building the executable](#building-the-executable)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Security and privacy](#security-and-privacy)
- [Keyboard shortcuts](#keyboard-shortcuts)

---

## What it does

- Lists every network adapter, discovered dynamically — it never assumes an
  adapter is called "Ethernet".
- Identifies each adapter's nature: physical, wireless, virtual, VPN,
  Bluetooth or loopback.
- Shows the live IPv4 configuration, including **all** addresses when an
  adapter has more than one, plus IPv6 and DNS for reference.
- Changes IPv4 address, subnet mask and default gateway.
- Switches between DHCP and static addressing.
- Saves, edits, duplicates and applies named presets.
- Verifies every change against the adapter's real state afterwards.
- Restores the previous configuration when something goes wrong.
- Keeps a local history of operations and a rotating log.

It deliberately does **not** modify DNS, IPv6, firewall rules, or any adapter
other than the one you selected.

---

## Requirements

| | |
|---|---|
| Operating system | Windows 10 or Windows 11, 64-bit |
| Privileges | Administrator (the application elevates itself) |
| Runtime | None for the packaged `IP_CHANGER.exe` |
| Runtime (from source) | Python 3.12+ and PySide6 |
| Network | None — the application is fully offline |

---

## Installation

### Packaged executable (recommended)

Copy `IP_CHANGER.exe` anywhere and run it. There is nothing to install and no
Python required. The application writes its data to your user profile, so it
runs happily from `C:\Program Files`, a USB stick or a network share.

### From source

```bash
git clone <repository>
cd IP_Changer
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

---

## Running

Packaged:

```bash
dist\IP_CHANGER.exe
```

From source:

```bash
python run_dev.py
```

Useful switches:

| Switch | Effect |
|---|---|
| `--no-elevate` | Start without requesting elevation (read-only inspection) |
| `--debug` | Verbose logging |
| `--version` | Print the version and exit |

---

## Administrator privileges

Changing a network adapter requires Administrator rights.

On start-up the application checks whether it is elevated. If it is not, it
restarts itself through the standard Windows UAC prompt, preserving its
arguments. **You never need to open a command prompt as Administrator.**

The header shows the current state:

- `● Administrator` — network changes are possible.
- `⚠ Administrator privileges required` — you can still inspect adapters;
  the **Restart as Administrator** button appears next to it.

If you dismiss the UAC prompt the application keeps running in read-only mode
rather than closing, and Apply stays disabled.

---

## Using the application

### Changing an IP address

1. Select the adapter from the dropdown. Connected adapters are marked `●`
   and disconnected ones `○`, and each entry shows its status and current
   address so you can confirm you have the right interface.
2. Check **Current configuration**.
3. Choose **Static**.
4. Enter the IP address, subnet mask and (optionally) the gateway. Fields
   validate as you type; **APPLY CONFIGURATION** stays disabled until the
   configuration is complete and valid.
5. Click **APPLY CONFIGURATION**. **There is no confirmation dialog — the
   change is applied straight away.**

The result dialog reports success with the verified configuration, or failure
with a plain-language explanation and a **Restore previous configuration**
button.

> **Apply is immediate.** Because nothing stands between the button and the
> change, the panel shows what you need beforehand: the selected adapter with
> its status and address, its type (a caution appears for virtual and VPN
> adapters), and any validation warning under the fields. Use **Dry run** to
> preview a change first, and remember that the previous configuration is
> always snapshotted and can be restored.

### Switching to DHCP

Choose **DHCP**. The address fields are disabled, because they no longer apply.
After applying, the application waits for Windows, polls for a lease, and
reports the address actually obtained.

If Windows ends up with a `169.254.x.x` address, the application says so
explicitly — that means **no DHCP server answered**, not that the change
succeeded quietly.

### Creating and applying presets

To save the configuration currently in the form, click **Save as Preset**,
name it (for example `PLC NETWORK`) and optionally describe it.

Presets record the adapter's **stable identity** — its interface GUID and MAC
address — not just its name. When you apply a preset:

- If the exact adapter is found, it is selected and the preset is applied.
- If the adapter was renamed but the MAC matches, it is matched and the
  application tells you the name changed.
- If it cannot be identified with confidence, the application **stops and
  asks**. It will never apply a preset to a different adapter.

A preset's **APPLY** is immediate too. Each preset card offers **Load** (copy
the values into the form without applying), **APPLY**, and a `⋮` menu with Edit, Rename, Duplicate and Delete.
Deleting asks for confirmation.

### Import and export

**Export** writes all presets to JSON:

```json
{
  "version": 1,
  "presets": [
    {
      "name": "PLC Network",
      "mode": "static",
      "ip_address": "192.168.10.100",
      "subnet_mask": "255.255.255.0",
      "gateway": "192.168.10.1"
    }
  ]
}
```

**Import** validates every entry *before* writing anything. Invalid entries are
reported and skipped; the valid ones are still imported. Nothing in the file is
ever executed — it is treated purely as data.

### Rollback

Before any change, the application captures a complete snapshot: DHCP state,
**every** IPv4 address with its prefix, gateways and interface identity.

If the change fails or cannot be verified, the result dialog offers
**RESTORE PREVIOUS CONFIGURATION**. Restoring is an independent operation — it
needs only the snapshot — and it is verified in turn. If the rollback itself
fails, the application says so clearly rather than pretending it worked.

If the application is closed or crashes mid-operation, the next start detects
the incomplete operation and offers to check the current configuration or
restore the previous one. **It never rolls back automatically.**

### Dry run

**Dry run** shows exactly what would change, without touching anything:

```
Adapter:  Ethernet  (Physical)

Mode:     Static  ->  Static
IP:       192.168.1.100  ->  192.168.10.100
Subnet:   255.255.255.0  ->  255.255.255.0
Gateway:  192.168.1.1  ->  192.168.10.1
```

---

## How a change is applied

Every change is a transaction (specification section 55):

```
READ  →  SNAPSHOT  →  VALIDATE  →  APPLY  →  WAIT  →  VERIFY  →  SUCCESS
                                       │
                                     FAIL  →  ROLLBACK  →  VERIFY ROLLBACK
```

1. **Read** — the adapter is re-read; if it vanished, nothing is changed.
2. **Snapshot** — the full previous state is captured and journalled to disk.
3. **Validate** — errors block the change; warnings do not.
4. **Apply** — through the privileged execution layer. Applying is immediate;
   there is no confirmation prompt.
5. **Wait** — Windows is given time to update the network stack.
6. **Verify** — the adapter is read back and compared field by field. Success
   is never assumed from an exit code.
7. **Report** — success, or failure with rollback offered.

### Validation: errors versus warnings

**Errors** block the change (these are refusals, not prompts — Windows would
reject the configuration anyway):

- Malformed IPv4 addresses (`192.168.1.999`, `192.168.1`, `hello`)
- Non-contiguous subnet masks (`255.255.0.255`)
- Network and broadcast addresses of the chosen subnet
- Loopback, multicast and reserved addresses
- A gateway equal to the adapter's own address

**Warnings** inform but let you continue, because they are unusual rather than
wrong:

- A gateway outside the configured subnet
- A `169.254.x.x` (APIPA) address assigned deliberately
- A public Internet address
- Virtual or VPN adapters
- A disconnected adapter
- An adapter that already has several IPv4 addresses

### Duplicate address detection

After applying a static address the application can probe for a conflict using
**ARP**, not ping — a device may block ICMP while still holding the address.

The result is reported honestly as either `No conflict detected` or
`Unable to determine`. The application never claims an address is definitely
free.

### Multiple IPv4 addresses

If an adapter has several IPv4 addresses, the extra ones are shown in the
interface and **preserved** across a change: the primary address is replaced
and the others are re-added. Rollback restores all of them.

Windows-assigned APIPA addresses are ignored deliberately — Windows manages
those itself.

---

## Architecture

The GUI never runs PowerShell or `netsh`. It calls `NetworkManager`, and only
the privileged execution layer talks to Windows.

```
app/
├── main.py                  start-up sequence, elevation, error dialogs
├── gui/
│   ├── main_window.py       wiring, operation lock, threading
│   ├── adapter_panel.py     adapter selection and the configuration form
│   ├── preset_panel.py      saved configurations
│   ├── history_panel.py     recent activity
│   ├── dialogs.py           confirmation, results, errors, diagnostics
│   ├── widgets.py           cards, validated inputs, status indicators
│   ├── workers.py           QThreadPool workers and the operation lock
│   └── styles.py            colour tokens and the stylesheet
├── network/
│   ├── winapi.py            ctypes binding for GetAdaptersAddresses
│   ├── adapter_manager.py   discovery and stable identity resolution
│   ├── powershell.py        privileged execution layer
│   ├── ip_manager.py        the transaction engine
│   ├── validator.py         errors versus warnings
│   └── verifier.py          post-change verification, ARP conflict check
├── storage/
│   ├── database.py          SQLite schema and access
│   ├── presets.py           preset CRUD, import/export
│   ├── history.py           operation history
│   ├── journal.py           crash-recovery journal
│   └── settings.py          preferences
├── security/elevation.py    UAC detection and self-elevation
├── models/                  Adapter, IPConfiguration, snapshots, results
└── utils/                   paths, logging, error translation
```

### Reading adapter state

Adapter information comes from the Windows **IP Helper API**
(`GetAdaptersAddresses`) through `ctypes` — not from parsing command output,
which is localized and unstable. This yields the interface GUID, MAC, ifType,
operational status, DHCP flag, every unicast address with its prefix length,
gateways and DNS servers, and it works for disconnected adapters that WMI
omits entirely.

### Changing adapter state

Changes go through a **fixed, constant PowerShell script**, written once to a
private per-process temporary directory and run with `-File`. Parameters are
**never** interpolated into the script text: they travel as a JSON document on
the child process's standard input and are parsed there with
`ConvertFrom-Json`, arriving as inert data. Script injection is therefore
structurally impossible rather than filtered.

The script uses the `NetTCPIP` cmdlets (`Set-NetIPInterface`,
`New-NetIPAddress`, `Remove-NetIPAddress`, `Remove-NetRoute`) and returns a
single JSON envelope, so nothing depends on parsing localized text.
`shell=True` is never used, arguments are passed as a list, every call has a
timeout, and exit codes are validated.

### Two Windows behaviours the engine works around

**`Set-NetIPInterface -Dhcp Disabled` can silently do nothing.** On some
adapters — notably media-disconnected ones — it reports success while DHCP
stays enabled. `New-NetIPAddress` then refuses with *"Inconsistent parameters
PolicyStore PersistentStore and Dhcp Enabled"* (Windows error 87) and the
address is never applied. The engine therefore **verifies** that DHCP actually
went off, and when it did not, falls back to the WMI `EnableStatic` /
`SetGateways` methods, which assign the addresses and clear DHCP atomically.

**The interface DHCP flag lies on a disconnected adapter.** An adapter holding
a manually assigned address still reports DHCP as enabled through
`GetAdaptersAddresses`, `Get-NetIPInterface` and WMI alike. The per-address
`PrefixOrigin` (`Manual` / `Dhcp`) stays correct, so that is what the
application uses to decide an adapter's real mode. This matters for more than
display: a snapshot built from the flag would record "DHCP" and a rollback
would then replace a static configuration with DHCP.

---

## Where data is stored

Everything lives in your user profile, so the application never needs write
access to `C:\Program Files`:

```
%LOCALAPPDATA%\IP_CHANGER\
├── ip_changer.db            presets and history (SQLite)
├── settings.json            theme, window geometry, last adapter
├── pending_operation.json   only while an operation is in flight
└── logs\
    └── ip_changer.log       rotating, 1 MB × 5 files
```

Presets are stored in SQLite, never only in JSON.

---

## Building the executable

```bash
pip install -r requirements-dev.txt
python build/build.py --clean --test
```

This produces `dist/IP_CHANGER.exe`:

- a single self-contained file (~48 MB)
- no console window
- an application icon and version information
- an embedded manifest requesting Administrator elevation
- no Python installation required on the target machine

Options: `--clean` removes previous artefacts, `--test` runs the suite first
and aborts the build if anything fails.

To build directly with PyInstaller:

```bash
pyinstaller --clean --noconfirm build/ip_changer.spec
```

---

## Testing

```bash
python -m pytest tests/ -q
```

The suite covers IP, subnet and gateway validation; adapter classification and
identity; preset CRUD, import and export; history and settings; and the full
transaction engine including verification failure, rollback, multi-address
preservation, crash-recovery journalling and dry run.

**All Windows operations are mocked — running the tests never changes the
configuration of the machine they run on.**

For real-hardware verification, follow [MANUAL_TEST_PLAN.md](MANUAL_TEST_PLAN.md).

---

## Troubleshooting

**"Administrator privileges required" stays in the header**
You dismissed the UAC prompt. Click **Restart as Administrator**.

**The adapter list is empty**
Press **Refresh** (F5). If it stays empty, open **About** and use
**Copy diagnostic information** — it lists everything Windows reported.

**"Configuration could not be verified"**
Windows accepted the command but the adapter does not show the requested
values. The dialog lists exactly which fields differ. Use **Restore previous
configuration**, then check whether the adapter is disconnected or managed by
other software (VPN clients and virtualisation tools sometimes re-apply their
own settings).

**"That IP address is already configured on this computer"**
Another adapter already holds the address. Windows will not allow it twice.

**DHCP produces a 169.254.x.x address**
No DHCP server answered. Check the cable, the switch port and the VLAN.

**Changes revert on their own**
Something else is managing the adapter — a VPN client, Hyper-V, VMware, or a
corporate policy. The **Type** shown next to the adapter tells you whether it
is virtual or a VPN interface.

**The application will not start**
Check `%LOCALAPPDATA%\IP_CHANGER\logs\ip_changer.log`.

For any support request, **About → Copy diagnostic information** captures the
application version, Windows version, Administrator status and the full
adapter inventory.

---

## Security and privacy

- No Internet access, telemetry, cloud services or online licensing.
- No external servers are contacted; the application is fully local.
- No passwords or credentials are handled or stored.
- All SQLite queries are parameterized.
- Operator input is never executed as a shell command; the privileged script
  is a constant and parameters travel as JSON on stdin.
- `shell=True` is never used; arguments are passed as lists with timeouts.
- Firewall rules and unrelated network settings are never modified.
- DNS and IPv6 are never modified by an IPv4 change.
- Only the adapter you selected is ever touched.

---

## Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `F5` / `Ctrl+R` | Refresh adapters |
| `Ctrl+Enter` | Apply configuration |
| `Ctrl+S` | Save as preset |
| `Ctrl+D` | Dry run |
| `F1` | About and diagnostics |
| `Ctrl+Q` | Quit |
| `Tab` | Move between controls |
| `Enter` | Confirm a dialog |
| `Esc` | Cancel a dialog |

`Ctrl+Enter` applies immediately, exactly like the button.

`Esc` deliberately does **not** close the progress dialog: a network change in
flight must not be abandoned halfway.

---

## Version

IP CHANGER 1.0.0 — semantic versioning. See **About** in the application.
