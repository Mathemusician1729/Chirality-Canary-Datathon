"""Weekly crew plan that MINIMISES the expected total loss of the coming week (Gurobi MILP).

Decision   x[w, d, c] = 1 if crew c visits candidate well w on day d (d = 1..7)

Objective  minimise  sum_w p_w * C_w * (1 - e * sum_{d,c} a_d * x[w,d,c])   expected failure loss left
                     + sum_{w,d,c} k_w * x[w,d,c]                           what the plan costs
    p_w  7-day failure risk from the model          C_w  cost of one failure (oil lost x quarterly value + repair)
    e    effectiveness of a timely visit            a_d  (8 - d) / 7: share of the week's risk still avoidable on day d
    k_w  visit cost
The objective value IS the expected total loss of the week for the candidate wells (it includes the
constant sum_w p_w*C_w). Wells outside the candidate list keep their full expected loss; the report
adds it so the fleet total is complete.

Equivalence (worth knowing when presenting): the old version maximised "savings" = sum x*(p*C*e*a_d - k).
Expected total loss = (loss with no action) - savings, and loss with no action is a constant, so both
objectives choose exactly the same visits. The new form is better to communicate ("expected loss this
week: C$X with no action -> C$Y with the plan") and makes it easy to add loss terms later.

Constraints  each well at most once; each crew's hours per day (job + travel) <= working day; crews only
             work their own zone (field area); weekly budget; critical wells (AlertLevel 3) visited by day 2.

Travel is a first approximation: round trip from the zone centroid (straight-line km / speed) plus setup.
There is no routing between wells.

Solved with Gurobi when gurobipy and a licence are available, otherwise with SciPy's HiGHS solver.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

DAYS = range(1, 8)
AVOIDABLE = np.array([(8 - d) / 7 for d in DAYS])


@dataclass
class PlanSettings:
    hours_per_day: float = 10.0          # assumption: one 10-hour crew shift per day
    visit_type: str = "inspection"       # "inspection" (check + fix small issues) or "proactive" (rod job)
    inspection_hours: float = 2.0        # assumption
    inspection_cost_cad: float = 1_500.0  # assumption (labelled in the report)
    proactive_hours: float = 8.0         # data: median Rod Truck job duration (capped at a shift); see derive_inputs
    proactive_cost_cad: float = 2_846.0  # data: median Rod Truck field_est_total; see derive_inputs
    setup_hours: float = 0.5             # assumption: per visit
    speed_kmh: float = 50.0              # assumption: lease-road driving speed
    effectiveness: float = 0.5           # assumption: share of a failure prevented by a timely visit
    weekly_budget_cad: float = 60_000.0  # assumption
    max_candidates: int = 120            # highest expected-loss wells considered
    must_visit_level: int = 3            # AlertLevel at or above this must be visited by day 2
    default_crews: int = 1
    crews: dict = field(default_factory=dict)      # data: zone -> crews observed historically
    centroids: dict = field(default_factory=dict)  # data: zone -> (lat, lon)

    @property
    def job_hours(self) -> float:
        return self.inspection_hours if self.visit_type == "inspection" else self.proactive_hours

    @property
    def visit_cost(self) -> float:
        return self.inspection_cost_cad if self.visit_type == "inspection" else self.proactive_cost_cad


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    h = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(h))


def build_inputs(scores: pd.DataFrame, s: PlanSettings) -> tuple[pd.DataFrame, float]:
    """scores: one row per running well (uwi, field, risk, expected_loss, level[, lat, lon]).

    Returns the candidate table and the expected loss of the wells left out of it.
    """
    sc = scores.copy()
    if "field" not in sc and "field_operating_area" in sc:
        sc["field"] = sc["field_operating_area"]
    sc = sc.sort_values("expected_loss", ascending=False)
    c, rest = sc.head(s.max_candidates).copy(), sc.iloc[s.max_candidates:]
    c["zone"] = c["field"]
    if "failure_cost" not in c:
        c["failure_cost"] = np.where(c["risk"] > 0, c["expected_loss"] / c["risk"], 0.0)
    if {"lat", "lon"} <= set(c.columns) and s.centroids:
        cen = c["zone"].map(s.centroids)
        lat0 = cen.map(lambda v: v[0] if isinstance(v, tuple) else np.nan)
        lon0 = cen.map(lambda v: v[1] if isinstance(v, tuple) else np.nan)
        km = pd.Series(haversine_km(c["lat"], c["lon"], lat0, lon0), index=c.index).fillna(0)
    else:
        km = pd.Series(0.0, index=c.index)
    c["travel_hours"] = 2 * km / s.speed_kmh + s.setup_hours
    c["visit_hours"] = s.job_hours + c["travel_hours"]
    c["visit_cost"] = s.visit_cost
    return c.reset_index(drop=True), float(rest["expected_loss"].sum())


def _crews(c: pd.DataFrame, s: PlanSettings) -> list[tuple[str, int]]:
    return [(z, k) for z in sorted(c["zone"].unique()) for k in range(max(1, int(s.crews.get(z, s.default_crews))))]


def _keys(c, crews, s):
    """Allowed (well, day, crew) choices: crew in the well's zone, and the visit fits in one shift."""
    return [(w, d, k) for w in range(len(c)) for d in DAYS for k, (z, _) in enumerate(crews)
            if c.at[w, "zone"] == z and c.at[w, "visit_hours"] <= s.hours_per_day]


