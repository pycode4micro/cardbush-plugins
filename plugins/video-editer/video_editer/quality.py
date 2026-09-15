"""Measure decoded video coverage independently of container duration."""
import math
import re
from pathlib import Path

import numpy as np

from . import engine, processes


def video_stream(path, expected_frames=None):
    result = processes.run([engine.ffmpeg_bin(), '-nostdin', '-hide_banner', '-i', str(path),
        '-map', '0:v:0', '-an', '-vf', 'showinfo=checksum=0', '-fps_mode', 'passthrough', '-f', 'null', '-'],
        capture_output=True, text=True, check=True, timeout=1800)
    times = [float(x) for x in re.findall(r'\bn:\s*\d+\s+pts:.*?pts_time:([-+\deE.]+)', result.stderr)]
    if not times:
        raise RuntimeError('No video frames were decoded')
    rates = re.findall(r'config in time_base:.*?frame_rate:\s*(\d+)/(\d+)', result.stderr)
    fps = int(rates[0][0])/int(rates[0][1]) if rates and int(rates[0][1]) else None
    deltas = np.diff(times)
    step = 1/fps if fps else float(np.median(deltas)) if len(deltas) else None
    span = times[-1]-times[0]+step if step else None
    container = engine.ffprobe(Path(path))['duration']
    warnings = []
    if expected_frames is not None and len(times) != expected_frames:
        warnings.append(f'Video decoded {len(times)} frames; expected {expected_frames}')
    if span is not None and container-span > max(.08, (step or .04)*2):
        warnings.append(f'Container extends {container-span:.3f}s beyond decoded video coverage')
    if len(deltas) and (np.min(deltas) <= 0 or (step and np.max(deltas) > step*1.6)):
        warnings.append('Video timestamps contain duplicates, reversals or gaps')
    return {'decoded_frame_count': len(times), 'fps': fps, 'first_pts_seconds': times[0], 'last_pts_seconds': times[-1],
        'video_duration_seconds': span, 'container_duration_seconds': container, 'expected_frame_count': expected_frames,
        'warnings': warnings, 'valid': not warnings}


def cadence(path, sample_seconds=30.0, difference_threshold=.5):
    if not math.isfinite(sample_seconds) or not 0 < sample_seconds <= 120:
        raise ValueError('sample_seconds must be 0..120')
    result = processes.run([engine.ffmpeg_bin(), '-nostdin', '-v', 'error', '-i', str(path), '-t', str(sample_seconds),
        '-an', '-vf', 'scale=96:54,format=gray', '-fps_mode', 'passthrough', '-f', 'rawvideo', '-'],
        capture_output=True, check=True, timeout=300)
    frames = np.frombuffer(result.stdout, dtype=np.uint8).reshape(-1, 96*54)
    differences = np.mean(np.abs(np.diff(frames.astype(np.int16), axis=0)), axis=1) if len(frames) > 1 else np.array([])
    duplicate = differences < difference_threshold
    duration = min(sample_seconds, float(engine.ffprobe(Path(path))['duration']))
    ratio = float(np.mean(duplicate)) if len(duplicate) else None
    return {'sample_start': 0, 'sample_seconds': duration, 'sampled_frames': len(frames),
        'near_duplicate_fraction': ratio, 'estimated_updates_per_second': float(np.count_nonzero(~duplicate)/duration) if duration else None,
        'difference_threshold_255': difference_threshold,
        'warnings': ['High near-duplicate ratio: inspect source cadence; nominal FPS is not effective motion.'] if ratio is not None and ratio > .5 else [],
        'scope': 'First sampled interval, consecutive 96x54 luma mean differences. Static scenes can score high; no interpolation or quality claim.'}
