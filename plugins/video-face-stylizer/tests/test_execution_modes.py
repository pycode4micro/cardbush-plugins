import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from video_face_stylizer import server
from video_face_stylizer.worker import validate_execution
from video_face_stylizer.engine.gpu_video import color_format, decoder_command, encoder_command
from video_face_stylizer.engine.model_metadata import detector_metadata, normalization
from video_face_stylizer.engine.plaster_face import FacePipeline, ROOT, run
from video_face_stylizer.engine.gpu_vision import gpu_compile


STAGES = ['decode', 'preprocess', 'inference', 'mask', 'temporal', 'render', 'composite', 'encode']


@pytest.mark.parametrize('mode', ['cpu', 'gpu'])
def test_worker_requires_every_stage_to_match_requested_mode(mode):
    benchmark = {'execution_mode': mode, 'pipeline_devices': {key: mode for key in STAGES}, 'fallback_used': False}
    validate_execution(benchmark, mode)
    for key in STAGES:
        with pytest.raises(RuntimeError, match='whole-pipeline'):
            validate_execution({**benchmark, 'pipeline_devices': {**benchmark['pipeline_devices'], key: 'cpu' if mode == 'gpu' else 'gpu'}}, mode)
    with pytest.raises(RuntimeError):
        validate_execution({**benchmark, 'fallback_used': True}, mode)
    with pytest.raises(RuntimeError):
        validate_execution({}, mode)


def test_old_mixed_pipeline_cannot_be_selected():
    with pytest.raises(ValueError, match='CPU-only'):
        FacePipeline(64, 64, ROOT/'models', 'gpu')


def test_gpu_entry_does_not_run_cpu_engine(monkeypatch):
    from video_face_stylizer.engine import gpu_pipeline
    calls = []
    def fail(args):
        calls.append(args)
        raise RuntimeError('GPU unavailable')
    monkeypatch.setattr(gpu_pipeline, 'run_gpu', fail)
    args = SimpleNamespace(mode='gpu')
    with pytest.raises(RuntimeError, match='GPU unavailable'):
        run(args)
    assert calls == [args]


@pytest.mark.parametrize('device', ['CPU', 'AUTO', 'HETERO:GPU,CPU'])
def test_gpu_inference_rejects_non_gpu_execution(device):
    calls = []
    class Core:
        def compile_model(self, model, target, config):
            calls.append(target)
            return SimpleNamespace(get_property=lambda key: [device])
    with pytest.raises(RuntimeError, match='non-GPU'):
        gpu_compile(Core(), object())
    assert calls == ['GPU']


def test_gpu_codecs_have_no_software_path(tmp_path):
    decode = decoder_command(tmp_path/'source.mp4', 'avc1', 1920, 1080, 30)
    encode = encoder_command(tmp_path/'source.mp4', tmp_path/'out.mp4', 1920, 1080, 30, 1)
    assert decode[decode.index('-c:v')+1] == 'h264_qsv'
    assert decode[decode.index('-hwaccel_output_format')+1] == 'qsv'
    assert decode[decode.index('-vf')+1] == 'vpp_qsv=w=1920:h=1080:format=nv12,hwdownload,format=nv12'
    assert decode[decode.index('-pix_fmt')+1] == 'nv12'
    assert encode[encode.index('-c:v')+1] == 'h264_qsv'
    assert encode[encode.index('-c:a')+1] == 'copy'
    assert encode[encode.index('-vf')+1] == 'hwupload=extra_hw_frames=32'
    assert encode[encode.index('-pix_fmt')+1] == 'nv12'
    assert encode.count('-color_range') == 2
    assert encode[encode.index('-color_range')+1] == 'tv'
    assert 'matrix_coefficients=1' in encode[encode.index('-bsf:v')+1]
    with pytest.raises(ValueError, match='no configured GPU decoder'):
        decoder_command(tmp_path/'source.avi', 'xvid', 1920, 1080, 30)


def test_source_color_metadata_and_untagged_sdr_defaults():
    assert color_format('h264, yuv420p(progressive), 1280x720') == {
        'weights': (.299, .114), 'full_range': False, 'matrix': 'smpte170m'}
    assert color_format('h264, yuv420p(tv, bt709, progressive)')['weights'] == (.2126, .0722)
    assert color_format('mjpeg, yuvj420p(pc, bt470bg)')['full_range'] is True
    for unsupported in ['bt2020nc', 'smpte2084', 'arib-std-b67', 'ycgco']:
        with pytest.raises(ValueError, match='GPU color'):
            color_format('hevc, yuv420p(tv, '+unsupported+')')


def test_decoder_reads_complete_frames_even_with_short_pipe_reads():
    from video_face_stylizer.engine.gpu_video import read_frame
    from io import BytesIO
    class Pipe(BytesIO):
        def read(self, size):
            return super().read(min(size, 3))
    assert read_frame(Pipe(b'123456789'), 9) == b'123456789'
    assert read_frame(Pipe(), 9) == b''
    with pytest.raises(RuntimeError, match='incomplete frame'):
        read_frame(Pipe(b'1234'), 9)


def test_bundled_metadata_not_guessed():
    data = (ROOT/'models/efficientdet_lite0.tflite').read_bytes()
    anchors, scales, exponential, sigmoid = detector_metadata(data)
    assert anchors.shape == (19206, 4)
    assert anchors[0].tolist() == pytest.approx([.0125, .0125, .075, .075])
    assert scales.tolist() == [1, 1, 1, 1] and exponential and not sigmoid
    mean, std = normalization(data)
    assert mean.tolist() == std.tolist() == [127.5]*3


def test_agent_recommendation_requires_complete_gpu_probe(monkeypatch):
    monkeypatch.setattr(server.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0,
        stdout=json.dumps({'checked': True, 'available': False, 'error': 'QSV unavailable'})))
    caps = server.capabilities()
    assert caps['recommended_mode'] == 'cpu' and not caps['gpu']['available']
    assert 'CPU tracking/segmentation' not in caps['modes']['gpu']
    assert 'no silent fallback' in server.create_server().instructions


def test_cpu_capabilities_never_initialize_gpu(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('GPU probe must not run')
    monkeypatch.setattr(server.subprocess, 'run', forbidden)
    caps = server.capabilities(False)
    assert caps['gpu'] == {'checked': False, 'available': None}
    assert caps['recommended_mode'] is None
