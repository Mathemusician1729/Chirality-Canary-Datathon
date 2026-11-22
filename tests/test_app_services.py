"""Desktop-app service layer, no display: alert actions + Canary mirroring, the one-writer lock, background
tasks, offline mode, KPI numbers and trend assembly."""

import json
import os
import threading

import pandas as pd
import pytest

from wellwatch import config, lock
from wellwatch.alerts import AlertStore
from wellwatch.app.services import OfflineError, Services
from wellwatch.app.settings import AppSettings


class FakeWriter:
    stored = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def store(self, tvqs):
        FakeWriter.stored.append(tvqs)


class BusyWriter(FakeWriter):
    def store(self, tvqs):
        raise RuntimeError("storeData: HTTP 400: Tag is already being logged by session 123")


class BrokenReader:
    def __init__(self, *a, **k):
        raise ConnectionError("Canary unreachable")


def scored(day, rows):
    return pd.DataFrame([{"uwi": u, "field_operating_area": f, "risk": r, "level": l, "rank": i + 1,
                          "expected_loss": r * 20_000, "driver": "test"} for i, (u, f, r, l) in enumerate(rows)])


@pytest.fixture
def svc(tmp_path):
    store = AlertStore(tmp_path / "a.db")
    store.process_day("2026-08-01", scored("2026-08-01", [("A", "Winter", 0.20, 3), ("B", "Winter", 0.09, 2),
                                                          ("C", "Court", 0.06, 1), ("D", "Court", 0.01, 0)]), set())
    s = Services(AppSettings(), writer_factory=FakeWriter, store=store)
    s.online = True
    s._registry = pd.DataFrame({"uwi": list("ABCD"), "field": ["Winter", "Winter", "Court", "Court"],
                                "lat": [52.0, 52.1, 52.2, 52.3], "lon": [-109.0, -109.1, -109.2, -109.3]})
    FakeWriter.stored = []
    return s


def test_alert_action_updates_sqlite_and_mirrors_to_canary(svc):
    aid = int(svc.alerts(open_only=True).query("uwi == 'A'")["id"].iloc[0])
    msg = svc.act(aid, "acknowledge", "karan", "checking")
    assert svc.alerts(open_only=False).set_index("id").loc[aid, "state"] == "Acknowledged"
    assert "AlertState = Acknowledged" in msg
    (tvqs,) = FakeWriter.stored
    tag = config.tag_path("Winter", "A", "AlertState", dataset=config.DATASET_MODEL)
    assert list(tvqs) == [tag] and tvqs[tag][0][1] == "Acknowledged"
    svc.act(aid, "close", "karan", "rods replaced", outcome="Fixed")
    assert FakeWriter.stored[-1][tag][0][1] == "None"  # no open alert left for the well
    with pytest.raises(ValueError):
        svc.act(aid, "shelve", "karan", "", until="2026-08-05")  # shelving needs a reason


def test_busy_canary_tag_gives_clear_message(svc):
    svc._writer_factory = BusyWriter
    aid = int(svc.alerts().query("uwi == 'B'")["id"].iloc[0])
    msg = svc.act(aid, "acknowledge", "karan")
    assert "busy" in msg and svc.alerts(open_only=False).set_index("id").loc[aid, "state"] == "Acknowledged"


def test_lock_blocks_second_engine_and_clears_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(lock, "LOCK_FILE", tmp_path / "engine.lock")
    lock.LOCK_FILE.write_text(json.dumps({"pid": os.getpid() + 999_999, "mode": "live", "started": "x"}))
    assert lock.read_lock() is None and not lock.LOCK_FILE.exists()  # dead pid -> stale lock cleared
    holder = {"pid": os.getppid(), "mode": "replay", "started": "x"}  # a live process that is not us
    lock.LOCK_FILE.write_text(json.dumps(holder))
    with pytest.raises(lock.EngineBusy):
        lock.acquire("live")
    with pytest.raises(lock.EngineBusy):
        Services(AppSettings(), store=AlertStore(tmp_path / "b.db")).start_engine(["live"])
    lock.LOCK_FILE.unlink()
    with lock.engine_lock("live"):
        assert lock.read_lock()["pid"] == os.getpid()
    assert lock.read_lock() is None


def test_background_tasks_run_off_the_ui_thread():
    from PySide6.QtCore import QCoreApplication, QThreadPool
    from wellwatch.app.workers import run_task
    app = QCoreApplication.instance() or QCoreApplication([])
    seen = {}
    run_task(threading.get_ident, on_done=lambda t: seen.setdefault("tid", t))
    QThreadPool.globalInstance().waitForDone(5000)
    for _ in range(50):
        app.processEvents()
    assert seen["tid"] != threading.get_ident()


