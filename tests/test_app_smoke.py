"""UI smoke test (offscreen): build the window, visit every screen, load data, open a well, solve a plan,
and save a screenshot of each screen to cache/screenshots/. Needs alerts.db from an engine replay.

Run:  python -m pytest tests/test_app_smoke.py -s     (sets QT_QPA_PLATFORM=offscreen itself)
"""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":  # offscreen Qt has no font database of its own on Windows
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

import pytest  # noqa: E402

from wellwatch import config  # noqa: E402

SHOTS = config.REPO_ROOT / "cache" / "screenshots"


def wait_idle(app, timeout=180.0, settle=0.5):
    from wellwatch.app.workers import tracker
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        if tracker.count == 0:
            t1 = time.time()
            while time.time() - t1 < settle:
                app.processEvents()
                time.sleep(0.02)
            if tracker.count == 0:
                return time.time() - t0
        time.sleep(0.02)
    raise TimeoutError(f"background tasks still running after {timeout}s")


@pytest.fixture(scope="module")
def window():
    from PySide6.QtWidgets import QApplication
    from wellwatch.app.main_window import MainWindow
    from wellwatch.app.services import Services

    app = QApplication.instance() or QApplication([])
    svc = Services()
    if svc.last_scored_day() is None:
        pytest.skip("no scored days in cache/alerts.db (run an engine replay first)")
    win = MainWindow(svc, start_timers=False)
    win.resize(1500, 950)
    win.show()
    wait_idle(app)
    yield app, win
    win.quit()


def shot(win, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    path = SHOTS / f"{name}.png"
    assert win.grab().save(str(path))
    return path


def test_every_screen_loads_and_screenshots(window):
    app, win = window
    timings = {}
    for i, name in enumerate(["1_overview", "2_inbox", "3_planner", "4_engine", "5_settings"]):
        win.nav.setCurrentRow(i)
        timings[name] = wait_idle(app)
        if name == "1_overview":
            assert len(win.overview.map._rows) > 0, "map has no wells"
        if name == "3_planner":
            win.planner.solver.setCurrentText("highs")
            win.planner.budget.setValue(15_000)
            win.planner.solve()
            timings["3_planner_solve"] = wait_idle(app, timeout=300)
            assert win.planner.res is not None and "loss_with_plan" in win.planner.res
            assert win.planner.t_before.value.text() != "-"
        if name == "5_settings":
            win.settings_screen.quality()
            timings["5_settings_quality"] = wait_idle(app, timeout=300)
        assert shot(win, name).exists()
    print("\nscreen load timings (s):", {k: round(v, 1) for k, v in timings.items()})
    assert len(win.overview.top.model_.df) > 0
    assert win.inbox.table.model_.rowCount() >= 0
