"""Structured, rotating application logging (specification section 23).

Logs are written to ``%LOCALAPPDATA%/IP_CHANGER/logs/ip_changer.log`` with
rotation, and mirrored to the console during development. No credentials are
ever logged: the application does not handle any.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from app.utils.paths import log_dir

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-28s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def configure_logging(level: int = logging.INFO, console: bool = True) -> Path:
    """Install rotating file logging. Safe to call more than once."""
    global _configured
    logfile = log_dir() / "ip_changer.log"
    if _configured:
        return logfile

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

    try:
        file_handler = logging.handlers.RotatingFileHandler(
            logfile, maxBytes=1_000_000, backupCount=5, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:
        # Logging must never prevent the application from starting.
        pass

    if console and sys.stderr is not None:
        # A frozen GUI build has no console, and a legacy code-page console
        # cannot encode the status glyphs used in messages, so never let the
        # console handler raise.
        try:
            sys.stderr.reconfigure(errors="backslashreplace")  # type: ignore[union-attr]
        except (AttributeError, OSError, ValueError):
            pass
        stream = logging.StreamHandler(sys.stderr)
        stream.setLevel(level)
        stream.setFormatter(formatter)
        root.addHandler(stream)

    logging.getLogger(__name__).debug("Logging initialised at %s", logfile)
    _configured = True
    return logfile


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def install_excepthook() -> None:
    """Log otherwise-unhandled exceptions instead of printing a traceback."""

    def _hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):  # pragma: no cover
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logging.getLogger("app.unhandled").critical(
            "Unhandled exception", exc_info=(exc_type, exc_value, exc_tb)
        )

    sys.excepthook = _hook
