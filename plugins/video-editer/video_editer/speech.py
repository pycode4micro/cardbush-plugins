"""Queued local ASR and source-bound sentence evidence, never semantic approval."""
import copy
import hashlib
import importlib.util
import math
import os
from pathlib import Path
import re
import sys
import wave

from . import engine, media_jobs, media_ops, media_store, processes, resources

VERSION = 'speech-evidence-1'
MODEL_REPO = 'Systran/faster-whisper-small'
MODEL_REVISION = '536b0662742c02347bc0e980a01041f333bce120'
MODEL_FILES = ('config.json', 'model.bin', 'tokenizer.json', 'vocabulary.txt')
MAX_WINDOW = 600
PUNCTUATION = re.compile(r'[。！？!?；;.]$')


def public_job(job):
    """Keep thousands of word tokens out of repeated MCP polling/context history."""
    if job.get('operation') != 'speech_transcribe' or job.get('status') != 'succeeded': return job
    result = job['result']
    keys = ('operation', 'version', 'asset_id', 'source_sha256', 'source_start', 'source_end',
            'source_duration', 'speech_status', 'backend', 'timestamp_errors', 'human_listened', 'scope')
    compact = {k: result[k] for k in keys if k in result}
    compact.update(word_count=len(result['words']), sentence_count=len(result['sentences']),
                   transcript_path=str(media_jobs.folder(job['project_id'], job['job_id']) / 'result.json'))
    return {**job, 'result': compact, 'next_tool': 'speech_candidates_get',
            'next_arguments': {'project_id': job['project_id'], 'job_id': job['job_id']}}


def model_dir():
    return Path(os.environ.get('VIDEO_EDITER_ASR_MODEL_DIR', str(engine.PROJECTS.parent / 'models' / 'faster-whisper-small'))).expanduser().resolve()


def runtime_status():
    missing = [name for name in ('faster_whisper', 'ctranslate2', 'huggingface_hub') if importlib.util.find_spec(name) is None]
    directory = model_dir()
    ready = all((directory / name).is_file() for name in MODEL_FILES)
    return {'backend': 'faster-whisper', 'missing_dependencies': missing, 'model_path': str(directory),
            'model_files_present': ready, 'default_model': MODEL_REPO, 'default_revision': MODEL_REVISION,
            'device_policy': 'CUDA first; bounded CPU int8 only if resource policy allows. Encoder GPU support is separate.',
            'heavy_concurrency': 1, 'next_tool': 'speech_transcribe_submit' if ready and not missing else 'speech_model_prepare',
            'installation': 'Install this plugin requirements.txt in the MCP interpreter; reconnect. No ad-hoc ASR script is needed.',
            'scope': 'File/dependency discovery only; the queued worker validates model, audio and actual device. No model download on this read.'}


def _submit(project_id, request):
    # Serialize deduplication with other callers, but do not hold a heavy slot.
    from .locking import file_lock
    with file_lock(engine.project_dir(project_id) / 'speech-submit.lock'):
        for saved in reversed(media_jobs.records(project_id)):
            if saved['operation'] != request['operation']: continue
            prior = engine.read_json(media_jobs.folder(project_id, saved['job_id']) / 'request.json')
            if prior != request: continue
            current = media_jobs.status(project_id, saved['job_id'])
            if current['status'] in ('queued', 'running'):
                return {**current, 'reused': True, 'next_tool': 'media_job_status'}
            if current['status'] == 'succeeded':
                try:
                    if request['operation'] == 'speech_transcribe': report(project_id, saved['job_id'])
                    elif not runtime_status()['model_files_present']: continue
                    return {**public_job(current), 'reused': True, 'next_tool': 'speech_candidates_get' if request['operation'] == 'speech_transcribe' else 'speech_transcribe_submit'}
                except (ValueError, OSError):
                    continue
            else:
                return {**current, 'reused': True, 'next_tool': 'media_job_retry',
                        'required_action': 'Fix the reported execution failure, then retry this job through media_job_retry; do not loop new submissions.'}
        return {**media_jobs.submit(project_id, request), 'next_tool': 'media_job_status'}


