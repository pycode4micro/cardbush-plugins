"""Self-contained, deterministic FFmpeg execution core. No platform or model imports."""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any
from .timing import point_rows
from . import canvas, processes, visuals

ASSETS = Path(__file__).resolve().parent / "assets"
DATA = Path(os.environ.get("VIDEO_EDITER_DATA_DIR", str(Path.home() / ".video_editer"))).expanduser().resolve()
PROJECTS = DATA / "projects"


def project_dir(project_id: str) -> Path:
    if not re.fullmatch(r"prj_[A-Za-z0-9]+", project_id):
        raise ValueError("Invalid project_id")
    target = (PROJECTS / project_id).resolve()
    if target.parent != PROJECTS.resolve():
        raise ValueError("Project path escapes the data directory")
    if not (target / "project.json").is_file():
        raise ValueError(f"Unknown project_id: {project_id}")
    return target


def read_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    # Same-directory replacement avoids a partially written project on interruption.
    # Concurrent writers still need external serialization, as documented.
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    for attempt in range(40):
        try:
            os.replace(temporary, path)
            break
        except PermissionError:
            # Windows readers/antivirus can briefly deny replace. Never remove
            # the previous manifest; bounded retry retains atomic visibility.
            if attempt==39: raise
            time.sleep(.025)


def create_project(title: str, prompt: str = "") -> dict[str, Any]:
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 100:
        raise ValueError("title requires 1-100 non-blank characters")
    if not isinstance(prompt, str) or len(prompt) > 3000:
        raise ValueError("prompt is metadata only, max 3000 characters")
    project_id = "prj_" + uuid.uuid4().hex[:16]
    folder = PROJECTS / project_id
    (folder / "materials").mkdir(parents=True, exist_ok=False)
    project = {"id": project_id, "title": title.strip(), "prompt": prompt, "materials": []}
    write_json(folder / "project.json", project)
    return project


def validate_transition_timing(clips: list[dict[str, Any]]) -> None:
    for index, clip in enumerate(clips):
        duration = (float(clip["end"]) - float(clip["start"])) / float(clip.get("playback_speed", 1))
        incoming = transition_overlap_seconds(str(clips[index - 1].get("transition", "none"))) if index else 0
        outgoing = transition_overlap_seconds(str(clip.get("transition", "none"))) if index < len(clips)-1 else 0
        if duration < incoming + outgoing + 0.04 - 1e-9:
            raise ValueError(f"clip {index + 1} is too short for its requested transition overlap; extend it or choose another transition")


def render_project(project_id: str, plan: dict[str, Any], *, render_job: str | None = None, project_snapshot: dict[str, Any] | None = None, job_prepared: bool = False) -> dict[str, Any]:
    """Render only the caller's explicit clips, speeds and effects; never plan."""
    folder = project_dir(project_id)
    project = project_snapshot if project_snapshot is not None else read_json(folder / "project.json", {})
    if not isinstance(plan, dict) or not isinstance(plan.get("clips"), list) or not plan["clips"]:
        raise ValueError("An explicit non-empty clip plan is required")
    assets = {item["id"]: item for item in project.get("materials", [])}
    clips = plan["clips"]
    for clip in clips:
        asset = assets.get(clip.get("asset_id"))
        if not asset or asset.get("kind") not in {"video", "image"} or not Path(asset["path"]).is_file():
            raise ValueError("Every main clip must reference an existing video/image asset")
        start, end, speed = float(clip["start"]), float(clip["end"]), float(clip.get("playback_speed", 1))
        if not all(math.isfinite(v) for v in (start, end, speed)) or speed not in {1.0, 1.25}:
            raise ValueError("Invalid clip timing/speed")
        limit = 3600 if asset['kind'] == 'image' else float(ffprobe(Path(asset["path"]))["duration"]) + 0.05
        if not 0 <= start < end <= limit:
            raise ValueError("Clip exceeds source duration")
    output_canvas = canvas.settings(plan.get('canvas'))
    cw, ch = output_canvas['width'], output_canvas['height']
    validate_transition_timing(clips)
    job = render_job if render_job is not None else "render-" + uuid.uuid4().hex
    if not re.fullmatch(r"[A-Za-z0-9_-]+", job):
        raise ValueError("render_job must be a simple identifier")
    render_dir = folder / "mcp_renders" / job
    if job_prepared:
        if not (render_dir / "state.json").is_file():
            raise ValueError("Prepared render job is missing its manifest")
        render_dir = render_dir / "base"
    render_dir.mkdir(parents=True, exist_ok=False)
    parts: list[Path] = []
    for index, clip in enumerate(clips):
        processes.check()
        asset = assets[clip["asset_id"]]
        start, end, speed = float(clip["start"]), float(clip["end"]), float(clip.get("playback_speed", 1))
        duration = render_duration(clip)
        part = render_dir / f"part-{index:02d}.mp4"
        has_audio = asset['kind'] == 'video' and bool(ffprobe(Path(asset['path'])).get('has_audio'))
        command = ([ffmpeg_bin(), '-nostdin', '-y', '-loop', '1', '-framerate', '25', '-t', str(end-start), '-i', asset['path']]
                   if asset['kind'] == 'image' else
                   [ffmpeg_bin(), "-nostdin", "-y", "-ss", str(start), "-t", str(end-start), "-i", asset["path"]])
        if not has_audio:
            command += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        vf = [f"setpts=(PTS-STARTPTS)/{speed}", canvas.fit_filter(output_canvas), exact_video_filter(duration)]
        if clip.get('camera'):
            vf.append(camera_filter(clip, cw, ch))
        previous_transition = clips[index-1].get("transition") if index else "none"
        if previous_transition in {"zoom_punch", "whip_pan", "detail_smash_cut"}:
            # This crop is part of the explicitly selected preset, not a role heuristic.
            zw, zh = math.ceil(cw*1.034/2)*2, math.ceil(ch*1.034/2)*2
            vf += [f"scale={zw}:{zh}", f"crop={cw}:{ch}:(iw-ow)/2:(ih-oh)/2"]
        from .audio import clip_filter
        command += ["-map", "0:v:0", "-map", "0:a:0" if has_audio else "1:a:0",
                    "-vf", ",".join(vf), "-af", clip_filter(clip),
                    "-t", f"{duration:.6f}", "-ar", "48000", "-ac", "2",
                    "-r", "25", "-c:v", "libx264", "-c:a", "alac", "-pix_fmt", "yuv420p", str(part)]
        processes.run(command, capture_output=True, check=True, timeout=600)
        parts.append(part)
    current = render_dir / "assembled.mp4"
    _assemble_edit(parts, plan, current)
    paint = any(c.get("transition") == "paint_flash" for c in clips[:-1])
    if paint:
        output = render_dir / "paint.mp4"
        if not _apply_paint_wipes(current, output, plan):
            raise RuntimeError("Requested paint transition failed to render")
        current = output
    audio = plan.get("audio") or {}
    has_bed = bool(audio.get("bed_asset_id"))
    if has_bed:
        bed = assets.get(audio["bed_asset_id"])
        if not bed or bed.get("kind") not in {"video", "audio"}:
            raise ValueError("Audio bed references an unknown audio/video asset")
        offset = float(audio.get("start_seconds", 0))
        if not math.isfinite(offset) or not 0 <= offset < float(ffprobe(Path(bed["path"]))["duration"]):
            raise ValueError("Audio bed offset exceeds source")
        output = render_dir / "dialogue.mp4"
        # A short bed is padded with silence; it must never truncate the video.
        duration = float(ffprobe(current)["duration"])
        command = [ffmpeg_bin(), "-nostdin", "-y", "-i", str(current), "-ss", str(offset), "-i", bed["path"],
                   "-map", "0:v:0", "-map", "1:a:0", "-af", "apad", "-t", str(duration),
                   "-c:v", "copy", "-c:a", "alac", str(output)]
        processes.run(command, capture_output=True, check=True, timeout=240)
        current = output
    output = render_dir / "final.mp4"
    effects = _apply_visual_effects(current, output, plan, render_dir)
    quality = ffprobe(output)
    if not quality.get("width") or float(quality.get("duration") or 0) <= 0:
        raise RuntimeError("Rendered file has invalid video metadata")
    return {"status": "succeeded", "path": str(output), "clip_count": len(parts),
            "continuous_dialogue_bed": has_bed, "paint_wipes_applied": paint,
            "visual_effects_applied": effects, "quality": quality}