def _coef(c: pd.DataFrame, s: PlanSettings, objective: str) -> np.ndarray:
    """Per (w, d) objective coefficient. min_loss: k - p*C*e*a_d (plus a constant); max_savings: the negative."""
    p, C, k = c["risk"].to_numpy(), c["failure_cost"].to_numpy(), c["visit_cost"].to_numpy()
    reduce = p[:, None] * C[:, None] * s.effectiveness * AVOIDABLE[None, :]
    return (k[:, None] - reduce) if objective == "min_loss" else (reduce - k[:, None])


def solve(scores: pd.DataFrame, s: PlanSettings | None = None, time_limit: float = 60.0,
          solver: str = "auto", objective: str = "min_loss") -> dict:
    """Plan the week. solver: "auto" (Gurobi, else HiGHS), "gurobi" or "highs".
    objective: "min_loss" (default) or "max_savings" (the old formulation, kept for the equivalence check)."""
    s = s or PlanSettings()
    c, outside_loss = build_inputs(scores, s)
    if c.empty:
        return {"plan": pd.DataFrame(), "status": "no candidates", "solver": "none"}
    crews = _crews(c, s)
    keys = _keys(c, crews, s)
    coef = _coef(c, s, objective)
    if solver in ("auto", "gurobi"):
        try:
            chosen, status, gap, secs = _solve_gurobi(c, s, crews, keys, coef, objective, time_limit)
            used = "gurobi"
        except Exception as exc:
            if solver == "gurobi":
                raise
            chosen, status, gap, secs = _solve_highs(c, s, crews, keys, coef, objective, time_limit)
            used = f"highs (Gurobi unavailable: {type(exc).__name__})"
    else:
        chosen, status, gap, secs = _solve_highs(c, s, crews, keys, coef, objective, time_limit)
        used = "highs"
    return _report(c, s, crews, chosen, outside_loss, used, status, gap, secs, objective)


