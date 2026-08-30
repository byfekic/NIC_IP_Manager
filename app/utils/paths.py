r"""Filesystem locations used by the application.

All writable application data lives under ``%LOCALAPPDATA%\IP_CHANGER`` so the
application never requires write access to ``C:\Program Files``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from app import __app_id__


def is_frozen() -> bool:
    """True when running from a PyInstaller-built executable."""
    return getattr(sys, "frozen", False)


def resource_root() -> Path:
    """Directory containing bundled read-only resources (assets, icons)."""
    if is_frozen():
        # PyInstaller onefile extracts to a temporary directory exposed as _MEIPASS.
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[2]


def asset_path(*parts: str) -> Path:
    return resource_root().joinpath("assets", *parts)


def data_dir() -> Path:
    """Per-user writable application data directory."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        root = Path(base) / __app_id__
    else:  # pragma: no cover - only on badly broken environments
        root = Path.home() / f".{__app_id__.lower()}"
    root.mkdir(parents=True, exist_ok=True)
    return root


def log_dir() -> Path:
    d = data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def database_path() -> Path:
    return data_dir() / "ip_changer.db"


def settings_path() -> Path:
    return data_dir() / "settings.json"


def journal_path() -> Path:
    """Crash-recovery journal for an in-flight network operation."""
    return data_dir() / "pending_operation.json"
