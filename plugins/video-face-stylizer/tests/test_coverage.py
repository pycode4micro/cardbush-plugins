from types import SimpleNamespace
import cv2
import numpy as np
import pytest
from pydantic import ValidationError
from video_face_stylizer.models import HeadRegion,ProcessingOptions
from video_face_stylizer.engine.head_coverage import (
    TemporalCoverage,clean_head_mask,ellipse_mask,hair_supported_probability,head_proposals,manual_mask,map_landmarks,person_head_probability,plaster_fill,tile_boxes,
)
from video_face_stylizer.engine.plaster_face import FacePipeline


def region():
    return HeadRegion(start_seconds=8,end_seconds=10,keyframes=[
        {'seconds':8,'box':[.1,.2,.3,.5]}, {'seconds':9,'box':[.5,.2,.7,.5]}])


@pytest.mark.parametrize('value',[
    {'min_face_detection_confidence':0}, {'min_face_presence_confidence':1.1},
    {'min_tracking_confidence':float('nan')},{'detection_max_side':32},
    {'segmentation_confidence':0},{'mask_padding':2},{'temporal_hold_seconds':-1},
    {'coverage':'blur'}, {'coverage':'face','head_regions':[region()]}, {'detection_tile_grid':5},
])
def test_options_reject_invalid_parameters(value):
    with pytest.raises(ValidationError):ProcessingOptions(**value)


def test_manual_region_source_timeline_and_interpolation():
    item=region();shape=(100,200,3)
    assert not manual_mask(shape,[item],7.99).any()
    assert not manual_mask(shape,[item],10).any()
    mask=manual_mask(shape,[item],8.5)
    assert mask[35,80]==255 and mask[35,10]==0
    assert manual_mask(shape,[item],9.9)[35,120]==255


@pytest.mark.parametrize('keys',[
    [{'seconds':9,'box':[.1,.2,.3,.5]},{'seconds':8,'box':[.1,.2,.3,.5]}],
    [{'seconds':10,'box':[.1,.2,.3,.5]}],
    [{'seconds':8,'box':[.4,.2,.3,.5]}],
])
def test_manual_region_does_not_accept_bad_timeline(keys):
    with pytest.raises(ValidationError):HeadRegion(start_seconds=8,end_seconds=10,keyframes=keys)


def test_tile_landmarks_return_to_full_frame_including_depth():
    points=map_landmarks([[.5,.25,-.1]],(400,100,800,500),1000,800)
    np.testing.assert_allclose(points,[[.6,.25,-.04]],rtol=1e-6)
    coverage=np.zeros((80,100),np.uint8)
    for x0,y0,x1,y1 in tile_boxes(100,80):coverage[y0:y1,x0:x1]=1
    assert coverage.all()


def test_head_fill_is_opaque_not_source_blur():
    mask=ellipse_mask((80,100,3),(.2,.1,.7,.8))
    first=np.zeros((80,100,3),np.uint8)
    second=np.full_like(first,255)
    a=plaster_fill(first,mask);b=plaster_fill(second,mask)
    np.testing.assert_array_equal(a[mask>0],b[mask>0])
    assert a[35,45].min()>120
    assert not a[0,0].any() and b[0,0].min()==255


def test_mask_fills_holes_and_ignores_tiny_noise():
    probability=np.zeros((100,100),np.float32)
    probability[20:50,20:50]=1;probability[30:40,30:40]=0;probability[80,80]=1
    mask=clean_head_mask(probability,.4,.05)
    assert mask[35,35]==255 and mask[80,80]==0


def test_temporal_bridge_expires_and_cuts_reset():
    rng=np.random.default_rng(8)
    frame=rng.integers(20,210,(96,128,3),dtype=np.uint8)
    mask=ellipse_mask(frame.shape,(.2,.2,.8,.8));empty=np.zeros(mask.shape,np.uint8)
    tracker=TemporalCoverage(.2)
    tracker.apply(frame,mask,0)
    shifted=cv2.warpAffine(frame,np.float32([[1,0,2],[0,1,1]]),(128,96))
    result,bridged,_=tracker.apply(shifted,empty,50)
    assert bridged and result.any()
    result,bridged,_=tracker.apply(shifted,empty,250)
    assert not bridged and not result.any()
    tracker.apply(frame,mask,300)
    result,bridged,cut=tracker.apply(np.zeros_like(frame),empty,350)
    assert cut and not bridged and not result.any()


