"""Cancellable local child processes. Never terminate an unrelated PID."""
from contextlib import contextmanager
import contextvars
import subprocess
import time

_cancel = contextvars.ContextVar('render_cancel', default=None)


class RenderCancelled(RuntimeError):
    pass


def check():
    path = _cancel.get()
    if path is not None and path.exists():
        raise RenderCancelled('Render cancelled by caller; existing artifacts are retained')


@contextmanager
def cancellation(path):
    token = _cancel.set(path)
    try:
        check()
        yield
    finally:
        _cancel.reset(token)


def stop(child):
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=3)


def run(args, *, capture_output=False, check=False, timeout=None, **kwargs):
    # Keep standard subprocess semantics when no render cancellation is active.
    if _cancel.get() is None:
        return subprocess.run(args, capture_output=capture_output, check=check, timeout=timeout, **kwargs)
    globals()['check']()
    if capture_output:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    payload = kwargs.pop('input', None)
    if payload is not None:
        kwargs['stdin'] = subprocess.PIPE
    kwargs.setdefault('creationflags', subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0)
    child = subprocess.Popen(args, **kwargs)
    started = time.monotonic()
    first = True
    try:
        while True:
            globals()['check']()
            if timeout is not None and time.monotonic()-started > timeout:
                raise subprocess.TimeoutExpired(args, timeout)
            try:
                out, err = child.communicate(input=payload if first else None, timeout=.2)
                break
            except subprocess.TimeoutExpired:
                first = False
        if check and child.returncode:
            raise subprocess.CalledProcessError(child.returncode, args, out, err)
        return subprocess.CompletedProcess(args, child.returncode, out, err)
    except BaseException:
        stop(child)
        raise
