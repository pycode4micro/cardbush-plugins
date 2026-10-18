"""Queued, revision-bound cue evidence. Signal measurements never certify listening."""
from __future__ import annotations

import copy
import math
from pathlib import Path
import struct
import tempfile

import numpy as np

from . import audio, engine, jobs, processes, timing


def _compact(project):
    timeline = timing.resolve(project['mcp_timeline'])
    timeline.pop('history', None)
    timeline['callouts'] = []
    timeline['tracks'] = {name: rows for name, rows in timeline['tracks'].items()
                          if name == 'main' or timeline.get('track_meta', {}).get(name, {}).get('kind') == 'audio'}
    used = {row['asset_id'] for rows in timeline['tracks'].values() for row in rows}
    if timeline.get('audio', {}).get('bed_asset_id'):
        used.add(timeline['audio']['bed_asset_id'])
    return {'id': project['id'], 'mcp_timeline': timeline,
            'materials': [copy.deepcopy(a) for a in project['materials'] if a['id'] in used]}


def prepare_request(project_id, project, audio_ids, render_job_id, context_seconds):
    """Snapshot/stat only: no media decoding, media hashing, heavy slot or timeline mutation."""
    if isinstance(context_seconds, bool) or not isinstance(context_seconds, (int, float)) or not math.isfinite(context_seconds) or not .25 <= context_seconds <= 5:
        raise ValueError('context_seconds must be .25..5')
    delivery, saved_manifest = None, None
    if render_job_id is not None:
        root = jobs.folder(project_id, render_job_id)
        state = engine.read_json(root / 'state.json')
        if state['status'] != 'succeeded':
            raise ValueError('Audio review requires a succeeded render job')
        if jobs.digest(root / 'input.json') != state['input_sha256']:
            raise ValueError('Saved render input checksum mismatch')
        saved = engine.read_json(root / 'input.json')
        project, saved_manifest = saved['project'], saved['media']
        if state.get('verified_media_sha256'):
            if jobs.digest(root / 'verified_media.json') != state['verified_media_sha256']:
                raise ValueError('Verified media manifest checksum mismatch')
            saved_manifest = engine.read_json(root / 'verified_media.json')
        result = engine.read_json(root / 'result.json')
        output = Path(result['path']).resolve()
        delivery = {'job_id': render_job_id, 'path': str(output), 'stat': jobs.media_stat(output),
                    'sha256': None, 'saved_input_sha256': state['input_sha256']}
    snapshot = _compact(project)
    timeline = snapshot['mcp_timeline']
    if not 0 < timing.time_map(timeline)['duration'] <= 600:
        raise ValueError('Cue review requires a nonempty timeline of at most 600 seconds')
    entries = {e['id']: e for kind, e in timing.entries(timeline) if kind == 'audio'}
    if audio_ids is None:
        audio_ids = [key for key, e in entries.items() if e.get('role') == 'sfx']
    if not isinstance(audio_ids, list) or not 1 <= len(audio_ids) <= 16 or any(not isinstance(key, str) for key in audio_ids):
        raise ValueError('Select 1-16 audio IDs, or tag entries role=sfx first')
    if len(set(audio_ids)) != len(audio_ids) or any(key not in entries for key in audio_ids):
        raise ValueError('Audio IDs must be unique and exist in the selected revision')
    manifest = jobs.media_manifest(snapshot, defer=True) if saved_manifest is None else {
        a['id']: saved_manifest[a['id']] for a in snapshot['materials']}
    return {'operation': 'audio_review', 'schema_version': 1, 'project': snapshot,
            'media': manifest, 'delivery': delivery, 'audio_ids': audio_ids[:],
            'context_seconds': context_seconds}


def _decode(path, start, duration):
    result = processes.run([engine.ffmpeg_bin(), '-nostdin', '-v', 'error', '-ss', str(start),
        '-i', str(path), '-t', str(duration), '-vn', '-ar', str(audio.RATE), '-ac', '2',
        '-f', 'f32le', '-'], capture_output=True, check=True, timeout=120)
    samples = np.frombuffer(result.stdout, dtype='<f4').reshape(-1, 2).astype('float64')
    expected = round(duration * audio.RATE)
    if abs(len(samples) - expected) > 2 or not np.isfinite(samples).all():
        raise ValueError('Review window has missing or non-finite decoded audio')
    # Accommodate only floating-point trim rounding, never pad a missing phrase.
    return np.pad(samples[:expected], ((0, max(0, expected-len(samples))), (0, 0)))


