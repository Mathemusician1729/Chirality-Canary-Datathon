"""Build a clean quarterly heavy-oil value table (2021Q1-2026Q4) from public sources.

Why: the downtime analysis and the early-warning app put a dollar value on every lost barrel.
Instead of one flat placeholder price, each quarter gets the price Strathcona actually earned
(Lloydminster heavy oil), minus the costs that disappear when a well is down.

Inputs (verbatim transcriptions, one row per published number):
    data/external/sources/benchmark_prices_reported.csv    WTI, WCS (US$ and C$), FX, WTI-WCS differential
    data/external/sources/lloydminster_segment_reported.csv Strathcona Lloydminster segment, C$/boe
    data/external/sources/sources.csv                       where every number came from

Outputs:
    data/external/oil_prices_quarterly.csv        one row per quarter
    data/external/oil_prices_quality_report.csv   every validation check and its result

Run from anywhere:  python datasets/build_oil_prices.py
The script exits with an error if any check fails. See data/external/README.md for the method.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "data" / "external"
SRC = EXT / "sources"

BBL_PER_M3 = 6.2898
FIRST_QUARTER, LAST_QUARTER = "2021Q1", "2026Q4"  # span of the competition data
SOURCE_PRIORITY = ("SCR_", "CVE_SUPP_", "CVE_XLSX")  # Strathcona first, then Cenovus PDFs, then workbook
EXPECTED_UNITS = {
    "wti_usd_bbl": "US$/bbl", "wcs_usd_bbl": "US$/bbl", "wcs_cad_bbl": "C$/bbl",
    "fx_cad_per_usd": "C$ per US$1", "wti_wcs_diff_usd_bbl": "US$/bbl",
}
PLAUSIBLE = {  # (min, max) for a quarterly average in 2021-2026
    "wti_usd_bbl": (30, 150), "wcs_usd_bbl": (20, 130), "wcs_cad_bbl": (25, 170),
    "fx_cad_per_usd": (1.15, 1.50), "wti_wcs_diff_usd_bbl": (3, 40),
}
SEGMENT_COST_COLUMNS = ["royalties_cad_boe", "opex_energy_cad_boe", "opex_nonenergy_cad_boe", "transport_cad_boe"]

CHECKS: list[dict] = []


def check(name: str, passed: bool, detail: str, severity: str = "error") -> None:
    CHECKS.append({"check": name, "passed": bool(passed), "severity": severity, "detail": detail})


# --------------------------------------------------------------------------- load and validate inputs
def load_inputs():
    sources = pd.read_csv(SRC / "sources.csv")
    bench = pd.read_csv(SRC / "benchmark_prices_reported.csv",
                        parse_dates=["period_start", "period_end"], dtype={"value_as_printed": str})
    seg = pd.read_csv(SRC / "lloydminster_segment_reported.csv", parse_dates=["period_start", "period_end"])

    unknown = (set(bench["source_id"]) | set(seg["source_id"])) - set(sources["source_id"])
    check("every value cites a listed source", not unknown, f"unknown source ids: {sorted(unknown)}")
    check("benchmark values are numeric", bench["value"].notna().all() and np.isfinite(bench["value"]).all(),
          f"{int(bench['value'].isna().sum())} missing values")
    check("period start is before period end", (bench["period_start"] <= bench["period_end"]).all()
          and (seg["period_start"] <= seg["period_end"]).all(), "period dates checked in both inputs")
    q = bench[bench["period_type"] == "quarter"]
    label_ok = (q["period"] == q["period_start"].dt.to_period("Q").astype(str)) & (
        q["period_end"] == q["period_start"] + pd.offsets.QuarterEnd(0))
    check("quarter labels match their dates", label_ok.all(), f"{int((~label_ok).sum())} mismatched rows")
    dup = bench.duplicated(["source_id", "period", "period_type", "metric"])
    check("no duplicate rows within a source", not dup.any(), f"{int(dup.sum())} duplicates")
    return sources, bench, seg


def normalise_benchmarks(bench: pd.DataFrame) -> pd.DataFrame:
    """Convert every value to the canonical unit of its metric."""
    out = bench.copy()
    fx_us = out["metric"] == "fx_usd_per_cad"  # Cenovus prints US$ per C$1; convert to C$ per US$1
    out.loc[fx_us, "value"] = (1.0 / out.loc[fx_us, "value"]).round(6)
    out.loc[fx_us, "metric"] = "fx_cad_per_usd"
    out.loc[fx_us, "unit"] = "C$ per US$1"
    out.loc[fx_us, "notes"] = "converted from US$ per C$1 as printed"
    bad_units = out[out["unit"] != out["metric"].map(EXPECTED_UNITS)]
    check("one unit per metric after conversion", bad_units.empty,
          f"{len(bad_units)} rows with unexpected units: {bad_units[['metric', 'unit']].drop_duplicates().values.tolist()}")
    return out


# --------------------------------------------------------------------------- reconcile sources
def source_rank(source_id: str) -> int:
    for i, prefix in enumerate(SOURCE_PRIORITY):
        if source_id.startswith(prefix):
            return i
    return len(SOURCE_PRIORITY)


def reconcile(bench: pd.DataFrame, sources: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compare every number published by more than one source; pick one value per period and metric."""
    b = bench.merge(sources[["source_id", "published"]], on="source_id", how="left")
    b["rank"] = b["source_id"].map(source_rank)
    spread = (b.groupby(["period", "period_type", "metric"])["value"]
                .agg(n_sources="count", min_value="min", max_value="max").reset_index())
    spread = spread[spread["n_sources"] > 1].copy()
    spread["relative_spread"] = (spread["max_value"] - spread["min_value"]) / spread[["min_value", "max_value"]].mean(axis=1)
    worst = spread.sort_values("relative_spread", ascending=False).head(3)
    check("sources agree within 2% where they overlap", (spread["relative_spread"] <= 0.02).all(),
          f"{len(spread)} overlapping figures; largest gaps: "
          + "; ".join(f"{r.period} {r.metric} {r.min_value}-{r.max_value}" for r in worst.itertuples()))
    check("sources agree within 0.5% where they overlap", (spread["relative_spread"] <= 0.005).all(),
          f"{int((spread['relative_spread'] > 0.005).sum())} of {len(spread)} overlapping figures differ by more than 0.5%",
          severity="warning")
    chosen = (b.sort_values(["rank", "published"], ascending=[True, False])
               .drop_duplicates(["period", "period_type", "metric"]))
    return chosen, spread


