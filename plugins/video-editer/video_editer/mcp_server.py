"""Standalone model-free MCP server for video_editer.

Agents provide editorial judgement.  This server only stores a versioned
timeline, validates explicit edits and renders it through the local FFmpeg
execution engine.  No planning, LLM, or generative-model call is made here.
"""

from __future__ import annotations

import copy
import hashlib
import tempfile
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from .contracts import check_overlay, finite, position_expression, timeline_changes

from . import engine, timing, preflight, jobs, canvas, visuals, animation, processes
from . import media_ops, media_jobs, media_store, segments, audio as audio_engine, quality
from . import resources, encoders, hooks, audio_review, delivery_review, commerce
from .locking import project_write, file_lock


mcp = FastMCP(
    "video_editer",
    instructions=(
        "Deterministic video-editing execution tools. Never infer editorial intent: "
        "inspect media, make explicit timeline edits, validate, then render; review the exact output before delivery. "
        "Timeline edits return a revision and undo snapshot; render jobs return an immutable job ID."
    ),
)

TRANSITIONS = {"punch_cut", "zoom_punch", "paint_flash", "slide_push", "whip_pan", "detail_smash_cut", "none", *engine.SEASONAL_TRANSITIONS, *visuals.TRANSITIONS}
POSITIONS = {"product_left", "product_right"}
SPEEDS = {1.0, 1.25}
COMPACT_TEMPLATES = {"comic_burst", "comic_bubble", "celebrate_cloud", "number_3d", "promo_3d", "entrance_card", "either_or", *visuals.CALLOUTS}


def _check_callout_text(text: str, template: str) -> None:
    """Reject overflow instead of silently truncating the agent's copy."""
    if template == "point_list":
        parts = text.split("|")
        if len(parts) != 2 or any(not part.strip() or len(part) > 7 for part in parts):
            raise ValueError("point_list needs exactly two non-empty points, max 7 characters each")
    elif template == "statement_block":
        parts = text.split("\n")
        if len(parts) != 2 or not all(p.strip() for p in parts) or len(parts[0]) > 14 or len(parts[1]) > 16:
            raise ValueError("statement_block needs two non-empty lines (max 14/16 characters)")
    elif not text.strip() or len(text) > (10 if template in COMPACT_TEMPLATES else 16):
        raise ValueError("Callout text exceeds template capacity (compact: 10, other: 16 characters)")


def _folder(project_id: str) -> Path:
    if not project_id.startswith("prj_") or not project_id[4:].isalnum():
        raise ValueError("Invalid project_id")
    return engine.project_dir(project_id)


def _read(project_id: str) -> dict[str, Any]:
    return engine.read_json(_folder(project_id) / "project.json", {})


def _write(project_id: str, project: dict[str, Any]) -> None:
    engine.write_json(_folder(project_id) / "project.json", project)


def _default_timeline() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "canvas": dict(canvas.DEFAULT),
        "revision": 0,
        "tracks": {"main": [], "overlays": [], "captions": [], "audio": []},
        "track_meta": {
            "main": {"kind": "video", "locked": False},
            "overlays": {"kind": "overlay", "locked": False},
            "captions": {"kind": "caption", "locked": False},
            "audio": {"kind": "audio", "locked": False},
        },
        "callouts": [],
        "audio": {},
        "outro": {"duration": 0},
        "history": [],
    }


def _timeline(project: dict[str, Any]) -> dict[str, Any]:
    timeline = project.get("mcp_timeline")
    if not isinstance(timeline, dict):
        timeline = _default_timeline()
        project["mcp_timeline"] = timeline
    timeline.setdefault("tracks", {"main": []})
    timeline["tracks"].setdefault("main", [])
    for track_id, kind in (("overlays", "overlay"), ("captions", "caption"), ("audio", "audio")):
        timeline["tracks"].setdefault(track_id, [])
        timeline.setdefault("track_meta", {}).setdefault(track_id, {"kind": kind, "locked": False})
    timeline["track_meta"].setdefault("main", {"kind": "video", "locked": False})
    timeline.setdefault("callouts", [])
    timeline.setdefault("history", [])
    return timeline


def _asset(project: dict[str, Any], asset_id: str) -> dict[str, Any]:
    for item in project.get("materials", []):
        if isinstance(item, dict) and item.get("id") == asset_id:
            return item
    raise ValueError(f"Unknown asset_id: {asset_id}")


def _duration(asset: dict[str, Any]) -> float:
    probe = asset.get("probe") if isinstance(asset.get("probe"), dict) else {}
    duration = float(probe.get("duration", 0) or 0)
    if duration <= 0:
        asset["probe"] = engine.ffprobe(Path(asset["path"]))
        duration = float(asset["probe"].get("duration", 0) or 0)
    return duration


def _snapshot(project: dict[str, Any], label: str) -> str:
    timeline = _timeline(project)
    snapshot_id = f"snap_{uuid.uuid4().hex[:12]}"
    snapshots = project.setdefault("mcp_snapshots", {})
    snapshots[snapshot_id] = {"label": label, "timeline": copy.deepcopy(timeline)}
    return snapshot_id


def _commit(project_id: str, project: dict[str, Any], action: str, detail: dict[str, Any], undo_snapshot: str) -> dict[str, Any]:
    timeline = _timeline(project)
    timing.resolve(timeline)  # Reject broken explicit source anchors before writing.
    timeline["revision"] = int(timeline.get("revision", 0)) + 1
    timeline["history"].append({"revision": timeline["revision"], "action": action, "detail": detail, "undo_snapshot": undo_snapshot})
    _write(project_id, project)
    return {"project_id": project_id, "revision": timeline["revision"], "undo_snapshot": undo_snapshot}


def _validate(project: dict[str, Any]) -> list[str]:
    try:
        timeline = timing.resolve(_timeline(project))
        output_canvas = canvas.settings(timeline.get('canvas'))
    except (KeyError, TypeError, ValueError) as exc:
        return [str(exc)]
    errors: list[str] = []
    clips = timeline["tracks"]["main"]
    try:
        audio_engine.config(timeline.get('audio_config'))
    except ValueError as exc:
        errors.append(str(exc))
    if not clips:
        errors.append("main track has no clips")
    for index, clip in enumerate(clips, 1):
        try:
            asset = _asset(project, str(clip.get("asset_id", "")))
            start, end = float(clip["start"]), float(clip["end"])
            speed = float(clip.get("playback_speed", 1.0))
            if asset.get("kind") not in {"video", "image"}:
                errors.append(f"clip {index} requires video/image media")
            limit = 3600 if asset.get('kind') == 'image' else _duration(asset)+.05
            if not finite(start, end, speed) or not 0 <= start < end <= limit:
                errors.append(f"clip {index} is outside source bounds")
            engine.check_camera(clip.get('camera'))
            audio_engine.clip_settings(clip)
            _clip_labels(clip)
            if speed not in SPEEDS:
                errors.append(f"clip {index} speed must be 1.0 or 1.25")
            if str(clip.get("transition", "punch_cut")) not in TRANSITIONS:
                errors.append(f"clip {index} has unsupported transition")
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"clip {index} invalid: {exc}")
    for index, event in enumerate(timeline["callouts"], 1):
        template = str(event.get("template", ""))
        text = str(event.get("text", ""))
        try:
            if template == "point_list" and "points" in event:
                timing.check_points(event["points"])
            else:
                _check_callout_text(text, template)
        except ValueError as exc:
            errors.append(f"callout {index}: {exc}")
        try:
            start, end = float(event["start"]), float(event["end"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"callout {index} has invalid timing")
            continue
        if not text.strip() or not finite(start, end) or not 0 <= start < end:
            errors.append(f"callout {index} must have text and positive duration")
        if str(event.get("position")) not in POSITIONS:
            errors.append(f"callout {index} must use product_left or product_right")
        if template not in engine.CALLOUT_TEMPLATES:
            errors.append(f"callout {index} has unsupported template")
        if template == "point_list" and "points" not in event:
            points = [part.strip() for part in text.split("|")]
            if len(points) != 2 or not all(points):
                errors.append(f"callout {index} point_list requires two non-empty points")
            if end - start < 1.1 - 1e-9:
                errors.append(f"callout {index} point_list requires at least 1.1 seconds for its two preset beats")
    for index, graphic in enumerate(_entries_by_kind(timeline, "overlay"), 1):
        try:
            if _asset(project, str(graphic["asset_id"])).get("kind") not in {"image", "video"}:
                raise ValueError("overlay asset must be image/video")
            check_overlay(graphic, (output_canvas['width'],output_canvas['height']))
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"overlay {index} invalid: {exc}")
    for index, subtitle in enumerate(_entries_by_kind(timeline, "caption"), 1):
        try:
            if not str(subtitle["text"]).strip() or not finite(subtitle["start"], subtitle["end"]) or not 0 <= float(subtitle["start"]) < float(subtitle["end"]):
                errors.append(f"caption {index} must have text and positive duration")
        except (KeyError, TypeError, ValueError):
            errors.append(f"caption {index} invalid")
    for index, audio in enumerate(_entries_by_kind(timeline, "audio"), 1):
        try:
            asset = _asset(project, str(audio["asset_id"]))
            if asset.get("kind") not in {"audio", "video"}:
                errors.append(f"audio track {index} is not audio/video media")
            values = [float(audio.get(k, 0)) for k in ("start", "duration", "volume", "fade_in", "fade_out", "source_start")]
            if not finite(*values) or any(value < 0 for value in values):
                errors.append(f"audio track {index} has invalid gain/timing")
            if isinstance(audio.get('playback_speed', 1.0), bool) or audio.get('playback_speed', 1.0) not in SPEEDS:
                errors.append(f"audio track {index} has unsupported playback speed")
        except (KeyError, TypeError, ValueError):
            errors.append(f"audio track {index} invalid")
    try:
        if clips and not errors:
            engine.validate_transition_timing(clips)
            errors.extend(preflight.inspect(project, timeline)["errors"])
    except ValueError as exc:
        errors.append(str(exc))
    return errors


def _plan(project: dict[str, Any]) -> dict[str, Any]:
    timeline = timing.resolve(_timeline(project))
    clips = copy.deepcopy(timeline["tracks"]["main"])
    return {
        "title": project.get("title", "MCP edit"),
        "clips": clips,
        "visual_effects": copy.deepcopy(timeline["callouts"]),
        "audio": copy.deepcopy(timeline.get("audio", {})),
        "canvas": canvas.settings(timeline.get('canvas')),
    }


