# Screenshots

Captured from the running application by [`docs/make_screenshots.py`](../make_screenshots.py).

Every adapter, preset and history entry in these images is **synthetic**. The
script substitutes a fixed set of demo adapters for `NetworkManager.list_adapters`
and redirects `%LOCALAPPDATA%` to a throwaway directory, so no real network
details, machine names or user paths appear. Addresses are drawn from private
and documentation ranges (RFC 1918, RFC 3849 `2001:db8::/32`).

Regenerate them with:

```bash
python docs/make_screenshots.py . docs/screenshots .screenshot-tmp
```

| File | Shows |
| --- | --- |
| `01-main-dark.png` | Main window, dark theme. Adapter selector, live configuration read back from Windows, the static-address form, and saved configurations grouped into folders. |
| `02-main-light.png` | The same screen in the light theme. |
| `03-dhcp-wifi-dark.png` | A wireless adapter on DHCP: the form fields drop to read-only and show what DHCP handed out. |
| `04-validation-dark.png` | Inline field validation. An out-of-range octet marks the field and disables **Apply Configuration** — the change can never reach the adapter. |
| `05-history-dark.png` | The History tab: every apply, its before → after transition, and the one that failed verification and was rolled back automatically. |
| `06-dry-run.png` | Dry run — the full before/after diff with nothing written to the adapter. |
| `07-result.png` | Result dialog after a successful apply, reporting that the new configuration was read back and verified. |
| `08-save-preset.png` | Saving a configuration as a named preset, optionally bound to the adapter it was captured from. |
| `09-confirm.png` | Confirmation before reconfiguring an adapter that is carrying traffic. |
| `10-about.png` | About dialog with the copyable diagnostics report used for support. |
| `11-history-light.png` | History tab, light theme. |
| `12-folders-collapsed-dark.png` | The same list with a folder collapsed. Folders are one level deep; presets with no folder fall under **Ungrouped**, which only appears once at least one folder exists. |
| `13-move-to-folder.png` | Moving a saved configuration between folders. The field offers the folders already in use and accepts a new name; clearing it takes the preset out of its folder. |
| `14-drag-drop-target.png` | A preset being dragged over a folder. The whole group highlights, heading and cards together, so the drop target is never ambiguous. |

## Suggested picks for a portfolio page

- **Hero image** — `01-main-dark.png`. One screen, the whole tool.
- **Safety story** — `04-validation-dark.png` and `06-dry-run.png` next to each
  other: validated before it is sent, previewed before it is applied.
- **Reliability story** — `05-history-dark.png`, which shows the automatic
  rollback after a failed verification.
- **Theming** — `01-main-dark.png` / `02-main-light.png` as a before/after pair.
- **Organisation** — `01-main-dark.png` / `12-folders-collapsed-dark.png` to show
  folders expanding and collapsing, or `14-drag-drop-target.png` for the drag.