def _apply_visual_effects(source: Path, output: Path, plan: dict[str, Any], workdir: Path) -> bool:
    events = [dict(event) for event in plan.get("visual_effects", [])]
    if not events:
        shutil.copy2(source, output)
        return False
    modern = [e for e in events if e.get('template') in visuals.CALLOUTS]
    events = [e for e in events if e.get('template') not in visuals.CALLOUTS]
    if modern:
        source = visuals.render_layer(source, workdir/'procedural-callouts.mp4', callouts=modern)
    if not events:
        shutil.copy2(source, output)
        return True
    stickers = workdir / "template-stickers.mp4"
    applied = _apply_template_stickers(source, stickers, events)
    if not applied and any(e.get("template") in {"comic_burst", "comic_bubble", "celebrate_cloud", "number_3d", "promo_3d", "entrance_card", "either_or"} for e in events):
        raise RuntimeError("Requested callout sticker failed to render")
    current = stickers if applied else source
    plates = workdir / "point-list-plates.mp4"
    applied = _apply_point_list_plates(current, plates, events)
    if not applied and any(e.get("template") == "point_list" for e in events):
        raise RuntimeError("Requested point-list plates failed to render")
    current = plates if applied else current
    ass = _ass_document(events, [], exact_text=True)
    info = ffprobe(source)
    ass = remap_ass(ass, info['width'], info['height'])
    # The caller may choose an installed font; no Windows font file is shipped.
    font = os.environ.get("VIDEO_EDITER_FONT", "Microsoft YaHei").strip()
    if not font or any(c in font for c in ",\r\n"):
        raise ValueError("VIDEO_EDITER_FONT must be one ASS-safe font family")
    ass = ass.replace("Microsoft YaHei", font)
    (workdir / "effects.ass").write_text(ass, encoding="utf-8-sig")
    command = [ffmpeg_bin(), "-nostdin", "-y", "-i", str(current), "-vf", "ass=effects.ass",
               "-r", "25", "-c:v", "libx264", "-c:a", "copy", "-movflags", "+faststart", str(output)]
    processes.run(command, cwd=workdir, capture_output=True, check=True, timeout=240)
    return True


