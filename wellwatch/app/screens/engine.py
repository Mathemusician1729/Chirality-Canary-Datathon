"""Replay & engine: start/stop the replay or the live engine, watch progress and the log, back up and
restore the alert database, and see the replay scoreboard (WellWatch vs the simple run-life rule)."""

from __future__ import annotations

import pandas as pd
import pyqtgraph as pg
from PySide6.QtCore import QDate, QTimer
from PySide6.QtWidgets import (QCheckBox, QDateEdit, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout, QWidget)

from ..theme import PALETTES, money, pct
from ..widgets import DataTable, muted, title
from ..workers import run_task


class EngineScreen(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.proc = None
        lay = QVBoxLayout(self)
        lay.addWidget(title("Replay & engine"))
        lay.addWidget(muted("Replay feeds the held-back July–October 2026 days into Canary one day at a time; the engine "
                            "reads each day back from Canary, scores every running well and manages alerts. Only one "
                            "engine can write to Canary at a time."))
        row = QHBoxLayout()
        box = QGroupBox("Replay")
        f = QFormLayout(box)
        self.start = QDateEdit(QDate(2026, 7, 1)); self.start.setCalendarPopup(True)
        self.end = QDateEdit(QDate(2026, 10, 26)); self.end.setCalendarPopup(True)
        self.pace = QDoubleSpinBox(); self.pace.setRange(0, 600); self.pace.setValue(0); self.pace.setSuffix(" s/day")
        self.pace.setToolTip("0 = full speed (7-day blocks). Any pause reads one day at a time, which is much slower.")
        self.fresh = QCheckBox("Start fresh (clears alerts and scores)")
        f.addRow("Start", self.start); f.addRow("End", self.end); f.addRow("Pace", self.pace); f.addRow(self.fresh)
        self.b_replay = QPushButton("Start replay")
        self.b_replay.clicked.connect(self.start_replay)
        f.addRow(self.b_replay)
        row.addWidget(box)
        box2 = QGroupBox("Live engine")
        f2 = QFormLayout(box2)
        self.poll = QDoubleSpinBox(); self.poll.setRange(5, 3600); self.poll.setValue(60); self.poll.setSuffix(" s")
        f2.addRow("Poll Canary every", self.poll)
        self.b_live = QPushButton("Start live engine")
        self.b_live.clicked.connect(self.start_live)
        f2.addRow(self.b_live)
        self.b_stop = QPushButton("Stop engine")
        self.b_stop.setObjectName("secondary")
        self.b_stop.clicked.connect(self.stop)
        f2.addRow(self.b_stop)
        row.addWidget(box2)
        box3 = QGroupBox("Progress")
        f3 = QVBoxLayout(box3)
        self.state = QLabel("-")
        self.state.setWordWrap(True)
        f3.addWidget(self.state)
        self.bar = QProgressBar()
        f3.addWidget(self.bar)
        row.addWidget(box3, 1)
        lay.addLayout(row)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(400)
        self.log.setFixedHeight(150)
        lay.addWidget(QLabel("<b>Engine log</b>"))
        lay.addWidget(self.log)

        bk = QGroupBox("Backups of the alerts and scores (cache/backups; Canary data is not affected)")
        bl = QHBoxLayout(bk)
        self.backups = DataTable({"created": "Created", "reason": "Why", "days": "Days covered",
                                  "scored_days": "Scored days", "alerts": "Alerts", "file": "File"})
        self.backups.setFixedHeight(130)
        self.backups.clicked.connect(lambda _: self._backup_buttons())
        bl.addWidget(self.backups, 1)
        bb = QVBoxLayout()
        self.b_backup = QPushButton("Back up now"); self.b_backup.setObjectName("secondary")
        self.b_restore = QPushButton("Restore selected…"); self.b_restore.setObjectName("secondary")
        self.b_backup.clicked.connect(self.backup_now)
        self.b_restore.clicked.connect(self.restore)
        bb.addWidget(self.b_backup); bb.addWidget(self.b_restore); bb.addStretch()
        bl.addLayout(bb)
        lay.addWidget(bk)
        self._running = False
        self._backup_paths = {}

        sb = QHBoxLayout()
        sb.addWidget(QLabel("<b>Replay scoreboard</b>: did warnings come before real crew-needing failures?"))
        sb.addStretch()
        self.b_score = QPushButton("Re-run scoreboard")
        self.b_score.setObjectName("secondary")
        self.b_score.clicked.connect(self.run_scoreboard)
        sb.addWidget(self.b_score)
        lay.addLayout(sb)
        low = QHBoxLayout()
        self.score = DataTable()
        low.addWidget(self.score, 3)
        self.hist = pg.PlotWidget(title="Warning lead time of caught failures (days)")
        low.addWidget(self.hist, 2)
        lay.addLayout(low, 1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll_progress)
        self.timer.start(3000)

    def apply_theme(self, theme):
        self.hist.setBackground(PALETTES[theme]["plot_bg"])

    def refresh(self):
        self.poll_progress()
        self.load_backups()
        run_task(self.ctx.services.scoreboard, on_done=self._score, on_error=self.ctx.error)

    # ------------------------------------------------------------------ backups
    def load_backups(self):
        run_task(self.ctx.services.list_backups, on_done=self._backups, on_error=self.ctx.error)

    def _backups(self, df):
        self._backup_paths = dict(zip(df["file"], df["path"])) if len(df) else {}
        if len(df):
            show = pd.DataFrame({
                "created": [str(c)[:16].replace("T", " ") for c in df["created"]],
                "reason": df["reason"],
                "days": [f"{a} to {b}" if a else "-" for a, b in zip(df["first_day"], df["last_day"])],
                "scored_days": df["scored_days"],
                "alerts": [f"{o + c:,} ({o} open)" for o, c in zip(df["open_alerts"], df["closed_alerts"])],
                "file": df["file"]})
        else:
            show = pd.DataFrame(columns=["created", "reason", "days", "scored_days", "alerts", "file"])
        self.backups.set_df(show)
        self._backup_buttons()

    def _backup_buttons(self):
        row = self.backups.selected_row()
        self.b_restore.setEnabled(bool(row) and not self._running)
        self.b_backup.setEnabled(not self._running)

    def backup_now(self):
        run_task(self.ctx.services.backup_now, on_done=lambda m: (self.ctx.status(m), self.load_backups()),
                 on_error=self.ctx.error)

    def restore(self):
        row = self.backups.selected_row()
        if not row:
            return
        cur = self.ctx.services.alert_db_summary()
        if not self._confirm(
                "Restore backup?",
                f"Replace the current alerts and scores with the backup from {row['created']} "
                f"({row['reason']}, {row['days']}, {row['alerts']})?",
                f"The current data ({cur['scored_days']} scored days, {cur['open_alerts'] + cur['closed_alerts']:,} "
                "alerts) is backed up first, so this can be undone. Canary data is not affected."):
            return
        run_task(self.ctx.services.restore_backup, self._backup_paths[row["file"]],
                 on_done=lambda m: (self.ctx.status(m), self.ctx.refresh_current()), on_error=self.ctx.error)

    def poll_progress(self):
        if not self.isVisible():
            return
        s = self.ctx.services
        run_task(lambda: (s.engine_status(), s.progress(), s.tail_log(8000)), on_done=self._progress)

    def _progress(self, res):
        eng, prog, log = res
        running = bool(eng) or (self.proc is not None and self.proc.poll() is None)
        self.b_replay.setEnabled(not running)
        self.b_live.setEnabled(not running)
        self.b_stop.setEnabled(running)
        if running != self._running:
            self._running = running
            self._backup_buttons()
        last = prog.get("last_scored_day")
        self.state.setText(f"<b>{'Running: ' + eng['mode'] + ' (pid ' + str(eng['pid']) + ')' if eng else 'Stopped'}</b><br>"
                           f"Last data day written: {prog.get('last_written_day') or '-'}<br>Last day scored: {last or '-'}")
        start, end = self.start.date().toPython(), self.end.date().toPython()
        if last:
            done = (pd.Timestamp(last) - pd.Timestamp(start)).days + 1
            total = (pd.Timestamp(end) - pd.Timestamp(start)).days + 1
            self.bar.setRange(0, max(total, 1))
            self.bar.setValue(max(0, min(done, total)))
        if log and log != self.log.toPlainText():
            self.log.setPlainText(log)
            self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _args(self, mode):
        if mode == "replay":
            a = ["replay", "--start", self.start.date().toString("yyyy-MM-dd"), "--end", self.end.date().toString("yyyy-MM-dd"),
                 "--seconds-per-day", str(self.pace.value())]
            return a + (["--fresh"] if self.fresh.isChecked() else [])
        return ["live", "--poll-seconds", str(self.poll.value())]

    def _confirm(self, title_: str, text: str, detail: str) -> bool:
        box = QMessageBox(QMessageBox.Warning, title_, text, QMessageBox.Yes | QMessageBox.No, self)
        box.setInformativeText(detail)
        box.setDefaultButton(QMessageBox.No)
        return box.exec() == QMessageBox.Yes

    def start_replay(self):
        days = self.start.date().daysTo(self.end.date()) + 1
        if self.fresh.isChecked():
            cur = self.ctx.services.alert_db_summary()
            if not self._confirm(
                    "Start fresh?",
                    f"This deletes {cur['open_alerts'] + cur['closed_alerts']:,} alerts with their audit trail and "
                    f"{cur['scored_days']} scored days, then replays {days} days from scratch.",
                    "Rebuilding takes about 45-60 minutes at pace 0 (much longer at a slower pace). Canary data is "
                    "not affected. A backup is saved first and can be restored from the Backups box below."):
                return
        if self.pace.value() > 0 and days > 14 and not self._confirm(
                "Slow replay?", f"A pace of {self.pace.value():g} s/day reads Canary one day at a time.",
                f"{days} days will take several hours (about 1-2 minutes per day). Set Pace to 0 to replay at full "
                "speed in 7-day blocks. Continue anyway?"):
            return
        if self._start(self._args("replay")):
            self.fresh.setChecked(False)  # never reuse "Start fresh" by accident

    def start_live(self):
        self._start(self._args("live"))

    def _start(self, args) -> bool:
        try:
            self.proc = self.ctx.services.start_engine(args)
            self.ctx.status(f"Engine started: {' '.join(args)}")
            return True
        except Exception as exc:
            self.ctx.error(str(exc))
            return False

    def stop(self):
        self.b_stop.setEnabled(False)
        run_task(self.ctx.services.stop_engine, self.proc, on_done=self.ctx.status, on_error=self.ctx.error)

    def run_scoreboard(self):
        self.b_score.setEnabled(False)
        run_task(self.ctx.services.run_scoreboard,
                 on_done=lambda _: (self.b_score.setEnabled(True), self.refresh()),
                 on_error=lambda e: (self.b_score.setEnabled(True), self.ctx.error(e)))

    def _score(self, res):
        summary, per = res
        if summary is None:
            self.score.set_df(pd.DataFrame({"": ["No scoreboard yet: finish a replay, then press Re-run scoreboard."]}))
            return
        show = summary.set_index("approach").T.reset_index().rename(columns={"index": "metric"})
        fmt = {"catch_rate": pct, "warning_precision": pct, "alert_precision": pct, "value_caught_cad": money,
               "value_all_failures_cad": money}
        for c in show.columns[1:]:
            show[c] = [fmt.get(m, lambda v: f"{v:,.1f}" if isinstance(v, float) else v)(v) for m, v in zip(show["metric"], show[c])]
        self.score.set_df(show)
        self.hist.clear()
        if per is not None and "lead_days_model" in per:
            lead = per.loc[per["caught_model"].astype(bool), "lead_days_model"].dropna().astype(int)
            counts = lead.value_counts().sort_index()
            if len(counts):
                self.hist.addItem(pg.BarGraphItem(x=counts.index.to_numpy(), height=counts.to_numpy(), width=0.8,
                                                  brush="#1f6f8b"))
