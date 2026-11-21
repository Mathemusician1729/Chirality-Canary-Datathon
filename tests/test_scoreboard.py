"""Scoreboard maths on a hand-made example."""

import pandas as pd

from wellwatch.scoreboard import alert_precision, catches, warning_precision

D = lambda s: pd.Timestamp(s)


def test_catches_and_lead_time():
    failures = pd.DataFrame({"uwi": ["A", "B", "C"], "production_date": [D("2026-07-10"), D("2026-07-10"), D("2026-07-10")]})
    warnings = pd.DataFrame({"uwi": ["A", "A", "B", "C"],
                             "day": [D("2026-07-04"), D("2026-07-08"),   # A: first warning 6 days before -> caught
                                     D("2026-07-01"),                    # B: 9 days before -> outside window
                                     D("2026-07-10")]})                  # C: same day -> too late
    f = catches(failures, warnings)
    assert f["caught"].tolist() == [True, False, False]
    assert f.loc[0, "lead_days"] == 6


def test_warning_and_alert_precision():
    failures = pd.DataFrame({"uwi": ["A"], "production_date": [D("2026-07-10")]})
    warnings = pd.DataFrame({"uwi": ["A", "A", "B", "A"],
                             "day": [D("2026-07-05"), D("2026-07-09"), D("2026-07-05"), D("2026-07-01")]})
    # A on 07-05 and 07-09 are followed by the failure within 7 days; B never fails; A on 07-01 is 9 days before.
    assert warning_precision(warnings, failures) == 0.5
    alerts = pd.DataFrame({"id": [1, 2], "uwi": ["A", "B"], "opened_day": ["2026-07-05", "2026-07-05"]})
    assert alert_precision(alerts, failures) == 0.5