def build_quarters(chosen: pd.DataFrame) -> pd.DataFrame:
    quarters = pd.period_range(FIRST_QUARTER, LAST_QUARTER, freq="Q")
    qrows = chosen[chosen["period_type"] == "quarter"]
    wide = qrows.pivot(index="period", columns="metric", values="value")
    src = qrows.pivot(index="period", columns="metric", values="source_id")
    out = pd.DataFrame({"quarter": quarters.astype(str)})
    out["period_start"] = quarters.start_time.normalize()
    out["period_end"] = quarters.end_time.normalize()
    for m in EXPECTED_UNITS:
        out[m] = out["quarter"].map(wide[m]) if m in wide else np.nan
    out["benchmark_source"] = out["quarter"].map(src["wcs_cad_bbl"]) if "wcs_cad_bbl" in src else np.nan
    out["is_partial_quarter"] = False

    # Final quarter: the competition data ends in October 2026, so use the October monthly average.
    last = out["quarter"] == LAST_QUARTER
    month = chosen[(chosen["period_type"] == "month") & (chosen["period"] == "2026-10")].set_index("metric")["value"]
    if out.loc[last, "wcs_cad_bbl"].isna().all() and not month.empty:
        for m in ["wti_usd_bbl", "wcs_usd_bbl", "wti_wcs_diff_usd_bbl", "fx_cad_per_usd"]:
            out.loc[last, m] = month.get(m, np.nan)
        out.loc[last, "benchmark_source"] = "CVE_XLSX (October 2026 only)"
        out.loc[last, "is_partial_quarter"] = True
        out.loc[last, "period_end"] = pd.Timestamp("2026-10-31")

    # Fill C$ WCS from US$ WCS x FX where no source printed it (workbook quarters and October 2026).
    need = out["wcs_cad_bbl"].isna() & out["wcs_usd_bbl"].notna() & out["fx_cad_per_usd"].notna()
    out.loc[need, "wcs_cad_bbl"] = (out.loc[need, "wcs_usd_bbl"] * out.loc[need, "fx_cad_per_usd"]).round(2)
    out.loc[need, "benchmark_source"] = out.loc[need, "benchmark_source"].fillna("CVE_XLSX") + " (C$ = US$ x FX)"
    # Fill FX where a source printed both WCS prices but no rate.
    need_fx = out["fx_cad_per_usd"].isna() & out["wcs_cad_bbl"].notna() & out["wcs_usd_bbl"].notna()
    out.loc[need_fx, "fx_cad_per_usd"] = (out.loc[need_fx, "wcs_cad_bbl"] / out.loc[need_fx, "wcs_usd_bbl"]).round(4)
    return out


