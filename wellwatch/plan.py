"""Plan crew visits on real WellWatch risk scores and write the plan back to Canary.

Uses the daily scores the engine stored (cache/alerts.db), only up to the planning day, and planner
inputs derived from the records (crews observed historically per field, Rod Truck job length and cost,
field centroids for travel). Day 1 of the plan is the day after the planning day.

Usage (from the repository root, after an engine replay):
    python -m wellwatch.plan --day 2026-08-03                   # one plan; writes outputs/plan_2026-08-03.csv
    python -m wellwatch.plan --day 2026-08-03 --extra-crew      # value of one more crew per field
    python -m wellwatch.plan --day 2026-08-03 --sensitivity     # effectiveness 0.25 / 0.5 / 0.75
    python -m wellwatch.plan --weekly 2026-07-06 2026-10-26     # every Monday + retrospective check
Add --no-canary to skip writing tags, --solver highs to force the free solver.
"""

from __future__ import annotations

import argparse
from dataclasses import replace

import numpy as np
import pandas as pd

from . import config, data
from .alerts import AlertStore
from .planner import PlanSettings, derive_inputs, marginal_crew_value, solve
from .train import REPAIR_CATEGORIES, crew_failure_starts

OUT = config.REPO_ROOT / "outputs"


class Planner:
    def __init__(self):
        self.store = AlertStore()
        self.scores = self.store.daily_scores()
        self.d = data.oil_producers()
        self.jobs = data.jobs()
        self._failures = None

    def day_scores(self, day: pd.Timestamp) -> pd.DataFrame:
        """Scores of the planning day only (never later days), with well coordinates as of that day."""
        sc = self.scores[self.scores["day"] == day].copy()
        loc = (self.d[self.d["production_date"] <= day].sort_values("production_date")
               .groupby("uwi", observed=True)[["surface_latitude", "surface_longitude"]].last())
        sc["lat"] = sc["uwi"].map(loc["surface_latitude"])
        sc["lon"] = sc["uwi"].map(loc["surface_longitude"])
        # Cost of one failure from failures known before the planning day (repair + lost oil); the
        # planner's expected loss is risk x this cost.
        if self.d is not None and len(sc) and "jobs" in self.__dict__:
            from .score import expected_failure_cost, failure_costs
            costs = failure_costs(self.d, self.jobs, day)
            sc["failure_cost"] = expected_failure_cost(sc["uwi"], sc["field"], costs)
            sc["expected_loss"] = sc["risk"] * sc["failure_cost"]
        return sc

    def settings(self, day: pd.Timestamp, **overrides) -> tuple[PlanSettings, dict]:
        """Inputs derived from records dated up to the planning day."""
        wells = self.day_scores(day)
        inputs = derive_inputs(self.jobs[self.jobs["start_date"] <= day], wells,
                               start=day - pd.Timedelta(days=3 * 365), end=day)
        s = PlanSettings(crews=inputs["crews"], centroids=inputs["centroids"],
                         proactive_hours=inputs["proactive_hours"], proactive_cost_cad=inputs["proactive_cost_cad"])
        return replace(s, **overrides), inputs

    @property
    def failures(self) -> pd.DataFrame:
        if self._failures is None:
            f = crew_failure_starts(self.d, self.jobs)
            f["value_cad"] = (f["episode_lost_oil_m3"].fillna(0)
                              * data.du.oil_value_cad_per_m3(f["production_date"], "base")
                              + f["repair_cost_cad"].fillna(0))
            self._failures = f
        return self._failures

    def plan(self, day, solver="auto", **overrides) -> tuple[dict, dict]:
        day = pd.Timestamp(day)
        s, inputs = self.settings(day, **overrides)
        res = solve(self.day_scores(day), s, solver=solver)
        if len(res["plan"]):
            res["plan"].insert(0, "visit_date", day + pd.to_timedelta(res["plan"]["day"], unit="D"))
            res["plan"].insert(0, "planning_day", day)
        return res, inputs

    def retrospective(self, day, res) -> dict:
        """Did the planned wells actually fail in the following week? Compared with the simple rule."""
        day = pd.Timestamp(day)
        f = self.failures
        week = f[(f["production_date"] > day) & (f["production_date"] <= day + pd.Timedelta(days=7))]
        plan = res["plan"]
        planned = set(plan["uwi"]) if len(plan) else set()
        hit = week[week["uwi"].isin(planned)]
        if len(plan):
            vd = dict(zip(plan["uwi"], plan["visit_date"]))
            in_time = hit[hit["production_date"] > hit["uwi"].map(vd)]
        else:
            in_time = hit.iloc[0:0]
        # simple rule: same number of visits to the scored wells with the shortest run life
        sc = self.day_scores(day)
        rep = self.jobs[self.jobs["job_category"].isin(REPAIR_CATEGORIES) & (self.jobs["start_date"] < day)]
        last = rep.groupby("uwi")["start_date"].max()
        sc["run_life"] = (day - sc["uwi"].map(last)).dt.days.fillna(1e9)
        rule = set(sc.nsmallest(len(planned), "run_life")["uwi"])
        rule_hit = week[week["uwi"].isin(rule)]
        return {"planning_day": day.date(), "visits": len(planned), "failures_next_7d_fleet": len(week),
                "planned_wells_failed": len(hit), "planned_wells_failed_after_visit": len(in_time),
                "value_failed_planned_cad": hit["value_cad"].sum(),
                "value_caught_in_time_cad": in_time["value_cad"].sum(),
                "rule_wells_failed": len(rule_hit), "value_failed_rule_cad": rule_hit["value_cad"].sum(),
                "loss_no_action": res["loss_no_action"], "loss_with_plan": res["loss_with_plan"],
                "plan_cost": res["plan_cost"], "solver": res["solver"], "solve_seconds": res["solve_seconds"]}


