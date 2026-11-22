# PyInstaller spec for the WellWatch desktop app.  Build with:  powershell -File scripts/build_app.ps1
#
# Bundled: the wellwatch package, datasets/data_utils.py, the trained model (models/risk_7d*.json) and the
# oil-price table (data/external/oil_prices_quarterly.csv).
# NOT bundled: competition parquet files, cache/, logs/, outputs/, Gurobi licence files, the Canary token
# (it stays in Windows Credential Manager). The exe finds its data folder by walking up from its own
# location (so dist/WellWatch/WellWatch.exe inside the repo uses the repo), or from WELLWATCH_HOME.
import os
from pathlib import PurePath
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = os.path.abspath(SPECPATH)
ICON = os.path.join(ROOT, "build", "wellwatch.ico")

datas = [
    (os.path.join(ROOT, "models", f), "models")
    for f in ("risk_7d.json", "risk_7d_calibration.json", "risk_7d_meta.json")
] + [(os.path.join(ROOT, "data", "external", "oil_prices_quarterly.csv"), os.path.join("data", "external"))]
datas += collect_data_files("xgboost") + collect_data_files("pyqtgraph")
binaries = collect_dynamic_libs("xgboost") + collect_dynamic_libs("gurobipy")
hiddenimports = (collect_submodules("wellwatch") + collect_submodules("keyring.backends")
                 + ["data_utils", "gurobipy", "scipy.optimize", "scipy.sparse", "win32ctypes.core"])

a = Analysis(
    [os.path.join(ROOT, "scripts", "wellwatch_app.py")],
    pathex=[ROOT, os.path.join(ROOT, "datasets")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    # Optional imports of pandas / xgboost / shap would otherwise pull in torch (3.5 GB), dask, spark, PIL...
    excludes=["torch", "torchvision", "torchaudio", "tensorflow", "jax", "dask", "distributed", "pyspark", "cupy",
              "PIL", "sqlalchemy", "numexpr", "bottleneck", "fsspec", "pydantic", "pydantic_core",
              "tkinter", "matplotlib", "shap", "numba", "llvmlite", "IPython", "ipykernel", "jupyter_client",
              "nbconvert", "notebook", "pytest", "sklearn", "pypdf", "openpyxl", "PySide6.Qt3DCore",
              "PySide6.QtQuick3D", "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization"],
    noarchive=False,
)
# drop library test data (pyarrow ships sample .parquet files)
a.datas = [d for d in a.datas if "tests" not in PurePath(d[0]).parts]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="WellWatch",
    console=False,          # windowed app; the engine it starts writes to logs/
    icon=ICON if os.path.exists(ICON) else None,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="WellWatch", upx=False)
