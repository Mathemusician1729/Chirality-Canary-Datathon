"""Render the app's drawn icon to an .ico for the exe:  python scripts/make_icon.py build/wellwatch.ico"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root, so `wellwatch` imports
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6.QtWidgets import QApplication  # noqa: E402

from wellwatch.app.main_window import app_icon  # noqa: E402

out = sys.argv[1] if len(sys.argv) > 1 else "build/wellwatch.ico"
app = QApplication([])
os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
ok = app_icon().pixmap(256, 256).toImage().save(out, "ICO")
print(("wrote " if ok else "could not write ") + out)
sys.exit(0 if ok else 1)
