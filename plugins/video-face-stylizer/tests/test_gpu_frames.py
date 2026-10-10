"""Opt-in tests on actual GPU drivers: VFS_TEST_GPU=1 python -m pytest."""
import os
import numpy as np
import pytest
from video_face_stylizer.engine.gpu_frames import GPUFrameOps
from video_face_stylizer.models import ProcessingOptions

pytestmark = pytest.mark.skipif(os.environ.get('VFS_TEST_GPU') != '1', reason='Explicit hardware test opt-in required.')


@pytest.fixture
def frames():
    value = GPUFrameOps(128, 128)
    try:
        yield value
    finally:
        value.close()


@pytest.mark.parametrize('weights', [(.299, .114), (.2126, .0722)])
@pytest.mark.parametrize('full_range', [False, True])
def test_gpu_nv12_primary_colors_and_range_are_preserved(frames, weights, full_range):
    colors = np.array([[0, 0, 0], [255, 255, 255], [128, 128, 128], [255, 0, 0],
                       [0, 255, 0], [0, 0, 255], [255, 255, 0], [0, 255, 255]], dtype=float)
    kr, kb = weights
    y = colors @ np.array([kr, 1-kr-kb, kb])/255
    cb, cr = (colors[:, 2]/255-y)/(2*(1-kb)), (colors[:, 0]/255-y)/(2*(1-kr))
    luma = np.round((0 if full_range else 16)+(255 if full_range else 219)*y).astype('u1')
    chroma = np.round(128+(255 if full_range else 224)*np.stack([cb, cr], axis=1)).clip(0, 255).astype('u1')
    raw = np.repeat(luma, 16)[None, :].repeat(128, axis=0).tobytes()
    raw += np.repeat(chroma, 8, axis=0)[None, :, :].repeat(64, axis=0).tobytes()
    frames.upload_nv12(raw, weights, full_range)
    frames.empty_material()
    image = np.frombuffer(frames.composite(), np.uint8).reshape(128, 128, 4)
    expected = colors[:, ::-1]
    assert np.abs(image[64, 8::16, :3].astype(float)-expected).max() <= 2
    # Output conversion uses one documented BT.709 limited-range format,
    # independent of the input matrix/range and QSV driver defaults.
    encoded = frames.composite(nv12=True)
    frames.upload_nv12(encoded, (.2126, .0722), False)
    frames.empty_material()
    restored = np.frombuffer(frames.composite(), np.uint8).reshape(128, 128, 4)
    assert np.abs(restored[64, 8::16, :3].astype(float)-expected).max() <= 3


def test_qsv_roundtrip_preserves_colors_and_signaling(tmp_path, frames):
    from video_face_stylizer.engine.gpu_video import probe_codecs
    result = probe_codecs(tmp_path, frames)
    assert result['encoder'] == 'h264_qsv'


def test_gpu_component_hair_and_person_filters(frames):
    frames.upload(bytes([30, 40, 50, 255])*128*128)
    pixels = np.zeros((256, 256, 3), np.float32)
    pixels[15:65, 90:150, 0] = .9  # hair-supported head
    pixels[65:90, 90:150, 1] = .9
    pixels[170:190, 90:150, 1] = .9  # disconnected body/hand skin
    frames.add_segmentation(pixels)
    options = ProcessingOptions(mask_padding=0)
    proposals, persons = frames.components(options, [(0., 0., 1., 1.)])
    assert len(proposals) == 1
    assert proposals[0].tolist() == pytest.approx([90/256, 15/256, 150/256, 90/256])
    automatic, manual, covered = frames.paint(options, persons, None, [])
    assert automatic and covered and not manual
    output = np.frombuffer(frames.composite(), np.uint8).reshape(128, 128, 4)
    assert output[25, 60, :3].min() > 140
    assert output[90, 60, :3].max() < 100
    assert output[120, 120, :3].tolist() == [30, 40, 50]


def test_manual_regions_do_not_leak_into_temporal_history(frames):
    options = ProcessingOptions()
    source = bytes([30, 40, 50, 255])*128*128
    for index in range(3):
        frames.upload(source)
        _, persons = frames.components(options, [])
        frames.paint(options, persons, None, [])
        assert frames.temporal(index*100, .2, False)[0] is False
        if index == 1:
            assert frames.manual([(.2, .2, .8, .8)])
        output = np.frombuffer(frames.composite(), np.uint8).reshape(128, 128, 4)
        assert (output[64, 64, :3].min() > 140) == (index == 1)


def test_crop_probability_merge_does_not_add_unrelated_low_confidences(frames):
    frames.upload(bytes([30, 40, 50, 255])*128*128)
    hair = np.zeros((256, 256, 3), np.float32)
    skin = hair.copy()
    hair[20:60, 90:150, 0] = .3
    skin[20:60, 90:150, 1] = .3
    frames.add_segmentation(hair)
    frames.add_segmentation(skin)
    proposals, _ = frames.components(ProcessingOptions(person_filter=False, segmentation_requires_hair=False), [(0., 0., 1., 1.)])
    assert len(proposals) == 0


def test_gpu_motion_bridge_expires_and_rejects_scene_cut(frames):
    options = ProcessingOptions(mask_padding=0)
    image = np.full((128, 128, 4), 60, np.uint8)
    image[:, :, 3] = 255
    noise = np.random.default_rng(42).integers(20, 240, (32, 32), dtype=np.uint8)
    image[12:44, 44:76, :3] = noise[:, :, None]
    frames.upload(image.tobytes())
    segmentation = np.zeros((256, 256, 3), np.float32)
    segmentation[24:88, 88:152, 0] = .95
    frames.add_segmentation(segmentation)
    _, persons = frames.components(options, [(0., 0., 1., 1.)])
    assert frames.paint(options, persons, None, [])[0]
    assert frames.temporal(0, .2, True, [(0., 0., 1., 1.)]) == (False, False)
    moved = np.roll(image, 4, axis=1)
    frames.upload(moved.tobytes())
    _, persons = frames.components(options, [(0., 0., 1., 1.)])
    assert not frames.paint(options, persons, None, [])[0]
    assert frames.temporal(100, .2, False, [(0., 0., 1., 1.)]) == (True, False)
    frames.upload(moved.tobytes())
    _, persons = frames.components(options, [(0., 0., 1., 1.)])
    frames.paint(options, persons, None, [])
    assert frames.temporal(300, .2, False, [(0., 0., 1., 1.)])[0] is False
    frames.upload(bytes([250, 250, 250, 255])*128*128)
    _, persons = frames.components(options, [])
    frames.paint(options, persons, None, [])
    assert frames.temporal(400, .2, False, []) == (False, True)
