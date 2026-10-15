"""Single-instance lock for the agent (doc 06 "Runtime"; same mechanism as the bridge's InstanceLock in ingest/bridge/bridge.py).

An exclusive, non-blocking lock on one byte of logs/agent.lock, held for the life of the process. The operating system drops it when
the holder dies, so a crash never leaves it stale, and it does not depend on how the process was started.
"""
import os
from pathlib import Path


class AlreadyRunning(Exception):
    """Another process holds the single-instance lock."""


class InstanceLock:
    def __init__(self, path):
        self.path, self._fd = Path(path), None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT)
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise AlreadyRunning(f"another agent holds {self.path}") from None
        self._fd = fd
        return self

    def release(self):
        if self._fd is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass                   # closing the descriptor below releases the lock regardless
        finally:
            os.close(self._fd)
            self._fd = None