def model_prepare(project_id, source_model_dir=None):
    engine.project_dir(project_id)
    if runtime_status()['missing_dependencies']:
        raise ValueError('ASR dependencies missing: install the plugin requirements.txt in its MCP interpreter, then reconnect')
    source = None
    if source_model_dir is not None:
        source = Path(source_model_dir).expanduser().resolve()
        if not all((source / name).is_file() for name in MODEL_FILES): raise ValueError('Offline model directory must contain config.json, model.bin, tokenizer.json and vocabulary.txt')
    return _submit(project_id, {'operation': 'speech_model_prepare', 'version': VERSION,
        'model_path': str(model_dir()), 'source_model_dir': str(source) if source else None,
        'model_repo': MODEL_REPO, 'model_revision': MODEL_REVISION})


def submit(project_id, asset_id, start=0, end=None, language='zh', device='auto'):
    asset = media_ops.asset(project_id, asset_id)
    if asset['kind'] != 'video': raise ValueError('Transcribe a registered source video')
    if not isinstance(language, str) or not re.fullmatch(r'[a-z]{2,3}', language): raise ValueError('Use an explicit language code, e.g. zh or en')
    if device not in ('auto', 'cuda', 'cpu'): raise ValueError('device must be auto, cuda or cpu')
    start, end = media_store.bounds(start, min(asset['probe']['duration'], start + MAX_WINDOW) if end is None else end, asset['probe']['duration'])
    if end - start > MAX_WINDOW: raise ValueError('Transcribe windows of at most 600 seconds; workflow status returns the next uncovered window')
    if asset['probe'].get('has_audio'):
        runtime = runtime_status()
        if runtime['missing_dependencies']: raise ValueError('Install plugin requirements.txt; ASR is now a bundled dependency')
        if not runtime['model_files_present']: raise ValueError('Call speech_model_prepare, then wait for its media_job_status to succeed')
    return _submit(project_id, {'operation': 'speech_transcribe', 'version': VERSION, 'asset_id': asset_id,
        'source_sha256': asset['identity']['sha256'], 'start': start, 'end': end, 'language': language,
        'device': device, 'model_path': str(model_dir())})


def build_sentences(segments, start, end):
    """Only structural proposals. Punctuation, pauses and ASR segments are not proof of meaning."""
    words, issues = [], []
    previous_end = start
    for segment_index, segment in enumerate(segments):
        raw_words = segment.get('words') or []
        if segment.get('text', '').strip() and not raw_words:
            issues.append('segment_%d_missing_word_timestamps' % segment_index)
        for raw in raw_words:
            text = raw.get('word', '')
            if not isinstance(text, str) or not text.strip(): continue
            a, b, probability = raw.get('start'), raw.get('end'), raw.get('probability')
            numeric = lambda x: type(x) in (float, int) and math.isfinite(x)
            if (not all(numeric(x) for x in (a, b, probability)) or not 0 <= probability <= 1
                    or a < start or b > end + .02 or b <= a or a < previous_end - .02):
                issues.append('invalid_word_timestamp_%d' % len(words))
                continue
            # Tiny numerical tolerances are recorded, never invented for missing times.
            a, b = max(a, previous_end), min(b, end)
            if b <= a:
                issues.append('zero_word_timestamp_%d' % len(words)); continue
            words.append({'id': len(words), 'start': round(a, 6), 'end': round(b, 6),
                          'word': text, 'probability': probability, 'segment': segment_index})
            previous_end = b
    sentences, first = [], 0
    for i, word in enumerate(words):
        following = words[i + 1] if i + 1 < len(words) else None
        reason = ('punctuation' if PUNCTUATION.search(word['word']) else
                  'pause' if following and following['start'] - word['end'] >= .35 else
                  'asr_segment' if following and following['segment'] != word['segment'] else
                  'window_end' if following is None else None)
        if reason is None: continue
        selected = words[first:i + 1]
        text = ''.join(w['word'] for w in selected).strip()
        flags = []
        if min(w['probability'] for w in selected) < .45: flags.append('low_word_confidence')
        if reason in ('asr_segment', 'window_end'): flags.append('meaning_boundary_unverified')
        if first == 0 and start > 0: flags.append('window_start_context_missing')
        if i == len(words) - 1: flags.append('check_sentence_tail')
        sentences.append({'sentence_id': 'sentence_%04d' % len(sentences), 'text': text,
            'first_word': first, 'last_word': i, 'start': selected[0]['start'], 'end': selected[-1]['end'],
            'duration': round(selected[-1]['end'] - selected[0]['start'], 6), 'boundary_basis': reason,
            'min_word_probability': min(w['probability'] for w in selected),
            'mean_word_probability': sum(w['probability'] for w in selected) / len(selected),
            'warnings': flags, 'meaning_status': 'unreviewed'})
        first = i + 1
    return words, sentences, issues


