"""Backups of the alert database (cache/alerts.db): alerts, their audit trail and the daily scores.

A fresh replay wipes that database and takes ~45-60 minutes to rebuild, so the engine saves a backup
before "--fresh" ("pre-fresh") and when a replay reaches its end day ("complete"); the app can back up
on demand ("manual") and restore any backup (after saving a "pre-restore" backup of the current data).
Canary data is never touched.

Backups live in cache/backups/ (git-ignored): alerts_<YYYYmmdd-HHMMSS>_<reason>.db plus a JSON sidecar.
The newest KEEP backups are kept, and the newest "complete" one is never deleted.

    python -m wellwatch.backup            # list backups
    python -m wellwatch.backup --now      # back up now
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
from pathlib import Path

import pandas as pd

from . import config, lock

CACHE = config.REPO_ROOT / "cache"
DB_FILE = CACHE / "alerts.db"
BACKUP_DIR = CACHE / "backups"
ENGINE_STATE = CACHE / "engine_state.json"
REPLAY_STATE = CACHE / "replay_state.json"
KEEP = 5
REASONS = ("pre-fresh", "complete", "manual", "pre-restore")


def db_summary(path: Path | str | None = None) -> dict:
    """First/last scored day, number of scored days and open/closed alerts in an alert database."""
    path = Path(path or DB_FILE)
    out = {"first_day": None, "last_day": None, "scored_days": 0, "open_alerts": 0, "closed_alerts": 0}
    if not path.exists():
        return out
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        first, last, n = con.execute("SELECT MIN(day), MAX(day), COUNT(DISTINCT day) FROM daily_scores").fetchone()
        opened, closed = con.execute("SELECT COALESCE(SUM(state != 'Closed'), 0), COALESCE(SUM(state = 'Closed'), 0) "
                                     "FROM alerts").fetchone()
        out.update(first_day=first, last_day=last, scored_days=int(n or 0), open_alerts=int(opened),
                   closed_alerts=int(closed))
    except sqlite3.Error:
        pass  # empty or new database without tables
    finally:
        con.close()
    return out


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _copy_db(src: Path, dst: Path) -> None:
    """Consistent copy with the SQLite backup API (safe while another connection has the db open)."""
    s, d = sqlite3.connect(src), sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def backup_alerts(reason: str, require_data: bool = False) -> Path | None:
    """Back up the alert database. Returns the backup path, or None if require_data and it has no scores."""
    if reason not in REASONS:
        raise ValueError(f"reason must be one of {REASONS}")
    if not DB_FILE.exists():
        raise FileNotFoundError(f"No alert database at {DB_FILE}")
    info = db_summary(DB_FILE)
    if require_data and info["scored_days"] == 0:
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now()
    stem = f"alerts_{now:%Y%m%d-%H%M%S}_{reason}"
    path, i = BACKUP_DIR / f"{stem}.db", 1
    while path.exists():  # two backups within the same second
        path, i = BACKUP_DIR / f"{stem}-{i}.db", i + 1
    _copy_db(DB_FILE, path)
    meta = {"reason": reason, "created": now.isoformat(timespec="microseconds"), **info,
            "engine_state": _read_json(ENGINE_STATE), "replay_state": _read_json(REPLAY_STATE)}
    path.with_suffix(".json").write_text(json.dumps(meta, indent=2))
    prune()
    return path


def list_backups() -> pd.DataFrame:
    """Backups, newest first, with their sidecar info and file size."""
    cols = ["file", "created", "reason", "first_day", "last_day", "scored_days", "open_alerts", "closed_alerts",
            "size_mb", "path"]
    rows = []
    for p in BACKUP_DIR.glob("alerts_*.db") if BACKUP_DIR.exists() else []:
        meta = _read_json(p.with_suffix(".json")) or {"reason": p.stem.split("_")[-1], **db_summary(p)}
        rows.append({"file": p.name, "created": meta.get("created") or dt.datetime.fromtimestamp(p.stat().st_mtime)
                     .isoformat(timespec="seconds"), "reason": meta.get("reason"), "first_day": meta.get("first_day"),
                     "last_day": meta.get("last_day"), "scored_days": meta.get("scored_days", 0),
                     "open_alerts": meta.get("open_alerts", 0), "closed_alerts": meta.get("closed_alerts", 0),
                     "size_mb": round(p.stat().st_size / 1e6, 1), "path": str(p)})
    df = pd.DataFrame(rows, columns=cols)
    return df.sort_values(["created", "file"], ascending=False).reset_index(drop=True)


def prune(keep: int = KEEP) -> list[Path]:
    """Delete all but the newest `keep` backups; the newest "complete" backup is always kept."""
    df = list_backups()
    if len(df) <= keep:
        return []
    complete = df[df["reason"] == "complete"]
    protected = set(complete["path"].head(1))
    kept = set(df["path"].head(keep)) | protected
    while len(kept) > keep:  # protecting an old "complete" one pushes out the oldest of the rest
        newest_first = [p for p in df["path"] if p in kept and p not in protected]
        kept.discard(newest_first[-1])
    removed = []
    for p in df["path"]:
        if p not in kept:
            for f in (Path(p), Path(p).with_suffix(".json")):
                f.unlink(missing_ok=True)
            removed.append(Path(p))
    return removed


def _write_state(path: Path, key: str, value) -> None:
    state = _read_json(path)
    state[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


def restore_backup(path: Path | str) -> dict:
    """Replace the alert database with a backup and point the engine/replay state at its last scored day."""
    path = Path(path)
    holder = lock.read_lock()
    if holder:
        raise lock.EngineBusy(f"Stop the running engine first ({holder.get('mode')}, pid {holder.get('pid')}): "
                              "it is writing to the alert database.")
    if not path.exists():
        raise FileNotFoundError(f"Backup not found: {path}")
    info = db_summary(path)
    if info["scored_days"] == 0:
        raise ValueError(f"{path.name} has no scored days; nothing to restore.")
    pre = backup_alerts("pre-restore", require_data=True) if DB_FILE.exists() else None
    if not DB_FILE.exists():
        DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    _copy_db(path, DB_FILE)
    _write_state(ENGINE_STATE, "last_scored_day", info["last_day"])
    _write_state(REPLAY_STATE, "last_written_day", info["last_day"])
    return {**info, "restored_from": path.name, "pre_restore_backup": pre.name if pre else None}


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--now", action="store_true", help="back up the alert database now")
    p.add_argument("--reason", default="manual", choices=REASONS)
    args = p.parse_args(argv)
    if args.now:
        print(f"backed up to {backup_alerts(args.reason)}")
    df = list_backups()
    print(df.drop(columns=["path"]).to_string(index=False) if len(df) else "no backups yet")


if __name__ == "__main__":
    main()
