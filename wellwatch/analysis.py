"""Results for the write-up: what failures cost, what drives them, what WellWatch catches, and the profit
estimate. Writes aggregate tables (no well IDs) to docs/results/ and charts to docs/figures/.

Inputs: the competition files (via wellwatch.data), the dated oil-price table, the trained model's
metadata, and the saved replay results outputs/replay_scoreboard.csv and outputs/plan_weekly_summary.csv
(from `python -m wellwatch.scoreboard` and `python -m wellwatch.plan --weekly 2026-07-06 2026-10-26`).

Run:  python -m wellwatch.analysis
"""

from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from . import config, data  # noqa: E402
from .train import EQUIPMENT, crew_failure_starts  # noqa: E402

DOCS = config.REPO_ROOT / "docs"
FIG = DOCS / "figures"
RES = DOCS / "results"
OUT = config.REPO_ROOT / "outputs"
MODELS = config.REPO_ROOT / "models"
du = data.du

# chart style (validated reference palette; text in ink colours, never the series colour)
BLUE, ORANGE = "#2a78d6", "#eb6834"
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "text.color": INK, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.spines.top": False, "axes.spines.right": False, "axes.spines.left": False,
    "font.family": "Segoe UI", "font.size": 11, "axes.titlesize": 13, "axes.titleweight": "bold",
    "axes.titlelocation": "left", "legend.frameon": False, "axes.axisbelow": True,
})

FEATURE_GROUPS = [  # (prefix, plain-English factor)
    ("jobs_prev_365", "Service jobs in the past year"), ("days_since_last_job", "Days since the last service job"),
    ("last_job_", "What the last job report mentions (broken rods, sand, ...)"),
    ("repairs_prev_365", "Repairs in the past year"), ("failures_prev_365", "Recorded failures in the past year"),
    ("days_since_last_failure", "Days since the last recorded failure"),
    ("run_life_days", "Run life: days since the last repair"),
    ("eq_down_hours", "Recent equipment downtime"),
    ("rpm", "Pump speed"), ("eff", "Pump efficiency"), ("torque", "Rod torque"), ("current", "Motor current"),
    ("tbg_p", "Tubing pressure"), ("csg_p", "Casing pressure"), ("wcut", "Water cut"), ("oil", "Recent oil rate"),
    ("pump_displacement", "Pump size"), ("well_age", "Well age"),
]


def money_m(x: float) -> str:
    return f"C${x / 1e6:,.2f}M"


def _save(fig, name: str):
    FIG.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG / name, dpi=160, bbox_inches="tight")
    plt.close(fig)


# --------------------------------------------------------------------------- failures and their cost
def failure_episodes(d: pd.DataFrame, jobs: pd.DataFrame, failures: pd.DataFrame) -> pd.DataFrame:
    """Every crew-needing failure 2021-2026 with its downtime, lost oil, repair cost, oil value and type."""
    d = d.sort_values(["uwi", "production_date"]).reset_index(drop=True)
    eq = d["downtime_class"] == EQUIPMENT
    start = eq & ~eq.groupby(d["uwi"]).shift(1, fill_value=False)
    episode = start.cumsum().where(eq)
    ep = d.loc[eq].groupby(episode[eq]).agg(hours_down=("hours_down", "sum"), days_down=("production_date", "size"))
    starts = d.loc[start, ["uwi", "production_date"]].copy()
    starts["episode"] = episode[start].to_numpy()
    starts = starts.join(ep, on="episode").drop(columns="episode")

    cf = crew_failure_starts(d, jobs).merge(starts, on=["uwi", "production_date"], how="left")
    cf["repair_cost_cad"] = cf["repair_cost_cad"].fillna(0.0)
    cf["lost_oil_m3"] = cf["episode_lost_oil_m3"].fillna(0.0)
    for case in ("low", "base", "high"):
        cf[f"oil_value_{case}_cad"] = cf["lost_oil_m3"] * du.oil_value_cad_per_m3(cf["production_date"], case)
    cf["total_base_cad"] = cf["repair_cost_cad"] + cf["oil_value_base_cad"]

    # failure type and cause: the failure record nearest the episode start (7 days before to 14 after)
    f = failures[["uwi", "failure_start_date", "failure_type_std", "cause_std"]].dropna(subset=["failure_start_date"])
    m = cf[["uwi", "production_date"]].reset_index().merge(f, on="uwi")
    gap = (m["failure_start_date"] - m["production_date"]).dt.days
    m = m[(gap >= -7) & (gap <= 14)].assign(abs_gap=gap.abs()).sort_values("abs_gap").drop_duplicates("index")
    cf = cf.join(m.set_index("index")[["failure_type_std", "cause_std"]])
    cf["failure_type"] = cf["failure_type_std"].astype("object").fillna("No failure record")
    cf["cause"] = cf["cause_std"].astype("object").fillna("No failure record")
    cf["field_operating_area"] = cf["field_operating_area"].astype("object").fillna("Unknown field")
    return cf.drop(columns=["failure_type_std", "cause_std"])


