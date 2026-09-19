"""Administrator privilege detection and UAC elevation (section 21).

The application elevates itself through the standard Windows UAC mechanism.
The operator is never asked to open a command prompt as Administrator.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from typing import Sequence

from app.utils.logging_setup import get_logger

log = get_logger(__name__)

# Marker argument added to the relaunched process so a denied UAC prompt can
# never turn into an endless elevate-restart loop.
ELEVATION_MARKER = "--elevated-restart"

SW_SHOWNORMAL = 1
# ShellExecuteW returns a value <= 32 on failure.
SE_ERR_ACCESSDENIED = 5


def is_windows() -> bool:
    return os.name == "nt" and sys.platform == "win32"


def is_elevated() -> bool:
    """True when the current process holds Administrator rights."""
    if not is_windows():
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("Could not determine elevation state: %s", exc)
        return False


def was_relaunched() -> bool:
    """True when this process is the result of an elevation attempt."""
    return ELEVATION_MARKER in sys.argv


def _quote(argument: str) -> str:
    """Quote one argument for the Windows command line."""
    if not argument:
        return '""'
    if not any(c in argument for c in ' \t"'):
        return argument
    return '"' + argument.replace('"', r"\"") + '"'


def build_relaunch_arguments(extra: Sequence[str] = ()) -> str:
    """Rebuild the current command line, preserving application state."""
    if getattr(sys, "frozen", False):
        arguments = list(sys.argv[1:])
    else:
        arguments = [os.path.abspath(sys.argv[0])] + list(sys.argv[1:])
    arguments = [a for a in arguments if a != ELEVATION_MARKER]
    arguments.extend(extra)
    arguments.append(ELEVATION_MARKER)
    return " ".join(_quote(a) for a in arguments)


def relaunch_as_admin(extra_arguments: Sequence[str] = ()) -> bool:
    """Restart this application elevated via UAC.

    Returns True when Windows accepted the request (the caller should then
    exit), and False when the operator dismissed the prompt or elevation is
    not possible. Never raises.
    """
    if not is_windows():
        return False

    executable = sys.executable
    parameters = build_relaunch_arguments(extra_arguments)
    log.info("Requesting elevation: %s %s", executable, parameters)

    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            executable,
            parameters,
            os.path.abspath(os.getcwd()),
            SW_SHOWNORMAL,
        )
    except Exception as exc:  # pragma: no cover
        log.error("Elevation request failed: %s", exc)
        return False

    if int(result) > 32:
        log.info("Elevation accepted; the elevated instance is starting")
        return True

    if int(result) == SE_ERR_ACCESSDENIED:
        log.warning("The operator declined the UAC prompt")
    else:
        log.warning("Elevation failed with ShellExecuteW code %s", result)
    return False


def elevation_status_text() -> str:
    return "Administrator" if is_elevated() else "Administrator privileges required"


def windows_version() -> str:
    """A readable Windows version string for the diagnostics page."""
    try:
        completed = subprocess.run(
            ["cmd", "/c", "ver"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=0x08000000,
            shell=False,
            check=False,
        )
        line = (completed.stdout or "").strip()
        if line:
            return line.splitlines()[-1].strip()
    except Exception:  # pragma: no cover
        pass
    return f"{sys.platform} (build information unavailable)"


def check_supported_platform() -> tuple[bool, str]:
    """Verify the application is running on a supported Windows release."""
    if not is_windows():
        return False, (
            "IP CHANGER configures Windows network adapters and can only run on Windows."
        )
    try:
        version = sys.getwindowsversion()
    except Exception:  # pragma: no cover
        return True, ""
    if version.major < 10:
        return False, (
            "IP CHANGER requires Windows 10 or Windows 11. "
            f"This computer reports Windows version {version.major}.{version.minor}."
        )
    return True, ""
