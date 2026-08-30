# IP CHANGER — Manual Test Plan

Verification on real Windows hardware (specification section 40). The automated
suite mocks every Windows operation; this plan covers what only real hardware
can prove.

## Before you start

> **Run these tests on a machine you have physical access to.**
> Several tests deliberately change or break network connectivity. Do not run
> them over Remote Desktop, and do not run them on a machine connected to live
> plant equipment.

**Recommended setup**

- A Windows 10 or 11 machine with at least two adapters (one Ethernet, one Wi-Fi).
- A test network with a DHCP server (a small switch and router is enough).
- If possible, virtual adapters present (Hyper-V, VMware or VirtualBox) to
  exercise the classification logic.
- A second device on the same subnet, for the conflict test.

**Record for each test:** date, Windows build, result (PASS/FAIL), and any
deviation from the expected result.

**Before starting**, note the original configuration of every adapter you will
touch so you can restore it manually if needed:

```powershell
Get-NetIPConfiguration | Format-List InterfaceAlias, IPv4Address, IPv4DefaultGateway
Get-NetIPInterface -AddressFamily IPv4 | Format-Table ifIndex, InterfaceAlias, Dhcp
```

---

## 1. Ethernet with DHCP

**Steps**
1. Connect Ethernet to a network with a DHCP server.
2. Start IP CHANGER, select the Ethernet adapter.
3. Choose **DHCP**, click **APPLY CONFIGURATION**.

**Expected**
- Progress dialog shows the steps, then a success dialog.
- **DHCP** reads `Enabled`; the address obtained from the server is displayed.
- If no DHCP server responds, the result states an APIPA address was assigned
  and that no server answered — it does **not** report a plain success.
- History records the change.

---

## 2. Ethernet with a static IP

**Steps**
1. Select the Ethernet adapter, choose **Static**.
2. Enter `192.168.100.50`, `255.255.255.0`, gateway `192.168.100.1`.
3. Click **APPLY CONFIGURATION**.

**Expected**
- **No confirmation dialog appears** — the change starts immediately.
- The caution *"Applies immediately - the connection may drop"* is visible
  next to the button beforehand.
- The progress dialog appears, then a success dialog showing the verified
  address.
- Cross-check independently:
  ```powershell
  Get-NetIPAddress -InterfaceAlias 'Ethernet' -AddressFamily IPv4
  Get-NetRoute -InterfaceAlias 'Ethernet' -DestinationPrefix '0.0.0.0/0'
  ```

---

## 3. Static → DHCP

**Steps** From the static configuration of test 2, switch to **DHCP** and apply.

**Expected**
- The static address is removed and a DHCP lease obtained.
- The old default route is gone (no leftover static gateway).
- `Get-NetIPInterface -InterfaceAlias 'Ethernet' -AddressFamily IPv4` shows
  `Dhcp: Enabled`.

---

## 4. DHCP → static

**Steps** From DHCP, switch to a static address and apply.

**Expected** DHCP disabled, the static address present, gateway route created.

---

## 5. Change the IP address only

**Steps** With a static configuration, change only the last octet and apply.

**Expected** Address changes; subnet mask and gateway are unchanged.

---

## 6. Change the subnet mask

**Steps** Change `255.255.255.0` to `255.255.0.0` and apply.

**Expected** The prefix changes to `/16`; the displayed mask reads
`255.255.0.0`.

---

## 7. Change the gateway

**Steps** Change the gateway to another address in the same subnet and apply.

**Expected** The old default route is replaced, not duplicated. Verify with
`Get-NetRoute -DestinationPrefix '0.0.0.0/0'` — there must be exactly one
route for this interface.

---

## 8. Invalid IP address

**Steps** Enter `192.168.1.999`, then `192.168.1`, then `hello`.

**Expected**
- The field is outlined and shows *"Not a valid IPv4 address."*
- **APPLY CONFIGURATION** is disabled in every case.
- Nothing is sent to Windows. (Validation still blocks invalid input; only the
  *confirmation* step was removed.)

---

## 9. Invalid subnet mask

**Steps** Enter `255.255.0.255`, then `999.0.0.0`, then `abc`.

**Expected** Field error *"Not a valid subnet mask"*; Apply stays disabled.

---

## 10. Invalid and unusual gateways

**Steps**
1. Gateway `192.168.1.999` → **error**, Apply disabled.
2. IP `192.168.10.50/24` with gateway `192.168.20.1` → **warning** shown
   under the fields, Apply stays enabled, and applying proceeds without a
   prompt. The warning is also written to the log.
3. Gateway equal to the adapter's own IP → **error**.

**Expected** Exactly this error/warning split: unusual configurations are
permitted with a warning, invalid ones are blocked.

---

## 11. Adapter disconnected

**Steps** Unplug the Ethernet cable, refresh, select the adapter, apply a
static configuration.

**Expected**
- The adapter shows `○` and *Disconnected*.
- A warning explains the configuration will take effect when the link comes up.
- The change is applied and stored by Windows without an error.
- Reconnecting the cable brings up the configured address.

---

## 12. Adapter disabled

**Steps** Disable the adapter in Windows Network Connections, then refresh.

