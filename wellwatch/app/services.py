"""Service layer for the desktop app: every data access the screens need, with no Qt dependency.

Sources
  * cache/alerts.db (AlertStore): daily scores of every running well, alerts and their audit trail.
    The fleet overview, inbox and planner read from here, so the UI never makes fleet-wide Canary reads.
  * Canary Read API: one well's trends (~20 tags) and a few WellWatch.Fleet.* tags, cached for 60 s.
  * Canary Write API: short-lived sessions to mirror an operator's alert action (AlertState) and to
    send a crew plan. Refuses when offline.
  * Production records (wellwatch.data): the well register (location, pump, dates) and, in offline
    mode, the well trends.
  * Engine processes: started/stopped through `python -m wellwatch.engine ...`, guarded by the lock file.

All methods are safe to call from worker threads.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import signal
import subprocess
import sys
import threading
import time

import numpy as np
import pandas as pd

from .. import backup, config, lock
from ..alerts import OPEN_STATES, AlertStore
from .settings import AppSettings

STAMP = pd.Timedelta(hours=config.DAILY_STAMP_UTC_HOUR)
REGISTRY_FILE = config.REPO_ROOT / "cache" / "well_registry.parquet"
LOG_FILE = config.REPO_ROOT / "logs" / "wellwatch.log"
OUTPUTS = config.REPO_ROOT / "outputs"
TREND_SIGNALS = ["OilRate", "WaterRate", "PumpEfficiency", "RodTorque", "MotorCurrent", "PumpSpeed",
                 "TubingPressure", "CasingPressure", "HoursDown", "EquipmentDownHours"]
MODEL_SERIES = ["FailureRisk7d", "ExpectedLoss7d", "AlertLevel"]
TEXT_TAGS_RAW = ["Job", "Failure", "DowntimeReason"]
TEXT_TAGS_MODEL = ["TopDriver", "AlertState", "PlannedVisitDay", "PlannedCrew"]
LEVEL_NAMES = {3: "Critical", 2: "Warning", 1: "Watch", 0: "None"}
# Readings that should move day to day on a running well. Pump speed and pressures are set-points or
# manual entries that legitimately stay constant, so they are not checked.
QUALITY_COLUMNS = {"pump_efficiency_pct": "PumpEfficiency", "pcp_pump_rod_torque_ftlb": "RodTorque",
                   "pcp_pump_electric_current_a": "MotorCurrent"}


class OfflineError(RuntimeError):
    pass


def _day_from_stamp(t) -> pd.Timestamp:
    """Canary timestamp (local offset) -> production date (values are stamped at 12:00 UTC)."""
    return (pd.Timestamp(t).tz_convert("UTC") - STAMP).tz_localize(None).normalize()


class Services:
    def __init__(self, settings: AppSettings | None = None, reader_factory=None, writer_factory=None,
                 store: AlertStore | None = None):
        self.settings = settings or AppSettings.load()
        self._reader_factory = reader_factory
        self._writer_factory = writer_factory
        self.store = store or AlertStore()
        self.online = False
        self._cache: dict = {}
        self._lock = threading.RLock()
        self._registry = self._jobs = self._fails = self._d = self._planner = None

    # ------------------------------------------------------------------ helpers
    def _cached(self, key, fn, ttl: float = 60.0):
        with self._lock:
            hit = self._cache.get(key)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
        value = fn()
        with self._lock:
            self._cache[key] = (time.time(), value)
        return value

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()

    def _reader(self):
        if self.settings.offline:
            raise OfflineError("Offline mode is on")
        if self._reader_factory:
            return self._reader_factory()
        from ..canary import CanaryReader
        return CanaryReader()

    def _writer(self):
        if self.settings.offline or not self.online:
            raise OfflineError("Canary is offline: changes are saved locally only")
        if self._writer_factory:
            return self._writer_factory()
        from ..canary import CanaryWriter
        return CanaryWriter()

    def _sql(self, query: str, params=()) -> pd.DataFrame:
        return pd.read_sql_query(query, self.store.db, params=list(params))

    # ------------------------------------------------------------------ connection
    @staticmethod
    def token_stored() -> bool:
        import keyring
        try:
            return bool(keyring.get_password(config.KEYRING_SERVICE, config.KEYRING_USER))
        except Exception:
            return False

    @staticmethod
    def store_token(token: str) -> None:
        import keyring
        token = token.strip()
        if not token:
            raise ValueError("Empty token")
        keyring.set_password(config.KEYRING_SERVICE, config.KEYRING_USER, token)

    def test_connection(self) -> tuple[bool, str]:
        """Read one fleet tag. Sets self.online. The message never contains the token."""
        if self.settings.offline:
            self.online = False
            return False, "Offline mode is on (Settings)"
        try:
            r = self._reader()
            r.get_tag_data([f"localhost.{config.FLEET_PATH}.LastScoredDay"], "2021-01-01T00:00:00Z",
                           "2030-01-01T00:00:00Z", max_size=10)
            self.online = True
            return True, f"Connected to Canary on {self.settings.canary_host}"
        except Exception as exc:
            self.online = False
            return False, f"Canary not reachable: {type(exc).__name__}: {str(exc)[:200]}"

    # ------------------------------------------------------------------ days, register
    def scored_days(self) -> list[pd.Timestamp]:
        d = self._sql("SELECT DISTINCT day FROM daily_scores ORDER BY day")
        return [pd.Timestamp(x) for x in d["day"]]

    def last_scored_day(self) -> pd.Timestamp | None:
        d = self._sql("SELECT MAX(day) AS d FROM daily_scores")
        return pd.Timestamp(d["d"].iloc[0]) if len(d) and d["d"].iloc[0] else None

    def production(self) -> pd.DataFrame:
        if self._d is None:
            from .. import data
            self._d = data.oil_producers()
        return self._d

    def jobs(self) -> pd.DataFrame:
        if self._jobs is None:
            from .. import data
            self._jobs = data.jobs()
        return self._jobs

    def failures(self) -> pd.DataFrame:
        if self._fails is None:
            from .. import data
            self._fails = data.failures()
        return self._fails

    def registry(self) -> pd.DataFrame:
        """One row per well: field, facility, location, pump, first/last production date."""
        if self._registry is not None:
            return self._registry
        if REGISTRY_FILE.exists():
            self._registry = pd.read_parquet(REGISTRY_FILE)
            return self._registry
        d = self.production().sort_values("production_date")
        g = d.groupby("uwi", observed=True)
        reg = g[["field_operating_area", "facility_name", "surface_latitude", "surface_longitude",
                 "pcp_pump_make", "pcp_pump_model", "pcp_pump_displacement_m3d100rpm", "on_production_date"]].last()
        reg["first_day"] = g["production_date"].min()
        reg["last_day"] = g["production_date"].max()
        reg = reg.reset_index().rename(columns={"field_operating_area": "field", "surface_latitude": "lat",
                                                "surface_longitude": "lon"})
        for c in ["field", "facility_name", "pcp_pump_make", "pcp_pump_model"]:
            reg[c] = reg[c].astype("string")
        REGISTRY_FILE.parent.mkdir(exist_ok=True)
        reg.to_parquet(REGISTRY_FILE)
        self._registry = reg
        return reg

    # ------------------------------------------------------------------ overview
    def overview(self, day=None) -> dict:
        day = pd.Timestamp(day) if day is not None else self.last_scored_day()
        if day is None:
            return {"day": None, "kpis": {}, "top": pd.DataFrame(), "fields": pd.DataFrame(), "map": pd.DataFrame()}
        ds = day.strftime("%Y-%m-%d")
        scores = self._sql("SELECT * FROM daily_scores WHERE day=?", (ds,))
        open_al = self._sql(f"SELECT uwi, field, max_level, expected_loss FROM alerts WHERE state IN {OPEN_STATES} "
                            "AND opened_day <= ?", (ds,))
        kpis = {
            "day": day, "wells_scored": int(len(scores)),
            "critical": int((open_al["max_level"] >= 3).sum()), "warning": int((open_al["max_level"] == 2).sum()),
            "watch": int((scores["level"] == 1).sum()), "expected_loss": float(scores["expected_loss"].sum()),
            "last_data_day": self.progress().get("last_written_day"), "last_scored_day": self.last_scored_day(),
            "engine": lock.read_lock(),
        }
        top = scores.sort_values("rank").head(15)[["rank", "uwi", "field", "risk", "expected_loss", "level", "driver"]]
        fields = scores.groupby("field").agg(wells=("uwi", "size"), expected_loss=("expected_loss", "sum"))
        fields["open_alerts"] = open_al.groupby("field").size().reindex(fields.index).fillna(0).astype(int)
        fields = fields.sort_values("expected_loss", ascending=False).reset_index()
        reg = self.registry()[["uwi", "lat", "lon"]]
        m = scores.merge(reg, on="uwi", how="left").dropna(subset=["lat", "lon"])
        return {"day": day, "kpis": kpis, "top": top.reset_index(drop=True), "fields": fields, "map": m}

    # ------------------------------------------------------------------ alerts
    def alerts(self, open_only: bool = True, field: str | None = None, min_level: int | None = None) -> pd.DataFrame:
        return self.store.list_alerts(open_only=open_only, field=field or None, min_level=min_level)

    def alert_counts(self) -> dict:
        d = self._sql("SELECT state, COUNT(*) AS n FROM alerts GROUP BY state")
        return dict(zip(d["state"], d["n"]))

    def alert_events(self, alert_id: int) -> pd.DataFrame:
        return self.store.events(alert_id)

    def act(self, alert_id: int, action: str, user: str, note: str = "", until=None, outcome: str | None = None) -> str:
        """Apply an operator action, then mirror the alert state to Canary. Returns a status message."""
        if not user.strip():
            raise ValueError("Operator name is required")
        row = self._sql("SELECT uwi, field FROM alerts WHERE id=?", (alert_id,))
        if row.empty:
            raise ValueError(f"Alert {alert_id} not found")
        if action == "acknowledge":
            self.store.acknowledge(alert_id, user, note)
        elif action == "shelve":
            if until is None or not note.strip():
                raise ValueError("Shelving needs a date and a reason")
            self.store.shelve(alert_id, until, user, note)
        elif action == "close":
            self.store.close(alert_id, user, note, outcome=f"Closed by operator: {outcome or 'Other'}")
        else:
            raise ValueError(f"Unknown action {action}")
        uwi, field = row["uwi"].iloc[0], row["field"].iloc[0]
        state = self.store.states_for([uwi])[uwi]
        return "Saved. " + self.mirror_state(uwi, field, state)

    def mirror_state(self, uwi: str, field: str, state: str) -> str:
        tag = config.tag_path(field, uwi, "AlertState", dataset=config.DATASET_MODEL)
        now = pd.Timestamp.now(tz="UTC").floor("s")
        try:
            with self._writer() as w:
                w.store({tag: [(now, state)]})
            return f"Canary AlertState = {state}."
        except OfflineError as exc:
            return str(exc) + "."
        except Exception as exc:
            if "already being logged" in str(exc):
                return ("Canary is busy: the engine is writing this tag right now. The state is saved and "
                        "the engine mirrors it on its next scored day.")
            return f"Canary write failed: {str(exc)[:160]}"

    # ------------------------------------------------------------------ well detail
    def well_header(self, uwi: str, day=None) -> dict:
        day = pd.Timestamp(day) if day is not None else self.last_scored_day()
        reg = self.registry().set_index("uwi")
        info = reg.loc[uwi].to_dict() if uwi in reg.index else {}
        jobs = self.jobs()
        rep = jobs[(jobs["uwi"] == uwi) & jobs["job_category"].isin(["Rod Truck", "Workover"])
                   & (jobs["start_date"] <= day)]
        last_rep = rep["start_date"].max() if len(rep) else None
        last_job = jobs[(jobs["uwi"] == uwi) & (jobs["start_date"] <= day)].sort_values("start_date").tail(1)
        score = self._sql("SELECT * FROM daily_scores WHERE uwi=? AND day<=? ORDER BY day DESC LIMIT 1",
                          (uwi, day.strftime("%Y-%m-%d")))
        alert = self._sql(f"SELECT id, state, max_level, opened_day FROM alerts WHERE uwi=? AND state IN {OPEN_STATES}", (uwi,))
        plan = self._planned_visit(uwi, info.get("field"))
        age = (day - pd.Timestamp(info["on_production_date"])).days / 365.25 if info.get("on_production_date") is not None and pd.notna(info.get("on_production_date")) else None
        return {
            "uwi": uwi, "day": day, "field": info.get("field"), "facility": info.get("facility_name"),
            "pump": " ".join(str(x) for x in [info.get("pcp_pump_make"), info.get("pcp_pump_model")] if x and str(x) != "<NA>") or "No PCP record",
            "pump_size": info.get("pcp_pump_displacement_m3d100rpm"), "well_age_years": age,
            "days_since_repair": (day - last_rep).days if last_rep is not None and pd.notna(last_rep) else None,
            "last_job": (f"{last_job['start_date'].iloc[0]:%Y-%m-%d} {last_job['job_category'].iloc[0]} / "
                         f"{last_job['job_type'].iloc[0]}" if len(last_job) else None),
            "last_job_summary": (str(last_job["summary"].iloc[0])[:400] if len(last_job) and pd.notna(last_job["summary"].iloc[0]) else ""),
            "risk": float(score["risk"].iloc[0]) if len(score) else None,
            "level": int(score["level"].iloc[0]) if len(score) else 0,
            "scored_day": score["day"].iloc[0] if len(score) else None,
            "driver": score["driver"].iloc[0] if len(score) else "",
            "expected_loss": float(score["expected_loss"].iloc[0]) if len(score) else None,
            "alert": alert.iloc[0].to_dict() if len(alert) else None,
            "planned_visit": plan,
            "lat": info.get("lat"), "lon": info.get("lon"),
        }

    def _planned_visit(self, uwi: str, field: str | None) -> str | None:
        if not field or not self.online:
            return None
        try:
            tags = [f"localhost.{config.tag_path(field, uwi, t, dataset=config.DATASET_MODEL)}"
                    for t in ("PlannedVisitDay", "PlannedCrew")]
            res = self._cached(("plan", uwi), lambda: self._reader().get_tag_data(
                tags, "2021-01-01T00:00:00Z", "2030-01-01T00:00:00Z", max_size=1000))
            day = res[tags[0]][-1][1] if res.get(tags[0]) else None
            crew = res[tags[1]][-1][1] if res.get(tags[1]) else None
            return f"{day} ({crew})" if day else None
        except Exception:
            return None

    def well_trends(self, uwi: str, field: str, end_day, days: int = 90) -> dict:
        """Daily series + events for one well. Canary when online, production records otherwise."""
        end_day = pd.Timestamp(end_day)
        start = end_day - pd.Timedelta(days=days)
        key = ("trend", uwi, end_day, days, self.online)
        return self._cached(key, lambda: self._trends_canary(uwi, field, start, end_day) if self.online
                            else self._trends_files(uwi, start, end_day))

    def _trends_canary(self, uwi, field, start, end) -> dict:
        r = self._reader()
        t0 = (start.tz_localize("UTC") + STAMP).isoformat()
        t1 = (end.tz_localize("UTC") + STAMP + pd.Timedelta(days=1)).isoformat()
        num = {f"localhost.{config.tag_path(field, uwi, s)}": s for s in TREND_SIGNALS}
        num.update({f"localhost.{config.tag_path(field, uwi, s, dataset=config.DATASET_MODEL)}": s for s in MODEL_SERIES})
        res = r.get_tag_data(list(num), t0, t1, aggregate="TimeAverage2", interval="1d", max_size=20_000)
        rows = [(num[tag], _day_from_stamp(t), v) for tag, pts in res.items() for t, v in pts]
        daily = (pd.DataFrame(rows, columns=["signal", "day", "v"]).pivot_table(index="day", columns="signal", values="v")
                 if rows else pd.DataFrame())
        # model outputs exist only on scored days; TimeAverage2 carries them forward, so blank non-scored days
        scored = set(pd.to_datetime(self._sql("SELECT day FROM daily_scores WHERE uwi=?", (uwi,))["day"]))
        for s in MODEL_SERIES:
            if s in daily:
                daily.loc[~daily.index.isin(scored), s] = np.nan
        text = {f"localhost.{config.tag_path(field, uwi, s)}": s for s in TEXT_TAGS_RAW}
        tres = r.get_tag_data(list(text), t0, t1, max_size=20_000)
        events = []
        for tag, pts in tres.items():
            kind = text[tag]
            for t, v in pts:
                if kind == "DowntimeReason" and v == "Running":
                    continue
                events.append({"day": _day_from_stamp(t), "kind": "Downtime" if kind == "DowntimeReason" else kind,
                               "text": str(v)})
        return self._finish_trends(daily, events, uwi, start, end, source="Canary")

    def _trends_files(self, uwi, start, end) -> dict:
        d = self.production()
        w = d[(d["uwi"] == uwi) & d["production_date"].between(start, end)].set_index("production_date")
        mapping = {"OilRate": "oil_produced_m3", "WaterRate": "water_produced_m3", "PumpEfficiency": "pump_efficiency_pct",
                   "RodTorque": "pcp_pump_rod_torque_ftlb", "MotorCurrent": "pcp_pump_electric_current_a",
                   "PumpSpeed": "pcp_pump_rpm", "TubingPressure": "tubing_pressure_kpa",
                   "CasingPressure": "casing_pressure_kpa", "HoursDown": "hours_down", "EquipmentDownHours": "eq_down_hours"}
        daily = pd.DataFrame({k: w[v] for k, v in mapping.items() if v in w}, index=w.index)
        sc = self._sql("SELECT day, risk, expected_loss, level FROM daily_scores WHERE uwi=?", (uwi,))
        if len(sc):
            sc = sc.assign(day=pd.to_datetime(sc["day"])).set_index("day")
            daily = daily.join(sc.rename(columns={"risk": "FailureRisk7d", "expected_loss": "ExpectedLoss7d",
                                                  "level": "AlertLevel"}), how="outer")
        events = [{"day": r.production_date, "kind": "Downtime", "text": str(r.problem)}
                  for r in d[(d["uwi"] == uwi) & d["production_date"].between(start, end) & (d["hours_down"] > 0)].itertuples()]
        j = self.jobs()
        events += [{"day": r.start_date.normalize(), "kind": "Job", "text": f"{r.job_category} / {r.job_type}"}
                   for r in j[(j["uwi"] == uwi) & j["start_date"].between(start, end)].itertuples()]
        f = self.failures()
        events += [{"day": r.failure_start_date.normalize(), "kind": "Failure", "text": f"{r.failure_type_std} / {r.cause_std}"}
                   for r in f[(f["uwi"] == uwi) & f["failure_start_date"].between(start, end)].itertuples()]
        return self._finish_trends(daily, events, uwi, start, end, source="offline (from files)")

    def _finish_trends(self, daily, events, uwi, start, end, source) -> dict:
        daily = daily.sort_index()
        daily = daily[(daily.index >= start) & (daily.index <= end)] if len(daily) else daily
        if {"OilRate", "WaterRate"} <= set(daily.columns):
            liquid = daily["OilRate"] + daily["WaterRate"]
            daily["WaterCut"] = (daily["WaterRate"] / liquid).where(liquid > 0)
        for a in self._sql("SELECT id FROM alerts WHERE uwi=?", (uwi,))["id"]:
            for e in self.store.events(int(a)).itertuples():
                if e.action in ("opened", "closed") and e.day:
                    ed = pd.Timestamp(e.day)
                    if start <= ed <= end:
                        events.append({"day": ed, "kind": f"Alert {e.action}", "text": e.detail or ""})
        return {"daily": daily, "events": sorted(events, key=lambda e: e["day"]), "source": source}

    # ------------------------------------------------------------------ planner
    def planner(self):
        if self._planner is None:
            from ..plan import Planner
            self._planner = Planner()
        else:
            self._planner.scores = self.store.daily_scores()
        return self._planner

    def default_planning_day(self) -> pd.Timestamp | None:
        days = self.scored_days()
        mondays = [d for d in days if d.weekday() == 0]
        return mondays[-1] if mondays else (days[-1] if days else None)

    def plan_defaults(self, day) -> tuple:
        return self.planner().settings(pd.Timestamp(day))

    def plan(self, day, solver: str = "auto", **settings) -> dict:
        res, _ = self.planner().plan(pd.Timestamp(day), solver, **settings)
        return res

    def extra_crew_value(self, day, solver="auto", **settings) -> dict:
        from ..planner import marginal_crew_value
        pl = self.planner()
        s, _ = pl.settings(pd.Timestamp(day), **settings)
        return marginal_crew_value(pl.day_scores(pd.Timestamp(day)), s, solver=solver)

    def sensitivity(self, day, solver="auto", **settings) -> pd.DataFrame:
        rows = []
        for e in (0.25, 0.5, 0.75):
            r = self.plan(day, solver, **{**settings, "effectiveness": e})
            rows.append({"effectiveness": e, "visits": r["visits"], "loss_no_action": r["loss_no_action"],
                         "loss_with_plan": r["loss_with_plan"], "reduction_pct": r["reduction_pct"],
                         "plan_cost": r["plan_cost"]})
        return pd.DataFrame(rows)

    def send_plan(self, day, res: dict) -> str:
        if self.settings.offline or not self.online:
            raise OfflineError("Canary is offline: the plan cannot be sent")
        from ..plan import write_to_canary
        try:
            write_to_canary(pd.Timestamp(day), res)
            return f"Sent {res['visits']} planned visits to Canary."
        except Exception as exc:
            if "already being logged" in str(exc):
                return "Canary is busy (the engine is writing these tags). Try again in a minute."
            raise

    def export_work_orders(self, day, res: dict, folder) -> list:
        """CSV of the plan plus one printable HTML work order per crew."""
        from pathlib import Path
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        plan = res["plan"].copy()
        day = pd.Timestamp(day)
        paths = [folder / f"plan_{day:%Y-%m-%d}.csv"]
        plan.to_csv(paths[0], index=False)
        scores = self._sql("SELECT uwi, driver FROM daily_scores WHERE day=?", (day.strftime("%Y-%m-%d"),))
        drivers = dict(zip(scores["uwi"], scores["driver"]))
        jobs = self.jobs()
        for crew, g in plan.groupby("crew"):
            rows = []
            for r in g.sort_values(["day", "risk"], ascending=[True, False]).itertuples():
                lj = jobs[(jobs["uwi"] == r.uwi) & (jobs["start_date"] <= day)].sort_values("start_date").tail(1)
                note = (f"{lj['start_date'].iloc[0]:%Y-%m-%d} {lj['job_type'].iloc[0]}: "
                        f"{str(lj['summary'].iloc[0])[:220] if pd.notna(lj['summary'].iloc[0]) else ''}") if len(lj) else ""
                rows.append(f"<tr><td>{pd.Timestamp(r.visit_date):%a %d %b}</td><td>{r.uwi}</td><td>{r.field}</td>"
                            f"<td>{r.risk:.1%}</td><td>{drivers.get(r.uwi, '')}</td><td>{note}</td></tr>")
            html = (f"<html><head><meta charset='utf-8'><title>Work orders {crew}</title><style>"
                    "body{font-family:Segoe UI,Arial;margin:24px} table{border-collapse:collapse;width:100%}"
                    "td,th{border:1px solid #999;padding:6px;font-size:12px;vertical-align:top} th{background:#eee}"
                    "</style></head><body>"
                    f"<h2>WellWatch work orders: {crew}</h2><p>Planned on {day:%Y-%m-%d} for the following 7 days.</p>"
                    "<table><tr><th>Date</th><th>Well</th><th>Field</th><th>7-day risk</th><th>Why flagged</th>"
                    f"<th>Last job notes</th></tr>{''.join(rows)}</table></body></html>")
            p = folder / f"work_orders_{day:%Y-%m-%d}_{crew.replace(' ', '_')}.html"
            p.write_text(html, encoding="utf-8")
            paths.append(p)
        return paths

    # ------------------------------------------------------------------ engine processes
    # ------------------------------------------------------------------ backups of the alert database
    @staticmethod
    def list_backups() -> pd.DataFrame:
        return backup.list_backups()

    @staticmethod
    def backup_now() -> str:
        path = backup.backup_alerts("manual")
        return f"Backed up the alerts and scores to {path.name}."

    def restore_backup(self, path: str) -> str:
        info = backup.restore_backup(path)
        self.clear_cache()
        pre = f" The previous data was saved as {info['pre_restore_backup']}." if info["pre_restore_backup"] else ""
        return (f"Restored {info['restored_from']}: {info['scored_days']} scored days "
                f"({info['first_day']} to {info['last_day']}), {info['open_alerts'] + info['closed_alerts']} alerts.{pre}")

    @staticmethod
    def alert_db_summary() -> dict:
        """What "Start fresh" would delete."""
        return backup.db_summary()

    @staticmethod
    def engine_status() -> dict | None:
        return lock.read_lock()

    @staticmethod
    def progress() -> dict:
        out = {}
        for name in ("replay_state.json", "engine_state.json"):
            try:
                out.update(json.loads((config.REPO_ROOT / "cache" / name).read_text()))
            except (OSError, ValueError):
                pass
        return out

    def start_engine(self, args: list[str]) -> subprocess.Popen:
        """Start `python -m wellwatch.engine <args>` in its own process group (so it can be stopped gracefully)."""
        holder = lock.read_lock()
        if holder:
            raise lock.EngineBusy(f"Engine already running ({holder['mode']}, pid {holder['pid']})")
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        LOG_FILE.parent.mkdir(exist_ok=True)
        out = open(config.REPO_ROOT / "logs" / "engine_app.out", "a", encoding="utf-8")
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        return subprocess.Popen(config.module_cmd("wellwatch.engine", *args), cwd=str(config.REPO_ROOT),
                                stdout=out, stderr=subprocess.STDOUT, creationflags=flags, env=env)

    @staticmethod
    def stop_engine(proc: subprocess.Popen | None = None, timeout: float = 180.0) -> str:
        """Graceful stop, so the engine closes its Canary session and the lock: a stop request it checks
        between days (works for the packaged app and engines started elsewhere), then Ctrl+Break, then
        terminate (only a process this app started)."""
        pid = proc.pid if proc else (lock.read_lock() or {}).get("pid")
        if not pid or not lock.pid_alive(pid):
            return "No engine is running."
        lock.request_stop()
        t0, signalled = time.time(), False
        while time.time() - t0 < timeout:
            if not lock.pid_alive(pid):
                lock.read_lock()  # clears the lock if the engine did not
                lock.clear_stop()
                return f"Engine (pid {pid}) stopped after {time.time() - t0:.0f} s."
            if not signalled and time.time() - t0 > timeout / 2:
                signalled = True
                try:
                    os.kill(pid, signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
                except OSError:
                    pass  # no shared console: rely on the stop request / terminate
            time.sleep(0.5)
        if proc:
            proc.terminate()
            lock.clear_stop()
            return f"Engine (pid {pid}) did not stop in {timeout:.0f} s and was terminated."
        return f"Engine (pid {pid}) is still finishing its current block; the stop request stays in place."

    @staticmethod
    def tail_log(max_bytes: int = 20_000) -> str:
        try:
            with open(LOG_FILE, "rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - max_bytes))
                return f.read().decode("utf-8", errors="replace").split("\n", 1)[-1]
        except OSError:
            return ""

    # ------------------------------------------------------------------ scoreboard, model, quality
    @staticmethod
    def scoreboard() -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
        s, f = OUTPUTS / "replay_summary.csv", OUTPUTS / "replay_scoreboard.csv"
        return (pd.read_csv(s) if s.exists() else None), (pd.read_csv(f) if f.exists() else None)

    @staticmethod
    def run_scoreboard() -> str:
        r = subprocess.run(config.module_cmd("wellwatch.scoreboard"), cwd=str(config.REPO_ROOT),
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if r.returncode != 0:
            raise RuntimeError(r.stderr[-800:] or r.stdout[-800:])
        return r.stdout[-1500:]

    @staticmethod
    def model_card() -> dict:
        try:
            return json.loads((config.resource("models") / "risk_7d_meta.json").read_text())
        except (OSError, ValueError):
            return {}

    def data_quality(self, day=None, n_days: int = 14) -> pd.DataFrame:
        """Running wells whose sensor readings did not change at all over the last n_days (possible stuck sensor)."""
        day = pd.Timestamp(day) if day is not None else self.last_scored_day()
        d = self.production()
        w = d[d["production_date"].between(day - pd.Timedelta(days=n_days - 1), day) & (d["hours_down"] == 0)]
        rows = []
        for col, name in QUALITY_COLUMNS.items():
            g = w.groupby("uwi", observed=True)[col].agg(["count", "nunique", "last"])
            stuck = g[(g["count"] >= n_days - 1) & (g["nunique"] == 1) & (g["last"] != 0)]
            rows += [{"uwi": u, "signal": name, "value": r["last"], "days_unchanged": int(r["count"])}
                     for u, r in stuck.iterrows()]
        out = pd.DataFrame(rows, columns=["uwi", "signal", "value", "days_unchanged"])
        reg = self.registry()[["uwi", "field"]]
        return out.merge(reg, on="uwi", how="left")[["uwi", "field", "signal", "value", "days_unchanged"]]