CALLOUT_TEMPLATES = {"comic_burst", "comic_bubble", "celebrate_cloud", "number_3d", "promo_3d", "entrance_card", "either_or", "recommend_arrow", "quote_card", "point_list", "statement_block", *visuals.CALLOUTS}
CALLOUT_ENTRANCES = {"pop", "slide_up", "fade", "none"}
CALLOUT_COLOR_THEMES = {"sunshine", "coral", "mint", "electric_blue", "cream"}
SEASONAL_TRANSITIONS: dict[str, tuple[str, str, str]] = {
    "spring_petal_open": ("春", "circleopen", "新品亮相、柔和开场；花瓣般由中心绽开。"),
    "spring_breeze_slide": ("春", "smoothleft", "同款不同角度、轻盈通勤展示；柔和侧滑。"),
    "spring_dew_wipe": ("春", "wipeup", "面料细节或清新色彩切换；露珠上拭。"),
    "spring_ribbon_diagonal": ("春", "diagtl", "穿搭前后对比、动作方向一致；斜向丝带感。"),
    "spring_bloom_radial": ("春", "radial", "核心卖点出现、画面重心稳定；向外绽放。"),
    "summer_wave_roll": ("夏", "smoothright", "海边、连衣裙摆动、镜头横移；水波推移。"),
    "summer_splash_pixel": ("夏", "pixelize", "热烈卖点、节奏爆点；颗粒水花碎裂。"),
    "summer_sun_flash": ("夏", "fadewhite", "高光、亮色或价格爆点；短促阳光闪白。"),
    "summer_citrus_slice": ("夏", "hlslice", "多角度快速展示；横向切片。"),
    "summer_pool_circle": ("夏", "circlecrop", "局部特写转全身、画面主体居中时；圆形水池裁切。"),
    "winter_snow_fade": ("冬", "fadegrays", "毛呢、针织、低饱和氛围；雪雾渐变。"),
    "winter_frost_wipe": ("冬", "wipedown", "材质特写与全身展示切换；冰霜下拭。"),
    "winter_night_slide": ("冬", "slideright", "深色系、夜景、正式穿搭；稳重侧推。"),
    "winter_wind_slice": ("冬", "hrslice", "风衣、围巾、走动镜头；横向风切。"),
    "winter_glow_open": ("冬", "vertopen", "保暖亮点、礼物感卖点；竖向暖光打开。"),
    "winter_crystal_diag": ("冬", "diagbr", "配饰、纹理、细节切换；冰晶斜切。"),
    "winter_lantern_zoom": ("冬", "zoomin", "节庆、红色、暖色卖点；暖光推进。"),
    "winter_smoke_dissolve": ("冬", "dissolve", "氛围镜头、静态质感画面；雾化溶解。"),
    "winter_window_close": ("冬", "horzclose", "收束一个子段落、切向细节；窗格合拢。"),
    "winter_firefly_distance": ("冬", "distance", "不同景别的柔和转换；微光扩散。"),
}


def ffprobe(path: Path) -> dict[str, Any]:
    cmd = [os.getenv("FFPROBE_BIN", "ffprobe"), "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height", "-of", "json", str(path)]
    try:
        result = processes.run(cmd, capture_output=True, text=True, check=True, timeout=20)
        raw = json.loads(result.stdout)
        video = next((s for s in raw.get("streams", []) if s.get("codec_type") == "video"), {})
        return {"duration": round(float(raw.get("format", {}).get("duration", 0)), 2), "width": video.get("width"), "height": video.get("height"), "has_audio": any(s.get("codec_type") == "audio" for s in raw.get("streams", []))}
    except (FileNotFoundError, subprocess.SubprocessError, json.JSONDecodeError, ValueError):
        # imageio-ffmpeg ships only ffmpeg. This fallback keeps the project runnable
        # on workstations where a system ffprobe was never installed.
        try:
            result = processes.run([ffmpeg_bin(), "-nostdin", "-hide_banner", "-i", str(path)], capture_output=True, text=True, timeout=20)
            text = result.stderr
            duration_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
            size_match = re.search(r"\b(\d{2,5})x(\d{2,5})\b", text)
            duration = 0.0
            if duration_match:
                hours, minutes, seconds = duration_match.groups()
                duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
            return {"duration": round(duration, 2), "width": int(size_match.group(1)) if size_match else None, "height": int(size_match.group(2)) if size_match else None, "has_audio": "Audio:" in text}
        except (FileNotFoundError, subprocess.SubprocessError):
            return {"duration": 0, "width": None, "height": None}


def ffmpeg_bin() -> str:
    configured = os.getenv("FFMPEG_BIN", "").strip()
    if configured:
        return configured
    system_binary = shutil.which("ffmpeg")
    if system_binary:
        return system_binary
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as exc:
        raise FileNotFoundError("未找到 FFmpeg；请安装 imageio-ffmpeg 或配置 FFMPEG_BIN") from exc


def transition_overlap_seconds(transition: str) -> float:
    """Preset overlap on the same 25fps clock used by the executor/time map."""
    if transition in visuals.TRANSITIONS:
        return math.ceil(visuals.TRANSITIONS[transition]['duration']*25-1e-9)/25
    nominal = .42 if transition == 'paint_flash' else .34 if transition == 'slide_push' else .58 if transition.startswith('winter_') else .50 if transition.startswith('summer_') else .46 if transition.startswith('spring_') else 0
    return math.ceil(nominal*25-1e-9)/25


def render_duration(clip: dict[str, Any]) -> float:
    """Explicit rounding on the shared 25fps clock; never change source bounds."""
    duration = (float(clip['end'])-float(clip['start']))/float(clip.get('playback_speed', 1))
    policy = clip.get('frame_alignment', 'cover')
    if policy not in {'cover', 'nearest', 'floor'}:
        raise ValueError('frame_alignment must be cover, nearest or floor')
    frames = math.ceil(duration*25-1e-9) if policy == 'cover' else math.floor(duration*25+(.5 if policy == 'nearest' else 1e-9))
    if frames < 1:
        raise ValueError('Clip selection must produce at least one output frame')
    return frames/25


def exact_video_filter(duration):
    # Also set output -r 25 on encoding commands: relying only on fps filters
    # can leave the final MP4 packet without duration (FFmpeg 7.1), losing a
    # decoded frame on every subsequent pass.
    # Two guard frames cover fps EOF lookahead after setpts; trim still caps the
    # visible output to the requested frame budget. Never pad an arbitrary tail.
    return f'fps=25:start_time=0,tpad=stop_mode=clone:stop=2,trim=end_frame={round(duration*25)},setpts=N/(25*TB)'