def cost_table(cf: pd.DataFrame, by: str) -> pd.DataFrame:
    g = cf.groupby(by).agg(failures=("uwi", "size"), avg_hours_down=("hours_down", "mean"),
                           avg_lost_oil_m3=("lost_oil_m3", "mean"), avg_repair_cad=("repair_cost_cad", "mean"),
                           avg_oil_value_base_cad=("oil_value_base_cad", "mean"),
                           total_repair_cad=("repair_cost_cad", "sum"),
                           total_oil_value_low_cad=("oil_value_low_cad", "sum"),
                           total_oil_value_base_cad=("oil_value_base_cad", "sum"),
                           total_oil_value_high_cad=("oil_value_high_cad", "sum"))
    g["total_cost_base_cad"] = g["total_repair_cad"] + g["total_oil_value_base_cad"]
    g["share_of_failures"] = g["failures"] / g["failures"].sum()
    g["lost_oil_share_of_cost_base"] = g["total_oil_value_base_cad"] / g["total_cost_base_cad"]
    return g.sort_values("total_cost_base_cad", ascending=False).reset_index()


def fig_cost_by_type(t: pd.DataFrame):
    """Average cost of one failure: repair bill vs value of the oil lost while the well was down."""
    t = t[t["failures"] >= 100].sort_values("avg_repair_cad")
    fig, ax = plt.subplots(figsize=(9, 3.8))
    y = np.arange(len(t))
    rep, oil = t["avg_repair_cad"] / 1e3, t["avg_oil_value_base_cad"] / 1e3
    ax.barh(y, rep, color=BLUE, height=0.6, edgecolor=SURFACE, linewidth=2, label="Repair (crew job)")
    ax.barh(y, oil, left=rep, color=ORANGE, height=0.6, edgecolor=SURFACE, linewidth=2,
            label="Lost oil (cash margin)")
    for yi, (r, o, n, h) in enumerate(zip(rep, oil, t["failures"], t["avg_hours_down"])):
        ax.text(r + o + 0.8, yi, f"C${r + o:,.1f}k   {n:,} failures, {h / 24:.1f} days down",
                va="center", color=INK2, fontsize=10)
    ax.set_yticks(y, t["failure_type"])
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("Average cost of one crew-needing failure, C$ thousand (2021 – Oct 2026)")
    ax.set_xlim(0, (rep + oil).max() * 1.75)
    ax.set_title("Repairs cost more than the lost oil, and pump and tubing failures cost the most")
    ax.legend(loc="lower right")
    _save(fig, "fig1_failure_cost_by_type.png")


def repeat_failures(cf: pd.DataFrame, d: pd.DataFrame) -> pd.DataFrame:
    """Average crew-needing failures in a calendar year, by the number the well had the year before."""
    years = range(2021, 2026)
    active = d.assign(year=d["production_date"].dt.year).groupby(["uwi", "year"]).size().rename("days").reset_index()
    active = active[active["days"] >= 300]  # wells producing most of both years
    cnt = cf.assign(year=cf["production_date"].dt.year).groupby(["uwi", "year"]).size().rename("n")
    rows = []
    for y in years:
        both = active[active["year"] == y].merge(active[active["year"] == y + 1], on="uwi")[["uwi"]]
        prev = both["uwi"].map(cnt.xs(y, level="year")).fillna(0) if y in cnt.index.get_level_values(1) else 0
        nxt = both["uwi"].map(cnt.xs(y + 1, level="year")).fillna(0) if (y + 1) in cnt.index.get_level_values(1) else 0
        rows.append(pd.DataFrame({"prev": prev, "next": nxt}))
    r = pd.concat(rows)
    r["prev_bucket"] = pd.cut(r["prev"], [-0.5, 0.5, 1.5, 2.5, 99], labels=["0", "1", "2", "3 or more"])
    out = r.groupby("prev_bucket", observed=True).agg(well_years=("next", "size"), avg_failures_next_year=("next", "mean"),
                                                      share_with_a_failure_next_year=("next", lambda s: (s > 0).mean()))
    return out.reset_index()


