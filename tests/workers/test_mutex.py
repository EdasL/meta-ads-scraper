import os
import pytest
from agf.workers.mutex import PidFileMutex, MutexHeld


def test_second_acquire_raises_while_held(tmp_path):
    m1 = PidFileMutex(tmp_path / "w.pid")
    m1.acquire()
    try:
        with pytest.raises(MutexHeld):
            PidFileMutex(tmp_path / "w.pid").acquire()
    finally:
        m1.release()


def test_stale_pid_is_reclaimed(tmp_path):
    p = tmp_path / "w.pid"
    p.write_text("999999")                      # almost certainly a dead pid
    m = PidFileMutex(p)
    m.acquire()
    assert p.read_text() == str(os.getpid())    # reclaimed
    m.release()


def test_release_removes_file(tmp_path):
    p = tmp_path / "w.pid"
    with PidFileMutex(p):
        assert p.exists()
    assert not p.exists()
