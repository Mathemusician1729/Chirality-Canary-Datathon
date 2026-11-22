"""Settings for WellWatch. Nothing secret lives here.

The Canary API token is stored in Windows Credential Manager (service "wellwatch-canary",
user "api_token") and read at runtime by wellwatch.canary.get_token().
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Packaged app (PyInstaller): code, models and the oil-price table ship inside the bundle; the data folder
# (competition parquet files, cache/, logs/, outputs/) is the repository the exe sits in, or WELLWATCH_HOME.
FROZEN = bool(getattr(sys, "frozen", False))


def _find_home() -> Path:
    env = os.environ.get("WELLWATCH_HOME")
    if env:
        return Path(env).resolve()
    if not FROZEN:
        return Path(__file__).resolve().parent.parent
    exe_dir = Path(sys.executable).resolve().parent
    for p in (exe_dir, *exe_dir.parents):  # e.g. <repo>/dist/WellWatch/WellWatch.exe -> <repo>
        if (p / "daily_production.parquet").exists() or (p / "cache" / "alerts.db").exists():
            return p
    return exe_dir


REPO_ROOT = _find_home()
BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", REPO_ROOT))


def resource(rel: str) -> Path:
    """A shipped file (models/, data/external/): the data folder's copy if present, else the bundled one."""
    p = REPO_ROOT / rel
    return p if p.exists() else BUNDLE_ROOT / rel


def module_cmd(module: str, *args: str) -> list[str]:
    """Command line that runs a WellWatch module's main(), from source or from the packaged exe."""
    if FROZEN:
        return [sys.executable, "--module", module, *args]
    return [sys.executable, "-m", module, *args]

# ---- Canary connection (local install by default; override with environment variables)
CANARY_HOST = os.environ.get("WELLWATCH_CANARY_HOST", "localhost")
READ_API_PORT = int(os.environ.get("WELLWATCH_READ_PORT", "55236"))   # Views service, HTTPS
WRITE_API_PORT = int(os.environ.get("WELLWATCH_WRITE_PORT", "55293"))  # Store & Forward service, HTTPS
# Canary installs a self-signed certificate on localhost. Set to a CA bundle path to verify.
TLS_VERIFY: bool | str = os.environ.get("WELLWATCH_TLS_VERIFY", "false").lower() not in ("false", "0", "no")
KEYRING_SERVICE, KEYRING_USER = "wellwatch-canary", "api_token"
READ_WORKERS = int(os.environ.get("WELLWATCH_READ_WORKERS", "8"))  # parallel Read API requests
CLIENT_ID = "wellwatch"

# ---- Canary naming: <DataSet>.<Field>.<UWI>.<Signal>
DATASET_RAW = "Wells"          # historical/live well data (one value per day)
DATASET_MODEL = "WellWatch"    # model outputs written back: risk, expected loss, alerts, plan

# One value per production day, stamped at a fixed UTC time so AB/SK time zones and daylight
# saving never move a value to a different day (12:00 UTC = 06:00 in Saskatchewan).
DAILY_STAMP_UTC_HOUR = 12

# Signals written per well: tag name -> (cleaned column, description, units)
SIGNALS = {
    "OilRate":        ("oil_produced_m3", "Oil produced that day", "m3/d"),
    "WaterRate":      ("water_produced_m3", "Water produced that day", "m3/d"),
    "GasRate":        ("gas_produced_e3m3", "Gas produced that day", "e3m3/d"),
    "HoursDown":      ("hours_down", "Hours not running that day", "h"),
    "LostOil":        ("lost_oil_m3", "Operator estimate of oil lost to downtime", "m3"),
    "PumpEfficiency": ("pump_efficiency_pct", "Actual vs rated pump volume (fraction)", "fraction"),
    "PumpSpeed":      ("pcp_pump_rpm", "PCP rotor speed", "rpm"),
    "RodTorque":      ("pcp_pump_rod_torque_ftlb", "Torque on the rod string", "ft-lb"),
    "MotorCurrent":   ("pcp_pump_electric_current_a", "Drive motor current", "A"),
    "TubingPressure": ("tubing_pressure_kpa", "Tubing pressure", "kPa"),
    "CasingPressure": ("casing_pressure_kpa", "Casing pressure", "kPa"),
    "EquipmentDownHours": ("eq_down_hours", "Hours down that day because of an equipment failure", "h"),
}

# Model outputs written back to Canary per well: WellWatch.<Field>.<UWI>.<Output>
OUTPUTS = {
    "FailureRisk7d": "Calibrated probability that an equipment failure starts within 7 days",
    "RiskRank": "Rank of this well's risk among all running wells that day (1 = highest)",
    "ExpectedLoss7d": "Risk x expected cost of one crew-needing failure (repair + lost oil), C$",
    "AlertLevel": "0 none, 1 watch, 2 warning, 3 critical (ISA-18.2 style priorities)",
    "TopDriver": "Main reason the model flags this well, in plain words",
}
FLEET_PATH = f"{DATASET_MODEL}.Fleet"  # fleet-wide KPI tags
# Text tags (string values) per well
TEXT_SIGNALS = {
    "DowntimeReason": "Downtime problem class that day (WellWatch classification)",
    "Job": "Service job that started that day (category / type)",
    "Failure": "Failure recorded that day (component / cause)",
}

# Fields loaded first: the three with the most equipment-failure lost oil (see outputs/3_1_pareto_field_area.csv)
DEFAULT_FIELDS = ["Cactus Lake", "Winter", "Druid Main"]


def tag_path(field: str, uwi: str, signal: str, dataset: str = DATASET_RAW) -> str:
    """Canary tag name. Dots separate levels, so they are stripped from the parts."""
    clean = lambda s: str(s).replace(".", "_").strip()
    return f"{dataset}.{clean(field)}.{clean(uwi)}.{signal}"
