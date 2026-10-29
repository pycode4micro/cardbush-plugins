"""Bundled vision models compiled exclusively for an Intel GPU, never AUTO/HETERO.

Only boxes and 478 landmark coordinates return to Python. Pixel preprocessing,
model inference, score filtering and detector decoding execute on the GPU.
"""
from __future__ import annotations

import math
import tempfile
import zipfile
from pathlib import Path
import numpy as np
import openvino as ov
from openvino import opset13 as ops
from .model_metadata import detector_metadata, normalization


def column(node, indices, axis=-1):
    return ops.gather(node, np.array(indices, np.int64), np.int64(axis))


def constant(value):
    return ops.constant(np.asarray(value, np.float32))


def gpu_compile(core, model, cache_dir=None):
    config = {'PERFORMANCE_HINT': 'LATENCY', 'INFERENCE_PRECISION_HINT': 'f32'}
    if cache_dir:
        config['CACHE_DIR'] = str(cache_dir)
    compiled = core.compile_model(model, 'GPU', config)
    devices = compiled.get_property('EXECUTION_DEVICES')
    if not devices or any(not str(device).startswith('GPU') for device in devices):
        raise RuntimeError(f'GPU inference refused a non-GPU execution device: {devices}')
    return compiled


def detection_model(model, boxes, scores, max_results=10, keypoints=None):
    nms = ops.non_max_suppression(boxes, ops.unsqueeze(scores, np.array([1], np.int64)),
                                 np.int64(max_results), np.float32(.3), np.float32(0),
                                 box_encoding='corner', sort_result_descending=True, output_type='i32')
    indices = ops.squeeze(column(nms.output(0), [2], 1), np.array([1], np.int64))
    selected = ops.gather(ops.squeeze(boxes, np.array([0], np.int64)), indices, np.int64(0))
    selected_scores = ops.gather(ops.squeeze(scores, np.array([0], np.int64)), indices, np.int64(0))
    outputs = [selected, selected_scores]
    if keypoints is not None:
        outputs.append(ops.gather(ops.squeeze(keypoints, np.array([0], np.int64)), indices, np.int64(0)))
    return ov.Model(outputs, model.get_parameters())


def model_data(model_dir, person_filter=True):
    with zipfile.ZipFile(model_dir / 'face_landmarker.task') as archive:
        files = {'face': archive.read('face_detector.tflite'),
                 'landmarks': archive.read('face_landmarks_detector.tflite'),
                 'segmentation': (model_dir/'selfie_multiclass.tflite').read_bytes()}
    if person_filter:
        files['person'] = (model_dir/'efficientdet_lite0.tflite').read_bytes()
    return files


def prepare_model(core, name, data, path, suppress=True):
    """Identical inference/decoding graphs for Intel and exported NVIDIA models."""
    path.write_bytes(data)
    model = core.read_model(str(path))
    if name == 'landmarks':
        model.reshape([1, 256, 256, 3])
    if name == 'person':
        anchors, scales, exponential, sigmoid = detector_metadata(data)
        scores, raw = [result.input_value(0) for result in model.get_results()]
        # YXHW tensor order; metadata anchors are XYWH.
        center = ops.add(ops.multiply(column(raw, [1, 0]) / constant(scales[:2]), anchors[None, :, 2:]), anchors[None, :, :2])
        size = column(raw, [3, 2]) / constant(scales[2:])
        if exponential:
            size = ops.exp(size)
        size = ops.multiply(size, anchors[None, :, 2:])
        corners = ops.concat([center-size/constant(2), center+size/constant(2)], -1)
        scores = ops.squeeze(column(scores, [0]), np.array([2], np.int64))
        if sigmoid:
            scores = ops.sigmoid(scores)
        model = detection_model(model, corners, scores) if suppress else ov.Model([corners, scores], model.get_parameters())
    elif name == 'face':
        anchors = []
        for grid, count in [(16, 2), (8, 6)]:
            for y in range(grid):
                for x in range(grid):
                    anchors.extend([[(x+.5)/grid, (y+.5)/grid]] * count)
        anchors = np.array(anchors, np.float32)[None]
        raw, scores = [result.input_value(0) for result in model.get_results()]
        center = column(raw, [0, 1]) / constant(128) + constant(anchors)
        size = column(raw, [2, 3]) / constant(128)
        corners = ops.concat([center-size/constant(2), center+size/constant(2)], -1)
        points = ops.reshape(column(raw, list(range(4, 16))), [1, 896, 6, 2], False)
        points = points / constant(128) + constant(anchors[:, :, None, :])
        scores = ops.squeeze(ops.sigmoid(scores), np.array([2], np.int64))
        model = detection_model(model, corners, scores, 8, points) if suppress else ov.Model([corners, scores, points], model.get_parameters())
    elif name == 'segmentation':
        # Bundled multiclass model emits logits; MediaPipe applies SOFTMAX.
        # Keep semantic head/hair/body channels entirely on device.
        output = column(ops.softmax(model.get_results()[0].input_value(0), 3), [1, 3, 2])
        model = ov.Model([output], model.get_parameters())
    return model


