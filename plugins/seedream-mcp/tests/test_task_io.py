"""No real keys or paid calls: mock the API and CDN including exact signatures."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest

from seedream_mcp.client import SeedreamError
from seedream_mcp.video_client import SeedanceClient
from seedream_mcp.video_models import VideoRequest, VideoLocalOptions
from seedream_mcp.task_io import TaskError


def task(task_id='cgt-one', **kwargs):
    return {'id': task_id, 'status': 'succeeded', 'content': {'video_url': 'https://cdn.example/v.mp4?X=A%2Fb%2Fc&plus=a+b&empty=&dup=1&dup=2'}, **kwargs}


def test_exact_signed_url_download_without_ark_header(tmp_path):
    calls=[]
    def handler(request):
        calls.append(request)
        if request.url.host != 'cdn.example':
            assert request.headers['authorization'] == 'Bearer fake'
            return httpx.Response(200,json=task())
        assert 'authorization' not in request.headers
        assert request.url.raw_path == b'/v.mp4?X=A%2Fb%2Fc&plus=a+b&empty=&dup=1&dup=2'
        return httpx.Response(200,content=b'unchanged-media-bytes',headers={'content-type':'video/mp4'})
    client=SeedanceClient(api_key='fake',transport=httpx.MockTransport(handler))
    dest=tmp_path/'folder'/'video.mp4'
    result=asyncio.run(client.download_task('cgt-one',str(dest)))
    assert dest.read_bytes()==b'unchanged-media-bytes'
    assert result['bytes']==21 and len(result['sha256'])==64
    assert 'cdn.example' not in json.dumps(result) and result['paid_request_sent'] is False
    assert [r.method for r in calls]==['GET','GET']
    with pytest.raises(ValueError,match='new absolute'):
        asyncio.run(client.download_task('cgt-one',str(dest)))
    assert len(calls)==2


def test_only_download_get_is_retried(tmp_path,monkeypatch):
    async def no_sleep(_): pass
    monkeypatch.setattr('seedream_mcp.task_io.asyncio.sleep',no_sleep)
    cdn=[]
    def handler(request):
        assert request.method=='GET'
        if request.url.host!='cdn.example':
            return httpx.Response(200,json=task())
        cdn.append(request)
        return httpx.Response(503) if len(cdn)==1 else httpx.Response(200,content=b'video')
    result=asyncio.run(SeedanceClient(api_key='fake',transport=httpx.MockTransport(handler)).download_task('cgt-one',str(tmp_path/'v.mp4')))
    assert result['download_attempts']==2


@pytest.mark.parametrize('status',[401,403,404,410])
def test_expired_links_no_regeneration_or_partial_file(status,tmp_path):
    cdn=[]
    def handler(request):
        assert request.method=='GET'
        if request.url.host!='cdn.example': return httpx.Response(200,json=task())
        cdn.append(request)
        return httpx.Response(status,content=b'secret signed-url response')
    client=SeedanceClient(api_key='fake',transport=httpx.MockTransport(handler))
    with pytest.raises(SeedreamError,match='expired') as error:
        asyncio.run(client.download_task('cgt-one',str(tmp_path/'v.mp4')))
    assert 'secret' not in str(error.value)
    assert len(cdn)==1 and not list(tmp_path.iterdir())


@pytest.mark.parametrize('response',[httpx.Response(200,content=b'123456'),httpx.Response(200,content=b'<html>',headers={'content-type':'text/html'})])
def test_bad_or_oversized_download_not_published(response,tmp_path):
    def handler(request):
        return httpx.Response(200,json=task()) if request.url.host!='cdn.example' else response
    with pytest.raises(SeedreamError):
        asyncio.run(SeedanceClient(api_key='fake',transport=httpx.MockTransport(handler)).download_task('cgt-one',str(tmp_path/'v.mp4'),max_bytes=5))
    assert not list(tmp_path.iterdir())


def test_https_redirect_and_last_frame(tmp_path):
    def handler(request):
        if request.url.host!='cdn.example':
            return httpx.Response(200,json=task(content={'last_frame_url':'https://cdn.example/a?sig=x%2Fy'}))
        assert 'authorization' not in request.headers
        if request.url.path=='/a': return httpx.Response(302,headers={'location':'/b?sig=x%2Fy'})
        assert request.url.raw_path==b'/b?sig=x%2Fy'
        return httpx.Response(200,content=b'frame')
    result=asyncio.run(SeedanceClient(api_key='fake',transport=httpx.MockTransport(handler)).download_task('cgt-one',str(tmp_path/'f.png'),output='last_frame'))
    assert result['output']=='last_frame'


def test_list_filters_and_partial_batch_status():
    calls=[]
    def handler(request):
        calls.append(request)
        assert request.method=='GET'
        if request.url.query:
            assert request.url.params.get_list('filter.task_ids')==['cgt-one','cgt-two']
            assert request.url.params['filter.status']=='running'
            return httpx.Response(200,json={'total':22,'items':[task(status='running',created_at=1)]})
        if request.url.path.endswith('cgt-two'): return httpx.Response(500,json={'error':{'code':'Oops','message':'private-url'}})
        return httpx.Response(200,json=task(status='running'))
    client=SeedanceClient(api_key='fake',transport=httpx.MockTransport(handler))
    result=asyncio.run(client.list_tasks(page_size=22,status='running',task_ids=['cgt-one','cgt-two']))
    assert result['total']==22 and result['items'][0]['observation']['provider_progress'] is None
    result=asyncio.run(client.get_tasks(['cgt-one','cgt-two','cgt-one']))
    assert len(result['items'])==2 and result['failed_queries']==1
    assert result['items'][0]['provider_eta_seconds'] is None
    assert 'private-url' not in json.dumps(result)
    assert len(calls)==3


def test_preflight_and_unknown_billing_have_different_evidence():
    posts=[]
    def handler(request):
        posts.append(request)
        raise httpx.ReadTimeout('secret-url')
    client=SeedanceClient(api_key='fake',transport=httpx.MockTransport(handler))
    with pytest.raises(TaskError) as rejected:
        asyncio.run(client.create(VideoRequest(model='2.0',content=[{'type':'text','text':'demo'}],duration=30),VideoLocalOptions()))
    assert rejected.value.details['billing']['charged'] is False and not posts
    with pytest.raises(TaskError) as unknown:
        asyncio.run(client.create(VideoRequest(content=[{'type':'text','text':'demo'}]),VideoLocalOptions()))
    assert unknown.value.details['paid_request_sent'] is True
    assert unknown.value.details['billing']['charged'] is None
    assert unknown.value.details['billing']['refunded'] is None
    assert len(posts)==1


def test_successful_create_returns_mandatory_preflight_receipt():
    client=SeedanceClient(api_key='fake',transport=httpx.MockTransport(lambda _:httpx.Response(200,json={'id':'cgt-one'})))
    result=asyncio.run(client.create(VideoRequest(content=[{'type':'text','text':'demo'}]),VideoLocalOptions()))
    assert result['preflight']['valid'] and result['preflight']['paid_request_sent'] is False
    assert len(result['preflight']['request_sha256'])==64
    assert result['paid_request_sent'] is True and result['billing']['charged'] is None