def _write_wave(path, samples):
    """IEEE float WAV retains the original comparison gain, including peaks above 0 dBFS."""
    data = samples.astype('<f4').tobytes()
    fmt = struct.pack('<HHIIHH', 3, 2, audio.RATE, audio.RATE * 8, 8, 32)
    payload = b'WAVEfmt ' + struct.pack('<I', len(fmt)) + fmt
    payload += b'fact' + struct.pack('<II', 4, len(samples)) + b'data' + struct.pack('<I', len(data)) + data
    path.write_bytes(b'RIFF' + struct.pack('<I', len(payload)) + payload)


def _rms(samples):
    return float(np.sqrt(np.mean(samples ** 2))) if samples.size else 0.0


def _db(value):
    return round(20 * math.log10(value), 3) if value > 1e-12 else None


def _active_context(samples):
    blocks = len(samples) // 960  # 20 ms at 48 kHz; no voice detector is implied.
    if not blocks:
        return None
    levels = np.sqrt(np.mean(samples[:blocks*960].reshape(blocks, -1) ** 2, axis=1))
    active = levels[levels > .001]  # Exclude < -60 dBFS windows, including breaths/silence.
    return _db(float(np.percentile(active, 75))) if len(active) else None


def _delivery_evidence(delivered, full, without, contribution):
    cue = _rms(contribution)
    if cue < 1e-7:
        return {'signal_presence': 'not_testable', 'reason': 'No measurable planned contribution'}
    full_error, without_error = _rms(delivered-full) / cue, _rms(delivered-without) / cue
    # AAC noise, resampling, changed masters or runtimes may prevent a conclusion.
    presence = 'inconclusive'
    if full_error < .35 and without_error > .65:
        presence = 'confirmed'
    elif without_error < .35 and full_error > .65:
        presence = 'not_detected'
    return {'signal_presence': presence, 'error_vs_with_relative_to_cue': round(full_error, 4),
            'error_vs_without_relative_to_cue': round(without_error, 4)}


