"""Alert lifecycle for WellWatch (SQLite database in cache/alerts.db).

ISA-18.2-style rules, applied once per scored day by the engine (AlertStore.process_day):
  * A well at AlertLevel >= 2 (warning or critical) with no open alert gets a new alert (state "Open").
    If it already has one, that alert is updated (last seen, highest level, latest risk and reason).
  * If the well starts equipment-failure downtime while its alert is open, the alert closes with
    outcome "Failure occurred" and lead_days = days from opening to the failure.
  * After 7 consecutive scored days below level 2 (and no failure), the alert closes as "Cleared".
  * Operators can acknowledge, shelve (until a date) or close alerts. Shelved alerts do not
    re-notify until shelved_until; then they return to Open.
Every state change is written to alert_events (who, when, what) for the audit trail.

The app uses: list_alerts(), acknowledge(), shelve(), close(), events(), daily_scores().
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import pandas as pd

from . import config

DB_FILE = config.REPO_ROOT / "cache" / "alerts.db"
WARNING_LEVEL = 2
CLEAR_AFTER_DAYS = 7
OPEN_STATES = ("Open", "Acknowledged", "Shelved")

SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uwi TEXT NOT NULL, field TEXT, opened_day TEXT NOT NULL, last_seen_day TEXT NOT NULL,
    max_level INTEGER NOT NULL, first_risk REAL, last_risk REAL, expected_loss REAL, driver TEXT,
    state TEXT NOT NULL DEFAULT 'Open', shelved_until TEXT, outcome TEXT, outcome_day TEXT,
    lead_days INTEGER, days_below INTEGER NOT NULL DEFAULT 0, notes TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_alerts_uwi_state ON alerts(uwi, state);
CREATE TABLE IF NOT EXISTS alert_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, alert_id INTEGER NOT NULL, at TEXT NOT NULL,
    day TEXT, actor TEXT NOT NULL, action TEXT NOT NULL, detail TEXT
);
CREATE TABLE IF NOT EXISTS daily_scores (
    day TEXT NOT NULL, uwi TEXT NOT NULL, field TEXT, risk REAL, level INTEGER, rank INTEGER,
    expected_loss REAL, driver TEXT, PRIMARY KEY (day, uwi)
);
"""


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