def test_offline_mode_when_reader_fails(svc, monkeypatch):
    svc._reader_factory = BrokenReader
    ok, msg = svc.test_connection()
    assert not ok and svc.online is False and "not reachable" in msg
    assert "token" not in msg.lower() or "Credential" not in msg
    with pytest.raises(OfflineError):
        svc.send_plan("2026-08-01", {"visits": 0})
    aid = int(svc.alerts().query("uwi == 'C'")["id"].iloc[0]) if len(svc.alerts().query("uwi == 'C'")) else None
    aid = aid or int(svc.alerts()["id"].iloc[0])
    assert "offline" in svc.act(aid, "acknowledge", "karan").lower()
    # trends fall back to the production records
    monkeypatch.setattr(svc, "production", lambda: pd.DataFrame({
        "uwi": ["A"] * 3, "production_date": pd.date_range("2026-07-30", periods=3), "oil_produced_m3": [1.0, 2.0, 3.0],
        "water_produced_m3": [3.0, 2.0, 1.0], "hours_down": [0.0, 0.0, 5.0], "problem": ["", "", "Rod string failure"]}))
    monkeypatch.setattr(svc, "jobs", lambda: pd.DataFrame(columns=["uwi", "start_date", "job_category", "job_type"]))
    monkeypatch.setattr(svc, "failures", lambda: pd.DataFrame(columns=["uwi", "failure_start_date", "failure_type_std", "cause_std"]))
    tr = svc.well_trends("A", "Winter", "2026-08-01", 30)
    assert tr["source"].startswith("offline") and tr["daily"]["WaterCut"].round(2).tolist()[:3] == [0.75, 0.5, 0.25]
    assert any(e["kind"] == "Downtime" for e in tr["events"])


def test_kpis_match_direct_sql(svc):
    ov = svc.overview("2026-08-01")
    k = ov["kpis"]
    db = svc.store.db
    assert k["wells_scored"] == db.execute("SELECT COUNT(*) FROM daily_scores WHERE day='2026-08-01'").fetchone()[0] == 4
    assert k["critical"] == db.execute("SELECT COUNT(*) FROM alerts WHERE state!='Closed' AND max_level>=3").fetchone()[0] == 1
    assert k["warning"] == db.execute("SELECT COUNT(*) FROM alerts WHERE state!='Closed' AND max_level=2").fetchone()[0] == 1
    assert k["watch"] == 1
    assert k["expected_loss"] == pytest.approx(db.execute(
        "SELECT SUM(expected_loss) FROM daily_scores WHERE day='2026-08-01'").fetchone()[0])
    assert ov["top"]["uwi"].tolist()[0] == "A" and len(ov["map"]) == 4


def test_trend_assembly_maps_canary_points_to_days(svc):
    stamp = lambda d: (pd.Timestamp(d) + pd.Timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%S.0000000-06:00")

    class FakeReader:
        def get_tag_data(self, tags, start, end, aggregate=None, interval=None, max_size=0):
            out = {t: [] for t in tags}
            for t in tags:
                if t.endswith(".OilRate"):
                    out[t] = [(stamp("2026-07-31"), 2.0), (stamp("2026-08-01"), 1.0)]
                elif t.endswith(".WaterRate"):
                    out[t] = [(stamp("2026-07-31"), 6.0), (stamp("2026-08-01"), 3.0)]
                elif t.endswith(".Job"):
                    out[t] = [(stamp("2026-07-31"), "Rod Truck / Rod Repair")]
                elif t.endswith(".DowntimeReason"):
                    out[t] = [(stamp("2026-07-31"), "Running"), (stamp("2026-08-01"), "Rod string failure")]
            return out

    svc._reader_factory = FakeReader
    tr = svc.well_trends("A", "Winter", "2026-08-01", 7)
    d = tr["daily"]
    assert list(d.index) == [pd.Timestamp("2026-07-31"), pd.Timestamp("2026-08-01")]
    assert d.loc["2026-07-31", "WaterCut"] == pytest.approx(0.75)
    kinds = {(e["day"], e["kind"]) for e in tr["events"]}
    assert (pd.Timestamp("2026-07-31"), "Job") in kinds and (pd.Timestamp("2026-08-01"), "Downtime") in kinds
    assert (pd.Timestamp("2026-07-31"), "Downtime") not in kinds  # "Running" is not an event


def test_stop_request_is_cleared_by_a_new_engine_and_stops_the_loop(tmp_path, monkeypatch):
    from wellwatch import engine
    monkeypatch.setattr(lock, "LOCK_FILE", tmp_path / "engine.lock")
    monkeypatch.setattr(lock, "STOP_FILE", tmp_path / "engine.stop")
    lock.request_stop()
    with pytest.raises(KeyboardInterrupt):
        engine.check_stop()
    with lock.engine_lock("live"):  # a stale stop request must not stop a new engine
        engine.check_stop()
        lock.request_stop()
        with pytest.raises(KeyboardInterrupt):
            engine.check_stop()
    assert not lock.stop_requested() and lock.read_lock() is None


def test_module_command_from_source_and_packaged(monkeypatch):
    import sys
    assert config.module_cmd("wellwatch.engine", "live")[1:] == ["-m", "wellwatch.engine", "live"]
    monkeypatch.setattr(config, "FROZEN", True)
    assert config.module_cmd("wellwatch.engine", "live") == [sys.executable, "--module", "wellwatch.engine", "live"]
    assert config.resource("models").name == "models"
