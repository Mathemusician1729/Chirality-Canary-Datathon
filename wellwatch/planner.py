"""Weekly crew plan: which at-risk wells to visit, which day and with which crew (Gurobi MILP).

Decision      x[w, d, c] = 1 if crew c checks / services well w on day d (d = 1..7)
Objective     maximise  sum x * (risk_w * avoidable_d * effectiveness * cost_of_failure_w - visit_cost_w)
Constraints   each well at most once; crew hours per day (job + travel); crews only work their own
              zone (field area); weekly budget; critical wells (AlertLevel 3) must be visited by day 2.

avoidable_d: a failure that happens before the visit cannot be prevented. With a 7-day risk spread
evenly over the week, a visit on day d can still prevent (8 - d) / 7 of it.

If Gurobi or its license is unavailable, the same model is solved with SciPy's HiGHS solver,
so teammates and judges can always run it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

DAYS = range(1, 8)


@dataclass
class PlanSettings:
    crews_per_zone: int = 1            # rod-truck crews available in each field area
    hours_per_day: float = 10.0
    job_hours: float = 3.0             # time on site for a check / proactive rod job
    travel_hours: float = 1.0          # average drive per visit within a zone
    visit_cost_cad: float = 1_500.0    # cost of one check / small proactive job
    effectiveness: float = 0.5         # share of a failure prevented when visited in time
    weekly_budget_cad: float = 60_000.0
    max_candidates: int = 120          # highest-expected-loss wells considered
    must_visit_level: int = 3          # AlertLevel at or above this must be visited by day 2
    zones: dict = field(default_factory=dict)  # optional field -> zone override


def build_inputs(risk: pd.DataFrame, s: PlanSettings) -> pd.DataFrame:
    """risk: one row per well with uwi, field_operating_area, risk, expected_loss, level."""
    c = risk.sort_values("expected_loss", ascending=False).head(s.max_candidates).copy()
    c["zone"] = c["field_operating_area"].map(lambda f: s.zones.get(f, f))
    # value of a prevented failure: expected_loss / risk = cost of one failure
    c["failure_cost"] = np.where(c["risk"] > 0, c["expected_loss"] / c["risk"], 0.0)
    return c.reset_index(drop=True)


def _value(c: pd.DataFrame, s: PlanSettings) -> np.ndarray:
    """value[w, d] of visiting well w on day d."""
    avoidable = np.array([(8 - d) / 7 for d in DAYS])
    return (c["risk"].to_numpy()[:, None] * avoidable[None, :] * s.effectiveness
            * c["failure_cost"].to_numpy()[:, None]) - s.visit_cost_cad


def solve(risk: pd.DataFrame, s: PlanSettings | None = None, time_limit: float = 30.0) -> dict:
    s = s or PlanSettings()
    c = build_inputs(risk, s)
    if c.empty:
        return {"plan": pd.DataFrame(), "objective": 0.0, "solver": "none", "status": "no candidates"}
    try:
        return _solve_gurobi(c, s, time_limit)
    except Exception as exc:  # no gurobipy, no license, or size-limited license
        res = _solve_highs(c, s, time_limit)
        res["note"] = f"Gurobi unavailable ({type(exc).__name__}: {str(exc)[:80]}); solved with HiGHS"
        return res


def _crews(c: pd.DataFrame, s: PlanSettings) -> list[tuple[str, int]]:
    return [(z, k) for z in sorted(c["zone"].unique()) for k in range(s.crews_per_zone)]


def _solve_gurobi(c: pd.DataFrame, s: PlanSettings, time_limit: float) -> dict:
    import gurobipy as gp
    from gurobipy import GRB

    val = _value(c, s)
    crews = _crews(c, s)
    W = range(len(c))
    m = gp.Model("wellwatch_crew_plan")
    m.Params.OutputFlag = 0
    m.Params.TimeLimit = time_limit
    keys = [(w, d, k) for w in W for d in DAYS for k, (z, _) in enumerate(crews) if c.at[w, "zone"] == z]
    x = m.addVars(keys, vtype=GRB.BINARY, name="x")
    m.setObjective(gp.quicksum(val[w, d - 1] * x[w, d, k] for (w, d, k) in keys), GRB.MAXIMIZE)
    m.addConstrs((x.sum(w, "*", "*") <= 1 for w in W), name="once")
    hours = s.job_hours + s.travel_hours
    m.addConstrs((gp.quicksum(hours * x[w, d, k] for (w, dd, kk) in keys if dd == d and kk == k) <= s.hours_per_day
                  for d in DAYS for k in range(len(crews))), name="crew_hours")
    m.addConstr(gp.quicksum(s.visit_cost_cad * x[key] for key in keys) <= s.weekly_budget_cad, name="budget")
    for w in W:
        if c.at[w, "level"] >= s.must_visit_level:
            m.addConstr(gp.quicksum(x[w, d, k] for (ww, d, k) in keys if ww == w and d <= 2) == 1, name=f"critical_{w}")
    m.optimize()
    if m.SolCount == 0:
        return {"plan": pd.DataFrame(), "objective": 0.0, "solver": "gurobi", "status": f"status {m.Status}"}
    chosen = [(w, d, k) for (w, d, k) in keys if x[w, d, k].X > 0.5]
    return _result(c, s, val, crews, chosen, m.ObjVal, "gurobi",
                   "optimal" if m.Status == GRB.OPTIMAL else f"status {m.Status}", m.MIPGap)


def _solve_highs(c: pd.DataFrame, s: PlanSettings, time_limit: float) -> dict:
    from scipy.optimize import Bounds, LinearConstraint, milp

    val = _value(c, s)
    crews = _crews(c, s)
    keys = [(w, d, k) for w in range(len(c)) for d in DAYS for k, (z, _) in enumerate(crews) if c.at[w, "zone"] == z]
    n = len(keys)
    obj = -np.array([val[w, d - 1] for (w, d, k) in keys])
    rows, lo, hi = [], [], []
    for w in range(len(c)):
        row = np.array([1.0 if kw == w else 0.0 for (kw, _, _) in keys])
        rows.append(row); lo.append(1.0 if c.at[w, "level"] >= s.must_visit_level else 0.0); hi.append(1.0)
        if c.at[w, "level"] >= s.must_visit_level:
            rows.append(np.array([1.0 if kw == w and d <= 2 else 0.0 for (kw, d, _) in keys])); lo.append(1.0); hi.append(1.0)
    hours = s.job_hours + s.travel_hours
    for d in DAYS:
        for k in range(len(crews)):
            rows.append(np.array([hours if (dd == d and kk == k) else 0.0 for (_, dd, kk) in keys]))
            lo.append(0.0); hi.append(s.hours_per_day)
    rows.append(np.full(n, s.visit_cost_cad)); lo.append(0.0); hi.append(s.weekly_budget_cad)
    res = milp(obj, constraints=LinearConstraint(np.array(rows), lo, hi), integrality=np.ones(n),
               bounds=Bounds(0, 1), options={"time_limit": time_limit})
    if res.x is None:
        return {"plan": pd.DataFrame(), "objective": 0.0, "solver": "highs", "status": res.message}
    chosen = [keys[i] for i in np.flatnonzero(res.x > 0.5)]
    return _result(c, s, val, crews, chosen, -res.fun, "highs", "optimal" if res.status == 0 else res.message, None)


def _result(c, s, val, crews, chosen, objective, solver, status, gap) -> dict:
    plan = pd.DataFrame([{
        "day": d, "crew": f"{crews[k][0]} crew {crews[k][1] + 1}", "uwi": c.at[w, "uwi"],
        "field": c.at[w, "field_operating_area"], "risk": c.at[w, "risk"], "level": int(c.at[w, "level"]),
        "expected_value_cad": val[w, d - 1] + s.visit_cost_cad, "visit_cost_cad": s.visit_cost_cad,
    } for (w, d, k) in chosen]).sort_values(["day", "crew", "risk"], ascending=[True, True, False])
    return {"plan": plan.reset_index(drop=True), "objective": float(objective), "solver": solver,
            "status": status, "mip_gap": gap, "visits": len(plan),
            "spend_cad": len(plan) * s.visit_cost_cad, "candidates": len(c)}


def marginal_crew_value(risk: pd.DataFrame, s: PlanSettings | None = None) -> dict:
    """What would one more crew per zone be worth this week? (re-solve with crews + 1)."""
    s = s or PlanSettings()
    base = solve(risk, s)
    more = solve(risk, PlanSettings(**{**s.__dict__, "crews_per_zone": s.crews_per_zone + 1}))
    return {"base": base["objective"], "plus_one_crew": more["objective"],
            "gain_cad": more["objective"] - base["objective"]}


if __name__ == "__main__":
    rng = np.random.default_rng(0)
    demo = pd.DataFrame({"uwi": [f"W{i:03d}" for i in range(150)],
                         "field_operating_area": rng.choice(["Cactus Lake", "Winter", "Druid Main"], 150),
                         "risk": rng.beta(1.2, 8, 150)})
    demo["expected_loss"] = demo["risk"] * rng.uniform(8_000, 40_000, 150)
    demo["level"] = np.select([demo["risk"] > 0.4, demo["risk"] > 0.25], [3, 2], 0)
    for solver_name in ("gurobi", "highs"):
        r = solve(demo) if solver_name == "gurobi" else _solve_highs(build_inputs(demo, PlanSettings()), PlanSettings(), 30)
        print(f"{r['solver']}: status={r['status']} visits={r.get('visits')} objective=C${r['objective']:,.0f}")
    print(r["plan"].head(8).to_string(index=False))
