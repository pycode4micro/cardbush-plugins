"""Strict QSV video decoding, scaling and H.264 encoding.

The CPU transports raw frames and multiplexes packets; no software codec or
software image filter is present in these video command lines.
"""
from __future__ import annotations

import subprocess
import re
from pathlib import Path
import cv2
from video_face_stylizer.processes import ffmpeg_executable, hidden_process_options

CODECS = {'avc1': 'h264_qsv', 'h264': 'h264_qsv', 'h265': 'hevc_qsv',
          'hevc': 'hevc_qsv', 'hvc1': 'hevc_qsv', 'hev1': 'hevc_qsv',
          'av01': 'av1_qsv', 'vp09': 'vp9_qsv', 'vp90': 'vp9_qsv',
          'mpg2': 'mpeg2_qsv', 'mpeg': 'mpeg2_qsv', 'mjpg': 'mjpeg_qsv'}


def color_format(stream_description):
    # FFmpeg's stream-copy header probe does not decode video pixels. Untagged
    # SDR YUV follows the conventional limited-range BT.601 decoder default.
    if re.search(r'bt2020|smpte2084|arib-std-b67', stream_description):
        raise ValueError('GPU color conversion currently supports SDR BT.601/709 only; HDR/wide-gamut input is not enabled.')
    matrix = re.search(r'\b(bt709|smpte170m|bt470bg|fcc|smpte240m|gbr|ycgco)\b', stream_description)
    matrix = matrix.group(1) if matrix else 'smpte170m'
    weights = {'bt709': (.2126, .0722), 'smpte170m': (.299, .114), 'bt470bg': (.299, .114)}
    if matrix not in weights:
        raise ValueError(f'GPU color matrix {matrix!r} is not supported.')
    full = bool(re.search(r'\b(pc|jpeg)\b|\byuvj', stream_description))
    return {'weights': weights[matrix], 'full_range': full, 'matrix': matrix}


def source_color(source):
    result = subprocess.run([ffmpeg_executable(), '-hide_banner', '-nostdin', '-i', str(source),
                             '-map', '0:v:0', '-c:v', 'copy', '-frames:v', '0', '-f', 'null', '-'],
                            capture_output=True, timeout=20, **hidden_process_options())
    lines = result.stderr.decode(errors='replace').splitlines()
    description = next((line.split('Video:', 1)[1] for line in lines if 'Video:' in line), None)
    if result.returncode or description is None:
        raise ValueError('Cannot inspect video color metadata: '+'\n'.join(lines)[-1200:])
    return color_format(description)


def metadata(source):
    # Header/frame-count inspection only. Actual frames never use VideoCapture.
    capture = cv2.VideoCapture(str(source), cv2.CAP_FFMPEG,
                              [cv2.CAP_PROP_HW_ACCELERATION, cv2.VIDEO_ACCELERATION_NONE])
    try:
        if not capture.isOpened():
            raise ValueError('Cannot read video header: '+str(source))
        fourcc = int(capture.get(cv2.CAP_PROP_FOURCC))
        return {'width': int(capture.get(3)), 'height': int(capture.get(4)),
                'fps': capture.get(cv2.CAP_PROP_FPS), 'frames': int(capture.get(7)),
                'codec': ''.join(chr((fourcc >> 8*i) & 255) for i in range(4)).lower(),
                'color': source_color(source)}
    finally:
        capture.release()


def decoder_command(source, codec, width, height, frames, start=0):
    if codec not in CODECS:
        raise ValueError(f'Input codec {codec!r} has no configured GPU decoder. Choose CPU explicitly for this video.')
    return [ffmpeg_executable(), '-hide_banner', '-loglevel', 'error', '-nostdin',
            '-hwaccel', 'qsv', '-hwaccel_output_format', 'qsv', '-c:v', CODECS[codec],
            '-ss', str(start), '-i', str(source), '-an', '-sn', '-dn',
            '-vf', f'vpp_qsv=w={width}:h={height}:format=nv12,hwdownload,format=nv12',
            '-fps_mode', 'passthrough', '-frames:v', str(frames),
            '-f', 'rawvideo', '-pix_fmt', 'nv12', 'pipe:1']


