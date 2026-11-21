"""Crew planner: solve the weekly crew plan with Gurobi (HiGHS fallback), see the expected loss with no
action vs with the plan, the crew x day schedule, send the plan to Canary and export work orders.
"""

from __future__ import annotations

import pandas as pd
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QScrollArea, QSpinBox, QSplitter, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ..theme import LEVEL_COLORS, money, pct
from ..widgets import DataTable, muted, title
from ..workers import run_task


class PlannerScreen(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.res = None
        lay = QVBoxLayout(self)
        lay.addWidget(title("Crew planner"))
        lay.addWidget(muted("Gurobi chooses which at-risk wells each field crew visits over the next 7 days. It "
                            "minimises expected failure loss + visit cost for the coming week, within crew hours, "
                            "the budget, and with critical wells visited by day 2."))
        split = QSplitter(Qt.Horizontal)

        left = QWidget()
        ll = QVBoxLayout(left)
        box = QGroupBox("Inputs")
        form = QFormLayout(box)
        self.day = QComboBox()
        self.day.currentIndexChanged.connect(lambda _: self.load_defaults())
        form.addRow("Planning day", self.day)
        self.visit_type = QComboBox()
        self.visit_type.addItems(["inspection", "proactive"])
        form.addRow("Visit type", self.visit_type)
        self.eff = QDoubleSpinBox(); self.eff.setRange(0.05, 1.0); self.eff.setSingleStep(0.05); self.eff.setValue(0.5)
        form.addRow("Effectiveness", self.eff)
        self.budget = QSpinBox(); self.budget.setRange(0, 1_000_000); self.budget.setSingleStep(5000); self.budget.setValue(60_000)
        self.budget.setPrefix("C$ ")
        form.addRow("Weekly budget", self.budget)
        self.cost = QSpinBox(); self.cost.setRange(0, 100_000); self.cost.setSingleStep(250); self.cost.setValue(1_500)
        self.cost.setPrefix("C$ ")
        form.addRow("Inspection cost", self.cost)
        self.solver = QComboBox(); self.solver.addItems(["auto", "gurobi", "highs"])
        form.addRow("Solver", self.solver)
        ll.addWidget(box)
        ll.addWidget(QLabel("<b>Crews per field</b> (observed historically; editable)"))
        self.crews = QTableWidget(0, 2)
        self.crews.setHorizontalHeaderLabels(["Field", "Crews"])
        self.crews.horizontalHeader().setStretchLastSection(True)
        self.crews.verticalHeader().setVisible(False)
        ll.addWidget(self.crews, 1)
        btns = QHBoxLayout()
        self.b_solve = QPushButton("Solve")
        self.b_solve.clicked.connect(self.solve)
        btns.addWidget(self.b_solve)
        ll.addLayout(btns)
        more = QHBoxLayout()
        self.b_crew = QPushButton("Value of one more crew"); self.b_crew.setObjectName("secondary")
        self.b_sens = QPushButton("Effectiveness sensitivity"); self.b_sens.setObjectName("secondary")
        self.b_crew.clicked.connect(self.extra_crew)
        self.b_sens.clicked.connect(self.sensitivity)
        more.addWidget(self.b_crew); more.addWidget(self.b_sens)
        ll.addLayout(more)
        out = QHBoxLayout()
        self.b_send = QPushButton("Send plan to Canary")
        self.b_export = QPushButton("Export work orders"); self.b_export.setObjectName("secondary")
        self.b_send.clicked.connect(self.send)
        self.b_export.clicked.connect(self.export)
        out.addWidget(self.b_send); out.addWidget(self.b_export)
        ll.addLayout(out)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        self.summary = QLabel("Choose a day and press Solve.")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("font-size: 11pt;")
        rl.addWidget(self.summary)
        self.extra = muted("")
        rl.addWidget(self.extra)
        tabs = QTabWidget()
        self.grid = QTableWidget()
        self.grid.cellDoubleClicked.connect(self._grid_click)
        tabs.addTab(self.grid, "Schedule (crew x day)")
        self.table = DataTable({"visit_date": "Visit", "crew": "Crew", "uwi": "Well", "field": "Field", "risk": "Risk",
                                "level": "Level", "expected_loss_before": "Expected loss before",
                                "expected_loss_after": "After", "visit_cost": "Cost", "travel_hours": "Travel h"})
        self.table.doubleClicked.connect(lambda i: self.ctx.open_well(self.table.row_at(i)["uwi"]))
        tabs.addTab(self.table, "Table")
        self.sens = DataTable()
        tabs.addTab(self.sens, "Sensitivity")
        rl.addWidget(tabs, 1)
        split.addWidget(right)
        split.setSizes([330, 900])
        lay.addWidget(split, 1)
        self._buttons(False)

    def _buttons(self, have_plan: bool):
        online = self.ctx.services.online and not self.ctx.services.settings.offline
        self.b_send.setEnabled(have_plan and online)
        self.b_export.setEnabled(have_plan)

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
        self.load_defaults()

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
        self.extra.setText(f"From the records: Rod Truck job {inputs['proactive_hours']:.1f} h, "
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

    def solve(self):
        day = self._day()
        if day is None:
            return
        self.summary.setText("Solving…")
        self.b_solve.setEnabled(False)
        run_task(self.ctx.services.plan, day, self.solver.currentText(), **self._settings(),
                 on_done=self._solved, on_error=lambda e: (self.b_solve.setEnabled(True), self.ctx.error(e)))

    def _solved(self, res):
        self.res, self.res_day = res, self._day()
        self.b_solve.setEnabled(True)
        self.summary.setText(
            f"<b>Expected loss next 7 days:</b> {money(res['loss_no_action'])} with no action → "
            f"<b>{money(res['loss_with_plan'])}</b> with the plan "
            f"(<b>−{money(res['reduction'])}</b>, {pct(res['reduction_pct'])}).<br>"
            f"Plan: {res['visits']} visits costing {money(res['plan_cost'])}. Fleet incl. the other "
            f"{len(self.ctx.services.planner().day_scores(self.res_day)) - res['candidates']} wells: "
            f"{money(res['fleet_loss_no_action'])} → {money(res['fleet_loss_with_plan'])}.<br>"
            f"<span style='color:gray'>Solver {res['solver']}, {res['status']}, {res['solve_seconds']} s. "
            "Objective: minimise expected failure loss + visit cost for the coming week.</span>")
        plan = res["plan"]
        self.table.set_df(plan.drop(columns=["planning_day", "day"], errors="ignore") if len(plan) else plan)
        self._fill_grid(plan)
        self._buttons(len(plan) > 0)

    def _fill_grid(self, plan: pd.DataFrame):
        self.grid.clear()
        if plan.empty:
            self.grid.setRowCount(0)
            self.grid.setColumnCount(0)
            return
        crews = sorted(plan["crew"].unique())
        dates = sorted(plan["visit_date"].unique())
        self.grid.setRowCount(len(crews))
        self.grid.setColumnCount(len(dates))
        self.grid.setVerticalHeaderLabels(crews)
        self.grid.setHorizontalHeaderLabels([f"{pd.Timestamp(d):%a %d %b}" for d in dates])
        self._cells = {}
        for (crew, date), g in plan.groupby(["crew", "visit_date"]):
            r, c = crews.index(crew), dates.index(date)
            lines = [f"● {u}  {p:.0%}" for u, p in zip(g["uwi"], g["risk"])]
            item = QTableWidgetItem("\n".join(lines))
            lvl = int(g["level"].max())
            if lvl >= 2:
                item.setForeground(Qt.GlobalColor.white)
                item.setBackground(pd_color(LEVEL_COLORS[lvl]))
            item.setToolTip("Double-click to open the first well\n" + "\n".join(f"{u} ({f})" for u, f in zip(g["uwi"], g["field"])))
            self.grid.setItem(r, c, item)
            self._cells[(r, c)] = list(g["uwi"])
        self.grid.resizeColumnsToContents()
        self.grid.resizeRowsToContents()

    def _grid_click(self, r, c):
        wells = getattr(self, "_cells", {}).get((r, c))
        if wells:
            self.ctx.open_well(wells[0])

    def extra_crew(self):
        day = self._day()
        self.extra.setText("Re-solving with one more crew per field…")
        run_task(self.ctx.services.extra_crew_value, day, self.solver.currentText(), **self._settings(),
                 on_done=lambda r: self.extra.setText(
                     f"One more crew per field would lower this week's expected loss by "
                     f"<b>{money(r['value_of_extra_crew_cad'])}</b> ({money(r['loss_with_plan'])} → "
                     f"{money(r['loss_with_extra_crew'])})."), on_error=self.ctx.error)

    def sensitivity(self):
        day = self._day()
        kw = {k: v for k, v in self._settings().items() if k != "effectiveness"}
        run_task(self.ctx.services.sensitivity, day, self.solver.currentText(), **kw,
                 on_done=self.sens.set_df, on_error=self.ctx.error)

    def send(self):
        if not self.res:
            return
        if QMessageBox.question(self, "Send plan", f"Write {self.res['visits']} planned visits to Canary "
                                                   "(PlannedVisitDay / PlannedCrew tags)?") != QMessageBox.Yes:
            return
        run_task(self.ctx.services.send_plan, self.res_day, self.res,
                 on_done=lambda m: (self.ctx.status(m), self.extra.setText(m)), on_error=self.ctx.error)

    def export(self):
        if not self.res:
            return
        folder = QFileDialog.getExistingDirectory(self, "Export work orders to")
        if not folder:
            return
        run_task(self.ctx.services.export_work_orders, self.res_day, self.res, folder,
                 on_done=lambda paths: self.ctx.status(f"Exported {len(paths)} files to {folder}"), on_error=self.ctx.error)


def pd_color(hex_color: str):
    from PySide6.QtGui import QColor
    return QColor(hex_color)
