"""Explicit output settings. Coordinates are normalized; clock remains 25 fps."""
import re

DEFAULT = {'width': 1080, 'height': 1920, 'fit': 'contain', 'background': '#000000'}
FPS = 25


def settings(value=None):
    result = {**DEFAULT, **(value or {})}
    if set(result) != set(DEFAULT):
        raise ValueError('Unknown canvas setting')
    for name in ('width', 'height'):
        v = result[name]
        if type(v) is not int or not 256 <= v <= 3840 or v % 2:
            raise ValueError('Canvas dimensions must be even integers between 256 and 3840')
    if result['width']*result['height'] > 8294400:
        raise ValueError('Canvas may not exceed 8,294,400 pixels')
    if result['fit'] not in {'contain', 'cover'}:
        raise ValueError('fit must be contain or cover')
    if not isinstance(result['background'], str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', result['background']):
        raise ValueError('background must be #RRGGBB')
    return result


def fit_filter(value=None):
    c = settings(value)
    w, h = c['width'], c['height']
    if c['fit'] == 'cover':
        return f'scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1'
    return f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={c['background']},setsar=1"


def preview_size(value=None):
    c = settings(value)
    scale = min(1, 960/max(c['width'], c['height']))
    return max(2, round(c['width']*scale/2)*2), max(2, round(c['height']*scale/2)*2)
