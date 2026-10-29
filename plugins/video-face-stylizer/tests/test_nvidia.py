import os
import hashlib
import json
from types import SimpleNamespace
import sys
import numpy as np
import pytest
from video_face_stylizer.engine.gpu_backend import renderer_backend, match_adapter
from video_face_stylizer.engine.gpu_video import decoder_command, encoder_command
from video_face_stylizer.engine.nvidia_vision import directml_session


@pytest.mark.parametrize('model', ['GeForce GTX 1080', 'GeForce RTX 3070 Ti', 'GeForce RTX 4050 Laptop GPU', 'RTX A4000', 'GeForce RTX 5090'])
def test_nvidia_selection_has_no_product_whitelist(model):
    assert renderer_backend('NVIDIA Corporation', 'NVIDIA '+model+'/PCIe/SSE2') == 'nvidia'
    selected = match_adapter('NVIDIA '+model+'/PCIe/SSE2', [
        {'id': 0, 'name': 'Intel UHD Graphics', 'vendor_id': 0x8086},
        {'id': 2, 'name': 'NVIDIA '+model, 'vendor_id': 0x10DE}])
    assert selected['id'] == 2


@pytest.mark.parametrize('vendor,renderer', [('Microsoft', 'GDI Generic'), ('Mesa', 'llvmpipe NVIDIA'),
    ('Intel', 'software renderer'), ('AMD', 'Radeon'), ('', 'NVIDIA')])
def test_gpu_selection_rejects_software_or_unknown_vendor(vendor, renderer):
    with pytest.raises(RuntimeError):
        renderer_backend(vendor, renderer)


def test_gpu_selection_keeps_intel_and_rejects_ambiguous_nvidia():
    assert renderer_backend('Intel', 'Intel UHD Graphics 730') == 'intel'
    adapter = {'id': 1, 'name': 'NVIDIA GPU', 'vendor_id': 0x10DE}
    for adapters in [[], [adapter, {**adapter, 'id': 2}], [{**adapter, 'software': True}]]:
        with pytest.raises(RuntimeError, match='uniquely'):
            match_adapter('NVIDIA GPU/PCIe', adapters)


def test_nvidia_video_uses_selected_hardware_without_software_filters(tmp_path):
    decode = decoder_command(tmp_path/'source.mp4', 'avc1', 640, 360, 5, .2, 'nvidia', 2)
    encode = encoder_command(tmp_path/'source.mp4', tmp_path/'out.mp4', 640, 360, 10, .5, .2, 'nvidia', 2)
    assert decode[decode.index('-c:v')+1] == 'h264_cuvid'
    assert decode[decode.index('-hwaccel_output_format')+1] == 'cuda'
    assert decode[decode.index('-resize')+1] == '640x360'
    assert decode[decode.index('-vf')+1] == 'hwdownload,format=nv12'
    assert decode[decode.index('-gpu')+1] == '2'
    assert encode[encode.index('-c:v')+1] == 'h264_nvenc'
    assert encode[encode.index('-init_hw_device')+1] == 'cuda=hw:2'
    assert encode[encode.index('-vf')+1] == 'hwupload'
    assert encode[encode.index('-c:a')+1] == 'copy'
    for backend, codec in [('nvidia', 'xvid'), ('unknown', 'avc1')]:
        with pytest.raises(ValueError):
            decoder_command(tmp_path/'source.mp4', codec, 640, 360, 5, backend=backend)


