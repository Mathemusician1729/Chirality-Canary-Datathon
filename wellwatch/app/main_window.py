"""Main window: left navigation, the five screens, status bar, tray icon with alert notifications,
keyboard shortcuts (Ctrl+1..5 screens, F5 refresh) and the light/dark theme."""

from __future__ import annotations

import time

import pandas as pd
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QListWidget, QMainWindow, QMenu, QMessageBox,
                               QStackedWidget, QSystemTrayIcon, QWidget)

from .. import config
from .screens.engine import EngineScreen
from .screens.inbox import InboxScreen
from .screens.overview import OverviewScreen
from .screens.planner import PlannerScreen
from .screens.settings import SettingsScreen
from .services import Services
from .theme import ACCENT, PALETTES, stylesheet
from .workers import run_task, tracker

SCREENS = ["Fleet overview", "Alert inbox", "Crew planner", "Replay & engine", "Settings & health"]


def app_icon() -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(ACCENT))
    p.setPen(Qt.NoPen)
    p.drawEllipse(2, 2, 60, 60)
    p.setPen(QColor("white"))
    p.setFont(QFont("Segoe UI", 28, QFont.Bold))
    p.drawText(pix.rect(), Qt.AlignCenter, "W")
    p.end()
    return QIcon(pix)


class MainWindow(QMainWindow):
    def __init__(self, services: Services | None = None, start_timers: bool = True):
        super().__init__()
        self.services = services or Services()
        self.apply_connection_settings()
        self.setWindowTitle("WellWatch: week-ahead well failure early warning")
        self.setWindowIcon(app_icon())
        self.resize(1440, 900)
        self._quitting = False

        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(200)
        self.nav.addItems(SCREENS)
        lay.addWidget(self.nav)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.overview = OverviewScreen(self)
        self.inbox = InboxScreen(self)
        self.planner = PlannerScreen(self)
        self.engine = EngineScreen(self)
        self.settings_screen = SettingsScreen(self)
        self.screens = [self.overview, self.inbox, self.planner, self.engine, self.settings_screen]
        for s in self.screens:
            self.stack.addWidget(s)
        self.nav.currentRowChanged.connect(self._goto)

        # status bar
        self.s_canary, self.s_engine, self.s_refresh, self.s_tasks = QLabel(), QLabel(), QLabel(), QLabel()
        for w in (self.s_canary, self.s_engine, self.s_refresh):
            self.statusBar().addPermanentWidget(w)
        self.statusBar().addPermanentWidget(self.s_tasks)
        tracker.changed.connect(lambda n: self.s_tasks.setText(f"⏳ {n} task(s)" if n else ""))

        # shortcuts
        for i in range(len(SCREENS)):
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self, activated=lambda i=i: self.nav.setCurrentRow(i))
        QShortcut(QKeySequence("F5"), self, activated=self.refresh_current)

        # tray + notifications
        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(app_icon(), self)
            menu = QMenu()
            menu.addAction(QAction("Open WellWatch", self, triggered=self._restore))
            menu.addAction(QAction("Alert inbox", self, triggered=lambda: (self._restore(), self.nav.setCurrentRow(1))))
            menu.addSeparator()
            menu.addAction(QAction("Quit", self, triggered=self.quit))
            self.tray.setContextMenu(menu)
            self.tray.setToolTip("WellWatch")
            self.tray.activated.connect(lambda r: self._restore() if r == QSystemTrayIcon.Trigger else None)
            self.tray.messageClicked.connect(self._notification_clicked)
            self.tray.show()
        self._last_event_id = None
        self._notified_alert = None

        self.apply_theme(self.services.settings.theme)
        self.nav.setCurrentRow(0)
        self.connection_changed(retest=True)
        if start_timers:
            self.t_status = QTimer(self, timeout=self._status_tick)
            self.t_status.start(5000)
            self.t_notify = QTimer(self, timeout=self.check_notifications)
            self.t_notify.start(30_000)
            QTimer.singleShot(2000, self.check_notifications)

    # ------------------------------------------------------------------ context API used by screens
    def status(self, msg: str):
        self.statusBar().showMessage(str(msg), 12_000)

    def error(self, msg: str):
        self.statusBar().showMessage(f"⚠ {msg}", 20_000)

    def apply_theme(self, theme: str):
        QApplication.instance().setStyleSheet(stylesheet(theme))
        pg.setConfigOptions(antialias=True, foreground=PALETTES[theme]["plot_fg"])
        for s in self.screens:
            if hasattr(s, "apply_theme"):
                s.apply_theme(theme)

    def apply_connection_settings(self):
        st = self.services.settings
        config.CANARY_HOST, config.READ_API_PORT, config.WRITE_API_PORT = st.canary_host, st.read_port, st.write_port

    def connection_changed(self, retest: bool = False):
        self.apply_connection_settings()
        if retest:
            run_task(self.services.test_connection, on_done=self._conn, on_error=self.error)
        else:
            self._conn((self.services.online, ""))

    def _conn(self, res):
        ok, msg = res
        self.s_canary.setText(f"Canary: {'connected' if ok else 'offline'}")
        self.s_canary.setStyleSheet(f"color: {'#2e7d32' if ok else '#c0392b'};")
        if msg:
            self.status(msg)
        self.planner._buttons(self.planner.res is not None and len(self.planner.res.get("plan", [])) > 0)

    # ------------------------------------------------------------------
    def _goto(self, i: int):
        self.stack.setCurrentIndex(i)
        self.refresh_current()

    def refresh_current(self):
        self.services.clear_cache()
        s = self.stack.currentWidget()
        if hasattr(s, "refresh"):
            s.refresh()
        self.s_refresh.setText(f"refreshed {time.strftime('%H:%M:%S')}")

    def _status_tick(self):
        eng = self.services.engine_status()
        self.s_engine.setText(f"Engine: running ({eng['mode']})" if eng else "Engine: stopped")

    # ------------------------------------------------------------------ notifications
    def check_notifications(self):
        if not self.services.settings.notifications or not self.tray:
            return
        run_task(self._new_alerts, on_done=self._notify, on_error=lambda e: None)

    def _new_alerts(self):
        ev = self.services._sql("SELECT e.id AS eid, e.action, a.id, a.uwi, a.field, a.max_level, a.last_risk, a.driver, "
                                "a.state FROM alert_events e JOIN alerts a ON a.id = e.alert_id "
                                "WHERE e.action IN ('opened','escalated') ORDER BY e.id")
        return ev

    def _notify(self, ev: pd.DataFrame):
        if ev is None or ev.empty:
            return
        if self._last_event_id is None:  # first check: do not replay history
            self._last_event_id = int(ev["eid"].max())
            return
        new = ev[(ev["eid"] > self._last_event_id) & (ev["max_level"] >= 2) & (ev["state"] != "Shelved")]
        self._last_event_id = int(ev["eid"].max())
        if new.empty:
            return
        top = new.sort_values(["max_level", "last_risk"], ascending=False).iloc[0]
        self._notified_alert = int(top["id"])
        more = f" (+{len(new) - 1} more)" if len(new) > 1 else ""
        kind = "Critical" if top["max_level"] >= 3 else "Warning"
        self.tray.showMessage(f"WellWatch {kind}: {top['uwi']}{more}",
                              f"{top['field']}, 7-day risk {top['last_risk']:.0%}. {top['driver']}",
                              QSystemTrayIcon.Critical if top["max_level"] >= 3 else QSystemTrayIcon.Warning, 15_000)

    def _notification_clicked(self):
        self._restore()
        self.nav.setCurrentRow(1)
        if self._notified_alert:
            QTimer.singleShot(800, lambda: self.inbox.select_alert(self._notified_alert))

    # ------------------------------------------------------------------ window / tray
    def _restore(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        if self.tray and self.services.settings.minimise_to_tray and not self._quitting:
            self.hide()
            self.tray.showMessage("WellWatch is still running", "Alerts will keep appearing here. Quit from the tray menu.",
                                  QSystemTrayIcon.Information, 4000)
            event.ignore()
        else:
            if self.tray:
                self.tray.hide()
            event.accept()

    def quit(self):
        self._quitting = True
        self.close()
        QApplication.instance().quit()