def _entries_by_kind(timeline: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    """Collect entries from default and agent-created tracks of a given kind."""
    return [entry for track_id, rows in timeline.get("tracks", {}).items() if timeline.get("track_meta", {}).get(track_id, {}).get("kind") == kind for entry in rows]


def _escape_filter_path(path: Path) -> str:
    """Use a Windows path safely inside an FFmpeg filter value."""
    return path.as_posix().replace("\\", "\\\\").replace(":", "\\:").replace("'", r"\'")


def _ass_time(seconds: float) -> str:
    centiseconds = max(0, round(seconds * 100))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    seconds_part, cs = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{seconds_part:02d}.{cs:02d}"


def _apply_captions(source: Path, output: Path, captions: list[dict[str, Any]]) -> Path:
    if not captions:
        return source
    ass_path = output.parent / "mcp-captions.ass"
    header = """[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\nStyle: Caption,Microsoft YaHei,54,&H00FFFFFF,&H000000FF,&H00101010,&H80101010,-1,0,0,0,100,100,0,0,1,4,1,2,46,46,140,1\n\n[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"""
    lines: list[str] = []
    for event in captions:
        text = str(event.get("text", "")).replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")
        lines.append(f"Dialogue: 0,{_ass_time(float(event['start']))},{_ass_time(float(event['end']))},Caption,,0,0,0,,{text}")
    font = os.environ.get("VIDEO_EDITER_FONT", "Microsoft YaHei").strip()
    if not font or any(c in font for c in ",\r\n"):
        raise ValueError("VIDEO_EDITER_FONT must be one ASS-safe font family")
    info=engine.ffprobe(source)
    ass_path.write_text(engine.remap_ass(header.replace("Microsoft YaHei", font) + "\n".join(lines) + "\n",info['width'],info['height']), encoding="utf-8")
    import subprocess
    command = [engine.ffmpeg_bin(), "-nostdin", "-y", "-i", str(source), "-vf", f"ass='{_escape_filter_path(ass_path)}'", "-map", "0:v", "-map", "0:a?", "-r", "25", "-c:v", "libx264", "-c:a", "copy", "-movflags", "+faststart", str(output)]
    processes.run(command, capture_output=True, check=True, timeout=240)
    return output


def _apply_graphics(source: Path, output: Path, graphics: list[dict[str, Any]]) -> Path:
    if not graphics:
        return source
    probe = engine.ffprobe(source)
    canvas_w, canvas_h = int(probe["width"]), int(probe["height"])
    for graphic in graphics:
        check_overlay(graphic,(canvas_w,canvas_h))
    if any(g.get('mask','none')!='none' or g.get('transform_mode') or
           any(any(k in key for k in ('scale','rotation','opacity','easing')) for key in g.get('keyframes',[])) for g in graphics):
        return visuals.render_layer(source,output,graphics=graphics)
    command = [engine.ffmpeg_bin(), "-nostdin", "-y", "-i", str(source)]
    for graphic in graphics:
        asset_path = Path(graphic["path"])
        check_overlay(graphic,(canvas_w,canvas_h))
        duration = float(graphic["end"]) - float(graphic["start"])
        if graphic.get("kind") == "image":
            command.extend(["-loop", "1", "-framerate", "30", "-t", f"{duration:.6f}", "-i", str(asset_path)])
        else:
            command.extend(["-ss", str(float(graphic.get("source_start", 0))), "-t", f"{duration:.3f}", "-i", str(asset_path)])
    filters = ["[0:v]setpts=PTS-STARTPTS[v0]"]
    current = "v0"
    for index, graphic in enumerate(graphics, start=1):
        start, end = float(graphic["start"]), float(graphic["end"])
        width = max(1, round(canvas_w * float(graphic["width"])))
        height = max(1, round(canvas_h * float(graphic["height"])))
        opacity = float(graphic.get("opacity", 1.0))
        label, next_label = f"g{index}", f"gv{index}"
        filters.append(f"[{index}:v]scale={width}:{height},setsar=1,format=rgba,colorchannelmixer=aa={opacity:.6f},setpts=PTS-STARTPTS+{start:.6f}/TB[{label}]")
        x_expr = position_expression(graphic, "x", canvas_w)
        y_expr = position_expression(graphic, "y", canvas_h)
        filters.append(f"[{current}][{label}]overlay=x='{x_expr}':y='{y_expr}':enable='gte(t,{start:.6f})*lt(t,{end:.6f})':eof_action=pass:repeatlast=0:eval=frame[{next_label}]")
        current = next_label
    command.extend(["-filter_complex_threads", "1", "-filter_complex", ";".join(filters), "-map", f"[{current}]", "-map", "0:a?", "-r", "25", "-c:v", "libx264", "-c:a", "copy", "-movflags", "+faststart", str(output)])
    processes.run(command, capture_output=True, check=True, timeout=300)
    return output


def _mix_audio_tracks(source: Path, output: Path, tracks: list[dict[str, Any]], source_volume: float = 1.0) -> Path:
    if not tracks and source_volume == 1.0:
        return source
    metadata = output_inspect(str(source))
    if not metadata["valid"]:
        raise ValueError("Cannot mix audio onto invalid video")
    duration_video = float(metadata["probe"]["duration"])
    if not tracks and not metadata["has_audio"]:
        return source
    command = [engine.ffmpeg_bin(), "-nostdin", "-y", "-i", str(source)]
    for entry in tracks:
        command.extend(["-i", str(entry["path"])])
    filters = ([f"[0:a]asetpts=PTS-STARTPTS,volume={source_volume:.6f},apad,atrim=duration={duration_video:.6f}[a0]"]
               if metadata["has_audio"] else
               [f"anullsrc=r=48000:cl=stereo,atrim=duration={duration_video:.6f}[a0]"])
    labels = ["[a0]"]
    for index, entry in enumerate(tracks, start=1):
        start = max(0.0, float(entry.get("start", 0)))
        volume = float(entry.get("volume", 1.0))
        fade_in = max(0.0, float(entry.get("fade_in", 0)))
        fade_out = max(0.0, float(entry.get("fade_out", 0)))
        source_start = float(entry.get("source_start", 0))
        speed = float(entry.get('playback_speed', 1.0))
        if speed not in SPEEDS:
            raise ValueError('Unsupported audio playback speed')
        available = (float(engine.ffprobe(Path(entry["path"])).get("duration", 0)) - source_start) / speed
        duration = min(float(entry.get("duration", 0) or available), available, duration_video - start)
        if duration <= 0:
            raise ValueError("Audio entry does not intersect available source/output duration")
        pieces = [f"[{index}:a]atrim=start={source_start:.6f}:duration={duration*speed:.6f}", "asetpts=PTS-STARTPTS"]
        if speed != 1.0:
            pieces.append(f"atempo={speed:.6f}")
        pieces.append(f"volume={volume:.6f}")
        if fade_in:
            pieces.append(f"afade=t=in:st=0:d={fade_in:.3f}")
        if fade_out:
            pieces.append(f"afade=t=out:st={max(0, duration-fade_out):.6f}:d={min(duration, fade_out):.6f}")
        pieces.append(f"adelay={int(start * 1000)}:all=1")
        label = f"a{index}"
        filters.append(",".join(pieces) + f"[{label}]")
        labels.append(f"[{label}]")
    filters.append(f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:dropout_transition=0:normalize=0[mix]")
    command.extend(["-filter_complex_threads", "1", "-filter_complex", ";".join(filters), "-map", "0:v", "-map", "[mix]", "-c:v", "copy", "-c:a", "aac", "-movflags", "+faststart", str(output)])
    processes.run(command, capture_output=True, check=True, timeout=300)
    return output


def _clip_labels(clip):
    for name in ('role', 'shot_type'):
        value = clip.get(name, 'detail' if name == 'role' else 'other')
        if not isinstance(value, str) or not value.strip() or len(value) > 80:
            raise ValueError(f'{name} requires a nonblank label of at most 80 characters; custom labels are preserved')


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def project_create(title: str, prompt: str = "") -> dict[str, Any]:
    """Create an empty, versioned editing project. No media or model calls occur."""
    project = engine.create_project(title, prompt)
    project["mcp_timeline"] = _default_timeline()
    _write(project["id"], project)
    return {"project_id": project["id"], "revision": 0, "timeline": project["mcp_timeline"]}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@resources.heavy_operation
@project_write
def media_import(project_id: str, source_path: str, base_dir: str | None = None, deduplicate: bool = True) -> dict[str, Any]:
    """Copy one local video/image/audio file into a project and inspect video metadata."""
    source = Path(source_path).expanduser()
    if not source.is_absolute():
        if base_dir is None or not Path(base_dir).is_absolute():
            raise ValueError('Relative media paths require an explicit absolute base_dir')
        source = Path(base_dir) / source
    source = source.resolve()
    if not source.is_file():
        raise ValueError(f"Source file does not exist: {source}")
    project = _read(project_id)
    def digest(path):
        with Path(path).open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest()
    fingerprint = digest(source)
    warnings = []
    for existing in project.get('materials', []):
        existing_path = Path(existing['path'])
        if deduplicate and existing_path.is_file() and existing_path.stat().st_size == source.stat().st_size and digest(existing_path) == fingerprint:
            return {'project_id': project_id, 'revision': _timeline(project)['revision'], 'asset': existing,
                    'deduplicated': True, 'warnings': ['Identical content already imported; reused asset ID.']}
        if existing.get('name', '').casefold() == source.name.casefold():
            warnings.append('A different asset uses the same filename; both are kept with distinct IDs.')
    undo = _snapshot(project, "before media_import")
    asset_id = f"mat_{uuid.uuid4().hex[:10]}"
    target = _folder(project_id) / "materials" / f"{asset_id}_{source.name}"
    shutil.copy2(source, target)
    suffix = source.suffix.lower()
    kind = "video" if suffix in {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"} else "image" if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp"} else "audio" if suffix in {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"} else "other"
    item = {"id": asset_id, "name": source.name, "kind": kind, "path": str(target), "content_type": None, "probe": engine.ffprobe(target) if kind == "video" else {}}
    project.setdefault("materials", []).append(item)
    result = _commit(project_id, project, "media_import", {"asset_id": asset_id, "source": str(source)}, undo)
    return {**result, "asset": item, "deduplicated": False, "sha256": fingerprint, "warnings": warnings}


@mcp.tool(annotations={"readOnlyHint": True})
def media_inspect(project_id: str, asset_id: str | None = None, refresh: bool = False) -> dict[str, Any]:
    """Read stored metadata immediately, without probes or a heavy-work wait. refresh=true explicitly probes under the shared resource guard; neither mode changes the project. Cached metadata is not source verification."""
    if not isinstance(refresh, bool):
        raise ValueError("refresh must be boolean")
    project = _read(project_id)
    rows = project.get("materials", [])
    if asset_id:
        rows = [_asset(project, asset_id)]
    if refresh:
        with resources.heavy_slot():
            for row in rows:
                if row.get("kind") in {"video", "audio"}:
                    row["probe"] = engine.ffprobe(Path(row["path"]))
    return {"project_id": project_id, "materials": rows,
            "inspection": "fresh_probe" if refresh else "stored_metadata",
            "metadata_refreshed": refresh, "content_verified": False}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@resources.heavy_operation
@project_write
def media_extract_frames(project_id: str, asset_id: str, frame_count: int = 8) -> dict[str, Any]:
    """Extract evenly distributed review frames from one video; no vision or model analysis is used."""
    if not 1 <= frame_count <= 24:
        raise ValueError("frame_count must be between 1 and 24")
    project = _read(project_id)
    asset = _asset(project, asset_id)
    if asset.get("kind") != "video":
        raise ValueError("Frame extraction requires a video asset")
    duration = _duration(asset)
    output = _folder(project_id) / "mcp_frames" / asset_id / uuid.uuid4().hex[:12]
    frames: list[str] = []
    for index in range(frame_count):
        timestamp = max(0.0, duration - 0.1) * index / max(1, frame_count - 1)
        frame = output / f"frame-{index + 1:02d}-{timestamp:.2f}.jpg"
        frame.parent.mkdir(parents=True, exist_ok=True)
        processes.run([engine.ffmpeg_bin(), "-nostdin", "-y", "-ss", f"{timestamp:.3f}", "-i", str(asset["path"]), "-frames:v", "1", "-q:v", "3", str(frame)], capture_output=True, check=True, timeout=60)
        if not frame.is_file() or frame.stat().st_size == 0:
            raise ValueError(f"No decodable frame at {timestamp:.3f}s")
        frames.append(str(frame))
    return {"project_id": project_id, "asset_id": asset_id, "frames": frames}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def media_register(project_id: str, source_path: str, mode: Literal['managed_copy','reference'] = 'managed_copy') -> dict[str, Any]:
    """Submit a background VIDEO import with streaming SHA-256; reference avoids copying. Audio/images use media_import under the same heavy resource guard. Poll media_job_status. No model calls."""
    _folder(project_id)
    source=Path(source_path).expanduser().resolve()
    if source.suffix.lower() in {'.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg', '.png', '.jpg', '.jpeg', '.webp', '.bmp'}:
        raise ValueError('media_register accepts VIDEO only; use media_import for audio/image files under the shared heavy resource guard. Do not retry this as a video job.')
    if mode not in ('managed_copy','reference'): raise ValueError('Unknown import mode')
    signature=media_ops.stat_signature(source)
    if signature['size']==0: raise ValueError('Cannot register an empty file')
    return media_jobs.submit(project_id,{'operation':'register','source_path':str(source),'source_stat':signature,
                                         'mode':mode,'asset_id':'mat_'+uuid.uuid4().hex[:16]})


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def media_prepare(project_id: str, asset_id: str, operation: Literal['fingerprint','verify','preview','reference','frames','scan'], options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Background media work. fingerprint upgrades legacy VIDEO imports; verify rejects changed bytes. preview/reference/scan: start,end <=600s; frames: 1-48 timestamps. Even max_edge=640,160..1920. reference exports higher-quality 25fps video, include_audio=false by default; no upload/anonymization. Scan only detects black/silence, not meaning. All operations/retries share the heavy queue."""
    _asset(_read(project_id),asset_id)
    if options is not None and not isinstance(options,dict): raise ValueError('options must be an object')
    options=copy.deepcopy({} if options is None else options)
    media_ops.validate_options(project_id,asset_id,operation,options)
    return media_jobs.submit(project_id,{'operation':operation,'asset_id':asset_id,'options':options})


@mcp.tool(annotations={"readOnlyHint": True})
def media_job_status(project_id: str, job_id: str) -> dict[str, Any]:
    """Read media job stage, byte/count progress, result or structured failure. No speculative percent."""
    from . import speech
    return speech.public_job(media_jobs.status(project_id,job_id))


@mcp.tool(annotations={"readOnlyHint": True})
def media_job_list(project_id: str, offset: int = 0, limit: int = 20) -> dict[str, Any]:
    """Page durable media jobs. Retains unsuccessful attempts and artifacts."""
    media_store.number(offset,'offset',0,1000000); media_store.number(limit,'limit',1,100)
    if not isinstance(offset,int) or not isinstance(limit,int): raise ValueError('Pagination must be integers')
    rows=media_jobs.records(project_id)
    return {'items':[media_job_status(project_id,r['job_id']) for r in rows[offset:offset+limit]],
            'total':len(rows),'next_offset':offset+limit if offset+limit<len(rows) else None}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def media_job_cancel(project_id: str, job_id: str) -> dict[str, Any]:
    """Cooperatively cancel this media job, retaining originals and all partial artifacts."""
    return media_jobs.cancel(project_id,job_id)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def media_job_retry(project_id: str, job_id: str) -> dict[str, Any]:
    """New attempt from a failed/cancelled/interrupted saved request. Reuse verified completed evidence; unfinished operations restart, not byte-resume."""
    return media_jobs.retry(project_id,job_id)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def audio_review_submit(project_id: str, audio_ids: list[str] | None = None, render_job_id: str | None = None, context_seconds: float = 1.5) -> dict[str, Any]:
    """Queue per-cue signal review and equal-level with/without/contribution WAVs. Select 1-16 audio IDs, or omit IDs to select role=sfx. Optional succeeded render_job_id reviews its SAVED revision and delivered sound, not live edits. Max 600s timeline, 15s/cue. Poll/cancel/retry via media_job_*; measurements never certify listening quality or adjust gain."""
    request = audio_review.prepare_request(project_id, _read(project_id), audio_ids, render_job_id, context_seconds)
    return media_jobs.submit(project_id, request)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def media_queue_start() -> dict[str, Any]:
    """Restart the shared media/render FIFO after host restart; drains queued jobs only."""
    return media_jobs.start()


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def delivery_review_record(project_id: str, render_job_id: str, checks: list[dict[str, Any]], audio_review_job_ids: list[str] | None = None, warning_resolutions: list[dict[str, Any]] | None = None, inspection_job_id: str | None = None) -> dict[str, Any]:
    """Record commerce-v2 review. Required: succeeded delivery_inspection_submit job for these bytes. checks: id,status(pass/fail/not_checked),note,evidence_paths,method,repair_action(on fail),observations. Continuous visual checks need continuous_review/user_feedback; auditory checks need listening/user_feedback. Pure hook_picture pass observations: full_body_visible,natural_display,no_continuous_speaking,burned_captions_compatible=true. not_applicable only for absent hook/sfx, never mandatory checks. Missing inspection cannot be waived. Failed checks require changed output AND relevant content, not merely revision. No automatic semantic certification."""
    return delivery_review.record(project_id, render_job_id, checks, audio_review_job_ids, warning_resolutions, inspection_job_id)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def delivery_inspection_submit(project_id: str, render_job_id: str) -> dict[str, Any]:
    """Queue bound output evidence: actual PTS frames, listening WAV, per-selected-source audio eligibility and per-final-segment sound. Uses the single heavy FIFO. Poll media_job_status; pass its succeeded job ID to delivery_review_record. Does not decide aesthetics or listen on behalf of an agent."""
    return delivery_review.inspection_submit(project_id, render_job_id)


@mcp.tool(annotations={"readOnlyHint": True})
def delivery_review_form(project_id: str, render_job_id: str, inspection_job_id: str) -> dict[str, Any]:
    """Return exact v2 check IDs, methods, false/not_checked defaults and bound evidence paths for a succeeded inspection. Watch/listen before filling. Pass the completed checks to delivery_review_record; a generated form is never approval."""
    return delivery_review.form(project_id, render_job_id, inspection_job_id)


@mcp.tool(annotations={"readOnlyHint": True})
def speech_runtime_status() -> dict[str, Any]:
    """Read bundled local ASR dependencies/model availability. No download or inference. CUDA ASR support differs from video encoder GPU support; actual device is recorded by queued inference."""
    from . import speech
    return speech.runtime_status()


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def commerce_materials_prepare(project_id: str, asset_ids: list[str], language: str = 'zh', device: Literal['auto','cuda','cpu'] = 'auto') -> dict[str, Any]:
    """Preferred batch ASR entry: select 1–16 registered source videos as a working pool, validate all windows, deduplicate and dispatch once on the shared single-heavy queue. Model must already be prepared. Returns job IDs for media_jobs_wait. Existing source/range/model analysis is reused without copying approvals. No render or generation."""
    from . import batch
    return batch.materials_prepare(project_id, asset_ids, language, device)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def commerce_candidates_submit(project_id: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Batch candidate evidence (1–32). Each row: role=hook_picture with asset_id/start/end, or role=hook_voice/body with transcript_job_id/consecutive sentence_ids. Validates all first, then queues audio/source frames/internal-cut analysis; no candidate is accepted automatically. Poll media_jobs_wait."""
    from . import batch
    return batch.candidates_submit(project_id, candidates)


@mcp.tool(annotations={"readOnlyHint": True})
def media_jobs_wait(project_id: str, job_ids: list[str], timeout_seconds: float = 20) -> dict[str, Any]:
    """Wait up to 30s for a batch to finish or need recovery, returning compact statuses and paginated sentence previews instead of repeated full job reports. No worker is started by this read; failure retries use media_job_retry on the same queue."""
    from . import batch
    return batch.wait(project_id, job_ids, timeout_seconds)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def media_convert_submit(project_id: str, asset_id: str, start: float, end: float, max_edge: int = 1920, fps: int = 25, interpolate: bool = False) -> dict[str, Any]:
    """Queue a bounded transcode/frame-cadence repair as NEW derived media. Preserve source sound, prefer GPU encoding under shared policy, heavy concurrency 1. Motion interpolation is expensive and only enabled by explicit interpolate=true; do not apply speculatively. Max 600s. Retry through media_job_retry; import/reanalyse output before selection. No terminal FFmpeg needed."""
    options = {'start': start, 'end': end, 'max_edge': max_edge, 'fps': fps, 'interpolate': interpolate}
    media_ops.validate_options(project_id, asset_id, 'convert', options)
    asset = media_ops.asset(project_id, asset_id)
    request = {'operation': 'convert', 'asset_id': asset_id, 'source_sha256': asset['identity']['sha256'], 'options': options}
    return media_jobs.submit_unique(project_id, request)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def speech_model_prepare(project_id: str, source_model_dir: str | None = None) -> dict[str, Any]:
    """Queue local ASR model preparation. Reuse VIDEO_EDITER_ASR_MODEL_DIR, copy an offline source_model_dir, or download the pinned small model from Hugging Face (no footage upload). Existing different files are never overwritten. Shares heavy concurrency 1. Poll media_job_status; retry failure with media_job_retry. No generation/editing."""
    from . import speech
    return speech.model_prepare(project_id, source_model_dir)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def speech_transcribe_submit(project_id: str, asset_id: str, start: float = 0, end: float | None = None, language: str = 'zh', device: Literal['auto','cuda','cpu'] = 'auto') -> dict[str, Any]:
    """Required BEFORE commerce material selection. Queue local faster-whisper word-timed ASR for a registered source (max 600s/window; default starts at 0). No audio upload, synthesis or editing. Auto prefers CUDA, allows bounded CPU only under resource policy. Records source hash, word confidence, sentences and estimated timestamps. Silence/no audio yields explicit display-only evidence, never valid body/voice. Poll media_job_status; failed retries share the same single-heavy-task queue. Read speech_candidates_get after success."""
    from . import speech
    return speech.submit(project_id, asset_id, start, end, language, device)


@mcp.tool(annotations={"readOnlyHint": True})
def speech_candidates_get(project_id: str, job_id: str, role: Literal['all','hook_voice','body'] = 'all', offset: int = 0, limit: int = 20) -> dict[str, Any]:
    """Page source-bound full sentence proposals with IDs, text, context, word confidence and duration. Hook filter is duration only, not an emotion/quality judgment. Join consecutive IDs for a complete thought. Low confidence and meaning boundaries still need actual review; no ASR result can certify speech naturally ends or sounds good."""
    from . import speech
    return speech.candidates_get(project_id, job_id, role, offset, limit)


@mcp.tool(annotations={"readOnlyHint": True})
def speech_range_validate(project_id: str, job_id: str, sentence_ids: list[str], start: float | None = None, end: float | None = None) -> dict[str, Any]:
    """Validate consecutive complete sentence IDs against source-bound ASR word times. Optional range cannot cut first/last word or include adjacent unselected words. Default adds only small handles inside existing pauses. Returns exact source range, quote and evidence hash for commerce_speech_candidate_submit; validation is structural, not listening approval."""
    from . import speech
    return speech.range_validate(project_id, job_id, sentence_ids, start, end)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def commerce_speech_candidate_submit(project_id: str, transcript_job_id: str, sentence_ids: list[str], role: Literal['hook_voice','body']) -> dict[str, Any]:
    """Select voice/body by real sentence IDs, automatically validate timestamps and queue source evidence. No hand-copied seconds needed. Read candidates first; join adjacent sentences to preserve a complete meaning. Still requires commerce_candidate_review/defer after actual audiovisual evidence. This tool does not edit/render a timeline."""
    return commerce.speech_candidate_submit(project_id, transcript_job_id, sentence_ids, role)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def commerce_workflow_start(project_id: str, account: str, product: str, target_seconds: float = 45) -> dict[str, Any]:
    """Start/resume SOURCE-ONLY display+independent-source-voice workflow. Locks account/product/mode. No generation or render. Returns current stage, required actions and exact next tool. Import sources with media_register before candidate selection."""
    return commerce.start(project_id, account, product, target_seconds)


@mcp.tool(annotations={"readOnlyHint": True})
def commerce_workflow_status(project_id: str) -> dict[str, Any]:
    """Resume staged pure editing after interruption/compaction. Returns accepted IDs, missing stage and next tool. State persists outside chat history."""
    return commerce.status(project_id)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def commerce_candidate_submit(project_id: str, asset_id: str, start: float, end: float, role: Literal['hook_picture','hook_voice','body'], transcript_job_id: str | None = None, sentence_ids: list[str] | None = None) -> dict[str, Any]:
    """Inspect a VIDEO range only AFTER source-bound ASR. Hook display 2–4s requires transcription coverage (no_audio/no_speech record allowed). Voice/body require transcript_job_id and consecutive sentence_ids; prefer commerce_speech_candidate_submit to avoid manual seconds. Queue continuous preview, actual source-PTS frames and audio measurement; still requires actual visual/listening review."""
    return commerce.candidate_submit(project_id, asset_id, start, end, role, transcript_job_id, sentence_ids)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def commerce_candidate_review(project_id: str, job_id: str, facts: dict[str, bool], method: Literal['continuous_review','listening','continuous_review_and_listening'], note: str, review_receipt_paths: list[str] | None = None) -> dict[str, Any]:
    """Review succeeded candidate evidence with observed facts AND review_receipt_paths exported from actual tool/user observations (see bundled efficient-execution.md). Method labels alone remain pending. Receipts bind candidate preview bytes, modality and time coverage; do not manufacture them from ASR or signal levels. Hook picture requires continuous review, voice listening, body both. Known rejected/silent defects cannot be relabelled."""
    return commerce.candidate_review(project_id, job_id, facts, method, note, review_receipt_paths)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def commerce_candidate_defer(project_id: str, job_id: str, facts: dict[str, bool | None], note: str) -> dict[str, Any]:
    """Record incomplete candidate review honestly: true=observed, null=unknown. Known false defects and silent body/voice remain blocked. Pending is NOT accepted and only permits explicit preview_only assembly, never final delivery. State exactly what could not be watched/listened to and why."""
    return commerce.candidate_defer(project_id, job_id, facts, note)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def commerce_candidate_skip(project_id: str, job_id: str, reason: str) -> dict[str, Any]:
    """Discard an unselected terminal candidate with a reason, preserving evidence. Use for exhausted/unsuitable source instead of endless execution retries. Queued/running jobs must first be cancelled and confirmed terminal; applied candidates need a replacement plan first. Never marks anything accepted."""
    return commerce.candidate_skip(project_id, job_id, reason)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def commerce_timeline_apply(project_id: str, hook_picture_job_id: str, hook_voice_job_id: str, body_job_ids: list[str], expected_revision: int, voice_speed: Literal[1.0,1.25] = 1.0, transition_after_body: int = 1, transition: str = 'slide_push', replace_timeline: bool = False, preview_only: bool = False) -> dict[str, Any]:
    """Atomically assemble display+independent voice+ordered body. Requires accepted candidates by default. Explicit preview_only=true also permits pending candidates for an unapproved review copy; final remains blocked until real review. Known rejected/silent sources never qualify. Mutes only hook source, keeps pictures/body 1x, fits complete voice, adds one topic transition, returns time map + undo. First body >=3s; total near target. transition_after_body is one-based between body units. 1.25 voice needs authorization. Existing edits require replace_timeline; no render."""
    return commerce.timeline_apply(project_id, hook_picture_job_id, hook_voice_job_id, body_job_ids, voice_speed, transition_after_body, transition, expected_revision, replace_timeline, preview_only)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def commerce_presentation_record(project_id: str, theme_colors: list[str], hook_text_ids: list[str], body_text_ids: list[str], sfx_audio_ids: list[str]) -> dict[str, Any]:
    """Bind existing hook/body effect text and role=sfx audio IDs to a 1–3 color theme. Checks timing and editable-caption exclusivity. Create graphics/sound with overlay/audio tools first. Does not certify the pixels, safe zones or actual audibility; review final output."""
    return commerce.presentation_record(project_id, theme_colors, hook_text_ids, body_text_ids, sfx_audio_ids)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def commerce_typography_add(project_id: str, text: str, emphasis: str, support: str, style: Literal['hook_center','keyword_stack','detail_underline'], start: float, end: float, box: list[float], theme_colors: list[str], reserved_boxes: list[list[float]]) -> dict[str, Any]:
    """Create transparent outlined effect lettering + editable overlay + short animation. text allows at most two explicit lines. Main/support fonts must remain readable (>=7%/3.6% of canvas width); shorten copy, omit redundant support or choose another safe box if it does not fit. box and observed reserved_boxes are [x0,y0,x1,y1]. Same 1–3 #RRGGBB palette per video. hook_center centers on full width in lower safe region; body styles use reviewed free space. Checks UI exclusions; clips our overlapping ordinary captions, never erases burned captions. No filled card, face detection or auto copywriting. Returns overlay ID for commerce_presentation_record. Add role=sfx audio separately at selected emphasis."""
    from . import commerce_typography
    return commerce_typography.add(project_id, text, emphasis, support, style, start, end, box, theme_colors, reserved_boxes)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def commerce_render_submit(project_id: str, preview: bool = False) -> dict[str, Any]:
    """Preflight the saved commerce stages and queue one delivery-quality render. preview=true remains only for an explicit legacy review copy; successful rendering is still unreviewed. Inspect and review this exact output before commerce_delivery_get."""
    commerce.load(project_id)
    return render_submit(project_id, preview)


@mcp.tool(annotations={"readOnlyHint": True})
def commerce_delivery_get(project_id: str, render_job_id: str) -> dict[str, Any]:
    """Delivery gate for source-only workflow: valid current stages AND the exact delivery-quality artifact's complete artifact-bound review. Missing/failed/stale checks never become delivery_ready. Legacy preview artifacts remain review-only."""
    state = commerce.load(project_id)
    errors = commerce.preflight(project_id, allow_pending=(state.get('selection') or {}).get('preview_only', False))
    result = delivery_review.get(project_id, render_job_id)
    if errors: result.update(status='needs_repair', delivery_ready=False, workflow_errors=errors)
    selection = state.get('selection') or {}
    pending = [job for job in selection.get('pictures', []) + ([selection['voice']] if selection.get('voice') else [])
               if state['reviews'].get(job, {}).get('status') == 'pending']
    if pending and not errors:
        result.update(delivery_ready=False, pending_candidate_ids=pending)
        if result['status'] not in ('needs_repair', 'stale'):
            result['status'] = 'needs_review'
    result['next_tool'] = ('commerce_workflow_status' if errors or pending or result['status'] in ('needs_repair', 'stale')
                           else 'delivery_inspection_submit' if result['status'] == 'needs_review' and not result.get('inspection_job_id')
                           else 'delivery_review_form' if result['status'] == 'needs_review' else 'delivery_review_get')
    return result


@mcp.tool(annotations={"readOnlyHint": True})
def delivery_review_get(project_id: str, render_job_id: str) -> dict[str, Any]:
    """Recheck delivery review and evidence hashes. Returns needs_review/needs_repair/stale/ready for the exact artifact; legacy previews may still report preview_reviewed. A render success alone is never delivery_ready."""
    return delivery_review.get(project_id, render_job_id)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def media_segment_preview(project_id: str, asset_id: str, source_start: float, source_end: float, max_edge: int = 640) -> dict[str, Any]:
    """Submit a <=600s source-relative audiovisual viewing proxy. Preserves available source audio; not a timeline cut or semantic review."""
    return media_prepare(project_id,asset_id,'preview',{'start':source_start,'end':source_end,'max_edge':max_edge})


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def media_frames_at(project_id: str, asset_id: str, timestamps: list[float], max_edge: int = 640) -> dict[str, Any]:
    """Submit 1-48 targeted review frames. Result records requested seconds and actual decoded source PTS; frames do not prove speech completeness."""
    return media_prepare(project_id,asset_id,'frames',{'timestamps':timestamps,'max_edge':max_edge})


@mcp.tool(annotations={"readOnlyHint": True})
def media_window_list(project_id: str, asset_id: str, window_seconds: float = 120, overlap_seconds: float = 2, offset: int = 0, limit: int = 12) -> dict[str, Any]:
    """Page a long source into overlapping viewing windows. Directory only; nothing is decoded, classified, or considered watched."""
    return media_ops.windows(project_id,asset_id,window_seconds,overlap_seconds,offset,limit)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def media_storyboard_page(project_id: str, asset_id: str, window_seconds: float = 120, overlap_seconds: float = 2, offset: int = 0, limit: int = 8) -> dict[str, Any]:
    """Submit three thumbnails per window (max16 windows/page). Return window directory plus async frame job; no semantic coverage is inferred."""
    if not 1<=limit<=16: raise ValueError('Storyboard limit must be 1..16')
    page=media_window_list(project_id,asset_id,window_seconds,overlap_seconds,offset,limit)
    timestamps=[]
    for w in page['windows']:
        duration=w['source_end']-w['source_start']
        w['thumbnail_request_times']=[w['source_start']+duration*f for f in (.05,.5,.95)]
        timestamps.extend(w['thumbnail_request_times'])
    return {**page,'job':media_frames_at(project_id,asset_id,timestamps,480) if timestamps else None}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def segment_index_upsert(project_id: str, asset_id: str, annotation: dict[str, Any], segment_id: str | None = None, expected_version: int = 0) -> dict[str, Any]:
    """Save HOST-written understanding, never infer it. Requires source_start/end, summary, observation(audiovisual/audio/visual_frames/silent_video), evidence_refs[{id,version}], tags, quotes[{text,source_start,source_end}], uncertainty. Optional claims,risks,parent_ref,sampling_note (required for frames). Updates append using expected_version."""
    return segments.annotate(project_id,asset_id,annotation,segment_id,expected_version)


@mcp.tool(annotations={"readOnlyHint": True})
def segment_index_search(project_id: str, query: str = '', tags: list[str] | None = None, asset_id: str | None = None, offset: int = 0, limit: int = 20) -> dict[str, Any]:
    """Search only host-written index, using literal Unicode terms/exact tags. Returns explainable matches, not automatic editorial choices."""
    return media_store.search(project_id,'segment',query,tags,asset_id,offset,limit)


@mcp.tool(annotations={"readOnlyHint": True})
def segment_index_get(project_id: str, segment_id: str, version: int | None = None) -> dict[str, Any]:
    """Read latest or immutable historical host observation, including evidence references."""
    return media_store.get(project_id,'segment',segment_id,version)


@mcp.tool(annotations={"readOnlyHint": True})
def media_coverage_get(project_id: str, asset_id: str, offset: int = 0, limit: int = 50) -> dict[str, Any]:
    """Separately page scan-ready, preview-ready, host AV/audio/silent-video reviewed intervals and unreviewed gaps. Sparse frames have no interval coverage."""
    return segments.coverage(project_id,asset_id,offset,limit)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def candidate_add(project_id: str, asset_id: str, candidate: dict[str, Any]) -> dict[str, Any]:
    """Save an explicit clip candidate, NEVER insert it. Requires source_start/end, segment_refs[{id,version}], reason,visual_evidence,tags,quotes,risks,boundary_review{opening,ending,speech,action,note}. Assessments: complete/incomplete/unknown/not_applicable. Optional context,preferred_speed(1/1.25)."""
    return segments.candidate(project_id,asset_id,candidate)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def candidate_update(project_id: str, candidate_id: str, expected_version: int, candidate: dict[str, Any]) -> dict[str, Any]:
    """Append a full replacement candidate version after further review, preserving all old versions."""
    current=media_store.get(project_id,'candidate',candidate_id)
    return segments.candidate(project_id,current['asset_id'],candidate,candidate_id,expected_version)


@mcp.tool(annotations={"readOnlyHint": True})
def candidate_list(project_id: str, query: str = '', tags: list[str] | None = None, asset_id: str | None = None, offset: int = 0, limit: int = 20, include_history: bool = False) -> dict[str, Any]:
    """Page/filter host candidates for comparison; no best-clip selector or automatic speed/trim."""
    return media_store.search(project_id,'candidate',query,tags,asset_id,offset,limit,include_history)


@mcp.tool(annotations={"readOnlyHint": True})
def candidate_get(project_id: str, candidate_id: str, version: int | None = None) -> dict[str, Any]:
    """Retrieve a candidate and its pinned index versions."""
    return media_store.get(project_id,'candidate',candidate_id,version)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def candidate_preview(project_id: str, candidate_id: str, version: int | None = None, context_before: float = 2, context_after: float = 2) -> dict[str, Any]:
    """Submit source audiovisual preview with context around a candidate for sentence/action boundary review. No timeline edits."""
    c=media_store.get(project_id,'candidate',candidate_id,version)
    a=media_ops.asset(project_id,c['asset_id'])
    media_store.number(context_before,'context_before',0,60); media_store.number(context_after,'context_after',0,60)
    start=max(0,c['source_start']-context_before); end=min(a['probe']['duration'],c['source_end']+context_after)
    return {'candidate_id':candidate_id,'version':c['version'],'candidate_range_in_preview':[c['source_start']-start,c['source_end']-start],
            'job':media_segment_preview(project_id,c['asset_id'],start,end)}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def hook_account_put(account_id: str, expected_version: int, profile: dict[str, Any]) -> dict[str, Any]:
    """Version an account's reusable generation identity/style. profile requires display_name, identity_description, identity_mode(authorized_person/synthetic/product_only), voice_policy. Optional asset_group_id, reference_assets[{uri:asset://...,kind,status,verification_reference}], style. expected_version=0 creates. No upload/model calls."""
    return hooks.account_put(account_id, expected_version, profile)


@mcp.tool(annotations={"readOnlyHint": True})
def hook_account_get(account_id: str | None = None, version: int | None = None) -> dict[str, Any]:
    """List reusable hook accounts or read a pinned identity profile. No automatic account selection."""
    return hooks.account_get(account_id, version)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@project_write
def hook_plan_create(project_id: str, account_id: str, account_version: int, plan: dict[str, Any]) -> dict[str, Any]:
    """Pin an agent-authored hook plan and actual source hashes. plan requires product_key,product_description,opening_idea,seam_description,prompt,keep_seconds,source_references[{asset_id,start,end,purpose}]. Save before preparing reference slices. No generation or timeline edits."""
    if commerce.path(project_id).is_file(): raise ValueError('This project is locked to source-only commerce; no generated hook plan')
    project = _read(project_id)
    result = hooks.plan_create(project_id, project, account_id, account_version, plan)
    _write(project_id, project)
    return result


@mcp.tool(annotations={"readOnlyHint": True})
def hook_get(project_id: str, hook_id: str | None = None) -> dict[str, Any]:
    """Read durable hook plan, redacted request, original external task ID and review state; omit ID for summary list."""
    project = _read(project_id)
    if hook_id:
        return hooks.get(project, hook_id)
    return {'items': [{'hook_id': h['hook_id'], 'account_id': h['account']['account_id'],
                       'product_key': h['plan']['product_key'], 'state': h['state']}
                      for h in project.get('mcp_hooks', {}).values()]}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@project_write
def hook_request_prepare(project_id: str, hook_id: str, provider: str, request: dict[str, Any], bindings: list[dict[str, Any]], preview_receipt: str) -> dict[str, Any]:
    """Pin fingerprint of the exact FREE provider-validated request. Every content media index needs a binding: source=account or slice. Slice: source_index,evidence_id,uploaded_sha256; real Seedance slices also provider_asset{uri,status:Active,account_id,asset_group_id,verification_reference}. Must contain actual exported reference_video. Stores redacted request only; no external call."""
    if commerce.path(project_id).is_file(): raise ValueError('Source-only commerce does not prepare generated hooks')
    project = _read(project_id)
    result = hooks.prepare(project_id, hooks.get(project, hook_id), provider, request, bindings, preview_receipt)
    _write(project_id, project)
    return result


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@project_write
def hook_generation_begin(project_id: str, hook_id: str, request_sha256: str, authorization_reference: str) -> dict[str, Any]:
    """Durably claim one externally submitted paid task. Only may_submit=true permits the host's one create call; repeated claims return false, including after failure/unknown/reconnection. Records the existing user authorization; does not request new consent, call a provider, or hold a local heavy slot."""
    if commerce.path(project_id).is_file(): raise ValueError('Source-only commerce does not submit generated hooks')
    project = _read(project_id)
    result = hooks.begin(project_id, hooks.get(project, hook_id), request_sha256, authorization_reference)
    _write(project_id, project)
    return result


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@project_write
def hook_generation_record(project_id: str, hook_id: str, attempt_id: str, state: Literal['unknown','queued','running','succeeded','failed','cancelled','expired'], task_id: str | None = None, receipt: dict[str, Any] | None = None) -> dict[str, Any]:
    """Persist the ORIGINAL provider task ID and observed status; redact returned URLs/secrets. Unknown submission is never a retry trigger. Poll errors retain queued/running state. No network or paid retry occurs."""
    project = _read(project_id)
    result = hooks.record(hooks.get(project, hook_id), attempt_id, state, task_id, receipt or {})
    _write(project_id, project)
    return result


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@project_write
def hook_output_review(project_id: str, hook_id: str, asset_id: str, source_start: float, source_end: float, checks: dict[str, str], note: str, evidence_refs: list[str]) -> dict[str, Any]:
    """Record host visual/AV review after successful queued import. checks: person,garment,motion,seam,claims,audio=pass/fail/not_applicable; concrete evidence_refs required. Pins reviewed bytes and retained range. This tool does NOT recognize identity or judge the video itself."""
    project = _read(project_id)
    result = hooks.review(project_id, hooks.get(project, hook_id), asset_id, source_start, source_end, checks, note, evidence_refs)
    _write(project_id, project)
    return result


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@project_write
def hook_attach(project_id: str, hook_id: str, expected_revision: int, audio_policy: Literal['silent','inherit'], playback_speed: Literal[1.0,1.25] = 1.0) -> dict[str, Any]:
    """Atomically prepend the accepted generated hook and ripple ALL captions, graphics, keyframes, point cards, audio events and source-bound events. Hard cut at seam, shared 25fps clock. CAS revision + undo. Silent requires audio-free hook or globally muted source; inherit keeps existing source volume. Convert legacy continuous bed to timed audio first. No render starts here."""
    if commerce.path(project_id).is_file(): raise ValueError('Pure source workflow uses commerce_timeline_apply, not generated hook_attach')
    project = _read(project_id)
    hook = hooks.get(project, hook_id)
    timeline = _timeline(project)
    if any(c.get('hook_id') == hook_id for c in timeline['tracks']['main']):
        return {'already_attached': True, 'project_id': project_id, 'revision': timeline['revision'], 'hook_id': hook_id}
    if timeline['revision'] != expected_revision:
        raise ValueError(f"Timeline revision conflict: current={timeline['revision']}")
    hooks.check_sources(project_id, hook)
    if not hook['reviews'] or not hook['reviews'][-1]['accepted']:
        raise ValueError('Review and accept the actual generated output before attaching it')
    review = hook['reviews'][-1]
    asset = media_ops.asset(project_id, review['asset_id'])
    if asset['identity']['sha256'] != review['source_sha256']:
        raise ValueError('Reviewed output bytes changed')
    if audio_policy == 'silent' and asset['probe'].get('has_audio') and timeline.get('audio_config', {}).get('source_volume', 1) != 0:
        raise ValueError('Silent hook requested but imported hook contains audio; queue a silent derivative or explicitly choose inherit')
    if audio_policy not in ('silent', 'inherit'):
        raise ValueError('Explicit hook audio policy is required')
    undo = _snapshot(project, 'before hook_attach')
    updated, clip, shift = hooks.prepend(timeline, review['asset_id'], review['source_start'], review['source_end'], hook_id, playback_speed)
    project['mcp_timeline'] = updated
    errors = _validate(project)
    if errors:
        raise ValueError('Invalid hook attachment: ' + '; '.join(errors))
    detail = {'hook_id': hook_id, 'clip_id': clip['id'], 'review_id': review['review_id'],
              'shift_seconds': shift, 'audio_policy': audio_policy, 'playback_speed': playback_speed}
    hook['attachment'] = detail
    return {**_commit(project_id, project, 'hook_attach', detail, undo), **detail}


@mcp.tool(annotations={"readOnlyHint": True})
def timeline_get(project_id: str) -> dict[str, Any]:
    """Read the complete versioned timeline and its edit history."""
    project = _read(project_id)
    return {"project_id": project_id, "timeline": timing.resolve(_timeline(project)), "time_map": timing.time_map(_timeline(project))}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@project_write
def timeline_snapshot(project_id: str, label: str) -> dict[str, Any]:
    """Save a named immutable snapshot without changing the live timeline."""
    project = _read(project_id)
    snapshot_id = _snapshot(project, label)
    _write(project_id, project)
    return {"project_id": project_id, "snapshot_id": snapshot_id, "revision": _timeline(project).get("revision", 0), "label": label}


@mcp.tool(annotations={"readOnlyHint": True})
def timeline_diff(project_id: str, snapshot_id: str) -> dict[str, Any]:
    """Compare all tracks, properties, keyframes, order and settings with a snapshot."""
    project = _read(project_id)
    snapshot = project.get("mcp_snapshots", {}).get(snapshot_id)
    if not isinstance(snapshot, dict):
        raise ValueError(f"Unknown snapshot_id: {snapshot_id}")
    current = _timeline(project)
    before = snapshot["timeline"]
    return {"project_id": project_id, "snapshot_id": snapshot_id, "from_revision": before.get("revision", 0), "to_revision": current.get("revision", 0), **timeline_changes(before, current)}


@mcp.tool(annotations={"readOnlyHint": True})
def timeline_validate(project_id: str, check_audio: bool = True, rms_delta_db: float = 8.0, discontinuity_threshold: float = 0.1) -> dict[str, Any]:
    """Validate structure, source reuse, frame clock and (by default) render/measure the planned audio only. No video rendering or timeline mutation. Audio checks cost local CPU; set check_audio=false for structural checks only."""
    project = _read(project_id)
    errors = _validate(project)
    try:
        timeline = timing.resolve(_timeline(project))
        details = preflight.inspect(project, timeline)
    except (KeyError, TypeError, ValueError):
        details = {'issues': [], 'warnings': []}
    audio_report = {'checked': False, 'reason': 'structural errors' if errors else 'disabled by caller'}
    if check_audio and not errors:
        try:
            with resources.heavy_slot(), tempfile.TemporaryDirectory(prefix='video-editer-audio-check-') as temporary:
                path = Path(temporary) / 'prediction.wav'
                settings = audio_engine.render(project, timeline, path)
                measured = audio_engine.measure(path)
                issues = audio_engine.join_checks(path, details['time_map'], rms_delta_db, discontinuity_threshold)
                issues += [{'code': 'audio_output_headroom', 'severity': 'warning', 'event_ids': [], 'message': w} for w in measured['warnings']]
                details['issues'].extend(issues)
                details['warnings'].extend(issues)
                audio_report = {'checked': True, 'predicted_output': measured, 'settings': settings,
                                'scope': 'Measured rendered float audio, including all tracks and master settings; final AAC true peak can differ.'}
        except Exception as exc:
            errors.append(f'Audio validation failed: {exc}')
            audio_report = {'checked': False, 'reason': 'audio analysis failed'}
    return {**details, 'project_id': project_id, 'valid': not errors, 'errors': errors, 'audio': audio_report,
            'revision': _timeline(project).get('revision', 0), 'scope': 'valid means executable; review warnings separately.'}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@project_write
def track_create(project_id: str, track_id: str, kind: Literal["overlay", "caption", "audio"]) -> dict[str, Any]:
    """Create a named non-main track. Track creation never places or generates content."""
    if not track_id.replace("_", "").replace("-", "").isalnum() or track_id == "main":
        raise ValueError("track_id must be a non-main alphanumeric identifier")
    project = _read(project_id)
    timeline = _timeline(project)
    if track_id in timeline["tracks"]:
        raise ValueError(f"Track already exists: {track_id}")
    undo = _snapshot(project, "before track_create")
    timeline["tracks"][track_id] = []
    timeline["track_meta"][track_id] = {"kind": kind, "locked": False}
    return _commit(project_id, project, "track_create", {"track_id": track_id, "kind": kind}, undo)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@project_write
def clip_add(project_id: str, asset_id: str, start: float, end: float, role: str = "detail", playback_speed: Literal[1.0, 1.25] = 1.0, transition: str = "punch_cut", shot_type: str = "other", audio_gain_db: float = 0.0, audio_fade_in_ms: float = 25.0, audio_fade_out_ms: float = 25.0, audio_mute: bool = False, frame_alignment: Literal["cover", "nearest", "floor"] = "cover") -> dict[str, Any]:
    """Append video/image to main track. Video start/end select source seconds; image start/end select a virtual still timeline (0..3600s), so playback duration=(end-start)/speed. No automatic image motion; optionally use clip_camera_set."""
    project = _read(project_id)
    asset = _asset(project, asset_id)
    if asset.get("kind") not in {"video", "image"}:
        raise ValueError("Only video/image assets can be placed on the main track")
    limit=3600 if asset['kind']=='image' else _duration(asset)+.05
    if not finite(start,end) or not 0 <= start < end <= limit:
        raise ValueError("Clip range is outside the source duration")
    if transition not in TRANSITIONS:
        raise ValueError(f"Unsupported transition: {transition}")
    if playback_speed not in SPEEDS:
        raise ValueError("Unsupported speed")
    undo = _snapshot(project, "before clip_add")
    clip = {"id": f"clip_{uuid.uuid4().hex[:10]}", "asset_id": asset_id, "start": start, "end": end, "role": role, "playback_speed": playback_speed, "transition": transition, "shot_type": shot_type, "audio_gain_db": audio_gain_db, "audio_fade_in_ms": audio_fade_in_ms, "audio_fade_out_ms": audio_fade_out_ms, "audio_mute": audio_mute, "frame_alignment": frame_alignment}
    audio_engine.clip_settings(clip)
    _clip_labels(clip)
    engine.render_duration(clip)
    _timeline(project)["tracks"]["main"].append(clip)
    result = _commit(project_id, project, "clip_add", {"clip_id": clip["id"]}, undo)
    return {**result, "clip": clip}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@project_write
def clip_update(project_id: str, clip_id: str, start: float | None = None, end: float | None = None, playback_speed: Literal[1.0, 1.25] | None = None, transition: str | None = None, role: str | None = None, shot_type: str | None = None, audio_gain_db: float | None = None, audio_fade_in_ms: float | None = None, audio_fade_out_ms: float | None = None, audio_mute: bool | None = None, frame_alignment: Literal["cover", "nearest", "floor"] | None = None) -> dict[str, Any]:
    """Update explicit clip properties without altering any other timeline entry."""
    project = _read(project_id)
    clips = _timeline(project)["tracks"]["main"]
    clip = next((item for item in clips if item.get("id") == clip_id), None)
    if not clip:
        raise ValueError(f"Unknown clip_id: {clip_id}")
    undo = _snapshot(project, "before clip_update")
    for key, value in {"start": start, "end": end, "playback_speed": playback_speed, "transition": transition, "role": role, "shot_type": shot_type, "audio_gain_db": audio_gain_db, "audio_fade_in_ms": audio_fade_in_ms, "audio_fade_out_ms": audio_fade_out_ms, "audio_mute": audio_mute, "frame_alignment": frame_alignment}.items():
        if value is not None:
            clip[key] = value
    errors = _validate(project)
    if errors:
        raise ValueError("Invalid update: " + "; ".join(errors))
    result = _commit(project_id, project, "clip_update", {"clip_id": clip_id}, undo)
    return {**result, "clip": clip}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@project_write
def clip_insert_at(project_id: str, index: int, asset_id: str, start: float, end: float, role: str = "detail", playback_speed: Literal[1.0, 1.25] = 1.0, transition: str = "none", shot_type: str = "other", audio_gain_db: float = 0.0, audio_fade_in_ms: float = 25.0, audio_fade_out_ms: float = 25.0, audio_mute: bool = False, frame_alignment: Literal["cover", "nearest", "floor"] = "cover") -> dict[str, Any]:
    """Insert at a zero-based main-track index (0..count). Other timed tracks stay at their absolute times; no ripple edit is inferred."""
    project = _read(project_id)
    clips = _timeline(project)["tracks"]["main"]
    if not 0 <= index <= len(clips):
        raise ValueError("index must be between 0 and clip count")
    if _asset(project, asset_id).get("kind") not in {"video", "image"}:
        raise ValueError("Main track requires a video/image asset")
    undo = _snapshot(project, "before clip_insert_at")
    clip = {"id": f"clip_{uuid.uuid4().hex[:10]}", "asset_id": asset_id, "start": start, "end": end,
            "role": role, "playback_speed": playback_speed, "transition": transition, "shot_type": shot_type, "audio_gain_db": audio_gain_db, "audio_fade_in_ms": audio_fade_in_ms, "audio_fade_out_ms": audio_fade_out_ms, "audio_mute": audio_mute, "frame_alignment": frame_alignment}
    clips.insert(index, clip)
    errors = _validate(project)
    if errors:
        raise ValueError("Invalid insert: " + "; ".join(errors))
    return {**_commit(project_id, project, "clip_insert_at", {"clip_id": clip["id"], "index": index}, undo), "clip": clip,
            "timing_policy": "Other tracks keep absolute timing; review synchronization after insertion."}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@project_write
def clip_split(project_id: str, clip_id: str, at_seconds: float) -> dict[str, Any]:
    """Split at playback seconds relative to this clip's start (not source/global time). Preserve total duration/speed; keep outgoing transition on the right half; add no transition between halves."""
    project = _read(project_id)
    clips = _timeline(project)["tracks"]["main"]
    index = next((i for i, clip in enumerate(clips) if clip.get("id") == clip_id), None)
    if index is None:
        raise ValueError(f"Unknown clip_id: {clip_id}")
    left = clips[index]
    if left.get('camera'):
        raise ValueError('Detach camera motion before splitting; explicitly set motion for the resulting clips')
    speed = float(left.get("playback_speed", 1))
    split_source = float(left["start"]) + at_seconds * speed
    if not finite(at_seconds) or not float(left["start"]) < split_source < float(left["end"]):
        raise ValueError("Split must lie strictly inside the clip's playback duration")
    undo = _snapshot(project, "before clip_split")
    right = copy.deepcopy(left)
    right.update(id=f"clip_{uuid.uuid4().hex[:10]}", start=split_source)
    left.update(end=split_source, transition="none", audio_fade_out_ms=0)
    right["audio_fade_in_ms"] = 0
    clips.insert(index + 1, right)
    timing.rebind_split(_timeline(project), clip_id, right["id"], split_source)
    return {**_commit(project_id, project, "clip_split", {"clip_id": clip_id, "right_clip_id": right["id"], "at_seconds": at_seconds}, undo), "left": left, "right": right}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
@project_write
def clip_remove(project_id: str, clip_id: str) -> dict[str, Any]:
    """Remove exactly one main-track clip. Use the returned undo snapshot to restore it."""
    project = _read(project_id)
    undo = _snapshot(project, "before clip_remove")
    clips = _timeline(project)["tracks"]["main"]
    remaining = [item for item in clips if item.get("id") != clip_id]
    if len(remaining) == len(clips):
        raise ValueError(f"Unknown clip_id: {clip_id}")
    _timeline(project)["tracks"]["main"] = remaining
    return _commit(project_id, project, "clip_remove", {"clip_id": clip_id}, undo)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@project_write
def clip_reorder(project_id: str, clip_ids: list[str]) -> dict[str, Any]:
    """Set the main-track order explicitly. Provide every existing clip ID exactly once."""
    project = _read(project_id)
    clips = _timeline(project)["tracks"]["main"]
    existing = [str(item.get("id")) for item in clips]
    if len(clip_ids) != len(existing) or set(clip_ids) != set(existing):
        raise ValueError("clip_ids must include every existing main-track clip exactly once")
    undo = _snapshot(project, "before clip_reorder")
    lookup = {str(item["id"]): item for item in clips}
    _timeline(project)["tracks"]["main"] = [lookup[item] for item in clip_ids]
    return _commit(project_id, project, "clip_reorder", {"clip_ids": clip_ids}, undo)


@mcp.tool(annotations={"readOnlyHint": True})
def transition_list(season: Literal["any", "spring", "summer", "winter"] = "any") -> dict[str, Any]:
    """List real, executable transitions and their visual intent. This tool never chooses one."""
    seasonal = []
    for name, (entry_season, effect, intent) in engine.SEASONAL_TRANSITIONS.items():
        if season == "any" or entry_season == {"spring": "春", "summer": "夏", "winter": "冬"}[season]:
            seasonal.append({"id": name, "season": entry_season, "renderer": effect, "intent": intent})
    procedural=[{'id':name, 'renderer':'per_frame_masked_reveal', **meta} for name,meta in visuals.TRANSITIONS.items()
                if season=='any' or meta['season'] in {season,'any'}]
    return {"generic": ["punch_cut", "zoom_punch", "paint_flash", "slide_push", "whip_pan", "detail_smash_cut", "none"], "seasonal": seasonal, "procedural": procedural,
            "overlap_seconds": {name: engine.transition_overlap_seconds(name) for name in sorted(TRANSITIONS)}, "clock_fps": 25}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@project_write
def transition_apply(project_id: str, clip_id: str, transition: str) -> dict[str, Any]:
    """Apply an explicitly selected transition to a clip's outgoing edge."""
    return clip_update(project_id, clip_id, transition=transition)


@mcp.tool(annotations={"readOnlyHint": True})
def callout_list_templates() -> dict[str, Any]:
    """List supported editable callout templates, entrances, colors and position rules."""
    return {"individual_points": {"tool": "callout_points_add", "count": [1, 4], "timing": "Explicit start/end for every point; no derived delay"}, "templates": sorted(engine.CALLOUT_TEMPLATES), "procedural": [{'id':name,**meta,'position':'scene-fixed side label at 60% height','recommended_seconds':[.6,2.5]} for name,meta in visuals.CALLOUTS.items()], "entrances": sorted(engine.CALLOUT_ENTRANCES), "color_themes": sorted(engine.CALLOUT_COLOR_THEMES), "positions": sorted(POSITIONS), "point_list_rule": "text must be 'point one|point two' with two non-empty parts"}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@project_write
def callout_add(project_id: str, text: str, start: float, end: float, position: Literal["product_left", "product_right"], template: str = "recommend_arrow", entrance: Literal["pop", "slide_up", "fade", "none"] = "pop", color_theme: Literal["sunshine", "coral", "mint", "electric_blue", "cream"] = "sunshine") -> dict[str, Any]:
    """Add a timed product callout. Text is supplied by the calling agent and is never generated locally."""
    project = _read(project_id)
    if template not in engine.CALLOUT_TEMPLATES:
        raise ValueError(f"Unsupported template: {template}")
    _check_callout_text(text, template)
    if template == "point_list":
        parts = [part.strip() for part in text.split("|")]
        if len(parts) != 2 or not all(parts):
            raise ValueError("point_list needs exactly two non-empty points separated by |")
        if end - start < 1.1 - 1e-9:
            raise ValueError("point_list requires at least 1.1 seconds; use separate overlays for other timing")
    if not text.strip() or not finite(start, end) or not 0 <= start < end:
        raise ValueError("Callout text and positive timing are required")
    undo = _snapshot(project, "before callout_add")
    event = {"id": f"callout_{uuid.uuid4().hex[:10]}", "text": text.strip(), "start": start, "end": end, "position": position, "anchor": "product", "template": template, "entrance": entrance, "color_theme": color_theme}
    _timeline(project)["callouts"].append(event)
    result = _commit(project_id, project, "callout_add", {"callout_id": event["id"]}, undo)
    return {**result, "callout": event}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
@project_write
def callout_remove(project_id: str, callout_id: str) -> dict[str, Any]:
    """Remove one timed callout; the original timeline is recoverable using undo_snapshot."""
    project = _read(project_id)
    undo = _snapshot(project, "before callout_remove")
    events = _timeline(project)["callouts"]
    remaining = [item for item in events if item.get("id") != callout_id]
    if len(remaining) == len(events):
        raise ValueError(f"Unknown callout_id: {callout_id}")
    _timeline(project)["callouts"] = remaining
    return _commit(project_id, project, "callout_remove", {"callout_id": callout_id}, undo)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@project_write
def callout_update(project_id: str, callout_id: str, text: str | None = None, start: float | None = None, end: float | None = None, position: Literal["product_left", "product_right"] | None = None, template: str | None = None, entrance: Literal["pop", "slide_up", "fade", "none"] | None = None, color_theme: Literal["sunshine", "coral", "mint", "electric_blue", "cream"] | None = None, points: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Update one callout without changing unrelated graphics or timing."""
    project = _read(project_id)
    event = next((item for item in _timeline(project)["callouts"] if item.get("id") == callout_id), None)
    if not event:
        raise ValueError(f"Unknown callout_id: {callout_id}")
    if event.get("time_binding") and (start is not None or end is not None or points is not None):
        raise ValueError("Detach the time binding before changing event timing/points")
    if "points" in event and text is not None and points is None:
        raise ValueError("Use points to update individual point texts/times")
    if "points" in event and points is None and (start is not None or end is not None):
        raise ValueError("Use points to change individual reveal/exit times")
    if ("points" in event or points is not None) and (entrance is not None or color_theme is not None):
        raise ValueError("Individually timed point cards currently use a fixed card appearance/entrance")
    undo = _snapshot(project, "before callout_update")
    for key, value in {"text": text, "start": start, "end": end, "position": position, "template": template, "entrance": entrance, "color_theme": color_theme}.items():
        if value is not None:
            event[key] = value
    if points is not None:
        timing.check_points(points)
        event.update(points=copy.deepcopy(points), text="|".join(p["text"] for p in points),
                     start=min(p["start"] for p in points), end=max(p["end"] for p in points), template="point_list")
    if "points" in event and event["template"] != "point_list":
        raise ValueError("Individually timed points must use point_list")
    errors = _validate(project)
    if errors:
        raise ValueError("Invalid update: " + "; ".join(errors))
    result = _commit(project_id, project, "callout_update", {"callout_id": callout_id}, undo)
    return {**result, "callout": event}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@project_write
def audio_set_bed(project_id: str, asset_id: str, start_seconds: float = 0.0) -> dict[str, Any]:
    """Set an explicit video source as continuous dialogue/audio bed; no audio is synthesized."""
    project = _read(project_id)
    asset = _asset(project, asset_id)
    if asset.get("kind") != "video":
        raise ValueError("Audio bed must currently be sourced from a video asset")
    if not finite(start_seconds) or not 0 <= start_seconds < _duration(asset):
        raise ValueError("Audio bed offset must be inside its source")
    if not engine.ffprobe(Path(asset["path"])).get("has_audio"):
        raise ValueError("Audio bed source has no audio stream")
    undo = _snapshot(project, "before audio_set_bed")
    _timeline(project)["audio"] = {"bed_asset_id": asset_id, "start_seconds": start_seconds}
    return _commit(project_id, project, "audio_set_bed", {"asset_id": asset_id}, undo)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@project_write
def overlay_add(project_id: str, asset_id: str, start: float, end: float, x: float, y: float, width: float, height: float, opacity: float = 1.0, track_id: str = "overlays") -> dict[str, Any]:
    """Place an image/video with normalized top-left x/y and exact width/height (stretch). The entire rectangle must fit. Visible in [start,end)."""
    project = _read(project_id)
    timeline = _timeline(project)
    asset = _asset(project, asset_id)
    if asset.get("kind") not in {"image", "video"}:
        raise ValueError("Overlay asset must be an image or video")
    if timeline.get("track_meta", {}).get(track_id, {}).get("kind") != "overlay":
        raise ValueError("track_id must refer to an overlay track")
    undo = _snapshot(project, "before overlay_add")
    item = {"id": f"overlay_{uuid.uuid4().hex[:10]}", "asset_id": asset_id, "start": start, "end": end, "x": x, "y": y, "width": width, "height": height, "opacity": opacity, "keyframes": []}
    c=canvas.settings(timeline.get('canvas'))
    check_overlay(item,(c['width'],c['height']))
    timeline["tracks"][track_id].append(item)
    result = _commit(project_id, project, "overlay_add", {"overlay_id": item["id"], "track_id": track_id}, undo)
    return {**result, "overlay": item}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@project_write
def overlay_keyframe(project_id: str, overlay_id: str, at: float, x: float, y: float, track_id: str = "overlays") -> dict[str, Any]:
    """Set x/y at absolute timeline seconds. Linearly interpolate every key; hold after last. Base position anchors start unless replaced by a key at start."""
    project = _read(project_id)
    track = _timeline(project)["tracks"].get(track_id, [])
    item = next((entry for entry in track if entry.get("id") == overlay_id), None)
    if not item:
        raise ValueError(f"Unknown overlay_id: {overlay_id}")
    if item.get("time_binding"):
        raise ValueError("Detach the time binding before editing keyframe timestamps")
    if not (float(item["start"]) <= at <= float(item["end"]) and 0 <= x <= 1 and 0 <= y <= 1):
        raise ValueError("Keyframe must be within overlay time and normalized canvas bounds")
    undo = _snapshot(project, "before overlay_keyframe")
    at = round(at, 3)
    keys = [entry for entry in item.get("keyframes", []) if float(entry.get("at", -1)) != at]
    keys.append({"at": at, "x": x, "y": y})
    item["keyframes"] = sorted(keys, key=lambda entry: float(entry["at"]))
    c=canvas.settings(_timeline(project).get('canvas'))
    check_overlay(item,(c['width'],c['height']))
    result = _commit(project_id, project, "overlay_keyframe", {"overlay_id": overlay_id}, undo)
    return {**result, "overlay": item}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
@project_write
def overlay_remove(project_id: str, overlay_id: str, track_id: str = "overlays") -> dict[str, Any]:
    """Remove one overlay from a track without touching any other graphics."""
    project = _read(project_id)
    timeline = _timeline(project)
    rows = timeline["tracks"].get(track_id, [])
    remaining = [entry for entry in rows if entry.get("id") != overlay_id]
    if len(rows) == len(remaining):
        raise ValueError(f"Unknown overlay_id: {overlay_id}")
    undo = _snapshot(project, "before overlay_remove")
    timeline["tracks"][track_id] = remaining
    return _commit(project_id, project, "overlay_remove", {"overlay_id": overlay_id}, undo)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@project_write
def subtitle_add(project_id: str, text: str, start: float, end: float, track_id: str = "captions") -> dict[str, Any]:
    """Add one timed subtitle; timing and text are supplied by the calling agent."""
    project = _read(project_id)
    timeline = _timeline(project)
    if timeline.get("track_meta", {}).get(track_id, {}).get("kind") != "caption":
        raise ValueError("track_id must refer to a caption track")
    if not text.strip() or not finite(start, end) or not 0 <= start < end:
        raise ValueError("Subtitle requires text and positive duration")
    undo = _snapshot(project, "before subtitle_add")
    item = {"id": f"subtitle_{uuid.uuid4().hex[:10]}", "text": text.strip(), "start": start, "end": end}
    timeline["tracks"][track_id].append(item)
    result = _commit(project_id, project, "subtitle_add", {"subtitle_id": item["id"], "track_id": track_id}, undo)
    return {**result, "subtitle": item}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
@project_write
def subtitle_remove(project_id: str, subtitle_id: str, track_id: str = "captions") -> dict[str, Any]:
    """Remove one timed subtitle from the requested caption track."""
    project = _read(project_id)
    timeline = _timeline(project)
    rows = timeline["tracks"].get(track_id, [])
    remaining = [entry for entry in rows if entry.get("id") != subtitle_id]
    if len(rows) == len(remaining):
        raise ValueError(f"Unknown subtitle_id: {subtitle_id}")
    undo = _snapshot(project, "before subtitle_remove")
    timeline["tracks"][track_id] = remaining
    return _commit(project_id, project, "subtitle_remove", {"subtitle_id": subtitle_id}, undo)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@project_write
def subtitle_update(project_id: str, subtitle_id: str, text: str | None = None, start: float | None = None, end: float | None = None, track_id: str = "captions") -> dict[str, Any]:
    """Update one subtitle's text or timing without regenerating the subtitle track."""
    project = _read(project_id)
    rows = _timeline(project)["tracks"].get(track_id, [])
    item = next((entry for entry in rows if entry.get("id") == subtitle_id), None)
    if not item:
        raise ValueError(f"Unknown subtitle_id: {subtitle_id}")
    if item.get("time_binding") and (start is not None or end is not None):
        raise ValueError("Detach the time binding before changing subtitle timing")
    undo = _snapshot(project, "before subtitle_update")
    for key, value in {"text": text, "start": start, "end": end}.items():
        if value is not None:
            item[key] = value
    errors = _validate(project)
    if errors:
        raise ValueError("Invalid subtitle update: " + "; ".join(errors))
    result = _commit(project_id, project, "subtitle_update", {"subtitle_id": subtitle_id}, undo)
    return {**result, "subtitle": item}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@project_write
def audio_track_add(project_id: str, asset_id: str, start: float = 0.0, duration: float = 0.0, volume: float = 1.0, fade_in: float = 0.0, fade_out: float = 0.0, track_id: str = "audio", source_start: float = 0.0, playback_speed: Literal[1.0,1.25] = 1.0, role: Literal['unspecified','sfx','voiceover','music'] = 'unspecified', label: str = '') -> dict[str, Any]:
    """Add explicit audio with gain/fades and pitch-preserving tempo. duration/fades use output seconds; source_start uses source seconds. No music is generated."""
    project = _read(project_id)
    timeline = _timeline(project)
    asset = _asset(project, asset_id)
    if asset.get("kind") not in {"audio", "video"}:
        raise ValueError("Audio track must reference an audio or video asset")
    if timeline.get("track_meta", {}).get(track_id, {}).get("kind") != "audio":
        raise ValueError("track_id must refer to an audio track")
    item = {"id": f"audio_{uuid.uuid4().hex[:10]}", "asset_id": asset_id, "start": start, "duration": duration, "volume": volume, "fade_in": fade_in, "fade_out": fade_out, "source_start": source_start, "playback_speed": playback_speed, "role": role, "label": label}
    _check_audio_entry(item, asset)
    item.update(start=round(start, 3), duration=round(duration, 3))
    undo = _snapshot(project, "before audio_track_add")
    timeline["tracks"][track_id].append(item)
    result = _commit(project_id, project, "audio_track_add", {"audio_id": item["id"], "track_id": track_id}, undo)
    return {**result, "audio": item}


def _check_audio_entry(item, asset):
    values = [item.get(k, default) for k, default in (
        ('start', 0), ('duration', 0), ('volume', 1), ('fade_in', 0), ('fade_out', 0), ('source_start', 0))]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in values) or not finite(*values) or min(values) < 0:
        raise ValueError('Audio timing and gain must be finite non-negative numbers')
    speed = item.get('playback_speed', 1.0)
    if isinstance(speed, bool) or speed not in SPEEDS:
        raise ValueError('Audio playback_speed must be 1.0 or 1.25')
    if item.get('role', 'unspecified') not in ('unspecified', 'sfx', 'voiceover', 'music'):
        raise ValueError('Unknown audio role')
    if not isinstance(item.get('label', ''), str) or len(item.get('label', '')) > 160:
        raise ValueError('Audio label must be text, at most 160 characters')
    source_start, duration = item.get('source_start', 0), item.get('duration', 0)
    available = _duration(asset)
    if source_start >= available or (duration > 0 and source_start + duration * speed > available + .05):
        raise ValueError('Audio selection exceeds source bounds')


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@project_write
def audio_track_update(project_id: str, audio_id: str, start: float | None = None, duration: float | None = None, volume: float | None = None, fade_in: float | None = None, fade_out: float | None = None, source_start: float | None = None, playback_speed: Literal[1.0,1.25] | None = None, role: Literal['unspecified','sfx','voiceover','music'] | None = None, label: str | None = None) -> dict[str, Any]:
    """Update an audio entry in place, preserving its ID, metadata and binding with undo. Detach a time binding before changing start/duration; volume/fades/labels can change while bound. No automatic mixing decisions."""
    project = _read(project_id)
    timeline = _timeline(project)
    kind, item = timing.locate(timeline, audio_id)
    if kind != 'audio':
        raise ValueError('audio_id must refer to an audio entry')
    if item.get('time_binding') and (start is not None or duration is not None):
        raise ValueError('Detach time binding before changing audio timestamps')
    undo = _snapshot(project, 'before audio_track_update')
    for key, value in {'start': start, 'duration': duration, 'volume': volume, 'fade_in': fade_in,
                       'fade_out': fade_out, 'source_start': source_start, 'playback_speed': playback_speed,
                       'role': role, 'label': label}.items():
        if value is not None:
            item[key] = value
    resolved = timing.locate(timing.resolve(timeline), audio_id)[1]
    _check_audio_entry(resolved, _asset(project, item['asset_id']))
    result = _commit(project_id, project, 'audio_track_update', {'audio_id': audio_id}, undo)
    return {**result, 'audio': resolved}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@project_write
def audio_duck_source(project_id: str, volume: float = 0.35) -> dict[str, Any]:
    """Set constant source-audio gain, also without extra tracks. This is not automatic speech-aware ducking."""
    if not 0 <= volume <= 1:
        raise ValueError("volume must be between 0 and 1")
    project = _read(project_id)
    undo = _snapshot(project, "before audio_duck_source")
    _timeline(project).setdefault("audio_config", {})["source_volume"] = volume
    return _commit(project_id, project, "audio_duck_source", {"source_volume": volume}, undo)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
@project_write
def audio_track_remove(project_id: str, audio_id: str, track_id: str = "audio") -> dict[str, Any]:
    """Remove one music/effect track entry; original source audio remains unchanged."""
    project = _read(project_id)
    timeline = _timeline(project)
    rows = timeline["tracks"].get(track_id, [])
    remaining = [entry for entry in rows if entry.get("id") != audio_id]
    if len(rows) == len(remaining):
        raise ValueError(f"Unknown audio_id: {audio_id}")
    undo = _snapshot(project, "before audio_track_remove")
    timeline["tracks"][track_id] = remaining
    return _commit(project_id, project, "audio_track_remove", {"audio_id": audio_id}, undo)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@project_write
def timeline_restore(project_id: str, undo_snapshot: str) -> dict[str, Any]:
    """Restore a prior timeline snapshot created by any mutating tool."""
    project = _read(project_id)
    snapshot = project.get("mcp_snapshots", {}).get(undo_snapshot)
    if not isinstance(snapshot, dict):
        raise ValueError(f"Unknown undo_snapshot: {undo_snapshot}")
    prior = _snapshot(project, "before timeline_restore")
    revision = _timeline(project).get("revision", 0)
    history = copy.deepcopy(_timeline(project)["history"])
    project["mcp_timeline"] = copy.deepcopy(snapshot["timeline"])
    project["mcp_timeline"].update(revision=revision, history=history)
    return _commit(project_id, project, "timeline_restore", {"restored": undo_snapshot}, prior)


@resources.heavy_operation
def _render(project_id: str, preview: bool, retry_job: str | None = None) -> dict[str, Any]:
    if retry_job:
        snapshot, prior = jobs.retry_input(project_id, retry_job)
        project, preview = snapshot["project"], snapshot["preview"]
    else:
        with file_lock(_folder(project_id) / "writer.lock"):
            project = _read(project_id)
        prior = None
    commerce.render_guard(project_id, project, preview=preview)
    errors = _validate(project)
    if errors:
        raise ValueError("Timeline validation failed: " + "; ".join(errors))
    project = copy.deepcopy(project)
    project["mcp_timeline"] = timing.resolve(_timeline(project))
    timeline = project["mcp_timeline"]
    preflight.inspect(project, timeline)
    attempt = jobs.Attempt(project_id, project, preview, prior)
    return _execute_attempt(attempt)


@resources.heavy_operation
def _execute_attempt(attempt):
    project,preview=attempt.project,attempt.preview
    project_id=project['id']
    timeline=project['mcp_timeline']
    diagnostics=preflight.inspect(project,timeline)
    try:
        with attempt.running(), processes.cancellation(attempt.root/'cancel.json'):
            commerce.render_guard(project_id, project, preview=preview)
            attempt.bind_media()
            def dependencies(value):
                used = set()
                def walk(item):
                    if isinstance(item, dict):
                        used.update(v for k, v in item.items() if k in ('asset_id', 'bed_asset_id') and v)
                        for v in item.values(): walk(v)
                    elif isinstance(item, list):
                        for v in item: walk(v)
                walk(value)
                return {'settings': value, 'media': {i: attempt.manifest[i] for i in sorted(used)}}
            plan = _plan(project)
            def base():
                value = engine.render_project(project_id, plan, render_job=attempt.job_id,
                                              project_snapshot=project, job_prepared=True)
                return Path(value["path"]), value
            current, result = attempt.stage("base", base, dependencies(plan))
            result = copy.deepcopy(result)
            graphics = [{**item, "path": _asset(project, item["asset_id"])["path"],
                         "kind": _asset(project, item["asset_id"])["kind"]}
                        for item in _entries_by_kind(timeline, "overlay")]
            current, _ = attempt.stage("graphics", lambda: (_apply_graphics(current, attempt.root / "mcp-graphics.mp4", graphics), {}),
                                       {'base': attempt.state['stages']['base']['sha256'], **dependencies(graphics)})
            captions = _entries_by_kind(timeline, "caption")
            current, _ = attempt.stage("captions", lambda: (_apply_captions(current, attempt.root / "mcp-captions.mp4", captions), {}),
                                       {'graphics': attempt.state['stages']['graphics']['sha256'], 'captions': captions})
            def soundtrack():
                path = attempt.root / 'soundtrack.wav'
                return path, audio_engine.render(project, timeline, path)
            sound, audio_settings = attempt.stage('audio', soundtrack, dependencies({
                'main': timeline['tracks']['main'], 'audio': _entries_by_kind(timeline, 'audio'),
                'bed': timeline.get('audio', {}), 'config': timeline.get('audio_config', {})}))
            output = attempt.root / ('preview.mp4' if preview else 'delivery.mp4')
            command = [engine.ffmpeg_bin(), '-nostdin', '-y', '-i', str(current), '-i', str(sound),
                       '-map', '0:v:0', '-map', '1:a:0']
            if preview:
                pw, ph = canvas.preview_size(timeline.get('canvas'))
                command += ['-vf', f'scale={pw}:{ph},setsar=1', '-r', '25', '-c:v', 'libx264']
            else:
                command += ['-c:v', 'copy']
            command += ['-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-movflags', '+faststart', str(output)]
            processes.run(command, capture_output=True, check=True, timeout=1800)
            stream = quality.video_stream(output, timing.time_map(timeline)['frame_count'])
            if not stream['valid']:
                raise ValueError('Output video coverage failed: ' + '; '.join(stream['warnings']))
            audio_report = {**audio_settings, **audio_engine.measure(output), 'delivery_codec': 'aac', 'delivery_bitrate': '192k'}
            diagnostics['warnings'].extend({'code': 'audio_output_headroom', 'severity': 'warning', 'message': w} for w in audio_report['warnings'])
            inspection = output_inspect(str(output))
            if not inspection["valid"]:
                raise ValueError("Renderer produced invalid video metadata")
            jobs.verify_media(attempt.manifest)
            processes.check()
            result.update(path=str(output), preview=preview, job_id=attempt.job_id, revision=timeline["revision"],
                          quality=inspection["probe"], has_audio=inspection["has_audio"], audio=audio_report, video_stream=stream,
                          timeline_path=str(attempt.root / "timeline.json"), state_path=str(attempt.root / "state.json"),
                          warnings=diagnostics["warnings"], retry_of=attempt.state["retry_of"],
                          reused_stages=[name for name, stage in attempt.state["stages"].items() if stage.get("reused")])
            attempt.succeed(result)
            return result
    except Exception as exc:
        raise RuntimeError(f"Render failed. job_id={attempt.job_id}; use render_job_status/render_retry. {exc}") from exc


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def render_preview(project_id: str) -> dict[str, Any]:
    """Render an isolated proxy, longest edge <=960px preserving canvas aspect. Never overwrite earlier previews/finals; return job ID, exact revision and saved timeline."""
    return _render(project_id, preview=True)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@project_write
def audio_configure(project_id: str, gain_db: float = 0.0, target_lufs: float | None = None, limiter: bool = False, true_peak_limit_dbfs: float = -2.0) -> dict[str, Any]:
    """Set explicit master processing. Default 0dB, normalization OFF, limiter OFF. target_lufs applies measured static gain, not compression. Limiter has no auto makeup gain. AAC true peak is measured again after export."""
    project = _read(project_id)
    timeline = _timeline(project)
    settings = audio_engine.config({**timeline.get('audio_config', {}), 'gain_db': gain_db,
        'target_lufs': target_lufs, 'limiter': limiter, 'true_peak_limit_dbfs': true_peak_limit_dbfs})
    undo = _snapshot(project, 'before audio_configure')
    timeline['audio_config'] = settings
    return {**_commit(project_id, project, 'audio_configure', settings, undo), 'audio': settings}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@resources.heavy_operation
def render_audio_only(project_id: str) -> dict[str, Any]:
    """Render the current timeline audio to a new float WAV with meters; no video decoding/rendering beyond source audio and metadata."""
    with file_lock(_folder(project_id) / 'writer.lock'):
        project = copy.deepcopy(_read(project_id))
    errors = _validate(project)
    if errors:
        raise ValueError('; '.join(errors))
    timeline = timing.resolve(_timeline(project))
    output = _folder(project_id) / 'mcp_renders' / ('audio-' + uuid.uuid4().hex) / 'soundtrack.wav'
    settings = audio_engine.render(project, timeline, output)
    return {'path': str(output), 'project_id': project_id, 'revision': timeline['revision'],
            'audio': {**settings, **audio_engine.measure(output)}}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@resources.heavy_operation
def audio_replace(video_path: str, audio_path: str, dest: str) -> dict[str, Any]:
    """Replace/remux an audio track into a new mp4/mov/mkv, copying video packets unchanged. Pad short audio with silence or trim long audio to decoded video coverage. Never overwrite an existing file."""
    video, sound, output = (Path(p).expanduser() for p in (video_path, audio_path, dest))
    if not all(p.is_absolute() for p in (video, sound, output)) or not video.is_file() or not sound.is_file():
        raise ValueError('Use existing absolute video/audio paths and an absolute destination')
    if output.exists() or output.suffix.lower() not in {'.mp4', '.mov', '.mkv'}:
        raise ValueError('Destination must be a new mp4/mov/mkv file')
    stream = quality.video_stream(video)
    duration = stream['video_duration_seconds']
    if not duration or not audio_engine.measure(sound)['has_audio']:
        raise ValueError('Cannot determine video duration or audio stream')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.stem + '.' + uuid.uuid4().hex + output.suffix)
    try:
        processes.run([engine.ffmpeg_bin(), '-nostdin', '-n', '-i', str(video), '-i', str(sound),
            '-map', '0:v:0', '-map', '1:a:0', '-map_metadata', '0', '-c:v', 'copy',
            '-af', f'apad,atrim=duration={duration}', '-c:a', 'aac', '-b:a', '192k', str(temporary)],
            capture_output=True, check=True, timeout=1800)
        result_stream = quality.video_stream(temporary, stream['decoded_frame_count'])
        if not result_stream['valid']:
            raise ValueError('Remux output failed video coverage checks')
        measured = audio_engine.measure(temporary)
        os.link(temporary, output)  # Atomic create-if-absent; protects a racing destination.
        return {'path': str(output), 'video_codec_mode': 'copy', 'audio': measured, 'video_stream': result_stream}
    finally:
        temporary.unlink(missing_ok=True)


@mcp.tool(annotations={"readOnlyHint": True})
@resources.heavy_operation
def media_cadence_inspect(media_path: str, sample_seconds: float = 30.0) -> dict[str, Any]:
    """Measure nominal frame coverage and consecutive near-duplicate cadence. Static scenes can resemble repeated animation frames; no automatic interpolation."""
    path = Path(media_path).expanduser().resolve()
    if not path.is_file():
        raise ValueError('Media does not exist')
    return {'path': str(path), 'video_stream': quality.video_stream(path), 'cadence': quality.cadence(path, sample_seconds)}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def render_final(project_id: str) -> dict[str, Any]:
    """Render to a new immutable job directory, returning the exact revision and output path. No model calls; no older output is overwritten."""
    return _render(project_id, preview=False)


@mcp.tool(annotations={"readOnlyHint": True})
@resources.heavy_operation
def quality_inspect(video_path: str, black_seconds_threshold: float = 0.35, freeze_seconds_threshold: float = 1.5) -> dict[str, Any]:
    """Run deterministic black-frame, freeze-frame, audio-stream and metadata checks on an export."""
    path = Path(video_path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"Output does not exist: {path}")
    if not finite(black_seconds_threshold, freeze_seconds_threshold) or min(black_seconds_threshold, freeze_seconds_threshold) <= 0:
        raise ValueError("Detection thresholds must be finite and positive")
    import re
    black = processes.run([engine.ffmpeg_bin(), "-nostdin", "-hide_banner", "-i", str(path), "-vf", f"blackdetect=d={black_seconds_threshold}:pix_th=0.10", "-an", "-f", "null", "-"], capture_output=True, text=True, timeout=120)
    freeze = processes.run([engine.ffmpeg_bin(), "-nostdin", "-hide_banner", "-i", str(path), "-vf", f"freezedetect=n=0.003:d={freeze_seconds_threshold}", "-an", "-f", "null", "-"], capture_output=True, text=True, timeout=120)
    black_hits = re.findall(r"black_start:([\d.]+) black_end:([\d.]+) black_duration:([\d.]+)", black.stderr)
    freeze_hits = re.findall(r"freeze_start: ([\d.]+)|freeze_end: ([\d.]+)|freeze_duration: ([\d.]+)", freeze.stderr)
    probe = engine.ffprobe(path)
    raw = processes.run([engine.ffmpeg_bin(), "-nostdin", "-hide_banner", "-i", str(path)], capture_output=True, text=True, timeout=30).stderr
    warnings: list[str] = []
    if black.returncode or freeze.returncode:
        warnings.append("decode/detection failed")
    if not (float(probe.get("duration", 0) or 0) > 0 and probe.get("width") and probe.get("height")):
        warnings.append("invalid video metadata")
    if black_hits:
        warnings.append("black frames detected")
    if freeze_hits:
        warnings.append("freeze frames detected")
    if "Audio:" not in raw:
        warnings.append("no audio stream")
    else:
        warnings.extend(audio_inspect(str(path))["warnings"])
    try:
        stream = quality.video_stream(path)
        cadence = quality.cadence(path)
    except (RuntimeError, subprocess.SubprocessError, ValueError) as exc:
        stream = {'valid': False, 'warnings': ['Video decode/coverage analysis failed']}
        cadence = {'warnings': [], 'checked': False}
    warnings.extend(stream['warnings'] + cadence['warnings'])
    return {"video_stream": stream, "cadence": cadence, "path": str(path), "probe": probe, "black_segments": [{"start": float(start), "end": float(end), "duration": float(duration)} for start, end, duration in black_hits], "freeze_events": freeze_hits, "warnings": warnings, "pass": not warnings,
            "scope": "Technical checks only; not speech completeness, factuality, timing-to-speech or visual aesthetics."}


@mcp.tool(annotations={"readOnlyHint": True})
def output_inspect(video_path: str) -> dict[str, Any]:
    """Inspect an exported video for deterministic properties such as duration, dimensions and audio streams."""
    path = Path(video_path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"Output does not exist: {path}")
    probe = engine.ffprobe(path)
    result = processes.run([engine.ffmpeg_bin(), "-nostdin", "-hide_banner", "-i", str(path)], capture_output=True, text=True, timeout=30)
    return {"path": str(path), "probe": probe, "has_audio": "Audio:" in result.stderr, "valid": bool(probe.get("duration", 0) > 0 and probe.get("width") and probe.get("height"))}


@mcp.tool(annotations={"readOnlyHint": True})
def timeline_time_map(project_id: str) -> dict[str, Any]:
    """Return source-to-output mapping per clip, including speed and outgoing transition overlap."""
    return timing.time_map(_timeline(_read(project_id)))


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@project_write
def event_bind(project_id: str, event_id: str, clip_id: str, source_start: float, source_end: float) -> dict[str, Any]:
    """Bind a callout/caption/overlay/audio event to an explicit source range. Follow reorder, trim and speed via this clip ID. Existing keys/point times must already map into that source range."""
    project = _read(project_id)
    undo = _snapshot(project, "before event_bind")
    timing.bind(_timeline(project), event_id, clip_id, source_start, source_end)
    errors = _validate(project)
    if errors:
        raise ValueError("; ".join(errors))
    return {**_commit(project_id, project, "event_bind", {"event_id": event_id}, undo),
            "event": timing.locate(timing.resolve(_timeline(project)), event_id)[1]}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@project_write
def event_unbind(project_id: str, event_id: str) -> dict[str, Any]:
    """Detach an event, preserving its currently resolved absolute times, keys and point beats."""
    project = _read(project_id)
    timeline = _timeline(project)
    _, event = timing.locate(timeline, event_id)
    resolved = timing.locate(timing.resolve(timeline), event_id)[1]
    undo = _snapshot(project, "before event_unbind")
    resolved.pop("time_binding", None)
    event.clear()
    event.update(resolved)
    return {**_commit(project_id, project, "event_unbind", {"event_id": event_id}, undo), "event": event}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@project_write
def callout_points_add(project_id: str, points: list[dict[str, Any]], position: Literal["product_left", "product_right"] = "product_left") -> dict[str, Any]:
    """Add 1-4 individually timed numbered points. Each item is {text: str (max 7), start: seconds, end: seconds}; all text and reveal/exit times come from the caller."""
    timing.check_points(points)
    project = _read(project_id)
    undo = _snapshot(project, "before callout_points_add")
    event = {"id": f"callout_{uuid.uuid4().hex[:10]}", "template": "point_list", "points": copy.deepcopy(points),
             "text": "|".join(p["text"] for p in points), "start": min(p["start"] for p in points),
             "end": max(p["end"] for p in points), "position": position, "anchor": "product"}
    _timeline(project)["callouts"].append(event)
    errors = _validate(project)
    if errors:
        raise ValueError("; ".join(errors))
    return {**_commit(project_id, project, "callout_points_add", {"callout_id": event["id"]}, undo), "callout": event}


@mcp.tool(annotations={"readOnlyHint": True})
def render_job_status(project_id: str, job_id: str) -> dict[str, Any]:
    """Read saved stage progress/error/result. Detect interrupted workers through OS locks; no model or automatic retry."""
    return jobs.status(project_id, job_id)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def render_retry(project_id: str, job_id: str) -> dict[str, Any]:
    """Queue a failed/interrupted/cancelled job in a NEW background attempt using its saved revision. Reuse completed stages only when checksums and runtime match. Reject changed source media."""
    from . import render_queue
    snapshot, prior = jobs.retry_input(project_id, job_id, strong=False)
    commerce.render_guard(project_id, snapshot['project'], preview=snapshot['preview'])
    return render_queue.submit(jobs.Attempt(project_id, snapshot['project'], snapshot['preview'], prior,
                                           defer_media=True, manifest=snapshot['media']))


@mcp.tool(annotations={"readOnlyHint": True})
@resources.heavy_operation
def audio_inspect(media_path: str) -> dict[str, Any]:
    """Measure decoded integrated LUFS, oversampled true peak, sample peak and RMS. No file modifications."""
    path = Path(media_path).expanduser().resolve()
    if not path.is_file():
        raise ValueError('Audio/video file does not exist')
    return audio_engine.measure(path)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@project_write
def canvas_configure(project_id: str, width: int = 1080, height: int = 1920, fit: Literal['contain','cover'] = 'contain', background: str = '#000000') -> dict[str, Any]:
    """Set explicit output canvas, fit and #RRGGBB padding. Even 256..3840 dimensions, <=8.3MP; clock stays 25fps. Revalidates transforms; never auto-crops by subject."""
    project=_read(project_id)
    value=canvas.settings({'width':width,'height':height,'fit':fit,'background':background})
    undo=_snapshot(project,'before canvas_configure')
    _timeline(project)['canvas']=value
    errors=[e for e in _validate(project) if e!='main track has no clips']
    if errors: raise ValueError('Invalid canvas change: '+'; '.join(errors))
    return {**_commit(project_id,project,'canvas_configure',value,undo),'canvas':value,'fps':25}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@project_write
def clip_camera_set(project_id: str, clip_id: str, camera: dict[str, Any] | None = None) -> dict[str, Any]:
    """Set/remove camera motion for a video or image clip. Supply start_zoom,end_zoom (1..4), start_x,start_y,end_x,end_y (0..1 within available crop), easing. Motion spans selected playback range. None detaches."""
    project=_read(project_id)
    clip=next((c for c in _timeline(project)['tracks']['main'] if c['id']==clip_id),None)
    if clip is None: raise ValueError('Unknown clip_id')
    engine.check_camera(camera)
    undo=_snapshot(project,'before clip_camera_set')
    if camera is None: clip.pop('camera',None)
    else: clip['camera']=copy.deepcopy(camera)
    return {**_commit(project_id,project,'clip_camera_set',{'clip_id':clip_id},undo),'clip':clip}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
@project_write
def overlay_transform_set(project_id: str, overlay_id: str, keyframes: list[dict[str, Any]], mask: Literal['none','circle','rounded_rect'] = 'none', track_id: str = 'overlays') -> dict[str, Any]:
    """Replace transform keys. Keys: at plus optional x,y,scale,rotation (clockwise degrees),opacity,easing. Unspecified fields hold previous state; easing on a destination key controls the preceding interval. Scale/rotation about rectangle centre. All visible frame bounds must fit; no automatic reposition. Empty keys reset motion."""
    project=_read(project_id); timeline=_timeline(project)
    if timeline['track_meta'].get(track_id,{}).get('kind')!='overlay': raise ValueError('Requires overlay track')
    item=next((e for e in timeline['tracks'][track_id] if e['id']==overlay_id),None)
    if item is None: raise ValueError('Unknown overlay_id')
    if item.get('time_binding'): raise ValueError('Detach time binding before replacing keys')
    if not isinstance(keyframes,list) or len(keyframes)>256: raise ValueError('Provide at most 256 keys')
    allowed={'at',*animation.FIELDS,'easing'}
    for key in keyframes:
        if not isinstance(key,dict) or 'at' not in key or set(key)-allowed: raise ValueError('Invalid transform key fields')
    undo=_snapshot(project,'before overlay_transform_set')
    item.update(keyframes=copy.deepcopy(keyframes),mask=mask,transform_mode=True)
    c=canvas.settings(timeline.get('canvas'))
    check_overlay(item,(c['width'],c['height']))
    item['keyframes'].sort(key=lambda k:k['at'])
    return {**_commit(project_id,project,'overlay_transform_set',{'overlay_id':overlay_id},undo),'overlay':item}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def render_submit(project_id: str, preview: bool = False) -> dict[str, Any]:
    """Snapshot the current validated edit and queue a detached render; return immediately with job_id/revision. FIFO single worker per data directory. Later edits do not affect this job."""
    from . import render_queue
    with file_lock(_folder(project_id)/'writer.lock'):
        project=copy.deepcopy(_read(project_id))
    commerce.render_guard(project_id, project, preview=preview)
    errors=_validate(project)
    if errors: raise ValueError('Timeline validation failed: '+'; '.join(errors))
    project['mcp_timeline']=timing.resolve(_timeline(project))
    return render_queue.submit(jobs.Attempt(project_id,project,preview,defer_media=True))


@mcp.tool(annotations={"readOnlyHint": True})
def render_queue_list(project_id: str | None = None) -> dict[str, Any]:
    """List durable background jobs and stage status. Read-only; does not restart work or change failed jobs."""
    from . import render_queue
    return {'jobs':[jobs.status(r['project_id'],r['job_id']) for r in render_queue.records(project_id)],'concurrency':1}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def render_cancel(project_id: str, job_id: str) -> dict[str, Any]:
    """Cancel an exact queued/running render. Running child FFmpeg processes stop cooperatively; confirm terminal status with render_job_status. Retain all inputs, checkpoints and earlier outputs."""
    from . import render_queue
    return render_queue.cancel(project_id,job_id)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def render_queue_start() -> dict[str, Any]:
    """Wake the local worker to drain queued snapshots after host restart. Does not retry failed/interrupted/cancelled work or create edits."""
    from . import render_queue
    return render_queue.start()


@mcp.tool(annotations={"readOnlyHint": True})
def resource_status() -> dict[str, Any]:
    """Read host-wide policy and cached GPU probe. No rendering or hardware probe is started."""
    return {**resources.status(), 'last_encoder_probe': encoders.cached()}


@mcp.tool(annotations={"readOnlyHint": True})
def task_queue_list(offset: int = 0, limit: int = 20) -> dict[str, Any]:
    """Read the unified media/render queue, including resource waits; never starts jobs."""
    from . import task_queue
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('offset >= 0 and limit 1..100 are required')
    records = task_queue.records()
    result = []
    for item in records[offset:offset+limit]:
        state = (jobs if item['kind'] == 'render' else media_jobs).status(item['project_id'], item['job_id'])
        result.append({**{k: state.get(k) for k in ('project_id', 'job_id', 'status', 'created_at', 'started_at',
                       'finished_at', 'resource_wait', 'current_stage', 'progress', 'retry_of')}, 'kind': item['kind']})
    return {'jobs': result, 'heavy_concurrency': 1, 'total': len(records),
            'next_offset': offset+limit if offset+limit < len(records) else None}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def resource_configure(settings: dict[str, Any]) -> dict[str, Any]:
    """Persist portable resource policy for future heavy slots. Concurrency is fixed at one. Running jobs retain their policy. See resource_status for defaults; no work is started."""
    resources.configure(settings)
    return resource_status()


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def resource_probe() -> dict[str, Any]:
    """Verify GPU encoders with four synthetic frames under the shared resource lock. No project/timeline/media is edited. Strict GPU mode fails if no encoder works."""
    with resources.heavy_slot():
        return encoders.select(engine.ffmpeg_bin(), refresh=True)


def main() -> None:
    from . import task_queue
    import logging

    try:
        task_queue.resume_pending()
    except Exception:
        # Keep inspection/retry tools available even if recovery needs attention.
        logging.getLogger(__name__).exception("Could not resume queued work at startup; use media_queue_start or render_queue_start")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
