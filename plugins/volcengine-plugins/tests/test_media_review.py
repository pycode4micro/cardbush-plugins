import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageFilter

from volcengine_plugins import media_review as m
from volcengine_plugins.subtitle import EraseLocation


def region(x1=0.1,y1=0.7,x2=0.9,y2=0.8):
    return EraseLocation(top_left_x=x1,top_left_y=y1,bottom_right_x=x2,bottom_right_y=y2)


@pytest.fixture
def source(tmp_path):
    path = tmp_path/'source.mp4'
    m.run([m.ffmpeg_bin(),'-nostdin','-v','error','-f','lavfi','-i','testsrc2=size=160x240:rate=10:duration=1',
           '-f','lavfi','-i','sine=frequency=440:sample_rate=44100:duration=1','-c:v','libx264','-crf','12',
           '-c:a','aac','-shortest',str(path)])
    return str(path)


def test_metadata_is_measured_and_environment_identified(source):
    result = m.preflight([source])
    assert result['files'][0]['width'] == 160
    assert result['files'][0]['height'] == 240
    assert result['files'][0]['bytes'] == Path(source).stat().st_size
    assert result['files'][0]['audio_codec'] == 'aac'
    assert result['environment']['python']
    assert {'numpy','Pillow','imageio-ffmpeg'} <= set(result['environment']['packages'])


def test_pixel_plan_grouped_and_padding_bounded(source):
    regions = [m.PixelRegion(start_time=0.1,end_time=0.5,left=2,top=180,right=70,bottom=235),
               m.PixelRegion(start_time=0.1,end_time=0.5,left=80,top=180,right=159,bottom=235)]
    result = m.erasure_plan(source,regions,4)
    segments = result['request_fields']['time_segment_filter']['segments']
    assert len(segments) == 1 and len(segments[0]['erase_ratio_location']) == 2
    assert segments[0]['erase_ratio_location'][0]['top_left_x'] == 0
    assert segments[0]['erase_ratio_location'][1]['bottom_right_x'] == 1
    assert result['selected_seconds'] == pytest.approx(0.4)
    assert result['paid_request_sent'] is False


@pytest.mark.parametrize('fields',[
    {'end_time':1.1}, {'right':161}, {'top':30}, {'start_time':0.6},
])
def test_plan_rejects_invalid_or_inaccessible_scope(source,fields):
    data = {'start_time':0.1,'end_time':0.5,'left':10,'top':180,'right':150,'bottom':235,**fields}
    with pytest.raises(ValueError):
        m.erasure_plan(source,[m.PixelRegion(**data)])


def test_overlapping_time_ranges_not_double_billed(source):
    with pytest.raises(ValueError,match='overlap'):
        m.erasure_plan(source,[m.PixelRegion(start_time=a,end_time=b,left=10,top=180,right=150,bottom=235)
                               for a,b in [(0.1,0.6),(0.5,0.9)]])


def test_blur_flags_texture_but_identical_frames_do_not():
    rng = np.random.default_rng(2026)
    image = Image.fromarray(rng.integers(0,256,(96,96,3),dtype=np.uint8))
    sample = m.ReviewSample(time=0,protected_regions=[region(0,0,1,1)])
    same = m.comparison_metrics(image,image,sample)
    assert same['protected_regions'][0]['possible_softening'] is False
    blurred = m.comparison_metrics(image,image.filter(ImageFilter.GaussianBlur(3)),sample)
    assert blurred['protected_regions'][0]['possible_softening'] is True


def test_caption_removal_is_excluded_from_protected_metrics():
    original = np.full((96,96,3),40,dtype=np.uint8)
    original[68:76,12:84] = 255
    cleaned = np.full_like(original,40)
    sample = m.ReviewSample(time=0,erase_regions=[region()],protected_regions=[region(0,0,1,1)])
    result = m.comparison_metrics(Image.fromarray(original),Image.fromarray(cleaned),sample)
    assert result['whole_frame_excluding_subtitles']['mean_absolute_delta_0_255'] == 0


def test_review_has_evidence_and_no_automatic_pass(source,tmp_path):
    result = m.review(source,source,str(tmp_path/'qc'),[m.ReviewSample(time=0.2,erase_regions=[region()])])
    assert result['checks']['audio']['encoded_streams_match'] is True
    assert result['automatic_quality_pass'] is False
    assert Path(result['samples'][0]['evidence_path']).is_file()
    assert json.loads(Path(result['report_path']).read_text())['status'] == 'needs_visual_review'
    with pytest.raises(ValueError,match='NEW'):
        m.review(source,source,str(tmp_path/'qc'))


def test_review_rejects_time_outside_media(source,tmp_path):
    with pytest.raises(ValueError,match='timestamps'):
        m.review(source,source,str(tmp_path/'qc'),[m.ReviewSample(time=1.2)])
    assert not (tmp_path/'qc').exists()


def test_publish_preserves_audio_geometry_and_master(source,tmp_path):
    original = Path(source).read_bytes()
    target = tmp_path/'publish.mp4'
    result = m.publish_copy(source,str(target),20,'medium')
    assert result['audio_streams_match'] is True
    assert (result['result']['width'],result['result']['height'],result['result']['fps']) == (160,240,10.0)
    assert Path(source).read_bytes() == original
    published = target.read_bytes()
    with pytest.raises(ValueError,match='NEW'):
        m.publish_copy(source,str(target))
    assert target.read_bytes() == published


def test_local_inputs_cannot_be_urls(tmp_path):
    for value in ['https://example.com/video.mp4','relative.mp4',str(tmp_path/'absent.mp4')]:
        with pytest.raises(ValueError):
            m.local_file(value)
