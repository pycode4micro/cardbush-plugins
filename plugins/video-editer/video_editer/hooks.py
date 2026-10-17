"""Durable, account-scoped handoff for externally generated opening hooks.

This module does not call a model or upload media. The host uses the connected
provider tools, records their receipts, and reviews the actual returned video.
"""
import copy
import hashlib
import json
import re
import time
import uuid
from urllib.parse import urlsplit

from . import engine, media_ops, media_store, timing
from .locking import file_lock


def identifier(value, label):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,119}', value):
        raise ValueError(f'Invalid {label}')
    return value


def text(value, label, maximum=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f'{label} requires nonempty text, at most {maximum} characters')
    return value


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def safe_receipt(value):
    """Receipts carry identifiers/evidence, never bearer URLs or credentials."""
    if isinstance(value, dict):
        return {str(k): ('[redacted]' if any(s in str(k).lower() for s in
                 ('secret', 'token', 'authorization', 'api_key')) else safe_receipt(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [safe_receipt(v) for v in value]
    if isinstance(value, str):
        return re.sub(r'https?://[^\s<>"\']+', lambda m:
                      '[url sha256:' + hashlib.sha256(m[0].encode()).hexdigest()[:16] + ']', value)
    return value


def account_path():
    root = engine.DATA / 'hook_accounts'
    root.mkdir(parents=True, exist_ok=True)
    return root


def account_get(account_id=None, version=None):
    root = account_path()
    if account_id is None:
        return {'accounts': [{'account_id': p.stem, 'version': rows[-1]['version'],
                             'display_name': rows[-1]['profile']['display_name']}
                            for p in sorted(root.glob('*.json')) if (rows := engine.read_json(p))]}
    path = root / (identifier(account_id, 'account_id') + '.json')
    rows = engine.read_json(path, [])
    if not rows:
        raise ValueError('Unknown hook account')
    found = next((r for r in rows if r['version'] == version), None) if version is not None else rows[-1]
    if not found:
        raise ValueError('Unknown account profile version')
    return copy.deepcopy(found)


def account_put(account_id, expected_version, profile):
    identifier(account_id, 'account_id')
    if not isinstance(expected_version, int) or isinstance(expected_version, bool) or expected_version < 0:
        raise ValueError('expected_version must be a nonnegative integer')
    p = copy.deepcopy(profile)
    text(p.get('display_name'), 'display_name', 200)
    text(p.get('identity_description'), 'identity_description')
    if p.get('identity_mode') not in ('authorized_person', 'synthetic', 'product_only'):
        raise ValueError('identity_mode: authorized_person / synthetic / product_only')
    text(p.get('voice_policy'), 'voice_policy')
    if not isinstance(p.get('reference_assets', []), list):
        raise ValueError('reference_assets must be a list')
    for a in p.get('reference_assets', []):
        if a.get('kind') not in ('image', 'video', 'audio') or not re.fullmatch(r'asset://[\w-]+', a.get('uri', '')):
            raise ValueError('Persistent account references need kind and asset:// URI; do not save temporary signed URLs')
        if a.get('status') not in ('Active', 'confirmed', 'pending', 'expired', 'revoked'):
            raise ValueError('Reference asset needs an explicit provider status')
        text(a.get('verification_reference'), 'verification_reference')
    root = account_path()
    with file_lock(root / 'writer.lock'):
        path = root / (account_id + '.json')
        rows = engine.read_json(path, [])
        current = rows[-1]['version'] if rows else 0
        if expected_version != current:
            raise ValueError(f'Account version conflict: current={current}')
        row = {'account_id': account_id, 'version': current + 1, 'profile': safe_receipt(p), 'created_at': time.time()}
        rows.append(row)
        engine.write_json(path, rows)
    return row


def get(project, hook_id):
    value = project.get('mcp_hooks', {}).get(hook_id)
    if not value:
        raise ValueError('Unknown hook_id in this project')
    return value


def plan_create(project_id, project, account_id, account_version, plan):
    account = account_get(account_id, account_version)
    p = copy.deepcopy(plan)
    for key in ('product_key', 'product_description', 'opening_idea', 'seam_description', 'prompt'):
        text(p.get(key), key)
    media_store.number(p.get('keep_seconds'), 'keep_seconds', .12, 30)
    refs = p.get('source_references')
    if not isinstance(refs, list) or not 1 <= len(refs) <= 10:
        raise ValueError('source_references needs 1-10 explicit video ranges')
    for r in refs:
        a = media_ops.asset(project_id, r['asset_id'])
        media_store.bounds(r['start'], r['end'], a['probe']['duration'])
        text(r.get('purpose'), 'reference purpose')
        r['source_sha256'] = a['identity']['sha256']
    hid = 'hook_' + uuid.uuid4().hex[:16]
    row = {'hook_id': hid, 'project_id': project_id, 'account': account, 'plan': p,
           'created_at': time.time(), 'state': 'planned', 'attempt': None, 'reviews': []}
    row['plan_sha256'] = fingerprint({'account': account, 'plan': p})
    project.setdefault('mcp_hooks', {})[hid] = row
    return copy.deepcopy(row)


def check_sources(project_id, hook):
    for r in hook['plan']['source_references']:
        a = media_ops.asset(project_id, r['asset_id'])
        if a['identity']['sha256'] != r['source_sha256']:
            raise ValueError('Hook source identity changed; create a new plan')
    # Do not silently inherit a changed identity, authorization or style profile.
    latest = account_get(hook['account']['account_id'])
    if latest['version'] != hook['account']['version']:
        raise ValueError('Account profile changed; create a new plan with the current version')


def prepare(project_id, hook, provider, request, bindings, preview_receipt):
    if hook['attempt']:
        raise ValueError('This plan already has a submission attempt; reconcile that task instead')
    check_sources(project_id, hook)
    identifier(provider, 'provider')
    if not isinstance(request, dict) or not isinstance(request.get('content'), list):
        raise ValueError('Use the provider native request with ordered content')
    if not isinstance(bindings, list) or not bindings:
        raise ValueError('Bind actual request media to account assets or queued slice evidence')
    text(preview_receipt, 'preview_receipt')
    content = request['content']
    media_indexes = [i for i, r in enumerate(content) if r.get('type') in ('video_url', 'image_url', 'audio_url')]
    if sorted(b.get('content_index', -1) for b in bindings) != media_indexes:
        raise ValueError('Every input media entry needs exactly one provenance binding')
    if not any(r.get('type') == 'video_url' and r.get('role') == 'reference_video' for r in content):
        raise ValueError('A real reference_video entry is required; text/image-only fallback is not a hook reference run')
    prompts = [r.get('text', '') for r in content if r.get('type') == 'text']
    if prompts != [hook['plan']['prompt']]:
        raise ValueError('Request prompt must match the pinned hook plan exactly')
    if float(request.get('duration', 0)) < hook['plan']['keep_seconds']:
        raise ValueError('Generation duration must cover the planned hook duration')
    real = hook['account']['profile']['identity_mode'] == 'authorized_person'
    slice_count = 0
    for binding in bindings:
        entry = content[binding['content_index']]
        kind = entry['type'].removesuffix('_url')
        uri = entry[entry['type']]['url']
        if not (re.fullmatch(r'asset://[\w-]+', uri) or (urlsplit(uri).scheme == 'https' and urlsplit(uri).hostname)):
            raise ValueError('Media needs a provider asset URI or HTTPS URL')
        if binding.get('source') == 'account':
            found = next((a for a in hook['account']['profile'].get('reference_assets', [])
                          if a['uri'] == uri and a['kind'] == kind and a['status'] in ('Active', 'confirmed')), None)
            if not found:
                raise ValueError('Reference is not an Active/provider-confirmed or user-confirmed asset of this account profile')
        elif binding.get('source') == 'slice':
            if kind != 'video' or entry.get('role') != 'reference_video':
                raise ValueError('Slice binding must point to a reference_video')
            idx = binding.get('source_index')
            if not isinstance(idx, int) or not 0 <= idx < len(hook['plan']['source_references']):
                raise ValueError('Invalid slice source_index')
            ref = hook['plan']['source_references'][idx]
            evidence = media_store.get(project_id, 'evidence', binding['evidence_id'])
            if (evidence.get('operation') not in ('preview', 'reference') or evidence.get('asset_id') != ref['asset_id'] or
                    evidence.get('source_sha256') != ref['source_sha256'] or
                    abs(evidence.get('source_start', -1)-ref['start']) > 1e-6 or
                    abs(evidence.get('source_end', -1)-ref['end']) > 1e-6):
                raise ValueError('Slice evidence must match the pinned source, hash and exact range')
            processed = binding.get('preprocessing')
            if processed:
                derivative = media_ops.asset(project_id, processed['output_asset_id'])
                if (processed.get('source_evidence_id') != evidence['id'] or processed.get('coverage') != 'head' or
                        processed.get('all_required_heads_covered') is not True or processed.get('garment_preserved') is not True or
                        not processed.get('review_evidence_refs')):
                    raise ValueError('Processed reference requires explicit reviewed head coverage, garment preservation and source evidence')
                text(processed.get('job_receipt'), 'preprocessing job_receipt')
                if abs(derivative['probe']['duration']-(ref['end']-ref['start'])) > .15:
                    raise ValueError('Processed reference changed the slice duration')
                accepted_hashes = [derivative['identity']['sha256']]
                if real and not any(c.get('type') == 'image_url' and c.get('role') == 'reference_image' and
                        any(a['kind'] == 'image' and a['uri'] == c['image_url']['url'] and a['status'] in ('Active', 'confirmed')
                            for a in hook['account']['profile'].get('reference_assets', [])) for c in content):
                    raise ValueError('A processed person reference also needs this account\'s authorized identity image asset in the request')
            else:
                accepted_hashes = [f['sha256'] for f in evidence.get('files', [])]
            if binding.get('uploaded_sha256') not in accepted_hashes:
                raise ValueError('Uploaded bytes must match the reviewed reference export/derivative')
            if real and provider == 'seedance' and not processed:
                if not uri.startswith('asset://'):
                    raise ValueError('Raw Seedance real-person slices require an authorized Active asset:// URI; use reviewed preprocessing plus this account\'s identity asset otherwise')
                receipt = binding.get('provider_asset', {})
                if (receipt.get('account_id') != hook['account']['account_id'] or
                        receipt.get('uri') != uri or receipt.get('status') != 'Active'):
                    raise ValueError('Slice needs an Active provider asset receipt for the selected account')
                text(receipt.get('verification_reference'), 'provider verification_reference')
                group = hook['account']['profile'].get('asset_group_id')
                if not group or receipt.get('asset_group_id') != group:
                    raise ValueError('Slice provider asset group must match the selected person')
            slice_count += 1
        else:
            raise ValueError('Binding source must be account or slice')
    if not slice_count:
        raise ValueError('At least one actual exported source slice must appear in the request')
    request_hash = fingerprint(request)
    hook['prepared'] = {'provider': provider, 'request_sha256': request_hash,
                        'request_redacted': safe_receipt(request), 'bindings': safe_receipt(bindings),
                        'preview_receipt': preview_receipt, 'prepared_at': time.time()}
    hook['state'] = 'prepared'
    return copy.deepcopy(hook['prepared'])


def begin(project_id, hook, request_sha256, authorization_reference):
    check_sources(project_id, hook)
    if hook['attempt']:
        return {'may_submit': False, 'reason': 'Existing attempt: query/reconcile it; never repeat a paid create call',
                'attempt': copy.deepcopy(hook['attempt'])}
    if hook.get('prepared', {}).get('request_sha256') != request_sha256:
        raise ValueError('Prepare and validate the exact request before claiming a submission')
    text(authorization_reference, 'authorization_reference')
    attempt = {'attempt_id': 'gen_' + uuid.uuid4().hex[:16], 'state': 'submission_pending',
               'task_id': None, 'request_sha256': request_sha256, 'created_at': time.time(),
               'authorization_reference': authorization_reference, 'events': []}
    hook['attempt'] = attempt
    hook['state'] = 'submission_pending'
    return {'may_submit': True, 'attempt': copy.deepcopy(attempt),
            'instruction': 'Call the authorized provider create exactly once with the prepared request. Persist its task ID immediately. Unknown outcomes must be reconciled, not retried.'}


TERMINAL = {'succeeded', 'failed', 'cancelled', 'expired'}


def record(hook, attempt_id, state, task_id, receipt):
    attempt = hook.get('attempt')
    if not attempt or attempt['attempt_id'] != attempt_id:
        raise ValueError('Unknown generation attempt')
    if state not in {'unknown', 'queued', 'running', *TERMINAL}:
        raise ValueError('Unsupported provider task state')
    if state != 'unknown' and not task_id:
        raise ValueError('A provider task ID is required for a known outcome')
    if task_id:
        identifier(task_id, 'task_id')
    if attempt.get('task_id') and task_id != attempt['task_id']:
        raise ValueError('Cannot replace the task ID of an existing paid attempt')
    old = attempt['state']
    if old in TERMINAL and state != old:
        raise ValueError('Terminal task state cannot be replaced; a retry needs a new explicit plan and authorization')
    if old == 'running' and state == 'queued':
        raise ValueError('Stale provider status cannot move a running task back to queued')
    if old in ('queued', 'running') and state == 'unknown':
        raise ValueError('A failed poll is not an unknown submission; keep polling the existing task ID')
    attempt.update(state=state, task_id=task_id or attempt.get('task_id'))
    attempt['events'].append({'state': state, 'recorded_at': time.time(), 'receipt': safe_receipt(receipt)})
    hook['state'] = state
    return copy.deepcopy(attempt)


def review(project_id, hook, asset_id, source_start, source_end, checks, note, evidence_refs):
    if not hook['attempt'] or hook['attempt']['state'] != 'succeeded':
        raise ValueError('Only a succeeded provider task can have an output review')
    check_sources(project_id, hook)
    a = media_ops.asset(project_id, asset_id)
    downloaded = [e.get('receipt', {}).get('output_sha256') for e in hook['attempt']['events'] if e['state'] == 'succeeded']
    if a['identity']['sha256'] not in downloaded:
        raise ValueError('Imported output must match output_sha256 from this task\'s successful download receipt')
    media_store.bounds(source_start, source_end, a['probe']['duration'])
    if abs(source_end-source_start-hook['plan']['keep_seconds']) > .04:
        raise ValueError('Review the planned retained hook range')
    required = {'person', 'garment', 'motion', 'seam', 'claims', 'audio'}
    if set(checks) != required or any(v not in ('pass', 'fail', 'not_applicable') for v in checks.values()):
        raise ValueError('Review person, garment, motion, seam, claims, audio as pass/fail/not_applicable')
    if hook['account']['profile']['identity_mode'] == 'authorized_person' and checks['person'] == 'not_applicable':
        raise ValueError('Person consistency is required for this account')
    if any(checks[k] == 'not_applicable' for k in ('garment', 'motion', 'seam', 'claims')):
        raise ValueError('Garment, motion, seam and claims must be reviewed')
    text(note, 'review note')
    if not isinstance(evidence_refs, list) or not evidence_refs or not all(isinstance(v, str) and v for v in evidence_refs):
        raise ValueError('Save concrete reviewed frame/video evidence references')
    value = {'review_id': 'review_' + uuid.uuid4().hex[:12], 'asset_id': asset_id,
             'source_sha256': a['identity']['sha256'], 'source_start': source_start, 'source_end': source_end,
             'checks': checks, 'note': note, 'evidence_refs': evidence_refs, 'reviewed_at': time.time(),
             'accepted': all(v != 'fail' for v in checks.values()),
             'reviewer': 'host-authored; not automatic person/garment recognition'}
    hook['reviews'].append(value)
    hook['state'] = 'reviewed' if value['accepted'] else 'rejected'
    return copy.deepcopy(value)


def prepend(timeline, asset_id, source_start, source_end, hook_id, playback_speed=1.0):
    """Explicit ripple on every event; bound source anchors resolve once."""
    if isinstance(playback_speed, bool) or playback_speed not in (1.0, 1.25):
        raise ValueError('Hook playback_speed must be 1.0 or 1.25')
    out = timing.resolve(timeline)
    if out.get('audio', {}).get('bed_asset_id'):
        raise ValueError('Convert the continuous audio bed to an explicit audio track before hook attachment')
    if any(m.get('locked') for m in out.get('track_meta', {}).values()):
        raise ValueError('Unlock affected tracks before the explicit hook ripple')
    clip = {'id': 'clip_' + uuid.uuid4().hex[:10], 'asset_id': asset_id, 'start': source_start,
            'end': source_end, 'playback_speed': playback_speed, 'transition': 'none', 'role': 'generated_hook',
            'shot_type': 'opening', 'hook_id': hook_id}
    shift = engine.render_duration(clip)
    for _, event in timing.entries(out):
        if event.get('time_binding'):
            continue
        for key in ('start', 'end'):
            if key in event:
                event[key] = round(event[key] + shift, 9)
        for keyframe in event.get('keyframes', []):
            keyframe['at'] = round(keyframe['at'] + shift, 9)
        for point in event.get('points', []):
            for key in ('start', 'end'):
                point[key] = round(point[key] + shift, 9)
    out['tracks']['main'].insert(0, clip)
    return timing.resolve(out), clip, shift
