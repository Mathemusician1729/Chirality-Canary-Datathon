"""Parity check: features built from Canary data must match features built from the source files.

Run after the backfill: python -m wellwatch.check_parity
"""
import sys, os
import numpy as np, pandas as pd
from wellwatch import data
from wellwatch.canary import CanaryReader
from wellwatch.score import read_window, well_registry, historian_view, RiskModel
from wellwatch.features import build_features
day = pd.Timestamp("2026-06-30"); start = day - pd.Timedelta(days=100)
d = data.oil_producers(); jobs, fails = data.jobs(), data.failures()
reg = well_registry(day, d)
running = d[(d.production_date == day) & (d.hours_down == 0)].uwi.unique()[:40]
wells = reg[reg.uwi.isin(running)]
canary = read_window(CanaryReader(), wells, start, day)
canary = canary.merge(reg, on="uwi", how="left").sort_values(["uwi", "production_date"]).reset_index(drop=True)
files = historian_view(d[d.uwi.isin(running) & d.production_date.between(start, day)]).reset_index(drop=True)
m = RiskModel()
fc = build_features(canary, jobs, fails)[m.features]; ff = build_features(files, jobs, fails)[m.features]
kc = canary[["uwi", "production_date"]]; kf = files[["uwi", "production_date"]]
a = pd.concat([kc, fc], axis=1).query("production_date == @day").set_index("uwi").sort_index()
b = pd.concat([kf, ff], axis=1).query("production_date == @day").set_index("uwi").sort_index()
num = [c for c in m.features if c in a.select_dtypes("number").columns]
diff = (a[num] - b[num]).abs()
rel = diff / b[num].abs().clip(lower=1e-6)
bad = (rel > 0.01) & (diff > 1e-3)
print(f"wells compared: {len(a)}; numeric features: {len(num)}")
print(f"feature values matching within 1%: {1 - bad.to_numpy().mean():.2%}")
print("features with mismatches:", bad.sum()[bad.sum() > 0].sort_values(ascending=False).head(8).to_dict())
pa, pb = m.predict(a.reset_index()[m.features]), m.predict(b.reset_index()[m.features])
print(f"risk from Canary vs files: max abs diff {np.abs(pa - pb).max():.4f}, correlation {np.corrcoef(pa, pb)[0, 1]:.4f}")