def execute(request, path, progress):
    """Called only inside the shared media-job heavy slot and cancellation context."""
    project = request['project']
    timeline = project['mcp_timeline']
    total = timing.time_map(timeline)['duration']
    progress('verify_sources', 0, 1)
    verified = jobs.verify_media(request['media'])
    delivery = request.get('delivery')
    delivered_identity = jobs.verify_media({'delivery': delivery})['delivery'] if delivery else None
    assets = {a['id']: a for a in project['materials']}
    entries = {e['id']: e for kind, e in timing.entries(timeline) if kind == 'audio'}
    cues = []
    for key in request['audio_ids']:
        entry = entries[key]
        probe = engine.ffprobe(Path(assets[entry['asset_id']]['path']))
        if not probe.get('has_audio'):
            raise ValueError(f'Cue {key} has no audio stream')
        speed = float(entry.get('playback_speed', 1))
        available = (float(probe['duration']) - float(entry.get('source_start', 0))) / speed
        duration = min(float(entry.get('duration') or available), available, total - entry['start'])
        if not math.isfinite(duration) or not 0 < duration <= 15:
            raise ValueError(f'Cue {key} needs 0 < effective duration <= 15 seconds')
        cues.append((entry, duration))
    report = {'schema_version': 1, 'project_id': project['id'], 'revision': timeline.get('revision', 0),
              'render_job_id': delivery['job_id'] if delivery else None, 'media': verified,
              'delivered_identity': delivered_identity, 'human_listened': False,
              'perceptual_review': 'needs_listening', 'delivery_checked': bool(delivery), 'cues': [],
              'scope': 'Per-cue marginal contribution after the common master. Includes limiter interactions; not speech isolation. Heuristics identify review windows, never perceptual approval.',
              'warning_thresholds': {'low_cue_rms_dbfs': -40, 'below_active_context_db': -14},
              'comparison': 'All samples share the full mix master gain. No independent A/B normalization. Delivered comparisons can be inconclusive after lossy encoding or runtime changes.'}
    with tempfile.TemporaryDirectory(prefix='cue-review-', dir=path) as temporary:
        root = Path(temporary)
        full_path = root / 'full.wav'
        progress('render_reference_mix', 0, len(cues))
        settings = audio.render(project, timeline, full_path)
        report['audio_settings'] = settings
        for index, (entry, duration) in enumerate(cues):
            processes.check()
            progress('compare_cues', index, len(cues))
            without_timeline = copy.deepcopy(timeline)
            for track, rows in without_timeline['tracks'].items():
                if without_timeline.get('track_meta', {}).get(track, {}).get('kind') == 'audio':
                    without_timeline['tracks'][track] = [e for e in rows if e['id'] != entry['id']]
            without_path = root / 'without.wav'
            audio.render(project, without_timeline, without_path, fixed_master_gain_db=settings['applied_master_gain_db'])
            start = max(0, entry['start'] - request['context_seconds'])
            end = min(total, entry['start'] + duration + request['context_seconds'])
            full = _decode(full_path, start, end-start)
            without = _decode(without_path, start, end-start)
            contribution = full - without
            begin, finish = round((entry['start']-start)*audio.RATE), round((entry['start']+duration-start)*audio.RATE)
            cue = contribution[begin:finish]
            cue_rms, cue_peak = _db(_rms(cue)), _db(float(np.max(np.abs(cue))))
            background_rms = _db(_rms(without[begin:finish]))
            context_rms = _active_context(without)
            relative = cue_rms-context_rms if cue_rms is not None and context_rms is not None else None
            warnings = []
            if entry.get('volume', 1) == 0:
                warnings.append('muted_cue')
            if cue_peak is None or cue_peak < -90:
                warnings.append('silent_or_negligible_contribution')
            if cue_rms is not None and cue_rms < -40:
                warnings.append('low_cue_level')
            if relative is not None and relative < -14:
                warnings.append('possible_masking_by_nearby_audio')
            if float(np.max(np.abs(full))) >= 1:
                warnings.append('mix_at_or_above_full_scale')
            sample_dir = path / f'cue-{index+1:02d}'
            sample_dir.mkdir(exist_ok=True)
            samples = {}
            for name, values in [('with', full), ('without', without), ('contribution', contribution)]:
                output = sample_dir / f'{name}.wav'
                _write_wave(output, values)
                samples[name] = str(output)
            evidence = {'signal_presence': 'unchecked'}
            if delivery:
                actual = _decode(delivery['path'], start, end-start)
                evidence = _delivery_evidence(actual[begin:finish], full[begin:finish], without[begin:finish], cue)
                output = sample_dir / 'delivered.wav'
                _write_wave(output, actual)
                samples['delivered'] = str(output)
                if evidence['signal_presence'] != 'confirmed':
                    warnings.append('delivered_cue_' + evidence['signal_presence'])
            report['cues'].append({'audio_id': entry['id'], 'asset_id': entry['asset_id'],
                'label': entry.get('label', ''), 'role': entry.get('role', 'unspecified'),
                'start': entry['start'], 'duration': duration, 'volume': entry.get('volume', 1),
                'window': {'start': start, 'end': end}, 'cue_rms_dbfs': cue_rms, 'cue_peak_dbfs': cue_peak,
                'other_audio_rms_dbfs': background_rms, 'other_audio_active_context_p75_dbfs': context_rms,
                'cue_minus_active_context_db': round(relative, 3) if relative is not None else None,
                'delivery': evidence, 'warnings': warnings, 'samples': samples,
                'human_listened': False, 'perceptual_review': 'needs_listening'})
    jobs.verify_media(verified, strong=False)
    if delivery:
        jobs.verify_media({'delivery': delivered_identity}, strong=False)
    report['warning_count'] = sum(len(c['warnings']) for c in report['cues'])
    report['report_path'] = str(path / 'audio-review.json')
    engine.write_json(Path(report['report_path']), report)
    progress('review_ready', len(cues), len(cues))
    return report
