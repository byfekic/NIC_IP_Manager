"""Development launcher: python run_dev.py [--no-elevate] [--debug]"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.main import main

if __name__ == "__main__":
    sys.exit(main())