def fig_repeat(t: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(7, 4))
    x = np.arange(len(t))
    ax.bar(x, t["avg_failures_next_year"], color=BLUE, width=0.55)
    for xi, (v, n) in enumerate(zip(t["avg_failures_next_year"], t["well_years"])):
        ax.text(xi, v + 0.05, f"{v:.2f}", ha="center", color=INK, fontsize=11)
    ax.set_xticks(x, [f"{b}\n({n:,} well-years)" for b, n in zip(t["prev_bucket"], t["well_years"])])
    ax.set_xlabel("Crew-needing failures in the previous year")
    ax.set_ylabel("Failures the next year (average)")
    ax.grid(axis="x", visible=False)
    ax.set_ylim(0, t["avg_failures_next_year"].max() * 1.2)
    ax.set_title("Wells that failed before fail again")
    _save(fig, "fig2_repeat_failures.png")


# --------------------------------------------------------------------------- model factors
def factor_importance() -> pd.DataFrame:
    meta = json.loads((MODELS / "risk_7d_meta.json").read_text())
    gain = pd.Series(meta["top_features_gain"], dtype=float)
    def group(f):
        for prefix, name in FEATURE_GROUPS:
            if f.startswith(prefix):
                return name
        return "Other"
    t = gain.groupby(gain.index.map(group)).sum().sort_values(ascending=False)
    t = (t / t.sum()).rename("share_of_importance").reset_index().rename(columns={"index": "factor"})
    t["note"] = "share of the gain of the 25 most important model inputs"
    return t


def fig_factors(t: pd.DataFrame):
    t = t.head(10).iloc[::-1]
    fig, ax = plt.subplots(figsize=(8.5, 4.6))
    y = np.arange(len(t))
    ax.barh(y, t["share_of_importance"] * 100, color=BLUE, height=0.6)
    for yi, v in enumerate(t["share_of_importance"] * 100):
        ax.text(v + 0.5, yi, f"{v:.0f}%", va="center", color=INK2, fontsize=10)
    ax.set_yticks(y, t["factor"])
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("Share of the model's predictive weight (%)")
    ax.set_title("What drives the week-ahead failure risk")
    _save(fig, "fig3_risk_factors.png")


# --------------------------------------------------------------------------- replay: catches
def replay_tables(cf: pd.DataFrame):
    sb = pd.read_csv(OUT / "replay_scoreboard.csv", parse_dates=["production_date"])
    summary = pd.read_csv(OUT / "replay_summary.csv")
    sb = sb.merge(cf[["uwi", "production_date", "failure_type", "cause"]], on=["uwi", "production_date"], how="left")
    sb[["failure_type", "cause"]] = sb[["failure_type", "cause"]].fillna("No failure record")
    by_type = sb.groupby("failure_type").agg(failures=("uwi", "size"), caught_model=("caught_model", "sum"),
                                              caught_rule=("caught_rule", "sum"), value_cad=("value_cad", "sum"),
                                              value_caught_model_cad=("value_cad", lambda s: s[sb.loc[s.index, "caught_model"]].sum()))
    by_type["catch_rate_model"] = by_type["caught_model"] / by_type["failures"]
    by_type["catch_rate_rule"] = by_type["caught_rule"] / by_type["failures"]
    by_cause = sb.groupby("cause").agg(failures=("uwi", "size"), caught_model=("caught_model", "sum"),
                                       caught_rule=("caught_rule", "sum"))
    by_cause["catch_rate_model"] = by_cause["caught_model"] / by_cause["failures"]
    by_field = sb.groupby("field_operating_area").agg(failures=("uwi", "size"), caught_model=("caught_model", "sum"),
                                                      caught_rule=("caught_rule", "sum"))
    by_field["catch_rate_model"] = by_field["caught_model"] / by_field["failures"]
    lead = sb.loc[sb["caught_model"], "lead_days_model"].value_counts().sort_index()
    return (summary, by_type.sort_values("failures", ascending=False).reset_index(),
            by_cause.sort_values("failures", ascending=False).reset_index(),
            by_field.sort_values("failures", ascending=False).reset_index(), lead, sb)