def report(project_id, job_id):
    job = media_jobs.status(project_id, job_id)
    if job['operation'] != 'speech_transcribe' or job['status'] != 'succeeded':
        raise ValueError('A succeeded speech_transcribe job is required; poll/retry its original job ID')
    directory = media_jobs.folder(project_id, job_id)
    checksum = hashlib.sha256((directory / 'result.json').read_bytes()).hexdigest()
    if checksum != job.get('result_sha256'): raise ValueError('Transcript evidence changed; resubmit transcription')
    request_bytes = (directory / 'request.json').read_bytes()
    if hashlib.sha256(request_bytes).hexdigest() != job['request_sha256']: raise ValueError('Transcript request changed')
    value = job['result']; request = engine.read_json(directory / 'request.json')
    if value.get('request') != request: raise ValueError('Transcript request/result mismatch')
    asset = media_ops.asset(project_id, value['asset_id'])
    if asset['identity']['sha256'] != value['source_sha256'] or value['source_sha256'] != request['source_sha256']:
        raise ValueError('Transcript source changed')
    if value.get('timestamp_errors'): raise ValueError('Invalid ASR timestamps: ' + ', '.join(value['timestamp_errors'][:5]))
    return value, checksum


def candidates_get(project_id, job_id, role='all', offset=0, limit=20):
    if role not in ('all', 'hook_voice', 'body'): raise ValueError('role must be all, hook_voice or body')
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100: raise ValueError('Invalid pagination')
    value, checksum = report(project_id, job_id)
    rows = value['sentences']
    selected = []
    for i, row in enumerate(rows):
        if role == 'hook_voice' and not .8 <= row['duration'] <= 4: continue
        selected.append({**row, 'context_before': rows[i - 1]['text'] if i else '',
                         'context_after': rows[i + 1]['text'] if i + 1 < len(rows) else '',
                         'low_confidence_words': [w for w in value['words'][row['first_word']:row['last_word'] + 1] if w['probability'] < .45],
                         'hook_duration_fit': .8 <= row['duration'] <= 4})
    return {'project_id': project_id, 'transcript_job_id': job_id, 'asset_id': value['asset_id'],
            'source_sha256': value['source_sha256'], 'transcript_sha256': checksum,
            'speech_status': value['speech_status'], 'items': selected[offset:offset + limit],
            'total': len(selected), 'next_offset': offset + limit if offset + limit < len(selected) else None,
            'next_tool': 'speech_range_validate' if selected else 'commerce_workflow_status',
            'required_actions': ['Read each full quote AND its context; join adjacent sentence IDs for a complete idea. Never remove conditions or split a word.',
                                 'ASR proposals are not approved sentences. Low confidence and unverified boundaries require checking the source.',
                                 'Pass chosen IDs to speech_range_validate, then commerce_speech_candidate_submit; no hand-written transcript or guessed seconds.']}


