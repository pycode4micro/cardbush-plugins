"""Nonblocking cross-process file locks; lock files are retained, never cleaned."""
from contextlib import contextmanager
from functools import wraps
import os
from pathlib import Path
import threading

_guard = threading.Lock()
_locks = {}
_local = threading.local()


@contextmanager
def file_lock(path):
    path = Path(path).resolve()
    key = str(path)
    held = getattr(_local, 'held', set())
    if key in held:
        yield
        return
    with _guard:
        lock = _locks.setdefault(key, threading.Lock())
    if not lock.acquire(blocking=False):
        raise ValueError('Resource busy; retry after the current operation completes')
    stream = None
    acquired = False
    try:
        stream = path.open('a+b')
        if path.stat().st_size == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError('Resource busy in another process; retry later') from exc
        acquired = True
        _local.held = held | {key}
        yield
    finally:
        _local.held = held
        if stream is not None:
            if acquired:
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            stream.close()
        lock.release()


def project_write(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        from . import engine
        project_id = args[0] if args else kwargs['project_id']
        with file_lock(engine.project_dir(project_id) / 'writer.lock'):
            return function(*args, **kwargs)
    return wrapped
