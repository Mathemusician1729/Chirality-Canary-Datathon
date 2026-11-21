"""Train the week-ahead failure-risk model.

Target: for an oil well that is running on day t (no downtime that day), does a FAILURE THAT NEEDS A
CREW start within days t+1..t+7?  A failure = an equipment-failure downtime episode (rods, pump, tubing,
torqued, wellhead, motor... see wellwatch/downtime.py) with a Rod Truck or Workover job starting
between 1 day before and 7 days after the episode starts. One-day stoppages that never needed a crew
are not counted: they are frequent, noisy and cheap.

Split (chronological, with a 7-day gap so labels never overlap):
    train      2021-01-01 .. 2024-12-24
    calibrate  2025-01-01 .. 2025-06-23   (isotonic calibration of probabilities)
    test       2025-07-01 .. 2026-06-23   (reported once)
    July-October 2026 is never used: it is the live-replay demo period.

Run: python -m wellwatch.train     -> models/risk_7d.json (+ risk_7d_meta.json, risk_7d_calibration.json)
"""

from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import average_precision_score, roc_auc_score

from . import config, data
from .features import build_features
from .score import historian_view

HORIZON = 7
MODELS = config.REPO_ROOT / "models"
SPLITS = {"train": ("2021-01-01", "2024-12-24"), "calibrate": ("2025-01-01", "2025-06-23"),
          "test": ("2025-07-01", "2026-06-23")}
EQUIPMENT = "Equipment failure"
REPAIR_CATEGORIES = ["Rod Truck", "Workover"]
# High-cardinality labels (facility, pump model...) overfit; pump displacement, history and sensors carry the signal.
DROP_FEATURES = ["facility_name", "pcp_pump_model", "field_operating_area", "last_job_category", "pcp_pump_make"]
ALERTS_PER_DAY = 15  # what the field crews can realistically check each day


def add_labels(d: pd.DataFrame, jobs: pd.DataFrame) -> pd.DataFrame:
    """Label day t with 1 if a crew-needing equipment failure starts in t+1..t+7 (same well)."""
    d = d.sort_values(["uwi", "production_date"]).reset_index(drop=True)
    eq = (d["downtime_class"] == EQUIPMENT)
    prev_eq = eq.groupby(d["uwi"]).shift(1, fill_value=False)
    start = eq & ~prev_eq
    d["eq_down_hours"] = d["hours_down"].where(eq, 0.0)
    starts = d.loc[start, ["uwi", "production_date"]].rename(columns={"production_date": "s"})
    rep = jobs[jobs["job_category"].isin(REPAIR_CATEGORIES)][["uwi", "start_date"]]
    linked = starts.merge(rep, on="uwi")
    linked = linked[(linked["start_date"] >= linked["s"] - pd.Timedelta(days=1))
                    & (linked["start_date"] <= linked["s"] + pd.Timedelta(days=7))]
    starts = linked.drop_duplicates(["uwi", "s"])[["uwi", "s"]].rename(columns={"s": "production_date"})
    # for each row, the first episode start strictly after day t
    s = starts.rename(columns={"production_date": "next_start"}).sort_values("next_start")
    left = d[["uwi", "production_date"]].reset_index().sort_values("production_date")
    m = pd.merge_asof(left, s, left_on="production_date", right_on="next_start", by="uwi",
                      direction="forward", allow_exact_matches=False).set_index("index").sort_index()
    gap = (m["next_start"] - d["production_date"]).dt.days
    d["label"] = (gap <= HORIZON).astype("int8")
    d["days_to_failure"] = gap
    return d


def prepare():
    d = data.oil_producers()
    jobs, fails = data.jobs(), data.failures()
    d = historian_view(add_labels(d, jobs))   # train on values the way Canary serves them
    feats = build_features(d, jobs, fails).drop(columns=DROP_FEATURES)
    running = d["hours_down"] == 0
    keep = running & d["production_date"].between(SPLITS["train"][0], SPLITS["test"][1])
    return feats.loc[keep], d.loc[keep, ["uwi", "production_date", "field_operating_area", "label"]]