def range_validate(project_id, job_id, sentence_ids, start=None, end=None):
    value, checksum = report(project_id, job_id)
    if value['speech_status'] != 'speech_detected': raise ValueError('No recognized speech: this source is only eligible as silent display after visual review, never voice/body')
    if not isinstance(sentence_ids, list) or not sentence_ids or len(sentence_ids) > 64 or len(set(sentence_ids)) != len(sentence_ids):
        raise ValueError('Supply distinct ordered sentence_ids from speech_candidates_get')
    indices = {row['sentence_id']: i for i, row in enumerate(value['sentences'])}
    if any(s not in indices for s in sentence_ids): raise ValueError('Unknown sentence ID; use IDs from this transcript job')
    order = [indices[s] for s in sentence_ids]
    if order != list(range(order[0], order[0] + len(order))): raise ValueError('Select consecutive sentences in source order; do not omit conditions or join disjoint quotes')
    rows = [value['sentences'][i] for i in order]
    words = value['words']; first, last = rows[0]['first_word'], rows[-1]['last_word']
    before = words[first - 1]['end'] if first else value['source_start']
    after = words[last + 1]['start'] if last + 1 < len(words) else value['source_end']
    lo, hi = words[first]['start'], words[last]['end']
    # Small handles must stay inside actual measured word gaps.
    start = max(before, lo - .04) if start is None else start
    end = min(after, hi + .06) if end is None else end
    start, end = media_store.bounds(start, end, value['source_duration'])
    if start > lo + 1e-6 or end < hi - 1e-6: raise ValueError('Selected range cuts the first/last ASR word; keep the complete candidate sentence')
    if start < before - 1e-6 or end > after + 1e-6: raise ValueError('Range includes an adjacent unselected word or exceeds transcript coverage; include adjacent sentence IDs')
    if end - start > 60: raise ValueError('Select a complete unit at most 60 seconds')
    return {'timestamp_valid': True, 'transcript_job_id': job_id, 'transcript_sha256': checksum,
            'asset_id': value['asset_id'], 'source_sha256': value['source_sha256'],
            'sentence_ids': sentence_ids, 'start': round(start, 6), 'end': round(end, 6),
            'duration': round(end - start, 6), 'text': ''.join(r['text'] for r in rows),
            'warnings': sorted({w for r in rows for w in r['warnings']}),
            'next_tool': 'commerce_speech_candidate_submit',
            'scope': 'Valid against estimated ASR word times, not sample-accurate forced alignment or semantic/listening approval.'}


def display_binding(project_id, asset_id, start, end, job_id=None):
    jobs = [job_id] if job_id else [r['job_id'] for r in reversed(media_jobs.records(project_id)) if r['operation'] == 'speech_transcribe' and r['status'] == 'succeeded']
    for ident in jobs:
        try:
            value, checksum = report(project_id, ident)
            if value['asset_id'] == asset_id and value['source_start'] <= start and value['source_end'] >= end:
                return {'transcript_job_id': ident, 'transcript_sha256': checksum, 'asset_id': asset_id,
                        'source_sha256': value['source_sha256'], 'start': start, 'end': end, 'display_only': True}
        except (ValueError, OSError):
            if job_id: raise
    raise ValueError('Transcription is required before selecting this source: speech_transcribe_submit → media_job_status. Silent display receives a no_audio/no_speech record; it is not exempt from provenance.')


def verify_binding(project_id, binding, asset_id, start, end, role):
    if not binding: raise ValueError('Missing ASR evidence; transcribe the source and select new bound candidates')
    if role == 'hook_picture':
        expected = display_binding(project_id, asset_id, start, end, binding['transcript_job_id'])
    else:
        expected = range_validate(project_id, binding['transcript_job_id'], binding.get('sentence_ids'), start, end)
    if expected != binding: raise ValueError('ASR candidate binding changed or belongs to another source/range')
    if expected['asset_id'] != asset_id: raise ValueError('ASR source does not match the selected video')
    return expected


