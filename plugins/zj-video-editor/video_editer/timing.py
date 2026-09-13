"""Source-time anchors and explicit point timing. No semantic inference."""
import copy
import math


def time_map(timeline):
    from .engine import transition_overlap_seconds, render_duration
    cursor, rows = 0.0, []
    clips = timeline.get('tracks', {}).get('main', [])
    for index, clip in enumerate(clips):
        start, end, speed = float(clip['start']), float(clip['end']), float(clip.get('playback_speed', 1))
        if not all(math.isfinite(v) for v in (start, end, speed)) or not 0 <= start < end or speed <= 0:
            raise ValueError('Invalid clip timing for time map')
        duration = render_duration(clip)
        overlap = transition_overlap_seconds(clip.get('transition', 'none')) if index < len(clips)-1 else 0
        rows.append({'clip_id': clip['id'], 'asset_id': clip['asset_id'], 'source_start': start,
                     'source_end': end, 'speed': speed, 'start': cursor, 'end': cursor+duration,
                     'outgoing_overlap': overlap, 'tail_padding': duration-(end-start)/speed})
        cursor += duration-overlap
    return {'duration': cursor, 'clips': rows, 'precision': '25fps shared clock; each source selection is preserved and padded by less than one frame, without reading extra source content.'}


def entries(timeline):
    for item in timeline.get('callouts', []):
        yield 'callout', item
    for track, rows in timeline.get('tracks', {}).items():
        if track != 'main':
            for item in rows:
                yield timeline.get('track_meta', {}).get(track, {}).get('kind', track), item


def locate(timeline, event_id):
    for kind, item in entries(timeline):
        if item.get('id') == event_id:
            return kind, item
    raise ValueError(f'Unknown timed event: {event_id}')


def check_points(points):
    if not isinstance(points, list) or not 1 <= len(points) <= 4:
        raise ValueError('points requires 1-4 individually timed entries')
    last = -1.0
    for point in points:
        if not isinstance(point, dict) or not isinstance(point.get('text'), str) or not point['text'].strip() or len(point['text']) > 7:
            raise ValueError('Each point requires nonempty text, max 7 characters')
        try:
            start, end = float(point['start']), float(point['end'])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('Every point needs explicit start and end seconds') from exc
        if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end or start < last:
            raise ValueError('Point times must be finite, positive ranges in reveal order')
        if end-start < .12-1e-9:
            raise ValueError('A point needs at least 0.12 seconds to render its entrance')
        last = start


def point_rows(event):
    if 'points' in event:
        check_points(event['points'])
        return event['points']
    # Compatibility preset only. New callout_points_add never derives a beat.
    first, second = str(event['text']).split('|')
    start, end = float(event['start']), float(event['end'])
    return [{'text': first, 'start': start, 'end': end},
            {'text': second, 'start': min(end-.38, start+.72), 'end': end}]


def resolve(timeline):
    result = copy.deepcopy(timeline)
    mapping = {r['clip_id']: r for r in time_map(result)['clips']}
    for kind, event in entries(result):
        binding = event.get('time_binding')
        if not binding:
            continue
        row = mapping.get(binding['clip_id'])
        if row is None:
            raise ValueError(f"Event {event['id']} is bound to a removed clip; detach or rebind it first")
        begin, finish = float(binding['source_start']), float(binding['source_end'])
        if not all(math.isfinite(x) for x in (begin, finish)) or not row['source_start']-1e-9 <= begin < finish <= row['source_end']+1e-9:
            raise ValueError(f"Event {event['id']} anchor falls outside the trimmed source clip")
        def output(value):
            if not math.isfinite(float(value)) or not begin-1e-9 <= float(value) <= finish+1e-9:
                raise ValueError(f"Event {event['id']} has a key/point outside its source anchor")
            return row['start']+(float(value)-row['source_start'])/row['speed']
        event['start'], event['end'] = output(begin), output(finish)
        if kind == 'audio':
            event['duration'] = event['end']-event['start']
        if event.get('keyframes'):
            keys = binding.get('keyframe_source_times', [])
            if len(keys) != len(event['keyframes']):
                raise ValueError('Bound overlay keys changed; detach and rebind')
            for key, value in zip(event['keyframes'], keys):
                key['at'] = output(value)
        if 'points' in event:
            keys = binding.get('point_source_times', [])
            if len(keys) != len(event['points']):
                raise ValueError('Bound point timings changed; detach and rebind')
            for point, pair in zip(event['points'], keys):
                point.update(start=output(pair[0]), end=output(pair[1]))
            check_points(event['points'])
    return result


def bind(timeline, event_id, clip_id, source_start, source_end):
    kind, event = locate(timeline, event_id)
    if event.get('time_binding'):
        raise ValueError('Detach the existing binding before rebinding')
    row = next((r for r in time_map(timeline)['clips'] if r['clip_id'] == clip_id), None)
    if row is None:
        raise ValueError('Unknown anchor clip')
    binding = {'clip_id': clip_id, 'source_start': source_start, 'source_end': source_end}
    def source(value):
        return row['source_start']+(float(value)-row['start'])*row['speed']
    if event.get('keyframes'):
        binding['keyframe_source_times'] = [source(k['at']) for k in event['keyframes']]
    if 'points' in event:
        binding['point_source_times'] = [[source(p['start']), source(p['end'])] for p in event['points']]
    event['time_binding'] = binding
    resolve(timeline)  # Reject partial/clipped anchors without changing disk state.


def rebind_split(timeline, left_id, right_id, split_source):
    for _, event in entries(timeline):
        binding = event.get('time_binding')
        if not binding or binding['clip_id'] != left_id:
            continue
        if float(binding['source_start']) >= split_source-1e-9:
            binding['clip_id'] = right_id
        elif float(binding['source_end']) > split_source+1e-9:
            raise ValueError(f"Event {event['id']} spans the split; detach or split that event explicitly")