def precision_at_k(scores: np.ndarray, y: np.ndarray, dates: pd.Series, k: int) -> float:
    df = pd.DataFrame({"s": scores, "y": y, "d": dates.to_numpy()})
    top = df.sort_values("s", ascending=False).groupby("d").head(k)
    return float(top["y"].mean())


def evaluate(name, scores, y, dates) -> dict:
    return {"model": name, "PR-AUC": average_precision_score(y, scores), "ROC-AUC": roc_auc_score(y, scores),
            f"precision@{ALERTS_PER_DAY}/day": precision_at_k(scores, y, dates, ALERTS_PER_DAY),
            "base rate": float(np.mean(y))}


def main() -> None:
    t0 = time.time()
    X, info = prepare()
    cats = list(X.select_dtypes("category").columns)
    feats = list(X.columns)
    part = {k: info["production_date"].between(a, b) for k, (a, b) in SPLITS.items()}
    print(f"rows: " + ", ".join(f"{k} {int(v.sum()):,} (positives {info.loc[v, 'label'].mean():.2%})"
                                  for k, v in part.items()) + f"  [{time.time() - t0:.0f}s]")

    tr, ca, te = part["train"], part["calibrate"], part["test"]
    pos = info.loc[tr, "label"].mean()
    # Shallow, heavily regularised trees: a deeper, class-weighted model overfit and lost to simple rules.
    model = xgb.XGBClassifier(
        n_estimators=300, learning_rate=0.03, max_depth=4, min_child_weight=200, subsample=0.8,
        colsample_bytree=0.6, reg_lambda=5, tree_method="hist", enable_categorical=True, max_cat_to_onehot=1,
        n_jobs=-1, random_state=42)
    model.fit(X.loc[tr], info.loc[tr, "label"])
    raw_ca = model.predict_proba(X.loc[ca])[:, 1]
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(raw_ca, info.loc[ca, "label"])
    p_te = iso.predict(model.predict_proba(X.loc[te])[:, 1])
    y_te, d_te = info.loc[te, "label"].to_numpy(), info.loc[te, "production_date"]

    # Baselines: (1) run life only, (2) recent equipment downtime, (3) the well's historical failure rate.
    base_runlife = -X.loc[te, "run_life_days"].fillna(10_000).to_numpy()
    base_recent = X.loc[te, "eq_down_hours_prev30"].fillna(0).to_numpy()
    rate = info.loc[tr].groupby("uwi")["label"].mean()
    base_rate = info.loc[te, "uwi"].map(rate).fillna(pos).to_numpy()
    scores = pd.DataFrame([evaluate("WellWatch model (calibrated)", p_te, y_te, d_te),
                           evaluate("Baseline: well's historical rate", base_rate, y_te, d_te),
                           evaluate("Baseline: equipment downtime in last 30 days", base_recent, y_te, d_te),
                           evaluate("Baseline: shortest run life since last repair", base_runlife, y_te, d_te)])
    print(scores.to_string(index=False, float_format=lambda v: f"{v:.3f}"))

    # Calibration check on the test year: predicted vs observed by decile.
    bins = pd.qcut(p_te, 10, duplicates="drop")
    calib = pd.DataFrame({"p": p_te, "y": y_te}).groupby(bins, observed=True).mean()
    print("\ncalibration (test): mean predicted vs observed per decile\n", calib.round(3).to_string())

    MODELS.mkdir(exist_ok=True)
    model.save_model(MODELS / "risk_7d.json")
    (MODELS / "risk_7d_calibration.json").write_text(json.dumps(
        {"x": iso.X_thresholds_.tolist(), "y": iso.y_thresholds_.tolist()}))
    imp = pd.Series(model.get_booster().get_score(importance_type="gain")).sort_values(ascending=False)
    (MODELS / "risk_7d_meta.json").write_text(json.dumps({
        "horizon_days": HORIZON, "splits": SPLITS, "features": feats, "categorical": cats,
        "categories": {c: [str(v) for v in X[c].cat.categories] for c in cats},
        "train_positive_rate": pos, "test_scores": scores.to_dict(orient="records"),
        "top_features_gain": imp.head(25).round(2).to_dict(), "trained": pd.Timestamp.now().isoformat(),
    }, indent=2))
    print(f"\nsaved model to {MODELS} [{time.time() - t0:.0f}s]")


if __name__ == "__main__":
    main()
