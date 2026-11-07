"""One menu-bar collector per Mac installation, including login starts."""
from pathlib import Path
import os


def acquire(data_dir):
    import fcntl
    root = Path(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    handle = (root/'tray.lock').open('a')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    (root/'tray.pid').write_text(str(os.getpid()))
    return handle  # Caller retains the descriptor throughout the menu loop.
