"""Explicit audio processing and decoded measurements; no automatic normalization."""
from __future__ import annotations

import json
import math
import re
import tempfile
from pathlib import Path

import numpy as np

from . import engine, processes, timing

RATE = 48000
DEFAULT_FADE_MS = 25.0


def clip_settings(clip):
    values = {name: float(clip.get(name, default)) for name, default in (
        ('audio_gain_db', 0), ('audio_fade_in_ms', DEFAULT_FADE_MS), ('audio_fade_out_ms', DEFAULT_FADE_MS))}
    if not all(math.isfinite(v) for v in values.values()):
        raise ValueError('Clip audio parameters must be finite')
    if not -96 <= values['audio_gain_db'] <= 24 or any(not 0 <= values[k] <= 10000 for k in ('audio_fade_in_ms', 'audio_fade_out_ms')):
        raise ValueError('Clip gain must be -96..24 dB and fades 0..10000 ms')
    if not isinstance(clip.get('audio_mute', False), bool):
        raise ValueError('audio_mute must be boolean')
    return {**values, 'audio_mute': clip.get('audio_mute', False)}


def clip_filter(clip):
    settings = clip_settings(clip)
    duration = engine.render_duration(clip)
    speed = float(clip.get('playback_speed', 1))
    filters = ['asetpts=PTS-STARTPTS', 'aresample=48000', 'aformat=sample_fmts=fltp:channel_layouts=stereo']
    if speed != 1:
        filters.append(f'atempo={speed}')
    filters.extend(['apad', f'atrim=end_sample={round(duration * RATE)}', 'asetpts=N/SR/TB'])
    if settings['audio_mute']:
        filters.append('volume=0')
    elif settings['audio_gain_db']:
        filters.append(f"volume={settings['audio_gain_db']}dB")
    for direction in ('in', 'out'):
        fade = min(settings[f'audio_fade_{direction}_ms'] / 1000, duration / 2)
        if fade:
            filters.append(f'afade=t={direction}:st={0 if direction == "in" else duration-fade:.9f}:d={fade:.9f}')
    return ','.join(filters)


def config(values=None):
    result = {'gain_db': 0.0, 'target_lufs': None, 'limiter': False, 'true_peak_limit_dbfs': -2.0, 'source_volume': 1.0, **(values or {})}
    for key, low, high in [('gain_db', -96, 24), ('true_peak_limit_dbfs', -12, 0), ('source_volume', 0, 1)]:
        value = float(result[key])
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f'{key} must be finite and {low}..{high}')
        result[key] = value
    target = result['target_lufs']
    if target is not None and (not math.isfinite(float(target)) or not -40 <= float(target) <= -5):
        raise ValueError('target_lufs must be null or -40..-5')
    if not isinstance(result['limiter'], bool):
        raise ValueError('limiter must be boolean')
    return result


def measure(path):
    if not engine.ffprobe(Path(path)).get('has_audio'):
        return {'has_audio': False, 'integrated_lufs': None, 'true_peak_dbfs': None, 'warnings': ['no audio stream']}
    # loudnorm's input_i/input_tp are measurements of the unmodified input.
    result = processes.run([engine.ffmpeg_bin(), '-nostdin', '-hide_banner', '-i', str(path), '-vn',
        '-af', 'astats=metadata=0:reset=0,loudnorm=I=-23:TP=-2:LRA=7:print_format=json', '-f', 'null', '-'],
        capture_output=True, text=True, check=True, timeout=1800)
    blocks = re.findall(r'\{\s*"input_i".*?\}', result.stderr, re.S)
    if not blocks:
        raise RuntimeError('FFmpeg returned no loudness/true-peak measurement')
    raw = json.loads(blocks[-1])
    def number(value):
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    def stat(name):
        hits = re.findall(re.escape(name) + r': (-?inf|[-+\d.]+)', result.stderr)
        return number(hits[-1]) if hits else None
    peak = number(raw['input_tp'])
    return {'has_audio': True, 'integrated_lufs': number(raw['input_i']), 'true_peak_dbfs': peak,
        'sample_peak_db': stat('Peak level dB'), 'rms_db': stat('RMS level dB'),
        'near_full_scale': peak is not None and peak >= -.1,
        'warnings': ['possible clipping / insufficient true-peak headroom'] if peak is not None and peak >= -.1 else [],
        'scope': 'Decoded EBU R128 integrated loudness and oversampled true peak; silence is null, not 0 LUFS.'}


