"""Portable review receipts. Validate provenance/coverage, never invent perception.

A receipt is an export of an actually received tool result or user feedback.
Local validation cannot authenticate a host or prove a model understood media.
There is intentionally no tool that mints an approval from a method label.
"""
import math
from pathlib import Path
from . import engine


def load(paths):
    from .delivery_review import fingerprint
    if not isinstance(paths, list) or len(paths) > 16:
        raise ValueError('review_receipt_paths must contain up to 16 received review records')
    records, files = [], []
    for path in paths:
        file = fingerprint(path)
        if file['size'] > 1024 * 1024:
            raise ValueError('Review receipt exceeds 1 MiB')
        row = engine.read_json(Path(path))
        if not isinstance(row, dict):
            raise ValueError('Receipt must be a JSON object')
        origin = row.get('provenance', {}) if isinstance(row, dict) else {}
        if not isinstance(origin, dict):
            raise ValueError('Receipt provenance must be an object')
        if (row.get('schema_version') != 1 or origin.get('kind') not in ('tool_result', 'user_feedback')
                or not isinstance(origin.get('reference'), str) or not origin['reference'].strip()
                or not isinstance(origin.get('producer'), str) or not origin['producer'].strip()
                or row.get('modality') not in ('video', 'audio', 'audiovisual', 'frames', 'user_feedback')
                or not isinstance(row.get('observation'), str) or not row['observation'].strip()
                or len(row['observation']) > 4000 or len(origin['reference']) > 512 or len(origin['producer']) > 200):
            raise ValueError('Receipt needs actual result/message provenance, modality and an observation')
        if (row['modality'] == 'user_feedback') != (origin['kind'] == 'user_feedback'):
            raise ValueError('User feedback must cite a user message, not a tool result')
        media = row.get('media', {})
        if not isinstance(media, dict) or not media.get('path') or not media.get('sha256'):
            raise ValueError('Receipt must bind the media actually reviewed')
        bound = fingerprint(media['path'])
        if bound['sha256'] != media['sha256']:
            raise ValueError('Reviewed media bytes changed')
        ranges = row.get('ranges')
        if not isinstance(ranges, list) or not 1 <= len(ranges) <= 128:
            raise ValueError('Receipt needs reviewed time ranges in this media clock')
        for span in ranges:
            if (not isinstance(span, list) or len(span) != 2
                    or any(type(x) not in (int, float) or not math.isfinite(x) for x in span)
                    or not 0 <= span[0] < span[1]):
                raise ValueError('Invalid reviewed range')
        records.append({k: row[k] for k in ('provenance', 'media', 'modality', 'ranges', 'observation')})
        files.extend([file, bound])
    return records, files


def covers(records, method, media, start, end):
    needs = {'continuous_review': ('video',), 'listening': ('audio',),
             'continuous_review_and_listening': ('video', 'audio'),
             'frame_review': ('frames',), 'user_feedback': ('user_feedback',)}.get(method, ())
    for modality in needs:
        intervals = []
        for row in records:
            if row['media']['sha256'] not in media:
                continue
            actual = row['modality']
            if (actual != modality and actual != 'user_feedback'
                    and not (actual == 'audiovisual' and modality in ('video', 'audio', 'frames'))
                    and not (actual == 'video' and modality == 'frames')):
                continue
            intervals.extend(row['ranges'])
        cursor = start
        for a, b in sorted(intervals):
            if a > cursor + .04:
                break
            cursor = max(cursor, b)
        if cursor < end - .04:
            return False
    return bool(needs)
