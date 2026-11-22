"""Settings & health: Canary connection and token, theme, notifications, offline mode, the
model card and a data-quality check (sensors that stopped changing)."""

from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QPushButton, QSpinBox, QTextBrowser, QVBoxLayout, QWidget)

from ..widgets import DataTable, muted, title
from ..workers import run_task


class SettingsScreen(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        s = ctx.services.settings
        lay = QVBoxLayout(self)
        lay.addWidget(title("Settings & health"))
        row = QHBoxLayout()

        box = QGroupBox("Connection && app")
        f = QFormLayout(box)
        self.host = QLineEdit(s.canary_host)
        self.rport = QSpinBox(); self.rport.setRange(1, 65535); self.rport.setValue(s.read_port)
        self.wport = QSpinBox(); self.wport.setRange(1, 65535); self.wport.setValue(s.write_port)
        f.addRow("Canary host", self.host); f.addRow("Read API port", self.rport); f.addRow("Write API port", self.wport)
        self.token = QLabel()
        b_tok = QPushButton("Store token…"); b_tok.setObjectName("secondary")
        b_tok.clicked.connect(self.store_token)
        t = QHBoxLayout(); t.addWidget(self.token); t.addWidget(b_tok)
        f.addRow("API token", t)
        self.conn = QLabel("-"); self.conn.setWordWrap(True)
        b_test = QPushButton("Test connection")
        b_test.clicked.connect(self.test)
        f.addRow(b_test, self.conn)
        self.theme = QComboBox(); self.theme.addItems(["light", "dark"]); self.theme.setCurrentText(s.theme)
        f.addRow("Theme", self.theme)
        self.operator = QLineEdit(s.operator)
        f.addRow("Operator name", self.operator)
        self.notify = QCheckBox("Pop-up notifications for new warning/critical alerts"); self.notify.setChecked(s.notifications)
        self.tray = QCheckBox("Closing the window keeps WellWatch in the tray"); self.tray.setChecked(s.minimise_to_tray)
        self.offline = QCheckBox("Offline mode (no Canary reads or writes)"); self.offline.setChecked(s.offline)
        for w in (self.notify, self.tray, self.offline):
            f.addRow(w)
        b_save = QPushButton("Save settings")
        b_save.clicked.connect(self.save)
        f.addRow(b_save)
        row.addWidget(box, 1)

        box2 = QGroupBox("Model card")
        v = QVBoxLayout(box2)
        self.card = QTextBrowser()
        v.addWidget(self.card)
        row.addWidget(box2, 1)
        lay.addLayout(row, 3)

        box3 = QGroupBox("Data quality: running wells whose efficiency / torque / current readings did not change for 14 days "
                         "(possible stuck sensor or stale manual entry)")
        v3 = QVBoxLayout(box3)
        b_q = QPushButton("Check data quality"); b_q.setObjectName("secondary")
        b_q.clicked.connect(self.quality)
        v3.addWidget(b_q)
        self.q = DataTable({"uwi": "Well", "field": "Field", "signal": "Signal", "value": "Value",
                            "days_unchanged": "Days unchanged"})
        v3.addWidget(self.q)
        lay.addWidget(box3, 2)
        self.note = muted("")
        lay.addWidget(self.note)

    def refresh(self):
        self.token.setText("stored in Windows Credential Manager" if self.ctx.services.token_stored() else "not stored")
        run_task(self.ctx.services.model_card, on_done=self._card)

    def _card(self, m):
        if not m:
            self.card.setText("No model found (run python -m wellwatch.train).")
            return
        rows = "".join(f"<tr><td>{r['model']}</td><td>{r['ROC-AUC']:.3f}</td><td>{r['PR-AUC']:.3f}</td>"
                       f"<td>{list(r.values())[3]:.1%}</td></tr>" for r in m.get("test_scores", []))
        feats = ", ".join(list(m.get("top_features_gain", {}))[:10])
        sp = m.get("splits", {})
        self.card.setHtml(
            "<b>Week-ahead failure risk (XGBoost, calibrated)</b><br>"
            f"Predicts whether a running oil well has a crew-needing equipment failure in the next "
            f"{m.get('horizon_days', 7)} days.<br>Train {sp.get('train')}, calibrate {sp.get('calibrate')}, "
            f"test {sp.get('test')}.<br><br><table border=1 cellspacing=0 cellpadding=3>"
            "<tr><th>Test year</th><th>ROC-AUC</th><th>PR-AUC</th><th>Precision, top 15/day</th></tr>"
            f"{rows}</table><br><b>Strongest inputs:</b> {feats}<br><span style='color:gray'>Trained {m.get('trained', '')[:19]}</span>")

    def test(self):
        self.save(quiet=True)
        self.conn.setText("Testing…")
        run_task(self.ctx.services.test_connection, on_done=lambda r: (self.conn.setText(r[1]), self.ctx.connection_changed()),
                 on_error=self.ctx.error)

    def store_token(self):
        tok, ok = QInputDialog.getText(self, "Store Canary API token", "Paste the API token (it is saved to Windows "
                                       "Credential Manager and never shown):", QLineEdit.Password)
        if ok and tok.strip():
            try:
                self.ctx.services.store_token(tok)
                self.ctx.status("Token stored in Windows Credential Manager.")
            except Exception as exc:
                self.ctx.error(str(exc))
            self.refresh()

    def save(self, quiet: bool = False):
        s = self.ctx.services.settings
        s.canary_host, s.read_port, s.write_port = self.host.text().strip(), self.rport.value(), self.wport.value()
        s.theme, s.operator = self.theme.currentText(), self.operator.text().strip()
        s.notifications, s.minimise_to_tray, s.offline = self.notify.isChecked(), self.tray.isChecked(), self.offline.isChecked()
        s.save()
        self.ctx.apply_theme(s.theme)
        if not quiet:
            self.ctx.status("Settings saved.")
            self.ctx.connection_changed(retest=True)

    def quality(self):
        self.note.setText("Checking (loads the production records the first time; ~20 s)…")
        run_task(self.ctx.services.data_quality, on_done=lambda df: (self.q.set_df(df), self.note.setText(
            f"{len(df)} stuck readings on {df['uwi'].nunique() if len(df) else 0} wells.")), on_error=self.ctx.error)
