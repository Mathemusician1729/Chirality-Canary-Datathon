# External data: value of a lost barrel (2021Q1–2026Q4)

The data records lost oil in m³ but not what that oil was worth. This folder holds a cleaned quarterly price table built only from **public** sources. Strathcona's own quarterly reports come first; Cenovus's published benchmark tables fill the quarters before Strathcona was a public company.

## Files

| File | What it is |
|---|---|
| `oil_prices_quarterly.csv` | **Use this.** One row per quarter, 2021Q1–2026Q4. |
| `oil_prices_quality_report.csv` | Every validation check run by the build, with its result. |
| `sources/benchmark_prices_reported.csv` | Every benchmark number as published: one row per source, period and metric. `value_as_printed` keeps the original before unit or sign normalisation. |
| `sources/lloydminster_segment_reported.csv` | Strathcona's Lloydminster segment economics (C$/boe) from its MD&As. |
| `sources/sources.csv` | Publisher, document, date and URL for every `source_id`. |
| `sources/source_overlap_comparison.csv` | Every figure published by more than one source, with the spread between them. |

Rebuild with `python datasets/build_oil_prices.py`. The script stops if any check fails.

## Columns in `oil_prices_quarterly.csv`

| Column | Meaning |
|---|---|
| `quarter`, `period_start`, `period_end`, `days` | Calendar quarter. 2026Q4 covers October only, because the competition data ends in October 2026 (`is_partial_quarter`). |
| `wti_usd_bbl`, `wcs_usd_bbl`, `wti_wcs_diff_usd_bbl` | Benchmark crude prices, US$/bbl (quarterly averages). |
| `fx_cad_per_usd` | Average exchange rate, C$ per US$1. |
| `wcs_cad_bbl` | WCS Hardisty in C$/bbl, the heavy-oil benchmark. |
| `benchmark_source` | `source_id` the C$ WCS figure came from. |
| `lloyd_basis` | `reported` = Strathcona published the Lloydminster numbers for that quarter; `estimated from WCS` = rebuilt from the WCS price (method below). |
| `lloyd_realized_cad_bbl` | Lloydminster sales price net of blending, C$/bbl. |
| `lloyd_royalties_cad_bbl`, `lloyd_transport_cad_bbl`, `lloyd_opex_energy_cad_bbl`, `lloyd_opex_nonenergy_cad_bbl` | Per-barrel deductions. |
| `lloyd_field_netback_cad_bbl` | Realized price minus all of the above. |
| `value_{low,base,high}_cad_bbl` / `_cad_m3` | **Value of one lost barrel / m³** (see below). 1 m³ = 6.2898 bbl. |

## How a lost barrel is valued

| Case | Formula | Reasoning |
|---|---|---|
| `high` | realized price | Revenue lost. An upper bound: it ignores costs that are also avoided. |
| **`base`** | realized price − royalties − transportation − energy operating cost | **Cash margin lost.** Royalties, trucking/processing and pump power are only paid on oil that is produced, so they are saved when a well is down. Non-energy operating cost (labour, maintenance, overhead) is mostly fixed and is still paid. |
| `low` | field operating netback (all field costs deducted) | Conservative lower bound. |

Strathcona published Lloydminster segment economics for 5 quarters (2023Q4, 2024Q1, 2024Q3, 2024Q4, 2025Q1). For other quarters the build uses the median relationships from those 5 quarters:

- realized price = **1.051 × WCS (C$)** (range 1.043–1.098)
- royalties = **10.5%** of realized price (range 9.6%–13.9%)
- transportation **C$14.46**, energy opex **C$6.68**, non-energy opex **C$10.76** per bbl (medians)

## Validation (all pass; see the quality report)

- Every value cites a listed source. Units are consistent after conversion (Cenovus prints US$ per C$, converted to C$ per US$). Quarter labels match their dates.
- **70 figures** were published by more than one source (e.g. Strathcona and Cenovus both report WCS). All agree within **0.5%**.
- C$ WCS = US$ WCS × exchange rate (within 0.16%), and WTI − WCS = the published differential (within US$0.02).
- **34** six-month, nine-month and annual averages match the average of their quarters (within 0.25%). This catches transcription errors.
- The Lloydminster tables add up (sales net of blending, netback, royalty rate), and duplicate periods across documents agree exactly.

## Caveats

- **Segment scope:** until Q3 2025 the Lloydminster segment combined conventional heavy oil (the competition wells) with thermal (SAGD) projects. Thermal has higher energy costs, so the `base` value is probably slightly understated for conventional wells.
- **Per boe vs per bbl:** segment figures are per boe. The segment is almost entirely heavy oil, so they are used as per bbl.
- **Estimated quarters** assume the 2023–2025 relationships held in 2021–2022 and 2025–2026. Energy costs in particular were higher in 2022 (gas prices).
- **Deferred vs lost:** some "lost" oil may be produced later (deferred), so these values describe lost *cash flow timing* as much as lost reserves.
- The Cenovus workbook is transcribed, not redistributed. Its values are labelled `CVE_XLSX`.
