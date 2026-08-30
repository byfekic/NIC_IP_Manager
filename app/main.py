"""Application entry point (specification section 45).

Startup sequence:
  1. Check the Windows version.
  2. Check Administrator status and elevate if required.
  3. Initialise logging.
  4. Initialise the database.
  5. Enumerate adapters and load presets (done by the main window).
  6. Display the main window.

Every startup failure produces a readable dialog rather than a traceback.
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from app import __app_id__, __app_name__, __version__
from app.utils.logging_setup import configure_logging, get_logger, install_excepthook
from app.utils.paths import asset_path

log = get_logger(__name__)


def parse_arguments(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="IP_CHANGER",
        description="Professional network configuration utility.",
    )
    parser.add_argument(
        "--no-elevate",
        action="store_true",
        help="Do not attempt to restart with Administrator privileges.",
    )
    parser.add_argument(
        "--elevated-restart",
        action="store_true",
        help=argparse.SUPPRESS,  # internal marker, prevents elevation loops
    )
    parser.add_argument(
        "--debug", action="store_true", help="Enable verbose logging."
    )
    parser.add_argument(
        "--version", action="version", version=f"{__app_name__} {__version__}"
    )
    return parser.parse_args(argv if argv is not None else sys.argv[1:])


def _fatal(title: str, message: str) -> int:
    """Show a startup failure without requiring a running main window."""
    app = QApplication.instance() or QApplication(sys.argv)
    box = QMessageBox()
    box.setIcon(QMessageBox.Critical)
    box.setWindowTitle(title)
    box.setText(message)
    box.exec()
    return 1


def main(argv: Optional[list[str]] = None) -> int:
    arguments = parse_arguments(argv)

    # -- 3. logging ------------------------------------------------------
    logfile = configure_logging(
        level=logging.DEBUG if arguments.debug else logging.INFO
    )
    install_excepthook()
    log.info("=" * 70)
    log.info("%s %s starting", __app_name__, __version__)
    log.info("Log file: %s", logfile)

    # -- 1. platform -----------------------------------------------------
    from app.security.elevation import (
        check_supported_platform,
        is_elevated,
        relaunch_as_admin,
        was_relaunched,
    )

    supported, problem = check_supported_platform()
    if not supported:
        log.error("Unsupported platform: %s", problem)
        return _fatal("Unsupported system", problem)

    # -- 2. elevation ----------------------------------------------------
    elevated = is_elevated()
    log.info("Administrator: %s", elevated)
    if not elevated and not arguments.no_elevate and not was_relaunched():
        log.info("Not elevated; requesting Administrator privileges")
        if relaunch_as_admin():
            log.info("Elevated instance started; exiting this one")
            return 0
        # The operator declined UAC. Continue in read-only mode rather than
        # refusing to start, and tell them what that means.
        log.warning("Elevation declined; continuing without Administrator rights")

    # -- Qt application --------------------------------------------------
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName(__app_name__)
    app.setApplicationDisplayName(__app_name__)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(__app_id__)

    icon_file = asset_path("ip_changer.ico")
    if icon_file.exists():
        app.setWindowIcon(QIcon(str(icon_file)))

    font = QFont("Segoe UI", 10)
    app.setFont(font)

    # -- 4. storage ------------------------------------------------------
    from app.gui.main_window import MainWindow
    from app.network.ip_manager import NetworkManager
    from app.storage.database import Database
    from app.storage.history import HistoryStore
    from app.storage.presets import PresetStore
    from app.storage.settings import Settings
    from app.utils.errors import IPChangerError

    settings = Settings()

    try:
        database = Database()
    except IPChangerError as exc:
        # Preset storage is not essential for changing an IP address, so warn
        # and continue in a degraded mode rather than refusing to start.
        log.error("Database unavailable: %s", exc.message)
        QMessageBox.warning(
            None,
            "Storage unavailable",
            f"{exc.message}\n\n{exc.detail}",
        )
        database = None  # type: ignore[assignment]

    if database is None:
        return _fatal(
            "Storage unavailable",
            "The configuration database could not be opened and the application "
            "cannot continue. Check that your user profile is accessible.",
        )

    presets = PresetStore(database)
    history = HistoryStore(database)
    manager = NetworkManager()

    # -- 5/6. main window ------------------------------------------------
    try:
        window = MainWindow(manager, presets, history, settings)
    except Exception:
        log.exception("The main window could not be created")
        return _fatal(
            "Startup failed",
            "The application window could not be created.\n\n"
            f"Details have been written to:\n{logfile}",
        )

    window.show()
    log.info("Main window displayed")

    try:
        exit_code = app.exec()
    finally:
        database.close()
        log.info("%s exiting with code %s", __app_name__, locals().get("exit_code", 0))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
