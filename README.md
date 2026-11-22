# Chirality-Canary-Datathon

**WellWatch** warns a week ahead of crew-needing well failures and plans crew visits to minimise the
expected loss, running on Canary Labs historian data. **Results, charts and the profit estimate:
[docs/RESULTS.md](docs/RESULTS.md).**
How a production team would use it day to day, and why each feature helps:
[docs/STRATHCONA_WORKFLOW.md](docs/STRATHCONA_WORKFLOW.md).

## WellWatch

| Part | What it does | Run |
|---|---|---|
| Canary backfill | Loads the daily well data into Canary as `Wells.<Field>.<UWI>.<Signal>` tags | `python -m wellwatch.backfill --all-fields --until 2026-06-30` |
| Risk model | XGBoost, 7-day failure risk, trained on 2021–2024, tested on Jul 2025 – Jun 2026 | `python -m wellwatch.train` |
| Engine + replay | Feeds the held-back Jul–Oct 2026 days into Canary, reads them back, scores every running well, manages alerts (ISA-18.2 style) and writes `WellWatch.*` tags | `python -m wellwatch.engine replay --seconds-per-day 0` (or `live`) |
| Scoreboard | Did warnings come before real failures? Model vs a simple rule | `python -m wellwatch.scoreboard` |
| Crew planner | Gurobi MILP (HiGHS fallback): minimise expected failure loss + visit cost | `python -m wellwatch.plan --day 2026-08-03` or `--weekly 2026-07-06 2026-10-26` |
| Analysis | Tables and charts for the write-up (`docs/`) | `python -m wellwatch.analysis` |
| Desktop app | Fleet overview with map, alert inbox, crew planner, replay/engine control, settings | `python -m wellwatch.app`, or build `dist\WellWatch\WellWatch.exe` with `scripts\build_app.ps1` |
| Tests | Replay, no look-ahead, alerts, scoreboard, planner, app | `python -m pytest tests` |

**Canary setup:** Canary Historian, Views and Store & Forward on `localhost` (read API port 55236, write
API port 55293). Create an API token in Canary Identity and store it with the app (Settings & health →
Store token). It goes to Windows Credential Manager and is never written to a file. The virtual view
`WellWatchAssets` (source `localhost`) merges each well's `Wells` and `WellWatch` tags into one `Well`
asset. The Calcs & Events job "WellWatch warning" raises a Canary event while a well's `AlertLevel` is
2 or higher.

**Without Canary:** step-by-step guide in [docs/RUN_OFFLINE.md](docs/RUN_OFFLINE.md). The app's offline
mode works from the competition files and a copy of `cache/alerts.db` from a replay.

Never commit data, `cache/`, `logs/`, `dist/`, licence files or tokens. The repository is public.

**Known issue:** 6 wells have no field in the source data, so their Canary tags sit under a field
named `nan` (for example `Wells.nan.<UWI>.OilRate`). They are scored normally.

## Team notebooks: how to run

1. **Data:** copy the three competition files into the repository root (next to this README):
   `daily_production.parquet`, `well_jobs.parquet`, `production_failures.parquet`.
   They are git-ignored and must never be committed (competition terms of use).
2. **Environment:** Python 3.12, then `pip install -r requirements.txt`.
3. **Run the notebooks in `datasets/`** (each loads the data itself through `datasets/data_utils.py`):
   - `well_jobs.ipynb`, `production_failures.ipynb`: quick per-file exploration (seconds).
   - `daily_production.ipynb`: the team's first EDA, PCA and XGBoost (needs ~8 GB RAM for the PCA cells).
   - `downtime_drivers.ipynb`: **main analysis** (about 15-25 minutes). It writes figures and tables to `outputs/`.

   To run headless: `python -m nbconvert --to notebook --execute --inplace datasets/downtime_drivers.ipynb`

## Repository layout

| Path | What it is |
|---|---|
| `datasets/data_utils.py` | Shared loaders (no file dialogs) and documented cleaning rules |
| `datasets/downtime_drivers.ipynb` | Goal analysis: downtime classes, Pareto, explanatory XGBoost + SHAP, failure cross-reference, cost, profit scenarios |
| `datasets/*.ipynb` (others) | Per-dataset exploration |
| `outputs/` | Presentation-ready PNG charts and CSV tables produced by `downtime_drivers.ipynb` |
