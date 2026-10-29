"""Whole-task Intel/NVIDIA GPU execution, without software fallback."""
from __future__ import annotations

import json
import math
import subprocess
import time
from pathlib import Path
import numpy as np
from video_face_stylizer.models import ProcessingOptions
from video_face_stylizer.processes import hidden_process_options
from .gpu_frames import GPUFrameOps
from .gpu_vision import GPUVision
from .gpu_video import metadata, decoder_command, encoder_command, read_frame
from .head_coverage import tile_boxes
from .plaster_face import PlasterRenderer


def active_manual_boxes(regions, seconds):
    boxes = []
    for region in regions:
        if not region.start_seconds <= seconds < region.end_seconds:
            continue
        keys = region.keyframes
        box = keys[0].box
        if seconds >= keys[-1].seconds:
            box = keys[-1].box
        else:
            for first, second in zip(keys, keys[1:]):
                if first.seconds <= seconds < second.seconds:
                    fraction = (seconds-first.seconds)/(second.seconds-first.seconds)
                    box = [a*(1-fraction)+b*fraction for a, b in zip(first.box, second.box)]
                    break
        boxes.append(box)
    return boxes


class GPUPipeline:
    def __init__(self, width, height, model_dir, options, cache_dir=None):
        self.w, self.h, self.options = width, height, options
        self.frames = GPUFrameOps(width, height)
        self.backend = self.frames.backend
        self.codec_device = 0
        self.vision = self.renderer = None
        try:
            if self.backend == 'nvidia':
                from .nvidia_vision import NvidiaVision
                self.vision = NvidiaVision(model_dir, options, self.frames)
                self.codec_device = self.vision.adapter['cuda_id']
            else:
                self.vision = GPUVision(model_dir, options, cache_dir)
            self.renderer = PlasterRenderer(width, height, model_dir, self.frames.ctx)
        except Exception:
            self.close()
            raise
        self.detected = self.frames_seen = 0
        self.last_box = self.tracked = self.previous_points = None
        self.timings = {'tracking': 0., 'segmentation': 0., 'render_composite': 0.}
        self.coverage_counts = {name: 0 for name in ['person_frames', 'no_person_frames', 'full_frame_faces',
            'crop_faces', 'segmentation_crop_frames', 'segmentation_fallback_frames', 'temporal_fallback_frames',
            'manual_mask_frames', 'covered_frames', 'uncovered_frames', 'no_mask_frames', 'fallback_only_frames']}
        self.review_intervals = []

    def _review(self, reason, seconds):
        if self.review_intervals and self.review_intervals[-1]['reason'] == reason and self.review_intervals[-1]['last_frame'] == self.frames_seen-1:
            self.review_intervals[-1].update(end_seconds=seconds, last_frame=self.frames_seen)
        else:
            self.review_intervals.append({'start_seconds': seconds, 'end_seconds': seconds,
                                          'last_frame': self.frames_seen, 'reason': reason})

    def _accept(self, points, persons):
        if points is None or not np.all(np.isfinite(points)):
            return False
        center = np.mean(points[:468, :2], axis=0)
        return any(x0 <= center[0] <= x1 and y0 <= center[1] <= y1 for x0, y0, x1, y1 in persons)

    def process(self, frame, timestamp_ms, source_seconds, nv12_color=None):
        begin = time.perf_counter()
        if nv12_color is None:
            self.frames.upload(frame)
        else:
            self.frames.upload_nv12(frame, nv12_color['weights'], nv12_color['full_range'])
        persons = self.vision.persons(self.frames)
        # Match the CPU path's modest padding around detected people.
        persons = [tuple(np.clip([x0-(x1-x0)*.03, y0-(y1-y0)*.04,
                                  x1+(x1-x0)*.03, y1+(y1-y0)*.04], 0, 1)) for x0, y0, x1, y1 in persons]
        if self.options.person_filter:
            self.coverage_counts['person_frames' if persons else 'no_person_frames'] += 1
        proposals = []
        boxes = np.zeros((10, 4), np.float32)
        if persons:
            self.frames.add_segmentation(self.vision.segment(self.frames))
        if self.options.coverage == 'head':
            proposals, boxes = self.frames.components(self.options, persons)
        if persons and self.options.coverage == 'head' and not len(proposals) and self.options.tiled_segmentation:
            for x0, y0, x1, y1 in tile_boxes(self.w, self.h, self.options.detection_tile_grid):
                region = (x0/self.w, y0/self.h, x1/self.w, y1/self.h)
                self.frames.add_segmentation(self.vision.segment(self.frames, region), region)
            proposals, boxes = self.frames.components(self.options, persons)
            if len(proposals):
                self.coverage_counts['segmentation_crop_frames'] += 1
        self.timings['segmentation'] += time.perf_counter()-begin
        begin = time.perf_counter()
        points = self.vision.landmarks(self.frames, tracked=self.tracked) if persons else None
        if self._accept(points, persons):
            self.coverage_counts['full_frame_faces'] += 1
        else:
            points = None
            if persons and self.options.tiled_detection:
                searches = ([self.last_box] if self.last_box is not None else [])
                for proposal in sorted(proposals, key=lambda b: float(np.prod(b[2:]-b[:2])), reverse=True)[:8]:
                    cx, cy = (proposal[:2]+proposal[2:])/2
                    side = max((proposal[2]-proposal[0])*self.w, (proposal[3]-proposal[1])*self.h)*1.8
                    searches.append(tuple(np.clip([cx-side/self.w/2, cy-side/self.h/2,
                                                   cx+side/self.w/2, cy+side/self.h/2], 0, 1)))
                searches += [(x0/self.w, y0/self.h, x1/self.w, y1/self.h)
                             for x0, y0, x1, y1 in tile_boxes(self.w, self.h, self.options.detection_tile_grid)]
                for region in dict.fromkeys(searches):
                    candidate = self.vision.landmarks(self.frames, region)
                    if self._accept(candidate, persons):
                        points = candidate
                        self.coverage_counts['crop_faces'] += 1
                        break
        self.frames_seen += 1
        geometry = None
        if points is not None:
            self.detected += 1
            lo, hi = points[:468, :2].min(axis=0), points[:468, :2].max(axis=0)
            size = hi-lo
            self.last_box = tuple(np.clip([lo[0]-size[0]*.5, lo[1]-size[1]*.8,
                                           hi[0]+size[0]*.5, hi[1]+size[1]*.3], 0, 1))
            side = max(size*[self.w, self.h])*1.5
            center = (lo+hi)/2
            roi = (center[0]-side/self.w/2, center[1]-side/self.h/2,
                   center[0]+side/self.w/2, center[1]+side/self.h/2)
            eye = (points[263, :2]-points[33, :2])*[self.w, self.h]
            self.tracked = (roi, math.atan2(float(eye[1]), float(eye[0])))
            geometry = np.clip([lo[0]-size[0]*.12, lo[1]-size[1]*.28,
                                hi[0]+size[0]*.12, hi[1]+size[1]*.06], 0, 1)
        else:
            self.tracked = None
            if not persons:
                self.last_box = None
        self.timings['tracking'] += time.perf_counter()-begin
        begin = time.perf_counter()
        if points is not None:
            self.frames.add_segmentation(self.vision.segment(self.frames, self.last_box), self.last_box)
            if self.options.coverage == 'head':
                _, boxes = self.frames.components(self.options, persons)
        self.timings['segmentation'] += time.perf_counter()-begin
        begin = time.perf_counter()
        automatic = False
        bridged = manual = False
        if self.options.coverage == 'head':
            automatic, _, _ = self.frames.paint(self.options, boxes, geometry, [])
            bridged, cut = self.frames.temporal(timestamp_ms, self.options.temporal_hold_seconds, automatic, persons)
            if cut and points is None:
                self.last_box = None
            manual = self.frames.manual(active_manual_boxes(self.options.head_regions, source_seconds))
            covered = automatic or bridged or manual
        else:
            self.frames.empty_material()
            covered = points is not None
        if manual:
            self.coverage_counts['manual_mask_frames'] += 1
        if bridged:
            self.coverage_counts['temporal_fallback_frames'] += 1
        elif automatic and points is None:
            self.coverage_counts['segmentation_fallback_frames'] += 1
        self.coverage_counts['covered_frames' if covered else 'uncovered_frames'] += 1
        if not covered:
            self.coverage_counts['no_mask_frames'] += 1
        elif points is None:
            self.coverage_counts['fallback_only_frames'] += 1
        reason = None
        if not covered:
            reason = 'no_face_mask' if self.options.coverage == 'face' else 'person_not_detected' if self.options.person_filter and not persons else 'no_head_mask'
        elif bridged:
            reason = 'optical_flow'
        elif points is None and not manual:
            reason = 'segmentation_only'
        if reason:
            self._review(reason, source_seconds)
        mesh = self.renderer.render_texture(points) if points is not None else None
        brow = float((points[105, 1]+points[334, 1])/2) if points is not None else 0.
        output = self.frames.composite(mesh, self.options.coverage == 'face', brow, nv12=nv12_color is not None)
        self.timings['render_composite'] += time.perf_counter()-begin
        return output, {'covered': covered, 'landmarks': points}

    def close(self):
        if self.renderer is not None:
            self.renderer.close()
        if self.vision is not None:
            self.vision.close()
        self.frames.close()


