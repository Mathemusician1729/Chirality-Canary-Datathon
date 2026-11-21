"""Launch the WellWatch desktop app:  python -m wellwatch.app"""

import sys

from PySide6.QtWidgets import QApplication

from .main_window import MainWindow, app_icon


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("WellWatch")
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)  # keeps running in the tray; Quit from the tray menu
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
