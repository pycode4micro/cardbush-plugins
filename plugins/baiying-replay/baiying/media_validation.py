"""Local media evidence for editing handoff. No network or model calls."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import time
from pathlib import Path


def ffmpeg_binary():
    binary = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg")
    if not binary:
        try:
            import imageio_ffmpeg
            binary = imageio_ffmpeg.get_ffmpeg_exe()
        except (ImportError, RuntimeError):
            raise ValueError("媒体检查需要 FFmpeg，请安装插件 requirements.txt 或设置 FFMPEG_BIN。") from None
    return str(binary)


def _run(command, timeout=60, checkpoint=None):
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    if not checkpoint:
        return subprocess.run(command, capture_output=True, timeout=timeout, creationflags=flags)
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
    deadline = time.monotonic() + timeout
    try:
        while True:
            checkpoint()
            try:
                stdout, stderr = process.communicate(timeout=1)
                return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                if time.monotonic() >= deadline:
                    raise
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _metadata(path, binary):
    probe = os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe")
    if probe:
        result = _run([probe, "-v", "error", "-protocol_whitelist", "file,pipe", "-show_streams",
                       "-show_format", "-of", "json", str(path)])
        if result.returncode:
            raise ValueError("无法读取视频信息。")
        raw = json.loads(result.stdout)
        streams = raw.get("streams", [])
        video = next((s for s in streams if s.get("codec_type") == "video"), {})
        rate = str(video.get("avg_frame_rate") or "0/1").split("/")
        fps = float(rate[0]) / (float(rate[1]) or 1) if len(rate) == 2 else float(rate[0])
        return {"duration_seconds": float(raw.get("format", {}).get("duration") or video.get("duration") or 0),
                "width": video.get("width"), "height": video.get("height"), "fps": fps,
                "video_codec": video.get("codec_name"),
                "has_audio": any(s.get("codec_type") == "audio" for s in streams)}
    result = _run([binary, "-nostdin", "-hide_banner", "-protocol_whitelist", "file,pipe", "-i", str(path)])
    info = result.stderr.decode("utf-8", errors="replace")
    duration = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", info)
    video_line = next((line for line in info.splitlines() if "Video:" in line), "")
    size = re.search(r"\b(\d{2,5})x(\d{2,5})\b", video_line)
    rate = re.search(r"(\d+(?:\.\d+)?) fps", video_line)
    codec = re.search(r"Video:\s*([^ ,]+)", video_line)
    return {"duration_seconds": (int(duration[1]) * 3600 + int(duration[2]) * 60 + float(duration[3])) if duration else 0,
            "width": int(size[1]) if size else 0, "height": int(size[2]) if size else 0,
            "fps": float(rate[1]) if rate else None, "video_codec": codec[1] if codec else None,
            "has_audio": "Audio:" in info}


def validate_video(path, checkpoint=None):
    """Decode all video/audio, hash bytes and sample visual similarity evidence."""
    path = Path(path)
    before = path.stat()
    if not path.is_file() or not before.st_size:
        raise ValueError("视频文件为空或不存在。")
    binary = ffmpeg_binary()
    metadata = _metadata(path, binary)
    duration = metadata["duration_seconds"]
    if not math.isfinite(duration) or not 0 < duration <= 24 * 3600 or not metadata["width"] or not metadata["height"]:
        raise ValueError("视频没有有效时长或画面尺寸。")
    timeout = min(1800, max(60, int(duration * 2)))
    result = _run([binary, "-nostdin", "-v", "error", "-xerror", "-err_detect", "explode",
                   "-threads", "2", "-protocol_whitelist", "file,pipe", "-i", str(path),
                   "-map", "0:v:0", "-map", "0:a?", "-f", "null", "-"], timeout, checkpoint)
    if result.returncode:
        raise ValueError("视频未通过完整解码检查，不能计入可用素材。")
    # Sixteen samples span the whole file. Similarity is advisory: it must not
    # silently discard product shots with similar framing but different speech.
    result = _run([binary, "-nostdin", "-v", "error", "-threads", "2",
                   "-protocol_whitelist", "file,pipe", "-i", str(path), "-an",
                   "-vf", f"fps={16 / duration:.9f},scale=9:8,format=gray", "-frames:v", "16",
                   "-f", "rawvideo", "-"], timeout, checkpoint)
    signatures = []
    if result.returncode == 0:
        for offset in range(0, len(result.stdout) - 71, 72):
            pixels, bits = result.stdout[offset:offset + 72], 0
            for row in range(8):
                for column in range(8):
                    bits = (bits << 1) | int(pixels[row * 9 + column] > pixels[row * 9 + column + 1])
            signatures.append(f"{bits:016x}")
    digest = file_hash(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("检查期间视频文件发生变化，请重新验证。")
    return {**metadata, "duration_seconds": round(duration, 3), "bytes": after.st_size,
            "sha256": digest, "mtime_ns": after.st_mtime_ns, "full_decode_passed": True,
            "visual_signatures": signatures}


def similar_visuals(left, right):
    a, b = left.get("visual_signatures", []), right.get("visual_signatures", [])
    da, db = left.get("duration_seconds", 0), right.get("duration_seconds", 0)
    if min(len(a), len(b)) < 8 or not da or abs(da - db) / da > .08:
        return False
    distances = [(int(x, 16) ^ int(y, 16)).bit_count() for x, y in zip(a, b)]
    return sum(distances) / len(distances) <= 4
