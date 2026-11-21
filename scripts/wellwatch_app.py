"""PyInstaller entry point for the WellWatch desktop app (same as `python -m wellwatch.app`)."""

import sys

from wellwatch.app.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
