"""Continuous WellWatch engine: reads new days from Canary, scores them, writes the results back to
Canary and runs the alert lifecycle (wellwatch/alerts.py).

Modes
  replay  One orchestrated loop: the replay feed writes day(s) into Canary -> the engine reads them
          back FROM CANARY -> scores -> writes risk outputs and alert states. Days are handled in
          blocks (default 7 at full speed): Canary's read cost is per tag per request, not per day
          of data, so one 7-day read costs about the same as a 1-day read.
          The 7 days before --start (already in Canary) are scored first as a warm-up, so failures
          early in the period have a full 7-day warning window.
  live    Engine only: polls the clock tag WellWatch.Fleet.DataDay (written by wellwatch.replay or, in
          production, by the data feed) and scores every new day, in one block if several arrived.

Usage (from the repository root):
    python -m wellwatch.engine replay --start 2026-07-01 --end 2026-10-26 --seconds-per-day 0 --fresh
    python -m wellwatch.engine live --poll-seconds 60
Both resume from cache/engine_state.json (last scored day). --fresh clears alerts and state first.
Logs go to logs/wellwatch.log.
"""

from __future__ import annotations

import argparse
import json
import logging
import time

import pandas as pd

from . import config
from .alerts import AlertStore
from .canary import CanaryReader, CanaryWriter
from .replay import CLOCK_TAG, REPLAY_END, REPLAY_START, Replay, save_state as save_replay_state
from .score import RiskEngine, write_outputs

STATE_FILE = config.REPO_ROOT / "cache" / "engine_state.json"
LOG_DIR = config.REPO_ROOT / "logs"
WARMUP_DAYS = 7
log = logging.getLogger("wellwatch.engine")


def setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(LOG_DIR / "wellwatch.log", encoding="utf-8"),
                                  logging.StreamHandler()])
    for noisy in ("urllib3", "requests"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(**kw) -> None:
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps({**load_state(), **kw}, indent=2))


def stamp(day: pd.Timestamp) -> pd.Timestamp:
    return pd.Timestamp(day).tz_localize("UTC") + pd.Timedelta(hours=config.DAILY_STAMP_UTC_HOUR)


def latest_data_day(reader: CanaryReader) -> pd.Timestamp | None:
    """Newest day announced on the clock tag, or None if the feed has not started."""
    res = reader.get_tag_data([f"localhost.{CLOCK_TAG}"], "2021-01-01T00:00:00Z", "2030-01-01T00:00:00Z",
                              max_size=100_000)
    days = [pd.Timestamp(v) for _, v in next(iter(res.values()), []) if v]
    return max(days) if days else None


class Engine:
    def __init__(self, workers: int = config.READ_WORKERS):
        self.risk = RiskEngine(workers=workers)
        self.alerts = AlertStore()
        self.loaded = False

    def failure_starts(self, day: pd.Timestamp) -> set[str]:
        """Wells whose equipment-failure downtime starts on ``day`` (EquipmentDownHours > 0 after a running day)."""
        f = self.risk.frame
        today = f[f["production_date"] == day].set_index("uwi")["eq_down_hours"]
        prev = f[f["production_date"] == day - pd.Timedelta(days=1)].set_index("uwi")["eq_down_hours"]
        started = today[(today > 0) & (prev.reindex(today.index).fillna(0) <= 0)]
        return set(started.index)

    def process(self, days, writer: CanaryWriter) -> dict:
        """Read ``days`` from Canary in one block, score them, run alerts, write outputs back."""
        days = list(pd.DatetimeIndex(sorted(days)))
        first, last = days[0], days[-1]
        writer.keep_alive()  # long reads below must not let the write session time out
        t0 = time.time()
        if not self.loaded:
            self.risk.load(first - pd.Timedelta(days=1))
            self.loaded = True
            log.info("loaded %d-day window for %d wells [%.0fs]", 100, len(self.risk.wells), time.time() - t0)
            t0 = time.time()
        self.risk.advance(first, last)
        t_read = time.time() - t0
        scored = self.risk.score_many(days)
        t_score = time.time() - t0 - t_read
        summary = {"days": len(days), "read_s": round(t_read, 1), "score_s": round(t_score, 1)}
        for day in days:
            out = scored[scored["production_date"] == day]
            fails = self.failure_starts(day)
            counts = self.alerts.process_day(day, out, fails)
            if len(out):
                write_outputs(writer, out, day)
            ts = stamp(day)
            tvqs = {}
            uwis = set(out["uwi"]) | fails
            fields = dict(zip(self.risk.wells["uwi"], self.risk.wells["field_operating_area"]))
            for uwi, state in self.alerts.states_for(uwis).items():
                if uwi in fields:
                    tvqs[config.tag_path(fields[uwi], uwi, "AlertState", dataset=config.DATASET_MODEL)] = [(ts, state)]
            tvqs[f"{config.FLEET_PATH}.OpenAlerts"] = [(ts, int(counts.get("open_alerts") or 0))]
            tvqs[f"{config.FLEET_PATH}.LastScoredDay"] = [(ts, day.strftime("%Y-%m-%d"))]
            writer.store(tvqs)
            save_state(last_scored_day=day.strftime("%Y-%m-%d"))
            log.info("%s scored %d wells, warnings %d, failures started %d, alerts opened %d / closed %d+%d, open %s",
                     day.date(), len(out), int((out["level"] >= 2).sum()) if len(out) else 0, len(fails),
                     counts.get("opened", 0), counts.get("failed", 0), counts.get("cleared", 0),
                     counts.get("open_alerts"))
        summary["write_s"] = round(time.time() - t0 - t_read - t_score, 1)
        summary["per_day_s"] = round((time.time() - t0) / len(days), 1)
        writer.keep_alive()
        log.info("block %s..%s: %s", first.date(), last.date(), summary)
        return summary


