"""One native sidecar owns a private control configuration at a time."""
import os
from pathlib import Path


class WorkerLock:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.stream = open(path, 'a+b')
        self.stream.seek(0, os.SEEK_END)
        if not self.stream.tell():
            self.stream.write(b'0'); self.stream.flush()
        self.held = False

    def acquire(self):
        self.stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            return False
        self.held = True
        return True

    def release(self):
        if not self.held:
            return
        self.stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        self.held = False

    def close(self):
        self.release()
        self.stream.close()
