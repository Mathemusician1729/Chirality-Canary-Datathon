"""The replay feed must write only day D (no future values)."""

import pandas as pd

from wellwatch import config
from wellwatch.backfill import day_stamps
from wellwatch.replay import CLOCK_TAG, day_tvqs


def _frame(day):
    rows = []
    for uwi in ["W1", "W2"]:
        row = {"uwi": uwi, "field_operating_area": "Test Field", "production_date": day, "problem": "Rod string failure"}
        for col, *_ in config.SIGNALS.values():
            row[col] = 1.0
        row["hours_down"] = 0.0
        rows.append(row)
    return pd.DataFrame(rows)


def test_replay_writes_only_day_d():
    day = pd.Timestamp("2026-07-10")
    jobs = pd.DataFrame({"uwi": ["W1", "W1"], "start_date": [day, day + pd.Timedelta(days=1)],
                         "job_category": ["Rod Truck", "Workover"], "job_type": ["Rod Repair", "Pump Repair"]})
    fails = pd.DataFrame({"uwi": ["W2", "W2"], "failure_start_date": [day - pd.Timedelta(days=3), day + pd.Timedelta(days=2)],
                          "failure_type_std": ["Rods", "Pump"], "cause_std": ["Fatigue", "Wear"]})
    tvqs = day_tvqs(_frame(day), jobs, fails, day)
    expected = day_stamps(pd.Series([day])).iloc[0]
    stamps = {pd.Timestamp(t) for points in tvqs.values() for t, _ in points}
    assert stamps == {expected}, f"found timestamps other than day D: {stamps}"
    assert tvqs[CLOCK_TAG] == [(expected, "2026-07-10")]
    job_tags = [t for t in tvqs if t.endswith(".Job")]
    assert len(job_tags) == 1 and len(tvqs[job_tags[0]]) == 1  # the job dated D+1 is not written
    assert not any(t.endswith(".Failure") for t in tvqs)  # failures dated D-3 and D+2 are not written