def test_head_segmentation_excludes_body_skin():
    masks=[np.zeros((16,16),np.float32) for _ in range(6)]
    masks[2][:,:]=1;masks[1][2:6,2:6]=.8;masks[3][6:10,2:6]=.9
    class Segmenter:
        def segment(self,image):
            return SimpleNamespace(confidence_masks=[SimpleNamespace(numpy_view=lambda a=a:a) for a in masks])
    pipeline=object.__new__(FacePipeline);pipeline.segmenter=Segmenter();pipeline.options=ProcessingOptions()
    result=pipeline._head_probability(np.zeros((16,16,3),np.uint8))
    assert result[4,4]==.8 and result[8,4]==.9 and result[12,12]==0


def test_crops_are_searched_after_full_frame_miss():
    empty=SimpleNamespace(face_landmarks=[])
    class FullTracker:
        def detect_for_video(self,*args):return empty
    class CropTracker:
        calls=0
        def detect(self,image):
            self.calls+=1
            if self.calls!=2:return empty
            return SimpleNamespace(face_landmarks=[[SimpleNamespace(x=.4,y=.4,z=0),SimpleNamespace(x=.6,y=.6,z=0)]])
    pipeline=object.__new__(FacePipeline)
    pipeline.options=ProcessingOptions();pipeline.w=100;pipeline.h=80
    pipeline.tracker=FullTracker();pipeline.crop_tracker=CropTracker();pipeline.last_box=None
    pipeline.coverage_counts={'full_frame_faces':0,'crop_faces':0}
    landmarks=pipeline._detect(np.zeros((80,100,3),np.uint8),0)
    assert landmarks is not None and pipeline.crop_tracker.calls==9
    assert pipeline.coverage_counts['crop_faces']==1


def test_segmentation_proposals_stay_inside_frame():
    probability=np.zeros((80,100),np.float32);probability[:20,:15]=1
    boxes=head_proposals(probability,.4)
    assert len(boxes)==1
    x0,y0,x1,y1=boxes[0]
    assert x0==y0==0 and 15<=x1<=100 and 20<=y1<=80


def test_no_face_still_runs_opaque_head_coverage():
    pipeline=object.__new__(FacePipeline)
    pipeline.w=100;pipeline.h=80;pipeline.options=ProcessingOptions(tiled_detection=False,tiled_segmentation=False)
    pipeline.last_box=None;pipeline.detected=0;pipeline.frames_seen=0;pipeline.person_detector=None
    pipeline.temporal=TemporalCoverage(.2);pipeline.review_intervals=[]
    pipeline.timings={'segmentation':0.,'tracking':0.,'render_composite':0.}
    pipeline.coverage_counts={'covered_frames':0,'uncovered_frames':0,'segmentation_fallback_frames':0}
    probability=np.zeros((80,100),np.float32);probability[10:45,30:65]=1
    pipeline._head_probability=lambda rgb:probability
    pipeline._detect=lambda *args:None
    frame=np.full((80,100,3),20,np.uint8)
    result,meta=pipeline.process(frame,0,8)
    assert meta['landmarks'] is None and meta['covered']
    assert result[25,50].min()>140 and result[70,90].max()==20
    assert pipeline.coverage_counts['segmentation_fallback_frames']==1
    assert pipeline.review_intervals[0]['reason']=='segmentation_only'


@pytest.mark.parametrize('channel_axis',[False,True])
def test_disconnected_hand_skin_is_not_a_head_region(channel_axis):
    hair=np.zeros((60,80),np.float32);skin=np.zeros_like(hair)
    hair[5:15,10:30]=.9;skin[15:30,10:30]=.9
    skin[40:55,60:75]=.99
    probability=hair_supported_probability(hair[...,None] if channel_axis else hair,skin[...,None] if channel_axis else skin,.4)
    assert probability[10,20]>.8 and probability[20,20]>.8
    assert probability[45,65]==0


def test_person_filter_rejects_background_and_clips_border_boxes():
    class Detector:
        def detect(self,image):return SimpleNamespace(detections=[SimpleNamespace(
            bounding_box=SimpleNamespace(origin_x=-5,origin_y=5,width=30,height=60))])
    pipeline=object.__new__(FacePipeline);pipeline.person_detector=Detector();pipeline.w=100;pipeline.h=80
    allowed=pipeline._person_mask(np.zeros((80,100,3),np.uint8))
    assert allowed[15,0]==255 and allowed[15,90]==0 and allowed[79,0]==0


def test_person_head_filter_rejects_torso_patches_but_can_allow_unusual_poses():
    probability=np.zeros((100,100),np.float32)
    probability[5:25,20:40]=1;probability[65:90,60:80]=1;probability[30:32,50:52]=1
    filtered=person_head_probability(probability,[(10,0,90,100)],.4,.35)
    assert filtered[10,30]==1 and filtered[70,70]==0 and filtered[30,50]==0
    assert person_head_probability(probability,[(10,0,90,100)],.4,1)[70,70]==1
