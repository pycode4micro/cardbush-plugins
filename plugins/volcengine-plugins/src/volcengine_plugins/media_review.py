"""Local erasure planning, evidence-based review and separate publishing copies."""
from __future__ import annotations

import hashlib
import importlib.metadata
import io
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw
from pydantic import Field

from .subtitle import EraseLocation, NativeModel, SubtitleEraseRequest


def ffmpeg_bin() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def run(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(args, capture_output=True, timeout=timeout,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("Local media process failed or timed out; check executable configuration and file access") from None
    if result.returncode:
        # Only local media is accepted. Keep logs bounded; never mix a partial file with a final artifact.
        raise ValueError("Local media process failed: " + result.stderr.decode("utf-8", "replace")[-1500:])
    return result


def local_file(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or not path.is_file() or not path.stat().st_size:
        raise ValueError("An existing nonempty absolute local file is required")
    return path.resolve()


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def media_info(value: str) -> dict:
    path = local_file(value)
    # The bundled decoder also works when no standalone ffprobe is on PATH.
    reader = imageio_ffmpeg.read_frames(str(path), input_params=["-protocol_whitelist", "file,pipe", "-threads", "1"])
    try:
        meta = next(reader)
        next(reader)  # Validate at least one decoded frame, not just a container header.
    except Exception:
        raise ValueError("Cannot decode a video frame from the local file") from None
    finally:
        reader.close()
    width, height = meta["size"]
    duration = float(meta.get("duration", 0))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Video duration is unavailable or invalid")
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": digest(path),
            "width": width, "height": height, "fps": meta.get("fps"), "duration_seconds": duration,
            "audio_codec": meta.get("audio_codec"), "video_codec": meta.get("codec"),
            "average_total_bitrate_mbps": round(path.stat().st_size * 8 / duration / 1e6, 3),
            "metadata_precision_seconds": 0.01, "validation": "metadata_and_first_decoded_frame"}


def preflight(file_paths: list[str]) -> dict:
    if not 1 <= len(file_paths) <= 12:
        raise ValueError("Provide 1..12 local video paths")
    files = [media_info(path) for path in file_paths]
    ranked = sorted(range(len(files)), key=lambda i: (files[i]["width"] * files[i]["height"],
                                                     files[i]["average_total_bitrate_mbps"]), reverse=True)
    return {"files": files, "candidate_order": ranked,
            "environment": {"python": sys.executable, "ffmpeg": ffmpeg_bin(),
                            "packages": {name: importlib.metadata.version(name) for name in
                                         ("numpy", "Pillow", "imageio-ffmpeg")}},
            "notes": ["Ranking is resolution then bitrate, not proof of identical content or original quality.",
                      "Metadata plus one decoded frame does not certify a complete decode.",
                      "Inspect subtitle positions and texture before specifying timed rectangles."],
            "paid_request_sent": False}


class PixelRegion(NativeModel):
    start_time: float = Field(ge=0)
    end_time: float = Field(gt=0)
    left: int = Field(ge=0)
    top: int = Field(ge=0)
    right: int = Field(gt=0)
    bottom: int = Field(gt=0)


def erasure_plan(source_path: str, regions: list[PixelRegion], padding_pixels: int = 4,
                 mode: str = "Subtitle") -> dict:
    if not 1 <= len(regions) <= 100 or not 0 <= padding_pixels <= 64:
        raise ValueError("Provide 1..100 observed regions and 0..64 pixels padding")
    info = media_info(source_path)
    width, height, duration = info["width"], info["height"], info["duration_seconds"]
    grouped = {}
    for region in regions:
        if not (region.left < region.right <= width and region.top < region.bottom <= height
                and region.start_time < region.end_time <= duration):
            raise ValueError("Observed rectangle or time range is outside the source")
        left, top = max(0, region.left-padding_pixels), max(0, region.top-padding_pixels)
        right, bottom = min(width, region.right+padding_pixels), min(height, region.bottom+padding_pixels)
        if mode == "Subtitle" and top < height / 2:
            raise ValueError("Subtitle mode cannot process the upper half; explicitly choose scoped Text mode if authorized")
        grouped.setdefault((region.start_time, region.end_time), []).append({
            "top_left_x": left/width, "top_left_y": top/height,
            "bottom_right_x": right/width, "bottom_right_y": bottom/height})
    intervals = sorted(grouped)
    if any(current[0] < previous[1] for previous, current in zip(intervals, intervals[1:])):
        raise ValueError("Time ranges may not overlap; give simultaneous rectangles the same start/end")
    segments = [{"start_time": a, "end_time": b, "erase_ratio_location": grouped[a, b]} for a, b in intervals]
    request = SubtitleEraseRequest(video_url="https://example.com/placeholder.mp4", mode=mode,
                                   time_segment_filter={"mode": "selected", "segments": segments})
    body = request.model_dump(exclude={"video_url"}, exclude_none=True)
    return {"source": info, "request_fields": body, "selected_seconds": sum(b-a for a, b in intervals),
            "padding_pixels": padding_pixels, "paid_request_sent": False,
            "notes": ["Rectangles/times are caller-observed evidence, not automatic subtitle detection.",
                      "Include outlines and shadows; review scene changes and moving subtitles.",
                      "Narrow rectangles limit exposure but cannot guarantee artifact-free reconstruction.",
                      "Upload the same source, add its video_url, preview, then submit once when authorized."]}


class ReviewSample(NativeModel):
    time: float = Field(ge=0)
    erase_regions: list[EraseLocation] = Field(default_factory=list, max_length=20)
    protected_regions: list[EraseLocation] = Field(default_factory=list, max_length=20)


def rectangle(region: EraseLocation, width: int, height: int) -> tuple[int, int, int, int]:
    return (int(region.top_left_x*width), int(region.top_left_y*height),
            min(width, math.ceil(region.bottom_right_x*width)), min(height, math.ceil(region.bottom_right_y*height)))


def frame(path: str, stamp: float) -> Image.Image:
    result = run([ffmpeg_bin(), "-nostdin", "-hide_banner", "-v", "error", "-protocol_whitelist", "file,pipe", "-ss", str(stamp),
                  "-i", path, "-map", "0:v:0", "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1"])
    with Image.open(io.BytesIO(result.stdout)) as decoded:
        return decoded.convert("RGB")


def comparison_metrics(before: Image.Image, after: Image.Image, sample: ReviewSample) -> dict:
    a, b = np.asarray(before, dtype=np.float32), np.asarray(after, dtype=np.float32)
    height, width = a.shape[:2]
    difference = np.abs(a-b).mean(axis=2)
    ga, gb = a.mean(axis=2), b.mean(axis=2)
    ea = np.hypot(*np.gradient(ga))
    eb = np.hypot(*np.gradient(gb))
    excluded = np.zeros((height, width), dtype=bool)
    for region in sample.erase_regions:
        x1, y1, x2, y2 = rectangle(region, width, height)
        # Protect the gradient from subtitle boundary pixels leaking out of its mask.
        excluded[max(0,y1-2):min(height,y2+2), max(0,x1-2):min(width,x2+2)] = True
    def measure(bounds):
        x1, y1, x2, y2 = bounds
        mask = ~excluded[y1:y2, x1:x2]
        if not mask.any():
            return {"status": "no_unmasked_pixels"}
        delta, source_edge, output_edge = difference[y1:y2,x1:x2][mask], ea[y1:y2,x1:x2][mask], eb[y1:y2,x1:x2][mask]
        energy = float(source_edge.mean())
        ratio = float(output_edge.mean()) / energy if energy > 0.01 else None
        return {"mean_absolute_delta_0_255": round(float(delta.mean()), 3),
                "changed_pixel_fraction": round(float((delta > 12).mean()), 4),
                "source_edge_energy": round(energy, 3), "edge_energy_ratio": round(ratio, 3) if ratio is not None else None,
                "possible_softening": bool(energy > 2 and ratio is not None and ratio < 0.65 and float(delta.mean()) > 2)}
    tiles = []
    for row in range(8):
        for col in range(8):
            bounds = (col*width//8, row*height//8, (col+1)*width//8, (row+1)*height//8)
            metric = measure(bounds)
            tiles.append({"normalized_bounds": [col/8,row/8,(col+1)/8,(row+1)/8], **metric})
    tiles.sort(key=lambda item: (item.get("possible_softening", False), item.get("mean_absolute_delta_0_255", 0)), reverse=True)
    return {"whole_frame_excluding_subtitles": measure((0,0,width,height)), "largest_changes": tiles[:8],
            "protected_regions": [{"region": region.model_dump(), **measure(rectangle(region,width,height))}
                                  for region in sample.protected_regions],
            "subtitle_mask_supplied": bool(sample.erase_regions),
            "interpretation": "Review candidates only. Without a subtitle mask, removed glyph edges can look like blur. Compression, motion/alignment and lighting also affect metrics; this does not prove artifact-free output or detect residual text."}


def audio_hashes(path: str) -> list[str]:
    # Hash each copied encoded audio stream separately, without re-encoding or writing audio.
    result = run([ffmpeg_bin(), "-nostdin", "-hide_banner", "-v", "error", "-protocol_whitelist", "file,pipe", "-i", path,
                  "-map", "0:a", "-c:a", "copy", "-f", "streamhash", "-hash", "sha256", "pipe:1"], 300)
    return [line.split("=",1)[1].strip() for line in result.stdout.decode().splitlines() if "=" in line]


def review(source_path: str, result_path: str, output_dir: str, samples: list[ReviewSample] | None = None) -> dict:
    source, result = media_info(source_path), media_info(result_path)
    duration = min(source["duration_seconds"], result["duration_seconds"])
    if samples is None:
        samples = [ReviewSample(time=duration*(i+0.5)/16) for i in range(16)]
    if not 1 <= len(samples) <= 60 or any(sample.time >= duration for sample in samples):
        raise ValueError("Provide 1..60 timestamps within both videos")
    dest = Path(output_dir)
    if not dest.is_absolute() or dest.exists():
        raise ValueError("Review output_dir must be a NEW absolute directory")
    dest.mkdir(parents=True, exist_ok=False)
    dimensions_match = (source["width"],source["height"]) == (result["width"],result["height"])
    fps_match = source["fps"] == result["fps"]
    audio = {"source_has_audio": bool(source["audio_codec"]), "result_has_audio": bool(result["audio_codec"])}
    if audio["source_has_audio"] and audio["result_has_audio"]:
        audio.update(source_hashes=audio_hashes(source_path), result_hashes=audio_hashes(result_path))
        audio["encoded_streams_match"] = bool(audio["source_hashes"]) and audio["source_hashes"] == audio["result_hashes"]
    else:
        audio["encoded_streams_match"] = None
    findings = []
    for index, sample in enumerate(samples):
        before, after = frame(source_path,sample.time), frame(result_path,sample.time)
        metrics = comparison_metrics(before,after,sample) if dimensions_match and fps_match else None
        preview_before = before.copy(); preview_after = after.copy()
        preview_before.thumbnail((540,720)); preview_after.thumbnail((540,720))
        width, height = preview_before.size
        preview_after = preview_after.resize((width,height))
        delta = Image.fromarray(np.clip(np.abs(np.asarray(preview_before,dtype=np.int16)-np.asarray(preview_after,dtype=np.int16))*4,0,255).astype(np.uint8))
        sheet = Image.new("RGB",(width*3,height+30),"white")
        draw = ImageDraw.Draw(sheet)
        for column, (im,label) in enumerate(((preview_before,"SOURCE"),(preview_after,"RESULT"),(delta,"DIFFERENCE x4"))):
            sheet.paste(im,(column*width,30)); draw.text((column*width+8,8),f"{label}  {sample.time:.3f}s",fill="black")
        evidence = dest / f"frame-{index:03d}-{sample.time:.3f}.png"
        sheet.save(evidence)
        findings.append({"time":sample.time,"evidence_path":str(evidence),"metrics":metrics,"crop_paths":[]})
        # Preserve full-resolution crops for supplied review regions, where denim/stair detail matters.
        for number, region in enumerate(sample.erase_regions + sample.protected_regions):
            if not dimensions_match:
                break
            bounds = rectangle(region,before.width,before.height)
            left,right = before.crop(bounds),after.crop(bounds)
            crop = Image.new("RGB",(left.width*2,left.height+24),"white")
            crop.paste(left,(0,24));crop.paste(right,(left.width,24))
            ImageDraw.Draw(crop).text((4,4),"SOURCE / RESULT - native pixels",fill="black")
            crop_path = dest/f"crop-{index:03d}-{number:02d}.png"
            crop.save(crop_path)
            findings[-1]["crop_paths"].append(str(crop_path))
    report = {"status":"needs_visual_review", "source":source,"result":result,
              "checks":{"dimensions_match":dimensions_match,"fps_match":fps_match,
                        "duration_delta_seconds":round(result["duration_seconds"]-source["duration_seconds"],3),
                        "size_ratio":round(result["bytes"]/source["bytes"],3),"audio":audio},
              "samples":findings,"report_path":str(dest/"report.json"),"paid_request_sent":False,
              "review_required":["Residual subtitles including shadows/first and last subtitle frames",
                                 "Denim/fabric texture, step/railing edges and other changes near the repair",
                                 "Play repaired intervals to check temporal flicker and audiovisual sync",
                                 "Audio hashes do not verify audio/video alignment; sample metrics cannot certify the entire video"],
              "automatic_quality_pass":False}
    (dest/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return report


def publish_copy(source_path: str, dest: str, crf: int = 18, preset: str = "slow") -> dict:
    source = media_info(source_path)
    target = Path(dest)
    if not target.is_absolute() or target.suffix.lower() != ".mp4" or os.path.lexists(target):
        raise ValueError("Publishing dest must be a NEW absolute .mp4 path")
    if not 14 <= crf <= 28 or preset not in {"medium","slow","slower"}:
        raise ValueError("crf must be 14..28; preset must be medium, slow or slower")
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".publish-",dir=target.parent) as folder:
        temporary = Path(folder)/"publish.mp4"
        run([ffmpeg_bin(),"-nostdin","-hide_banner","-v","error","-protocol_whitelist","file,pipe","-i",source_path,
             "-map","0:v:0","-map","0:a?","-map_metadata","0","-c:v","libx264","-crf",str(crf),
             "-preset",preset,"-c:a","copy","-movflags","+faststart",str(temporary)],1800)
        result = media_info(str(temporary))
        if (source["width"],source["height"],source["fps"]) != (result["width"],result["height"],result["fps"]):
            raise ValueError("Publishing copy unexpectedly changed dimensions or frame rate")
        if abs(source["duration_seconds"]-result["duration_seconds"]) > max(0.02,1/(source["fps"] or 25)):
            raise ValueError("Publishing copy unexpectedly changed duration")
        matches = audio_hashes(source_path) == audio_hashes(str(temporary)) if source["audio_codec"] else None
        if matches is False:
            raise ValueError("Publishing copy audio verification failed")
        created = False
        try:
            with target.open("xb") as output, temporary.open("rb") as incoming:
                created = True
                shutil.copyfileobj(incoming,output)
        except BaseException:
            if created:
                target.unlink(missing_ok=True)
            raise
    result["path"] = str(target)
    return {"source":source,"result":result,"crf":crf,"preset":preset,"audio_streams_match":matches,
            "size_ratio":round(result["bytes"]/source["bytes"],3),"paid_request_sent":False,
            "note":"Separate lossy H.264 publishing copy; master remains unchanged. No size guarantee. Compression cannot recover repaired texture. Review the copy before publishing."}
