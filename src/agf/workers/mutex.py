"""Single-instance PID-file mutex — one worker process per name (no Redis day-1)."""
import os
from pathlib import Path


class MutexHeld(Exception):
    """Raised when another live process already holds the mutex."""


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)            # signal 0 = existence check
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                # exists but not ours
    return True


class PidFileMutex:
    """Context manager. Writes our PID to a file; a second acquire raises while the
    holder is alive, and a stale file (dead PID) is reclaimed."""

    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)

    def acquire(self) -> "PidFileMutex":
        if self.path.exists():
            try:
                existing = int(self.path.read_text().strip() or "0")
            except ValueError:
                existing = 0
            if existing and _pid_alive(existing):
                raise MutexHeld(f"mutex held by live pid {existing} at {self.path}")
            # else: stale (dead pid / empty) -> reclaim
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(str(os.getpid()))
        return self

    def release(self) -> None:
        try:
            if self.path.exists() and self.path.read_text().strip() == str(os.getpid()):
                self.path.unlink()
        except FileNotFoundError:
            pass

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
        return False
