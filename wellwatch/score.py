"""Daily risk engine: read from Canary -> build features -> 7-day failure risk -> write back to Canary.

For a scoring day t (after day t's data has arrived), every well that ran all of day t gets:
    FailureRisk7d   calibrated probability that an equipment failure starts in t+1..t+7
    ExpectedLoss7d  risk x expected cost of one crew-needing failure (repair + lost oil), C$
    RiskRank, AlertLevel, TopDriver
written to Canary as WellWatch.<Field>.<UWI>.<Output>, stamped on day t.

Time series come from the Canary Historian (daily time-weighted averages, which is how the
historian serves values that are only stored when they change). Event history (service jobs,
failures) and static well attributes come from the maintenance records (the competition files),
as they would from a maintenance system in production.

    python -m wellwatch.score --day 2026-06-30            # score one day from Canary
"""

from __future__ import annotations

import argparse
import json
import time

import numpy as np
import pandas as pd
import xgboost as xgb

from . import config, data
from .canary import CanaryReader, CanaryWriter
from .features import build_features

MODELS = config.resource("models")
WINDOW_DAYS = 100          # history needed: 90-day downtime sums + 30-day rolling windows
READ_BATCH_TAGS = 250
NUMERIC_SIGNALS = list(config.SIGNALS)
REPAIR_COST_CAD = 2_846.0  # median Rod Truck job field estimate (well_jobs.field_est_total)


def historian_view(d: pd.DataFrame) -> pd.DataFrame:
    """Forward-fill sensor values per well, as the historian serves them (values are stored on change)."""
    cols = [c for c, *_ in config.SIGNALS.values() if c in d]
    d = d.sort_values(["uwi", "production_date"]).copy()
    d[cols] = d.groupby("uwi", observed=True)[cols].ffill()
    return d


class RiskModel:
    def __init__(self):
        self.meta = json.loads((MODELS / "risk_7d_meta.json").read_text())
        cal = json.loads((MODELS / "risk_7d_calibration.json").read_text())
        self.cal_x, self.cal_y = np.array(cal["x"]), np.array(cal["y"])
        self.booster = xgb.Booster()
        self.booster.load_model(MODELS / "risk_7d.json")
        self.features = self.meta["features"]

    def _matrix(self, X: pd.DataFrame) -> xgb.DMatrix:
        X = X[self.features].copy()
        for c, cats in self.meta["categories"].items():
            X[c] = pd.Categorical(X[c].astype("string"), categories=cats)
        return xgb.DMatrix(X, enable_categorical=True)

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        raw = self.booster.predict(self._matrix(X))
        return np.interp(raw, self.cal_x, self.cal_y)

    def drivers(self, X: pd.DataFrame, top: int = 2) -> list[str]:
        """Plain-language top reasons per row, from SHAP contributions (positive = raises risk)."""
        contrib = self.booster.predict(self._matrix(X), pred_contribs=True)[:, :-1]
        names = np.array(self.features)
        out = []
        for row, values in zip(contrib, X[self.features].itertuples(index=False)):
            idx = np.argsort(-row)[:top]
            vals = dict(zip(self.features, values))
            parts = [describe(names[i], vals[names[i]]) for i in idx if row[i] > 0]
            out.append("; ".join(dict.fromkeys(parts)) or "No single dominant factor")
        return out


READABLE = {
    "eff": "Pump efficiency", "rpm": "Pump speed", "torque": "Rod torque", "current": "Motor current",
    "tbg_p": "Tubing pressure", "csg_p": "Casing pressure", "wcut": "Water cut", "oil": "Oil rate",
}


