"""Alert inbox (ISA-18.2 style): filter alerts, acknowledge / shelve / close them, see the audit trail.

Actions are saved in the alert database and mirrored to Canary (WellWatch.<Field>.<UWI>.AlertState).
"""

from __future__ import annotations

import pandas as pd
from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QSplitter, QVBoxLayout, QWidget)

from ..theme import LEVEL_COLORS
from ..widgets import DataTable, muted, title
from ..workers import run_task

COLUMNS = ["id", "max_level", "uwi", "field", "opened_day", "last_seen_day", "last_risk", "expected_loss", "driver",
           "state", "outcome", "lead_days"]
HEADERS = {"id": "#", "max_level": "Priority", "uwi": "Well", "field": "Field", "opened_day": "Opened",
           "last_seen_day": "Last seen", "last_risk": "Risk", "expected_loss": "Expected loss", "driver": "Why flagged",
           "state": "State", "outcome": "Outcome", "lead_days": "Lead days"}


class ActionDialog(QDialog):
    def __init__(self, parent, action: str, operator: str):
        super().__init__(parent)
        self.setWindowTitle({"acknowledge": "Acknowledge alert", "shelve": "Shelve alert", "close": "Close alert"}[action])
        form = QFormLayout(self)
        self.operator = QLineEdit(operator)
        form.addRow("Operator name *", self.operator)
        self.note = QLineEdit()
        form.addRow("Reason *" if action == "shelve" else "Note", self.note)
        self.until = self.outcome = None
        if action == "shelve":
            self.until = QDateEdit(QDate.currentDate().addDays(3))
            self.until.setCalendarPopup(True)
            form.addRow("Shelve until", self.until)
        if action == "close":
            self.outcome = QComboBox()
            self.outcome.addItems(["Fixed", "False alarm", "Other"])
            form.addRow("Outcome", self.outcome)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        self.action = action

    def _ok(self):
        if not self.operator.text().strip() or (self.action == "shelve" and not self.note.text().strip()):
            self.note.setPlaceholderText("required")
            return
        self.accept()


class InboxScreen(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        lay = QVBoxLayout(self)
        head = QHBoxLayout()
        head.addWidget(title("Alert inbox"))
        head.addStretch()
        self.badges = QLabel()
        head.addWidget(self.badges)
        lay.addLayout(head)
        lay.addWidget(muted("Warnings (level 2) and critical alerts (level 3) open an alert. It closes when the well "
                            "fails (\"Failure occurred\"), after 7 days below warning (\"Cleared\"), or by an operator."))

        filt = QHBoxLayout()
        self.open_only = QCheckBox("Open only")
        self.open_only.setChecked(True)
        self.field = QComboBox()
        self.field.addItem("All fields", None)
        self.min_level = QComboBox()
        for txt, val in [("Any level", None), ("Warning +", 2), ("Critical", 3)]:
            self.min_level.addItem(txt, val)
        for w in (self.open_only, self.field, self.min_level):
            (w.stateChanged if isinstance(w, QCheckBox) else w.currentIndexChanged).connect(lambda *_: self.refresh())
            filt.addWidget(w)
        filt.addStretch()
        self.b_ack = QPushButton("Acknowledge")
        self.b_shelve = QPushButton("Shelve until…")
        self.b_close = QPushButton("Close…")
        for b, a in [(self.b_ack, "acknowledge"), (self.b_shelve, "shelve"), (self.b_close, "close")]:
            b.clicked.connect(lambda _=False, a=a: self.act(a))
            b.setEnabled(False)
            filt.addWidget(b)
        lay.addLayout(filt)

        split = QSplitter(Qt.Vertical)
        self.table = DataTable(HEADERS)
        self.table.doubleClicked.connect(lambda i: self.ctx.open_well(self.table.row_at(i)["uwi"]))
        self.table.clicked.connect(lambda _: self._selected())
        split.addWidget(self.table)
        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addWidget(QLabel("<b>Audit trail</b> of the selected alert"))
        self.events = DataTable({"at": "When", "day": "Data day", "actor": "Who", "action": "Action", "detail": "Detail"})
        bl.addWidget(self.events)
        split.addWidget(bottom)
        split.setSizes([480, 200])
        lay.addWidget(split, 1)
        self.msg = muted("")
        lay.addWidget(self.msg)
        self._fields_loaded = False

    def refresh(self):
        s = self.ctx.services
        run_task(lambda: (s.alerts(self.open_only.isChecked(), self.field.currentData(), self.min_level.currentData()),
                          s.alert_counts()), on_done=self._show, on_error=self.ctx.error)

    def _show(self, res):
        df, counts = res
        if not self._fields_loaded and len(df):
            self.field.blockSignals(True)
            for f in sorted(self.ctx.services.alerts(False)["field"].dropna().unique()):
                self.field.addItem(f, f)
            self.field.blockSignals(False)
            self._fields_loaded = True
        self.table.set_df(df[[c for c in COLUMNS if c in df]] if len(df) else pd.DataFrame(columns=COLUMNS))
        self.badges.setText("  ".join(f"<span style='color:{c}'><b>{counts.get(k, 0)}</b> {k}</span>" for k, c in
                                      [("Open", LEVEL_COLORS[2]), ("Acknowledged", "#1f6f8b"),
                                       ("Shelved", "#7f8c8d"), ("Closed", "#616e7c")]))
        if self.table.model_.rowCount() and not self.table.selectionModel().selectedRows():
            self.table.selectRow(0)  # show the audit trail of the top alert straight away
        self._selected()

    def select_alert(self, alert_id: int):
        for r in range(self.table.proxy.rowCount()):
            if self.table.row_at(self.table.proxy.index(r, 0)).get("id") == alert_id:
                self.table.selectRow(r)
                self._selected()
                return

    def _selected(self):
        row = self.table.selected_row()
        open_ = bool(row) and row.get("state") != "Closed"
        writable = not self.ctx.services.settings.offline
        for b in (self.b_ack, self.b_shelve, self.b_close):
            b.setEnabled(open_ and writable)
        if row:
            run_task(self.ctx.services.alert_events, int(row["id"]),
                     on_done=lambda ev: self.events.set_df(ev[["at", "day", "actor", "action", "detail"]] if len(ev) else ev),
                     on_error=self.ctx.error)

    def act(self, action: str):
        row = self.table.selected_row()
        if not row:
            return
        dlg = ActionDialog(self, action, self.ctx.services.settings.operator)
        if dlg.exec() != QDialog.Accepted:
            return
        user = dlg.operator.text().strip()
        self.ctx.services.settings.operator = user
        self.ctx.services.settings.save()
        until = dlg.until.date().toPython() if dlg.until else None
        outcome = dlg.outcome.currentText() if dlg.outcome else None
        run_task(self.ctx.services.act, int(row["id"]), action, user, dlg.note.text().strip(), until, outcome,
                 on_done=lambda m: (self.msg.setText(m), self.ctx.status(m), self.refresh()), on_error=self.ctx.error)