def workflow_progress(project_id):
    """Cheap restart discovery; no new work and no historical media mutation."""
    project = engine.read_json(engine.project_dir(project_id) / 'project.json')
    jobs = media_jobs.records(project_id)
    transcripts, first_missing = [], None
    for asset in project.get('materials', []):
        if asset.get('kind') != 'video': continue
        duration = asset['probe']['duration']; coverage = []
        for job in jobs:
            if job['operation'] != 'speech_transcribe': continue
            request = engine.read_json(media_jobs.folder(project_id, job['job_id']) / 'request.json')
            if request.get('asset_id') != asset['id']: continue
            if job['status'] == 'succeeded':
                try:
                    value, _ = report(project_id, job['job_id'])
                    coverage.append((value['source_start'], value['source_end']))
                    transcripts.append({'asset_id': asset['id'], 'job_id': job['job_id'], 'speech_status': value['speech_status'],
                                        'start': value['source_start'], 'end': value['source_end'], 'sentences': len(value['sentences'])})
                except (ValueError, OSError): pass
        cursor = 0
        for a, b in sorted(coverage):
            if a > cursor + .001: break
            cursor = max(cursor, b)
        if cursor >= duration - .001 or first_missing: continue
        args = {'project_id': project_id, 'asset_id': asset['id'], 'start': cursor, 'end': min(duration, cursor + MAX_WINDOW)}
        relevant = []
        for job in jobs:
            if job['operation'] != 'speech_transcribe': continue
            req = engine.read_json(media_jobs.folder(project_id, job['job_id']) / 'request.json')
            if req.get('asset_id') == asset['id'] and req.get('start') == args['start'] and req.get('end') == args['end']:
                relevant.append(media_jobs.status(project_id, job['job_id']))
        last = relevant[-1] if relevant else None
        first_missing = {'stage': 'source_transcription', 'next_tool': 'speech_transcribe_submit', 'next_arguments': args}
        if last and last['status'] in ('queued', 'running', 'failed', 'cancelled', 'interrupted'):
            first_missing.update(stage='transcript_wait' if last['status'] in ('queued', 'running') else 'transcript_failed',
                                 next_tool='media_job_status' if last['status'] in ('queued', 'running') else 'media_job_retry',
                                 next_arguments={'project_id': project_id, 'job_id': last['job_id']})
        elif asset['probe'].get('has_audio') and not runtime_status()['model_files_present']:
            prepares = [j for j in jobs if j['operation'] == 'speech_model_prepare']
            last_prepare = media_jobs.status(project_id, prepares[-1]['job_id']) if prepares else None
            if last_prepare and last_prepare['status'] in ('queued', 'running', 'failed', 'cancelled', 'interrupted'):
                first_missing.update(next_tool='media_job_status' if last_prepare['status'] in ('queued', 'running') else 'media_job_retry',
                                     next_arguments={'project_id': project_id, 'job_id': last_prepare['job_id']})
            else: first_missing.update(next_tool='speech_model_prepare', next_arguments={'project_id': project_id})
    return {'transcripts': transcripts, 'missing': first_missing}


def extract_audio(source_path, audio_path, container_start, start, end):
    """Keep the source clock, including delayed audio and gaps, in the PCM samples."""
    origin = container_start + start; duration = end - start
    filters = (f'atrim=start={origin:.9f}:end={origin + duration:.9f},'
               f'asetpts=PTS-({origin:.9f})/TB,aresample=16000:async=1:first_pts=0,'
               f'apad=whole_dur={duration:.9f},atrim=duration={duration:.9f}')
    processes.run([engine.ffmpeg_bin(), '-nostdin', '-v', 'error', '-n', '-copyts', '-i', str(source_path),
        '-map', '0:a:0', '-vn', '-af', filters, '-t', str(duration), '-ac', '1', '-ar', '16000',
        '-c:a', 'pcm_s16le', str(audio_path)], capture_output=True, check=True, timeout=300)
    with wave.open(str(audio_path), 'rb') as wav:
        decoded = wav.getnframes() / wav.getframerate()
    if abs(decoded - duration) > .002:
        raise ValueError('Decoded audio does not cover requested source time range; inspect stream offsets before choosing speech')