def describe(feature: str, value) -> str:
    """Plain-language text for a model feature and its value."""
    v = value if value == value else None  # NaN -> None
    if feature == "run_life_days":
        return f"{int(v)} days since last repair" if v is not None else "No repair on record"
    if feature == "repairs_prev_365":
        return f"{int(v or 0)} repair jobs in the past year"
    if feature == "jobs_prev_365":
        return f"{int(v or 0)} service jobs in the past year"
    if feature == "failures_prev_365":
        return f"{int(v or 0)} recorded failures in the past year"
    if feature == "days_since_last_job":
        return f"Last service job {int(v)} days ago" if v is not None else "No service job on record"
    if feature == "days_since_last_failure":
        return f"Last failure {int(v)} days ago" if v is not None else "No failure on record"
    if feature.startswith("last_job_sum_"):
        what = feature.removeprefix("last_job_sum_").replace("_", " ")
        return f"Last job report mentions {what}" if v else "Last job report"
    if feature.startswith("eq_down_hours_prev"):
        return f"{(v or 0):.0f} h of equipment downtime in the last {feature.removeprefix('eq_down_hours_prev')} days"
    if feature == "well_age_years":
        return f"Well age {v:.0f} years" if v is not None else "Well age"
    if feature == "pump_displacement":
        return f"Pump size {v:.0f} m3/d per 100 rpm" if v is not None else "Pump size"
    prefix, _, kind = feature.partition("_")
    name = READABLE.get(prefix)
    if feature.startswith("tbg_p") or feature.startswith("csg_p"):
        name, kind = READABLE[feature[:5]], feature[6:]
    if name:
        if kind == "drift7v30":
            return f"{name} {'rising' if (v or 0) > 0 else 'falling'} (last 7 vs 30 days)"
        if kind == "std7":
            return f"{name} unstable this week"
        return f"{name} level"
    return feature.replace("_", " ")


# --------------------------------------------------------------------------- Canary I/O
def well_registry(as_of: pd.Timestamp, d: pd.DataFrame | None = None) -> pd.DataFrame:
    """Latest static attributes per well up to as_of (asset register)."""
    d = data.oil_producers() if d is None else d
    d = d[d["production_date"] <= as_of]
    keep = ["uwi", "field_operating_area", "facility_name", "on_production_date", "pcp_pump_make",
            "pcp_pump_model", "pcp_pump_displacement_m3d100rpm", "surface_latitude", "surface_longitude"]
    return d.sort_values("production_date").groupby("uwi", observed=True)[keep[1:]].last().reset_index()


def existing_well_tags(reader: CanaryReader) -> set[str]:
    """All raw well tags that exist in Canary (one browse call, ~1 s). Requesting missing tags wastes time."""
    return set(reader.browse_tags(f"localhost.{config.DATASET_RAW}", deep=True))


def read_window(reader: CanaryReader, wells: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp,
                workers: int = 1, existing: set[str] | None = None) -> pd.DataFrame:
    """Daily values for production days start..end (inclusive) for every well, from Canary.

    Canary's cost is per tag per request (~9 ms), not per day of data, so read many days at once and
    use several parallel requests (``workers``). ``existing`` skips tags that do not exist.
    """
    stamp = pd.Timedelta(hours=config.DAILY_STAMP_UTC_HOUR)
    t0 = (start.tz_localize("UTC") + stamp).isoformat()
    t1 = (end.tz_localize("UTC") + stamp + pd.Timedelta(days=1)).isoformat()
    tags = {f"localhost.{config.tag_path(f, u, s)}": (u, config.SIGNALS[s][0])
            for u, f in zip(wells["uwi"], wells["field_operating_area"]) for s in NUMERIC_SIGNALS}
    names = [t for t in tags if existing is None or t in existing]
    chunks = [names[i:i + READ_BATCH_TAGS] for i in range(0, len(names), READ_BATCH_TAGS)]
    readers = [reader] + [CanaryReader() for _ in range(max(workers, 1) - 1)]

    def fetch(job):
        i, chunk = job
        return readers[i % len(readers)].get_tag_data(chunk, t0, t1, aggregate="TimeAverage2",
                                                      interval="1d", max_size=200_000)

    if workers > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(workers) as ex:
            results = list(ex.map(fetch, enumerate(chunks)))
    else:
        results = [fetch(job) for job in enumerate(chunks)]
    rows = []
    for res in results:
        for tag, points in res.items():
            uwi, col = tags[tag]
            rows += [(uwi, t, col, v) for t, v in points]
    if not rows:  # keep a real datetime column so later concatenation keeps dates as dates
        return pd.DataFrame({"uwi": pd.Series(dtype=object),
                             "production_date": pd.Series(dtype="datetime64[ns]")})
    long = pd.DataFrame(rows, columns=["uwi", "t", "col", "v"])
    # Each 1-day interval starts at the day's stamp, so its local start date is the production date.
    long["production_date"] = (pd.to_datetime(long["t"], utc=True) - stamp).dt.tz_localize(None).dt.normalize()
    wide = long.pivot_table(index=["uwi", "production_date"], columns="col", values="v", aggfunc="last").reset_index()
    wide.columns.name = None
    wide["production_date"] = wide["production_date"].astype("datetime64[ns]")
    return wide