**Expected** The adapter either disappears or shows a non-connected state. The
application does not crash, and does not silently switch to another adapter.

---

## 13. Multiple adapters

**Steps** With Ethernet and Wi-Fi both present, select each in turn.

**Expected**
- Each shows its own correct configuration.
- Changing one leaves the other untouched — confirm the second adapter's
  address is unchanged afterwards.
- The dropdown shows status and address for each.

---

## 14. Virtual and VPN adapters

**Steps** With Hyper-V, VMware, VirtualBox or a VPN client installed, inspect
those adapters.

**Expected**
- Type shows *Virtual* or *VPN*, not *Physical*.
- Selecting one shows a warning that changing it may disrupt virtual machines
  or VPN connectivity.
- The loopback interface is hidden by default and cannot be configured.

---

## 15. UAC cancelled

**Steps** Start the application from a non-elevated session and dismiss the
UAC prompt.

**Expected**
- The application still starts, in read-only mode.
- The header shows `⚠ Administrator privileges required` and a
  **Restart as Administrator** button.
- Adapters are visible; Apply is disabled.
- Clicking **Restart as Administrator** re-prompts and, when accepted, the
  elevated instance starts and the old one closes.
- **No elevation loop occurs** if UAC is dismissed repeatedly.

---

## 16. Configuration failure

**Steps** Provoke a rejection — for example, assign an address already held by
another adapter on the same machine.

**Expected**
- A plain-language message such as *"That IP address is already configured on
  this computer."*
- No raw traceback or `CalledProcessError` anywhere in the dialog.
- **Show technical details** reveals the underlying message for engineers.
- **Restore previous configuration** is offered.

---

## 17. Rollback

**Steps**
1. Note the current configuration.
2. Cause a failure as in test 16.
3. Click **RESTORE PREVIOUS CONFIGURATION**.

**Expected**
- The previous configuration returns exactly, including the gateway.
- The restore is itself verified and reported.
- History records a rollback entry.

**Multi-address variant** — add a second address first:
```powershell
New-NetIPAddress -InterfaceAlias 'Ethernet' -IPAddress 192.168.100.51 -PrefixLength 24 -SkipAsSource $true
```
The interface must warn that the adapter has several addresses; after applying
a new primary address, `192.168.100.51` must still be present; after a
rollback, **both** original addresses must be restored.

---

## 18. Applying a preset

**Steps** Save a preset, change the adapter to something else, then apply the
preset.

**Expected** The saved adapter is selected automatically and the preset's
values are applied immediately, with no confirmation prompt.

---

## 19. Preset for a missing adapter

**Steps**
1. Save a preset for a USB Ethernet adapter.
2. Unplug the adapter, refresh, and apply the preset.

**Expected**
- An **Adapter not found** dialog naming the saved adapter and its MAC.
- **Nothing is applied to any other adapter.**

**Rename variant** — rename the adapter in Windows, then apply the preset. It
must be matched by MAC address, and the interface must state that the adapter
is now called something else.

---

## 20. Application restart

**Steps** Set a theme, resize the window, select an adapter, close and reopen.

**Expected** Theme, window size, position and selected adapter are restored;
presets and history persist.

---

## 21. Crash recovery

**Steps**
1. Start an apply operation.
2. While the progress dialog is showing, kill the process:
   ```powershell
   Stop-Process -Name IP_CHANGER -Force
   ```
3. Restart the application.

**Expected**
- A dialog reports the incomplete operation with the adapter, previous and
  requested configuration.
- Options are **Check current configuration** and **Restore previous
  configuration**.
- **Nothing is rolled back automatically.**
- Dismissing clears the journal so the prompt does not reappear.

---

## 22. Concurrency lock

**Steps** Start an apply, and while it runs try to press Apply again, switch
adapters, or apply a preset.

**Expected** Controls are disabled during the operation; only one network
operation can ever run. The window stays responsive and never shows
"Not Responding".

---

## 23. Duplicate address detection

**Steps** Assign an address already used by another live device on the subnet.

**Expected** The result reports `IP conflict check: Address already in use`
with the other device's MAC, or `Unable to determine` — never a false claim
that the address is definitely free.

---

## 24. Import and export

**Steps** Export presets, delete them, re-import. Then import a deliberately
corrupt file:

```json
{"version": 1, "presets": [
  {"name": "Bad", "mode": "static", "ip_address": "999.1.1.1", "subnet_mask": "255.255.255.0"},
  {"name": "Good", "mode": "static", "ip_address": "10.1.1.5", "subnet_mask": "255.255.255.0"}
]}
```

**Expected** Round trip restores everything. In the corrupt file, `Good` is
imported and `Bad` is reported and skipped. A file that is not valid JSON
produces a friendly error, not a crash.

---

## 25. Dry run

**Steps** Enter a configuration and click **Dry run**.

**Expected** The before/after report appears, warnings are listed, and
**nothing changes** — confirm with `Get-NetIPAddress` afterwards.

---

## 26. Accessibility and keyboard

**Steps** Navigate the whole application using only the keyboard.