def fig_scoreboard(summary: pd.DataFrame):
    m, r = summary.iloc[0], summary.iloc[1]
    panels = [("Value of failures caught", [m["value_caught_cad"] / 1e6, r["value_caught_cad"] / 1e6], "C$ million", "{:.2f}"),
              ("Warnings followed by a failure", [m["warning_precision"] * 100, r["warning_precision"] * 100], "%", "{:.1f}%"),
              ("Median warning lead time", [m["median_lead_days"], r["median_lead_days"]], "days", "{:.0f} d")]
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.8))
    for ax, (ttl, vals, unit, fmt) in zip(axes, panels):
        ax.bar([0, 1], vals, color=[BLUE, ORANGE], width=0.55)
        for x, v in enumerate(vals):
            ax.text(x, v * 1.02, fmt.format(v), ha="center", color=INK, fontsize=11)
        ax.set_xticks([0, 1], ["WellWatch", "Simple rule"])
        ax.set_ylim(0, max(vals) * 1.22)
        ax.grid(axis="x", visible=False)
        ax.set_title(ttl, fontsize=11.5)
        ax.set_ylabel(unit)
    fig.suptitle("Replay of Jul–Oct 2026 (never seen in training), same 12.3 warnings per day",
                 x=0.01, ha="left", fontsize=13, fontweight="bold")
    fig.tight_layout()
    _save(fig, "fig4_replay_scoreboard.png")


def fig_catch_by_type(t: pd.DataFrame):
    t = t[t["failures"] >= 20]
    fig, ax = plt.subplots(figsize=(8, 4))
    x = np.arange(len(t))
    w = 0.36
    ax.bar(x - w / 2 - 0.01, t["catch_rate_model"] * 100, width=w, color=BLUE, label="WellWatch")
    ax.bar(x + w / 2 + 0.01, t["catch_rate_rule"] * 100, width=w, color=ORANGE, label="Simple rule")
    ax.set_xticks(x, [f"{ft}" + chr(10) + f"({n} failures)" for ft, n in zip(t["failure_type"], t["failures"])])
    ax.set_ylabel("Failures warned about in advance (%)")
    ax.grid(axis="x", visible=False)
    ax.legend()
    ax.set_title("Which failures get a warning, by failure type (replay)")
    _save(fig, "fig5_catch_by_type.png")


