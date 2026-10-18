"""Persistent, staged pure-source commerce editing. No model or paid API calls."""
import copy
import hashlib
import json
import re
import uuid
from pathlib import Path

from . import engine, media_jobs, media_ops, media_store, timing, speech
from .locking import project_write

FACTS = {
    'hook_picture': ('same_person', 'same_garment', 'full_body_visible', 'natural_display',
                     'no_continuous_speaking', 'burned_captions_compatible'),
    'hook_voice': ('complete_sentence', 'emotional_delivery', 'applicable_to_product', 'intelligible'),
    'body': ('same_person', 'same_garment', 'complete_meaning', 'complete_action',
             'visible_support_for_claim', 'intelligible_source_speech'),
}
METHODS = {'hook_picture': 'continuous_review', 'hook_voice': 'listening', 'body': 'continuous_review_and_listening'}


def path(project_id):
    return engine.project_dir(project_id) / 'commerce-workflow.json'


def load(project_id):
    state = engine.read_json(path(project_id), {})
    if not state: raise ValueError('Call commerce_workflow_start first')
    return state


def save(project_id, state):
    state['state_revision'] = state.get('state_revision', 0) + 1
    engine.write_json(path(project_id), state)


def signature(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


@project_write
def start(project_id, account, product, target_seconds=45):
    account = media_store.text(account, 'account', 200)
    product = media_store.text(product, 'product', 200)
    target = media_store.number(target_seconds, 'target_seconds', 15, 120)
    existing = engine.read_json(path(project_id), {})
    if existing:
        if (existing['account'], existing['product'], existing['target_seconds']) != (account, product, target):
            raise ValueError('Workflow scope is locked; create another project for a different account/product/target')
        return status(project_id)
    save(project_id, {'schema_version': 1, 'mode': 'source_voiceover', 'account': account,
        'product': product, 'target_seconds': target, 'duration_range': [max(15, target - 8), min(120, target + 8)],
        'generation_allowed': False, 'reviews': {}, 'selection': None, 'presentation': None})
    return status(project_id)


def candidate_submit(project_id, asset_id, start, end, role, transcript_job_id=None, sentence_ids=None):
    state = load(project_id)
    if role not in FACTS: raise ValueError('role must be hook_picture, hook_voice or body')
    asset = media_ops.asset(project_id, asset_id)
    if asset.get('kind') != 'video': raise ValueError('Select a registered source video; this route uses original slice picture and speech')
    start, end = media_store.bounds(start, end, asset['probe']['duration'])
    if end - start > 60: raise ValueError('Select one complete unit, at most 60 seconds')
    if role == 'hook_picture' and not 2 <= end - start <= 4:
        raise ValueError('Select 2–4 seconds of continuous full-body display at original speed')
    if role == 'hook_picture':
        binding = speech.display_binding(project_id, asset_id, start, end, transcript_job_id)
    else:
        if not transcript_job_id or not sentence_ids:
            raise ValueError('ASR is mandatory before voice/body selection: speech_transcribe_submit → speech_candidates_get → commerce_speech_candidate_submit. Supply real transcript_job_id and sentence_ids, never guessed timestamps.')
        binding = speech.range_validate(project_id, transcript_job_id, sentence_ids, start, end)
        if binding['asset_id'] != asset_id: raise ValueError('Transcript belongs to another source video')
    request = {'operation': 'commerce_inspect', 'asset_id': asset_id,
        'start': start, 'end': end, 'role': role, 'scope': [state['account'], state['product']],
        'speech_binding': binding}
    job = None
    for saved in reversed(media_jobs.records(project_id)):
        if saved['operation'] == 'commerce_inspect' and saved['status'] in ('queued', 'running', 'succeeded'):
            prior = engine.read_json(media_jobs.folder(project_id, saved['job_id']) / 'request.json')
            if prior == request:
                if saved['status'] == 'succeeded':
                    try:
                        candidate_report(project_id, saved['job_id'])
                    except (ValueError, OSError):
                        continue
                job = {**media_jobs.status(project_id, saved['job_id']), 'reused': True}; break
    if job is None: job = media_jobs.submit(project_id, request)
    return {**job, 'next_tool': 'media_job_status', 'required_facts': list(FACTS[role]),
            'selection_guidance': {
                'hook_picture': 'Review this display WITH the chosen independent quote. Burned source captions about a different sentence are incompatible even without geometric overlap; known conflicts cannot be deferred.',
                'hook_voice': 'A short complete sentence is not automatically an attention hook. Compare quotes/context for a real attention reason, then fit display duration; listen for emotion instead of approving plain styling narration.',
                'body': 'Match the spoken claim to visible detail/action. Log look/session continuity; a medium talking shot or unrelated item held in frame is not product detail evidence.',
            }[role],
            'required_method': METHODS[role], 'after_success': 'Watch/listen to the returned evidence, then commerce_candidate_review. If unable to observe a fact, commerce_candidate_defer records null for an explicitly unapproved review copy; known defects still require rejection.'}


def speech_candidate_submit(project_id, transcript_job_id, sentence_ids, role):
    if role not in ('hook_voice', 'body'): raise ValueError('Use this tool for hook_voice/body; display has its own visual source range')
    binding = speech.range_validate(project_id, transcript_job_id, sentence_ids)
    return candidate_submit(project_id, binding['asset_id'], binding['start'], binding['end'], role,
                            transcript_job_id, sentence_ids)


def candidate_report(project_id, job_id):
    from .delivery_review import fingerprint
    job = media_jobs.status(project_id, job_id)
    if job['status'] != 'succeeded' or job['operation'] != 'commerce_inspect':
        raise ValueError('Candidate inspection must succeed first; poll/retry the original job')
    report = job['result']
    state = load(project_id)
    if report['request']['scope'] != [state['account'], state['product']]:
        raise ValueError('Candidate belongs to a different workflow scope')
    asset = media_ops.asset(project_id, report['asset_id'])
    if asset['identity']['sha256'] != report['source_sha256']:
        raise ValueError('Candidate source changed')
    speech.verify_binding(project_id, report['request'].get('speech_binding'), report['asset_id'],
                          report['source_start'], report['source_end'], report['role'])
    for kind in ('preview', 'frames'):
        for file in report[kind]['files']:
            if fingerprint(file['path'])['sha256'] != file['sha256']:
                raise ValueError('Candidate evidence changed; inspect again')
    return report, fingerprint(media_jobs.folder(project_id, job_id) / 'result.json')


@project_write
def candidate_review(project_id, job_id, facts, method, note):
    report, evidence = candidate_report(project_id, job_id)
    role = report['role']
    if not isinstance(facts, dict) or set(facts) != set(FACTS[role]) or any(type(v) is not bool for v in facts.values()):
        raise ValueError('Supply exactly these observed boolean facts: ' + ', '.join(FACTS[role]))
    if method != METHODS[role]: raise ValueError('This candidate requires ' + METHODS[role])
    note = media_store.text(note, 'observed facts and source time locations')
    failed = [key for key, value in facts.items() if not value]
    if role != 'hook_picture' and report['audio_eligibility'] != 'audible_signal':
        failed.append('source_audio_' + report['audio_eligibility'])
    state = load(project_id)
    row = {'job_id': job_id, 'role': role, 'facts': facts, 'method': method, 'note': note,
           'evidence': evidence, 'source_sha256': report['source_sha256'],
           'status': 'rejected' if failed else 'accepted', 'failed': failed}
    previous = state['reviews'].get(job_id)
    if previous and previous['status'] == 'rejected' and row['status'] == 'accepted':
        raise ValueError('Rejected candidate cannot be relabelled; inspect a new source range and save new evidence')
    state['reviews'][job_id] = row
    save(project_id, state)
    return {**status(project_id), 'candidate': row}


@project_write
def candidate_defer(project_id, job_id, facts, note):
    """Preserve unknowns for a review copy, never turn them into approval."""
    report, evidence = candidate_report(project_id, job_id)
    role = report['role']; state = load(project_id)
    if not isinstance(facts, dict) or set(facts) != set(FACTS[role]) or any(v is not None and type(v) is not bool for v in facts.values()):
        raise ValueError('Supply every required fact as true, false or null (unreviewed)')
    if any(v is False for v in facts.values()):
        raise ValueError('Known defects cannot be deferred into a preview; reject and replace this candidate')
    if not any(v is None for v in facts.values()):
        raise ValueError('No unknown facts; use candidate_review with the actual review method')
    if state['reviews'].get(job_id, {}).get('status') == 'rejected':
        raise ValueError('A rejected candidate cannot be relabelled pending')
    if role != 'hook_picture' and report['audio_eligibility'] != 'audible_signal':
        raise ValueError('Silent or unresolved source audio is not eligible even for a review copy')
    state['reviews'][job_id] = {'job_id': job_id, 'role': role, 'facts': facts,
        'method': 'incomplete_review', 'note': media_store.text(note, 'specific unreviewed facts and reason'),
        'evidence': evidence, 'source_sha256': report['source_sha256'], 'status': 'pending',
        'pending_facts': [k for k, v in facts.items() if v is None], 'failed': []}
    save(project_id, state)
    return {**status(project_id), 'candidate': state['reviews'][job_id],
            'scope': 'Not accepted. Eligible only for an explicitly preview_only timeline; final delivery remains blocked.'}


def accepted(project_id, state, job_id, role, allow_pending=False):
    row = state['reviews'].get(job_id, {})
    if row.get('status') not in (('accepted', 'pending') if allow_pending else ('accepted',)) or row.get('role') != role:
        raise ValueError('An accepted ' + role + ' candidate is required: ' + str(job_id))
    report, evidence = candidate_report(project_id, job_id)
    if row['evidence'] != evidence: raise ValueError('Candidate review is stale')
    return report


@project_write
def candidate_skip(project_id, job_id, reason):
    state = load(project_id); job = media_jobs.status(project_id, job_id)
    request = engine.read_json(media_jobs.folder(project_id, job_id) / 'request.json')
    if job['operation'] != 'commerce_inspect' or request.get('scope') != [state['account'], state['product']]:
        raise ValueError('Not a candidate of this workflow')
    if job['status'] in ('queued', 'running'): raise ValueError('Cancel and confirm terminal status before abandoning an active job')
    if state.get('selection') and job_id in state['selection']['pictures'] + [state['selection']['voice']]:
        raise ValueError('Candidate is in the applied timeline; replace the plan before discarding it')
    state['reviews'][job_id] = {'job_id': job_id, 'role': request['role'], 'status': 'rejected',
        'method': 'skipped', 'facts': {}, 'note': media_store.text(reason, 'skip reason'), 'failed': ['discarded_candidate']}
    save(project_id, state)
    return status(project_id)


@project_write
def timeline_apply(project_id, hook_picture_job_id, hook_voice_job_id, body_job_ids,
                   voice_speed=1.0, transition_after_body=1, transition='slide_push',
                   expected_revision=0, replace_timeline=False, preview_only=False):
    from . import mcp_server as server
    state = load(project_id)
    if voice_speed not in (1.0, 1.25): raise ValueError('Voice speed must be 1.0 or explicitly authorized 1.25')
    if not isinstance(body_job_ids, list) or not 1 <= len(body_job_ids) <= 20 or len(set(body_job_ids)) != len(body_job_ids):
        raise ValueError('Provide 1–20 distinct accepted body candidate job IDs in sentence order')
    if transition not in server.TRANSITIONS or transition in ('none', 'punch_cut', 'detail_smash_cut'):
        raise ValueError('Choose one visible decorative transition from transition_list')
    if type(transition_after_body) is not int or not 1 <= transition_after_body < len(body_job_ids):
        raise ValueError('Place the transition between complete body units; index is one-based')
    picture = accepted(project_id, state, hook_picture_job_id, 'hook_picture', preview_only)
    voice = accepted(project_id, state, hook_voice_job_id, 'hook_voice', preview_only)
    body = [accepted(project_id, state, j, 'body', preview_only) for j in body_job_ids]
    if picture['source_sha256'] == voice['source_sha256'] and max(picture['source_start'], voice['source_start']) < min(picture['source_end'], voice['source_end']):
        raise ValueError('Select the independent voice from another source range, not the display\'s own sound')
    for index, first in enumerate(body):
        for second in body[index + 1:]:
            if first['source_sha256'] == second['source_sha256'] and min(first['source_end'], second['source_end']) - max(first['source_start'], second['source_start']) > .12:
                raise ValueError('Body candidates overlap the same original footage; avoid duplicate units')
    hook_length = picture['source_end'] - picture['source_start']
    voice_length = (voice['source_end'] - voice['source_start']) / voice_speed
    if not hook_length - .4 <= voice_length <= hook_length + .001:
        raise ValueError('The complete voice sentence must fit the display with at most 0.4s quiet space; choose another range, never truncate')
    if body[0]['source_end'] - body[0]['source_start'] < 3:
        raise ValueError('The first body unit must hold one complete point for at least 3 seconds')
    project = server._read(project_id); timeline = server._timeline(project)
    if timeline['revision'] != expected_revision: raise ValueError('Timeline revision changed; reload before applying')
    if any(timeline['tracks'].values()) and not replace_timeline:
        raise ValueError('Timeline is not empty. Explicit replace_timeline=true preserves an undo snapshot')
    undo = server._snapshot(project, 'before commerce_timeline_apply')
    fresh = server._default_timeline()
    fresh.update(canvas=copy.deepcopy(timeline['canvas']), revision=timeline['revision'], history=timeline['history'])
    ids = [hook_picture_job_id] + body_job_ids
    for index, (candidate, job_id) in enumerate(zip([picture] + body, ids)):
        clip = {'id': 'clip_' + uuid.uuid4().hex[:10], 'asset_id': candidate['asset_id'],
                'start': candidate['source_start'], 'end': candidate['source_end'],
                'role': 'hook_picture' if index == 0 else 'body', 'playback_speed': 1.0,
                'transition': transition if index == transition_after_body else 'none',
                'shot_type': 'full_body' if index == 0 else 'reviewed_body',
                'audio_gain_db': 0, 'audio_fade_in_ms': 25, 'audio_fade_out_ms': 25,
                'audio_mute': index == 0, 'frame_alignment': 'cover', 'commerce_candidate_id': job_id}
        fresh['tracks']['main'].append(clip)
    fresh['tracks']['audio'].append({'id': 'audio_' + uuid.uuid4().hex[:10], 'asset_id': voice['asset_id'],
        'start': 0, 'source_start': voice['source_start'], 'duration': voice_length,
        'playback_speed': voice_speed, 'volume': 1, 'fade_in': .015, 'fade_out': .025,
        'role': 'voiceover', 'label': 'Independent source hook voice', 'commerce_candidate_id': hook_voice_job_id})
    duration = timing.time_map(fresh)['duration']
    if not state['duration_range'][0] <= duration <= state['duration_range'][1]:
        raise ValueError('Selected complete units total %.2fs; required range is %s' % (duration, state['duration_range']))
    project['mcp_timeline'] = fresh
    errors = server._validate(project)
    if errors: raise ValueError('; '.join(errors))
    committed = server._commit(project_id, project, 'commerce_timeline_apply', {'candidates': ids}, undo)
    state['selection'] = {'pictures': ids, 'voice': hook_voice_job_id,
        'voice_speed': voice_speed, 'base_signature': base_signature(fresh), 'preview_only': bool(preview_only)}
    state['presentation'] = None
    save(project_id, state)
    return {**committed, **status(project_id), 'time_map': timing.time_map(fresh)}


def base_signature(timeline):
    # Gains may be repaired without invalidating chosen footage; source ranges, camera and audio route may not.
    keys = ('asset_id', 'start', 'end', 'playback_speed', 'audio_mute', 'camera', 'commerce_candidate_id', 'transition')
    main = [{k: c[k] for k in keys if k in c} for c in timeline['tracks']['main']]
    voice = [{k: a.get(k) for k in ('asset_id', 'start', 'duration', 'source_start', 'playback_speed', 'commerce_candidate_id')}
             for a in timeline['tracks'].get('audio', []) if a.get('role') == 'voiceover']
    return signature({'main': main, 'voice': voice, 'canvas': timeline.get('canvas')})


@project_write
def presentation_record(project_id, theme_colors, hook_text_ids, body_text_ids, sfx_audio_ids):
    from . import mcp_server as server
    state = load(project_id); timeline = server._timeline(server._read(project_id))
    if not state['selection'] or base_signature(timeline) != state['selection']['base_signature']:
        raise ValueError('Apply a reviewed source timeline first; changed source ranges require a new plan')
    if not isinstance(theme_colors, list) or not 1 <= len(theme_colors) <= 3 or any(not isinstance(c, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', c) for c in theme_colors):
        raise ValueError('Use 1–3 theme colors as #RRGGBB; neutral outlines are separate')
    for name, ids in [('hook_text_ids', hook_text_ids), ('body_text_ids', body_text_ids), ('sfx_audio_ids', sfx_audio_ids)]:
        if not isinstance(ids, list) or not ids or len(ids) > 32 or len(set(ids)) != len(ids):
            raise ValueError(name + ' requires distinct existing event IDs')
    state['presentation'] = {'theme_colors': theme_colors, 'hook_text_ids': hook_text_ids,
                            'body_text_ids': body_text_ids, 'sfx_audio_ids': sfx_audio_ids}
    errors = validate_presentation(timeline, state['presentation'])
    if errors: raise ValueError('; '.join(errors))
    save(project_id, state)
    return status(project_id)


def validate_presentation(timeline, plan):
    from . import canvas, commerce_typography
    errors = []
    entries = {e['id']: e for kind, e in timing.entries(timeline) if kind in ('overlay', 'callout', 'caption')}
    hook_end = timing.time_map(timeline)['clips'][0]['end']
    for field in ('hook_text_ids', 'body_text_ids'):
        for ident in plan[field]:
            e = entries.get(ident)
            if e is None: errors.append('Missing text event: ' + ident); continue
            if e.get('commerce_text') and e['commerce_text']['theme_colors'] != plan['theme_colors']:
                errors.append('Effect text does not share the declared palette: ' + ident)
            errors.extend(ident + ': ' + message for message in
                          commerce_typography.readability_errors(e, canvas.settings(timeline['canvas'])))
            if field == 'hook_text_ids' and not 0 <= e.get('start', -1) < e.get('end', -1) <= hook_end + .04:
                errors.append('Hook text must fall inside the hook: ' + ident)
            if field == 'body_text_ids' and e.get('start', -1) < hook_end:
                errors.append('Body emphasis must follow hook: ' + ident)
            for caption in timeline['tracks'].get('captions', []):
                if caption['id'] != ident and max(caption['start'], e.get('start', 0)) < min(caption['end'], e.get('end', 0)):
                    errors.append('Editable ordinary caption overlaps effect text: ' + ident)
    audio_ids = {a['id'] for a in timeline['tracks'].get('audio', []) if a.get('role') == 'sfx'}
    if set(plan['sfx_audio_ids']) - audio_ids: errors.append('Every planned sound must be an existing role=sfx audio event')
    return errors


def preflight(project_id, project=None, presentation_required=True, allow_pending=False):
    state = load(project_id)
    project = project or engine.read_json(engine.project_dir(project_id) / 'project.json')
    timeline = project['mcp_timeline']; errors = []
    selection = state['selection']
    if not selection: return ['Use commerce_timeline_apply with reviewed candidates first']
    if base_signature(timeline) != selection['base_signature']:
        errors.append('Selected footage/voice/transition changed: apply a new reviewed plan')
    for i, job_id in enumerate(selection['pictures']):
        try: accepted(project_id, state, job_id, 'hook_picture' if i == 0 else 'body', allow_pending)
        except (ValueError, OSError) as exc: errors.append(str(exc))
    try: accepted(project_id, state, selection['voice'], 'hook_voice', allow_pending)
    except (ValueError, OSError) as exc: errors.append(str(exc))
    if presentation_required:
        if not state['presentation']: errors.append('Plan effect text, sound and theme; call commerce_presentation_record')
        else: errors.extend(validate_presentation(timeline, state['presentation']))
    return errors


def render_guard(project_id, project, preview=True):
    if path(project_id).is_file():
        state = load(project_id)
        pending_preview = preview and (state.get('selection') or {}).get('preview_only', False)
        errors = preflight(project_id, project, allow_pending=pending_preview)
        if errors: raise ValueError('Commerce preflight: ' + '; '.join(errors))
        if not preview:
            from . import delivery_review
            candidates = [engine.read_json(p) for p in (engine.project_dir(project_id) / 'mcp_renders').glob('*/state.json')]
            previews = [r for r in candidates if r['preview'] and r['status'] == 'succeeded' and r['revision'] == project['mcp_timeline']['revision']]
            if not any(delivery_review.get(project_id, r['job_id'])['status'] == 'preview_reviewed' for r in previews):
                raise ValueError('Review a succeeded preview of this revision before final submission')


def status(project_id):
    state = load(project_id)
    counts = {role: sum(r['role'] == role and r['status'] == 'accepted' for r in state['reviews'].values()) for role in FACTS}
    pending = {role: [j for j,r in state['reviews'].items() if r['role']==role and r['status']=='pending'] for role in FACTS}
    if not state['selection'] and (not counts['hook_picture'] or not counts['hook_voice'] or counts['body'] < 2):
        stage, tool = 'candidates', 'commerce_candidate_submit'
        todo = ['Find and continuously review full-body display with no sustained speaking', 'Choose a complete emotional source sentence independently', 'Review complete body explanation/action units with audible source speech']
        if counts['hook_picture']+len(pending['hook_picture']) and counts['hook_voice']+len(pending['hook_voice']) and counts['body']+len(pending['body']) >= 2:
            stage, tool = 'candidate_review_pending', 'commerce_timeline_apply'
            todo = ['Complete pending reviews for normal production, or explicitly use preview_only=true to produce an unapproved review copy; never claim approval']
    elif not state['selection']:
        stage, tool, todo = 'timeline', 'commerce_timeline_apply', ['Order accepted body units; supply one topic-boundary transition']
    elif not state['presentation']:
        stage, tool, todo = 'presentation', 'commerce_typography_add', ['Review source safe boxes; add hook_center and body keyword_stack/detail_underline with one palette', 'Add selected short role=sfx sounds, then commerce_presentation_record with actual IDs']
    else:
        errors = preflight(project_id, allow_pending=(state['selection'] or {}).get('preview_only', False))
        stage, tool = ('repair', 'commerce_timeline_apply') if errors else ('preview', 'commerce_render_submit')
        if errors and any('Candidate' in error or 'candidate' in error or 'source changed' in error for error in errors):
            tool = 'commerce_candidate_submit'
        todo = errors or ['Queue preview, then delivery_inspection_submit and delivery_review_record; final delivery requires review of the final artifact']
    recovery = {}
    if stage == 'candidates':
        candidates = []
        for job in media_jobs.records(project_id):
            if job['operation'] != 'commerce_inspect' or job['job_id'] in state['reviews']: continue
            request = engine.read_json(media_jobs.folder(project_id, job['job_id']) / 'request.json')
            if request.get('scope') == [state['account'], state['product']]:
                candidates.append({**job, 'role': request['role']})
        if candidates:
            candidate = candidates[0]; recovery['candidate_job_id'] = candidate['job_id']
            if candidate['status'] in ('queued', 'running'):
                stage, tool, todo = 'candidate_wait', 'media_job_status', ['Wait for existing candidate job; do not resubmit']
            elif candidate['status'] == 'succeeded':
                stage, tool, todo = 'candidate_review', 'commerce_candidate_review', ['Open continuous preview/listening evidence; fill only observed facts']
                recovery.update(required_facts=list(FACTS[candidate['role']]), required_method=METHODS[candidate['role']])
            else:
                stage, tool, todo = 'candidate_failed', 'media_job_retry', ['Inspect failure; retry the existing request only for execution failure, otherwise select another source range']
                recovery['alternative_tool'] = 'commerce_candidate_skip'
    if stage == 'preview':
        project = engine.read_json(engine.project_dir(project_id) / 'project.json')
        renders = [engine.read_json(p) for p in (engine.project_dir(project_id) / 'mcp_renders').glob('*/state.json')]
        renders = sorted([r for r in renders if r['revision'] == project['mcp_timeline']['revision']], key=lambda r: r['created_at'])
        if renders:
            render = renders[-1]; recovery['render_job_id'] = render['job_id']
            if render['status'] != 'succeeded':
                stage = 'render_wait' if render['status'] in ('queued', 'running') else 'render_failed'
                tool = 'render_job_status' if stage == 'render_wait' else 'render_retry'
                todo = ['Continue the existing job; do not resubmit while queued or running']
            else:
                from . import delivery_review
                review = delivery_review.get(project_id, render['job_id'])
                recovery['delivery_status'] = review['status']
                inspections = [j for j in media_jobs.records(project_id) if j['operation'] == 'delivery_inspect' and
                    engine.read_json(media_jobs.folder(project_id, j['job_id']) / 'request.json').get('render_job_id') == render['job_id']]
                if review.get('delivery_ready'):
                    stage, tool, todo = 'delivery', 'commerce_delivery_get', ['Read the final gate result and deliver its reviewed artifact']
                elif review['status'] == 'preview_reviewed':
                    stage, tool, todo = 'final_render', 'commerce_render_submit', ['Submit preview=false, then inspect/review that final output']
                elif review['status'] == 'needs_repair':
                    stage = 'repair'
                    failed = review.get('failed_checks', [])
                    content_checks = {'source_audio', 'source_frame_timestamps', 'hook_picture', 'hook_voice',
                                      'hook_body_continuity', 'selling_point_evidence', 'body_continuity'}
                    tool = 'commerce_candidate_submit' if content_checks.intersection(failed) else 'timeline_get'
                    recovery['failed_checks'] = failed
                    recovery['repair_actions'] = [{'id': c['id'], 'note': c['note'], 'repair_action': c['repair_action']}
                                                  for c in review.get('checks', []) if c['status'] == 'fail']
                    recovery['technical_findings'] = review.get('technical_findings', [])
                    todo = ['Repair the failed subject, apply a new reviewed plan if footage/voice changes, then submit a new preview; do not resubmit pass or render_retry for a content failure']
                elif review['status'] == 'stale':
                    stage, tool, todo = 'inspection', 'delivery_inspection_submit', ['Rebuild and review current bound evidence; stale approval cannot certify delivery']
                elif inspections:
                    inspection = inspections[-1]; recovery['inspection_job_id'] = inspection['job_id']
                    if inspection['status'] == 'succeeded':
                        try:
                            identity, _ = delivery_review.artifact(project_id, render['job_id'])
                            delivery_review.inspection_findings(project_id, identity, inspection['job_id'])
                        except (ValueError, OSError):
                            inspection = {**inspection, 'status': 'stale'}
                    stage, tool = ('review', 'delivery_review_form') if inspection['status'] == 'succeeded' else ('inspection_wait', 'media_job_status')
                    if inspection['status'] in ('failed', 'cancelled', 'interrupted'): stage, tool = 'inspection_failed', 'media_job_retry'
                    if inspection['status'] == 'stale': stage, tool = 'inspection', 'delivery_inspection_submit'
                    todo = ['Read exact inspection evidence and every missing/failed check; unknown remains not_checked']
                else:
                    stage, tool, todo = 'inspection', 'delivery_inspection_submit', ['Inspect this succeeded render, including source and final segment sound']
    speech_progress = speech.workflow_progress(project_id)
    if speech_progress['missing']:
        missing = speech_progress['missing']
        stage, tool = missing['stage'], missing['next_tool']
        recovery['next_arguments'] = missing['next_arguments']
        todo = ['Complete source transcription before selecting material. Poll queued work; retry failures through media_job_retry. Do not invent ASR text/times or bypass this stage.']
    elif stage == 'candidates':
        usable = [t for t in speech_progress['transcripts'] if t['speech_status'] == 'speech_detected']
        if usable:
            tool = 'speech_candidates_get'
            recovery['next_arguments'] = {'project_id': project_id, 'job_id': usable[0]['job_id'], 'role': 'all'}
            todo.insert(0, 'Read full sentence candidates and their surrounding context; select consecutive IDs with commerce_speech_candidate_submit, which validates word boundaries automatically')
        elif speech_progress['transcripts']:
            stage, tool = 'materials_insufficient', None
            todo = ['No recognized source speech is available. Inspect ASR language/quality or obtain authorized audible material; silent/no-speech sources cannot become voice/body. Do not loop transcription or fabricate sentences.']
    recovery['transcripts'] = speech_progress['transcripts']
    reference_root = Path(__file__).resolve().parents[1] / 'skills' / 'baiying-dialogue-video' / 'references'
    return {**recovery, 'project_id': project_id, 'mode': state['mode'], 'stage': stage, 'state_revision': state['state_revision'],
            'account': state['account'], 'product': state['product'], 'target_seconds': state['target_seconds'],
            'duration_range': state['duration_range'],
            'reference_files': [str(reference_root / name) for name in ('source-only-stages.md', 'speech-evidence.md', 'quality-and-repair.md')
                                if (reference_root / name).is_file()],
            'next_tool': tool, 'required_actions': todo, 'accepted_counts': counts,
            'pending_candidate_ids': pending, 'preview_only': (state.get('selection') or {}).get('preview_only', False),
            'candidate_ids': {role: [j for j, r in state['reviews'].items() if r['role'] == role and r['status'] == 'accepted'] for role in FACTS},
            'must_keep': ['Source-only: no video generation or speech synthesis', 'Display picture is not talking footage with its sound muted',
                          'ASR first: source-bound transcript, complete consecutive sentence IDs and word-boundary validation; ASR is not listening approval',
                          'Original picture/body speed; independent voice speed is explicit', 'GPU first; all heavy operations use the shared single queue'],
            'scope': 'Caller visual/listening observations are recorded, not independently certified by this plugin.'}
