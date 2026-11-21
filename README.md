# Chirality-Canary-Datathon

## How to run

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
| `ml_prep.ipynb`, `ml_beginner.ipynb` | Time-series ML tutorials (reference only) |
