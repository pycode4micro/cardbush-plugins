"""NVIDIA inference through DirectML, with CPU provider fallback forbidden.

DirectML uses the NVIDIA D3D12 driver; CUDA/cuDNN toolkits are not required.
The selected adapter must match the hardware OpenGL renderer and NVDEC device.
"""
from __future__ import annotations
import hashlib
import json
import numpy as np
from .gpu_backend import nvidia_adapter
from .gpu_detection import GPUDetectionFilter
from .gpu_vision import GPUVision, model_data


def directml_session(model, device_id):
    import onnxruntime as ort
    if 'DmlExecutionProvider' not in ort.get_available_providers():
        raise RuntimeError('NVIDIA inference requires the bundled ONNX Runtime DirectML provider.')
    options = ort.SessionOptions()
    options.enable_mem_pattern = False
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.add_session_config_entry('session.disable_cpu_ep_fallback', '1')
    session = ort.InferenceSession(model, sess_options=options,
                                  providers=[('DmlExecutionProvider', {'device_id': device_id})])
    session.disable_fallback()
    if 'DmlExecutionProvider' not in session.get_providers():
        raise RuntimeError('NVIDIA inference refused a session without DirectML GPU execution.')
    return session


def verified_models(model_dir, person_filter=True):
    directory = model_dir/'nvidia'
    manifest = json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('format') != 1:
        raise RuntimeError('Unsupported NVIDIA model manifest version.')
    for name, source in model_data(model_dir, person_filter).items():
        info = manifest['models'][name]
        if info['file'] != name+'.onnx':
            raise RuntimeError('Invalid NVIDIA model filename.')
        if manifest['source_sha256'][name] != hashlib.sha256(source).hexdigest():
            raise RuntimeError(f'NVIDIA model source checksum mismatch: {name}')
        data = (directory/info['file']).read_bytes()
        if hashlib.sha256(data).hexdigest() != info['sha256']:
            raise RuntimeError(f'NVIDIA model checksum mismatch: {name}')
        yield name, info, data


class NvidiaVision(GPUVision):
    def __init__(self, model_dir, options, frames):
        self.options = options
        self.adapter = nvidia_adapter(frames.ctx.info['GL_RENDERER'])
        self.device = self.adapter['name']
        self.inference_delegate = 'ONNX Runtime DirectML (NVIDIA, CPU fallback disabled)'
        self.models, self.normalizations, self.shapes = {}, {}, {}
        self.previous_bounds = None
        self.suppression = GPUDetectionFilter(frames.ctx)
        try:
            for name, info, data in verified_models(model_dir, options.person_filter):
                self.models[name] = directml_session(data, self.adapter['id'])
                self.normalizations[name] = (np.array(info['mean'], np.float32), np.array(info['std'], np.float32))
                self.shapes[name] = tuple(info['shape'][1:3])
        except Exception:
            self.close()
            raise

    def infer(self, name, pixels):
        session = self.models[name]
        output = session.run(None, {session.get_inputs()[0].name: pixels})
        if name == 'face':
            return self.suppression.run(output[0], output[1], 8, self.options.min_face_detection_confidence, output[2])
        if name == 'person':
            return self.suppression.run(output[0], output[1], 10, self.options.person_detection_confidence)
        return output

    def close(self):
        self.models.clear()
        self.suppression.close()
