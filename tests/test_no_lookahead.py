"""No look-ahead: scoring day D must not change when anything after D (or any job/failure on or after D) changes.

Uses 3 real wells from the cached data and the real model, so it needs the competition files and
models/risk_7d* (run python -m wellwatch.train first). Takes ~20 s.
"""

import numpy as np
import pandas as pd
import pytest

from wellwatch import config, data
from wellwatch.score import RiskModel, historian_view, score_days, well_registry

DAY = pd.Timestamp("2026-06-15")


@pytest.fixture(scope="module")
def inputs():
    d = data.oil_producers()
    running = d[(d["production_date"] == DAY) & (d["hours_down"] == 0)]["uwi"].unique()[:3]
    frame = historian_view(d[d["uwi"].isin(running)
                             & d["production_date"].between(DAY - pd.Timedelta(days=100), DAY + pd.Timedelta(days=10))])
    return d, frame.reset_index(drop=True), data.jobs(), data.failures(), RiskModel()


def test_future_changes_do_not_change_day_d(inputs):
    d, frame, jobs, fails, model = inputs
    reg = well_registry(DAY, d)
    base = score_days(frame, [DAY], model, jobs, fails, reg).sort_values("uwi").reset_index(drop=True)
    assert len(base) == 3

    changed = frame.copy()
    future = changed["production_date"] > DAY
    sensor_cols = [c for c, *_ in config.SIGNALS.values() if c in changed and c != "hours_down"]
    changed.loc[future, sensor_cols] = changed.loc[future, sensor_cols] + 10_000
    changed.loc[future, "hours_down"] = 24.0
    # day-D sensor values are not inputs for day D either (features use t-1 and earlier)
    changed.loc[changed["production_date"] == DAY, sensor_cols] += 5_000
    uwis = frame["uwi"].unique()
    jobs2 = pd.concat([jobs, pd.DataFrame({"uwi": uwis, "start_date": DAY, "job_category": "Rod Truck",
                                           "job_type": "Rod Repair", "field_est_total": 1.0})], ignore_index=True)
    fails2 = pd.concat([fails, pd.DataFrame({"uwi": uwis, "failure_start_date": DAY + pd.Timedelta(days=1)})],
                       ignore_index=True)
    after = score_days(changed, [DAY], model, jobs2, fails2, reg).sort_values("uwi").reset_index(drop=True)
    np.testing.assert_allclose(base["risk"].to_numpy(), after["risk"].to_numpy(), rtol=0, atol=1e-12)
    assert base["driver"].tolist() == after["driver"].tolist()


def test_past_change_does_change_day_d(inputs):
    """Sanity check that the test above can fail: changing the past must move the score."""
    d, frame, jobs, fails, model = inputs
    reg = well_registry(DAY, d)
    base = score_days(frame, [DAY], model, jobs, fails, reg).sort_values("uwi")["risk"].to_numpy()
    uwis = frame["uwi"].unique()
    jobs2 = pd.concat([jobs, pd.DataFrame({"uwi": uwis, "start_date": DAY - pd.Timedelta(days=3),
                                           "job_category": "Rod Truck", "job_type": "Rod Repair",
                                           "field_est_total": 1.0})], ignore_index=True)
    moved = score_days(frame, [DAY], model, jobs2, fails, reg).sort_values("uwi")["risk"].to_numpy()
    assert not np.allclose(base, moved)