**Expected** Tab reaches every control with a visible focus outline; Enter
confirms and Esc cancels dialogs; Esc does **not** dismiss the progress
dialog; F5, Ctrl+Enter, Ctrl+S, Ctrl+D and F1 work as documented.

Note that `Ctrl+Enter` applies immediately with no prompt — check you do not
trigger it by accident while typing in a field.

---

## 27. Both themes

**Steps** Switch between dark and light with the header button.

**Expected** Both themes are fully legible, status colours remain
distinguishable, and every status still carries an icon and text so colour is
never the only signal.

---

## 28. Diagnostics

**Steps** Open **About** (F1) and click **Copy diagnostic information**.

**Expected** The report contains the application version, Windows version,
Administrator status, and for every adapter its GUID, MAC, index, type,
status, IPv4 addresses, gateway, DNS and DHCP state; it pastes cleanly into a
text editor.

---

## 29. Non-elevated inspection

**Steps** Run `IP_CHANGER.exe --no-elevate`.

**Expected** The application starts unelevated, lists adapters and shows
configuration, but Apply is disabled and the header warns about privileges.

---

## 30. Packaged executable

**Steps** Copy `IP_CHANGER.exe` to a clean machine **without Python**.

**Expected**
- It starts from a normal desktop double-click.
- The UAC prompt appears immediately, showing the correct product name.
- **No console window appears at any point.**
- Right-click → Properties → Details shows the version and description.
- Data is created under `%LOCALAPPDATA%\IP_CHANGER\`.
- The application runs correctly from `C:\Program Files` without write access
  to that folder.

---

## 31. DHCP to static on a disconnected adapter

This is the combination that previously failed with *"Inconsistent parameters
PolicyStore PersistentStore and Dhcp Enabled"* (Windows error 87), because
`Set-NetIPInterface -Dhcp Disabled` silently does nothing on some adapters.

**Steps**
1. Unplug the Ethernet cable (or pick any adapter showing DHCP + Disconnected).
2. Set it to **DHCP** and apply.
3. Now set a static address and apply.

**Expected**
- The static address is applied and verified; no error 87.
- The result may note that Windows still reports DHCP because the link is
  down — the address is nonetheless configured.
- **Current configuration** shows `DHCP: Disabled` afterwards, because the
  mode is read from the address origin, not the unreliable interface flag.
- Cross-check the origin is `Manual`:
  ```powershell
  Get-NetIPAddress -InterfaceAlias 'Ethernet' -AddressFamily IPv4 |
    Select-Object IPAddress, PrefixLength, PrefixOrigin
  ```
- Reconnect the cable: the static address stays.

---

## 32. Rollback from a disconnected static adapter

**Steps** On a disconnected adapter holding a static address, note the address,
apply a different one, then use **RESTORE PREVIOUS CONFIGURATION**.

**Expected** The original static address returns — *not* DHCP. (The snapshot
must record the real mode; a snapshot built from the interface DHCP flag would
wrongly restore DHCP here.)

---

## 33. Changing an address replaces it

**Steps** On an adapter with a single static address, change it and apply.

**Expected** Only the new address remains; the old one is gone. Then add a
second address manually:
```powershell
New-NetIPAddress -InterfaceAlias 'Ethernet' -IPAddress 192.168.100.51 -PrefixLength 24 -SkipAsSource $true
```
Change the primary again: the secondary `.51` must survive, and the old primary
must be gone.

---

## 34. Apply is immediate

**Steps** Enter a valid static configuration and press **APPLY CONFIGURATION**.
Repeat using `Ctrl+Enter`, and again from a preset's **APPLY**.

**Expected**
- In all three cases the change starts with **no confirmation dialog**.
- The caution line is visible next to the button before pressing it.
- Invalid input still blocks with an error dialog.
- Deleting a preset and clearing history *do* still ask for confirmation.

---

## Sign-off

| Test | Result | Notes |
|---|---|---|
| 1 Ethernet + DHCP | | |
| 2 Ethernet + static | | |
| 3 Static → DHCP | | |
| 4 DHCP → static | | |
| 5 IP only | | |
| 6 Subnet | | |
| 7 Gateway | | |
| 8 Invalid IP | | |
| 9 Invalid subnet | | |
| 10 Gateways | | |
| 11 Disconnected | | |
| 12 Disabled | | |
| 13 Multiple adapters | | |
| 14 Virtual / VPN | | |
| 15 UAC cancelled | | |
| 16 Failure handling | | |
| 17 Rollback | | |
| 18 Preset applied | | |
| 19 Preset, missing adapter | | |
| 20 Restart | | |
| 21 Crash recovery | | |
| 22 Concurrency lock | | |
| 31 DHCP → static, disconnected | | |
| 32 Rollback, disconnected static | | |
| 33 Address is replaced, not added | | |
| 34 Apply is immediate (no prompt) | | |
| 23 Duplicate address | | |
| 24 Import / export | | |
| 25 Dry run | | |
| 26 Keyboard | | |
| 27 Themes | | |
| 28 Diagnostics | | |
| 29 Non-elevated | | |
| 30 Packaged exe | | |

Tested by: ______________________  Date: ____________

Windows build: ______________________
