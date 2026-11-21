"""Fleet overview: KPI tiles, a risk map of every scored well, top-risk wells and a field summary.

Reads only the local alert database (daily scores, alerts) and the well register, so it never makes
fleet-wide Canary reads.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6.QtWidgets import QComboBox, QGridLayout, QHBoxLayout, QLabel, QSplitter, QVBoxLayout, QWidget
from PySide6.QtCore import Qt

from ..theme import LEVEL_COLORS, PALETTES, money
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
        ll.addWidget(QLabel("<b>Well map</b> (colour = alert level, size = expected loss; click a well to open it)"))
        self.map = pg.PlotWidget()
        self.map.setAspectLocked(False)
        self.map.setLabel("bottom", "Longitude")
        self.map.setLabel("left", "Latitude")
        self.map.showGrid(x=True, y=True, alpha=0.15)
        self.scatter = pg.ScatterPlotItem(hoverable=True, hoverPen=pg.mkPen("k", width=2), tip=self._tip)
        self.scatter.sigClicked.connect(self._clicked)
        self.map.addItem(self.scatter)
        ll.addWidget(self.map, 1)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(QLabel("<b>Top 15 wells by expected loss</b> (double-click to open)"))
        self.top = DataTable({"rank": "Rank", "uwi": "Well", "field": "Field", "risk": "7-day risk",
                              "expected_loss": "Expected loss", "level": "Level", "driver": "Why flagged"})
        self.top.doubleClicked.connect(lambda i: self.ctx.open_well(self.top.row_at(i)["uwi"]))
        rl.addWidget(self.top, 3)
        rl.addWidget(QLabel("<b>Fields</b>"))
        self.fields = DataTable({"field": "Field", "wells": "Running wells", "expected_loss": "Expected loss",
                                 "open_alerts": "Open alerts"})
        rl.addWidget(self.fields, 2)
        split.addWidget(right)
        split.setSizes([520, 760])
        lay.addWidget(split, 1)
        self.note = muted("")
        lay.addWidget(self.note)
        self._meta = {}

    # ------------------------------------------------------------------
    def apply_theme(self, theme: str):
        p = PALETTES[theme]
        self.map.setBackground(p["plot_bg"])

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
        m = ov["map"]
        self._meta = {i: r for i, r in enumerate(m.itertuples(index=False))}
        if len(m):
            el = m["expected_loss"].clip(lower=0).to_numpy()
            size = 5 + 18 * np.sqrt(el / max(el.max(), 1))
            brushes = [pg.mkBrush(LEVEL_COLORS.get(int(l), "#7f8c8d") + ("ff" if l >= 2 else ("cc" if l == 1 else "55")))
                       for l in m["level"]]
            self.scatter.setData(x=m["lon"].to_numpy(float), y=m["lat"].to_numpy(float), size=size, brush=brushes,
                                 pen=None, data=list(range(len(m))))
        else:
            self.scatter.clear()
        self.note.setText(f"Day {ov['day']:%Y-%m-%d}: scores come from the WellWatch engine (data read from Canary). "
                          "Expected loss = 7-day failure risk x expected cost of one crew-needing failure.")

    def _tip(self, x, y, data):
        r = self._meta.get(data)
        if r is None:
            return ""
        return f"{r.uwi} ({r.field})\nrisk {r.risk:.1%}, expected loss {money(r.expected_loss)}\n{r.driver}"

    def _clicked(self, _plot, points, *_):
        if len(points):
            r = self._meta.get(points[0].data())
            if r is not None:
                self.ctx.open_well(r.uwi)
