import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

SCRIPT=Path(__file__).resolve().parents[1]/'skills/video-face-stylizer/scripts/head_region_grid.py'
spec=importlib.util.spec_from_file_location('head_region_grid',SCRIPT)
grid=importlib.util.module_from_spec(spec)
spec.loader.exec_module(grid)


@pytest.fixture
def video(tmp_path):
    source=tmp_path/'原片 test.mp4'
    writer=cv2.VideoWriter(str(source),cv2.VideoWriter_fourcc(*'mp4v'),10,(160,120))
    assert writer.isOpened()
    for index in range(10):writer.write(np.full((120,160,3),index*12,np.uint8))
    writer.release()
    return source


def test_cli_extracts_requested_frame_without_changing_source(video,tmp_path):
    digest=hashlib.sha256(video.read_bytes()).hexdigest()
    output=tmp_path/'检查 帧.png'
    result=subprocess.run([sys.executable,str(SCRIPT),'--input',str(video),'--seconds','.4',
        '--output',str(output),'--box','.2','.2','.6','.7'],capture_output=True,encoding='utf-8',timeout=30)
    assert result.returncode==0,result.stderr
    report=json.loads(result.stdout)
    assert report['requested_seconds']==.4 and report['decoded_seconds']==pytest.approx(.4,abs=.05)
    assert report['frame_index']==4 and report['mask_shape']=='ellipse'
    image=cv2.imdecode(np.frombuffer(output.read_bytes(),np.uint8),cv2.IMREAD_COLOR)
    assert image.shape==(120,160,3)
    assert image[69,133].mean()==pytest.approx(48,abs=5)  # Off-grid pixel from the selected frame.
    assert hashlib.sha256(video.read_bytes()).hexdigest()==digest


def test_overlay_matches_the_renderer_ellipse():
    from video_face_stylizer.engine.head_coverage import ellipse_mask
    frame=np.zeros((120,160,3),np.uint8);before=frame.copy();box=(.2,.2,.6,.7)
    diagnostic=grid.render_grid(frame,5,box)
    mask=ellipse_mask(frame.shape,box)
    assert not mask[24,32]  # Bounding rectangle corner is not filled.
    contour=cv2.erode(mask,np.ones((3,3),np.uint8))!=mask
    yellow=np.all(diagnostic==(0,230,255),axis=2)
    assert yellow[contour].mean()>.95
    np.testing.assert_array_equal(frame,before)


@pytest.mark.parametrize('seconds',[-1,float('nan'),float('inf'),1,20])
def test_invalid_or_out_of_range_time_does_not_publish(video,tmp_path,seconds):
    output=tmp_path/'missing.png'
    with pytest.raises(ValueError):grid.extract_grid(video,seconds,output)
    assert not output.exists()


def test_refuses_to_overwrite_existing_diagnostic(video,tmp_path):
    output=tmp_path/'existing.png';output.write_bytes(b'keep')
    with pytest.raises(FileExistsError):grid.extract_grid(video,0,output)
    assert output.read_bytes()==b'keep'


@pytest.mark.parametrize('box',[(.5,.2,.2,.4),(0,0,1,float('nan')),(-.1,.2,.5,.5)])
def test_invalid_region_is_rejected(box):
    with pytest.raises(ValueError):grid.render_grid(np.zeros((100,100,3),np.uint8),box=box)
