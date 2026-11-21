"""Replay feed: writes the held-back days (July-October 2026) into Canary one day at a time, as if
they were arriving live from the field.

Each day D gets exactly the history-load format (wellwatch/backfill.py): the numeric signals and the
DowntimeReason / Job / Failure text tags of every well that has a record on D, all stamped on D.
After each day it writes the clock tag WellWatch.Fleet.DataDay = "YYYY-MM-DD" (stamped on D), which is
how the live engine learns that a new day has arrived.

Usage (from the repository root):
    python -m wellwatch.replay --start 2026-07-01 --end 2026-10-26 --seconds-per-day 10
    python -m wellwatch.replay --resume                 # continue after the last day written
Run the engine alongside it with:  python -m wellwatch.engine live

The replay period was never used for training, calibration or testing of the model.
"""

from __future__ import annotations

import argparse
import json
import logging
import time

import pandas as pd

from . import config, data
from .backfill import day_stamps, well_tvqs
from .canary import CanaryWriter

STATE_FILE = config.REPO_ROOT / "cache" / "replay_state.json"
CLOCK_TAG = f"{config.FLEET_PATH}.DataDay"
REPLAY_START, REPLAY_END = pd.Timestamp("2026-07-01"), pd.Timestamp("2026-10-26")
log = logging.getLogger("wellwatch.replay")


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(**kw) -> None:
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps({**load_state(), **kw}, indent=2))


def day_tvqs(d_day: pd.DataFrame, jobs: pd.DataFrame, fails: pd.DataFrame, day: pd.Timestamp) -> dict:
    """All tag values for one production day (only that day's rows, jobs and failures)."""
    tvqs = {}
    for uwi, w in d_day.groupby("uwi", observed=True):
        tvqs.update(well_tvqs(w, w["field_operating_area"].iloc[0], uwi, jobs, fails))
    tvqs[CLOCK_TAG] = [(day_stamps(pd.Series([day])).iloc[0], day.strftime("%Y-%m-%d"))]
    return tvqs


class Replay:
    """Writes days into Canary through an open CanaryWriter session."""

    def __init__(self, d: pd.DataFrame | None = None):
        self.d = data.oil_producers() if d is None else d
        self.jobs, self.fails = data.jobs(), data.failures()
        self.by_day = dict(tuple(self.d[self.d["production_date"].between(REPLAY_START, REPLAY_END)]
                                 .groupby("production_date")))

    def write_day(self, writer: CanaryWriter, day: pd.Timestamp) -> int:
        day = pd.Timestamp(day)
        rows = self.by_day.get(day, self.d.iloc[0:0])
        before = writer.values_written
        writer.store(day_tvqs(rows, self.jobs, self.fails, day))
        save_state(last_written_day=day.strftime("%Y-%m-%d"))
        return writer.values_written - before


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", default=str(REPLAY_START.date()))
    p.add_argument("--end", default=str(REPLAY_END.date()))
    p.add_argument("--seconds-per-day", type=float, default=10.0, help="pace; 0 = as fast as possible")
    p.add_argument("--resume", action="store_true", help="start after the last day written")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    start, end = pd.Timestamp(args.start), pd.Timestamp(args.end)
    last = load_state().get("last_written_day")
    if args.resume and last:
        start = pd.Timestamp(last) + pd.Timedelta(days=1)
    rp = Replay()
    days = pd.date_range(start, end, freq="D")
    log.info("replaying %d days %s -> %s at %.1f s/day", len(days), start.date(), end.date(), args.seconds_per_day)
    try:
        with CanaryWriter() as w:
            for day in days:
                t0 = time.time()
                n = rp.write_day(w, day)
                w.keep_alive()
                log.info("wrote %s (%d values)", day.date(), n)
                time.sleep(max(0.0, args.seconds_per_day - (time.time() - t0)))
    except KeyboardInterrupt:
        log.info("stopped; resume with --resume (last day written: %s)", load_state().get("last_written_day"))


if __name__ == "__main__":
    main()
