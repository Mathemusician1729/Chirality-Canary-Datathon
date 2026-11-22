# WellWatch Alert and Optimization System
**Authored by the Fantastic 4**: Carson Chan, Matthew Tran, Prabhasees Raina, Karanvir Mann

[<u><b>Summary Video](https://youtu.be/5Z0hkO-mv1E)</u></b> | [<u><b>Project Slides](WellWatch.pdf)</u></b>
### Motivation
In large oil and gas systems, there are many parts working together in order to maintain a stable, steady pipeline, but consequently, it can also be difficult to keep track of all these parts and catch signs or risks of those parts failing early enough before a downtime is caused in the system, and when downtimes occur, the company has to spend extra costs to fix them, as well as lose out on revenue from potential oil that could have been produced.

### Research Question
Our team then, wanted to focus our project on what factors contribute the most to production downtime and losses, how much they affect company costs/revenue, and our goal was to determine how can we alert and notify the company as soon as possible of risks of production failure, and how we can optimize and schedule jobs to resolve them, to minimize the overall costs from sending workers as well as from when the production failures do happen.


## Our Solution: WellWatch 
#### ***Solution Category***: *AI/ML Prototype, Analytical Dashboard*

**WellWatch** is an all-in-one centralized platform that both warns a week ahead of crew-needing well failures, and plans optimal crew visits and scheduling to minimise expected losses and costs spent on fixing failures during production. 

## Features
| Feature | What it does | How to Run |
|---|---|---|
| **Canary Labs Integration** | **Utilized Canary labs to load and store historical daily well data into Canary as `Wells.<Field>.<UWI>.<Signal>` tags. The engine reads its inputs back from Canary and writes risk, alerts and planned visits to Canary as `WellWatch.*` tags; a Canary asset view groups each well's tags, and a Canary Calcs & Events job raises an alarm for wells at warning level.** | `python -m wellwatch.backfill --all-fields --until 2026-06-30` |
| Risk model | XGBoost, 7-day failure risk, trained on 2021–2024, tested on Jul 2025 – Jun 2026 | `python -m wellwatch.train` |
| Engine + replay | Feeds the held-back Jul–Oct 2026 days into Canary, reads them back, scores every running well, manages alerts (ISA-18.2 style) and writes `WellWatch.*` tags | `python -m wellwatch.engine replay --seconds-per-day 0` (or `live`) |
| Scoreboard | Did warnings come before real failures? Model vs a simple rule | `python -m wellwatch.scoreboard` |
| Crew planner | Gurobi MILP/HiGHs: minimise expected failure loss + visit cost | `python -m wellwatch.plan --day 2026-08-03` or `--weekly 2026-07-06 2026-10-26` |
| Analysis | Tables and charts for the write-up (`docs/`) | `python -m wellwatch.analysis` |
| Desktop app | Fleet overview with an interactive map displaying alerts, coded by severity, an alert inbox, crew planner, and a replay/engine control to simulate live data tracking | `python -m wellwatch.app`, or build `dist\WellWatch\WellWatch.exe` with `scripts\build_app.ps1` |
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

## How To Run

1. **Copy the three competition files into the repository root** (next to this README):
   `daily_production.parquet`, `well_jobs.parquet`, `production_failures.parquet`.
   They are git-ignored and must never be committed (competition terms of use).
2. **Environment:** Python 3.12, then `pip install -r requirements.txt`.
3. **Run the notebooks in `datasets/`** (each loads the data itself through `datasets/data_utils.py`):
   - `well_jobs.ipynb`, `production_failures.ipynb`: quick per-file exploration (seconds).
   - `daily_production.ipynb`: the team's first EDA, PCA and XGBoost (needs ~8 GB RAM for the PCA cells).
   - `downtime_drivers.ipynb`: **main analysis** (about 15-25 minutes). It writes figures and tables to `outputs/`.

   To run headless: 
   ```
   python -m nbconvert --to notebook --execute --inplace datasets \ downtime_drivers.ipynb
   ```

   **Results, charts and the profit estimates** can be found in
[docs/RESULTS.md](docs/RESULTS.md).

   **How a production team would use it day to day, and why each feature helps**:
   [docs/STRATHCONA_WORKFLOW.md](docs/STRATHCONA_WORKFLOW.md).

## Repository layout

| Path | What it is |
|---|---|
| `datasets/data_utils.py` | Shared loaders (no file dialogs) and documented cleaning rules |
| `datasets/downtime_drivers.ipynb` | Goal analysis: downtime classes, Pareto, explanatory XGBoost + SHAP, failure cross-reference, cost, profit scenarios |
| `datasets/*.ipynb` (others) | Per-dataset exploration |
| `outputs/` | Presentation-ready PNG charts and CSV tables produced by `downtime_drivers.ipynb` |
| `docs/` | The project write-up and the numbers behind it (nothing here is read by the code). `RESULTS.md`: all results, charts and assumptions. `STRATHCONA_WORKFLOW.md`: how a production team would use WellWatch. `RUN_OFFLINE.md`: run the app without Canary. `VIDEO_SCRIPT_5MIN.md` / `PRESENTATION_SCRIPT_10MIN.md`: presentation scripts. `WellWatch_Code_Guide.pdf`: how every part of the code and the Canary integration work. `figures/`: the 7 result charts and `results/`: the fleet-level tables behind them (no well IDs), both made by `python -m wellwatch.analysis`. |
| `models/` | The trained week-ahead risk model, written by `python -m wellwatch.train` and loaded by the engine (`wellwatch/score.py`): `risk_7d.json` is the XGBoost model (300 trees), `risk_7d_calibration.json` turns its raw score into a real probability (isotonic calibration), and `risk_7d_meta.json` lists the 57 input features, the training / calibration / test dates and the test-year scores (shown in the app's model card). Bundled inside the packaged app. |
| `scripts/` | Build and create application, run `wellwatch_app.py` using the instructions from before, to run WellWatch app. |
| `tests/` | Run tests on each WellWatch feature script found in `wellwatch` folder. |
| `wellwatch/` | All features included to run WellWatch app, including alert prediction (`wellwatch/alerts.py`) and gurobi planner (`wellwatch/planner.py`). Application build scripts are included under `wellwatch/app`. |

## External Resources Used
**Financial Reports/Market Indicators used for Oil Value**:
- [<u><b>Strathcona Financial Reports](https://www.strathconaresources.com/investors/reports/)</u></b> 
- [<u><b>Cenovus Market Indicators](https://cenovus.com/Investors)</u></b> 
- [<u><b>Cenovus Financial Reports/Results](https://cenovus.com/Investors/Financial-results-and-reports)</u></b>