def concat(parts, durations, transitions, output):
    """Lossless assembly. Each piece has exactly duration * 48000 samples."""
    if not any(transitions):
        manifest = output.with_suffix('.ffconcat')
        manifest.write_text('ffconcat version 1.0\n' + ''.join(f"file '{p.name}'\n" for p in parts), encoding='utf-8')
        processes.run([engine.ffmpeg_bin(), '-nostdin', '-y', '-f', 'concat', '-safe', '0', '-i', str(manifest),
            '-c:a', 'pcm_f32le', str(output)], capture_output=True, check=True, timeout=1800)
        return
    current = parts[0]
    for i, part in enumerate(parts[1:], 1):
        dest = output if i == len(parts)-1 else output.with_name(f'join-{i}.wav')
        overlap = transitions[i-1]
        graph = f'[0:a][1:a]acrossfade=d={overlap}:c1=tri:c2=tri[a]' if overlap else '[0:a][1:a]concat=n=2:v=0:a=1[a]'
        processes.run([engine.ffmpeg_bin(), '-nostdin', '-y', '-i', str(current), '-i', str(part),
            '-filter_complex', graph, '-map', '[a]', '-c:a', 'pcm_f32le', str(dest)], capture_output=True, check=True, timeout=1800)
        current = dest


def render(project, timeline, output):
    """Render only audio from the same clip clock, including bed, tracks and explicit master settings."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    assets = {a['id']: a for a in project['materials']}
    clips = timeline['tracks']['main']
    mapping = timing.time_map(timeline)
    total = mapping['duration']
    settings = config(timeline.get('audio_config'))
    with tempfile.TemporaryDirectory(prefix='audio-', dir=output.parent) as temporary:
        root = Path(temporary)
        parts = []
        for i, clip in enumerate(clips):
            asset = assets[clip['asset_id']]
            dest = root / f'clip-{i}.wav'
            command = [engine.ffmpeg_bin(), '-nostdin', '-y']
            if asset['kind'] != 'image' and engine.ffprobe(Path(asset['path'])).get('has_audio'):
                command += ['-ss', str(clip['start']), '-t', str(clip['end']-clip['start']), '-i', asset['path']]
            else:
                command += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
            command += ['-vn', '-af', clip_filter(clip), '-c:a', 'pcm_f32le', str(dest)]
            processes.run(command, capture_output=True, check=True, timeout=1800)
            parts.append(dest)
        source = root / 'source.wav'
        concat(parts, [r['end']-r['start'] for r in mapping['clips']], [r['outgoing_overlap'] for r in mapping['clips'][:-1]], source)
        bed = timeline.get('audio', {})
        if bed.get('bed_asset_id'):
            source = root / 'bed.wav'
            processes.run([engine.ffmpeg_bin(), '-nostdin', '-y', '-ss', str(bed.get('start_seconds', 0)),
                '-i', assets[bed['bed_asset_id']]['path'], '-vn', '-af', f'apad,atrim=end_sample={round(total*RATE)}',
                '-ar', str(RATE), '-ac', '2', '-c:a', 'pcm_f32le', str(source)], capture_output=True, check=True, timeout=1800)
        tracks = [e for kind, e in timing.entries(timeline) if kind == 'audio']
        command = [engine.ffmpeg_bin(), '-nostdin', '-y', '-i', str(source)]
        filters = [f"[0:a]volume={settings['source_volume']}[a0]"]
        for i, entry in enumerate(tracks, 1):
            command += ['-i', assets[entry['asset_id']]['path']]
            available = float(engine.ffprobe(Path(assets[entry['asset_id']]['path']))['duration'])-float(entry.get('source_start', 0))
            duration = min(float(entry.get('duration') or available), total-entry['start'])
            chain = [f"[{i}:a]atrim=start={entry.get('source_start', 0)}:duration={duration}", 'asetpts=PTS-STARTPTS', f"volume={entry.get('volume', 1)}"]
            for direction in ('in', 'out'):
                fade = min(float(entry.get(f'fade_{direction}', 0)), duration)
                if fade:
                    chain.append(f'afade=t={direction}:st={0 if direction == "in" else duration-fade}:d={fade}')
            chain.append(f"adelay={round(entry['start']*RATE)}S:all=1[a{i}]")
            filters.append(','.join(chain))
        filters.append(''.join(f'[a{i}]' for i in range(len(tracks)+1)) + f'amix=inputs={len(tracks)+1}:duration=first:dropout_transition=0:normalize=0,apad,atrim=end_sample={round(total*RATE)}[mix]')
        mixed = root / 'mixed.wav'
        processes.run(command + ['-filter_complex_threads', '1', '-filter_complex', ';'.join(filters), '-map', '[mix]',
            '-ar', str(RATE), '-ac', '2', '-c:a', 'pcm_f32le', str(mixed)], capture_output=True, check=True, timeout=1800)
        gain = settings['gain_db']
        normalization_gain = 0.0
        if settings['target_lufs'] is not None:
            loudness = measure(mixed)['integrated_lufs']
            if loudness is not None:
                normalization_gain = float(settings['target_lufs'])-loudness
                gain += normalization_gain
        master = [f'volume={gain}dB']
        if settings['limiter']:
            master += ['aresample=192000', f"alimiter=limit={10**(settings['true_peak_limit_dbfs']/20)}:level=false:latency=true", 'aresample=48000']
        master += ['apad', f'atrim=end_sample={round(total*RATE)}']
        processes.run([engine.ffmpeg_bin(), '-nostdin', '-y', '-i', str(mixed), '-af', ','.join(master),
            '-c:a', 'pcm_f32le', str(output)], capture_output=True, check=True, timeout=1800)
    return {**settings, 'normalization_gain_db': normalization_gain, 'applied_master_gain_db': gain,
        'normalization_mode': 'static measured gain' if settings['target_lufs'] is not None else 'off',
        'clip_settings': [{'clip_id': c['id'], **clip_settings(c)} for c in clips],
        'mix_mode': 'additive, normalize=0', 'intermediate_codec': 'pcm_f32le', 'sample_rate': RATE,
        'channels': 2, 'channel_layout': 'stereo', 'duration': total}


def join_checks(path, rows, rms_delta_db=8.0, jump_threshold=.1):
    if not math.isfinite(rms_delta_db) or rms_delta_db <= 0 or not 0 < jump_threshold <= 2:
        raise ValueError('RMS threshold must be positive; discontinuity threshold must be 0..2')
    issues = []
    for left, right in zip(rows, rows[1:]):
        if left['outgoing_overlap']:
            continue
        at = right['start']
        start = max(0, at-.1)
        result = processes.run([engine.ffmpeg_bin(), '-nostdin', '-v', 'error', '-ss', str(start), '-i', str(path),
            '-t', '0.2', '-vn', '-ar', str(RATE), '-ac', '2', '-f', 'f32le', '-'], capture_output=True, check=True, timeout=60)
        samples = np.frombuffer(result.stdout, dtype='<f4').reshape(-1, 2)
        pivot = round((at-start)*RATE)
        if not 0 < pivot < len(samples):
            continue
        before, after = samples[:pivot], samples[pivot:]
        rms = lambda a: 20*math.log10(max(float(np.sqrt(np.mean(a.astype('float64')**2))), 1e-12))
        delta = abs(rms(before)-rms(after))
        jump = float(np.max(np.abs(samples[pivot]-samples[pivot-1])))
        for code, value, threshold in [('audio_level_jump', delta, rms_delta_db), ('audio_join_discontinuity', jump, jump_threshold)]:
            if value > threshold:
                issues.append({'code': code, 'severity': 'warning', 'event_ids': [left['clip_id'], right['clip_id']],
                    'at_seconds': at, 'measured': value, 'threshold': threshold,
                    'message': f'{code} at {at:.3f}s: {value:.4f} > {threshold}; inspect the join, not proof of audible distortion.'})
    return issues