def test_directml_disables_both_graph_and_runtime_cpu_fallback(monkeypatch):
    record = {}
    class Options:
        def add_session_config_entry(self, name, value):
            record[name] = value
    class Session:
        def __init__(self, model, sess_options, providers):
            record.update(options=sess_options, providers=providers)
        def disable_fallback(self):
            record['runtime_fallback_disabled'] = True
        def get_providers(self):
            return ['DmlExecutionProvider']
    monkeypatch.setitem(sys.modules, 'onnxruntime', SimpleNamespace(
        SessionOptions=Options, InferenceSession=Session,
        ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL='sequential'),
        get_available_providers=lambda: ['DmlExecutionProvider', 'CPUExecutionProvider']))
    directml_session(b'model', 3)
    assert record['session.disable_cpu_ep_fallback'] == '1'
    assert record['runtime_fallback_disabled']
    assert record['providers'] == [('DmlExecutionProvider', {'device_id': 3})]
    assert record['options'].enable_mem_pattern is False
    assert record['options'].execution_mode == 'sequential'
    monkeypatch.setattr(sys.modules['onnxruntime'], 'get_available_providers', lambda: ['CPUExecutionProvider'])
    with pytest.raises(RuntimeError, match='DirectML'):
        directml_session(b'model', 3)


@pytest.mark.parametrize('corruption', ['file', 'source', 'path', 'format'])
def test_model_integrity_rejects_corruption_before_gpu_initialization(tmp_path, monkeypatch, corruption):
    from video_face_stylizer.engine import nvidia_vision
    directory = tmp_path/'nvidia'
    directory.mkdir()
    source, data = b'source-weights', b'converted-weights'
    manifest = {'format': 1, 'source_sha256': {'face': hashlib.sha256(source).hexdigest()},
                'models': {'face': {'file': 'face.onnx', 'sha256': hashlib.sha256(data).hexdigest()}}}
    (directory/'face.onnx').write_bytes(data)
    monkeypatch.setattr(nvidia_vision, 'model_data', lambda *args: {'face': source})
    path = directory/'manifest.json'
    path.write_text(json.dumps(manifest))
    assert list(nvidia_vision.verified_models(tmp_path))[0][2] == data
    if corruption == 'file':
        (directory/'face.onnx').write_bytes(b'tampered')
    elif corruption == 'source':
        manifest['source_sha256']['face'] = '0'*64
    elif corruption == 'path':
        manifest['models']['face']['file'] = '../external.onnx'
    else:
        manifest['format'] = 99
    path.write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError):
        list(nvidia_vision.verified_models(tmp_path))


@pytest.mark.skipif(os.environ.get('VFS_TEST_GPU') != '1', reason='Explicit hardware test opt-in required.')
def test_gpu_nms_sort_threshold_ties_invalid_values_and_sparse_points():
    from video_face_stylizer.engine.gpu_frames import GPUFrameOps
    from video_face_stylizer.engine.gpu_detection import GPUDetectionFilter
    frames = GPUFrameOps(128, 128)
    detector = None
    try:
        detector = GPUDetectionFilter(frames.ctx)
        boxes = np.array([[[0,0,1,1], [.1,.1,1,1], [2,2,3,3], [4,4,5,5],
                           [6,6,7,7], [np.nan,0,1,1], [8,8,9,9]]], np.float32)
        scores = np.array([[.8,.9,.8,.1,np.nan,.99,np.inf]], np.float32)
        points = np.arange(84, dtype=np.float32).reshape(1,7,6,2)
        result = detector.run(boxes, scores, 10, .2, points)
        np.testing.assert_array_equal(result[0], boxes[0,[1,2]])
        np.testing.assert_array_equal(result[1], scores[0,[1,2]])
        np.testing.assert_array_equal(result[2], points[0,[1,2]])
        assert detector.run(boxes, scores, 10, 1)[0].shape == (0,4)
        assert detector.run(boxes[:,:0], scores[:,:0], 10, .2)[0].shape == (0,4)
        # Full EfficientDet candidate count exercises all 256 shader lanes.
        many = np.zeros((1,19206,4), np.float32)
        confidence = np.zeros((1,19206), np.float32)
        many[0,-1], confidence[0,-1] = [10,10,11,11], .95
        np.testing.assert_array_equal(detector.run(many, confidence, 10, .2)[0], many[0,-1:])
    finally:
        if detector is not None:
            detector.close()
        frames.close()
