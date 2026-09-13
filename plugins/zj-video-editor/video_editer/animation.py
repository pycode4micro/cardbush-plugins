"""Deterministic transform curves, evaluated on the rendered frame clock."""
import math

EASINGS = {'linear', 'ease_in', 'ease_out', 'ease_in_out', 'hold'}
FIELDS = ('x', 'y', 'scale', 'rotation', 'opacity')


def ease(t, mode):
    t = max(0., min(1., t))
    if mode == 'ease_in': return t*t
    if mode == 'ease_out': return 1-(1-t)**2
    if mode == 'ease_in_out': return t*t*(3-2*t)
    if mode == 'hold': return 0. if t < 1 else 1.
    return t


def states(item):
    state = {k: float(item.get(k, 1 if k in {'scale', 'opacity'} else 0)) for k in FIELDS}
    points = {float(item['start']): {**state, 'easing': 'linear'}}
    for key in sorted(item.get('keyframes', []), key=lambda k: k['at']):
        state.update({k: float(key[k]) for k in FIELDS if k in key})
        points[float(key['at'])] = {**state, 'easing': key.get('easing', 'linear')}
    return sorted(points.items())


def value(item, at):
    return curve_value(states(item),at)


def curve_value(points,at):
    if at <= points[0][0]: return points[0][1]
    for (a, left), (b, right) in zip(points, points[1:]):
        if at < b:
            p = ease((at-a)/(b-a), right['easing'])
            return {k: left[k]+(right[k]-left[k])*p for k in FIELDS}
    return points[-1][1]


def rectangle(item, state, canvas=(1080, 1920)):
    w, h = canvas
    bw, bh = float(item['width'])*w, float(item['height'])*h
    angle = math.radians(state.get('rotation', 0))
    rw = (abs(math.cos(angle))*bw+abs(math.sin(angle))*bh)*state.get('scale', 1)
    rh = (abs(math.sin(angle))*bw+abs(math.cos(angle))*bh)*state.get('scale', 1)
    cx, cy = state['x']*w+bw/2, state['y']*h+bh/2
    return ((cx-rw/2)/w, (cy-rh/2)/h, (cx+rw/2)/w, (cy+rh/2)/h)


def sample_times(item):
    start, end = float(item['start']), float(item['end'])
    return sorted({start, end, *(float(k['at']) for k in item.get('keyframes', [])),
                   *(i/25 for i in range(math.ceil(start*25), math.ceil(end*25)))})


def validate(item, canvas=(1080, 1920)):
    if item.get('mask', 'none') not in {'none', 'circle', 'rounded_rect'}:
        raise ValueError('Unsupported overlay mask')
    points=states(item)
    for _, state in points:
        if not all(math.isfinite(float(state[k])) for k in FIELDS):
            raise ValueError('Transform values must be finite')
        if not .05 <= state['scale'] <= 4 or not -360 <= state['rotation'] <= 360 or not 0 <= state['opacity'] <= 1:
            raise ValueError('Transform scale 0.05..4, rotation -360..360, opacity 0..1 required')
        if state['easing'] not in EASINGS:
            raise ValueError('Unsupported easing curve')
    if float(item['end'])-float(item['start']) > 3600:
        raise ValueError('An overlay may last at most one hour')
    for at in sample_times(item):
        box = rectangle(item, curve_value(points, at), canvas)
        if min(box) < -1e-9 or max(box) > 1+1e-9:
            raise ValueError('Entire transformed overlay must fit inside the canvas at every rendered frame')


def envelope(item, canvas=(1080,1920)):
    points=states(item)
    boxes = [rectangle(item, curve_value(points, at), canvas) for at in sample_times(item)]
    return min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)