def _solve_gurobi(c, s, crews, keys, coef, objective, time_limit):
    import time

    import gurobipy as gp
    from gurobipy import GRB

    t0 = time.time()
    with gp.Env(params={"OutputFlag": 0}) as env, gp.Model("wellwatch_min_loss", env=env) as m:
        m.Params.TimeLimit = time_limit
        x = m.addVars(keys, vtype=GRB.BINARY, name="x")
        const = float((c["risk"] * c["failure_cost"]).sum()) if objective == "min_loss" else 0.0
        sense = GRB.MINIMIZE if objective == "min_loss" else GRB.MAXIMIZE
        m.setObjective(const + gp.quicksum(coef[w, d - 1] * x[w, d, k] for (w, d, k) in keys), sense)
        m.addConstrs((x.sum(w, "*", "*") <= 1 for w in range(len(c))), name="once")
        hours = c["visit_hours"].to_numpy()
        by_dk = {}
        for (w, d, k) in keys:
            by_dk.setdefault((d, k), []).append(w)
        for (d, k), ws in by_dk.items():
            m.addConstr(gp.quicksum(hours[w] * x[w, d, k] for w in ws) <= s.hours_per_day, name=f"hours_{d}_{k}")
        m.addConstr(gp.quicksum(c.at[w, "visit_cost"] * x[w, d, k] for (w, d, k) in keys) <= s.weekly_budget_cad,
                    name="budget")
        visitable = {w for (w, _, _) in keys}
        for w in range(len(c)):
            if c.at[w, "level"] >= s.must_visit_level and w in visitable:
                m.addConstr(gp.quicksum(x[ww, d, k] for (ww, d, k) in keys if ww == w and d <= 2) == 1,
                            name=f"critical_{w}")
        m.optimize()
        if m.SolCount == 0:
            raise RuntimeError(f"Gurobi found no feasible plan (status {m.Status})")
        chosen = [key for key in keys if x[key].X > 0.5]
        status = "optimal" if m.Status == GRB.OPTIMAL else f"status {m.Status}"
        return chosen, status, float(m.MIPGap), time.time() - t0


def _solve_highs(c, s, crews, keys, coef, objective, time_limit):
    import time

    from scipy.optimize import Bounds, LinearConstraint, milp
    from scipy.sparse import lil_matrix

    t0 = time.time()
    n = len(keys)
    obj = np.array([coef[w, d - 1] for (w, d, k) in keys])
    if objective == "max_savings":
        obj = -obj
    groups_w, groups_dk = {}, {}
    for i, (w, d, k) in enumerate(keys):
        groups_w.setdefault(w, []).append(i)
        groups_dk.setdefault((d, k), []).append(i)
    critical = [w for w in range(len(c)) if c.at[w, "level"] >= s.must_visit_level and w in groups_w]
    rows = len(groups_w) + len(groups_dk) + 1 + len(critical)
    A = lil_matrix((rows, n))
    lo, hi = [], []
    r = 0
    for w, idx in groups_w.items():
        A[r, idx] = 1.0; lo.append(0.0); hi.append(1.0); r += 1
    hours = c["visit_hours"].to_numpy()
    for (d, k), idx in groups_dk.items():
        for i in idx:
            A[r, i] = hours[keys[i][0]]
        lo.append(0.0); hi.append(s.hours_per_day); r += 1
    for i, (w, d, k) in enumerate(keys):
        A[r, i] = c.at[w, "visit_cost"]
    lo.append(0.0); hi.append(s.weekly_budget_cad); r += 1
    for w in critical:
        for i in groups_w[w]:
            if keys[i][1] <= 2:
                A[r, i] = 1.0
        lo.append(1.0); hi.append(1.0); r += 1
    res = milp(obj, constraints=LinearConstraint(A.tocsr(), lo, hi), integrality=np.ones(n), bounds=Bounds(0, 1),
               options={"time_limit": time_limit, "mip_rel_gap": 1e-9})
    if res.x is None:
        raise RuntimeError(f"HiGHS found no feasible plan: {res.message}")
    chosen = [keys[i] for i in np.flatnonzero(res.x > 0.5)]
    gap = getattr(res, "mip_gap", None)
    return chosen, ("optimal" if res.status == 0 else res.message), gap, time.time() - t0