class GPUVision:
    def __init__(self, model_dir, options, cache_dir=None):
        self.options = options
        self.core = ov.Core()
        if not any(device.startswith('GPU') for device in self.core.available_devices):
            raise RuntimeError('No supported Intel GPU found by OpenVINO. CPU mode is a separate explicit choice.')
        self.device = self.core.get_property('GPU', 'FULL_DEVICE_NAME')
        self.inference_delegate = 'OpenVINO GPU'
        self.models = {}
        self.normalizations = {}
        self.shapes = {}
        self.previous_bounds = None
        with tempfile.TemporaryDirectory(prefix='video-face-gpu-models-') as directory:
            for name, data in model_data(model_dir, options.person_filter).items():
                model = prepare_model(self.core, name, data, Path(directory)/(name+'.tflite'))
                self.normalizations[name] = normalization(data)
                self.shapes[name] = tuple(model.input().shape[1:3])
                self.models[name] = gpu_compile(self.core, model, cache_dir)

    def infer(self, name, pixels):
        result = self.models[name]([pixels])
        return [result[output] for output in self.models[name].outputs]

    def input(self, frame_ops, name, box=(0, 0, 1, 1), rotation=0, letterbox=False):
        mean, std = self.normalizations[name]
        return frame_ops.tensor(self.shapes[name], box, rotation, mean, std, letterbox)

    def persons(self, frame_ops):
        if 'person' not in self.models:
            return [(0., 0., 1., 1.)]
        boxes, scores = self.infer('person', self.input(frame_ops, 'person', letterbox=True))
        boxes = frame_ops.unletterbox(boxes, (0, 0, 1, 1))
        return [tuple(np.clip(box, 0, 1)) for box, score in zip(boxes, scores) if score >= self.options.person_detection_confidence]

    def segment(self, frame_ops, box=(0, 0, 1, 1)):
        return self.infer('segmentation', self.input(frame_ops, 'segmentation', box))[0][0]

    def landmarks(self, frame_ops, search_box=(0, 0, 1, 1), tracked=None):
        if tracked is not None:
            result = self._landmarks(frame_ops, *tracked)
            if result is not None and self._tracking_valid(result):
                self._remember(result)
                return result
        boxes, scores, points = self.infer('face', self.input(frame_ops, 'face', search_box, letterbox=True))
        boxes = frame_ops.unletterbox(boxes, search_box)
        points = frame_ops.unletterbox(points, search_box, points=True)
        for box, score, keypoints in zip(boxes, scores, points):
            if score < self.options.min_face_detection_confidence:
                continue
            x0, y0, x1, y1 = box
            center = ((x0+x1)/2, (y0+y1)/2)
            side = max((x1-x0)*frame_ops.w, (y1-y0)*frame_ops.h)*1.5
            roi = (center[0]-side/frame_ops.w/2, center[1]-side/frame_ops.h/2,
                   center[0]+side/frame_ops.w/2, center[1]+side/frame_ops.h/2)
            eye = (keypoints[1]-keypoints[0]) * [frame_ops.w, frame_ops.h]
            rotation = math.atan2(float(eye[1]), float(eye[0]))
            result = self._landmarks(frame_ops, roi, rotation)
            if result is not None:
                self._remember(result)
                return result
        return None

    def _remember(self, points):
        self.previous_bounds = np.concatenate([points[:468, :2].min(axis=0), points[:468, :2].max(axis=0)])

    def _tracking_valid(self, points):
        if self.previous_bounds is None:
            return False
        current = np.concatenate([points[:468, :2].min(axis=0), points[:468, :2].max(axis=0)])
        previous = self.previous_bounds
        intersection = np.prod(np.maximum(0, np.minimum(previous[2:], current[2:])-np.maximum(previous[:2], current[:2])))
        union = np.prod(previous[2:]-previous[:2])+np.prod(current[2:]-current[:2])-intersection
        return bool(union > 0 and intersection/union >= self.options.min_tracking_confidence)

    def _landmarks(self, frame_ops, roi, rotation):
        output = self.infer('landmarks', self.input(frame_ops, 'landmarks', roi, rotation))
        presence = float(output[1].ravel()[0])
        # Presence output is a logit; the third output is the tongue score.
        presence = 1 / (1 + math.exp(-max(-80, min(80, presence))))
        if presence < self.options.min_face_presence_confidence:
            return None
        points = output[0].reshape(-1, 3).copy()/np.float32(256)
        x0, y0, x1, y1 = roi
        center = np.array([(x0+x1)/2, (y0+y1)/2], np.float32)
        size = np.array([x1-x0, y1-y0], np.float32)
        local = (points[:, :2]-.5)*size*[frame_ops.w, frame_ops.h]
        cosine, sine = math.cos(rotation), math.sin(rotation)
        local = local @ np.array([[cosine, sine], [-sine, cosine]], np.float32)
        points[:, :2] = center + local/[frame_ops.w, frame_ops.h]
        points[:, 2] *= x1-x0
        return points

    def close(self):
        self.models.clear()
