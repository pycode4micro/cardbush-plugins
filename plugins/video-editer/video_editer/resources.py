"""Host-wide admission and conservative resource defaults, independent of projects."""

from contextlib import contextmanager
import contextvars
from functools import wraps
import json
import os
from pathlib import Path
import time

import psutil

from .locking import file_lock

_active = contextvars.ContextVar("video_heavy_slot", default=None)
_worker_pid = None
_worker_affinity = None
DEFAULTS = {
    "heavy_concurrency": 1,
    "cpu_threads": 0,  # Auto: at most one quarter of logical CPUs, capped at four.
    "min_free_memory_mb": 1024,
    "reserve_memory_percent": 20,
    "max_worker_memory_mb": 0,  # Auto: at most 25% RAM, capped at 4 GiB.
    "max_system_cpu_percent": 85,
    "encoder": "auto",
    "allow_cpu_fallback": True,
}
ENCODERS = (
    "auto",
    "cpu",
    "gpu_required",
    "h264_nvenc",
    "h264_qsv",
    "h264_amf",
    "h264_videotoolbox",
)


class ResourceLimitError(RuntimeError):
    """Only our current job is stopped; its saved checkpoints remain retryable."""


def root():
    return (
        Path(
            os.environ.get(
                "VIDEO_EDITER_RESOURCE_DIR", str(Path.home() / ".video_editer_runtime")
            )
        )
        .expanduser()
        .resolve()
    )


def validate(value):
    if not isinstance(value, dict) or set(value) - DEFAULTS.keys():
        raise ValueError("Unknown resource setting")
    result = {**DEFAULTS, **value}
    bounds = {
        "heavy_concurrency": (1, 1),
        "cpu_threads": (0, 64),
        "min_free_memory_mb": (128, 1048576),
        "reserve_memory_percent": (5, 80),
        "max_worker_memory_mb": (0, 1048576),
        "max_system_cpu_percent": (10, 100),
    }
    for name, (low, high) in bounds.items():
        x = result[name]
        if type(x) is not int or not low <= x <= high:
            raise ValueError(f"{name} must be an integer in {low}..{high}")
    if result["max_worker_memory_mb"] and result["max_worker_memory_mb"] < 128:
        raise ValueError("max_worker_memory_mb must be zero (auto) or at least 128")
    if (
        result["encoder"] not in ENCODERS
        or type(result["allow_cpu_fallback"]) is not bool
    ):
        raise ValueError("Invalid encoder or allow_cpu_fallback")
    return result


def policy():
    path = root() / "policy.json"
    value = json.loads(path.read_text("utf-8")) if path.is_file() else {}
    return validate(value)


def configure(changes):
    from . import engine

    directory = root()
    directory.mkdir(parents=True, exist_ok=True)
    with file_lock(directory / "policy.lock"):
        value = validate({**policy(), **changes})
        engine.write_json(directory / "policy.json", value)
    return value