def encoder_command(source, target, width, height, fps, seconds, start=0):
    return [ffmpeg_executable(), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
            '-init_hw_device', 'qsv=hw', '-filter_hw_device', 'hw',
            '-f', 'rawvideo', '-pix_fmt', 'nv12', '-s', f'{width}x{height}',
            '-r', str(fps), '-color_range', 'tv', '-colorspace', 'bt709', '-i', 'pipe:0',
            '-ss', str(start), '-i', str(source),
            '-map', '0:v:0', '-map', '1:a:0?',
            '-vf', 'hwupload=extra_hw_frames=32',
            '-c:v', 'h264_qsv', '-preset', 'fast', '-global_quality', '18',
            '-look_ahead', '0', '-color_range', 'tv',
            '-bsf:v', 'h264_metadata=video_full_range_flag=0:colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1',
            '-c:a', 'copy', '-t', str(seconds),
            '-movflags', '+faststart', str(target)]


def read_frame(stream, size):
    chunks = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    result = b''.join(chunks)
    if result and len(result) != size:
        raise RuntimeError('GPU decoder returned an incomplete frame.')
    return result


def probe_codecs(directory, pixels=None):
    from .gpu_frames import GPUFrameOps
    source = directory/'codec-probe.mp4'
    encoded = directory/'codec-roundtrip.mp4'
    # Generating this tiny test pattern is setup, outside any video job.
    result = subprocess.run([ffmpeg_executable(), '-v', 'error', '-f', 'lavfi',
                             '-i', 'color=c=navy:s=128x128:r=10', '-frames:v', '3',
                             '-c:v', 'h264_qsv', '-y', str(source)],
                            capture_output=True, timeout=20, **hidden_process_options())
    if result.returncode:
        raise RuntimeError('QSV encoder probe failed: '+result.stderr.decode(errors='replace')[-1200:])
    result = subprocess.run(decoder_command(source, 'avc1', 128, 128, 3),
                            capture_output=True, timeout=20, **hidden_process_options())
    if result.returncode or len(result.stdout) != 128*128*3//2*3:
        raise RuntimeError('QSV decoder/VPP probe failed: '+result.stderr.decode(errors='replace')[-1200:])
    owned_pixels = pixels is None
    if owned_pixels:
        pixels = GPUFrameOps(128, 128)
    try:
        frame_size = 128*128*3//2
        pixels.upload_nv12(result.stdout[:frame_size])
        pixels.empty_material()
        original_pixel = tuple(pixels.composite()[:3])
        if not (abs(original_pixel[0]-128) <= 5 and max(original_pixel[1:]) <= 4):
            raise RuntimeError(f'GPU NV12/RGB color probe failed: {original_pixel}')
        frame = pixels.composite(nv12=True)
        result = subprocess.run(encoder_command(source, encoded, 128, 128, 10, .3), input=frame*3,
                                capture_output=True, timeout=20, **hidden_process_options())
        if result.returncode or not encoded.is_file():
            raise RuntimeError('QSV GPU upload/encoding probe failed: '+result.stderr.decode(errors='replace')[-1200:])
        color = source_color(encoded)
        if color['matrix'] != 'bt709' or color['full_range']:
            raise RuntimeError('QSV encoder did not preserve H.264 color signaling.')
        result = subprocess.run(decoder_command(encoded, 'avc1', 128, 128, 3),
                                capture_output=True, timeout=20, **hidden_process_options())
        if result.returncode or len(result.stdout) != frame_size*3:
            raise RuntimeError('QSV roundtrip decode failed.')
        pixels.upload_nv12(result.stdout[:frame_size], color['weights'], color['full_range'])
        pixels.empty_material()
        if max(abs(a-b) for a, b in zip(pixels.composite()[:3], original_pixel)) > 5:
            raise RuntimeError('QSV encode/decode did not preserve video colors.')
    finally:
        if owned_pixels:
            pixels.close()
    return {'decoder': 'QSV hardware decoder', 'scaling_color_conversion': 'QSV VPP scaling + OpenGL NV12/RGB conversion',
            'encoder': 'h264_qsv', 'color_range': 'BT.709 limited YUV, full RGB; explicit H.264 signaling',
            'audio': 'original packets (no audio re-encoding)'}
