"""Background tasks: run any function on the thread pool and get the result back on the UI thread.

    run_task(self, fn, arg1, on_done=callback, on_error=callback)

The number of running tasks is tracked so the status bar can show a busy indicator.
"""

from __future__ import annotations

import traceback

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class _Signals(QObject):
    done = Signal(object)
    error = Signal(str)
    finished = Signal()


class TaskTracker(QObject):
    changed = Signal(int)

    def __init__(self):
        super().__init__()
        self.count = 0

    def inc(self, n: int):
        self.count = max(0, self.count + n)
        self.changed.emit(self.count)


tracker = TaskTracker()
_alive: set = set()  # keep signal objects alive until delivered


class Task(QRunnable):
    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = _Signals()

    def run(self):
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:  # report, never crash the app
            detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            self.signals.error.emit(detail[:600])
        else:
            self.signals.done.emit(result)
        finally:
            self.signals.finished.emit()


def run_task(fn, *args, on_done=None, on_error=None, **kwargs) -> Task:
    task = Task(fn, *args, **kwargs)
    sig = task.signals
    _alive.add(sig)
    if on_done:
        sig.done.connect(on_done)
    if on_error:
        sig.error.connect(on_error)
    sig.finished.connect(lambda: (tracker.inc(-1), _alive.discard(sig)))
    tracker.inc(1)
    QThreadPool.globalInstance().start(task)
    return task
