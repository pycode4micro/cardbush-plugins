"""Artifact-bound editorial review. Measurements never certify editorial judgement."""
from datetime import datetime, timezone
import contextvars
from functools import wraps
import hashlib
import json
from pathlib import Path
import uuid

from . import engine, jobs, media_jobs, resources, timing
from .locking import file_lock

CHECKS = (
    'source_audio', 'source_frame_timestamps', 'hook_picture', 'hook_voice',
    'hook_body_continuity', 'selling_point_evidence', 'body_continuity',
    'typography', 'safe_layout', 'text_overlap', 'transition',
    'final_picture', 'final_speech', 'sfx_audibility',
)

_hashes = contextvars.ContextVar('review_hashes', default=None)


def scoped_hashes(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if _hashes.get() is not None:
            return function(*args, **kwargs)
        token = _hashes.set({})
        try:
            return function(*args, **kwargs)
        finally:
            _hashes.reset(token)
    return wrapped


def fingerprint(path):
    path = Path(path).expanduser().resolve()
    before = path.stat()
    cache = _hashes.get()
    key = (str(path), before.st_size, before.st_mtime_ns, before.st_ctime_ns)
    if cache is not None and key in cache:
        return dict(cache[key])
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('Review artifact changed while hashing')
    result = {'path': str(path), 'sha256': h.hexdigest(), 'size': after.st_size}
    if cache is not None:
        cache[key] = result
    return result


def artifact(project_id, render_job_id):
    folder = jobs.folder(project_id, render_job_id)
    state = engine.read_json(folder / 'state.json')
    if state['status'] != 'succeeded':
        raise ValueError('Review requires a succeeded render, not a submitted job')
    if fingerprint(folder / 'input.json')['sha256'] != state['input_sha256']:
        raise ValueError('Saved render input checksum mismatch')
    result = engine.read_json(folder / 'result.json')
    return {'project_id': project_id, 'render_job_id': render_job_id,
            'revision': state['revision'], 'preview': state['preview'],
            'input_sha256': state['input_sha256'],
            'output': fingerprint(result['path']),
            'result': fingerprint(folder / 'result.json')}, result


def applicable_checks(timeline):
    required = set(CHECKS)
    if not any(c.get('role') in ('hook', 'hook_picture') or c.get('hook_id') for c in timeline['tracks']['main']):
        required -= {'hook_picture', 'hook_voice', 'hook_body_continuity'}
    if not any(a.get('role') == 'sfx' for rows in timeline['tracks'].values() for a in rows):
        required.discard('sfx_audibility')
    return required


def normalize_checks(checks, required=None, pure_display=False, framing_rule=None):
    from . import hook_framing
    required = set(CHECKS) if required is None else required
    if not isinstance(checks, list) or len(checks) > len(CHECKS):
        raise ValueError('checks must be a list of named commerce review checks')
    rows = {}
    for item in checks:
        if not isinstance(item, dict) or set(item) - {'id', 'status', 'note', 'evidence_paths', 'repair_action', 'method', 'observations', 'review_receipt_paths'}:
            raise ValueError('Unexpected review check fields')
        name = item.get('id')
        if name not in CHECKS or name in rows:
            raise ValueError('Unknown or duplicate check ID')
        status = item.get('status')
        if status not in ('pass', 'fail', 'not_checked'):
            raise ValueError('Check status must be pass, fail or not_checked')
        note = item.get('note', '')
        repair = item.get('repair_action', '')
        method = item.get('method', 'unspecified')
        if method not in ('unspecified', 'measurement', 'frame_review', 'continuous_review', 'listening', 'user_feedback', 'not_applicable'):
            raise ValueError('Unknown review method')
        if status == 'pass' and method == 'unspecified':
            raise ValueError('A passing check requires an explicit review method')
        if status == 'pass' and method == 'not_applicable' and name in required:
            raise ValueError(name + ' is required by the actual timeline; not_applicable cannot waive it')
        if status == 'pass' and name in ('hook_picture', 'hook_body_continuity', 'body_continuity', 'final_picture') and method not in ('continuous_review', 'user_feedback', 'not_applicable'):
            raise ValueError(name + ' requires continuous review, not a labelled still or measurement')
        observations = item.get('observations', {})
        if not isinstance(observations, dict): raise ValueError('observations must be an object')
        if pure_display and status == 'pass' and name == 'hook_picture' and method != 'not_applicable':
            needed = (hook_framing.fact(framing_rule), 'natural_display', 'no_continuous_speaking', 'burned_captions_compatible')
            if any(observations.get(key) is not True for key in needed):
                raise ValueError('Pure display requires observed true facts: ' + ', '.join(needed))
        if (status == 'pass' and name in ('hook_voice', 'final_speech', 'sfx_audibility')
                and method not in ('listening', 'user_feedback', 'not_applicable')):
            raise ValueError('Auditory approval requires explicit listening/user_feedback, not signal measurements or ASR')
        paths = item.get('evidence_paths', [])
        if not isinstance(note, str) or not note.strip() or len(note) > 4000:
            raise ValueError('Every check requires a specific note, max 4000 characters')
        if status == 'pass' and note == 'Not reviewed yet':
            raise ValueError('Replace the unchecked form note with an actual observation before passing')
        if not isinstance(paths, list) or len(paths) > 12 or any(not isinstance(p, str) for p in paths):
            raise ValueError('evidence_paths must contain up to 12 local file paths')
        if status in ('pass', 'fail') and not paths:
            raise ValueError('A pass/fail judgement requires inspectable evidence')
        if status == 'fail' and (not isinstance(repair, str) or not repair.strip()):
            raise ValueError('Failed checks require a concrete repair_action')
        from . import review_evidence
        receipts, receipt_files = review_evidence.load(item.get('review_receipt_paths', []))
        if status == 'pass' and method in ('continuous_review', 'listening', 'user_feedback', 'frame_review') and not receipts:
            status = 'not_checked'
            note = 'Actual review receipt missing. Requested conclusion: ' + note
        rows[name] = {'id': name, 'status': status, 'note': note, 'method': method,
                      'observations': observations, 'repair_action': repair, 'receipts': receipts,
                      'evidence': [fingerprint(p) for p in paths] + receipt_files}
    return [rows.get(name, {'id': name, 'status': 'not_checked', 'note': 'No review supplied',
                            'repair_action': '', 'evidence': []}) for name in CHECKS]


def inspection_submit(project_id, render_job_id):
    identity, _ = artifact(project_id, render_job_id)
    for job in reversed(media_jobs.records(project_id)):
        if job['operation'] == 'delivery_inspect' and job['status'] in ('queued', 'running', 'succeeded'):
            request = engine.read_json(media_jobs.folder(project_id, job['job_id']) / 'request.json')
            if request.get('artifact') == identity:
                if job['status'] == 'succeeded':
                    try:
                        inspection_findings(project_id, identity, job['job_id'])
                    except (ValueError, OSError):
                        continue  # Keep stale evidence; create a new inspection instead of a permanent retry loop.
                return {**media_jobs.status(project_id, job['job_id']), 'reused': True}
    return media_jobs.submit(project_id, {'operation': 'delivery_inspect', 'render_job_id': render_job_id,
        'artifact': identity, 'inspection_asset_id': 'mat_' + uuid.uuid4().hex[:16]})


def inspection_findings(project_id, identity, job_id):
    if not job_id:
        return [{'code': 'missing_render_inspection', 'severity': 'review',
                 'message': 'Queue delivery_inspection_submit for this output; arbitrary evidence files cannot certify delivery'}], []
    job = media_jobs.status(project_id, job_id)
    if job['status'] != 'succeeded' or job['operation'] != 'delivery_inspect':
        raise ValueError('A succeeded delivery_inspect job is required')
    report = job['result']
    if report['artifact'] != identity: raise ValueError('Inspection belongs to different rendered bytes/revision')
    evidence = [fingerprint(media_jobs.folder(project_id, job_id) / 'result.json')]
    for f in report['files']:
        if fingerprint(f['path']) != f: raise ValueError('Inspection evidence changed')
        evidence.append(f)
    findings = []
    if report['levels'].get('sample_peak_db') is None:
        findings.append({'code': 'silent_final_audio', 'severity': 'fail', 'message': 'Final output has no decoded audio signal'})
    for row in report['source_ranges']:
        scenes = row.get('internal_cuts')
        if scenes and scenes.get('requires_review'):
            findings.append({'code': 'internal_cuts:' + row['clip_id'], 'severity': 'review',
                             'message': 'Review short shots inside the selected source range',
                             'source_ranges': scenes['short_shots']})
        if row.get('role') == 'hook_picture': continue
        if row['audio_eligibility'] != 'audible_signal':
            findings.append({'code': 'source_audio:' + row['clip_id'], 'severity': 'fail',
                             'message': 'Selected body source is silent or unresolved; background sound cannot qualify it'})
    for row in report['final_segments']:
        if row['levels'].get('sample_peak_db') is None:
            findings.append({'code': 'silent_final_segment:' + row['clip_id'], 'severity': 'fail',
                             'message': 'This rendered segment is silent; whole-output average is insufficient'})
    findings.extend(report.get('typography_findings', []))
    return findings, evidence


@resources.heavy_operation
@scoped_hashes
def form(project_id, render_job_id, inspection_job_id):
    identity, _ = artifact(project_id, render_job_id)
    findings, _ = inspection_findings(project_id, identity, inspection_job_id)
    snapshot = engine.read_json(jobs.folder(project_id, render_job_id) / 'input.json')
    timeline = snapshot['project']['mcp_timeline']; required = applicable_checks(timeline)
    from . import hook_framing
    evidence_path = str(media_jobs.folder(project_id, inspection_job_id) / 'result.json')
    report = media_jobs.status(project_id, inspection_job_id)['result']
    rows = []
    for name in CHECKS:
        method = ('listening' if name in ('hook_voice','final_speech','sfx_audibility') else
                  'measurement' if name in ('source_audio','source_frame_timestamps') else
                  'continuous_review' if name in ('hook_picture','hook_body_continuity','body_continuity','final_picture') else 'frame_review')
        row = {'id': name, 'status': 'not_checked', 'method': method if name in required else 'not_applicable',
               'note': 'Not reviewed yet', 'evidence_paths': [evidence_path], 'review_receipt_paths': [], 'repair_action': ''}
        if name == 'hook_picture' and any(c.get('role') == 'hook_picture' for c in timeline['tracks']['main']):
            row['observations'] = {k: False for k in (hook_framing.fact(timeline.get('commerce_hook_framing')),'natural_display','no_continuous_speaking','burned_captions_compatible')}
        rows.append(row)
    return {'project_id': project_id, 'render_job_id': render_job_id, 'inspection_job_id': inspection_job_id,
            'checks': rows, 'technical_findings': findings, 'frames': report.get('frames'),
            'listening_path': report.get('listening_path'), 'next_tool': 'delivery_review_record',
            'instruction': 'Open the actual evidence. Change only observed conclusions; unknown stays not_checked. Supply notes for every submitted check. Source/measurement evidence does not certify visual or listening quality.'}


def subject_signatures(project):
    """Content signatures exclude revision numbers, IDs, labels and unrelated edits."""
    timeline = timing.resolve(project['mcp_timeline'])
    assets = {a['id']: a.get('identity', {}).get('sha256', a.get('path')) for a in project.get('materials', [])}
    def clean(value):
        if isinstance(value, list): return [clean(v) for v in value]
        if isinstance(value, dict):
            return {k: (assets.get(v, v) if k == 'asset_id' else clean(v)) for k, v in value.items()
                    if k not in ('id', 'revision', 'history', 'role', 'shot_type', 'commerce_candidate_id', 'label')}
        return value
    main = timeline['tracks']['main']
    visual_main = [{k: v for k, v in clip.items() if not k.startswith('audio_')} for clip in main]
    audio_keys = ('asset_id', 'start', 'end', 'playback_speed', 'transition', 'audio_mute',
                  'audio_gain_db', 'audio_fade_in_ms', 'audio_fade_out_ms', 'frame_alignment')
    sound_main = [{k: clip[k] for k in audio_keys if k in clip} for clip in main]
    picture = {'main': visual_main, 'canvas': timeline.get('canvas'), 'overlays': timeline['tracks'].get('overlays'),
               'captions': timeline['tracks'].get('captions'), 'callouts': timeline.get('callouts')}
    sound = {'main': sound_main, 'audio': timeline['tracks'].get('audio'), 'config': timeline.get('audio_config')}
    subjects = {name: {'picture': picture, 'sound': sound} for name in CHECKS}
    subjects['hook_picture'] = {'first_clip': visual_main[:1], 'canvas': timeline.get('canvas')}
    if 'commerce_hook_framing' in timeline:
        subjects['hook_picture']['framing'] = timeline['commerce_hook_framing']
    hook_end = timing.time_map(timeline)['clips'][0]['end'] if main else 0
    hook_audio = [a for a in timeline['tracks'].get('audio', [])
                  if a.get('start', 0) < hook_end and a.get('start', 0) + a.get('duration', 0) > 0]
    subjects['hook_voice'] = {'main': sound_main[:1], 'audio': hook_audio, 'config': timeline.get('audio_config')}
    for name in ('final_speech', 'sfx_audibility'): subjects[name] = sound
    for name in ('source_audio', 'source_frame_timestamps'):
        subjects[name] = [{k: c[k] for k in ('asset_id', 'start', 'end') if k in c} for c in main]
    subjects['transition'] = visual_main
    for name in ('typography', 'safe_layout', 'text_overlap', 'final_picture'): subjects[name] = picture
    return {name: hashlib.sha256(json.dumps(clean(value), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            for name, value in subjects.items()}


def audio_findings(project_id, render_job_id, identity, job_ids):
    if not isinstance(job_ids, list) or len(job_ids) > 64 or any(not isinstance(j, str) for j in job_ids) or len(set(job_ids)) != len(job_ids):
        raise ValueError('Provide up to 64 distinct audio review job IDs')
    findings, evidence, covered = [], [], set()
    for job_id in job_ids:
        status = media_jobs.status(project_id, job_id)
        if status['status'] != 'succeeded' or status['operation'] != 'audio_review':
            raise ValueError('audio_review_job_ids requires succeeded audio_review jobs')
        report = status['result']
        delivered = report.get('delivered_identity') or {}
        if (report.get('project_id') != project_id or report.get('render_job_id') != render_job_id or report.get('revision') != identity['revision']
                or delivered.get('saved_input_sha256') != identity['input_sha256']
                or delivered.get('sha256') != identity['output']['sha256']):
            raise ValueError('Audio review does not match this rendered artifact')
        evidence.append(fingerprint(media_jobs.folder(project_id, job_id) / 'result.json'))
        for cue in report.get('cues', []):
            cue_id = cue['audio_id']
            covered.add(cue_id)
            if (cue.get('delivery') or {}).get('signal_presence') != 'confirmed':
                findings.append({'code': 'sfx_signal:' + cue_id, 'severity': 'fail',
                                 'message': 'Planned cue signal was not confirmed in this render'})
            for warning in cue.get('warnings', []):
                findings.append({'code': 'sfx_warning:' + cue_id + ':' + str(warning), 'severity': 'review',
                                 'message': str(warning)})
    return findings, evidence, covered


def verdict(record):
    failed = [c['id'] for c in record['checks'] if c['status'] == 'fail']
    missing = [c['id'] for c in record['checks'] if c['status'] == 'not_checked']
    resolved = {r['code'] for r in record['warning_resolutions']}
    hard = [f['code'] for f in record['technical_findings'] if f['severity'] == 'fail']
    pending = [f['code'] for f in record['technical_findings'] if f['severity'] == 'review' and f['code'] not in resolved]
    state = 'needs_repair' if failed or hard else 'needs_review' if missing or pending else 'ready'
    if record['artifact']['preview'] and state == 'ready':
        state = 'preview_reviewed'
    return {'status': state, 'delivery_ready': state == 'ready', 'failed_checks': failed + hard,
            'pending_checks': missing + pending}


@resources.heavy_operation
@scoped_hashes
def record(project_id, render_job_id, checks, audio_review_job_ids=None, warning_resolutions=None, inspection_job_id=None):
    identity, result = artifact(project_id, render_job_id)
    snapshot = engine.read_json(jobs.folder(project_id, render_job_id) / 'input.json')
    timeline = snapshot['project']['mcp_timeline']
    checked = normalize_checks(checks, applicable_checks(timeline),
        any(c.get('role') == 'hook_picture' for c in timeline['tracks']['main']), timeline.get('commerce_hook_framing'))
    findings = []
    if not result.get('has_audio'):
        findings.append({'code': 'no_final_audio', 'severity': 'fail', 'message': 'Rendered audio stream missing'})
    for category in ('audio', 'video_stream'):
        for warning in result.get(category, {}).get('warnings', []):
            findings.append({'code': category + ':' + str(warning), 'severity': 'review', 'message': str(warning)})
    for warning in result.get('warnings', []):
        code = warning.get('code', str(warning)) if isinstance(warning, dict) else str(warning)
        findings.append({'code': 'render:' + code, 'severity': 'review', 'message': str(warning)})
    audio_issues, audio_files, covered = audio_findings(project_id, render_job_id, identity, audio_review_job_ids or [])
    findings.extend(audio_issues)
    inspection_issues, inspection_files = inspection_findings(project_id, identity, inspection_job_id)
    findings.extend(inspection_issues)
    if inspection_job_id:
        allowed = {(f['path'], f['sha256']) for f in inspection_files + audio_files + [identity['output']]}
        for check in checked:
            if check['status'] == 'pass' and check['method'] != 'not_applicable' and not any(
                (f['path'], f['sha256']) in allowed for f in check['evidence']):
                raise ValueError('Passing check must cite this inspection, its media, or matching audio review: ' + check['id'])
    from . import commerce
    if commerce.path(project_id).is_file():
        state = commerce.load(project_id)
        selection = state.get('selection') or {}
        pending_preview = selection.get('preview_only', False)
        findings.extend({'code': 'workflow:' + str(i), 'severity': 'fail', 'message': message}
                        for i, message in enumerate(commerce.preflight(project_id, snapshot['project'], allow_pending=pending_preview)))
        for job in selection.get('pictures', []) + ([selection['voice']] if selection.get('voice') else []):
            if state['reviews'].get(job, {}).get('status') == 'pending':
                findings.append({'code': 'candidate_pending:' + job, 'severity': 'review',
                                 'message': 'Candidate visual/listening review is incomplete; target-quality export is not approval'})
        findings.extend(commerce.presentation_findings(timeline, state.get('presentation')))
    # A receipt must cover this actual output, not a source, ASR report or another
    # render. Matching audio-only inspection files use the same output clock.
    from . import review_evidence
    picture_hashes = {identity['output']['sha256']}
    sound_hashes = set(picture_hashes)
    if inspection_job_id:
        report = media_jobs.status(project_id, inspection_job_id)['result']
        if report.get('listening_path'):
            sound_hashes.add(fingerprint(report['listening_path'])['sha256'])
    intervals = timing.time_map(timeline)
    duration = intervals['duration']
    hook_end = intervals['clips'][0]['end'] if intervals['clips'] else duration
    for check in checked:
        if check['status'] != 'pass' or check['method'] not in ('continuous_review', 'listening', 'user_feedback', 'frame_review'):
            continue
        end = hook_end if check['id'] in ('hook_picture', 'hook_voice') else duration
        start = hook_end if check['id'] == 'body_continuity' else 0
        hashes = sound_hashes if check['method'] == 'listening' else picture_hashes
        if not review_evidence.covers(check.get('receipts', []), check['method'], hashes, start, end):
            check.update(status='not_checked', note='Review receipt does not cover this output/range: ' + check['note'])
    if any(f['code'].startswith('typography_unchecked:') for f in findings):
        next(c for c in checked if c['id'] == 'typography').update(status='not_checked',
            note='Typography size/theme checks are incomplete: missing metadata')
    planned = {a['id'] for track in timeline['tracks'].values() for a in track if a.get('role') == 'sfx'}
    for cue in sorted(planned - covered):
        findings.append({'code': 'sfx_unmeasured:' + cue, 'severity': 'review', 'message': 'No matching rendered cue review'})
    resolutions = []
    if warning_resolutions is not None and (not isinstance(warning_resolutions, list) or len(warning_resolutions) > 128):
        raise ValueError('warning_resolutions must contain up to 128 specific resolutions')
    known = {f['code'] for f in findings if f['severity'] == 'review' and f['code'] != 'missing_render_inspection'
             and not f['code'].startswith(('sfx_unmeasured:', 'candidate_pending:', 'typography_unchecked:'))}
    for resolution in warning_resolutions or []:
        if (not isinstance(resolution, dict) or set(resolution) != {'code', 'note', 'evidence_paths'}
                or resolution['code'] not in known or not isinstance(resolution['note'], str)
                or resolution['code'] in {r['code'] for r in resolutions}
                or not resolution['note'].strip() or len(resolution['note']) > 4000 or not isinstance(resolution['evidence_paths'], list)
                or not 1 <= len(resolution['evidence_paths']) <= 12):
            raise ValueError('Each warning resolution requires a known code, explanation and evidence files')
        # Masking warnings cannot be waived while listening remains unchecked.
        if resolution['code'].startswith('sfx_warning:') and next(c for c in checked if c['id'] == 'sfx_audibility')['status'] != 'pass':
            raise ValueError('Review cue audibility before resolving an audio warning')
        if resolution['code'].startswith('internal_cuts:') and next(c for c in checked if c['id'] == 'body_continuity')['status'] != 'pass':
            raise ValueError('Actual continuous review is required before resolving internal-cut signals')
        resolutions.append({'code': resolution['code'], 'note': resolution['note'],
                            'evidence': [fingerprint(p) for p in resolution['evidence_paths']]})
    subjects = subject_signatures(snapshot['project'])
    # Keep receipts by fingerprint/reference, not repeated full payloads in
    # every check and every subsequent status response.
    for check in checked:
        check.pop('receipts', None)
    item = {'schema_version': 3, 'profile': 'commerce-v3', 'id': 'review_' + uuid.uuid4().hex[:16],
            'created_at': datetime.now(timezone.utc).isoformat(), 'artifact': identity,
            'inspection_job_id': inspection_job_id, 'inspection_evidence': inspection_files, 'check_subjects': subjects,
            'checks': checked, 'technical_findings': findings, 'audio_evidence': audio_files,
            'warning_resolutions': resolutions,
            'scope': 'Caller-authored editorial observations; the plugin does not see or hear aesthetics.'}
    folder = engine.project_dir(project_id) / 'delivery_reviews'
    folder.mkdir(exist_ok=True)
    with file_lock(folder / 'write.lock'):
        prior = sorted([engine.read_json(p) for p in folder.glob('review_*.json')], key=lambda r: r['created_at'])
        # A second form submission must not erase a known editorial failure.
        for previous in prior:
            failed = {c['id'] for c in previous['checks'] if c['status'] == 'fail'}
            for check in checked:
                if check['id'] not in failed or check['status'] != 'pass': continue
                if (previous['artifact']['revision'] >= identity['revision'] or
                    previous['artifact']['output']['sha256'] == identity['output']['sha256'] or
                    previous.get('check_subjects', {}).get(check['id']) == subjects[check['id']]):
                    raise ValueError('Known failure requires changed output AND a repaired relevant subject, not a revision bump or unrelated edit: ' + check['id'])
        item['previous_review_id'] = prior[-1]['id'] if prior else None
        engine.write_json(folder / (item['id'] + '.json'), item)
    return get(project_id, render_job_id)


@resources.heavy_operation
@scoped_hashes
def get(project_id, render_job_id):
    identity, _ = artifact(project_id, render_job_id)
    folder = engine.project_dir(project_id) / 'delivery_reviews'
    history = [engine.read_json(p) for p in folder.glob('review_*.json')]
    matching = sorted([r for r in history if r['artifact']['render_job_id'] == render_job_id], key=lambda r: r['created_at'])
    if not matching:
        return {'status': 'needs_review', 'delivery_ready': False, 'artifact': identity,
                'pending_checks': list(CHECKS), 'history_count': len(history)}
    item = matching[-1]
    if item.get('schema_version', 1) < 3:
        return {**item, 'status': 'needs_review', 'delivery_ready': False,
                'pending_checks': ['legacy_review_requires_actual_review_receipts'], 'history_count': len(history)}
    stale = identity != item['artifact']
    current = engine.read_json(engine.project_dir(project_id) / 'project.json')
    stale |= current['mcp_timeline']['revision'] != identity['revision']
    snapshot = engine.read_json(jobs.folder(project_id, render_job_id) / 'input.json')
    stale |= current['mcp_timeline'] != snapshot['project']['mcp_timeline']
    files = item['audio_evidence'] + item.get('inspection_evidence', []) + [f for c in item['checks'] for f in c['evidence']] + [f for r in item['warning_resolutions'] for f in r['evidence']]
    for file in files:
        try:
            stale |= fingerprint(file['path']) != file
        except (OSError, ValueError):
            stale = True
    return {**item, **verdict(item), **({'status': 'stale', 'delivery_ready': False} if stale else {}),
            'history_count': len(history)}