def run_gpu(args):
    start = time.perf_counter()
    source, target = Path(args.input).resolve(), Path(args.output).resolve()
    if source == target:
        raise ValueError('Input and output paths must differ.')
    if args.previews:
        raise ValueError('GPU mode does not produce CPU debug preview images. Extract preview frames from the completed video with head_region_grid.py.')
    target.parent.mkdir(parents=True, exist_ok=True)
    header = metadata(source)
    w, h, fps, total = header['width'], header['height'], header['fps'], header['frames']
    if not math.isfinite(fps) or fps <= 0 or min(w, h, total) <= 0:
        raise ValueError('Input video has invalid metadata.')
    if args.max_height and h > args.max_height:
        w, h = int(round(w*args.max_height/h/2)*2), args.max_height//2*2
    if w % 2 or h % 2:
        raise ValueError('GPU H.264 requires even video dimensions; choose an even max_height or CPU explicitly.')
    count = min(total-round(args.start*fps), round(args.seconds*fps) if args.seconds else total)
    if count <= 0:
        raise ValueError('Requested time range contains no frames.')
    options = ProcessingOptions.model_validate(json.loads(args.settings))
    pipeline = GPUPipeline(w, h, Path(args.models), options, Path(args.models).parents[3]/'.gpu-cache')
    decoder = encoder = None
    processed = 0
    decode_log = target.with_suffix('.decode.log')
    encode_log = target.with_suffix('.ffmpeg.log')
    try:
        decode = decoder_command(source, header['codec'], w, h, count, args.start,
                                 pipeline.backend, pipeline.codec_device)
        with decode_log.open('w', encoding='utf-8') as dlog, encode_log.open('w', encoding='utf-8') as elog:
            decoder = subprocess.Popen(decode, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=dlog, **hidden_process_options())
            encoder = subprocess.Popen(encoder_command(source, target, w, h, fps, count/fps, args.start,
                                                       pipeline.backend, pipeline.codec_device),
                                       stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=elog, **hidden_process_options())
            print(json.dumps({'stage': 'start', 'frames': count, 'fps': fps, 'size': [w, h],
                              'renderer': pipeline.renderer.renderer}), flush=True)
            for index in range(count):
                frame = read_frame(decoder.stdout, w*h*3//2)
                if not frame:
                    break
                output, _ = pipeline.process(frame, round(index*1000/fps), args.start+index/fps, header['color'])
                encoder.stdin.write(output)
                processed += 1
                if index % 90 == 0:
                    print(json.dumps({'frame': index+1, 'total': count, 'elapsed_s': round(time.perf_counter()-start, 2),
                                      'detected': pipeline.detected, 'coverage': pipeline.coverage_counts}), flush=True)
            decoder.stdout.close()
            encoder.stdin.close()
            decoder.wait(timeout=30)
            encoder.wait(timeout=60)
        if decoder.returncode or encoder.returncode or processed != count:
            raise RuntimeError(f'GPU video processing failed ({processed}/{count} frames). '
                               +decode_log.read_text(errors='replace')[-1500:]+encode_log.read_text(errors='replace')[-1500:])
        elapsed = time.perf_counter()-start
        stats = {'source': str(source), 'output': str(target), 'width': w, 'height': h, 'fps': fps,
                 'frames_processed': processed, 'video_seconds': processed/fps,
                 'faces_detected_frames': pipeline.detected, 'missed_frames': processed-pipeline.detected,
                 'coverage': pipeline.coverage_counts, 'settings': options.model_dump(),
                 'review_intervals': [{**{k: v for k, v in item.items() if k != 'last_frame'},
                                       'end_seconds': round(item['end_seconds']+1/fps, 6)} for item in pipeline.review_intervals],
                 'wall_seconds': elapsed, 'processing_fps': processed/elapsed,
                 'normalized_30s_wall_seconds': elapsed*30/(processed/fps), 'stage_seconds': pipeline.timings,
                 'renderer': pipeline.renderer.renderer, 'renderer_mode': 'gpu', 'execution_mode': 'gpu',
                 'gpu_backend': pipeline.backend,
                 'inference_delegate': pipeline.vision.inference_delegate, 'inference_device': pipeline.vision.device,
                 'decoder_hardware_acceleration': 'NVDEC' if pipeline.backend == 'nvidia' else 'QSV',
                 'encoder': 'h264_nvenc' if pipeline.backend == 'nvidia' else 'h264_qsv', 'audio': 'packet copy',
                 'source_video_color': header['color'], 'output_video_color': 'BT.709 limited YUV; full RGB',
                 'pipeline_devices': {stage: 'gpu' for stage in ['decode', 'preprocess', 'inference', 'mask', 'temporal', 'render', 'composite', 'encode']},
                 'cpu_work': ['container header inspection', 'process control', 'frame transport', 'sparse landmark/box coordinates', 'packet muxing'],
                 'fallback_used': False, 'timing_includes': 'model initialization and complete GPU video pipeline; excludes setup/download',
                 'limitations': ['One 3D face mesh; counts do not prove every head is covered.',
                                 'GPU temporal bridge uses checked block matching with translation; CPU uses sparse affine optical flow.',
                                 'Constant-frame-rate input expected; unsupported GPU codecs fail without software fallback.']}
        target.with_suffix('.benchmark.json').write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(stats, ensure_ascii=False), flush=True)
    except (BrokenPipeError, OSError, subprocess.TimeoutExpired) as error:
        details = ''.join(path.read_text(encoding='utf-8', errors='replace')[-1200:]
                          for path in [decode_log, encode_log] if path.is_file())
        raise RuntimeError(f'GPU video I/O failed without CPU fallback: {error}. {details}') from error
    finally:
        for process in [decoder, encoder]:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            if process is not None:
                for stream in [process.stdin, process.stdout]:
                    if stream is not None and not stream.closed:
                        stream.close()
        pipeline.close()