def check_quarters(q: pd.DataFrame, chosen: pd.DataFrame) -> None:
    missing = q.loc[q[["wti_usd_bbl", "wcs_cad_bbl"]].isna().any(axis=1), "quarter"].tolist()
    check(f"every quarter {FIRST_QUARTER}-{LAST_QUARTER} has WTI and C$ WCS", not missing, f"missing: {missing}")

    both = q.dropna(subset=["wcs_cad_bbl", "wcs_usd_bbl", "fx_cad_per_usd"])
    rel = (both["wcs_cad_bbl"] - both["wcs_usd_bbl"] * both["fx_cad_per_usd"]).abs() / both["wcs_cad_bbl"]
    check("C$ WCS = US$ WCS x exchange rate (within 1%)", (rel <= 0.01).all(),
          f"{len(both)} quarters checked; largest gap {rel.max():.2%} in {both.loc[rel.idxmax(), 'quarter'] if len(both) else '-'}")

    d = q.dropna(subset=["wti_usd_bbl", "wcs_usd_bbl", "wti_wcs_diff_usd_bbl"])
    gap = (d["wti_usd_bbl"] - d["wcs_usd_bbl"] - d["wti_wcs_diff_usd_bbl"]).abs()
    check("WTI - WCS = reported differential (within US$0.10)", (gap <= 0.10).all(),
          f"{len(d)} quarters checked; largest gap US${gap.max():.2f}")

    for metric, (lo, hi) in PLAUSIBLE.items():
        v = q[metric].dropna()
        check(f"{metric} within plausible range {lo}-{hi}", v.between(lo, hi).all(),
              f"min {v.min():.2f}, max {v.max():.2f}")

    # Multi-quarter figures (6M, 9M, full year) must match the average of the chosen quarters.
    idx = q.set_index("quarter")
    rows = []
    for r in chosen[chosen["period_type"].isin(["6M", "9M", "year"])].itertuples():
        covered = pd.period_range(r.period_start, r.period_end, freq="Q").astype(str)
        vals = idx.reindex(covered)[r.metric]
        if vals.isna().any():
            continue
        rows.append(abs(vals.mean() - r.value) / r.value)
    rows = pd.Series(rows, dtype=float)
    check("6-month, 9-month and annual averages match their quarters (within 0.5%)", (rows <= 0.005).all(),
          f"{len(rows)} multi-quarter figures checked; largest gap {rows.max():.2%}")


