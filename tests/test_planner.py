"""Crew planner: known optimum, Gurobi = HiGHS, constraints hold, old/new objectives agree, no future scores."""

import numpy as np
import pandas as pd
import pytest

from wellwatch.planner import AVOIDABLE, PlanSettings, build_inputs, solve


def gurobi_ok():
    try:
        import gurobipy as gp
        with gp.Env(params={"OutputFlag": 0}) as env, gp.Model(env=env) as m:
            m.addVars(3000)  # more than the size-limited licence allows
            m.update()
            m.optimize()
        return True
    except Exception:
        return False


def random_scores(n=60, seed=0):
    rng = np.random.default_rng(seed)
    risk = rng.beta(1.2, 12, n)
    df = pd.DataFrame({"uwi": [f"W{i:03d}" for i in range(n)],
                       "field": rng.choice(["Cactus Lake", "Winter", "Druid Main"], n),
                       "risk": risk, "expected_loss": risk * rng.uniform(5_000, 60_000, n)})
    df["level"] = np.select([df["risk"] > 0.25, df["risk"] > 0.15], [3, 2], 0)
    return df


SETTINGS = PlanSettings(crews={"Cactus Lake": 1, "Winter": 2, "Druid Main": 1}, weekly_budget_cad=30_000)


def test_known_optimum_tiny_instance():
    # 1 crew, room for exactly one 2.5 h visit per day... but only one day matters: make the shift fit 1 visit
    scores = pd.DataFrame({"uwi": ["A", "B", "C"], "field": "F", "risk": [0.10, 0.30, 0.20],
                           "expected_loss": [0.10 * 50_000, 0.30 * 20_000, 0.20 * 40_000], "level": 0})
    s = PlanSettings(crews={"F": 1}, weekly_budget_cad=1_500, effectiveness=0.5)  # budget = exactly one visit
    res = solve(scores, s, solver="highs")
    # reduction by visiting on day 1: p*C*e*1 -> A 2500, B 3000, C 4000 -> C is best (4000 - 1500 cost)
    assert res["plan"]["uwi"].tolist() == ["C"] and res["plan"]["day"].tolist() == [1]
    no_action = 5_000 + 6_000 + 8_000
    assert res["loss_no_action"] == pytest.approx(no_action)
    assert res["loss_with_plan"] == pytest.approx(no_action - 4_000 + 1_500)


@pytest.mark.skipif(not gurobi_ok(), reason="gurobipy or a full licence not available")
def test_gurobi_and_highs_agree():
    scores = random_scores()
    g = solve(scores, SETTINGS, solver="gurobi")
    h = solve(scores, SETTINGS, solver="highs")
    assert g["solver"] == "gurobi" and h["solver"] == "highs"
    assert g["loss_with_plan"] == pytest.approx(h["loss_with_plan"], rel=1e-6)


def check_constraints(res, scores, s):
    plan = res["plan"]
    c, _ = build_inputs(scores, s)
    assert plan["uwi"].is_unique
    hours = dict(zip(c["uwi"], c["visit_hours"]))
    per = plan.assign(h=plan["uwi"].map(hours)).groupby(["day", "crew"])["h"].sum()
    assert (per <= s.hours_per_day + 1e-9).all()
    assert all(crew.startswith(field) for crew, field in zip(plan["crew"], plan["field"]))
    assert plan["visit_cost"].sum() <= s.weekly_budget_cad + 1e-9
    crit = set(c.loc[c["level"] >= s.must_visit_level, "uwi"])
    early = set(plan.loc[plan["day"] <= 2, "uwi"])
    assert crit <= early


@pytest.mark.parametrize("solver", ["highs", "gurobi"])
def test_every_plan_satisfies_constraints(solver):
    if solver == "gurobi" and not gurobi_ok():
        pytest.skip("gurobi not available")
    scores = random_scores(seed=3)
    res = solve(scores, SETTINGS, solver=solver)
    assert res["visits"] > 0
    check_constraints(res, scores, SETTINGS)


def test_min_loss_equals_old_max_savings():
    scores = random_scores(seed=7)
    new = solve(scores, SETTINGS, solver="highs", objective="min_loss")
    old = solve(scores, SETTINGS, solver="highs", objective="max_savings")
    key = lambda r: set(zip(r["plan"]["uwi"], r["plan"]["day"]))
    # same objective value up to a constant -> same optimal value; plans may differ only between exact ties
    assert new["loss_with_plan"] == pytest.approx(old["loss_with_plan"], rel=1e-9)
    assert new["loss_no_action"] - new["loss_with_plan"] == pytest.approx(new["savings_old_objective"], rel=1e-9)
    assert len(key(new) ^ key(old)) == 0 or new["loss_with_plan"] == pytest.approx(old["loss_with_plan"])


def test_planning_day_uses_no_later_scores(monkeypatch):
    from wellwatch import plan as plan_mod

    class FakeStore:
        def daily_scores(self):
            rows = []
            for day, risk in [("2026-08-03", 0.1), ("2026-08-04", 0.9)]:
                rows.append({"day": pd.Timestamp(day), "uwi": "A", "field": "F", "risk": risk, "level": 0,
                             "rank": 1, "expected_loss": risk * 10_000, "driver": ""})
            return pd.DataFrame(rows)

    pl = plan_mod.Planner.__new__(plan_mod.Planner)
    pl.scores = FakeStore().daily_scores()
    pl.d = pd.DataFrame({"uwi": ["A"], "production_date": [pd.Timestamp("2026-08-01")],
                         "surface_latitude": [52.0], "surface_longitude": [-110.0]})
    sc = pl.day_scores(pd.Timestamp("2026-08-03"))
    assert sc["risk"].tolist() == [0.1]  # the 08-04 score is never used for the 08-03 plan
