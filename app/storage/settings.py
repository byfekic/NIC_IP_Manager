"""Small JSON application settings file (specification section 47).

Holds only preferences: theme, window geometry, last selected adapter.
Network presets are never stored here - they live in SQLite.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from app.utils.logging_setup import get_logger
from app.utils.paths import settings_path

log = get_logger(__name__)

DEFAULTS: dict[str, Any] = {
    "theme": "dark",
    "last_adapter_guid": "",
    "window_width": 1120,
    "window_height": 820,
    "window_x": -1,
    "window_y": -1,
    "check_ip_conflicts": True,
    "show_virtual_adapters": True,
}


class Settings:
    """Preferences with safe defaults; corruption never blocks startup."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else settings_path()
        self._data: dict[str, Any] = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                for key, value in loaded.items():
                    if key in DEFAULTS and isinstance(value, type(DEFAULTS[key])):
                        self._data[key] = value
        except (OSError, ValueError) as exc:
            log.warning("Settings could not be read, using defaults: %s", exc)

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        except OSError as exc:  # pragma: no cover
            log.warning("Settings could not be saved: %s", exc)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def update(self, **values: Any) -> None:
        self._data.update(values)