def check_camera(camera):
    if camera is None:
        return
    required={'start_zoom','end_zoom','start_x','start_y','end_x','end_y','easing'}
    if not isinstance(camera,dict) or set(camera)!=required:
        raise ValueError('Camera needs start/end zoom, start/end x/y and easing')
    for name in required-{'easing'}:
        number=float(camera[name])
        low,high=(1,4) if name.endswith('zoom') else (0,1)
        if not math.isfinite(number) or not low<=number<=high:
            raise ValueError('Camera zoom is 1..4; pan x/y is 0..1 within available crop')
    if camera['easing'] not in visuals.animation.EASINGS:
        raise ValueError('Unknown camera easing')


def camera_filter(clip,w,h):
    c=clip['camera']; check_camera(c)
    p=f'min(1,on/{max(1,round(render_duration(clip)*25)-1)})'
    mode=c['easing']
    q=f'({p})*({p})' if mode=='ease_in' else f'1-(1-({p}))*(1-({p}))' if mode=='ease_out' else f'({p})*({p})*(3-2*({p}))' if mode=='ease_in_out' else f'gte({p},1)' if mode=='hold' else p
    def expr(name):
        a,b=float(c['start_'+name]),float(c['end_'+name])
        return f'({a:.8f}+({b-a:.8f})*({q}))'
    return f"zoompan=z='{expr('zoom')}':x='(iw-iw/zoom)*{expr('x')}':y='(ih-ih/zoom)*{expr('y')}':d=1:s={w}x{h}:fps=25"


def remap_ass(document,w,h):
    """Preserve legacy side alignment and glyph proportions on other canvases."""
    if (w,h)==(1080,1920): return document
    scale=min(w/1080,h/1920)
    document=document.replace('PlayResX: 1080',f'PlayResX: {w}').replace('PlayResY: 1920',f'PlayResY: {h}')
    def px(x):
        x=float(x)
        return x*scale if x<540 else w-(1080-x)*scale if x>540 else w/2
    def pos(match):
        return f'\\pos({px(match[1]):.3f},{float(match[2])*h/1920:.3f})'
    document=re.sub(r'\\pos\(([-\d.]+),([-\d.]+)\)',pos,document)
    def move(match):
        a=match[1].split(',')
        a[:4]=[f'{px(a[0]):.3f}',f'{float(a[1])*h/1920:.3f}',f'{px(a[2]):.3f}',f'{float(a[3])*h/1920:.3f}']
        return '\\move('+','.join(a)+')'
    document=re.sub(r'\\move\(([^)]+)\)',move,document)
    document=re.sub(r'\\(fs|bord|shad)([\d.]+)',lambda m:'\\'+m[1]+f'{float(m[2])*scale:.3f}',document)
    lines=[]
    for line in document.splitlines():
        if line.startswith('Style: '):
            fields=line.split(',')
            for index in (2,16,17,19,20): fields[index]=str(round(float(fields[index])*scale,3))
            fields[21]=str(round(float(fields[21])*h/1920))
            line=','.join(fields)
        lines.append(line)
    return '\n'.join(lines)+'\n'


def _assemble_procedural(left,right,output,name,left_duration,right_duration):
    info=ffprobe(left); size=(info['width'],info['height'])
    overlap=transition_overlap_seconds(name)
    root=output.parent/(output.stem+'-animation'); root.mkdir(exist_ok=False)
    mask,edge=visuals.transition_assets(name,root,size,overlap)
    cut=left_duration-overlap
    graph=(f'[0:v]split=2[lp][lt];[1:v]split=2[rt][rs];'
           f'[lp]trim=duration={cut:.6f},setpts=PTS-STARTPTS[prefix];'
           f'[lt]trim=start={cut:.6f}:duration={overlap:.6f},setpts=PTS-STARTPTS,format=gbrp[a];'
           f'[rt]trim=duration={overlap:.6f},setpts=PTS-STARTPTS,format=gbrp[b];'
           '[2:v]format=gbrp,setpts=PTS-STARTPTS[m];'
           '[a][b][m]maskedmerge[reveal];[reveal][3:v]overlay=0:0:shortest=1,format=yuv420p[transition];'
           f'[rs]trim=start={overlap:.6f},setpts=PTS-STARTPTS[suffix];'
           '[prefix][transition][suffix]concat=n=3:v=1:a=0[video];'
           f'[0:a]apad,atrim=duration={left_duration:.6f},asetpts=PTS-STARTPTS[la];'
           f'[1:a]apad,atrim=duration={right_duration:.6f},asetpts=PTS-STARTPTS[ra];'
           f'[la][ra]acrossfade=d={overlap:.6f}:c1=tri:c2=tri[audio]')
    graph = graph.replace('[video];', '[joined];') + f';[joined]{exact_video_filter(left_duration+right_duration-overlap)}[video]'
    cmd=[ffmpeg_bin(),'-nostdin','-y','-i',str(left),'-i',str(right),'-i',str(mask),'-i',str(edge),
         '-filter_complex_threads','1','-filter_complex',graph,'-map','[video]','-map','[audio]',
         '-t',str(left_duration+right_duration-overlap),'-r','25','-c:v','libx264','-pix_fmt','yuv420p','-c:a','alac',str(output)]
    processes.run(cmd,capture_output=True,check=True,timeout=600)


