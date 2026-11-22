"""Backups of the alert database: round trip, restore refused while an engine runs, state files,
pruning, and the pre-fresh backup. Temporary paths only; the real cache/ is never touched."""

import json
import os
import sqlite3

import pandas as pd
import pytest

from wellwatch import backup, engine, lock, replay
from wellwatch.alerts import AlertStore


def scored(rows):
    return pd.DataFrame([{"uwi": u, "field_operating_area": "Winter", "risk": r, "level": l, "rank": i + 1,
                          "expected_loss": r * 20_000, "driver": "test"} for i, (u, r, l) in enumerate(rows)])


@pytest.fixture
def env(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(backup, "DB_FILE", cache / "alerts.db")
    monkeypatch.setattr(backup, "BACKUP_DIR", cache / "backups")
    monkeypatch.setattr(backup, "ENGINE_STATE", cache / "engine_state.json")
    monkeypatch.setattr(backup, "REPLAY_STATE", cache / "replay_state.json")
    monkeypatch.setattr(engine, "STATE_FILE", cache / "engine_state.json")
    monkeypatch.setattr(replay, "STATE_FILE", cache / "replay_state.json")
    monkeypatch.setattr(lock, "LOCK_FILE", cache / "engine.lock")
    monkeypatch.setattr(lock, "STOP_FILE", cache / "engine.stop")
    store = AlertStore(cache / "alerts.db")
    store.process_day("2026-08-01", scored([("A", 0.20, 3), ("B", 0.09, 2), ("C", 0.01, 0)]), set())
    store.process_day("2026-08-02", scored([("A", 0.22, 3), ("B", 0.03, 0), ("C", 0.01, 0)]), set())
    return cache, store


def dump(path):
    con = sqlite3.connect(path)
    try:
        return {t: con.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall()
                for t in ("alerts", "alert_events", "daily_scores")}
    finally:
        con.close()


def test_round_trip_restores_identical_data_and_state(env):
    cache, store = env
    before = dump(cache / "alerts.db")
    b = backup.backup_alerts("manual")
    meta = json.loads(b.with_suffix(".json").read_text())
    assert meta["reason"] == "manual" and meta["scored_days"] == 2 and meta["last_day"] == "2026-08-02"
    store.reset()  # the data is gone ...
    assert backup.db_summary()["scored_days"] == 0
    info = backup.restore_backup(b)  # ... and comes back
    assert dump(cache / "alerts.db") == before
    assert info["pre_restore_backup"] is None  # nothing to save: the current db was empty
    assert json.loads((cache / "engine_state.json").read_text())["last_scored_day"] == "2026-08-02"
    assert json.loads((cache / "replay_state.json").read_text())["last_written_day"] == "2026-08-02"


def test_restore_saves_current_data_first(env):
    cache, store = env
    b = backup.backup_alerts("manual")
    store.process_day("2026-08-03", scored([("A", 0.3, 3)]), set())
    info = backup.restore_backup(b)
    assert info["pre_restore_backup"] and "pre-restore" in info["pre_restore_backup"]
    assert backup.db_summary(backup.BACKUP_DIR / info["pre_restore_backup"])["last_day"] == "2026-08-03"


def test_restore_refused_while_an_engine_holds_the_lock(env):
    cache, _ = env
    b = backup.backup_alerts("manual")
    lock.LOCK_FILE.write_text(json.dumps({"pid": os.getppid(), "mode": "replay", "started": "x"}))
    before = dump(cache / "alerts.db")
    with pytest.raises(lock.EngineBusy):
        backup.restore_backup(b)
    assert dump(cache / "alerts.db") == before
    lock.LOCK_FILE.unlink()


def test_pruning_keeps_five_and_the_newest_complete(env):
    complete = backup.backup_alerts("complete")
    for _ in range(6):
        backup.backup_alerts("manual")
    df = backup.list_backups()
    assert len(df) == 5
    assert complete.name in set(df["file"])  # oldest, but the only "complete" one
    assert list(df["reason"]).count("manual") == 4
    assert df["created"].is_monotonic_decreasing


def test_fresh_takes_a_pre_fresh_backup_then_clears(env):
    cache, store = env
    engine.save_state(last_scored_day="2026-08-02")
    path = engine.reset_for_fresh(store)
    assert path is not None and "pre-fresh" in path.name
    assert backup.db_summary(path)["scored_days"] == 2
    assert backup.db_summary()["scored_days"] == 0 and not engine.STATE_FILE.exists()
    # a second fresh start on an empty database does not add an empty backup
    assert engine.reset_for_fresh(store) is None
    assert len(backup.list_backups()) == 1


def test_failed_backup_never_stops_the_engine(env, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(backup, "backup_alerts", boom)
    assert engine.safe_backup("complete") is None
