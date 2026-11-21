"""Replay & engine: start/stop the replay or the live engine, watch progress and the log, and see the
replay scoreboard (WellWatch vs the simple run-life rule)."""

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
        self.pace = QDoubleSpinBox(); self.pace.setRange(0, 600); self.pace.setValue(10); self.pace.setSuffix(" s/day")
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
        run_task(self.ctx.services.scoreboard, on_done=self._score, on_error=self.ctx.error)

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

    def start_replay(self):
        if self.fresh.isChecked() and QMessageBox.question(
                self, "Start fresh", "This deletes all alerts, their audit trail and the daily scores. Continue?") != QMessageBox.Yes:
            return
        self._start(self._args("replay"))

    def start_live(self):
        self._start(self._args("live"))

    def _start(self, args):
        try:
            self.proc = self.ctx.services.start_engine(args)
            self.ctx.status(f"Engine started: {' '.join(args)}")
        except Exception as exc:
            self.ctx.error(str(exc))

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
