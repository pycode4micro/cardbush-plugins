"""Native-frame scene-change signals, not semantic or continuous-view approval."""
import re

from . import engine, processes

VERSION = 1


def summarize(times, start, end, minimum_shot=.45):
    cuts = sorted({round(t, 6) for t in times if start < t < end})
    boundaries = [start, *cuts, end]
    short = [{'start': a, 'end': b, 'duration': round(b-a, 6)}
             for a, b in zip(boundaries, boundaries[1:]) if b-a < minimum_shot]
    return {'status': 'checked', 'version': VERSION, 'source_start': start, 'source_end': end,
            'cuts': cuts, 'short_shots': short, 'minimum_shot_seconds': minimum_shot,
            'requires_review': bool(short),
            'scope': 'Scene-change heuristic at native frame cadence. Motion/flashes can trigger it; '
                     'cuts are review targets, never automatic evidence of a bad edit or semantic approval.'}


def inspect(path, start, end, progress):
    progress('source_internal_cuts', 0, end-start)
    result = processes.run([
        engine.ffmpeg_bin(), '-nostdin', '-hide_banner', '-ss', str(start), '-i', str(path),
        '-t', str(end-start), '-map', '0:v:0', '-an',
        '-vf', "setpts=PTS-STARTPTS,scale=160:-2,select='gt(scene,0.30)',showinfo",
        '-f', 'null', '-'], capture_output=True, check=True, timeout=max(180, 15*(end-start)))
    times = [start + float(t) for t in re.findall(
        r'\bn:\s*\d+\s+pts:\s*-?\d+\s+pts_time:([-\d.e+]+)', result.stderr.decode('utf-8', 'replace'))]
    return summarize(times, start, end)