def _apply_paint_wipes(source: Path, output: Path, plan: dict[str, Any]) -> bool:
    info = ffprobe(source)
    w,h = info['width'], info['height']
    phases = [ASSETS / f"water-phase-{index}.png" for index in range(1, 5)]
    if not all(phase.exists() for phase in phases):
        return False
    offsets: list[float] = []
    cursor = 0.0
    for clip in plan.get("clips", [])[:-1]:
        clip_duration = render_duration(clip)
        transition = clip.get("transition")
        if transition == "paint_flash":
            # Texture and xfade use the same frame-quantized transition window.
            offsets.append(max(0.0, cursor + clip_duration - transition_overlap_seconds("paint_flash")))
            cursor += clip_duration - transition_overlap_seconds("paint_flash")
        elif transition == "slide_push":
            cursor += clip_duration - transition_overlap_seconds(str(transition))
        elif transition in SEASONAL_TRANSITIONS or transition in visuals.TRANSITIONS:
            cursor += clip_duration - transition_overlap_seconds(str(transition))
        else:
            cursor += clip_duration
    if not offsets:
        return False
    cmd = [ffmpeg_bin(), "-nostdin", "-y", "-i", str(source)]
    for _ in offsets:
        for phase in phases:
            cmd.extend(["-loop", "1", "-t", "0.105", "-i", str(phase)])
    filters = ["[0:v]setpts=PTS-STARTPTS[v0]"]
    current = "v0"
    for index, offset in enumerate(offsets, start=1):
        overlay = f"r{index}"
        next_video = f"v{index}"
        # Four independently drawn phases make the paint itself evolve:
        # corner entry -> spread -> overflow/cover -> trailing recovery.
        # This is intentionally a frame-sequence, not a static decal moved
        # across the screen.
        phase_labels: list[str] = []
        first_input = 1 + (index - 1) * len(phases)
        for phase_index in range(len(phases)):
            label = f"r{index}p{phase_index}"
            phase_labels.append(f"[{label}]")
            filters.append(
                f"[{first_input + phase_index}:v]format=rgba,scale={w}:{h},setsar=1,"
                f"trim=duration=0.105,setpts=PTS-STARTPTS[{label}]"
            )
        filters.append(
            f"{''.join(phase_labels)}concat=n=4:v=1:a=0,"
            # Add a sub-frame turn so even frames inside a state do not hold
            # perfectly still; the state sequence supplies the larger shape
            # changes and this supplies continuous fluid motion at 25fps.
            f"rotate=0.012*sin(15*t):c=none:ow={w}:oh={h},"
            f"fade=t=in:st=0:d=0.025:alpha=1,fade=t=out:st=0.38:d=0.04:alpha=1,"
            f"setpts=PTS+{offset:.3f}/TB[{overlay}]"
        )
        stop = offset+transition_overlap_seconds("paint_flash")
        filters.append(f"[{current}][{overlay}]overlay=0:0:enable='gte(t,{offset:.6f})*lt(t,{stop:.6f})':eof_action=pass:repeatlast=0[{next_video}]")
        current = next_video
    source_duration = float(ffprobe(source).get("duration") or 0)
    cmd.extend(["-filter_complex_threads", "1", "-filter_complex", ";".join(filters), "-map", f"[{current}]", "-map", "0:a?", "-t", str(source_duration), "-r", "25", "-c:v", "libx264", "-c:a", "copy", "-movflags", "+faststart", str(output)])
    try:
        processes.run(cmd, capture_output=True, check=True, timeout=240)
        return True
    except subprocess.SubprocessError:
        return False


def _apply_template_stickers(source: Path, output: Path, events: list[dict[str, Any]]) -> bool:
    """Put generated comic plates behind editable agent-authored text."""
    # Compact horizontal plates deliberately leave the product centre open.
    # The first generation used portrait frames, which read as a large blank
    # mask over clothing; only side-safe plates are allowed here.
    assets = {
        "comic_burst": (ASSETS / "comic-burst-v3.png", 360),
        "comic_bubble": (ASSETS / "number-3d-v3.png", 350),
        "celebrate_cloud": (ASSETS / "number-3d-v3.png", 350),
        "number_3d": (ASSETS / "number-3d-v3.png", 350),
        "promo_3d": (ASSETS / "comic-burst-v3.png", 360),
        "entrance_card": (ASSETS / "comic-burst-v3.png", 360),
        "either_or": (ASSETS / "comic-burst-v3.png", 360),
    }
    selected = [event for event in events if str(event.get("template")) in assets]
    if not selected or not all(assets[str(event["template"])][0].exists() for event in selected):
        return False
    info = ffprobe(source)
    w,h = info['width'],info['height']
    scale = min(w/1080,h/1920)
    cmd = [ffmpeg_bin(), "-nostdin", "-y", "-i", str(source)]
    for event in selected:
        duration = float(event["end"]) - float(event["start"])
        cmd.extend(["-loop", "1", "-t", f"{duration:.3f}", "-i", str(assets[str(event["template"])][0])])
    filters = ["[0:v]setpts=PTS-STARTPTS[v0]"]
    current = "v0"
    for index, event in enumerate(selected, start=1):
        _, width = assets[str(event["template"])]
        width = max(2, round(width*scale))
        position = str(event.get("position", "product_left"))
        x = round(28*scale) if position == "product_left" else w - width - round(28*scale)
        y = round((330 if position == "product_left" else 350)*h/1920)
        start, end = float(event["start"]), float(event["end"])
        duration = end - start
        overlay, next_video = f"s{index}", f"sv{index}"
        filters.append(
            f"[{index}:v]format=rgba,scale={width}:-1,fade=t=in:st=0:d=0.1:alpha=1,"
            f"fade=t=out:st={max(0, duration - 0.06):.6f}:d=0.06:alpha=1,"
            f"setpts=PTS+{start:.3f}/TB[{overlay}]"
        )
        filters.append(f"[{current}][{overlay}]overlay={x}:{y}:enable='gte(t,{start:.3f})*lt(t,{end:.3f})':eof_action=pass:repeatlast=0[{next_video}]")
        current = next_video
    cmd.extend(["-filter_complex_threads", "1", "-filter_complex", ";".join(filters), "-map", f"[{current}]", "-map", "0:a?", "-r", "25", "-c:v", "libx264", "-c:a", "copy", "-movflags", "+faststart", str(output)])
    try:
        processes.run(cmd, capture_output=True, check=True, timeout=180)
        return True
    except subprocess.SubprocessError:
        return False