def _report(c, s, crews, chosen, outside_loss, solver, status, gap, secs, objective) -> dict:
    no_action = float((c["risk"] * c["failure_cost"]).sum())
    plan = pd.DataFrame([{
        "day": d, "crew": f"{crews[k][0]} crew {crews[k][1] + 1}", "uwi": c.at[w, "uwi"], "field": c.at[w, "field"],
        "risk": c.at[w, "risk"], "level": int(c.at[w, "level"]),
        "expected_loss_before": c.at[w, "risk"] * c.at[w, "failure_cost"],
        "expected_loss_after": c.at[w, "risk"] * c.at[w, "failure_cost"] * (1 - s.effectiveness * AVOIDABLE[d - 1]),
        "visit_cost": c.at[w, "visit_cost"], "travel_hours": c.at[w, "travel_hours"],
    } for (w, d, k) in chosen])
    if len(plan):
        plan = plan.sort_values(["day", "crew", "risk"], ascending=[True, True, False]).reset_index(drop=True)
        reduction = float((plan["expected_loss_before"] - plan["expected_loss_after"]).sum())
        cost = float(plan["visit_cost"].sum())
    else:
        reduction = cost = 0.0
    with_plan = no_action - reduction + cost
    return {
        "plan": plan, "objective": objective, "solver": solver, "status": status, "mip_gap": gap,
        "solve_seconds": round(secs, 3), "candidates": len(c), "crews": len(crews), "visits": len(plan),
        "loss_no_action": no_action, "loss_with_plan": with_plan, "reduction": no_action - with_plan,
        "reduction_pct": (no_action - with_plan) / no_action if no_action else 0.0, "plan_cost": cost,
        "savings_old_objective": reduction - cost, "outside_candidates_loss": outside_loss,
        "fleet_loss_no_action": no_action + outside_loss, "fleet_loss_with_plan": with_plan + outside_loss,
    }


def marginal_crew_value(scores: pd.DataFrame, s: PlanSettings | None = None, **kw) -> dict:
    """What one more crew per zone would be worth this week (re-solve with crews + 1)."""
    s = s or PlanSettings()
    base = solve(scores, s, **kw)
    zones = base["plan"]["field"].unique() if len(base["plan"]) else []
    more = replace(s, crews={z: s.crews.get(z, s.default_crews) + 1 for z in set(zones) | set(s.crews)},
                   default_crews=s.default_crews + 1)
    plus = solve(scores, more, **kw)
    return {"loss_with_plan": base["loss_with_plan"], "loss_with_extra_crew": plus["loss_with_plan"],
            "value_of_extra_crew_cad": base["loss_with_plan"] - plus["loss_with_plan"]}


# --------------------------------------------------------------------------- inputs from the data
def derive_inputs(jobs: pd.DataFrame, wells: pd.DataFrame, start="2024-01-01", end="2026-10-26") -> dict:
    """Planner inputs from the records: crews per zone, proactive job hours and cost, zone centroids."""
    rep = jobs[jobs["job_category"].isin(["Rod Truck", "Workover"])].dropna(subset=["start_date"]).copy()
    rep["end"] = rep["end_date"].fillna(rep["start_date"])
    rep = rep[(rep["start_date"] <= end) & (rep["end"] >= start) & (rep["end"] >= rep["start_date"])]
    rep["end"] = rep[["end", "start_date"]].max(axis=1).clip(upper=rep["start_date"] + pd.Timedelta(days=30))
    days = pd.date_range(start, end, freq="D")
    crews = {}
    for zone, g in rep.groupby("field_operating_area"):
        counts = pd.Series(0, index=days)
        for s0, e0 in zip(g["start_date"].dt.normalize(), g["end"].dt.normalize()):
            counts.loc[max(s0, days[0]):min(e0, days[-1])] += 1
        crews[zone] = max(1, int(math.ceil(counts.quantile(0.9))))
    rod = jobs[jobs["job_category"] == "Rod Truck"]
    hours = float(min(10.0, rod["job_duration_days"].dropna().median() * 24))
    cost = float(rod["field_est_total"].dropna().median())
    cen = wells.dropna(subset=["lat", "lon"]).groupby("field")[["lat", "lon"]].mean()
    return {"crews": crews, "proactive_hours": hours, "proactive_cost_cad": cost,
            "centroids": {z: (float(r.lat), float(r.lon)) for z, r in cen.iterrows()}}
