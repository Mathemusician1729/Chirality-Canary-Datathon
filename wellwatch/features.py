"""Past-only features per well and day, shared by model training and live scoring.

Every feature for day t uses data up to day t-1 only (lags, rolling windows with closed="left",
events dated strictly before t). The leakage test in tests/ checks this.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SENSORS = {  # feature prefix: cleaned column
    "eff": "pump_efficiency_pct", "rpm": "pcp_pump_rpm", "torque": "pcp_pump_rod_torque_ftlb",
    "current": "pcp_pump_electric_current_a", "tbg_p": "tubing_pressure_kpa",
    "csg_p": "casing_pressure_kpa", "wcut": "water_cut", "oil": "oil_produced_m3",
}
FACTOR_GROUPS = {
    "eff": "Pump efficiency", "rpm": "Pump speed (RPM)", "torque": "Rod torque",
    "current": "Motor current", "tbg_p": "Tubing pressure", "csg_p": "Casing pressure",
    "wcut": "Water cut", "oil": "Recent oil rate", "well_age": "Well age",
    "days_since_last_failure": "Failure history", "failures_prev_365": "Failure history",
    "days_since_last_job": "Service-job history", "jobs_prev_365": "Service-job history",
    "last_job": "Service-job history", "pcp_pump": "Pump type", "pump_displacement": "Pump type",
    "field_operating_area": "Location", "facility_name": "Location",
}

JOB_FLAGS = ["sum_rod_broken", "sum_stator_rotor", "sum_sand", "sum_corrosion", "sum_worn", "sum_torque", "sum_injury"]

def _asof(left, right, right_date, cols, left_date="production_date"):
    """For each (uwi, day) take the latest right-hand event dated STRICTLY BEFORE the day."""
    l = left[["uwi", left_date]].reset_index()
    l[left_date] = l[left_date].astype("datetime64[ns]")
    r = right[["uwi", right_date] + cols].dropna(subset=[right_date]).sort_values(right_date)
    r[right_date] = r[right_date].astype("datetime64[ns]")
    m = pd.merge_asof(l.sort_values(left_date), r, left_on=left_date, right_on=right_date,
                      by="uwi", allow_exact_matches=False, direction="backward")
    return m.set_index("index").reindex(left.index)


def _count_prev(left, events, event_date, days=365):
    """Number of events per well in [t - days, t)."""
    ev = events[["uwi", event_date]].dropna().sort_values(["uwi", event_date]).copy()
    ev["cum"] = ev.groupby("uwi").cumcount() + 1
    now = _asof(left, ev, event_date, ["cum"])["cum"].fillna(0)
    shifted = left[["uwi"]].assign(_d=left["production_date"] - pd.Timedelta(days=days))
    before = _asof(shifted, ev, event_date, ["cum"], left_date="_d")["cum"].fillna(0)
    return (now - before).astype("float32")


def build_features(d, jobs, failures):
    """Past-only features for every row of ``d`` (sorted by uwi, production_date; one row per day)."""
    d = d.copy()
    liquid = d["oil_produced_m3"] + d["water_produced_m3"]
    d["water_cut"] = (d["water_produced_m3"] / liquid).where(liquid > 0)
    feats = pd.DataFrame(index=d.index)

    # Lag 1: only when the previous row is the previous calendar day.
    g = d.groupby("uwi", sort=True)
    consecutive = (d["production_date"] - g["production_date"].shift(1)) == pd.Timedelta(days=1)
    for name, col in SENSORS.items():
        feats[f"{name}_lag1"] = g[col].shift(1).where(consecutive)

    # Rolling windows over the previous 7 / 30 calendar days, excluding day t (closed="left").
    src = d[["uwi", "production_date"] + list(SENSORS.values())].set_index("production_date")
    for win in (7, 30):
        r = src.groupby("uwi", sort=True).rolling(f"{win}D", closed="left")
        mean = r.mean().reset_index(drop=True)
        for name, col in SENSORS.items():
            feats[f"{name}_mean{win}"] = mean[col].to_numpy()
        if win == 7:
            std = r.std().reset_index(drop=True)
            for name, col in SENSORS.items():
                feats[f"{name}_std7"] = std[col].to_numpy()
    for name in SENSORS:
        # Recent drift: last-7-day average minus last-30-day average (e.g. falling efficiency < 0).
        feats[f"{name}_drift7v30"] = feats[f"{name}_mean7"] - feats[f"{name}_mean30"]

    # Static / slowly changing context.
    feats["well_age_years"] = (d["production_date"] - d["on_production_date"]).dt.days / 365.25
    feats["pump_displacement"] = d["pcp_pump_displacement_m3d100rpm"]
    for col in ["pcp_pump_make", "pcp_pump_model", "field_operating_area", "facility_name"]:
        feats[col] = d[col]

    # Event history, strictly before day t.
    last_fail = _asof(d, failures, "failure_start_date", [])
    feats["days_since_last_failure"] = (d["production_date"] - last_fail["failure_start_date"]).dt.days
    feats["failures_prev_365"] = _count_prev(d, failures, "failure_start_date")
    last_job = _asof(d, jobs, "start_date", ["job_category"] + JOB_FLAGS)
    feats["days_since_last_job"] = (d["production_date"] - last_job["start_date"]).dt.days
    feats["jobs_prev_365"] = _count_prev(d, jobs, "start_date")
    feats["last_job_category"] = last_job["job_category"].astype("category")
    for c in JOB_FLAGS:
        feats[f"last_job_{c}"] = last_job[c].astype("float32")

    # Run life: days since the last rod/pump/tubing repair (the industry's standard reliability measure),
    # and recent downtime of any equipment kind (from the daily data itself, up to day t-1).
    repairs = jobs[jobs["job_category"].isin(["Rod Truck", "Workover"])]
    last_repair = _asof(d, repairs, "start_date", [])
    feats["run_life_days"] = (d["production_date"] - last_repair["start_date"]).dt.days
    feats["repairs_prev_365"] = _count_prev(d, repairs, "start_date")
    if "eq_down_hours" in d:
        down = d[["uwi", "production_date", "eq_down_hours"]].set_index("production_date")
        for win in (30, 90):
            feats[f"eq_down_hours_prev{win}"] = (down.groupby("uwi", sort=True)["eq_down_hours"]
                                                 .rolling(f"{win}D", closed="left").sum().to_numpy())

    num = feats.select_dtypes("number").columns
    feats[num] = feats[num].astype("float32")
    return feats
