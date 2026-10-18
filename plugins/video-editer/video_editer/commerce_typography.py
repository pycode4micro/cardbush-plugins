"""Transparent editable commerce lettering; explicit copy and reviewed placement."""
import re
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import canvas, commerce, media_ops, timing, visuals
from .contracts import check_overlay
from .locking import project_write

UI = [[0, 0, 1, .06], [0, .94, 1, 1], [.84, .41, 1, .94], [.02, .77, .80, .94]]


def overlaps(a, b):
    return max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3])


@project_write
def add(project_id, text, emphasis, support, style, start, end, box, theme_colors, reserved_boxes):
    from . import mcp_server as server
    commerce.load(project_id)
    if style not in ('hook_center', 'keyword_stack', 'detail_underline'): raise ValueError('Unknown transparent lettering style')
    if not isinstance(text, str) or not 1 <= len(text) <= 24 or '\n' in text: raise ValueError('Use one readable phrase, 1–24 characters')
    if not isinstance(support, str) or len(support) > 18 or '\n' in support: raise ValueError('Support text is optional, max 18 characters')
    if emphasis and emphasis not in text: raise ValueError('emphasis must occur in text')
    if not isinstance(theme_colors, list) or not 1 <= len(theme_colors) <= 3 or any(not isinstance(c, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', c) for c in theme_colors):
        raise ValueError('Provide 1–3 #RRGGBB theme colors')
    def rectangle(v):
        if not isinstance(v, list) or len(v) != 4 or any(type(n) not in (int, float) for n in v) or not 0 <= v[0] < v[2] <= 1 or not 0 <= v[1] < v[3] <= 1:
            raise ValueError('Rectangles are normalized [x0,y0,x1,y1] inside the canvas')
        return v
    rectangle(box)
    if not isinstance(reserved_boxes, list) or len(reserved_boxes) > 32: raise ValueError('Provide observed face, product-action and burned-caption exclusion boxes; [] only after actual review')
    for b in reserved_boxes: rectangle(b)
    x0, y0, x1, y1 = box
    if style == 'hook_center' and (abs((x0 + x1) / 2 - .5) > .001 or not .50 <= y0 < y1 <= .77):
        raise ValueError('Hook lettering centers on full canvas x=0.5 in lower allowed region; narrow/wrap, do not shift left')
    if any(overlaps(box, b) for b in UI + reserved_boxes): raise ValueError('Text rectangle conflicts with platform UI or supplied observed exclusions')
    project = server._read(project_id); timeline = server._timeline(project)
    for existing in timeline['tracks'].get('overlays', []):
        if existing.get('commerce_text') and existing['commerce_text']['theme_colors'] != theme_colors:
            raise ValueError('Reuse this video\'s existing palette; a different video may use a different theme')
    if any(c.get('time_binding') and max(start, c['start']) < min(end, c['end']) for c in timeline['tracks'].get('captions', [])):
        raise ValueError('Detach bound overlapping captions before typography insertion')
    if not 0 <= start < end <= timing.time_map(timeline)['duration'] or end - start < .5:
        raise ValueError('Text timing must fit timeline and remain readable for at least 0.5s')
    settings = canvas.settings(timeline['canvas']); width = max(1, round((x1 - x0) * settings['width']))
    height = max(1, round((y1 - y0) * settings['height']))
    image = Image.new('RGBA', (width, height)); draw = ImageDraw.Draw(image)
    font_path = visuals.font_file(); stroke = max(1, round(width / 130)); pad = stroke * 3
    # Fit the actual glyph block, including the support and underline, rather
    # than pinning the support to the bottom of a potentially tall safe box.
    # A wide safety box must not create a large empty gap inside the lettering.
    for size in range(max(12, height - pad * 2), 10, -1):
        font = ImageFont.truetype(font_path, size)
        bounds = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
        ink_height = bounds[3] - bounds[1]
        gap = max(2, round(size * .10))
        line_width = max(2, stroke)
        decoration_height = gap + line_width if style == 'detail_underline' else 0
        small = ImageFont.truetype(font_path, max(11, int(size * .48))) if support else None
        sb = draw.textbbox((0, 0), support, font=small, stroke_width=max(1, stroke // 2)) if support else (0, 0, 0, 0)
        total_height = ink_height + decoration_height + (gap + sb[3] - sb[1] if support else 0)
        if (bounds[2] - bounds[0] <= width - pad * 2
                and sb[2] - sb[0] <= width - pad * 2
                and total_height <= height - pad * 2): break
    else: raise ValueError('Text does not fit the reviewed box; shorten the copy or choose another box')
    centered = style == 'hook_center'
    x = (width - (bounds[2] - bounds[0])) / 2 - bounds[0] if centered else pad - bounds[0]
    y = pad - bounds[1]
    split = text.find(emphasis) if emphasis else -1
    parts = [(text, theme_colors[0])] if split < 0 else [(text[:split], theme_colors[0]),
        (emphasis, theme_colors[min(1, len(theme_colors) - 1)]), (text[split + len(emphasis):], theme_colors[0])]
    for part, color in parts:
        draw.text((x, y), part, font=font, fill=color, stroke_width=stroke, stroke_fill='#26211F')
        x += draw.textlength(part, font=font)
    cursor = pad + ink_height
    if style == 'detail_underline':
        line_y = cursor + gap + line_width / 2
        draw.line((pad, line_y, width - pad, line_y), fill=theme_colors[-1], width=line_width)
        cursor += decoration_height
    if support:
        support_top = cursor + gap
        sw = sb[2] - sb[0]
        sx = (width - sw) / 2 - sb[0] if centered else pad - sb[0]
        draw.text((sx, support_top - sb[1]), support, font=small,
                  fill=theme_colors[0], stroke_width=max(1, stroke // 2), stroke_fill='#26211F')
    folder = server._folder(project_id) / 'materials'; folder.mkdir(exist_ok=True)
    asset_id = 'mat_' + uuid.uuid4().hex[:16]; output = folder / (asset_id + '.png')
    image.save(output)
    asset = {'id': asset_id, 'name': output.name, 'kind': 'image', 'path': str(output), 'probe': {},
             'identity': {'sha256': media_ops.digest(output)[0], 'stat': media_ops.stat_signature(output)}}
    undo = server._snapshot(project, 'before commerce_typography_add')
    project['materials'].append(asset)
    # Animate only within the reviewed box, without overshoot into exclusions.
    enter = min(.2, (end - start) / 4); leave = min(.15, (end - start) / 4)
    item = {'id': 'overlay_' + uuid.uuid4().hex[:10], 'asset_id': asset_id, 'start': start, 'end': end,
        'x': x0, 'y': y0, 'width': x1 - x0, 'height': y1 - y0, 'opacity': 1,
        'keyframes': [{'at': start, 'scale': .90 if style == 'keyword_stack' else .96, 'opacity': 0},
                      {'at': start + enter, 'scale': 1, 'opacity': 1},
                      {'at': end - leave, 'scale': 1, 'opacity': 1}, {'at': end, 'scale': 1, 'opacity': 0}],
        'commerce_text': {'text': text, 'support': support, 'emphasis': emphasis, 'style': style,
                          'theme_colors': theme_colors, 'reserved_boxes': reserved_boxes,
                          'layout': {'main_font_px': size, 'main_ink_bottom': pad + ink_height,
                                     'support_font_px': small.size if support else None,
                                     'support_ink_top': support_top if support else None,
                                     'block_height_px': total_height, 'padding_px': pad}}}
    check_overlay(item, (settings['width'], settings['height']))
    # Split editable caption intervals so no same-time duplicate captions or exit flashes remain.
    captions = []
    for caption in timeline['tracks'].get('captions', []):
        if max(start, caption['start']) >= min(end, caption['end']): captions.append(caption); continue
        if caption.get('time_binding'): raise ValueError('Detach bound overlapping captions before typography insertion')
        if caption['start'] < start: captions.append({**caption, 'end': start})
        if caption['end'] > end: captions.append({**caption, 'id': 'subtitle_' + uuid.uuid4().hex[:10], 'start': end})
    timeline['tracks']['captions'] = captions
    timeline['tracks']['overlays'].append(item)
    return {**server._commit(project_id, project, 'commerce_typography_add', {'overlay_id': item['id']}, undo),
            'overlay': item, 'asset': asset, 'scope': 'Geometry checked against supplied exclusions; actual product/face/text visibility still requires rendered review.'}
