"""Shared loading and cleaning helpers for the Chirality x Canary datathon data.

The three parquet files are expected in the repository root (one level above this
``datasets/`` folder). They are git-ignored; copy them there before running anything.

Every cleaning function returns a NEW DataFrame and never drops rows: bad values are
set to NaN so that the rest of the row (dates, volumes, other sensors) is kept.
Each function prints what it changed so the notebooks show an audit trail.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent

FILES = {
    "daily": "daily_production.parquet",
    "jobs": "well_jobs.parquet",
    "failures": "production_failures.parquet",
}


# --------------------------------------------------------------------------- loading
def _path(key: str) -> Path:
    path = REPO_ROOT / FILES[key]
    if not path.exists():
        raise FileNotFoundError(
            f"Expected {FILES[key]} at {path}. Copy the competition parquet files into "
            f"the repository root ({REPO_ROOT})."
        )
    return path


def load_daily(columns: list[str] | None = None) -> pd.DataFrame:
    """Raw daily production (well x day). Pass ``columns`` to save memory (5.08M rows)."""
    return pd.read_parquet(_path("daily"), columns=columns)


def load_jobs() -> pd.DataFrame:
    """Raw well jobs (one row per job)."""
    return pd.read_parquet(_path("jobs"))


def load_failures() -> pd.DataFrame:
    """Raw production failures (one row per failure)."""
    return pd.read_parquet(_path("failures"))


# --------------------------------------------------------------------------- types
DATE_COLUMNS = [
    "production_date", "on_production_date",            # daily
    "failure_start_date", "failure_end_date",           # failures
    "start_date", "end_date",                           # jobs
]
NUMERIC_TEXT_COLUMNS = ["surface_latitude", "surface_longitude", "pcp_pump_rpm"]


def convert_types(df: pd.DataFrame, max_categories: int = 1000) -> pd.DataFrame:
    """Dates -> datetime, numeric-looking text -> numbers, low-cardinality text -> category.

    Prints how many non-null values could not be converted for each column.
    """
    out = df.copy()
    for col in DATE_COLUMNS:
        if col in out.columns:
            before = out[col].notna().sum()
            # one resolution for every file, so dates from different files can be merged
            out[col] = pd.to_datetime(out[col], errors="coerce").astype("datetime64[ns]")
            failed = before - out[col].notna().sum()
            print(f"  {col:<32} -> datetime ({failed:,} values could not be parsed)")
    for col in NUMERIC_TEXT_COLUMNS:
        if col in out.columns:
            before = out[col].notna().sum()
            out[col] = pd.to_numeric(out[col], errors="coerce")
            failed = before - out[col].notna().sum()
            print(f"  {col:<32} -> numeric  ({failed:,} values could not be parsed)")
    for col in out.columns:
        if (pd.api.types.is_string_dtype(out[col]) or out[col].dtype == object) and col != "uwi":
            if out[col].nunique(dropna=True) <= max_categories:
                out[col] = out[col].astype("category")
    return out


# --------------------------------------------------------------------------- cleaning
# Rules are documented here so they can be quoted in the methods write-up.
# Thresholds were chosen from the full-data distribution (see downtime_drivers.ipynb):
#   * pressures: 999999 / -1 / -13.79 kPa are logger placeholders (-13.79 kPa = -2 psi);
#     any negative pressure is physically meaningless for these gauges.
#   * pump_efficiency_pct is a 0-1 decimal (median 0.69). Values up to ~1.5 occur when
#     metering and rated displacement disagree; values > 1.5 (max 91.4) are not percent
#     (their median is 3.3, which would be 0.03 as a percent) so they are set to NaN.
#   * choke_open_pct is on a 0-100 scale (215k rows are exactly 100); > 100 is invalid.
#   * PCP rotor speed above 1,000 rpm is not physically plausible (99th pct = 453).
#   * remaining heavy tails (torque, current, hydraulic/casing/injection/tubing pressure,
#     throughput) are cut at the 99.9th percentile of the non-placeholder values.
PRESSURE_COLUMNS = [
    "tubing_pressure_kpa", "casing_pressure_kpa", "injection_pressure_kpa",
    "hydraulic_pressure_psi",
]
PLACEHOLDER_VALUES = {
    "tubing_pressure_kpa": [999999, -1, -13.79],
    "casing_pressure_kpa": [-1],
    "injected_polymer_m3": [999, -1],
    "hydraulic_pressure_psi": [999],
}
RANGE_RULES = {  # column: (min_valid, max_valid); outside -> NaN
    "pump_efficiency_pct": (0.0, 1.5),
    "choke_open_pct": (0.0, 100.0),
    "pcp_pump_rpm": (0.0, 1000.0),
    "injected_polymer_m3": (0.0, None),
    "oil_produced_m3": (0.0, None),
    "water_produced_m3": (0.0, None),
    "gas_produced_e3m3": (0.0, None),
}
QUANTILE_CAP_COLUMNS = [
    "pcp_pump_rod_torque_ftlb", "pcp_pump_electric_current_a", "hydraulic_pressure_psi",
    "casing_pressure_kpa", "injection_pressure_kpa", "tubing_pressure_kpa",
    "pcp_pump_volume_throughput_m3d",
]
QUANTILE_CAP = 0.999


def clean_daily(df: pd.DataFrame, verbose: bool = True, return_report: bool = False):
    """Set placeholders and physically impossible daily values to NaN (rows are kept).

    Also fills ``lost_oil_m3`` with 0 on days with no downtime: in the raw data the
    column is NaN on 99.9% of zero-downtime days and populated on 98.5% of downtime days,
    i.e. NaN means "no downtime, nothing lost". Downtime days with no loss estimate are
    left NaN (they are an unrecorded value, not a zero).

    With ``return_report=True`` returns ``(cleaned_df, report_df)``.
    """
    out = df.copy()
    log = []

    def record(rule, col, mask):
        n = int(mask.sum())
        log.append({"column": col, "rule": rule, "values_set_to_nan": n,
                    "non_null_before": int(out[col].notna().sum())})
        out.loc[mask, col] = np.nan

    for col, values in PLACEHOLDER_VALUES.items():
        if col in out.columns:
            record(f"placeholder in {values}", col, out[col].isin(values))
    for col in PRESSURE_COLUMNS:
        if col in out.columns:
            record("negative pressure", col, out[col] < 0)
    for col, (lo, hi) in RANGE_RULES.items():
        if col in out.columns:
            mask = pd.Series(False, index=out.index)
            if lo is not None:
                mask |= out[col] < lo
            if hi is not None:
                mask |= out[col] > hi
            record(f"outside [{lo}, {hi}]", col, mask)
    for col in QUANTILE_CAP_COLUMNS:
        if col in out.columns and out[col].notna().any():
            cap = out[col].quantile(QUANTILE_CAP)
            record(f"> {QUANTILE_CAP:.1%} quantile ({cap:,.1f})", col, out[col] > cap)

    if {"lost_oil_m3", "hours_down"} <= set(out.columns):
        fill = out["lost_oil_m3"].isna() & (out["hours_down"] == 0)
        out.loc[fill, "lost_oil_m3"] = 0.0
        unrecorded = int((out["lost_oil_m3"].isna() & (out["hours_down"] > 0)).sum())
        log.append({"column": "lost_oil_m3", "rule": "NaN -> 0 where hours_down == 0",
                    "values_set_to_nan": -int(fill.sum()), "non_null_before": None})
        if verbose:
            print(f"lost_oil_m3: filled {int(fill.sum()):,} zero-downtime days with 0; "
                  f"{unrecorded:,} downtime days have no loss estimate (left NaN).")

    report = pd.DataFrame(log)
    if verbose and len(report):
        print(report.to_string(index=False))
    return (out, report) if return_report else out


def clean_jobs(df: pd.DataFrame, verbose: bool = True) -> pd.DataFrame:
    """Negative job durations and negative cost estimates -> NaN (rows kept)."""
    out = df.copy()
    for col in ["job_duration_days", "field_est_total"]:
        if col in out.columns:
            mask = out[col] < 0
            if verbose:
                print(f"{col}: {int(mask.sum())} negative values -> NaN")
            out.loc[mask, col] = np.nan
    return out


FAILURE_TYPE_MAP = {  # free-text failure_type -> standard component
    "rods": "Rods", "rod": "Rods", "|rod": "Rods", "pared rod": "Rods", "parted rod": "Rods",
    "broken": "Rods", "broken threads": "Rods", "parted/ backed off polish rod": "Rods",
    "pump": "Pump", "pcp": "Pump", "es- pcp": "Pump", "broken insert": "Pump",
    "tubing": "Tubing", "parted tubing broke": "Tubing", "parted tbg": "Tubing",
    "hole in tubing": "Tubing", "hole in tbg colar": "Tubing", "tubing parted": "Tubing",
    "packer": "Packer", "packer  repair": "Packer", "hole in mandral packer": "Packer",
    "casing": "Casing", "hole in csig": "Casing",
    "surface failure": "Surface", "hole in surface pup": "Surface",
    "sanded": "Sand",
}
CAUSE_MAP = {
    "normal or expected wear and tear": "Wear and Tear",
    "normal or expected wear-and-tear": "Wear and Tear",
    "rod wear": "Wear and Tear", "unknowen": "Unknown",
    "sand": "Sand", "possible sand": "Sand",
    "ntt failed": "Other", "ntt failure": "Other",
}


def clean_failures(df: pd.DataFrame, verbose: bool = True) -> pd.DataFrame:
    """Standardise the free-text ``failure_type`` and ``cause`` spellings.

    Adds ``failure_type_std`` and ``cause_std`` (originals are kept).
    """
    out = df.copy()
    ft = out["failure_type"].astype("string").str.strip().str.lower()
    out["failure_type_std"] = ft.map(FAILURE_TYPE_MAP).fillna(
        out["failure_type"].astype("string").str.strip()).astype("category")
    cause = out["cause"].astype("string").str.strip()
    out["cause_std"] = cause.str.lower().map(CAUSE_MAP).fillna(cause).astype("category")
    if verbose:
        print("failure_type:", out["failure_type"].nunique(), "spellings ->",
              out["failure_type_std"].nunique(), "standard values")
        print("cause:", out["cause"].nunique(), "spellings ->", out["cause_std"].nunique(),
              "standard values")
    return out


# --------------------------------------------------------------------------- text features
SUMMARY_FLAGS = {
    "rod_broken": r"broke|parted",
    "stator_rotor": r"stator|rotor",
    "sand": r"sand",
    "corrosion": r"corro",
    "worn": r"worn",
    "torque": r"torque",
    "injury": r"stitch|injur|hospital",
}


def extract_summary_flags(jobs: pd.DataFrame, verbose: bool = True) -> pd.DataFrame:
    """Boolean keyword flags (``sum_<name>``) from the crew's free-text job ``summary``."""
    out = jobs.copy()
    text = out["summary"].astype("string")
    for name, pattern in SUMMARY_FLAGS.items():
        out[f"sum_{name}"] = text.str.contains(pattern, case=False, regex=True, na=False)
    if verbose:
        freq = out[[f"sum_{n}" for n in SUMMARY_FLAGS]].mean().mul(100).round(1)
        print("Share of jobs with each summary flag (%):")
        print(freq.to_string())
    return out


