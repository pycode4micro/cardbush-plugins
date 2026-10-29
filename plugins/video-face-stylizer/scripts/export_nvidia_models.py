"""Reproducible NVIDIA models derived only from the bundled MediaPipe assets.

Build tools: openvino2onnx==1.1.0, onnx==1.17.0, openvino==2026.4.1.
The end-user runtime consumes the exported ONNX files, never this converter.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
import numpy as np
import onnx
import openvino as ov
import openvino2onnx
from video_face_stylizer.engine.gpu_vision import model_data, prepare_model
from video_face_stylizer.engine.model_metadata import normalization


def export(source, destination):
    destination.mkdir(parents=True, exist_ok=True)
    core = ov.Core()
    manifest = {'format': 1, 'source_sha256': {}, 'models': {}}
    with tempfile.TemporaryDirectory(prefix='video-face-export-') as directory:
        for name, data in model_data(source).items():
            model = prepare_model(core, name, data, Path(directory)/(name+'.tflite'), suppress=False)
            xml = Path(directory)/(name+'.xml')
            ov.serialize(model, str(xml))
            # PReLU->LeakyRelu is invalid for per-channel slopes. Preserve PReLU.
            graph = openvino2onnx.convert_graph(str(xml),
                exclude=['prelu_to_leaky'] if name == 'landmarks' else None, print_passes=False)
            exported = graph.model
            # Preserve the declared port order; converters may topologically sort outputs.
            # All bundled models have distinct fixed output shapes. Identity
            # elimination changes names, so match ports by shape and reject
            # ambiguity instead of silently swapping boxes/scores/landmarks.
            ordered = []
            for result in model.outputs:
                matches = [value for value in exported.graph.output
                           if [dim.dim_value for dim in value.type.tensor_type.shape.dim] == list(result.shape)]
                if len(matches) != 1:
                    raise RuntimeError(f'{name}: ambiguous converted output port')
                ordered.append(matches[0])
            del exported.graph.output[:]
            exported.graph.output.extend(ordered)
            onnx.checker.check_model(exported)
            # Compare all fixed inference/decoder outputs, with no NMS ambiguity.
            import onnxruntime as ort
            ort.set_default_logger_severity(3)
            reference_options = ort.SessionOptions()
            # ORT 1.23 CPU layout rewriting mis-infers Resize(axes=[1,2]);
            # keep this independent parity reference in the exported layout.
            reference_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
            session = ort.InferenceSession(exported.SerializeToString(), reference_options, providers=['CPUExecutionProvider'])
            reference = core.compile_model(model, 'CPU', {'INFERENCE_PRECISION_HINT': 'f32'})
            input_name = session.get_inputs()[0].name
            errors = []
            for seed in (0, 1, 2):
                pixels = np.random.default_rng(seed).uniform(-1, 1, tuple(model.input().shape)).astype(np.float32)
                actual = session.run(None, {input_name: pixels})
                expected = list(reference([pixels]).values())
                if len(actual) != len(expected):
                    raise RuntimeError(f'{name}: output count changed in conversion')
                for a, b in zip(actual, expected):
                    if a.shape != b.shape or not np.allclose(a, b, rtol=3e-3, atol=3e-3):
                        raise RuntimeError(f'{name}: model conversion parity failed: {a.shape}/{b.shape}; '
                                           f'max error {np.max(np.abs(a-b)) if a.shape == b.shape else "shape mismatch"}')
                    errors.append(float(np.max(np.abs(a-b))))
            path = destination/(name+'.onnx')
            onnx.save_model(exported, path)
            mean, std = normalization(data)
            manifest['source_sha256'][name] = hashlib.sha256(data).hexdigest()
            manifest['models'][name] = {'file': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'shape': list(model.input().shape), 'mean': mean.tolist(), 'std': std.tolist(),
                'conversion_max_absolute_error': max(errors)}
            print(json.dumps({'model': name, 'max_absolute_error': max(errors)}), flush=True)
    (destination/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    export(Path(__file__).resolve().parents[1]/'src/video_face_stylizer/engine/models', args.output)