class AlertStore:
    def __init__(self, path: Path | str = DB_FILE):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ------------------------------------------------------------------ engine side
    def _event(self, alert_id: int, action: str, actor: str = "engine", day: str | None = None, detail: str = ""):
        self.db.execute("INSERT INTO alert_events(alert_id, at, day, actor, action, detail) VALUES (?,?,?,?,?,?)",
                        (alert_id, _now(), day, actor, action, detail))

    def process_day(self, day, scored: pd.DataFrame, failure_starts: set[str]) -> dict:
        """Apply the rules for one scored day.

        scored: one row per running well scored that day (uwi, field_operating_area, risk, level, rank,
        expected_loss, driver). failure_starts: wells whose equipment-failure downtime started that day.
        Returns counts and the alerts to notify about (new or escalated, not shelved).
        """
        day = pd.Timestamp(day).strftime("%Y-%m-%d")
        cols = ["uwi", "field_operating_area", "risk", "level", "rank", "expected_loss", "driver"]
        if scored is None or scored.empty:
            scored = pd.DataFrame(columns=cols)
        cur = self.db.cursor()
        counts = {"opened": 0, "updated": 0, "failed": 0, "cleared": 0, "unshelved": 0}
        notify = []

        # 1) shelving expires
        for a in cur.execute("SELECT id FROM alerts WHERE state='Shelved' AND shelved_until IS NOT NULL "
                             "AND shelved_until <= ?", (day,)).fetchall():
            cur.execute("UPDATE alerts SET state='Open', shelved_until=NULL, updated_at=? WHERE id=?", (_now(), a["id"]))
            self._event(a["id"], "unshelved", day=day)
            counts["unshelved"] += 1

        # 2) failures close open alerts
        if failure_starts:
            q = f"SELECT id, opened_day FROM alerts WHERE state IN {OPEN_STATES} AND uwi IN ({','.join('?' * len(failure_starts))})"
            for a in cur.execute(q, tuple(failure_starts)).fetchall():
                lead = (pd.Timestamp(day) - pd.Timestamp(a["opened_day"])).days
                cur.execute("UPDATE alerts SET state='Closed', outcome='Failure occurred', outcome_day=?, lead_days=?, "
                            "updated_at=? WHERE id=?", (day, lead, _now(), a["id"]))
                self._event(a["id"], "closed", day=day, detail=f"Failure occurred {lead} days after the alert opened")
                counts["failed"] += 1

        # 3) warnings open or update alerts
        warn = scored[scored["level"] >= WARNING_LEVEL]
        for r in warn.itertuples(index=False):
            a = cur.execute(f"SELECT id, max_level, state FROM alerts WHERE uwi=? AND state IN {OPEN_STATES}",
                            (r.uwi,)).fetchone()
            if a is None:
                cur.execute("INSERT INTO alerts(uwi, field, opened_day, last_seen_day, max_level, first_risk, last_risk, "
                            "expected_loss, driver, state, updated_at) VALUES (?,?,?,?,?,?,?,?,?, 'Open', ?)",
                            (r.uwi, r.field_operating_area, day, day, int(r.level), float(r.risk), float(r.risk),
                             float(r.expected_loss), r.driver, _now()))
                aid = cur.lastrowid
                self._event(aid, "opened", day=day, detail=f"level {int(r.level)}, risk {r.risk:.1%}: {r.driver}")
                counts["opened"] += 1
                notify.append(aid)
            else:
                escalated = int(r.level) > a["max_level"]
                cur.execute("UPDATE alerts SET last_seen_day=?, max_level=MAX(max_level, ?), last_risk=?, expected_loss=?, "
                            "driver=?, days_below=0, updated_at=? WHERE id=?",
                            (day, int(r.level), float(r.risk), float(r.expected_loss), r.driver, _now(), a["id"]))
                counts["updated"] += 1
                if escalated:
                    self._event(a["id"], "escalated", day=day, detail=f"level {a['max_level']} -> {int(r.level)}")
                    if a["state"] != "Shelved":
                        notify.append(a["id"])

        # 4) days below the warning level (only days the well was scored, i.e. running)
        below = set(scored.loc[scored["level"] < WARNING_LEVEL, "uwi"])
        if below:
            q = f"SELECT id, days_below FROM alerts WHERE state IN {OPEN_STATES} AND uwi IN ({','.join('?' * len(below))})"
            for a in cur.execute(q, tuple(below)).fetchall():
                n = a["days_below"] + 1
                if n >= CLEAR_AFTER_DAYS:
                    cur.execute("UPDATE alerts SET state='Closed', outcome='Cleared', outcome_day=?, days_below=?, "
                                "updated_at=? WHERE id=?", (day, n, _now(), a["id"]))
                    self._event(a["id"], "closed", day=day, detail=f"Cleared after {n} days below warning level")
                    counts["cleared"] += 1
                else:
                    cur.execute("UPDATE alerts SET days_below=? WHERE id=?", (n, a["id"]))

        # 5) keep every running well's score for the scoreboard and the app
        cur.executemany("INSERT OR REPLACE INTO daily_scores(day, uwi, field, risk, level, rank, expected_loss, driver) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        [(day, r.uwi, r.field_operating_area, float(r.risk), int(r.level), int(r.rank),
                          float(r.expected_loss), r.driver) for r in scored.itertuples(index=False)])
        self.db.commit()
        counts["notify"] = notify
        counts["open_alerts"] = cur.execute(f"SELECT COUNT(*) FROM alerts WHERE state IN {OPEN_STATES}").fetchone()[0]
        return counts

    def states_for(self, uwis) -> dict[str, str]:
        """Current alert state per well ('None' if the well has no open alert)."""
        out = {u: "None" for u in uwis}
        for r in self.db.execute(f"SELECT uwi, state FROM alerts WHERE state IN {OPEN_STATES}"):
            if r["uwi"] in out:
                out[r["uwi"]] = r["state"]
        return out

    # ------------------------------------------------------------------ operator side (used by the app)
    def list_alerts(self, open_only: bool = False, field: str | None = None, min_level: int | None = None,
                    state: str | None = None) -> pd.DataFrame:
        q, args = "SELECT * FROM alerts WHERE 1=1", []
        if open_only:
            q += f" AND state IN {OPEN_STATES}"
        if field:
            q += " AND field=?"; args.append(field)
        if min_level is not None:
            q += " AND max_level>=?"; args.append(min_level)
        if state:
            q += " AND state=?"; args.append(state)
        q += " ORDER BY (state='Closed'), max_level DESC, expected_loss DESC"
        return pd.read_sql_query(q, self.db, params=args)

    def _operator(self, alert_id: int, state: str, action: str, user: str, note: str, **extra):
        sets = ", ".join(f"{k}=?" for k in extra)
        self.db.execute(f"UPDATE alerts SET state=?, updated_at=?, notes=COALESCE(notes || char(10), '') || ?"
                        f"{', ' + sets if sets else ''} WHERE id=?",
                        (state, _now(), f"[{user}] {note}" if note else "", *extra.values(), alert_id))
        self._event(alert_id, action, actor=user, detail=note)
        self.db.commit()

    def acknowledge(self, alert_id: int, user: str, note: str = "") -> None:
        self._operator(alert_id, "Acknowledged", "acknowledged", user, note)

    def shelve(self, alert_id: int, until, user: str, note: str = "") -> None:
        self._operator(alert_id, "Shelved", "shelved", user, note,
                       shelved_until=pd.Timestamp(until).strftime("%Y-%m-%d"))

    def close(self, alert_id: int, user: str, note: str = "", outcome: str = "Closed by operator") -> None:
        self._operator(alert_id, "Closed", "closed", user, note, outcome=outcome)

    def events(self, alert_id: int | None = None) -> pd.DataFrame:
        if alert_id is None:
            return pd.read_sql_query("SELECT * FROM alert_events ORDER BY id", self.db)
        return pd.read_sql_query("SELECT * FROM alert_events WHERE alert_id=? ORDER BY id", self.db, params=[alert_id])

    def daily_scores(self) -> pd.DataFrame:
        return pd.read_sql_query("SELECT * FROM daily_scores", self.db, parse_dates=["day"])

    def reset(self) -> None:
        """Delete all alerts, events and scores (used before a fresh replay)."""
        self.db.executescript("DELETE FROM alerts; DELETE FROM alert_events; DELETE FROM daily_scores;")
        self.db.commit()