def run_replay(start, end, seconds_per_day: float, block: int, fresh: bool, workers: int) -> None:
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    eng = Engine(workers)
    if fresh:
        eng.alerts.reset()
        STATE_FILE.unlink(missing_ok=True)
        save_replay_state(last_written_day=None)
    last = load_state().get("last_scored_day")
    warmup = pd.date_range(start - pd.Timedelta(days=WARMUP_DAYS), start - pd.Timedelta(days=1), freq="D")
    feed = pd.date_range(start, end, freq="D")
    if last:  # resume
        warmup = warmup[warmup > pd.Timestamp(last)]
        feed = feed[feed > pd.Timestamp(last)]
    rp = Replay(eng.risk.d)
    log.info("replay: warm-up %d days, feed %d days, block %d, %.1f s/day", len(warmup), len(feed), block,
             seconds_per_day)
    try:
        with CanaryWriter() as w:
            if len(warmup):
                eng.process(warmup, w)  # already in Canary: score only
            for i in range(0, len(feed), block):
                chunk = feed[i:i + block]
                for day in chunk:
                    t0 = time.time()
                    rp.write_day(w, day)
                    time.sleep(max(0.0, seconds_per_day - (time.time() - t0)))
                eng.process(chunk, w)
    except KeyboardInterrupt:
        log.info("stopped by user; resume by running the same command without --fresh")


def run_live(poll_seconds: float, max_block: int, workers: int) -> None:
    eng = Engine(workers)
    reader = CanaryReader()
    log.info("live: polling %s every %.0f s", CLOCK_TAG, poll_seconds)
    try:
        with CanaryWriter() as w:
            while True:
                latest = latest_data_day(reader)
                last = load_state().get("last_scored_day")
                if latest is not None:
                    begin = pd.Timestamp(last) + pd.Timedelta(days=1) if last else latest
                    todo = pd.date_range(begin, latest, freq="D")
                    for i in range(0, len(todo), max_block):
                        eng.process(todo[i:i + max_block], w)
                w.keep_alive()
                time.sleep(poll_seconds)
    except KeyboardInterrupt:
        log.info("stopped by user")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="mode", required=True)
    r = sub.add_parser("replay", help="feed held-back days into Canary and score them")
    r.add_argument("--start", default=str(REPLAY_START.date()))
    r.add_argument("--end", default=str(REPLAY_END.date()))
    r.add_argument("--seconds-per-day", type=float, default=0.0)
    r.add_argument("--block", type=int, default=None, help="days per read/score block (default 7 at full speed, else 1)")
    r.add_argument("--fresh", action="store_true", help="clear alerts and state before starting")
    lv = sub.add_parser("live", help="score new days as they appear in Canary")
    lv.add_argument("--poll-seconds", type=float, default=60.0)
    lv.add_argument("--max-block", type=int, default=14)
    for sp in (r, lv):
        sp.add_argument("--workers", type=int, default=config.READ_WORKERS)
    args = p.parse_args(argv)
    setup_logging()
    if args.mode == "replay":
        block = args.block or (7 if args.seconds_per_day == 0 else 1)
        run_replay(args.start, args.end, args.seconds_per_day, block, args.fresh, args.workers)
    else:
        run_live(args.poll_seconds, args.max_block, args.workers)


if __name__ == "__main__":
    main()
