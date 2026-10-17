"""Cancellable local child processes. Never terminate an unrelated PID."""

from contextlib import contextmanager
import contextvars
import subprocess
import time
from pathlib import Path
import uuid
import psutil
from . import resources, encoders

_cancel = contextvars.ContextVar("render_cancel", default=None)


class RenderCancelled(RuntimeError):
    pass


def check():
    path = _cancel.get()
    if path is not None and path.exists():
        raise RenderCancelled(
            "Render cancelled by caller; existing artifacts are retained"
        )
    resources.check_memory()


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


def popen(args, **kwargs):
    """For streaming FFV1 frame writers; caller owns cancellation and cleanup."""
    child = subprocess.Popen(
        resources.limit_ffmpeg(args), **resources.child_options(kwargs)
    )
    try:
        resources.low_priority(
            psutil.Process(child.pid), resources.current_policy()["cpu_threads"]
        )
    except psutil.Error:
        pass
    return child


def _run_once(args, *, capture_output=False, timeout=None, **kwargs):
    globals()["check"]()
    if capture_output:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    payload = kwargs.pop("input", None)
    if payload is not None:
        kwargs["stdin"] = subprocess.PIPE
    child = popen(args, **kwargs)
    started = time.monotonic()
    first = True
    try:
        while True:
            resources.check_memory(child)
            globals()["check"]()
            if timeout is not None and time.monotonic() - started > timeout:
                raise subprocess.TimeoutExpired(args, timeout)
            try:
                out, err = child.communicate(
                    input=payload if first else None, timeout=0.2
                )
                break
            except subprocess.TimeoutExpired:
                first = False
        return subprocess.CompletedProcess(args, child.returncode, out, err)
    except BaseException:
        stop(child)
        raise


def run(args, *, capture_output=False, check=False, timeout=None, **kwargs):
    with resources.heavy_slot():
        original = list(map(str, args))
        command, selected = encoders.prepare(original)
        output = (
            Path(original[-1])
            if original and Path(original[-1]).suffix.lower() == ".mp4"
            else None
        )
        existed = output.exists() if output else False
        result = _run_once(
            command, capture_output=capture_output, timeout=timeout, **kwargs
        )
        if (
            result.returncode
            and not existed
            and output
            and encoders.can_fallback(selected, result.stderr)
        ):
            # Keep the failed artifact and never overwrite a pre-existing user file.
            if output.is_file():
                output.rename(
                    output.with_name(
                        output.name + ".gpu-failed-" + uuid.uuid4().hex[:8]
                    )
                )
            detail = (
                result.stderr.decode("utf-8", "replace")
                if isinstance(result.stderr, bytes)
                else result.stderr
            )
            encoders.fallback(selected, detail)
            command, selected = encoders.prepare(original)
            result = _run_once(
                command, capture_output=capture_output, timeout=timeout, **kwargs
            )
        if selected:
            resources.record(
                {
                    "event": "encoding",
                    "encoder": selected["encoder"],
                    "gpu": selected["gpu"],
                    "success": result.returncode == 0,
                    "fallback_reason": selected.get("fallback_reason"),
                }
            )
        if check and result.returncode:
            raise subprocess.CalledProcessError(
                result.returncode, command, result.stdout, result.stderr
            )
        return result
