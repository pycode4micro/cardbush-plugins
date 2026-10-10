import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from volcengine_plugins.media_review import MediaRequirements, check_requirements
from volcengine_plugins.video_client import SeedanceClient, prepare_video
from volcengine_plugins.video_models import VideoRequest, VideoLocalOptions
from volcengine_plugins.task_io import TaskError


def request(prompt):
    return VideoRequest(model='2.0-fast', content=[{'type':'text','text':prompt},
        {'type':'video_url','video_url':{'url':'https://example.invalid/ref.mp4'},'role':'reference_video'}])


def test_nonexistent_reference_rejected_before_paid_post():
    calls=[]
    client=SeedanceClient(api_key='fake',transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(500)))
    with pytest.raises(TaskError,match='provides 1 video') as error:
        asyncio.run(client.create(request('参考视频2中的动作'),VideoLocalOptions()))
    assert error.value.details['paid_request_sent'] is False
    assert calls==[]


@pytest.mark.parametrize('prompt',['跟随视频1的动作','Follow Video 1 movement','生成视频15秒，参考视频1','a video 2 seconds long',
    '生成视频15s，参考视频1','a video 15.1 seconds long','视频15.1秒','video 15s','视频15000ms'])
def test_valid_reference_and_duration_mentions_remain_verbatim(prompt):
    body,_,_=prepare_video(request(prompt),VideoLocalOptions())
    assert body['content'][0]['text']==prompt


@pytest.mark.parametrize('duration,passed',[(14.9,True),(15.0,True),(15.01,False),(15.1,False)])
def test_hard_duration_limit_is_not_rounded_down(duration,passed):
    result=check_requirements({'duration_seconds':duration,'width':720,'height':1280,'audio_codec':'aac','metadata_precision_seconds':0.01},
        MediaRequirements(max_duration_seconds=15,width=720,height=1280,require_audio=True))
    assert result['passed'] is passed
    assert result['semantic_quality_checked'] is False


@pytest.mark.parametrize('value',[0,-1,float('inf'),float('nan')])
def test_invalid_limits_rejected(value):
    with pytest.raises(ValidationError):MediaRequirements(max_duration_seconds=value)


def test_download_preserves_file_and_reports_failed_validation(tmp_path,monkeypatch):
    from volcengine_plugins import media_review
    monkeypatch.setattr(media_review,'media_info',lambda _: {'duration_seconds':15.1,'width':720,'height':1280,'audio_codec':'aac'})
    def handler(req):
        assert req.method=='GET'
        if req.url.host=='cdn.example':return httpx.Response(200,content=b'original-video')
        return httpx.Response(200,json={'id':'cgt-test','status':'succeeded','content':{'video_url':'https://cdn.example/video.mp4'}})
    client=SeedanceClient(api_key='fake',transport=httpx.MockTransport(handler))
    dest=tmp_path/'video.mp4'
    result=asyncio.run(client.download_task('cgt-test',str(dest),requirements={'max_duration_seconds':15}))
    assert dest.read_bytes()==b'original-video'
    assert result['validation']['status']=='failed' and result['validation']['passed'] is False
    assert result['validation']['checks'][0]['actual']==15.1
    assert result['paid_request_sent'] is False


def test_unreadable_download_is_reported_unverified_without_losing_receipt(tmp_path,monkeypatch):
    from volcengine_plugins import media_review
    def fail(_):raise ValueError('Cannot decode a video frame')
    monkeypatch.setattr(media_review,'media_info',fail)
    def handler(req):
        if req.url.host=='cdn.example':return httpx.Response(200,content=b'original-video')
        return httpx.Response(200,json={'id':'cgt-test','status':'succeeded','content':{'video_url':'https://cdn.example/video.mp4'}})
    client=SeedanceClient(api_key='fake',transport=httpx.MockTransport(handler))
    result=asyncio.run(client.download_task('cgt-test',str(tmp_path/'v.mp4'),requirements={'max_duration_seconds':15}))
    assert result['validation']['status']=='unverified' and len(result['sha256'])==64
    assert result['bytes']==14
