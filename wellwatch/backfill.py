"""Load the historical daily well data into the Canary Historian.

Each well gets one tag per signal (see config.SIGNALS) plus text tags for the downtime reason,
service jobs and failures:  Wells.<Field>.<UWI>.<Signal>

Usage (from the repository root):
    python -m wellwatch.backfill --fields "Cactus Lake" --limit-wells 1     # one-well speed test
    python -m wellwatch.backfill                                           # default fields
    python -m wellwatch.backfill --until 2025-08-31                        # leave later days for live replay
"""

from __future__ import annotations

import argparse
import time

import pandas as pd

from . import config, data
from .canary import CanaryWriter


def day_stamps(dates: pd.Series) -> pd.Series:
    return pd.to_datetime(dates).dt.tz_localize("UTC") + pd.Timedelta(hours=config.DAILY_STAMP_UTC_HOUR)


def well_tvqs(w: pd.DataFrame, field: str, uwi: str, jobs: pd.DataFrame, fails: pd.DataFrame,
              signals: list[str] | None = None) -> dict:
    ts = day_stamps(w["production_date"]).tolist()
    tvqs = {}
    for signal, (col, _desc, _unit) in config.SIGNALS.items():
        if signals and signal not in signals:
            continue
        vals = w[col].astype(float).tolist()
        pts = [(t, v) for t, v in zip(ts, vals) if v == v]
        if pts:
            tvqs[config.tag_path(field, uwi, signal)] = pts
    if signals:  # partial reload of selected numeric signals only
        return tvqs
    reason = w["problem"].astype(str).where(w["hours_down"] > 0, "Running").tolist()
    tvqs[config.tag_path(field, uwi, "DowntimeReason")] = list(zip(ts, reason))
    first, last = w["production_date"].min(), w["production_date"].max()
    j = jobs[(jobs["uwi"] == uwi) & jobs["start_date"].between(first, last)]
    if len(j):
        text = (j["job_category"].astype(str) + " / " + j["job_type"].astype(str)).tolist()
        tvqs[config.tag_path(field, uwi, "Job")] = list(zip(day_stamps(j["start_date"]), text))
    f = fails[(fails["uwi"] == uwi) & fails["failure_start_date"].between(first, last)]
    if len(f):
        text = (f["failure_type_std"].astype(str) + " / " + f["cause_std"].astype(str)).tolist()
        tvqs[config.tag_path(field, uwi, "Failure")] = list(zip(day_stamps(f["failure_start_date"]), text))
    return tvqs


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--fields", nargs="*", default=config.DEFAULT_FIELDS)
    p.add_argument("--all-fields", action="store_true", help="load every oil-producing field")
    p.add_argument("--limit-wells", type=int, default=None)
    p.add_argument("--until", default=None, help="last production date to load (YYYY-MM-DD)")
    p.add_argument("--from", dest="since", default=None, help="first production date to load (YYYY-MM-DD)")
    p.add_argument("--signals", nargs="*", default=None, help="only these numeric signals (e.g. EquipmentDownHours)")
    args = p.parse_args(argv)

    t0 = time.time()
    d = data.oil_producers()
    if args.all_fields:
        args.fields = sorted(d["field_operating_area"].dropna().unique().tolist())
    d = d[d["field_operating_area"].isin(args.fields)]
    if args.until:
        d = d[d["production_date"] <= pd.Timestamp(args.until)]
    if args.since:
        d = d[d["production_date"] >= pd.Timestamp(args.since)]
    wells = d.groupby("uwi", observed=True)["field_operating_area"].first()
    if args.limit_wells:
        wells = wells.iloc[: args.limit_wells]
    jobs, fails = data.jobs(), data.failures()
    print(f"Loading {len(wells):,} wells from {args.fields} ({time.time() - t0:.0f}s to prepare)")

    by_well = dict(tuple(d[d["uwi"].isin(wells.index)].groupby("uwi", observed=True)))
    with CanaryWriter() as w:
        for i, (uwi, field) in enumerate(wells.items(), 1):
            w.store(well_tvqs(by_well[uwi], field, uwi, jobs, fails, args.signals))
            if i % 25 == 0 or i == len(wells):
                rate = w.values_written / max(time.time() - t0, 1e-9)
                print(f"  {i:,}/{len(wells):,} wells, {w.values_written:,} values ({rate:,.0f} values/s)")
                w.keep_alive()
    print(f"Done: {w.values_written:,} values in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