# --------------------------------------------------------------------------- Lloydminster economics
def lloydminster(q: pd.DataFrame, seg: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    net = seg["sales_cad_boe"] + seg["purchased_sales_cad_boe"] + seg["blending_cad_boe"] + seg["purchased_cost_cad_boe"]
    check("segment: sales net of blending adds up (within C$0.02)",
          ((net - seg["sales_net_blending_cad_boe"]).abs() <= 0.02).all(), f"{len(seg)} rows checked")
    netback = seg["sales_net_blending_cad_boe"] - seg[SEGMENT_COST_COLUMNS].sum(axis=1)
    check("segment: field operating netback adds up (within C$0.02)",
          ((netback - seg["field_netback_cad_boe"]).abs() <= 0.02).all(), f"{len(seg)} rows checked")
    rr = seg.dropna(subset=["royalty_rate_pct"])
    rate_gap = (100 * rr["royalties_cad_boe"] / rr["sales_net_blending_cad_boe"] - rr["royalty_rate_pct"]).abs()
    check("segment: royalty rate = royalties / sales (within 0.15 points)", (rate_gap <= 0.15).all(),
          f"largest gap {rate_gap.max():.2f} points")
    value_cols = [c for c in seg.columns if c.endswith("_cad_boe") or c.endswith("_boe_d")]
    conflicts = seg.groupby("period")[value_cols].nunique().gt(1).any(axis=1)
    check("segment: duplicate periods agree exactly", not conflicts.any(),
          f"conflicting periods: {conflicts[conflicts].index.tolist()}")

    rep = seg[seg["period_type"] == "quarter"].drop_duplicates("period").set_index("period")
    wcs = q.set_index("quarter")["wcs_cad_bbl"]
    params = {
        "realized_to_wcs": (rep["sales_net_blending_cad_boe"] / wcs.reindex(rep.index)),
        "royalty_share": rep["royalties_cad_boe"] / rep["sales_net_blending_cad_boe"],
        "transport_cad_boe": rep["transport_cad_boe"],
        "opex_energy_cad_boe": rep["opex_energy_cad_boe"],
        "opex_nonenergy_cad_boe": rep["opex_nonenergy_cad_boe"],
    }
    summary = {k: {"median": float(v.median()), "min": float(v.min()), "max": float(v.max()), "n": int(v.count())}
               for k, v in params.items()}
    check("segment: realized price tracks WCS (ratio 0.9-1.2)",
          0.9 <= summary["realized_to_wcs"]["min"] and summary["realized_to_wcs"]["max"] <= 1.2,
          f"ratio range {summary['realized_to_wcs']['min']:.3f}-{summary['realized_to_wcs']['max']:.3f}")

    out = q.copy()
    reported = out["quarter"].isin(rep.index)
    r = rep.reindex(out["quarter"]).reset_index(drop=True)
    med = {k: v["median"] for k, v in summary.items()}
    realized = np.where(reported, r["sales_net_blending_cad_boe"], med["realized_to_wcs"] * out["wcs_cad_bbl"])
    out["lloyd_basis"] = np.where(reported, "reported", "estimated from WCS")
    out["lloyd_realized_cad_bbl"] = realized
    out["lloyd_royalties_cad_bbl"] = np.where(reported, r["royalties_cad_boe"], med["royalty_share"] * realized)
    out["lloyd_transport_cad_bbl"] = np.where(reported, r["transport_cad_boe"], med["transport_cad_boe"])
    out["lloyd_opex_energy_cad_bbl"] = np.where(reported, r["opex_energy_cad_boe"], med["opex_energy_cad_boe"])
    out["lloyd_opex_nonenergy_cad_bbl"] = np.where(reported, r["opex_nonenergy_cad_boe"], med["opex_nonenergy_cad_boe"])
    out["lloyd_field_netback_cad_bbl"] = (out["lloyd_realized_cad_bbl"] - out["lloyd_royalties_cad_bbl"]
                                          - out["lloyd_transport_cad_bbl"] - out["lloyd_opex_energy_cad_bbl"]
                                          - out["lloyd_opex_nonenergy_cad_bbl"])
    check("segment: rebuilt netback equals reported netback in reported quarters",
          np.allclose(out.loc[reported, "lloyd_field_netback_cad_bbl"].to_numpy(),
                      rep.reindex(out.loc[reported, "quarter"])["field_netback_cad_boe"].to_numpy(), atol=0.02),
          f"{int(reported.sum())} reported quarters")

    # Value of one lost barrel (three cases).
    out["value_high_cad_bbl"] = out["lloyd_realized_cad_bbl"]                         # revenue lost
    out["value_base_cad_bbl"] = (out["lloyd_realized_cad_bbl"] - out["lloyd_royalties_cad_bbl"]
                                 - out["lloyd_transport_cad_bbl"] - out["lloyd_opex_energy_cad_bbl"])  # cash margin lost
    out["value_low_cad_bbl"] = out["lloyd_field_netback_cad_bbl"]                     # after all field costs
    for case in ("high", "base", "low"):
        out[f"value_{case}_cad_m3"] = out[f"value_{case}_cad_bbl"] * BBL_PER_M3
    ordered = (out["value_low_cad_bbl"] < out["value_base_cad_bbl"]) & (out["value_base_cad_bbl"] < out["value_high_cad_bbl"])
    check("value cases are ordered low < base < high in every quarter", ordered.all(),
          f"{int((~ordered).sum())} quarters out of order")
    check("every quarter has a positive base value", (out["value_base_cad_bbl"] > 0).all(),
          f"min C${out['value_base_cad_bbl'].min():.2f}/bbl")
    return out, summary


def main() -> int:
    sources, bench, seg = load_inputs()
    bench = normalise_benchmarks(bench)
    chosen, spread = reconcile(bench, sources)
    q = build_quarters(chosen)
    check_quarters(q, chosen)
    q, summary = lloydminster(q, seg)

    money = [c for c in q.columns if c.endswith(("_bbl", "_m3"))]
    q[money] = q[money].astype(float).round(2)
    q["fx_cad_per_usd"] = q["fx_cad_per_usd"].round(4)
    q["days"] = (q["period_end"] - q["period_start"]).dt.days + 1
    cols = ["quarter", "period_start", "period_end", "days", "is_partial_quarter",
            "wti_usd_bbl", "wcs_usd_bbl", "wti_wcs_diff_usd_bbl", "fx_cad_per_usd", "wcs_cad_bbl", "benchmark_source",
            "lloyd_basis", "lloyd_realized_cad_bbl", "lloyd_royalties_cad_bbl", "lloyd_transport_cad_bbl",
            "lloyd_opex_energy_cad_bbl", "lloyd_opex_nonenergy_cad_bbl", "lloyd_field_netback_cad_bbl",
            "value_low_cad_bbl", "value_base_cad_bbl", "value_high_cad_bbl",
            "value_low_cad_m3", "value_base_cad_m3", "value_high_cad_m3"]
    q[cols].to_csv(EXT / "oil_prices_quarterly.csv", index=False, date_format="%Y-%m-%d")

    for name, s in summary.items():
        CHECKS.append({"check": f"estimation parameter: {name}", "passed": True, "severity": "info",
                       "detail": f"median {s['median']:.4f} (range {s['min']:.4f}-{s['max']:.4f}, n={s['n']} reported quarters)"})
    report = pd.DataFrame(CHECKS)
    report.to_csv(EXT / "oil_prices_quality_report.csv", index=False)
    spread.to_csv(EXT / "sources" / "source_overlap_comparison.csv", index=False)

    print(report.to_string(index=False, max_colwidth=110))
    print(f"\nWrote {len(q)} quarters to {EXT / 'oil_prices_quarterly.csv'}")
    failed = report[(~report["passed"]) & (report["severity"] == "error")]
    if not failed.empty:
        print(f"\n{len(failed)} check(s) FAILED", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