def score_days(frame: pd.DataFrame, days, model: RiskModel, jobs, fails, registry: pd.DataFrame) -> pd.DataFrame:
    """Score wells running on each of ``days`` from one daily frame (from Canary or from files).

    Features for day t use only data up to t-1 and events dated before t, so one feature build can
    serve several days without look-ahead. ``registry`` must be built as of the earliest day.
    """
    f = frame.merge(registry, on="uwi", how="left", suffixes=("", "_reg"))
    for c in ["field_operating_area", "facility_name", "pcp_pump_make", "pcp_pump_model", "on_production_date",
              "pcp_pump_displacement_m3d100rpm"]:
        if f"{c}_reg" in f:
            f[c] = f[c].fillna(f[f"{c}_reg"]) if c in frame else f[f"{c}_reg"]
    for c, *_ in config.SIGNALS.values():
        if c not in f:
            f[c] = np.nan
    f = f.sort_values(["uwi", "production_date"]).reset_index(drop=True)
    feats = build_features(f, jobs, fails)
    today = f["production_date"].isin(pd.DatetimeIndex(days)) & (f["hours_down"].fillna(1) == 0)
    X = feats.loc[today]
    out = f.loc[today, ["uwi", "field_operating_area", "production_date"]].copy()
    if X.empty:
        return out.assign(risk=pd.Series(dtype=float), driver=pd.Series(dtype=str))
    out["risk"] = model.predict(X)
    out["driver"] = model.drivers(X)
    return out.reset_index(drop=True)


def score_frame(frame: pd.DataFrame, day: pd.Timestamp, model: RiskModel, jobs, fails,
                registry: pd.DataFrame) -> pd.DataFrame:
    """Score wells running on `day` using a daily frame (from Canary or from files)."""
    return score_days(frame, [day], model, jobs, fails, registry)


def loss_per_failure(d: pd.DataFrame, as_of: pd.Timestamp) -> pd.Series:
    """Typical C$ cost of one equipment failure per well: oil lost (well median, else field median) x value + repair."""
    hist = d[(d["production_date"] <= as_of) & (d["downtime_class"] == "Equipment failure")]
    epi = hist.assign(new=lambda x: x.groupby("uwi", observed=True)["production_date"].diff().dt.days.ne(1))
    epi["episode"] = epi["new"].cumsum()
    per = epi.groupby(["uwi", "episode"], observed=True).agg(oil=("lost_oil_m3", "sum"),
                                                             field=("field_operating_area", "first"))
    well = per.groupby("uwi", observed=True)["oil"].median()
    field = per.groupby("field", observed=True)["oil"].median()
    return well, field


