# Run the WellWatch app on your own computer (no Canary needed)

This guide gets the WellWatch desktop app running on a Windows laptop **without Canary**. You can
browse every result: the fleet overview and map, the alert inbox, the Gurobi crew planner, the replay
scoreboard, the model card and the data-quality check.

Plan for about **30 minutes**, most of it waiting for installs. You need about **4 GB** of free disk
space and an internet connection (for the install and the map background).

> Just want the numbers and charts for the slides? You don't need any of this. Open
> [docs/RESULTS.md](RESULTS.md) on GitHub (branch `feature/wellwatch-app`); the charts are in
> [docs/figures/](figures/).

---

## What works offline

| Works | Needs Canary (does not work offline) |
|---|---|
| Fleet overview, KPI tiles, map, top wells, fields | Starting a replay or the live engine |
| Alert inbox: browse, filter, acknowledge, shelve, close (saved on your computer only) | Copying alert actions to Canary |
| Crew planner: solve, schedule, what-if, export work orders | "Send to Canary" |
| Replay scoreboard and "Re-run scoreboard" | |
| Model card, data-quality check, backups | |

The alerts and scores you see come from the replay already run on the team's Canary machine.
You receive them as one file, `alerts.db` (step 3).

---

## 1. Install the tools (once)

1. **Python 3.12.** Download "Windows installer (64-bit)" from
   <https://www.python.org/downloads/windows/>. On the first installer screen, **tick "Add python.exe
   to PATH"**, then Install.
2. **Git** (optional; you can download a ZIP instead): <https://git-scm.com/download/win>, default
   options.

Check in **PowerShell** (Start menu → type "PowerShell"):

```powershell
python --version      # should say Python 3.12.x
```

If it says "Python was not found", close and reopen PowerShell. If it still fails, use `py -3.12`
wherever this guide says `python`.

## 2. Get the code

**With Git:**

```powershell
cd $HOME\Documents
git clone https://github.com/Mathemusician1729/Chirality-Canary-Datathon.git
cd Chirality-Canary-Datathon
git checkout feature/wellwatch-app
```

**Without Git:** on GitHub, switch the branch drop-down to **`feature/wellwatch-app`**, then
**Code → Download ZIP**. Unzip it into `Documents`. Then in PowerShell, `cd` into the unzipped folder
(the one that contains `README.md`).

To get later updates with Git: `git pull`.

## 3. Add the data files

These are **not on GitHub** (competition data; the repository is public). Put them in the repository
folder, next to `README.md`:

| File | Where to get it | Put it in |
|---|---|---|
| `daily_production.parquet` | the datathon data files | repository folder |
| `well_jobs.parquet` | the datathon data files | repository folder |
| `production_failures.parquet` | the datathon data files | repository folder |
| `alerts.db` | the teammate who ran the replay (shared privately: Discord DM or Drive, **never** GitHub) | a new folder named **`cache`** in the repository folder |

When you're done, the folder should look like this:

```
Chirality-Canary-Datathon\
    README.md
    daily_production.parquet
    well_jobs.parquet
    production_failures.parquet
    cache\
        alerts.db
    wellwatch\   docs\   models\   ...
```

All of these are git-ignored, so they can't be committed by accident. Please don't share them anywhere
public.

## 4. Install the Python packages (once, about 5–10 minutes)

In PowerShell, in the repository folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

This creates a private Python environment in the `.venv` folder (git-ignored) and installs everything
the app needs (about 2 GB).

- From here on, run Python as `.\.venv\Scripts\python.exe`. Or activate the environment first with
  `.\.venv\Scripts\Activate.ps1` and then use plain `python`. If PowerShell refuses to run the
  activate script, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or just use the full
  path.
- **Gurobi:** `pip` installs Gurobi with a small free licence. When a plan is too big for it, the planner
  automatically uses the free HiGHS solver instead, with the same answers, just a little slower. You
  don't need a Gurobi licence.

## 5. Turn on offline mode before the first start

Create the app's settings file so it doesn't look for Canary:

```powershell
'{"offline": true}' | Out-File -Encoding ascii cache\app_settings.json
```

