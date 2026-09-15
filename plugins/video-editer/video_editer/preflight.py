"""Deterministic cross-track diagnostics; warnings never select editorial fixes."""
from pathlib import Path
from . import engine, timing, canvas, animation, visuals


def inspect(project, timeline):
    issues = []
    mapping = timing.time_map(timeline)
    total = mapping['duration']
    assets = {a['id']: a for a in project.get('materials', [])}
    c=canvas.settings(timeline.get('canvas'))
    size=(c['width'],c['height'])
    scale=min(size[0]/1080,size[1]/1920)
    def issue(code, severity, ids, message):
        issues.append({'code': code, 'severity': severity, 'event_ids': ids, 'message': message})
    for clip in timeline['tracks']['main']:
        asset = assets.get(clip['asset_id'], {})
        if not Path(asset.get('path', '')).is_file():
            issue('missing_media', 'error', [clip['id']], 'A selected source file is missing')
    clips = timeline['tracks']['main']
    for index, clip in enumerate(clips):
        previous = [c for c in clips[:index] if c['asset_id'] == clip['asset_id']]
        if previous and float(clip['start']) < float(previous[-1]['start']):
            issue('source_time_reversal', 'warning', [previous[-1]['id'], clip['id']], 'Same-source selections move backwards; verify this replay is intentional.')
        for other in previous:
            overlap = min(float(other['end']), float(clip['end']))-max(float(other['start']), float(clip['start']))
            if overlap > 1e-9 and assets.get(clip['asset_id'], {}).get('kind') == 'video':
                issue('source_range_overlap', 'warning', [other['id'], clip['id']], f'Same source replays {overlap:.3f}s; verify the repeat is intentional.')
    boxes = []
    for kind, event in timing.entries(timeline):
        event_id = event['id']
        start = float(event['start'])
        asset = assets.get(event.get('asset_id'), {})
        if kind in {'overlay', 'audio'} and not Path(asset.get('path', '')).is_file():
            issue('missing_media', 'error', [event_id], 'Timed media file is missing')
            continue
        end = float(event.get('end', start))
        if kind == 'audio':
            probe = engine.ffprobe(Path(asset['path']))
            available = float(probe.get('duration') or 0)-float(event.get('source_start', 0))
            duration = float(event.get('duration', 0))
            if available <= 0 or (duration > 0 and duration > available+.04):
                issue('audio_source_bounds', 'error', [event_id], 'Audio source selection exceeds available media')
            if not probe.get('has_audio'):
                issue('missing_audio_stream', 'error', [event_id], 'Selected audio track has no audio stream')
            end = start+(duration if duration else min(max(0, available), max(0, total-start)))
        if start >= total-1e-9 or end > total+.04:
            issue('event_outside_output', 'error', [event_id], f'Event [{start:.3f},{end:.3f}) exceeds output duration {total:.3f}s')
        if kind == 'overlay':
            if asset.get('kind')=='video':
                available=float(engine.ffprobe(Path(asset['path'])).get('duration') or 0)-float(event.get('source_start',0))
                if end-start > available+.04:
                    issue('overlay_source_bounds','error',[event_id],'Overlay video ends before the requested event; trim explicitly')
            if any(s['opacity']>0 for _,s in animation.states(event)):
                boxes.append((event_id,start,end,animation.envelope(event,size)))
        elif kind == 'callout':
            right = event.get('position') == 'product_right'
            if event.get('template') in visuals.CALLOUTS:
                x,y,w,h=visuals.callout_box(event,size)
                boxes.append((event_id,start,end,(x/size[0],y/size[1],(x+w)/size[0],(y+h)/size[1])))
            elif event.get('template') == 'point_list':
                for index, point in enumerate(timing.point_rows(event)):
                    x, y = (size[0]-532*scale if right else 32*scale)/size[0], (640+(232 if 'points' in event else 202)*index)/1920
                    boxes.append((event_id, point['start'], point['end'], (x, y, x+500*scale/size[0], y+214*scale/size[1])))
            else:
                x = .57 if right else .025
                compact = event.get('template') in {'comic_burst','comic_bubble','celebrate_cloud','number_3d','promo_3d','entrance_card','either_or'}
                y = .17 if compact else .33
                boxes.append((event_id, start, end, (x, y, min(1, x+.405), y+.17)))
        elif kind == 'caption':
            boxes.append((event_id, start, end, (.04, .86, .96, .98)))
    seen = set()
    for i, (aid, a0, a1, a) in enumerate(boxes):
        for bid, b0, b1, b in boxes[i+1:]:
            pair = tuple(sorted((aid, bid)))
            if aid == bid or pair in seen or max(a0, b0) >= min(a1, b1)-1e-9:
                continue
            if max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3]):
                seen.add(pair)
                issue('possible_visual_overlap', 'warning', list(pair), 'Visible regions overlap; inspect preview or explicitly reposition. Bounds are conservative, not product recognition.')
    audio = [e for kind, e in timing.entries(timeline) if kind == 'audio']
    gain = float(timeline.get('audio_config', {}).get('source_volume', 1))
    if any(gain+float(e.get('volume', 1)) > 1 for e in audio):
        issue('audio_headroom', 'warning', [e['id'] for e in audio], 'Additive mix gain may exceed headroom. Inspect audio peaks; no gain was changed.')
    return {'duration': total, 'time_map': mapping['clips'], 'issues': issues,
            'errors': [i['message'] for i in issues if i['severity'] == 'error'],
            'warnings': [i for i in issues if i['severity'] == 'warning']}
