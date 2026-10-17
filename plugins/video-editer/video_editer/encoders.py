"""Portable H.264 encoder selection, verified by a tiny synthetic encode."""

import hashlib
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import time

from . import resources

GPU_ENCODERS = ("h264_nvenc", "h264_qsv", "h264_amf", "h264_videotoolbox")


def diagnostic(detail):
    detail = str(detail)
    relevant = [
        line
        for line in detail.splitlines()
        if re.search(
            r"error|unsupported|not supported|cannot|could not|failed|unable|no .*device",
            line,
            re.I,
        )
    ]
    return "\n".join(relevant)[:1600] if relevant else detail[-800:]


def options(encoder, quality=23):
    if encoder == "h264_nvenc":
        return ["-preset", "p4", "-cq", str(quality), "-b:v", "0"]
    if encoder == "h264_qsv":
        return [
            "-preset",
            "veryfast",
            "-global_quality",
            str(quality),
            "-async_depth",
            "1",
        ]
    if encoder == "h264_amf":
        return [
            "-quality",
            "speed",
            "-rc",
            "cqp",
            "-qp_i",
            str(quality),
            "-qp_p",
            str(quality),
        ]
    if encoder == "h264_videotoolbox":
        return ["-allow_sw", "0", "-realtime", "1", "-b:v", "6M"]
    return ["-preset", "veryfast", "-crf", str(quality)]


def cache_key(binary, value):
    executable = Path(shutil.which(str(binary)) or binary).resolve()
    stat = executable.stat()
    return hashlib.sha256(
        json.dumps(
            [
                str(executable),
                stat.st_size,
                stat.st_mtime_ns,
                platform.system(),
                value["encoder"],
                value["allow_cpu_fallback"],
            ],
            sort_keys=True,
        ).encode()
    ).hexdigest()


def cached():
    path = resources.root() / "encoders.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text("utf-8"))
    except (ValueError, OSError):
        return None


def select(binary, refresh=False):
    from . import engine, processes

    value = resources.current_policy()
    key = cache_key(binary, value)
    old = cached()
    if (
        not refresh
        and old
        and old.get("cache_key") == key
        and time.time() - old.get("checked_at", 0) < 600
    ):
        if old.get("error"):
            raise ValueError(old["error"])
        return old
    result = {
        "cache_key": key,
        "checked_at": time.time(),
        "requested": value["encoder"],
        "encoder": None,
        "gpu": False,
        "probes": [],
    }
    if value["encoder"] == "cpu":
        result["encoder"] = "libx264"
    else:
        listing = subprocess.run(
            [str(binary), "-hide_banner", "-encoders"],
            capture_output=True,
            timeout=15,
            **resources.child_options(),
        )
        available = {
            row.split()[1]
            for row in listing.stdout.decode("utf-8", "replace").splitlines()
            if len(row.split()) > 1
        }
        candidates = (
            GPU_ENCODERS
            if value["encoder"] in ("auto", "gpu_required")
            else (value["encoder"],)
        )
        for encoder in candidates:
            processes.check()
            if encoder not in available:
                result["probes"].append(
                    {
                        "encoder": encoder,
                        "available": False,
                        "reason": "not compiled into this FFmpeg",
                    }
                )
                continue
            command = [
                str(binary),
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=black:s=128x96:r=25",
                "-frames:v",
                "4",
                "-an",
                "-c:v",
                encoder,
                *options(encoder),
                "-pix_fmt",
                "nv12",
                "-f",
                "null",
                "-",
            ]
            try:
                probe = subprocess.run(
                    resources.limit_ffmpeg(command),
                    capture_output=True,
                    timeout=12,
                    **resources.child_options(),
                )
                passed = probe.returncode == 0
                detail = diagnostic(probe.stderr.decode("utf-8", "replace"))
            except subprocess.TimeoutExpired:
                passed, detail = False, "synthetic encoder probe timed out"
            result["probes"].append(
                {
                    "encoder": encoder,
                    "available": passed,
                    "reason": None if passed else detail,
                }
            )
            if passed:
                result.update(encoder=encoder, gpu=True)
                break
        if result["encoder"] is None:
            if value["encoder"] == "gpu_required" or not value["allow_cpu_fallback"]:
                result["error"] = (
                    "No requested GPU encoder passed the runtime probe; CPU fallback is disabled"
                )
            else:
                result.update(
                    encoder="libx264",
                    fallback_reason="No requested GPU encoder passed the runtime probe",
                )
    resources.root().mkdir(parents=True, exist_ok=True)
    engine.write_json(resources.root() / "encoders.json", result)
    if result.get("error"):
        raise ValueError(result["error"])
    return result


def prepare(args):
    args = list(map(str, args))
    if not args or "ffmpeg" not in Path(args[0]).name.lower():
        return args, None
    index = next(
        (
            i
            for i, x in enumerate(args[:-1])
            if x in ("-c:v", "-vcodec", "-codec:v") and args[i + 1] == "libx264"
        ),
        None,
    )
    if index is None:
        return args, None
    selected = select(args[0])
    encoder = selected["encoder"]
    if encoder == "libx264":
        if "-preset" not in args:
            args[-1:-1] = ["-preset", "veryfast"]
        return args, selected
    result = []
    quality = 23
    i = 0
    while i < len(args):
        if args[i] in ("-crf", "-preset", "-pix_fmt"):
            if args[i] == "-crf":
                quality = int(args[i + 1])
            i += 2
            continue
        if i == index:
            result.extend([args[i], encoder])
            i += 2
        else:
            result.append(args[i])
            i += 1
    result[-1:-1] = [*options(encoder, quality), "-pix_fmt", "nv12"]
    return result, selected


def can_fallback(selected, stderr):
    value = resources.current_policy()
    if (
        not selected
        or not selected["gpu"]
        or value["encoder"] == "gpu_required"
        or not value["allow_cpu_fallback"]
    ):
        return False
    log = (
        stderr.decode("utf-8", "replace")
        if isinstance(stderr, bytes)
        else str(stderr or "")
    )
    return bool(
        re.search(
            r"Error (?:while )?opening (?:an )?encoder|Error initializing.*encoder|"
            r"(?:device|MFX|NVENC|CUDA|AMF|VideoToolbox).*(?:fail|error|unavailable|not found)|"
            r"No (?:capable|capable devices|device)|Cannot load.*(?:cuda|nvidia)",
            log,
            re.I,
        )
    )


def fallback(selected, detail):
    from . import engine

    result = {
        **selected,
        "encoder": "libx264",
        "gpu": False,
        "fallback_reason": "Hardware encoding failed during this task: "
        + diagnostic(detail),
    }
    engine.write_json(resources.root() / "encoders.json", result)
    resources.record(
        {
            "event": "encoder_fallback",
            "from_encoder": selected["encoder"],
            "to_encoder": "libx264",
            "reason": result["fallback_reason"],
        }
    )