def _apply_point_list_plates(source: Path, output: Path, events: list[dict[str, Any]]) -> bool:
    """Reveal two individual ecommerce point cards on separate beats."""
    first_plate = ASSETS / "point-list-row1.png"
    second_plate = ASSETS / "point-list-row2.png"
    point_events = [
        event for event in events
        if event.get("template") == "point_list"
        and ("points" in event or (len(str(event.get("text", "")).split("|")) == 2 and all(item.strip() for item in str(event.get("text", "")).split("|"))))
    ]
    if not first_plate.exists() or not second_plate.exists() or not point_events:
        return False
    info=ffprobe(source); w,h=info['width'],info['height']; scale=min(w/1080,h/1920)
    cmd = [ffmpeg_bin(), "-nostdin", "-y", "-i", str(source)]
    for event in point_events:
        for index, point in enumerate(point_rows(event)):
            plate = first_plate if "points" in event or index == 0 else second_plate
            cmd.extend(["-loop", "1", "-t", f"{point['end']-point['start']:.6f}", "-i", str(plate)])
    filters = ["[0:v]setpts=PTS-STARTPTS[v0]"]
    current = "v0"
    input_index = 1
    for event_index, event in enumerate(point_events, start=1):
        plate_x = round(w-532*scale) if event.get("position") == "product_right" else round(32*scale)
        entry_x = 1080 if event.get("position") == "product_right" else -520
        for row, point in enumerate(point_rows(event), start=1):
            offset, end = float(point["start"]), float(point["end"])
            y = round((640+(232 if "points" in event else 202)*(row-1))*h/1920)
            duration = end-offset
            overlay, next_video = f"p{event_index}_{row}", f"pv{event_index}_{row}"
            filters.append(
                f"[{input_index}:v]format=rgba,scale={max(2,round(500*scale))}:-1,fade=t=in:st=0:d=0.06:alpha=1,"
                f"fade=t=out:st={max(0.18, duration - 0.14):.3f}:d=0.14:alpha=1,"
                f"setpts=PTS+{offset:.3f}/TB[{overlay}]"
            )
            filters.append(
                f"[{current}][{overlay}]overlay="
                f"x={plate_x}:"
                f"y={y}:enable='gte(t,{offset:.3f})*lt(t,{end:.3f})':eval=frame:eof_action=pass:repeatlast=0[{next_video}]"
            )
            current = next_video
            input_index += 1
    cmd.extend([
        "-filter_complex_threads", "1", "-filter_complex", ";".join(filters), "-map", f"[{current}]", "-map", "0:a?",
        "-r", "25", "-c:v", "libx264", "-c:a", "copy", "-movflags", "+faststart", str(output),
    ])
    try:
        processes.run(cmd, capture_output=True, check=True, timeout=180)
        return True
    except subprocess.SubprocessError:
        return False


