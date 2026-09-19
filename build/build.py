"""Reproducible build script for IP_CHANGER (specification section 48).

Usage:
    python build/build.py            build IP_CHANGER.exe
    python build/build.py --clean    remove build artefacts first
    python build/build.py --test     run the test suite before building

The result is dist/IP_CHANGER.exe: a single file, no console window, with an
icon, version information and an Administrator manifest.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPEC = PROJECT_ROOT / "build" / "ip_changer.spec"
DIST = PROJECT_ROOT / "dist"
WORK = PROJECT_ROOT / "build" / "_work"

sys.path.insert(0, str(PROJECT_ROOT))
from app import __version__  # noqa: E402

VERSION_TEMPLATE = """# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({major}, {minor}, {patch}, 0),
    prodvers=({major}, {minor}, {patch}, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [StringStruct('CompanyName', 'IP_CHANGER'),
         StringStruct('FileDescription', 'Professional Network Configuration Utility'),
         StringStruct('FileVersion', '{version}'),
         StringStruct('InternalName', 'IP_CHANGER'),
         StringStruct('OriginalFilename', 'IP_CHANGER.exe'),
         StringStruct('ProductName', 'IP CHANGER'),
         StringStruct('ProductVersion', '{version}')])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def write_version_info() -> Path:
    """Generate the Windows version resource from the single source of truth."""
    # Tolerates a suffixed version such as "1.2.0rc1": non-numeric parts are
    # dropped and the tuple is padded to three components.
    numbers = [int(part) for part in __version__.split(".") if part.isdigit()]
    major, minor, patch = (*numbers, 0, 0, 0)[:3]
    target = PROJECT_ROOT / "build" / "version_info.txt"
    target.write_text(
        VERSION_TEMPLATE.format(
            major=major, minor=minor, patch=patch, version=__version__
        ),
        encoding="utf-8",
    )
    print(f"  version resource : {__version__}")
    return target


def ensure_icon() -> None:
    icon = PROJECT_ROOT / "assets" / "ip_changer.ico"
    if icon.exists():
        print(f"  icon             : {icon.name}")
    else:
        print("  icon             : MISSING (the build continues without one)")


def clean() -> None:
    for path in (DIST, WORK, PROJECT_ROOT / "build" / "IP_CHANGER"):
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
            print(f"  removed {path}")


def run_tests() -> bool:
    print("\nRunning the test suite...")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q"],
        cwd=str(PROJECT_ROOT),
        check=False,
    )
    return result.returncode == 0


def build() -> int:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print(
            "\nPyInstaller is not installed.\n"
            "  pip install -r requirements-dev.txt",
            file=sys.stderr,
        )
        return 1

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        "--distpath",
        str(DIST),
        "--workpath",
        str(WORK),
        str(SPEC),
    ]
    print("\nBuilding...")
    result = subprocess.run(command, cwd=str(PROJECT_ROOT), check=False)
    if result.returncode != 0:
        print("\nBuild failed.", file=sys.stderr)
        return result.returncode

    executable = DIST / "IP_CHANGER.exe"
    if not executable.exists():
        print("\nBuild reported success but IP_CHANGER.exe was not produced.", file=sys.stderr)
        return 1

    size_mb = executable.stat().st_size / (1024 * 1024)
    print("\n" + "=" * 60)
    print(f"  Built: {executable}")
    print(f"  Size : {size_mb:.1f} MB")
    print("=" * 60)
    print(
        "\nThe executable requests Administrator privileges when started.\n"
        "It is self-contained: Python does not need to be installed on the\n"
        "target machine."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Build IP_CHANGER.exe")
    parser.add_argument("--clean", action="store_true", help="Remove build artefacts first.")
    parser.add_argument("--test", action="store_true", help="Run tests before building.")
    arguments = parser.parse_args()

    print("=" * 60)
    print(f"  IP_CHANGER build  -  version {__version__}")
    print("=" * 60)

    if arguments.clean:
        print("\nCleaning...")
        clean()

    print("\nPreparing resources...")
    write_version_info()
    ensure_icon()

    if arguments.test and not run_tests():
        print("\nTests failed; the build was not started.", file=sys.stderr)
        return 1

    return build()


if __name__ == "__main__":
    sys.exit(main())
