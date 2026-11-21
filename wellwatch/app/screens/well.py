"""Well detail: header (pump, run life, risk and reasons, alert, planned visit), linked trend charts read
from Canary (or the production records when offline), event markers, and the well's Axiom screen.
"""

from __future__ import annotations

import pandas as pd
import pyqtgraph as pg
from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import (QComboBox, QCompleter, QDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ..theme import LEVEL_COLORS, PALETTES, level_name, money, pct
from ..widgets import muted, title
from ..workers import run_task

EVENT_COLORS = {"Job": "#1f6f8b", "Failure": "#c0392b", "Downtime": "#9aa5b1", "Alert opened": "#e67e22",
                "Alert closed": "#2e7d32"}
PANELS = [  # (title, [(series, label, colour)]); risk first: it is what the operator acts on
    ("7-day failure risk (dashed: warning 8%, critical 15%)", [("FailureRisk7d", "risk", "#c0392b")]),
    ("Oil & water (m³/d)", [("OilRate", "oil", "#2e7d32"), ("WaterRate", "water", "#1f78b4")]),
    ("Water cut", [("WaterCut", "water cut", "#1f78b4")]),
    ("Pump efficiency", [("PumpEfficiency", "efficiency", "#8e44ad")]),
    ("Rod torque (ft-lb) & motor current (A)", [("RodTorque", "torque", "#d35400"), ("MotorCurrent", "current", "#7f8c8d")]),
    ("Pump speed (rpm)", [("PumpSpeed", "rpm", "#16a085")]),
    ("Tubing & casing pressure (kPa)", [("TubingPressure", "tubing", "#2c3e50"), ("CasingPressure", "casing", "#95a5a6")]),
]


def _ts(days) -> list[float]:
    return [pd.Timestamp(d).timestamp() for d in days]


class AxiomWindow(QDialog):
    """Embedded Axiom page. Accepts Canary's self-signed certificate for localhost only."""

    def __init__(self, parent, url: str):
        super().__init__(parent)
        self.setWindowTitle("Axiom")
        self.resize(1200, 800)
        lay = QVBoxLayout(self)
        try:
            from PySide6.QtWebEngineCore import QWebEnginePage
            from PySide6.QtWebEngineWidgets import QWebEngineView
        except ImportError:
            lay.addWidget(QLabel("Qt WebEngine is not available: open Axiom in your browser at " + url))
            return
        self.view = QWebEngineView()
        page = QWebEnginePage(self.view)
        page.certificateError.connect(self._cert)
        self.view.setPage(page)
        self.view.loadFinished.connect(lambda ok: None if ok else self.view.setHtml(
            f"<h3>Axiom is not reachable</h3><p>Check that the Canary Axiom service is running and the URL in "
            f"Settings is right.</p><p>{url}</p>"))
        lay.addWidget(self.view)
        self.view.setUrl(QUrl(url))

    @staticmethod
    def _cert(error):
        if error.url().host() in ("localhost", "127.0.0.1"):
            error.acceptCertificate()
        else:
            error.rejectCertificate()


class WellScreen(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.uwi = self.field = None
        lay = QVBoxLayout(self)
        head = QHBoxLayout()
        self.h_title = title("Well detail")
        head.addWidget(self.h_title)
        head.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search well (UWI)…  Ctrl+F")
        self.search.setMinimumWidth(240)
        self.search.returnPressed.connect(lambda: self.ctx.open_well(self.search.text().strip()))
        head.addWidget(self.search)
        self.range = QComboBox()
        for txt, d in [("30 days", 30), ("90 days", 90), ("365 days", 365)]:
            self.range.addItem(txt, d)
        self.range.setCurrentIndex(1)
        self.range.currentIndexChanged.connect(lambda _: self.load_trends())
        head.addWidget(self.range)
        self.b_axiom = QPushButton("Open in Axiom")
        self.b_axiom.clicked.connect(self.open_axiom)
        head.addWidget(self.b_axiom)
        lay.addLayout(head)

        grid = QGridLayout()
        self.f = {}
        items = [("field", "Field"), ("facility", "Facility"), ("pump", "Pump"), ("age", "Well age"),
                 ("repair", "Since last repair"), ("last_job", "Last job"), ("alert", "Alert"), ("plan", "Planned visit")]
        for i, (k, lab) in enumerate(items):
            grid.addWidget(muted(lab), (i // 4) * 2, i % 4)
            self.f[k] = QLabel("-")
            self.f[k].setWordWrap(True)
            grid.addWidget(self.f[k], (i // 4) * 2 + 1, i % 4)
        self.risk = QLabel("-")
        self.risk.setStyleSheet("font-size: 26pt; font-weight: 600;")
        risk_box = QVBoxLayout()
        risk_box.addWidget(muted("7-day failure risk"))
        risk_box.addWidget(self.risk)
        self.level = QLabel("")
        risk_box.addWidget(self.level)
        top = QHBoxLayout()
        top.addLayout(risk_box)
        top.addLayout(grid, 1)
        lay.addLayout(top)
        self.reasons = QLabel("")
        self.reasons.setWordWrap(True)
        lay.addWidget(self.reasons)

        self.plots = pg.GraphicsLayoutWidget()
        self.panels = []
        for i, (name, _) in enumerate(PANELS):
            p = self.plots.addPlot(row=i, col=0, axisItems={"bottom": pg.DateAxisItem()})
            p.setTitle(name, size="9pt")
            p.showGrid(x=True, y=True, alpha=0.15)
            p.setFixedHeight(150)
            p.addLegend(offset=(5, 5), labelTextSize="8pt")
            if self.panels:
                p.setXLink(self.panels[0])
            self.panels.append(p)
        self.plots.setFixedHeight(150 * len(PANELS) + 30)
        self.markers = pg.ScatterPlotItem(hoverable=True, tip=self._tip, size=11, symbol="t")
        self.panels[1].addItem(self.markers)  # event triangles on the oil & water chart
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.plots)
        lay.addWidget(scroll, 1)
        self.source = muted("Search a well or open one from the overview or inbox.")
        lay.addWidget(self.source)
        self._events = []

    def apply_theme(self, theme: str):
        self.plots.setBackground(PALETTES[theme]["plot_bg"])

    def set_wells(self, uwis):
        self.search.setCompleter(QCompleter(sorted(uwis)))

    def open(self, uwi: str, day=None):
        self.uwi, self.day = uwi, day
        self.h_title.setText(f"Well {uwi}")
        run_task(self.ctx.services.well_header, uwi, day, on_done=self._header, on_error=self.ctx.error)

    def _header(self, h):
        self.field, self.day = h["field"], h["day"]
        self.f["field"].setText(str(h["field"] or "-"))
        self.f["facility"].setText(str(h["facility"] or "-"))
        size = f", {h['pump_size']:.0f} m³/d/100rpm" if h.get("pump_size") and pd.notna(h["pump_size"]) else ""
        self.f["pump"].setText(f"{h['pump']}{size}")
        self.f["age"].setText(f"{h['well_age_years']:.0f} years" if h["well_age_years"] else "-")
        self.f["repair"].setText(f"{h['days_since_repair']} days" if h["days_since_repair"] is not None else "no repair on record")
        self.f["last_job"].setText(h["last_job"] or "-")
        self.f["last_job"].setToolTip(h.get("last_job_summary") or "")
        a = h["alert"]
        self.f["alert"].setText(f"#{a['id']} {a['state']} since {a['opened_day']}" if a else "none open")
        self.f["plan"].setText(h["planned_visit"] or "-")
        lvl = h["level"] or 0
        self.risk.setText(pct(h["risk"]) if h["risk"] is not None else "-")
        self.risk.setStyleSheet(f"font-size: 26pt; font-weight: 600; color: {LEVEL_COLORS[lvl]};")
        self.level.setText(f"{level_name(lvl)}  ·  expected loss {money(h['expected_loss'])}  ·  scored {h['scored_day'] or '-'}")
        self.reasons.setText(f"<b>Why flagged:</b> {h['driver'] or '-'}")
        self.load_trends()

    def load_trends(self):
        if not self.uwi or not self.field:
            return
        run_task(self.ctx.services.well_trends, self.uwi, self.field, self.day, self.range.currentData(),
                 on_done=self._trends, on_error=self.ctx.error)

    def _trends(self, tr):
        daily, events = tr["daily"], tr["events"]
        for p, (_, series) in zip(self.panels, PANELS):
            for item in list(p.items):
                if item is not self.markers:
                    p.removeItem(item)
            if p.legend:
                p.legend.clear()
            for col, lab, colour in series:
                if col in daily and daily[col].notna().any():
                    s = daily[col].dropna()
                    p.plot(_ts(s.index), s.to_numpy(float), pen=pg.mkPen(colour, width=2), name=lab)
        risk = self.panels[0]
        for y, c in [(0.08, LEVEL_COLORS[2]), (0.15, LEVEL_COLORS[3])]:
            risk.addItem(pg.InfiniteLine(pos=y, angle=0, pen=pg.mkPen(c, style=Qt.DashLine)))
        # collapse consecutive downtime days into one marker per run
        merged, last = [], None
        for e in events:
            if e["kind"] == "Downtime" and last and last["kind"] == "Downtime" and (e["day"] - last["end"]).days <= 1 \
                    and e["text"] == last["text"]:
                last["end"] = e["day"]
                last["n"] += 1
                continue
            last = {**e, "end": e["day"], "n": 1}
            merged.append(last)
        self._events = merged
        for p in self.panels:
            for e in merged:
                if e["kind"] != "Downtime" or e["n"] >= 2:
                    p.addItem(pg.InfiniteLine(pos=pd.Timestamp(e["day"]).timestamp(), angle=90,
                                              pen=pg.mkPen(EVENT_COLORS.get(e["kind"], "#999"), width=1,
                                                           style=Qt.DotLine if e["kind"] == "Downtime" else Qt.SolidLine)))
        top = self.panels[1]
        ymax = max([daily[c].max() for c in ("OilRate", "WaterRate") if c in daily and daily[c].notna().any()] or [1])
        self.markers.setData(x=[pd.Timestamp(e["day"]).timestamp() for e in merged], y=[ymax * 1.05] * len(merged),
                             brush=[pg.mkBrush(EVENT_COLORS.get(e["kind"], "#999")) for e in merged],
                             data=list(range(len(merged))))
        top.addItem(self.markers) if self.markers not in top.items else None
        if len(daily):
            top.setXRange(_ts([daily.index.min()])[0], _ts([daily.index.max()])[0])
        n = {k: sum(1 for e in merged if e["kind"] == k) for k in EVENT_COLORS}
        self.source.setText(f"Source: {tr['source']}. Markers (hover the triangles): "
                            + ", ".join(f"<span style='color:{EVENT_COLORS[k]}'>{k} {v}</span>" for k, v in n.items()))

    def _tip(self, x, y, data):
        e = self._events[data] if data is not None and data < len(self._events) else None
        if not e:
            return ""
        span = f" ({e['n']} days)" if e.get("n", 1) > 1 else ""
        return f"{pd.Timestamp(e['day']):%Y-%m-%d} {e['kind']}{span}\n{e['text']}"

    def open_axiom(self):
        url = self.ctx.services.settings.axiom_url(self.field or "", self.uwi or "")
        AxiomWindow(self, url).show()