def _ass_document(visual_events: list[dict[str, Any]], caption_events: list[dict[str, Any]], atomic_events: list[dict[str, Any]] | None = None, *, exact_text: bool = False) -> str:
    header = """[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\nStyle: Pop,Microsoft YaHei,76,&H00FFFFFF,&H000000FF,&H00100D14,&H80100D14,-1,0,0,0,100,100,0,0,1,5,1,8,50,50,250,1\nStyle: Tag,Microsoft YaHei,54,&H00FFFFFF,&H000000FF,&H00100D14,&H80100D14,-1,0,0,0,100,100,0,0,3,14,0,8,50,50,105,1\nStyle: Recommend,Microsoft YaHei,72,&H00FFFFFF,&H000000FF,&H00100FF,&H80100D14,-1,0,0,0,100,100,0,0,1,7,2,7,40,40,110,1\nStyle: Quote,Microsoft YaHei,66,&H0000E8FF,&H000000FF,&H00100D14,&H80100D14,-1,0,0,0,100,100,0,0,1,7,2,7,40,40,110,1\nStyle: Headline3D,Microsoft YaHei,76,&H0000D8FF,&H000000FF,&H00005AC8,&H80002890,-1,0,0,0,100,100,0,0,1,4,5,7,40,40,110,1\nStyle: StatementLead,Microsoft YaHei,76,&H0000E8FF,&H000000FF,&H00100D14,&H80100D14,-1,0,0,0,100,100,0,0,1,7,2,7,40,40,110,1\nStyle: StatementBody,Microsoft YaHei,68,&H00FFFFFF,&H000000FF,&H00100D14,&H80100D14,-1,0,0,0,100,100,0,0,1,7,2,7,40,40,110,1\nStyle: PointNo,Microsoft YaHei,82,&H00F8E6C8,&H000000FF,&H00594A3E,&H00000000,-1,0,0,0,100,100,0,0,1,4,2,7,40,40,110,1\nStyle: PointCard,Microsoft YaHei,58,&H00334CA6,&H000000FF,&H006F7B58,&H00D0E5F8,-1,0,0,0,100,100,0,0,3,10,1,7,40,40,110,1\nStyle: Caption,Microsoft YaHei,56,&H00FFFFFF,&H000000FF,&H00100D14,&H80100D14,-1,0,0,0,100,100,0,0,1,4,1,2,45,45,120,1\n\n[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"""
    lines: list[str] = []
    for index, event in enumerate(visual_events):
        template = str(event.get("template", ""))
        style = "Recommend" if template == "recommend_arrow" else "Quote" if template == "quote_card" else "Headline3D" if template == "headline_3d" else "Recommend" if template in {"comic_burst", "promo_3d", "entrance_card", "either_or"} else "Quote" if template in {"comic_bubble", "celebrate_cloud", "number_3d"} else "Pop"
        start, end = float(event.get("start", index * 3)), float(event.get("end", index * 3 + 2))
        text = str(event["text"]).replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")
        position = str(event.get("position", "product_left"))
        anchors = {
            "upper_left": "\\an7\\pos(58,420)",
            "upper_right": "\\an9\\pos(1022,420)",
            # Product labels are deliberately scene-fixed; they never track a
            # person. The caller may choose left/right after locating the product.
            "product_left": "\\an7\\pos(58,720)",
            "product_right": "\\an9\\pos(1022,650)",
            "product_center": "\\an8\\pos(540,820)",
        }
        anchor = anchors.get(position, anchors["product_left"])
        if template in {"comic_burst", "comic_bubble", "celebrate_cloud", "number_3d", "promo_3d", "entrance_card", "either_or"}:
            # Match the compact plate's empty centre rather than the video
            # centre.  This keeps both the typography and its decoration in
            # the side safe zone and clear of the product.  Keep extra
            # room on the outside edge: ASS centres long glyph strings on
            # this coordinate, so a 10-character Chinese claim at x=208
            # could be clipped by the left frame edge.
            anchor = "\\an5\\pos(232,420)" if position == "product_left" else "\\an5\\pos(848,440)"
        if template == "statement_block":
            first, _, second = text.partition("\\N")
            lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},StatementLead,,0,0,0,,{{{anchor}}}{first if exact_text else first[:14]}")
            lines.append(f"Dialogue: 1,{_ass_time(start)},{_ass_time(end)},StatementBody,,0,0,0,,{{{anchor}\\pos(540,900)}}{second if exact_text else second[:16]}")
            continue
        if template == "point_list":
            badge_x, label_x = (633, 866) if position == "product_right" else (117, 350)
            for row, point in enumerate(point_rows(event), start=1):
                begin, finish = float(point["start"]), float(point["end"])
                cy = 758+232*(row-1) if "points" in event or row == 1 else 948
                label = str(point["text"]).replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
                font_size = min(58, int(280/max(1, len(str(point["text"])))))
                lines.append(f"Dialogue: 0,{_ass_time(begin)},{_ass_time(finish)},PointNo,,0,0,0,,{{\\an5\\pos({badge_x},{cy})\\bord2\\shad0\\fad(60,60)}}{row}")
                lines.append(f"Dialogue: 1,{_ass_time(begin)},{_ass_time(finish)},PointCard,,0,0,0,,{{\\an5\\pos({label_x},{cy})\\bord0\\shad0\\fs{font_size}\\fad(60,60)}}{label}")
            continue
        decoration = "★ " if template == "recommend_arrow" else "「" if template in {"quote_card", "either_or"} else "✦ " if template == "celebrate_cloud" else ""
        suffix = "」" if template in {"quote_card", "either_or"} else ""
        compact_limit = 10 if template in {"comic_burst", "comic_bubble", "celebrate_cloud", "number_3d", "promo_3d", "entrance_card", "either_or"} else 16
        # Comic plates have a deliberately narrow transparent centre.  Wrap
        # their longer model-authored claims into two short visual beats so
        # the decorative plate and its text stay wholly within the side
        # safe zone instead of being clipped by the frame edge.
        display_text = text if exact_text else text[:compact_limit]
        if template in {"comic_burst", "comic_bubble", "celebrate_cloud", "number_3d", "promo_3d", "entrance_card", "either_or"} and len(display_text) > 6:
            split_at = (len(display_text) + 1) // 2
            display_text = display_text[:split_at] + "\\N" + display_text[split_at:]
        theme = {"sunshine": "&H0000D8FF&", "coral": "&H004D5CFF&", "mint": "&H007FE6A5&", "electric_blue": "&H00FFB340&", "cream": "&H00DDEBFF&"}.get(str(event.get("color_theme")), "")
        entrance = str(event.get("entrance", "pop"))
        animation = "\\fscx88\\fscy88\\t(0,140,\\fscx100\\fscy100)" if entrance == "pop" else "\\fad(140,120)" if entrance == "fade" else "\\move(58,800,58,720,0,180)" if entrance == "slide_up" and position == "product_left" else "\\move(1022,730,1022,650,0,180)" if entrance == "slide_up" else ""
        color_override = f"\\c{theme}" if theme else ""
        lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},{style},,0,0,0,,{{{anchor}{animation}{color_override}}}{decoration}{display_text}{suffix}")
    for event in caption_events:
        start, end = float(event.get("start", 0)), float(event.get("end", 2))
        text = str(event["text"]).replace("{", "\\{").replace("}", "\\}")
        lines.append(f"Dialogue: 1,{_ass_time(start)},{_ass_time(end)},Caption,,0,0,0,,{text}")
    lines.extend(_atomic_ass_events(atomic_events or []))
    return header + "\n".join(lines) + "\n"