def write_to_canary(day: pd.Timestamp, res: dict) -> None:
    from .canary import CanaryWriter

    ts = pd.Timestamp(day).tz_localize("UTC") + pd.Timedelta(hours=config.DAILY_STAMP_UTC_HOUR)
    tvqs = {}
    for r in res["plan"].itertuples(index=False):
        base = lambda o: config.tag_path(r.field, r.uwi, o, dataset=config.DATASET_MODEL)
        tvqs[base("PlannedVisitDay")] = [(ts, pd.Timestamp(r.visit_date).strftime("%Y-%m-%d"))]
        tvqs[base("PlannedCrew")] = [(ts, r.crew)]
    for name, key in [("PlanLossNoAction", "loss_no_action"), ("PlanLossWithPlan", "loss_with_plan"),
                      ("PlanCost", "plan_cost"), ("PlanVisits", "visits")]:
        tvqs[f"{config.FLEET_PATH}.{name}"] = [(ts, round(float(res[key]), 0))]
    with CanaryWriter() as w:
        w.store(tvqs)


def summary_line(res: dict) -> str:
    return (f"loss C${res['loss_no_action']:,.0f} with no action -> C${res['loss_with_plan']:,.0f} with the plan "
            f"(-C${res['reduction']:,.0f}, {res['reduction_pct']:.1%}); plan cost C${res['plan_cost']:,.0f}; "
            f"{res['visits']} visits; {res['solver']} {res['status']} in {res['solve_seconds']}s")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--day")
    p.add_argument("--weekly", nargs=2, metavar=("START", "END"))
    p.add_argument("--extra-crew", action="store_true")
    p.add_argument("--sensitivity", action="store_true")
    p.add_argument("--visit-type", choices=["inspection", "proactive"], default="inspection")
    p.add_argument("--solver", choices=["auto", "gurobi", "highs"], default="auto")
    p.add_argument("--no-canary", action="store_true")
    args = p.parse_args(argv)
    pd.set_option("display.width", 220)
    pl = Planner()
    OUT.mkdir(exist_ok=True)

    if args.day:
        day = pd.Timestamp(args.day)
        res, inputs = pl.plan(day, args.solver, visit_type=args.visit_type)
        print(f"Inputs from the records (as of {day.date()}): crews per field {inputs['crews']}; "
              f"Rod Truck job {inputs['proactive_hours']:.1f} h, C${inputs['proactive_cost_cad']:,.0f}")
        if len(res["plan"]):
            print(f"Travel per visit: {res['plan']['travel_hours'].min():.1f}-{res['plan']['travel_hours'].max():.1f} h")
        print(summary_line(res))
        print(f"fleet: C${res['fleet_loss_no_action']:,.0f} -> C${res['fleet_loss_with_plan']:,.0f} "
              f"(wells outside the {res['candidates']} candidates: C${res['outside_candidates_loss']:,.0f})")
        res["plan"].to_csv(OUT / f"plan_{day.date()}.csv", index=False)
        print(res["plan"].head(12).to_string(index=False))
        if args.extra_crew:
            s, _ = pl.settings(day, visit_type=args.visit_type)
            mv = marginal_crew_value(pl.day_scores(day), s, solver=args.solver)
            print(f"one more crew per field: C${mv['value_of_extra_crew_cad']:,.0f} lower expected loss this week")
        if args.sensitivity:
            rows = []
            for e in (0.25, 0.5, 0.75):
                r, _ = pl.plan(day, args.solver, visit_type=args.visit_type, effectiveness=e)
                rows.append({"effectiveness": e, "visits": r["visits"], "loss_no_action": r["loss_no_action"],
                             "loss_with_plan": r["loss_with_plan"], "reduction": r["reduction"],
                             "reduction_pct": r["reduction_pct"], "plan_cost": r["plan_cost"]})
            sens = pd.DataFrame(rows)
            sens.to_csv(OUT / f"plan_sensitivity_{day.date()}.csv", index=False)
            print(sens.to_string(index=False, float_format=lambda v: f"{v:,.2f}"))
        if not args.no_canary:
            write_to_canary(day, res)
            print("wrote plan to Canary")

    if args.weekly:
        mondays = pd.date_range(args.weekly[0], args.weekly[1], freq="W-MON")
        rows = []
        for day in mondays:
            if not (pl.scores["day"] == day).any():
                continue
            res, _ = pl.plan(day, args.solver, visit_type=args.visit_type)
            rows.append(pl.retrospective(day, res))
            if not args.no_canary:
                write_to_canary(day, res)
            print(f"{day.date()}: {summary_line(res)}")
        weekly = pd.DataFrame(rows)
        weekly.to_csv(OUT / "plan_weekly_summary.csv", index=False)
        tot = weekly.sum(numeric_only=True)
        print(f"\n{len(weekly)} weekly plans: {int(tot['visits'])} visits; planned wells that failed within 7 days "
              f"{int(tot['planned_wells_failed'])} ({tot['planned_wells_failed'] / max(tot['visits'], 1):.1%}), "
              f"of which visited before the failure {int(tot['planned_wells_failed_after_visit'])}; "
              f"simple rule with the same visits: {int(tot['rule_wells_failed'])} "
              f"({tot['rule_wells_failed'] / max(tot['visits'], 1):.1%})")
        print(f"value of failures on planned wells C${tot['value_failed_planned_cad']:,.0f} "
              f"(visited in time: C${tot['value_caught_in_time_cad']:,.0f}) vs simple rule C${tot['value_failed_rule_cad']:,.0f}; "
              f"expected loss C${tot['loss_no_action']:,.0f} -> C${tot['loss_with_plan']:,.0f}, plan cost C${tot['plan_cost']:,.0f}")


if __name__ == "__main__":
    main()
