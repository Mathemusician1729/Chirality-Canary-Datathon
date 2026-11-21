"""One-writer lock for the engine and the replay feed (cache/engine.lock).

Canary lets only one write session own a tag, so only one engine or replay may run at a time.
The lock file holds the pid, mode ("replay", "live", "replay-feed") and start time. A lock whose
process has exited is stale and is cleared automatically. The desktop app reads it to show
"Engine running" and to disable its Start buttons.

    from wellwatch.lock import engine_lock
    with engine_lock("live"):
        ...  # raises EngineBusy if another engine holds the lock
"""

from __future__ import annotations

import contextlib
import ctypes
import datetime as dt
import json
import os

from . import config

LOCK_FILE = config.REPO_ROOT / "cache" / "engine.lock"


class EngineBusy(RuntimeError):
    pass


def pid_alive(pid: int) -> bool:
    """True if a process with this pid is running (Windows API; never signals the process)."""
    if not pid or pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    k32 = ctypes.windll.kernel32
    handle = k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(handle, ctypes.byref(code))
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    finally:
        k32.CloseHandle(handle)


def read_lock() -> dict | None:
    """Current lock holder, or None. Clears a stale lock left by a crashed process."""
    try:
        info = json.loads(LOCK_FILE.read_text())
    except (OSError, ValueError):
        return None
    if not pid_alive(int(info.get("pid", 0))):
        LOCK_FILE.unlink(missing_ok=True)
        return None
    return info


def acquire(mode: str) -> dict:
    holder = read_lock()
    if holder and holder.get("pid") != os.getpid():
        raise EngineBusy(f"Another WellWatch engine is running ({holder.get('mode')}, pid {holder.get('pid')}, "
                         f"since {holder.get('started')}). Stop it first.")
    info = {"pid": os.getpid(), "mode": mode, "started": dt.datetime.now().isoformat(timespec="seconds")}
    LOCK_FILE.parent.mkdir(exist_ok=True)
    LOCK_FILE.write_text(json.dumps(info))
    return info


def release() -> None:
    holder = read_lock()
    if holder and holder.get("pid") == os.getpid():
        LOCK_FILE.unlink(missing_ok=True)


@contextlib.contextmanager
def engine_lock(mode: str):
    acquire(mode)
    try:
        yield
    finally:
        release()