def failure_costs(d: pd.DataFrame, jobs: pd.DataFrame, as_of: pd.Timestamp, shrink: float = 3.0) -> dict:
    """Expected C$ cost of ONE crew-needing failure per well, from failures known before ``as_of``.

    cost = mean repair cost (field estimates of the linked Rod Truck / Workover jobs)
         + mean oil lost per failure x the oil value of as_of's quarter.
    Well means are pulled towards the field mean (``shrink`` pseudo-failures) so wells with one or two
    failures are not over-trusted; values above the 99th percentile are capped. Only failures that
    started at least 8 days before as_of are used, so their linked repair jobs are already on record.
    (Historically a crew-needing failure averaged ~C$14.3k repair + ~C$5.4k lost oil.)
    """
    from .train import crew_failure_starts  # local import: train imports this module

    f = crew_failure_starts(d[d["production_date"] <= as_of], jobs[jobs["start_date"] <= as_of])
    f = f[f["production_date"] <= as_of - pd.Timedelta(days=8)].copy()
    for col in ("repair_cost_cad", "episode_lost_oil_m3"):
        f[col] = f[col].fillna(0).clip(upper=f[col].quantile(0.99))
    value = float(data.du.oil_value_cad_per_m3([as_of], "base")[0])
    f["cost"] = f["repair_cost_cad"] + f["episode_lost_oil_m3"] * value
    fleet = float(f["cost"].mean())
    by_field = f.groupby("field_operating_area", observed=True)["cost"].mean()
    g = f.groupby("uwi", observed=True).agg(n=("cost", "size"), mean=("cost", "mean"),
                                            field=("field_operating_area", "first"))
    prior = g["field"].map(by_field).fillna(fleet)
    well = (g["n"] * g["mean"] + shrink * prior) / (g["n"] + shrink)
    return {"well": well, "field": by_field, "fleet": fleet}


def expected_failure_cost(uwis: pd.Series, fields: pd.Series, costs: dict) -> np.ndarray:
    """Per-row expected cost of one failure: well estimate, else field mean, else fleet mean."""
    return (uwis.map(costs["well"]).fillna(fields.map(costs["field"])).fillna(costs["fleet"])).to_numpy(dtype=float)


def assign_levels(out: pd.DataFrame) -> pd.DataFrame:
    """ISA-18.2-style priorities by expected loss, capped so crews are not flooded."""
    out = out.sort_values("expected_loss", ascending=False).reset_index(drop=True)
    out["rank"] = np.arange(1, len(out) + 1)
    out["level"] = 0
    # Risk thresholds sit on the calibrated scale (fleet base rate ~3.7% per week; top decile ~11%).
    out.loc[(out["rank"] <= 50) & (out["risk"] >= 0.05), "level"] = 1   # watch
    out.loc[(out["rank"] <= 15) & (out["risk"] >= 0.08), "level"] = 2   # warning: daily crew-check budget
    out.loc[(out["rank"] <= 5) & (out["risk"] >= 0.15), "level"] = 3    # critical
    return out


def write_outputs(writer: CanaryWriter, out: pd.DataFrame, day: pd.Timestamp) -> None:
    ts = day.tz_localize("UTC") + pd.Timedelta(hours=config.DAILY_STAMP_UTC_HOUR)
    tvqs = {}
    for r in out.itertuples(index=False):
        base = lambda o: config.tag_path(r.field_operating_area, r.uwi, o, dataset=config.DATASET_MODEL)
        tvqs[base("FailureRisk7d")] = [(ts, round(float(r.risk), 4))]
        tvqs[base("RiskRank")] = [(ts, int(r.rank))]
        tvqs[base("ExpectedLoss7d")] = [(ts, round(float(r.expected_loss), 0))]
        tvqs[base("AlertLevel")] = [(ts, int(r.level))]
        tvqs[base("TopDriver")] = [(ts, str(r.driver))]
    tvqs[f"{config.FLEET_PATH}.WellsScored"] = [(ts, len(out))]
    tvqs[f"{config.FLEET_PATH}.Warnings"] = [(ts, int((out["level"] >= 2).sum()))]
    tvqs[f"{config.FLEET_PATH}.ExpectedLoss7d"] = [(ts, round(float(out["expected_loss"].sum()), 0))]
    writer.store(tvqs)