def effective(value=None):
    value = dict(value or policy())
    memory = psutil.virtual_memory()
    cpus = os.cpu_count() or 1
    value["cpu_threads"] = min(cpus, value["cpu_threads"] or max(1, min(4, cpus // 4)))
    value["max_worker_memory_mb"] = value["max_worker_memory_mb"] or max(
        128, min(4096, int(memory.total / 1048576 * 0.25))
    )
    value["reserve_memory_mb"] = max(
        value["min_free_memory_mb"],
        int(memory.total / 1048576 * value["reserve_memory_percent"] / 100),
    )
    return value


def low_priority(process, threads=None, affinity=None):
    result = {}
    try:
        process.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 10)
        result["priority"] = "below_normal" if os.name == "nt" else "nice_10"
    except (psutil.Error, AttributeError, OSError, NotImplementedError) as exc:
        result["priority_warning"] = str(exc)
    try:
        if os.name == "nt":
            process.ionice(psutil.IOPRIO_LOW)
        elif hasattr(psutil, "IOPRIO_CLASS_IDLE"):
            process.ionice(psutil.IOPRIO_CLASS_IDLE)
        result["io_priority"] = "low"
    except (psutil.Error, AttributeError, OSError, NotImplementedError):
        result["io_priority"] = "unsupported"
    if threads:
        try:
            allowed = affinity if affinity is not None else process.cpu_affinity()
            process.cpu_affinity(allowed[:threads])
            result["cpu_affinity_count"] = min(len(allowed), threads)
        except (psutil.Error, AttributeError, OSError, NotImplementedError):
            result["cpu_affinity_count"] = None
    return result


def worker_setup():
    global _worker_pid, _worker_affinity
    _worker_pid = os.getpid()
    process = psutil.Process()
    try:
        _worker_affinity = process.cpu_affinity()
    except (psutil.Error, AttributeError, OSError, NotImplementedError):
        _worker_affinity = None
    return low_priority(process)


def child_options(options=None):
    value = current_policy()
    options = dict(options or {})
    env = dict(options.get("env") or os.environ)
    for key in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        env[key] = str(value["cpu_threads"])
    options["env"] = env
    if os.name == "nt":
        import subprocess

        options["creationflags"] = (
            options.get("creationflags", 0)
            | subprocess.CREATE_NO_WINDOW
            | subprocess.BELOW_NORMAL_PRIORITY_CLASS
        )
    return options


def current_policy():
    active = _active.get()
    return active["policy"] if active else effective()


def in_slot():
    return _active.get() is not None


def admission(value):
    memory = psutil.virtual_memory()
    available = memory.available / 1048576
    if available < value["reserve_memory_mb"]:
        return {
            "reason": "low_memory",
            "available_mb": round(available),
            "required_free_mb": value["reserve_memory_mb"],
        }
    cpu = psutil.cpu_percent(interval=0.1)
    if cpu > value["max_system_cpu_percent"]:
        return {
            "reason": "system_busy",
            "system_cpu_percent": cpu,
            "threshold": value["max_system_cpu_percent"],
        }
    return None


@contextmanager
def heavy_slot(wait_callback=None, event_callback=None):
    if in_slot():
        yield _active.get()
        return
    from . import processes

    directory = root()
    directory.mkdir(parents=True, exist_ok=True)
    lock = None
    while lock is None:
        processes.check()
        candidate = file_lock(directory / "heavy.lock")
        try:
            candidate.__enter__()
        except ValueError:
            if wait_callback:
                wait_callback({"reason": "heavy_slot_busy"})
            time.sleep(0.25)
            continue
        try:
            value = effective()
            blocked = admission(value)
        except BaseException:
            candidate.__exit__(None, None, None)
            raise
        if blocked:
            candidate.__exit__(None, None, None)
            if wait_callback:
                wait_callback(blocked)
            time.sleep(2)
        else:
            lock = candidate
    context = {
        "policy": value,
        "event_callback": event_callback,
        "last_memory_check": 0,
    }
    token = _active.set(context)
    try:
        if _worker_pid == os.getpid():
            low_priority(psutil.Process(), value["cpu_threads"], _worker_affinity)
        if wait_callback:
            wait_callback(None)
        yield context
    finally:
        _active.reset(token)
        lock.__exit__(None, None, None)


def heavy_operation(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with heavy_slot():
            return function(*args, **kwargs)

    return wrapped


def record(event):
    active = _active.get()
    if active and active["event_callback"]:
        active["event_callback"](event)


def check_memory(child=None):
    active = _active.get()
    if not active or time.monotonic() - active["last_memory_check"] < 0.5:
        return
    active["last_memory_check"] = time.monotonic()
    value = active["policy"]
    try:
        parent = (
            psutil.Process()
            if _worker_pid == os.getpid()
            else (psutil.Process(child.pid) if child else None)
        )
        if parent:
            rss = (
                sum(
                    p.memory_info().rss
                    for p in [parent, *parent.children(recursive=True)]
                    if p.is_running()
                )
                / 1048576
            )
            if rss > value["max_worker_memory_mb"]:
                raise ResourceLimitError(
                    f"Worker memory {rss:.0f} MiB exceeds {value['max_worker_memory_mb']} MiB; retry through the queue after adjusting resources"
                )
        if (
            psutil.virtual_memory().available / 1048576
            < value["min_free_memory_mb"] / 2
        ):
            raise ResourceLimitError(
                "Critically low system memory; stopped this job, checkpoints retained; retry through the queue when memory is available"
            )
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass


def limit_ffmpeg(args):
    args = list(map(str, args))
    if not args or "ffmpeg" not in Path(args[0]).name.lower():
        return args
    n = str(current_policy()["cpu_threads"])
    # Our commands use one output. Bound each input decoder and the output encoder.
    result = [args[0], "-filter_threads", "1", "-filter_complex_threads", "1"]
    index = 1
    while index < len(args):
        token = args[index]
        if token in (
            "-threads",
            "-threads:v",
            "-filter_threads",
            "-filter_complex_threads",
        ):
            index += 2
            continue
        if token == "-i":
            result.extend(["-threads", n])
        result.append(token)
        index += 1
    if len(args) > 1 and args[-1] not in ("-encoders", "-version") and args[-2] != "-i":
        result[-1:-1] = ["-threads", n]
    return result


def status():
    value = effective()
    memory = psutil.virtual_memory()
    return {
        "policy": policy(),
        "effective": value,
        "resource_directory": str(root()),
        "scope": "one heavy operation per resource directory across plugin data directories; default is per OS user",
        "available_memory_mb": round(memory.available / 1048576),
        "total_memory_mb": round(memory.total / 1048576),
        "limitations": [
            "GPU encoding does not move every filter or Python graphic operation to the GPU",
            "Thread/priority limits reduce contention; memory is sampled, not a hard OS allocation sandbox",
        ],
    }
