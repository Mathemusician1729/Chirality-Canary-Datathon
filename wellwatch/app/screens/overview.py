"""Fleet overview: KPI tiles, a map of every scored well on a real basemap, top-risk wells and a field summary.

Reads only the local alert database (daily scores, alerts) and the well register, so it never makes
fleet-wide Canary reads.
"""

from __future__ import annotations

import pandas as pd
from PySide6.QtWidgets import QComboBox, QGridLayout, QHBoxLayout, QLabel, QSplitter, QVBoxLayout, QWidget
from PySide6.QtCore import Qt

from ..map_view import WellMap
from ..theme import LEVEL_COLORS, money
from ..widgets import DataTable, Tile, muted, title
from ..workers import run_task


class OverviewScreen(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        lay = QVBoxLayout(self)
        head = QHBoxLayout()
        head.addWidget(title("Fleet overview"))
        head.addStretch()
        head.addWidget(QLabel("Day:"))
        self.day = QComboBox()
        self.day.setMinimumWidth(130)
        self.day.currentIndexChanged.connect(lambda _: self.load())
        head.addWidget(self.day)
        lay.addLayout(head)

        tiles = QGridLayout()
        self.t_wells = Tile("running wells scored")
        self.t_crit = Tile("critical alerts open")
        self.t_warn = Tile("warning alerts open")
        self.t_watch = Tile("wells on watch today")
        self.t_loss = Tile("expected failure cost, next 7 days")
        self.t_days = Tile("last data day / last scored day")
        self.t_engine = Tile("engine")
        for i, t in enumerate([self.t_wells, self.t_crit, self.t_warn, self.t_watch, self.t_loss, self.t_days, self.t_engine]):
            tiles.addWidget(t, 0, i)
        lay.addLayout(tiles)

        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("<b>Well map</b> (colour = alert level, size = expected loss; click a well for details)"))
        # offline mode means "no Canary"; the basemap needs only internet and falls back to a plot without it
        self.map = WellMap(ctx.services.settings.theme)
        ll.addWidget(self.map, 1)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(QLabel("<b>Top 15 wells by expected loss</b>"))
        self.top = DataTable({"rank": "Rank", "uwi": "Well", "field": "Field", "risk": "7-day risk",
                              "expected_loss": "Expected loss", "level": "Level", "driver": "Why flagged"})
        rl.addWidget(self.top, 3)
        rl.addWidget(QLabel("<b>Fields</b>"))
        self.fields = DataTable({"field": "Field", "wells": "Running wells", "expected_loss": "Expected loss",
                                 "open_alerts": "Open alerts"})
        rl.addWidget(self.fields, 2)
        split.addWidget(right)
        split.setSizes([640, 700])
        lay.addWidget(split, 1)
        self.note = muted("")
        lay.addWidget(self.note)
        self._meta = {}

    # ------------------------------------------------------------------
    def apply_theme(self, theme: str):
        self.map.set_theme(theme)

    def refresh(self):
        run_task(self.ctx.services.scored_days, on_done=self._got_days, on_error=self.ctx.error)

    def _got_days(self, days):
        cur = self.day.currentData()
        self.day.blockSignals(True)
        self.day.clear()
        for d in reversed(days):  # plain strings: Qt cannot compare pandas Timestamps in findData
            self.day.addItem(d.strftime("%Y-%m-%d"), d.strftime("%Y-%m-%d"))
        if cur is not None:
            self.day.setCurrentIndex(max(0, self.day.findData(cur)))
        self.day.blockSignals(False)
        self.load()

    def selected_day(self):
        v = self.day.currentData()
        return pd.Timestamp(v) if v else None

    def load(self):
        day = self.selected_day()
        run_task(self.ctx.services.overview, day, on_done=self._show, on_error=self.ctx.error)

    def _show(self, ov):
        k = ov["kpis"]
        if not k:
            self.note.setText("No scored days yet: start a replay or the live engine (Replay & engine screen).")
            return
        self.t_wells.set(f"{k['wells_scored']:,}")
        self.t_crit.set(str(k["critical"]), LEVEL_COLORS[3])
        self.t_warn.set(str(k["warning"]), LEVEL_COLORS[2])
        self.t_watch.set(str(k["watch"]), LEVEL_COLORS[1])
        self.t_loss.set(money(k["expected_loss"]))
        last_sc = k["last_scored_day"].strftime("%Y-%m-%d") if k["last_scored_day"] is not None else "-"
        self.t_days.set(f"{(k['last_data_day'] or '-')[5:] if k['last_data_day'] else '-'} / {last_sc[5:]}")
        eng = k["engine"]
        self.t_engine.set("Running" if eng else "Stopped", "#2e7d32" if eng else None,
                          f"engine ({eng['mode']}, pid {eng['pid']})" if eng else "engine (no process holds the lock)")
        self.top.set_df(ov["top"])
        self.top.sortByColumn(0, Qt.AscendingOrder)  # rank 1 first
        self.fields.set_df(ov["fields"])
        self.map.set_wells(ov["map"])
        self.note.setText(f"Day {ov['day']:%Y-%m-%d}: scores come from the WellWatch engine (data read from Canary). "
                          "Expected loss = 7-day failure risk x expected cost of one crew-needing failure.")
