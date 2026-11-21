"""Access to the competition data, cleaned with the shared rules in datasets/data_utils.py.

The cleaned oil-producer table is cached as a parquet file in cache/ (git-ignored) because
loading and cleaning the 5M-row daily file takes about a minute.
"""

from __future__ import annotations

import sys

import pandas as pd

from . import config
from .downtime import classify_code

sys.path.insert(0, str(config.REPO_ROOT / "datasets"))
import data_utils as du  # noqa: E402

CACHE = config.REPO_ROOT / "cache"
OIL_FLUID_TYPES = ["Heavy Oil", "Oil"]
DAILY_COLS = [
    "uwi", "production_date", "flow_direction", "well_daily_status", "primary_fluid_type",
    "field_operating_area", "facility_name", "on_production_date", "surface_latitude", "surface_longitude",
    "hours_down", "lost_oil_m3", "downtime_code_2", "downtime_code_3",
    "oil_produced_m3", "water_produced_m3", "gas_produced_e3m3", "pump_efficiency_pct", "pcp_pump_rpm",
    "pcp_pump_rod_torque_ftlb", "pcp_pump_electric_current_a", "tubing_pressure_kpa", "casing_pressure_kpa",
    "pcp_pump_volume_throughput_m3d", "pcp_pump_displacement_m3d100rpm", "pcp_pump_make", "pcp_pump_model",
]


def oil_producers(refresh: bool = False) -> pd.DataFrame:
    """Cleaned daily rows for oil-producing wells, one row per (uwi, day), with downtime problem/class."""
    path = CACHE / "oil_producers_clean.parquet"
    if path.exists() and not refresh:
        d = pd.read_parquet(path)
        if "eq_down_hours" not in d:
            d["eq_down_hours"] = d["hours_down"].where(d["downtime_class"] == "Equipment failure", 0.0)
        return d
    d = du.producers_only(du.load_daily(DAILY_COLS))
    d = du.clean_daily(du.convert_types(d), verbose=False)
    d = d[d["primary_fluid_type"].isin(OIL_FLUID_TYPES)]
    d = (d.sort_values(["uwi", "production_date", "hours_down"], ascending=[True, True, False])
          .drop_duplicates(["uwi", "production_date"]).reset_index(drop=True))
    pairs = d.loc[d["hours_down"] > 0, ["downtime_code_2", "downtime_code_3"]].astype("string").drop_duplicates()
    mapping = {(a, b): classify_code(a, b) for a, b in pairs.itertuples(index=False)}
    keys = list(zip(d["downtime_code_2"].astype("string"), d["downtime_code_3"].astype("string")))
    problem, cls = zip(*[mapping.get(k, ("No downtime", "No downtime")) for k in keys])
    d["problem"], d["downtime_class"] = list(problem), list(cls)
    d.loc[d["hours_down"] <= 0, ["problem", "downtime_class"]] = "No downtime"
    d["problem"] = d["problem"].astype("category")
    d["downtime_class"] = d["downtime_class"].astype("category")
    d["eq_down_hours"] = d["hours_down"].where(d["downtime_class"] == "Equipment failure", 0.0)
    CACHE.mkdir(exist_ok=True)
    d.to_parquet(path)
    return d


def jobs() -> pd.DataFrame:
    return du.extract_summary_flags(du.clean_jobs(du.convert_types(du.load_jobs()), verbose=False), verbose=False)


def failures() -> pd.DataFrame:
    return du.clean_failures(du.extract_failure_depth(du.convert_types(du.load_failures()), verbose=False),
                             verbose=False)