def _atomic_ass_events(events: list[dict[str, Any]]) -> list[str]:
    """Render the reference-template attention primitives from normalized boxes."""
    lines: list[str] = []
    for event in events[:6]:
        tool = str(event.get("tool", ""))
        if tool not in {"detail_focus_box", "detail_magnifier", "detail_arrow"}:
            continue
        try:
            start, end = float(event["start"]), float(event["end"])
            x, y, width, height = [float(value) for value in event.get("focus_box", [])]
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 <= x < 1 and 0 <= y < 1 and 0 < width <= 1 and 0 < height <= 1):
            continue
        px, py = int(x * 1080), int(y * 1920)
        pw, ph = max(48, int(width * 1080)), max(48, int(height * 1920))
        if tool in {"detail_focus_box", "detail_magnifier"}:
            # ASS vector outline: a small animated focus frame around the
            # model-selected product detail, matching the reference circle/
            # magnifier role without covering the garment with a card.
            drawing = f"m 0 0 l {pw} 0 l {pw} {ph} l 0 {ph} l 0 0"
            lines.append(f"Dialogue: 3,{_ass_time(start)},{_ass_time(end)},Pop,,0,0,0,,{{\\an7\\pos({px},{py})\\p1\\1a&HFF&\\bord5\\3c&H00FFFFFF&\\t(0,120,\\bord8)}}{drawing}")
        if tool in {"detail_arrow", "detail_magnifier"}:
            lines.append(f"Dialogue: 4,{_ass_time(start)},{_ass_time(end)},Recommend,,0,0,0,,{{\\an5\\pos({max(48, px - 28)},{max(48, py - 30)})\\bord3\\shad0}}↘")
    return lines


def _ass_time(value: float) -> str:
    value = max(0.0, value)
    hours, remaining = divmod(value, 3600)
    minutes, seconds = divmod(remaining, 60)
    return f"{int(hours)}:{int(minutes):02d}:{seconds:05.2f}"


def _assemble_edit(parts: list[Path], plan: dict[str, Any], output: Path) -> None:
    """Build the visual edit with real transitions only where requested.

    Hard/zoom cuts remain immediate.  A ``paint_flash`` overlaps the outgoing
    and incoming clips with a directional wipe; the water texture is then
    animated over precisely that overlap by ``_apply_paint_wipes``.
    """
    if len(parts) == 1:
        shutil.copy2(parts[0], output)
        return
    clips = plan.get("clips", [])
    if not any(transition_overlap_seconds(c.get('transition', 'none')) for c in clips[:-1]):
        # Encode each clip once. Concatenate exact video packets and lossless audio,
        # instead of re-encoding all earlier cuts for every additional clip.
        manifest = output.with_suffix('.ffconcat')
        manifest.write_text('ffconcat version 1.0\n' + ''.join(
            f"file '{p.name}'\nduration {render_duration(c):.9f}\n" for p, c in zip(parts, clips)), encoding='utf-8')
        processes.run([ffmpeg_bin(), '-nostdin', '-y', '-f', 'concat', '-safe', '0', '-i', str(manifest),
            '-map', '0:v:0', '-map', '0:a:0', '-c', 'copy', '-movflags', '+faststart', str(output)],
            capture_output=True, check=True, timeout=1800)
        return
    current = parts[0]
    current_duration = render_duration(clips[0])
    for index, following in enumerate(parts[1:], start=1):
        stage = output.parent / f"assembled-{index:02d}.mp4"
        requested = str(clips[index - 1].get("transition", "punch_cut")) if index - 1 < len(clips) else "punch_cut"
        seasonal = SEASONAL_TRANSITIONS.get(requested)
        if requested in visuals.TRANSITIONS:
            _assemble_procedural(current, following, stage, requested, current_duration, render_duration(clips[index]))
            current_duration += render_duration(clips[index])-transition_overlap_seconds(requested)
            current=stage
            continue
        if requested in {"paint_flash", "slide_push"} or seasonal:
            overlap_target = transition_overlap_seconds(requested)
            overlap = overlap_target  # Preflight rejects short clips; never change requested timing.
            xfade = "wipeleft" if requested == "paint_flash" else "slideleft" if requested == "slide_push" else seasonal[1]
            filter_graph = (
                "[0:v]settb=AVTB,setpts=PTS-STARTPTS,fps=fps=25:start_time=0,format=yuv420p[left];"
                "[1:v]settb=AVTB,setpts=PTS-STARTPTS,fps=fps=25:start_time=0,format=yuv420p[right];"
                f"[left][right]xfade=transition={xfade}:duration={overlap:.3f}:"
                f"offset={max(0.0, current_duration - overlap):.3f}[video];"
                f"[0:a]asetpts=PTS-STARTPTS,apad,atrim=duration={current_duration:.6f}[lefta];[1:a]asetpts=PTS-STARTPTS,apad,atrim=duration={render_duration(clips[index]):.6f}[righta];"
                f"[lefta][righta]acrossfade=d={overlap:.3f}:c1=tri:c2=tri[audio]"
            )
        else:
            # Keep the requested punch-cut tempo intact for all other beats.
            filter_graph = (
                "[0:v]fps=25,format=yuv420p,setpts=PTS-STARTPTS[left];"
                "[1:v]fps=25,format=yuv420p,setpts=PTS-STARTPTS[right];"
                "[left][right]concat=n=2:v=1:a=0[video];"
                f"[0:a]asetpts=PTS-STARTPTS,apad,atrim=duration={current_duration:.6f}[lefta];[1:a]asetpts=PTS-STARTPTS,apad,atrim=duration={render_duration(clips[index]):.6f}[righta];"
                "[lefta][righta]concat=n=2:v=0:a=1[audio]"
            )
        current_duration += render_duration(clips[index])-transition_overlap_seconds(requested)
        filter_graph = filter_graph.replace('[video];', '[joined];') + f';[joined]{exact_video_filter(current_duration)}[video]'
        cmd = [
            ffmpeg_bin(), "-nostdin", "-y", "-i", str(current), "-i", str(following),
            "-filter_complex_threads", "1", "-filter_complex", filter_graph, "-map", "[video]", "-map", "[audio]",
            "-t", f"{current_duration:.6f}", "-r", "25", "-c:v", "libx264", "-c:a", "alac", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(stage),
        ]
        processes.run(cmd, capture_output=True, check=True, timeout=600)
        current = stage
    shutil.copy2(current, output)