_DEPTH_RE = r"(?:@|at|\+/-)?\s*(\d{2,4}(?:\.\d+)?)\s*(?:m\b|mkb\b|meters?\b|metres?\b)"


def extract_failure_depth(failures: pd.DataFrame, verbose: bool = True) -> pd.DataFrame:
    """Parse a failure depth in metres from ``failure_comments`` into ``failure_depth_m``.

    Matches e.g. "Broken corod @ 268m", "broke +/- 151m corod", "@ 826 m". Comments that
    give a rod number ("107th rod") instead of a depth are not converted.
    """
    out = failures.copy()
    text = out["failure_comments"].astype("string")
    depth = text.str.extract(_DEPTH_RE, flags=2, expand=False)  # 2 = re.IGNORECASE
    out["failure_depth_m"] = pd.to_numeric(depth, errors="coerce")
    out.loc[(out["failure_depth_m"] < 5) | (out["failure_depth_m"] > 3000), "failure_depth_m"] = np.nan
    # Rod-string position when the crew counted rods instead ("90th broken rod", "rod 108").
    rod_no = text.str.extract(r"(\d{1,3})\s*(?:st|nd|rd|th)\b[^\d]{0,20}?rod|rod\s*#?\s*(\d{1,3})\b",
                              flags=2, expand=True)
    out["failure_rod_number"] = pd.to_numeric(rod_no[0].fillna(rod_no[1]), errors="coerce")
    if verbose:
        print(f"failure_rod_number parsed for {int(out['failure_rod_number'].notna().sum()):,} comments "
              "(rod count from surface; not converted to metres).")
        has_comment = text.notna().sum()
        parsed = out["failure_depth_m"].notna().sum()
        print(f"failure_depth_m parsed for {parsed:,} of {has_comment:,} comments "
              f"({parsed / max(has_comment, 1):.1%}); {parsed / len(out):.1%} of all failures.")
    return out


# --------------------------------------------------------------------------- filters
def producers_only(daily: pd.DataFrame) -> pd.DataFrame:
    """Rows where ``flow_direction == 'production'`` (injectors excluded)."""
    mask = daily["flow_direction"] == "production"
    print(f"producers_only: kept {int(mask.sum()):,} of {len(daily):,} rows")
    return daily.loc[mask].copy()
