"""Alert lifecycle rules (ISA-18.2 style)."""

import pandas as pd

from wellwatch.alerts import AlertStore


def scored(rows):
    """rows: list of (uwi, level)."""
    return pd.DataFrame([{"uwi": u, "field_operating_area": "F", "risk": 0.1 * lvl + 0.01, "level": lvl, "rank": i + 1,
                          "expected_loss": 1000.0 * lvl, "driver": "test"} for i, (u, lvl) in enumerate(rows)])


def day(n):
    return pd.Timestamp("2026-07-01") + pd.Timedelta(days=n)


def test_open_update_and_clear_after_7_days(tmp_path):
    s = AlertStore(tmp_path / "a.db")
    c = s.process_day(day(0), scored([("A", 2)]), set())
    assert c["opened"] == 1 and len(c["notify"]) == 1
    c = s.process_day(day(1), scored([("A", 3)]), set())
    assert c["opened"] == 0 and c["updated"] == 1 and len(c["notify"]) == 1  # escalation notifies
    for n in range(2, 8):  # 6 days below warning: still open
        s.process_day(day(n), scored([("A", 0)]), set())
    assert s.list_alerts(open_only=True).shape[0] == 1
    c = s.process_day(day(8), scored([("A", 1)]), set())  # 7th consecutive day below level 2
    assert c["cleared"] == 1
    a = s.list_alerts().iloc[0]
    assert a["state"] == "Closed" and a["outcome"] == "Cleared" and a["max_level"] == 3


def test_failure_closes_alert_with_lead_days(tmp_path):
    s = AlertStore(tmp_path / "a.db")
    s.process_day(day(0), scored([("B", 2)]), set())
    s.process_day(day(1), scored([("B", 2)]), set())
    c = s.process_day(day(3), scored([]), {"B"})
    assert c["failed"] == 1
    a = s.list_alerts().iloc[0]
    assert a["outcome"] == "Failure occurred" and a["lead_days"] == 3 and a["outcome_day"] == "2026-07-04"


def test_shelved_alert_does_not_renotify_until_expiry(tmp_path):
    s = AlertStore(tmp_path / "a.db")
    s.process_day(day(0), scored([("C", 2)]), set())
    aid = int(s.list_alerts().iloc[0]["id"])
    s.shelve(aid, day(4), user="operator", note="crew already booked")
    c = s.process_day(day(2), scored([("C", 3)]), set())  # escalates while shelved
    assert c["notify"] == []
    assert s.list_alerts().iloc[0]["state"] == "Shelved"
    c = s.process_day(day(4), scored([("C", 2)]), set())
    assert c["unshelved"] == 1 and s.list_alerts().iloc[0]["state"] == "Open"
    assert {"shelved", "escalated", "unshelved"} <= set(s.events(aid)["action"])


def test_no_duplicate_open_alerts(tmp_path):
    s = AlertStore(tmp_path / "a.db")
    for n in range(3):
        s.process_day(day(n), scored([("D", 2), ("E", 2)]), set())
    open_ = s.list_alerts(open_only=True)
    assert len(open_) == 2 and open_["uwi"].is_unique


def test_operator_actions_are_audited(tmp_path):
    s = AlertStore(tmp_path / "a.db")
    s.process_day(day(0), scored([("F", 2)]), set())
    aid = int(s.list_alerts().iloc[0]["id"])
    s.acknowledge(aid, user="karan", note="checking torque")
    s.close(aid, user="karan", note="rods replaced proactively")
    ev = s.events(aid)
    assert list(ev["action"]) == ["opened", "acknowledged", "closed"]
    assert s.list_alerts().iloc[0]["outcome"] == "Closed by operator"
