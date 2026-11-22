"""Launch the WellWatch desktop app:  python -m wellwatch.app   (or dist/WellWatch/WellWatch.exe)

The packaged exe also runs the engine and scoreboard for the app:  WellWatch.exe --module wellwatch.engine live
"""

import importlib
import os
import sys

# Modules the packaged exe may run in place of `python -m <module>`
RUNNABLE = {"wellwatch.engine", "wellwatch.scoreboard", "wellwatch.plan", "wellwatch.replay"}


def run_module(argv: list[str]) -> int:
    module, args = argv[0], argv[1:]
    if module not in RUNNABLE:
        print(f"unknown module {module}; one of {sorted(RUNNABLE)}", file=sys.stderr)
        return 2
    importlib.import_module(module).main(args)
    return 0


def main() -> int:
    # A windowed exe has no console; keep print/logging working when output is not redirected.
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
    if len(sys.argv) > 2 and sys.argv[1] == "--module":
        return run_module(sys.argv[2:])

    from PySide6.QtWidgets import QApplication

    from wellwatch.app.main_window import MainWindow, app_icon

    app = QApplication(sys.argv)
    app.setApplicationName("WellWatch")
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)  # keeps running in the tray; Quit from the tray menu
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
