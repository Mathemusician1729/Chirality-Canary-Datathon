"""Crew planner: solve the weekly crew plan with Gurobi (HiGHS fallback), compare the expected loss with
no action vs with the plan, see the crew x day schedule, send the plan to Canary and export work orders.

Main inputs (day, budget, effectiveness) are always visible; visit type, inspection cost, solver and
crews per field sit under "Advanced settings". What-if analyses live in their own tab.
"""

from __future__ import annotations

import pandas as pd
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QMessageBox,
                               QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
                               QToolButton, QVBoxLayout, QWidget)

from ..theme import LEVEL_COLORS, money, pct
from ..widgets import DataTable, Tile, muted, title
from ..workers import run_task


class PlannerScreen(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.res = None
        self.res_day = None
        lay = QVBoxLayout(self)
        lay.addWidget(title("Crew planner"))
        lay.addWidget(muted("Gurobi picks which at-risk wells each field crew visits this week, to minimise expected "
                            "failure loss plus visit cost."))
        split = QSplitter(Qt.Horizontal)

        # ---------------------------------------------------------------- left: inputs
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 10, 0)
        form = QFormLayout()
        self.day = QComboBox()
        self.day.currentIndexChanged.connect(lambda _: self._day_changed())
        form.addRow("Planning day", self.day)
        self.budget = QSpinBox(); self.budget.setRange(0, 1_000_000); self.budget.setSingleStep(5000)
        self.budget.setValue(60_000); self.budget.setPrefix("C$ ")
        form.addRow("Weekly budget", self.budget)
        self.eff = QDoubleSpinBox(); self.eff.setRange(0.05, 1.0); self.eff.setSingleStep(0.05); self.eff.setValue(0.5)
        self.eff.setToolTip("Share of a failure that a visit prevents (an assumption; see the What-if tab).")
        form.addRow("Visit effectiveness", self.eff)
        ll.addLayout(form)
        self.b_solve = QPushButton("Solve plan")
        self.b_solve.clicked.connect(self.solve)
        ll.addWidget(self.b_solve)

        self.b_adv = QToolButton()
        self.b_adv.setText("Advanced settings")
        self.b_adv.setCheckable(True)
        self.b_adv.setAutoRaise(True)
        self.b_adv.setArrowType(Qt.RightArrow)
        self.b_adv.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.b_adv.toggled.connect(self._toggle_advanced)
        ll.addWidget(self.b_adv)
        self.adv = QWidget()
        al = QVBoxLayout(self.adv)
        al.setContentsMargins(0, 0, 0, 0)
        af = QFormLayout()
        self.visit_type = QComboBox(); self.visit_type.addItems(["inspection", "proactive"])
        af.addRow("Visit type", self.visit_type)
        self.cost = QSpinBox(); self.cost.setRange(0, 100_000); self.cost.setSingleStep(250); self.cost.setValue(1_500)
        self.cost.setPrefix("C$ ")
        af.addRow("Inspection cost", self.cost)
        self.solver = QComboBox(); self.solver.addItems(["auto", "gurobi", "highs"])
        af.addRow("Solver", self.solver)
        al.addLayout(af)
        al.addWidget(QLabel("Crews per field (from the records; editable)"))
        self.crews = QTableWidget(0, 2)
        self.crews.setHorizontalHeaderLabels(["Field", "Crews"])
        self.crews.horizontalHeader().setStretchLastSection(True)
        self.crews.verticalHeader().setVisible(False)
        al.addWidget(self.crews, 1)
        self.records = muted("")
        al.addWidget(self.records)
        self.adv.setVisible(False)
        ll.addWidget(self.adv, 1)
        ll.addStretch(1)

        out = QHBoxLayout()
        self.b_send = QPushButton("Send to Canary")
        self.b_export = QPushButton("Export work orders"); self.b_export.setObjectName("secondary")
        self.b_send.clicked.connect(self.send)
        self.b_export.clicked.connect(self.export)
        out.addWidget(self.b_send)
        out.addWidget(self.b_export)
        ll.addLayout(out)
        split.addWidget(left)

        # ---------------------------------------------------------------- right: results
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(10, 0, 0, 0)
        tiles = QHBoxLayout()
        self.t_before = Tile("expected loss next 7 days, no action")
        self.t_after = Tile("with the plan (incl. visit cost)")
        self.t_saving = Tile("saving this week")
        self.t_visits = Tile("visits / plan cost")
        for t in (self.t_before, self.t_after, self.t_saving, self.t_visits):
            tiles.addWidget(t)
        rl.addLayout(tiles)
        self.summary = muted("Choose a planning day and press Solve plan.")
        rl.addWidget(self.summary)

        tabs = QTabWidget()
        self.grid = QTableWidget()
        self.grid.setEditTriggers(QTableWidget.NoEditTriggers)
        tabs.addTab(self.grid, "Schedule")
        self.table = DataTable({"visit_date": "Visit", "crew": "Crew", "uwi": "Well", "field": "Field", "risk": "Risk",
                                "level": "Level", "expected_loss_before": "Expected loss before",
                                "expected_loss_after": "After", "visit_cost": "Cost", "travel_hours": "Travel h"})
        tabs.addTab(self.table, "Visit list")
        whatif = QWidget()
        wl = QVBoxLayout(whatif)
        wb = QHBoxLayout()
        self.b_crew = QPushButton("Value of one more crew per field"); self.b_crew.setObjectName("secondary")
        self.b_sens = QPushButton("Effectiveness 25% / 50% / 75%"); self.b_sens.setObjectName("secondary")
        self.b_crew.clicked.connect(self.extra_crew)
        self.b_sens.clicked.connect(self.sensitivity)
        wb.addWidget(self.b_crew)
        wb.addWidget(self.b_sens)
        wb.addStretch()
        wl.addLayout(wb)
        self.extra = QLabel("")
        self.extra.setWordWrap(True)
        wl.addWidget(self.extra)
        self.sens = DataTable()
        wl.addWidget(self.sens, 1)
        tabs.addTab(whatif, "What if…")
        rl.addWidget(tabs, 1)
        split.addWidget(right)
        split.setSizes([300, 950])
        lay.addWidget(split, 1)
        self._clear_results()

    # ------------------------------------------------------------------ state
    def _toggle_advanced(self, on: bool):
        self.adv.setVisible(on)
        self.b_adv.setArrowType(Qt.DownArrow if on else Qt.RightArrow)

    def _buttons(self, have_plan: bool):
        online = self.ctx.services.online and not self.ctx.services.settings.offline
        self.b_send.setEnabled(have_plan and online)
        self.b_export.setEnabled(have_plan)

    def _clear_results(self, note: str = "Choose a planning day and press Solve plan."):
        self.res, self.res_day = None, None
        for t in (self.t_before, self.t_after, self.t_saving, self.t_visits):
            t.set("-")
        self.summary.setText(note)
        self.table.set_df(pd.DataFrame())
        self._fill_grid(pd.DataFrame())
        self.extra.setText("")
        self.sens.set_df(pd.DataFrame())
        self._buttons(False)

    def _day_changed(self):
        day = self._day()
        self._clear_results(f"Press Solve plan to plan the week after {day:%a %d %b %Y}." if day is not None else
                            "Choose a planning day and press Solve plan.")
        self.load_defaults()

    # ------------------------------------------------------------------ data
    def refresh(self):
        run_task(lambda: (self.ctx.services.scored_days(), self.ctx.services.default_planning_day()),
                 on_done=self._days, on_error=self.ctx.error)

    def _days(self, res):
        days, default = res
        if self.day.count() == len(days):
            return
        self.day.blockSignals(True)
        self.day.clear()
        for d in reversed(days):  # store plain strings: Qt cannot compare pandas Timestamps in findData
            self.day.addItem(f"{d:%Y-%m-%d} ({d:%a})", d.strftime("%Y-%m-%d"))
        if default is not None:
            self.day.setCurrentIndex(max(0, self.day.findData(default.strftime("%Y-%m-%d"))))
        self.day.blockSignals(False)
        self._day_changed()

    def _day(self):
        v = self.day.currentData()
        return pd.Timestamp(v) if v else None

    def load_defaults(self):
        day = self._day()
        if day is None:
            return
        run_task(self.ctx.services.plan_defaults, day, on_done=self._defaults, on_error=self.ctx.error)

    def _defaults(self, res):
        s, inputs = res
        self.crews.setRowCount(0)
        for f, n in sorted(inputs["crews"].items()):
            r = self.crews.rowCount()
            self.crews.insertRow(r)
            self.crews.setItem(r, 0, QTableWidgetItem(f))
            self.crews.setItem(r, 1, QTableWidgetItem(str(n)))
        self.records.setText(f"Rod Truck job from the records: {inputs['proactive_hours']:.1f} h, "
                             f"{money(inputs['proactive_cost_cad'])} (used for proactive visits).")

    def _settings(self) -> dict:
        crews = {}
        for r in range(self.crews.rowCount()):
            try:
                crews[self.crews.item(r, 0).text()] = max(1, int(self.crews.item(r, 1).text()))
            except (ValueError, AttributeError):
                pass
        kw = {"visit_type": self.visit_type.currentText(), "effectiveness": self.eff.value(),
              "weekly_budget_cad": float(self.budget.value()), "inspection_cost_cad": float(self.cost.value())}
        if crews:
            kw["crews"] = crews
        return kw

    # ------------------------------------------------------------------ solve
    def solve(self):
        day = self._day()
        if day is None:
            return
        self.summary.setText("Solving…")
        self.b_solve.setEnabled(False)
        run_task(self.ctx.services.plan, day, self.solver.currentText(), **self._settings(),
                 on_done=lambda r, d=day: self._solved(r, d),
                 on_error=lambda e: (self.b_solve.setEnabled(True), self.ctx.error(e)))

    def _solved(self, res, day):
        self.b_solve.setEnabled(True)
        if day != self._day():  # the user picked another day while this was solving
            return
        self.res, self.res_day = res, day
        self.t_before.set(money(res["loss_no_action"]))
        self.t_after.set(money(res["loss_with_plan"]))
        saving = res["reduction"]
        self.t_saving.set(money(saving), "#2e7d32" if saving > 0 else None,
                          f"saving this week ({pct(res['reduction_pct'])})")
        self.t_visits.set(f"{res['visits']} / {money(res['plan_cost'])}")
        self.summary.setText(
            f"Week after {day:%a %d %b %Y}. Whole fleet: {money(res['fleet_loss_no_action'])} → "
            f"{money(res['fleet_loss_with_plan'])}. Solver {res['solver']} ({res['status']}, {res['solve_seconds']} s).")
        plan = res["plan"]
        self.table.set_df(plan.drop(columns=["planning_day", "day"], errors="ignore") if len(plan) else plan)
        self._fill_grid(plan)
        self._buttons(len(plan) > 0)

    def _fill_grid(self, plan: pd.DataFrame):
        self.grid.clear()
        if plan is None or plan.empty:
            self.grid.setRowCount(0)
            self.grid.setColumnCount(0)
            return
        crews = sorted(plan["crew"].unique())
        dates = sorted(plan["visit_date"].unique())
        self.grid.setRowCount(len(crews))
        self.grid.setColumnCount(len(dates))
        self.grid.setVerticalHeaderLabels(crews)
        self.grid.setHorizontalHeaderLabels([f"{pd.Timestamp(d):%a %d %b}" for d in dates])
        for (crew, date), g in plan.groupby(["crew", "visit_date"]):
            r, c = crews.index(crew), dates.index(date)
            item = QTableWidgetItem("\n".join(f"{u}  {p:.0%}" for u, p in zip(g["uwi"], g["risk"])))
            lvl = int(g["level"].max())
            if lvl >= 2:
                item.setForeground(Qt.GlobalColor.white)
                item.setBackground(QColor(LEVEL_COLORS[lvl]))
            item.setToolTip("\n".join(f"{u} ({f}), risk {p:.0%}" for u, f, p in zip(g["uwi"], g["field"], g["risk"])))
            self.grid.setItem(r, c, item)
        self.grid.resizeColumnsToContents()
        self.grid.resizeRowsToContents()

    # ------------------------------------------------------------------ what-if
    def extra_crew(self):
        day = self._day()
        if day is None:
            return
        self.extra.setText("Re-solving with one more crew per field…")
        run_task(self.ctx.services.extra_crew_value, day, self.solver.currentText(), **self._settings(),
                 on_done=lambda r: self.extra.setText(
                     f"One more crew per field would lower this week's expected loss by "
                     f"<b>{money(r['value_of_extra_crew_cad'])}</b> ({money(r['loss_with_plan'])} → "
                     f"{money(r['loss_with_extra_crew'])})."), on_error=self.ctx.error)

    def sensitivity(self):
        day = self._day()
        if day is None:
            return
        self.extra.setText("Solving at 25%, 50% and 75% effectiveness…")
        kw = {k: v for k, v in self._settings().items() if k != "effectiveness"}
        run_task(self.ctx.services.sensitivity, day, self.solver.currentText(), **kw,
                 on_done=lambda df: (self.sens.set_df(df), self.extra.setText(
                     "How the plan changes with the share of a failure a visit prevents:")),
                 on_error=self.ctx.error)

    # ------------------------------------------------------------------ outputs
    def send(self):
        if not self.res:
            return
        if QMessageBox.question(self, "Send plan", f"Write {self.res['visits']} planned visits to Canary "
                                                   "(PlannedVisitDay / PlannedCrew tags)?") != QMessageBox.Yes:
            return
        run_task(self.ctx.services.send_plan, self.res_day, self.res,
                 on_done=lambda m: (self.ctx.status(m), self.summary.setText(m)), on_error=self.ctx.error)

    def export(self):
        if not self.res:
            return
        folder = QFileDialog.getExistingDirectory(self, "Export work orders to")
        if not folder:
            return
        run_task(self.ctx.services.export_work_orders, self.res_day, self.res, folder,
                 on_done=lambda paths: self.ctx.status(f"Exported {len(paths)} files to {folder}"), on_error=self.ctx.error)
