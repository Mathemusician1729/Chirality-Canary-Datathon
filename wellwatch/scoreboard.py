"""Replay scoreboard: how well did WellWatch's warnings anticipate real failures?

Ground truth: crew-needing failures (same definition the model was trained on, see
train.crew_failure_starts) that started during the replay period. For each failure:
    caught     the well had a warning (AlertLevel >= 2) on at least one scored day in the 7 days before it
    lead time  days from the first such warning to the failure start
Also reported: warnings per day, warning precision (share of warning well-days followed by a failure
within 7 days), alert precision (share of opened alerts followed by a failure within 7 days), and the
value of the failures caught (lost oil x quarterly value + repair cost).
The same metrics are computed for the best simple rule (shortest run life since the last repair)
given exactly the same number of warnings per day, so the comparison is fair.

Run after a replay:  python -m wellwatch.scoreboard
Outputs: outputs/replay_scoreboard.csv (one row per failure), outputs/replay_summary.csv,
and fleet KPI tags in Canary (WellWatch.Fleet.CaughtFailures, MissedFailures, MedianLeadDays, CatchRate).
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from . import config, data
from .alerts import AlertStore
from .train import REPAIR_CATEGORIES, crew_failure_starts

HORIZON = 7
OUT = config.REPO_ROOT / "outputs"


def catches(failures: pd.DataFrame, warnings: pd.DataFrame, horizon: int = HORIZON) -> pd.DataFrame:
    """failures: uwi, production_date. warnings: uwi, day (one row per warning well-day).

    Returns failures with caught (bool) and lead_days (days from first warning in the window to the failure).
    """
    f = failures.reset_index(drop=True).copy()
    w = warnings[["uwi", "day"]].drop_duplicates()
    m = f.reset_index().merge(w, on="uwi")
    m = m[(m["day"] >= m["production_date"] - pd.Timedelta(days=horizon)) & (m["day"] < m["production_date"])]
    first = m.groupby("index")["day"].min()
    f["caught"] = f.index.isin(first.index)
    f["lead_days"] = (f["production_date"] - first.reindex(f.index)).dt.days
    return f


def warning_precision(warnings: pd.DataFrame, failures: pd.DataFrame, horizon: int = HORIZON) -> float:
    """Share of warning well-days followed by a failure start on the same well within 1..horizon days."""
    if warnings.empty:
        return float("nan")
    w = warnings[["uwi", "day"]].drop_duplicates().reset_index()
    m = w.merge(failures[["uwi", "production_date"]], on="uwi")
    hit = m[(m["production_date"] > m["day"]) & (m["production_date"] <= m["day"] + pd.Timedelta(days=horizon))]
    return float(w["index"].isin(hit["index"]).mean())


def alert_precision(alerts: pd.DataFrame, failures: pd.DataFrame, horizon: int = HORIZON) -> float:
    """Share of opened alerts followed by a failure on that well within 1..horizon days of opening."""
    if alerts.empty:
        return float("nan")
    a = alerts[["id", "uwi", "opened_day"]].copy()
    a["opened_day"] = pd.to_datetime(a["opened_day"])
    m = a.merge(failures[["uwi", "production_date"]], on="uwi")
    hit = m[(m["production_date"] > m["opened_day"]) & (m["production_date"] <= m["opened_day"] + pd.Timedelta(days=horizon))]
    return float(a["id"].isin(hit["id"]).mean())


def run_life(scores: pd.DataFrame, jobs: pd.DataFrame) -> pd.Series:
    """Days since the last Rod Truck / Workover job strictly before each (uwi, day)."""
    rep = jobs[jobs["job_category"].isin(REPAIR_CATEGORIES)][["uwi", "start_date"]].dropna().sort_values("start_date")
    left = scores[["uwi", "day"]].reset_index().sort_values("day")
    m = pd.merge_asof(left, rep, left_on="day", right_on="start_date", by="uwi", allow_exact_matches=False)
    m = m.set_index("index").sort_index()
    return (m["day"] - m["start_date"]).dt.days


def baseline_warnings(scores: pd.DataFrame, jobs: pd.DataFrame, per_day: pd.Series) -> pd.DataFrame:
    """Same number of warnings per day as the model, given to the wells with the shortest run life."""
    s = scores.copy()
    s["run_life"] = run_life(s, jobs).fillna(1e9).to_numpy()
    s = s.sort_values(["day", "run_life"])
    s["k"] = s["day"].map(per_day).fillna(0)
    s["pos"] = s.groupby("day").cumcount()
    return s[s["pos"] < s["k"]][["uwi", "day"]]


def summarise(name: str, f: pd.DataFrame, warnings: pd.DataFrame, failures: pd.DataFrame, n_days: int,
              alerts: pd.DataFrame | None = None) -> dict:
    caught = f[f["caught"]]
    return {
        "approach": name, "failures": len(f), "caught": int(f["caught"].sum()), "missed": int((~f["caught"]).sum()),
        "catch_rate": f["caught"].mean(), "median_lead_days": caught["lead_days"].median(),
        "lead_days_p25": caught["lead_days"].quantile(0.25), "lead_days_p75": caught["lead_days"].quantile(0.75),
        "warnings_per_day": len(warnings) / max(n_days, 1),
        "warning_precision": warning_precision(warnings, failures),
        "alert_precision": alert_precision(alerts, failures) if alerts is not None else np.nan,
        "value_caught_cad": caught["value_cad"].sum(), "value_all_failures_cad": f["value_cad"].sum(),
    }


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", default="2026-07-01")
    p.add_argument("--no-canary", action="store_true", help="do not write KPI tags to Canary")
    args = p.parse_args(argv)
    store = AlertStore()
    scores = store.daily_scores()
    if scores.empty:
        raise SystemExit("No scored days yet. Run: python -m wellwatch.engine replay ...")
    start, end = pd.Timestamp(args.start), scores["day"].max()
    d, jobs = data.oil_producers(), data.jobs()
    failures = crew_failure_starts(d, jobs)
    failures = failures[failures["production_date"].between(start, end)].reset_index(drop=True)
    failures["value_cad"] = (failures["episode_lost_oil_m3"].fillna(0)
                             * data.du.oil_value_cad_per_m3(failures["production_date"], "base")
                             + failures["repair_cost_cad"].fillna(0))
    period = scores[scores["day"].between(start - pd.Timedelta(days=HORIZON), end)]
    n_days = scores.loc[scores["day"].between(start, end), "day"].nunique()

    model_w = period[period["level"] >= 2][["uwi", "day"]]
    per_day = model_w.groupby("day").size()
    base_w = baseline_warnings(period, jobs, per_day)
    alerts = store.list_alerts()
    alerts = alerts[pd.to_datetime(alerts["opened_day"]).between(start, end)]

    fm, fb = catches(failures, model_w), catches(failures, base_w)
    in_period = lambda w: w[w["day"].between(start, end)]
    summary = pd.DataFrame([
        summarise("WellWatch model", fm, in_period(model_w), failures, n_days, alerts),
        summarise("Simple rule: shortest run life (same warnings/day)", fb, in_period(base_w), failures, n_days),
    ])
    per_failure = failures.assign(caught_model=fm["caught"], lead_days_model=fm["lead_days"],
                                  caught_rule=fb["caught"], lead_days_rule=fb["lead_days"])
    OUT.mkdir(exist_ok=True)
    per_failure.to_csv(OUT / "replay_scoreboard.csv", index=False)
    summary.to_csv(OUT / "replay_summary.csv", index=False)
    pd.set_option("display.width", 200)
    print(f"Replay {start.date()} .. {end.date()}: {len(failures)} crew-needing failures, {n_days} scored days")
    print(summary.T.to_string(header=False))

    if not args.no_canary:
        from .canary import CanaryWriter
        ts = end.tz_localize("UTC") + pd.Timedelta(hours=config.DAILY_STAMP_UTC_HOUR)
        row = summary.iloc[0]
        with CanaryWriter() as w:
            w.store({f"{config.FLEET_PATH}.CaughtFailures": [(ts, int(row["caught"]))],
                     f"{config.FLEET_PATH}.MissedFailures": [(ts, int(row["missed"]))],
                     f"{config.FLEET_PATH}.MedianLeadDays": [(ts, float(row["median_lead_days"]))],
                     f"{config.FLEET_PATH}.CatchRate": [(ts, round(float(row["catch_rate"]), 4))]})
        print("wrote KPI tags to Canary")


if __name__ == "__main__":
    main()
