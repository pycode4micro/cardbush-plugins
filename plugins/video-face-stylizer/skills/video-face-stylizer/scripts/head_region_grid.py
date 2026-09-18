"""Extract a diagnostic frame with a normalized grid and optional mask ellipse."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import cv2


def render_grid(frame, grid_percent=5, box=None):
    if grid_percent < 1 or grid_percent > 50:
        raise ValueError('grid-percent must be between 1 and 50.')
    if box is not None:
        if len(box) != 4 or not all(math.isfinite(v) and 0 <= v <= 1 for v in box):
            raise ValueError('box requires four finite coordinates between 0 and 1.')
        if box[0] >= box[2] or box[1] >= box[3]:
            raise ValueError('box must have positive width and height.')
    result = frame.copy()
    h, w = result.shape[:2]
    scale = max(.3, min(w, h) / 1400)

    def label(text, origin):
        for color, thickness in [((0, 0, 0), 3), ((255, 255, 255), 1)]:
            cv2.putText(result, text, origin, cv2.FONT_HERSHEY_SIMPLEX,
                        scale, color, thickness, cv2.LINE_AA)

    for percent in range(grid_percent, 100, grid_percent):
        x, y = min(w - 1, round(w * percent / 100)), min(h - 1, round(h * percent / 100))
        cv2.line(result, (x, 0), (x, h - 1), (140, 140, 140), 1)
        cv2.line(result, (0, y), (w - 1, y), (140, 140, 140), 1)
        if percent % 10 == 0:
            label(f'{percent / 100:.1f}', (x + 2, max(12, round(20 * scale))))
            label(f'{percent / 100:.1f}', (2, max(12, y - 2)))
    if box is not None:
        left, top, right, bottom = box
        # Match ellipse_mask's geometry, including rounding, not a rectangular fill.
        center = (round((left + right) * w / 2), round((top + bottom) * h / 2))
        radii = (max(1, round((right - left) * w / 2)), max(1, round((bottom - top) * h / 2)))
        cv2.rectangle(result, (round(left * w), round(top * h)),
                      (round(right * w), round(bottom * h)), (255, 120, 0), 1)
        cv2.ellipse(result, center, radii, 0, 0, 360, (0, 230, 255), 2)
    return result


def extract_grid(input_path, seconds, output_path, grid_percent=5, box=None):
    source, output = Path(input_path).resolve(), Path(output_path).resolve()
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError('seconds must be finite and nonnegative.')
    if not source.is_file():
        raise ValueError('Input video does not exist.')
    if output.suffix.lower() != '.png':
        raise ValueError('Output must be a new PNG file.')
    if output.exists():
        raise FileExistsError(f'Refusing to overwrite: {output}')
    cap = cv2.VideoCapture(str(source), cv2.CAP_FFMPEG,
                           [cv2.CAP_PROP_HW_ACCELERATION, cv2.VIDEO_ACCELERATION_NONE])
    try:
        if not cap.isOpened():
            raise ValueError('Cannot open input video.')
        fps, frame_count = cap.get(cv2.CAP_PROP_FPS), cap.get(cv2.CAP_PROP_FRAME_COUNT)
        if math.isfinite(fps) and fps > 0 and frame_count > 0 and seconds >= frame_count / fps:
            raise ValueError('Requested timestamp is outside the video.')
        if not cap.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000):
            raise ValueError('Decoder could not seek to the requested timestamp.')
        ok, frame = cap.read()
        if not ok:
            raise ValueError('No decodable frame at the requested timestamp.')
        actual_ms, next_frame = cap.get(cv2.CAP_PROP_POS_MSEC), cap.get(cv2.CAP_PROP_POS_FRAMES)
    finally:
        cap.release()
    diagnostic = render_grid(frame, grid_percent, box)
    ok, encoded = cv2.imencode('.png', diagnostic)
    if not ok:
        raise ValueError('Could not encode diagnostic PNG.')
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb') as stream:
        stream.write(encoded.tobytes())
    return {
        'source': str(source), 'output': str(output), 'requested_seconds': seconds,
        'decoded_seconds': actual_ms / 1000 if math.isfinite(actual_ms) and actual_ms >= 0 else None,
        'frame_index': round(next_frame) - 1 if math.isfinite(next_frame) and next_frame >= 1 else None,
        'width': frame.shape[1], 'height': frame.shape[0], 'grid_percent': grid_percent,
        'box': box, 'mask_shape': 'ellipse' if box is not None else None,
        'note': 'Decoder-reported position; verify it against the requested time. Diagnostic overlay only, not a processed reference video.',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--seconds', type=float, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--grid-percent', type=int, default=5)
    parser.add_argument('--box', type=float, nargs=4, metavar=('LEFT', 'TOP', 'RIGHT', 'BOTTOM'))
    args = parser.parse_args()
    try:
        result = extract_grid(args.input, args.seconds, args.output, args.grid_percent, args.box)
    except (ValueError, OSError, cv2.error) as exc:
        parser.exit(2, f'error: {exc}\n')
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
