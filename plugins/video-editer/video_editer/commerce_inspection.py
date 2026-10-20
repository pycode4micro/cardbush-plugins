"""Queued evidence for the commerce workflow. Never infers speech or visual meaning."""
import copy
import wave
from pathlib import Path

from . import engine, media_ops, media_store, audio, processes, analysis_cache, scene_analysis


def range_evidence(project_id, asset_id, start, end, directory, progress):
    directory.mkdir(parents=True, exist_ok=True)
    asset = media_ops.asset(project_id, asset_id, strong=True, progress=progress)
    start, end = media_store.bounds(start, end, asset['probe']['duration'])
    if end - start > 60:
        raise ValueError('Review one complete unit at a time, at most 60 seconds')
    identity = {'source': asset['identity']['sha256'], 'probe': asset['probe'], 'start': start, 'end': end,
                'runtime': media_ops.cache_key(asset, 'source_range', {}),
                'implementation': analysis_cache.implementation('commerce_inspection.py', 'scene_analysis.py', 'audio.py')}
    cached = analysis_cache.read('source_range', identity)
    if cached:
        cached.update(asset_id=asset_id, cache_hit=True)
        for name in ('preview', 'frames'):
            cached[name]['asset_id'] = asset_id
        return cached
    preview_dir = directory / 'preview'; preview_dir.mkdir()
    preview = media_ops.prepare(project_id, {'asset_id': asset_id, 'operation': 'preview',
        'options': {'start': start, 'end': end, 'max_edge': 640}}, preview_dir, progress)
    frames_dir = directory / 'frames'; frames_dir.mkdir()
    stamps = [start, (start + end) / 2, max(start, end - .12)]
    frames = media_ops.prepare(project_id, {'asset_id': asset_id, 'operation': 'frames',
        'options': {'timestamps': stamps, 'max_edge': 640}}, frames_dir, progress)
    levels = audio.measure(Path(preview['path'])) if asset['probe'].get('has_audio') else {}
    peak = levels.get('sample_peak_db')
    eligibility = ('silent' if not asset['probe'].get('has_audio') or peak is None else
                   'pending' if peak < -50 else 'audible_signal')
    scenes = scene_analysis.inspect(asset['path'], start, end, progress)
    if media_ops.stat_signature(Path(asset['path'])) != asset['identity']['stat']:
        raise ValueError('Source changed during candidate analysis')
    result = {'asset_id': asset_id, 'source_sha256': asset['identity']['sha256'],
            'source_start': start, 'source_end': end, 'preview': preview,
            'frames': frames, 'audio': levels, 'audio_eligibility': eligibility,
            'internal_cuts': scenes, 'cache_hit': False,
            'scope': 'Decoded signal and source timestamps only. Review the continuous preview and listen; no semantic approval.'}
    analysis_cache.write('source_range', identity, result, preview['files'] + frames['files'])
    return result


def execute(project_id, request, directory, progress):
    if request['operation'] == 'commerce_inspect':
        from . import speech
        speech.verify_binding(project_id, request.get('speech_binding'), request['asset_id'],
                              request['start'], request['end'], request['role'])
        result = range_evidence(project_id, request['asset_id'], request['start'], request['end'], directory / 'candidate', progress)
        return {**result, 'role': request['role'], 'project_id': project_id,
                'operation': 'commerce_inspect', 'request': copy.deepcopy(request)}
    from . import delivery_review, jobs
    identity, rendered = delivery_review.artifact(project_id, request['render_job_id'])
    if identity != request['artifact']:
        raise ValueError('Rendered artifact changed before inspection')
    # Inspection must not mutate the edit or stale the very render being checked.
    imported = {'id': request['inspection_asset_id'], 'kind': 'video', 'path': rendered['path'],
                'probe': media_ops.metadata(Path(rendered['path'])),
                'identity': {'sha256': identity['output']['sha256'],
                             'stat': media_ops.stat_signature(Path(rendered['path']))}}
    duration = imported['probe']['duration']
    if duration > 600:
        raise ValueError('Delivery inspection is limited to 600 seconds')
    frames_dir = directory / 'frames'; frames_dir.mkdir()
    stamps = sorted(set([0, min(1, duration / 2), max(0, duration - .12)] +
                        [duration * n / 24 for n in range(1, 24)]))
    frames = media_ops.prepare(project_id, {'asset_id': imported['id'], 'operation': 'frames',
        'options': {'timestamps': stamps, 'max_edge': 640}}, frames_dir, progress, evidence_asset=imported)
    soundtrack = directory / 'listening.wav'
    levels = {}
    if imported['probe'].get('has_audio'):
        processes.run([engine.ffmpeg_bin(), '-nostdin', '-n', '-i', rendered['path'],
                       '-vn', '-c:a', 'pcm_s16le', str(soundtrack)], capture_output=True, check=True, timeout=180)
        levels = audio.measure(soundtrack)
    # Source qualification is checked on each selected range, not whole-file average loudness.
    snapshot = engine.read_json(jobs.folder(project_id, request['render_job_id']) / 'input.json')
    timeline = snapshot['project']['mcp_timeline']
    source_ranges = []
    for index, clip in enumerate(timeline['tracks']['main']):
        processes.check()
        evidence = range_evidence(project_id, clip['asset_id'], clip['start'], clip['end'],
                                  directory / ('source-%02d' % index), progress)
        source_ranges.append({'clip_id': clip['id'], 'role': clip.get('role'), **evidence})
    from . import timing
    intervals = timing.time_map(timeline)['clips']
    final_segments = []
    if imported['probe'].get('has_audio'):
        for index, interval in enumerate(intervals):
            path = directory / ('segment-%02d.wav' % index)
            # Decode the output once; all segment checks use this exact PCM clock.
            with wave.open(str(soundtrack), 'rb') as source:
                rate = source.getframerate()
                first = min(source.getnframes(), round(interval['start'] * rate))
                count = max(0, round(interval['end'] * rate) - first)
                source.setpos(first)
                with wave.open(str(path), 'wb') as output:
                    output.setparams(source.getparams())
                    output.writeframes(source.readframes(count))
            final_segments.append({'clip_id': interval['clip_id'], 'start': interval['start'],
                                   'end': interval['end'], 'levels': audio.measure(path),
                                   'file': delivery_review.fingerprint(path)})
    files = [delivery_review.fingerprint(Path(f['path'])) for f in frames['files']]
    if soundtrack.is_file(): files.append(delivery_review.fingerprint(soundtrack))
    for row in source_ranges:
        files.extend(delivery_review.fingerprint(Path(f['path'])) for kind in ('preview', 'frames') for f in row[kind]['files'])
    files.extend(row['file'] for row in final_segments)
    from . import commerce
    presentation = commerce.load(project_id).get('presentation') if commerce.path(project_id).is_file() else None
    return {'operation': 'delivery_inspect', 'project_id': project_id, 'artifact': identity,
            'typography_findings': commerce.presentation_findings(timeline, presentation),
            'frames': frames, 'listening_path': str(soundtrack) if soundtrack.exists() else None,
            'levels': levels, 'source_ranges': source_ranges, 'final_segments': final_segments,
            'files': files, 'report_path': str(directory / 'result.json'),
            'reviewed_by_agent': False, 'scope': 'Measurements and viewable evidence; visual/listening judgements still required.'}