# --------------------------------------------------------------------------- planner: profit
def profit(weekly: pd.DataFrame, sb: pd.DataFrame, cf: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    weeks = len(weekly)
    per_year = 365 / (7 * weeks)
    caught = weekly["value_caught_in_time_cad"].sum()
    cost = weekly["plan_cost"].sum()
    expected = (weekly["loss_no_action"] - weekly["loss_with_plan"]).sum()  # already net of visit cost

    # price cases: rescale the lost-oil part of the failure value (replay-period oil share)
    period = cf[cf["production_date"].between(sb["production_date"].min(), sb["production_date"].max())]
    oil_share = period["oil_value_base_cad"].sum() / period["total_base_cad"].sum()
    price_ratio = {c: period[f"oil_value_{c}_cad"].sum() / period["oil_value_base_cad"].sum() for c in ("low", "base", "high")}
    rows = []
    for eff in (0.25, 0.5, 0.75):
        for case in ("low", "base", "high"):
            value = caught * ((1 - oil_share) + oil_share * price_ratio[case])
            net = eff * value - cost
            rows.append({"effectiveness": eff, "oil_value_case": case, "prevented_cad": eff * value,
                         "visit_cost_cad": cost, "net_saving_period_cad": net, "net_saving_per_year_cad": net * per_year})
    t = pd.DataFrame(rows)
    key = {"weeks": weeks, "visits": int(weekly["visits"].sum()), "visit_cost_cad": cost,
           "expected_loss_no_action_cad": weekly["loss_no_action"].sum(),
           "expected_loss_with_plan_cad": weekly["loss_with_plan"].sum(),
           "expected_net_saving_period_cad": expected, "expected_net_saving_per_year_cad": expected * per_year,
           "value_failed_on_planned_wells_cad": weekly["value_failed_planned_cad"].sum(),
           "value_caught_in_time_cad": caught,
           "value_failed_on_rule_wells_cad": weekly["value_failed_rule_cad"].sum(),
           "planned_wells_failed": int(weekly["planned_wells_failed"].sum()),
           "planned_wells_failed_after_visit": int(weekly["planned_wells_failed_after_visit"].sum()),
           "rule_wells_failed": int(weekly["rule_wells_failed"].sum()),
           "oil_share_of_failure_value_base": oil_share, "per_year_factor": per_year}
    # robustness: drop the single week with the most value caught in time
    best = weekly["value_caught_in_time_cad"].idxmax()
    rest = weekly.drop(index=best)
    rest_per_year = 365 / (7 * len(rest))
    key["robustness_without_best_week"] = {
        "dropped_week": str(weekly.loc[best, "planning_day"]),
        "dropped_week_value_caught_cad": float(weekly.loc[best, "value_caught_in_time_cad"]),
        "break_even_effectiveness": float(rest["plan_cost"].sum() / max(rest["value_caught_in_time_cad"].sum(), 1)),
        "net_per_year_cad": {f"{e:.2f}": float((e * rest["value_caught_in_time_cad"].sum() - rest["plan_cost"].sum())
                                               * rest_per_year) for e in (0.25, 0.5, 0.75)},
        "expected_net_per_year_cad": float((rest["loss_no_action"] - rest["loss_with_plan"]).sum() * rest_per_year),
        "value_failed_planned_vs_rule_cad": [float(rest["value_failed_planned_cad"].sum()),
                                             float(rest["value_failed_rule_cad"].sum())],
    }
    key["break_even_effectiveness"] = float(cost / caught)
    return t, key


def fig_weekly(weekly: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(10, 4))
    x = np.arange(len(weekly))
    w = 0.38
    ax.bar(x - w / 2 - 0.01, weekly["value_failed_planned_cad"] / 1e3, width=w, color=BLUE,
           label="WellWatch plan (Gurobi)")
    ax.bar(x + w / 2 + 0.01, weekly["value_failed_rule_cad"] / 1e3, width=w, color=ORANGE,
           label="Simple rule, same number of visits")
    ax.set_xticks(x, pd.to_datetime(weekly["planning_day"]).dt.strftime("%d %b"), rotation=45, ha="right")
    ax.set_ylabel("C$ thousand")
    ax.grid(axis="x", visible=False)
    ax.legend(loc="upper right")
    ax.set_title("Value of failures on the wells each weekly plan sent a crew to")
    _save(fig, "fig6_weekly_plans.png")


def fig_profit(t: pd.DataFrame, key: dict):
    base = t[t["oil_value_case"] == "base"].set_index("effectiveness")
    lo = t[t["oil_value_case"] == "low"].set_index("effectiveness")["net_saving_per_year_cad"]
    hi = t[t["oil_value_case"] == "high"].set_index("effectiveness")["net_saving_per_year_cad"]
    fig, ax = plt.subplots(figsize=(8, 4.2))
    x = np.arange(len(base))
    vals = base["net_saving_per_year_cad"] / 1e6
    ax.bar(x, vals, color=BLUE, width=0.5)
    ax.errorbar(x, vals, yerr=[(vals - lo / 1e6).clip(lower=0), (hi / 1e6 - vals).clip(lower=0)], fmt="none",
                ecolor=INK2, elinewidth=1.2, capsize=5)
    for xi, v in enumerate(vals):
        ax.text(xi + 0.3, v, f"{'−' if v < 0 else '+'}C${abs(v):,.2f}M", va="center", color=INK, fontsize=11)
    ax.axhline(0, color=INK2, linewidth=1)
    be = key["visit_cost_cad"] / key["value_caught_in_time_cad"]
    ax.text(len(base) - 0.5, ax.get_ylim()[0] * 0.8,
            f"Break-even: a visit must prevent {be:.0%} of a failure", ha="right", color=INK2, fontsize=10)
    ax.set_xticks(x, [f"{e:.0%} of a failure\nprevented per visit" for e in base.index])
    ax.set_ylabel("Net saving per year, C$ million")
    ax.grid(axis="x", visible=False)
    ax.set_title("Profit estimate: failures prevented minus visit cost (per year)")
    ax.text(-0.4, ax.get_ylim()[1] * 0.98, "whiskers: low / high oil value", color=INK2, fontsize=9, va="top")
    _save(fig, "fig7_profit_estimate.png")


# --------------------------------------------------------------------------- main
def main() -> None:
    RES.mkdir(parents=True, exist_ok=True)
    d, jobs, failures = data.oil_producers(), data.jobs(), data.failures()
    cf = failure_episodes(d, jobs, failures)
    years = (d["production_date"].max() - d["production_date"].min()).days / 365.25

    by_type, by_cause = cost_table(cf, "failure_type"), cost_table(cf, "cause")
    by_field = cost_table(cf, "field_operating_area").rename(columns={"field_operating_area": "field"})
    by_type.to_csv(RES / "failure_cost_by_type.csv", index=False)
    by_cause.to_csv(RES / "failure_cost_by_cause.csv", index=False)
    by_field.to_csv(RES / "failure_cost_by_field.csv", index=False)
    fig_cost_by_type(by_type)

    rep = repeat_failures(cf, d)
    rep.to_csv(RES / "repeat_failures.csv", index=False)
    fig_repeat(rep)

    fac = factor_importance()
    fac.to_csv(RES / "risk_factors.csv", index=False)
    fig_factors(fac)

    summary, c_type, c_cause, c_field, lead, sb = replay_tables(cf)
    c_type.to_csv(RES / "replay_catch_by_type.csv", index=False)
    c_cause.to_csv(RES / "replay_catch_by_cause.csv", index=False)
    c_field.to_csv(RES / "replay_catch_by_field.csv", index=False)
    lead.rename_axis("lead_days").rename("caught_failures").to_csv(RES / "replay_lead_days.csv")
    fig_scoreboard(summary)
    fig_catch_by_type(c_type)

    weekly = pd.read_csv(OUT / "plan_weekly_summary.csv")
    weekly.to_csv(RES / "weekly_plans.csv", index=False)
    prof, pkey = profit(weekly, sb, cf)
    prof.to_csv(RES / "profit_sensitivity.csv", index=False)
    fig_weekly(weekly)
    fig_profit(prof, pkey)

    # downtime and production losses (all equipment-failure downtime, not only crew-needing)
    eqd = d[d["downtime_class"] == EQUIPMENT]
    key = {
        "data_years": round(years, 2), "oil_wells": int(d["uwi"].nunique()),
        "crew_failures": int(len(cf)), "crew_failures_per_year": len(cf) / years,
        "crew_failure_hours_down": float(cf["hours_down"].sum()),
        "crew_failure_lost_oil_m3": float(cf["lost_oil_m3"].sum()),
        "crew_failure_repair_cad": float(cf["repair_cost_cad"].sum()),
        "crew_failure_oil_value_cad": {c: float(cf[f"oil_value_{c}_cad"].sum()) for c in ("low", "base", "high")},
        "crew_failure_cost_base_per_year_cad": float(cf["total_base_cad"].sum() / years),
        "avg_failure": {"hours_down": float(cf["hours_down"].mean()), "lost_oil_m3": float(cf["lost_oil_m3"].mean()),
                        "repair_cad": float(cf["repair_cost_cad"].mean()),
                        "oil_value_base_cad": float(cf["oil_value_base_cad"].mean())},
        "lost_oil_share_of_cost_base": float(cf["oil_value_base_cad"].sum() / cf["total_base_cad"].sum()),
        "equipment_downtime_hours_all": float(eqd["hours_down"].sum()),
        "equipment_lost_oil_m3_all": float(eqd["lost_oil_m3"].sum()),
        "share_of_equipment_downtime_in_crew_failures": float(cf["hours_down"].sum() / eqd["hours_down"].sum()),
        "failure_type_matched_share": float((cf["failure_type"] != "No failure record").mean()),
        "replay": summary.set_index("approach").to_dict(orient="index"),
        "model_test": json.loads((MODELS / "risk_7d_meta.json").read_text())["test_scores"],
        "planner": pkey,
    }
    (RES / "key_numbers.json").write_text(json.dumps(key, indent=2, default=float))
    print(json.dumps(key, indent=2, default=float))


if __name__ == "__main__":
    main()
