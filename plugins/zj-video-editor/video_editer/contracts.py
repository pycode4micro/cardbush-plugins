"""Pure, model-free editing contracts shared by MCP tools and regression tests."""

import math


def finite(*values):
    return all(math.isfinite(float(value)) for value in values)


def check_overlay(item, canvas=(1080, 1920)):
    start, end = float(item['start']), float(item['end'])
    x, y, width, height = (float(item[k]) for k in ('x', 'y', 'width', 'height'))
    opacity = float(item.get('opacity', 1))
    if not finite(start, end, x, y, width, height, opacity):
        raise ValueError('Overlay values must be finite')
    if not (0 <= start < end and 0 < width <= 1 and 0 < height <= 1 and 0 <= opacity <= 1):
        raise ValueError('Invalid overlay timing, dimensions or opacity')
    def bounds(px, py):
        if not finite(px, py) or not (0 <= px <= 1 - width + 1e-9 and 0 <= py <= 1 - height + 1e-9):
            raise ValueError('Entire overlay rectangle must fit inside the canvas')
    bounds(x, y)
    times = set()
    for key in item.get('keyframes', []):
        at = float(key['at'])
        if not finite(at) or not start <= at <= end or at in times:
            raise ValueError('Keyframe times must be unique and within overlay time')
        times.add(at)
        bounds(float(key.get('x', x)), float(key.get('y', y)))
    from .animation import validate
    validate(item, canvas)


def position_expression(item, axis, pixels):
    """Piecewise linear at absolute timeline times; hold after the last key."""
    points = {float(item['start']): float(item[axis])}
    points.update({float(key['at']): float(key[axis]) for key in item.get('keyframes', [])})
    points = sorted(points.items())
    expression = f'{points[-1][1] * pixels:.8f}'
    for (t0, v0), (t1, v1) in reversed(list(zip(points, points[1:]))):
        segment = f'({v0*pixels:.8f}+({(v1-v0)*pixels:.8f})*(t-{t0:.8f})/{t1-t0:.8f})'
        expression = f'if(lt(t,{t1:.8f}),{segment},{expression})'
    return f'if(lt(t,{points[0][0]:.8f}),{points[0][1]*pixels:.8f},{expression})'


def field_changes(before, after):
    return {key: {'before': before.get(key), 'after': after.get(key),
                  'before_present': key in before, 'after_present': key in after}
            for key in sorted(set(before) | set(after))
            if (key in before) != (key in after) or before.get(key) != after.get(key)}


def entry_diff(before, after):
    old, new = {item['id']: item for item in before}, {item['id']: item for item in after}
    old_order, new_order = list(old), list(new)
    modified = {key: field_changes(old[key], new[key]) for key in old.keys() & new.keys()
                if old[key] != new[key]}
    return {'added': sorted(new.keys() - old.keys()), 'removed': sorted(old.keys() - new.keys()),
            'modified': modified, 'order_changed': old_order != new_order,
            'before_order': old_order, 'after_order': new_order}


def timeline_changes(before, after):
    old_tracks, new_tracks = before.get('tracks', {}), after.get('tracks', {})
    tracks = {name: entry_diff(old_tracks.get(name, []), new_tracks.get(name, []))
              for name in sorted(set(old_tracks) | set(new_tracks))}
    ignored = {'tracks', 'callouts', 'history', 'revision'}
    settings = field_changes({k: v for k, v in before.items() if k not in ignored},
                             {k: v for k, v in after.items() if k not in ignored})
    return {'tracks': tracks, 'clips': tracks.get('main', entry_diff([], [])),
            'callouts': entry_diff(before.get('callouts', []), after.get('callouts', [])),
            'settings': settings, 'tracks_added': sorted(new_tracks.keys() - old_tracks.keys()),
            'tracks_removed': sorted(old_tracks.keys() - new_tracks.keys())}
