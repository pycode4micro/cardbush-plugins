"""Small batch entry points over the existing durable, single-heavy-task queue."""
import time
from . import commerce, engine, media_jobs, media_ops, speech
from .locking import file_lock


def distinct(values, name, maximum=64):
    if (not isinstance(values, list) or not 1 <= len(values) <= maximum
            or any(not isinstance(v, str) for v in values) or len(set(values)) != len(values)):
        raise ValueError(f'{name} requires 1–{maximum} distinct IDs')


def compact(job):
    keys = ('job_id', 'status', 'operation', 'progress', 'error', 'retry_of', 'reused',
            'next_tool', 'required_action', 'resource_wait')
    return {k: job[k] for k in keys if k in job}


def materials_prepare(project_id, asset_ids, language='zh', device='auto'):
    distinct(asset_ids, 'asset_ids', 16)
    commerce.load(project_id)
    requests = []
    for ident in asset_ids:
        asset = media_ops.asset(project_id, ident)
        if asset['kind'] != 'video':
            raise ValueError('Working materials must be registered videos')
        start = 0
        while start < asset['probe']['duration']:
            end = min(start + speech.MAX_WINDOW, asset['probe']['duration'])
            requests.append(speech.transcription_request(project_id, ident, start, end, language, device))
            start = end
    if len(requests) > 64:
        raise ValueError('Batch exceeds 64 ASR windows; choose a smaller working material pool')
    # Validate the complete batch first. Dispatch one worker, never one per file.
    jobs = [compact(speech._submit(project_id, request, start_worker=False)) for request in requests]
    with file_lock(engine.project_dir(project_id) / 'writer.lock'):
        state = commerce.load(project_id)
        state['analysis_asset_ids'] = list(dict.fromkeys(state.get('analysis_asset_ids', []) + asset_ids))
        commerce.save(project_id, state)
    if any(j['status'] == 'queued' for j in jobs):
        media_jobs.start()
    return {'project_id': project_id, 'jobs': jobs, 'next_tool': 'media_jobs_wait',
            'next_arguments': {'project_id': project_id, 'job_ids': [j['job_id'] for j in jobs]},
            'scope': 'Transcription only; range sound/scene analysis follows source-bound candidate selection. '
                     'No edits or semantic approvals. Add more material only if this pool is insufficient.'}


def candidates_submit(project_id, candidates):
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= 32:
        raise ValueError('Supply 1–32 candidate objects')
    prepared = []
    for row in candidates:
        if not isinstance(row, dict) or set(row) - {'role', 'asset_id', 'start', 'end', 'transcript_job_id', 'sentence_ids'}:
            raise ValueError('Unexpected candidate fields')
        if row.get('role') == 'hook_picture':
            args = dict(row)
        else:
            binding = speech.range_validate(project_id, row.get('transcript_job_id'), row.get('sentence_ids'))
            args = {**row, 'asset_id': binding['asset_id'], 'start': binding['start'], 'end': binding['end']}
        commerce.candidate_request(project_id, **args)
        prepared.append(args)
    jobs = [commerce.candidate_submit(project_id, **args, start_worker=False) for args in prepared]
    if any(j['status'] == 'queued' for j in jobs):
        media_jobs.start()
    return {'jobs': [compact(j) for j in jobs], 'next_tool': 'media_jobs_wait',
            'next_arguments': {'project_id': project_id, 'job_ids': list(dict.fromkeys(j['job_id'] for j in jobs))}}


def wait(project_id, job_ids, timeout_seconds=20):
    distinct(job_ids, 'job_ids')
    if type(timeout_seconds) not in (int, float) or not 0 <= timeout_seconds <= 30:
        raise ValueError('timeout_seconds must be 0–30')
    for jid in job_ids:
        media_jobs.folder(project_id, jid)
    deadline = time.monotonic() + timeout_seconds
    finished = {}
    while True:
        jobs = [finished.get(jid) or media_jobs.status(project_id, jid) for jid in job_ids]
        finished.update({j['job_id']: j for j in jobs if j['status'] not in ('queued', 'running')})
        active = [j for j in jobs if j['status'] in ('queued', 'running')]
        failed = [j for j in jobs if j['status'] in ('failed', 'cancelled', 'interrupted')]
        if not active or failed or time.monotonic() >= deadline:
            break
        time.sleep(min(.25, max(0, deadline-time.monotonic())))
    rows = []
    for job in jobs:
        row = compact(job)
        if job['status'] == 'succeeded':
            row['result_path'] = str(media_jobs.folder(project_id, job['job_id']) / 'result.json')
            if job['operation'] == 'speech_transcribe':
                row['candidates'] = speech.candidates_get(project_id, job['job_id'], limit=6)
            elif job['operation'] == 'commerce_inspect':
                report = job['result']
                row.update(role=report['role'], preview_path=report['preview']['path'],
                           audio_eligibility=report['audio_eligibility'], internal_cuts=report.get('internal_cuts'),
                           required_facts=list(commerce.hook_framing.keys(project_id, report['role'])),
                           next_tool='commerce_candidate_review')
        if job in failed:
            row.update(next_tool='media_job_retry',
                       next_arguments={'project_id': project_id, 'job_id': job['job_id']})
        rows.append(row)
    return {'project_id': project_id, 'complete': not active, 'jobs': rows,
            'next_tool': 'media_job_retry' if failed else 'media_jobs_wait' if active else None,
            'scope': 'Compact status, not approval. Sentence lists are paginated; read next_offset before assuming exhaustion.'}
