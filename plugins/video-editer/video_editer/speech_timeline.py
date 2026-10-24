"""Source-relative ASR sentences and uncovered intervals, never visual approval."""
import math

from . import speech


def get(project_id, job_id, min_gap_seconds=2.0, offset=0, limit=40):
    if isinstance(min_gap_seconds, bool) or not isinstance(min_gap_seconds, (int, float)) or not math.isfinite(min_gap_seconds) or not .1 <= min_gap_seconds <= 60:
        raise ValueError('min_gap_seconds must be finite and between 0.1 and 60')
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Invalid pagination')
    value, checksum = speech.report(project_id, job_id)
    lo, hi = value['source_start'], value['source_end']
    intervals = []
    for word in value['words']:
        start, end = max(lo, word['start']), min(hi, word['end'])
        if end > start:
            intervals.append((start, end))
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    sentences = value['sentences']
    rows = [{'kind': 'sentence', **row} for row in sentences]
    cursor = lo
    gaps = []
    for start, end in merged + [[hi, hi]]:
        if start - cursor + 1e-6 >= min_gap_seconds:
            before = [s for s in sentences if s['end'] <= cursor + 1e-6]
            after = [s for s in sentences if s['start'] >= start - 1e-6]
            gaps.append({'kind': 'asr_uncovered', 'gap_id': f'gap_{len(gaps):04d}',
                         'start': round(cursor, 6), 'end': round(start, 6), 'duration': round(start - cursor, 6),
                         'previous_sentence_id': before[-1]['sentence_id'] if before else None,
                         'next_sentence_id': after[0]['sentence_id'] if after else None,
                         'audio_silence_verified': False, 'natural_display_verified': False,
                         'review_status': 'unreviewed'})
        cursor = max(cursor, end)
    rows += gaps
    rows.sort(key=lambda r: (r['start'], r['end'], r['kind']))
    return {'project_id': project_id, 'transcript_job_id': job_id, 'asset_id': value['asset_id'],
            'source_sha256': value['source_sha256'], 'transcript_sha256': checksum,
            'source_coverage': {'start': lo, 'end': hi, 'duration': value['source_duration']},
            'speech_status': value['speech_status'], 'sentence_count': len(sentences), 'gap_count': len(gaps),
            'items': rows[offset:offset + limit], 'total': len(rows),
            'next_offset': offset + limit if offset + limit < len(rows) else None,
            'time_origin': 'original_source_seconds',
            'required_actions': ['Only this transcribed window is covered; transcribe remaining windows before selecting from them.',
                                 'ASR-uncovered is a search hint, not proof of silence, closed mouth, full body or no burned captions. Inspect source frames and continuous footage.',
                                 'Choose a full-body natural display for hook_picture and an independent complete source quote for hook_voice; do not mute talking footage and call it display.',
                                 'Use speech_candidates_get for quote context, speech_range_validate for sentence IDs, and queued commerce candidate inspection before editing.']}