def execute(project_id, request, directory, progress):
    if request['operation'] == 'speech_model_prepare':
        progress('preparing_asr_model', 0, 1)
        child_request = {**request, 'action': 'prepare'}
    else:
        asset = media_ops.asset(project_id, request['asset_id'], strong=True, progress=progress)
        if asset['identity']['sha256'] != request['source_sha256']: raise ValueError('ASR source changed since submission')
        a, b = request['start'], request['end']
        if not asset['probe'].get('has_audio'):
            child_result = {'segments': [], 'device': None, 'reason': 'no_audio_stream'}
            audio_path = None
        else:
            progress('extracting_asr_audio', 0, 1)
            audio_path = directory / 'source-audio.wav'
            extract_audio(asset['path'], audio_path, asset['probe'].get('container_start', 0), a, b)
        child_request = {**request, 'action': 'transcribe', 'audio_path': str(audio_path),
                         'resource_policy': resources.current_policy()}
    if request['operation'] == 'speech_model_prepare' or audio_path is not None:
        worker_input = directory / 'asr-input.json'; worker_output = directory / 'asr-output.json'
        engine.write_json(worker_input, child_request)
        progress('asr_inference' if request['operation'] == 'speech_transcribe' else 'preparing_asr_model', 0, 1)
        # Child inherits limits; parent owns the shared heavy slot, cancellation and memory supervision.
        processes.run([sys.executable, '-m', 'video_editer.speech_worker', str(worker_input), str(worker_output)],
                      cwd=str(Path(__file__).resolve().parent.parent), capture_output=True, check=True, timeout=1800)
        child_result = engine.read_json(worker_output)
    if request['operation'] == 'speech_model_prepare': return {**child_result, 'next_tool': 'speech_transcribe_submit'}
    progress('validating_word_timestamps', 0, 1)
    segments = child_result.get('segments', [])
    # Worker returns seconds relative to extracted WAV; normalize exactly once.
    for segment in segments:
        for word in segment.get('words', []):
            word['start'] = round(word['start'] + a, 6); word['end'] = round(word['end'] + a, 6)
    words, sentences, errors = build_sentences(segments, a, b)
    if errors: raise ValueError('ASR word timestamp validation failed: ' + ', '.join(errors[:8]))
    media_ops.asset(project_id, request['asset_id'], strong=True, progress=progress)
    result = {'operation': 'speech_transcribe', 'version': VERSION, 'request': copy.deepcopy(request),
        'asset_id': request['asset_id'], 'source_sha256': asset['identity']['sha256'],
        'source_start': a, 'source_end': b, 'source_duration': asset['probe']['duration'],
        'time_origin': asset['probe'].get('time_origin', 'container-relative seconds'),
        'speech_status': 'speech_detected' if words else 'no_audio' if not asset['probe'].get('has_audio') else 'no_speech_detected',
        'words': words, 'sentences': sentences, 'timestamp_errors': errors,
        'backend': {k: child_result.get(k) for k in ('engine', 'model', 'model_identity', 'device', 'compute_type', 'fallback_reason')},
        'audio_evidence': {'path': str(audio_path), 'sha256': media_ops.digest(audio_path)[0]} if audio_path else None,
        'human_listened': False, 'scope': 'Automatic transcript and structural sentence proposals; word times are estimates, meaning/emotion/listening are unreviewed.'}
    resources.record({'event': 'asr', **result['backend'], 'heavy_concurrency': 1})
    return result