(You can also skip this and tick **Offline mode** later under Settings & health. Without it, the app
just reports "Canary: offline" in the bottom bar.)

## 6. Prepare the scoreboard (recommended, about 2 minutes)

```powershell
.\.venv\Scripts\python.exe -m wellwatch.scoreboard --no-canary
```

This cleans the production data once (saved to `cache/`, which makes the app start faster) and writes
the replay scoreboard to `outputs/`. At the end it should print **603 crew-needing failures**, with
WellWatch catching C$1.44M vs C$1.28M for the simple rule. Lines like
`start_date -> datetime (0 values could not be parsed)` are normal.

## 7. Start the app

```powershell
.\.venv\Scripts\python.exe -m wellwatch.app
```

The window opens in a few seconds. The bottom bar should say **"Canary: offline"**, which is expected.

**Closing the window keeps WellWatch running in the system tray** (the round **W** icon near the
clock). To quit, right-click it → **Quit**. You can switch this off under Settings & health.

---

## A quick tour

Switch screens on the left, or with **Ctrl+1** to **Ctrl+5**. **F5** refreshes.

1. **Fleet overview.** Use the **Day** picker (top right) to go back to any day from 1 July to
   26 October 2026.
   - The map shows every scored well: colour = alert level, size = expected loss. Click a well for its
     risk, expected loss and the main reason. Scroll to zoom and drag to move.
   - The map background needs internet. Without it you get a plain latitude/longitude plot.
2. **Alert inbox.** About 200 alerts from the replay.
   - Tick or untick "Open only", filter by field or level, and select an alert to see its audit trail.
   - Acknowledge / Shelve / Close work and are saved on your computer only.
3. **Crew planner.** Pick a Monday as the planning day and press **Solve plan** (about 10–20 seconds).
   - You get the expected loss without and with the plan, the saving, and a crew × day schedule.
   - **What if…** has "value of one more crew" and the effectiveness sensitivity.
   - **Export work orders** saves printable HTML/CSV files.
   - Advanced settings hold visit type, inspection cost, solver and crews per field.
4. **Replay & engine.** Shows the scoreboard (WellWatch vs the simple rule) and the warning lead-time
   chart.
   - **Re-run scoreboard** works offline.
   - **Don't press Start replay or Start live engine.** They need Canary.
5. **Settings & health.**
   - The **model card** shows how the model was trained and tested.
   - **Check data quality** lists sensors that stopped changing.

The first time you open a screen it may take a few seconds while it loads the data. After that it's
quick.

---

## Optional extras

**Weekly crew plans and the write-up charts.** These regenerate the files behind `docs/RESULTS.md`:

```powershell
.\.venv\Scripts\python.exe -m wellwatch.plan --weekly 2026-07-06 2026-10-26 --no-canary   # about 5-10 min
.\.venv\Scripts\python.exe -m wellwatch.analysis                                          # about 1 min
```

**Run the tests:** `.\.venv\Scripts\python.exe -m pytest tests -q`

---

## Troubleshooting

| What you see | Fix |
|---|---|
| `python` is not recognised | Reinstall Python with "Add python.exe to PATH" ticked, or use `py -3.12` |
| `Expected daily_production.parquet at ...` | The three `.parquet` files are not in the repository folder (next to `README.md`) |
| Overview says "No scored days yet" | `alerts.db` is missing or not inside the `cache` folder |
| The map is a plain dotted plot | No internet, or a firewall blocks `server.arcgisonline.com` / `cdnjs.cloudflare.com`. Everything else still works |
| "Canary: offline" in the bottom bar | Expected: there is no Canary on your computer |
| Planner says "highs (Gurobi unavailable…)" | Expected without a full Gurobi licence; the plan is the same |
| `pip install` fails on one package | Make sure it's Python **3.12** (`python --version`), then re-run the install. If only `gurobipy` fails, delete that line from `requirements.txt` and re-run |
| The window is gone but the app still seems to be running | It's in the tray: right-click the **W** icon near the clock → Quit |
| First start is slow (up to a minute) | The data is being cleaned and cached once; step 6 does this ahead of time |