class RiskEngine:
    """Keeps a rolling window of daily data read from Canary and scores days as they arrive."""

    def __init__(self, reader: CanaryReader | None = None, workers: int = config.READ_WORKERS):
        self.reader = reader or CanaryReader()
        self.workers = workers
        self.model = RiskModel()
        self.d = data.oil_producers()
        self.jobs, self.fails = data.jobs(), data.failures()
        self.frame = pd.DataFrame()
        self.wells = pd.DataFrame(columns=["uwi", "field_operating_area"])

    def _active(self, day: pd.Timestamp) -> pd.DataFrame:
        """Wells producing in the 100 days up to ``day`` per the production records (as of ``day``)."""
        self.registry = well_registry(day, self.d)
        start = day - pd.Timedelta(days=WINDOW_DAYS)
        active = self.d[self.d["production_date"].between(start, day)]["uwi"].unique()
        return self.registry[self.registry["uwi"].isin(active)]

    def load(self, day: pd.Timestamp) -> None:
        """Read the full window ending on ``day`` (inclusive)."""
        self.wells = self._active(day)
        self.existing = existing_well_tags(self.reader)
        start = day - pd.Timedelta(days=WINDOW_DAYS)
        self.frame = read_window(self.reader, self.wells, start, day, self.workers, self.existing)

    def advance(self, day: pd.Timestamp, end: pd.Timestamp | None = None) -> None:
        """Append days ``day``..``end`` (default: just ``day``) read from Canary; drop days older than the window.

        Wells that start producing get their whole window read once.
        """
        end = end or day
        self.existing = existing_well_tags(self.reader)
        active = self._active(end)
        new_wells = active[~active["uwi"].isin(self.wells["uwi"])]
        parts = [self.frame[self.frame["production_date"] < day],
                 read_window(self.reader, self.wells, day, end, self.workers, self.existing)]
        if len(new_wells):
            parts.append(read_window(self.reader, new_wells, end - pd.Timedelta(days=WINDOW_DAYS), end,
                                     self.workers, self.existing))
            self.wells = pd.concat([self.wells, new_wells], ignore_index=True)
        parts = [p for p in parts if len(p)]
        frame = pd.concat(parts, ignore_index=True)
        frame["production_date"] = pd.to_datetime(frame["production_date"]).astype("datetime64[ns]")
        self.frame = frame.drop_duplicates(["uwi", "production_date"], keep="last")
        self.frame = self.frame[self.frame["production_date"] > end - pd.Timedelta(days=WINDOW_DAYS + 1)]

    def score_many(self, days) -> pd.DataFrame:
        """Score several days. Registry and loss estimates are taken as of the earliest day (no look-ahead)."""
        days = sorted(pd.DatetimeIndex(days))
        first = days[0]
        registry = well_registry(first, self.d)
        out = score_days(self.frame, days, self.model, self.jobs, self.fails, registry)
        if out.empty:
            return out
        costs = failure_costs(self.d, self.jobs, first - pd.Timedelta(days=1))  # known before the first day
        out["failure_cost"] = expected_failure_cost(out["uwi"], out["field_operating_area"], costs)
        out["expected_loss"] = out["risk"] * out["failure_cost"]
        return pd.concat([assign_levels(g) for _, g in out.groupby("production_date")], ignore_index=True)

    def score(self, day: pd.Timestamp) -> pd.DataFrame:
        return self.score_many([day])


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--day", required=True)
    p.add_argument("--no-write", action="store_true")
    args = p.parse_args(argv)
    day = pd.Timestamp(args.day)
    t0 = time.time()
    eng = RiskEngine()
    eng.load(day)
    print(f"read {len(eng.frame):,} well-days for {len(eng.wells):,} wells from Canary [{time.time() - t0:.0f}s]")
    out = eng.score(day)
    print(f"scored {len(out):,} running wells [{time.time() - t0:.0f}s]")
    print(out.head(10)[["uwi", "field_operating_area", "risk", "expected_loss", "level", "driver"]].to_string(index=False))
    if not args.no_write:
        with CanaryWriter() as w:
            write_outputs(w, out, day)
        print("wrote outputs to Canary")


if __name__ == "__main__":
    main()
