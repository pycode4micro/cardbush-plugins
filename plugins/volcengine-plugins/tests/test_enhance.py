import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from volcengine_plugins import config
from volcengine_plugins.enhance import EnhanceClient, EnhanceError, EnhanceRequest, enhance_capabilities, enhance_preview, prepare_enhance


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    for name in config.CONFIG_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('ARK_READ_USER_ENV','0')


def request(**kwargs):
    return EnhanceRequest(video_url='https://example.com/source.mp4?secret=hidden', **kwargs)


@pytest.mark.parametrize('variant', ['fast','professional','2.5',''])
def test_only_two_variants(variant):
    with pytest.raises(ValidationError): request(variant=variant)


@pytest.mark.parametrize('options', [
    {'tool_version':'professional'}, {'extra_body':{'tool_version':'professional'}}, {'bit_depth':16},
    {'resolution':'1080p','resolution_limit':1080}, {'variant':'generative','resolution':'4k'},
    {'variant':'generative','scene':'aigc'}, {'variant':'generative','enhance_style':'natural'},
    {'variant':'generative','resolution_limit':1080}, {'fps':121}, {'fps':14}, {'bitrate':9},
    {'callback_args':'中'*171}, {'client_token':'中'}, {'client_token':'a\n'},
    {'callback_url':'https://user:pass@example.com'}, {'media_output_destination':'https://example.com'},
    {'resolution_limit':True}, {'api_key':'secret'}])
def test_invalid_native_parameters(options):
    with pytest.raises(ValidationError): request(**options)


@pytest.mark.parametrize('uri', ['C:/a.mp4','data:video/mp4;base64,AAAA','https://a/x#frag','asset://123','https://user:pass@a/x'])
def test_invalid_inputs(uri):
    with pytest.raises(ValidationError): EnhanceRequest(video_url=uri)


@pytest.mark.parametrize('uri', ['https://a/x','http://a/x','mediakit://abc','tos://bucket/a','vod://space/id'])
def test_input_protocols(uri):
    assert EnhanceRequest(video_url=uri).video_url==uri


def test_native_mapping_and_redaction():
    req=request(scene='aigc',enhance_style='natural',resolution='1080p',client_token='token',callback_args='private')
    path, body=prepare_enhance(req)
    assert path.endswith('/enhance-video') and body['tool_version']=='standard'
    assert 'variant' not in body and 'fps' not in body
    preview=enhance_preview(req)
    assert preview['paid_request_sent'] is False
    assert 'hidden' not in json.dumps(preview) and 'private' not in json.dumps(preview)
    path, body=prepare_enhance(request(variant='generative',resolution='2k'))
    assert path.endswith('/enhance-video-generative') and 'tool_version' not in body
    assert enhance_preview(request(bitrate=9000,bitrate_level='high'))['warnings'][-1].startswith('Native bitrate')


def test_configuration_independent_live_user_key(monkeypatch):
    monkeypatch.setenv('ARK_API_KEY','ark-secret')
    assert not enhance_capabilities()['configured']
    with pytest.raises(EnhanceError,match='not configured'):
        asyncio.run(EnhanceClient().create(request()))
    monkeypatch.setenv('ARK_READ_USER_ENV','1')
    values={'MEDIAKIT_API_KEY':'media-secret'}
    monkeypatch.setattr(config,'_read_windows_user',lambda n: config.ConfigValue(values.get(n),'windows_user'))
    cap=enhance_capabilities()
    assert cap['configured'] and 'media-secret' not in json.dumps(cap)
    monkeypatch.setenv('MEDIAKIT_API_KEY','')
    assert not enhance_capabilities()['configured']


@pytest.mark.parametrize('variant', ['standard','generative'])
def test_create_query_and_same_native_token(monkeypatch,variant):
    monkeypatch.setenv('MEDIAKIT_API_KEY','media-secret')
    seen=[]
    def handler(r):
        seen.append(r)
        assert r.headers['authorization']=='Bearer media-secret'
        if r.method=='POST':
            body=json.loads(r.content)
            assert body['client_token']=='fixed-token' and body['resolution']=='1080p'
            assert ('tool_version' in body)==(variant=='standard')
            return httpx.Response(200,json={'success':True,'task_id':'amk-tool-test-123'})
        return httpx.Response(200,json={'success':True,'status':'completed','result':{'video_url':'https://result.example/out.mp4'}})
    async def run():
        client=EnhanceClient(transport=httpx.MockTransport(handler))
        created=await client.create(request(variant=variant,resolution='1080p',client_token='fixed-token'))
        result=await client.get(created['task']['task_id'])
        assert result['paid_request_sent'] is False and result['task']['status']=='completed'
    asyncio.run(run())
    assert len(seen)==2


@pytest.mark.parametrize('failure', ['timeout','403','500','redirect','badjson','missing_id'])
def test_errors_do_not_retry_or_leak(monkeypatch,failure):
    monkeypatch.setenv('MEDIAKIT_API_KEY','never-log-this-key')
    seen=[]
    def handler(r):
        seen.append(r)
        if failure=='timeout': raise httpx.ReadTimeout('never-log-this-key https://private.example',request=r)
        if failure=='badjson': return httpx.Response(200,text='never-log-this-key')
        if failure=='missing_id': return httpx.Response(200,json={'success':True})
        if failure=='redirect': return httpx.Response(302,headers={'Location':'https://private.example'})
        return httpx.Response(int(failure),json={'success':False,'error':{'code':'AccessDenied','message':'never-log-this-key https://private.example'}})
    with pytest.raises(EnhanceError) as e:
        asyncio.run(EnhanceClient(transport=httpx.MockTransport(handler)).create(request()))
    assert len(seen)==1 and 'never-log-this-key' not in str(e.value) and 'private.example' not in str(e.value)


@pytest.mark.parametrize('task_id',['../x','amk-tool-x?admin=1','https://a',''])
def test_task_path_validation(task_id):
    with pytest.raises(ValueError): asyncio.run(EnhanceClient().get(task_id))


def test_upload_unchanged_and_separate_auth(monkeypatch,tmp_path):
    monkeypatch.setenv('MEDIAKIT_API_KEY','media-secret')
    path=tmp_path/'sample.mp4'; path.write_bytes(b'video bytes')
    def api_handler(r):
        assert json.loads(r.content)=={} and r.headers['authorization']=='Bearer media-secret'
        return httpx.Response(200,json={'success':True,'result':{'file_id':'mediakit://already-prefixed',
            'upload_url':'https://upload.example/file?signature=private','method':'PUT',
            'upload_headers':[{'key':'Content-Type','value':'video/mp4'},{'key':'X-Upload','value':'signed'}]}})
    def upload_handler(r):
        assert 'authorization' not in r.headers
        assert r.headers['x-upload']=='signed' and r.content==b'video bytes'
        assert r.headers['content-type']=='video/mp4' and r.headers['content-length']=='11'
        return httpx.Response(200)
    result=asyncio.run(EnhanceClient(transport=httpx.MockTransport(api_handler),upload_transport=httpx.MockTransport(upload_handler)).upload(str(path)))
    assert result['video_url']=='mediakit://already-prefixed' and result['paid_request_sent'] is False
    assert 'signature' not in json.dumps(result)


def test_upload_rejects_nonvideo_and_empty(tmp_path):
    for name,data in [('secret.env',b'secret'),('empty.mp4',b'')]:
        p=tmp_path/name;p.write_bytes(data)
        with pytest.raises(ValueError): asyncio.run(EnhanceClient().upload(str(p)))


def test_mediakit_secret_not_expanded(monkeypatch):
    from test_config import fake_registry,REAL_USER_READER
    fake_registry(monkeypatch,'%PRIVATE_ENV%',2)
    monkeypatch.setenv('PRIVATE_ENV','expanded')
    assert REAL_USER_READER('MEDIAKIT_API_KEY').value=='%PRIVATE_ENV%'


@pytest.mark.parametrize('base', ['http://host', 'https://user:pass@host', 'https://host?key=secret', 'https://host/#frag'])
def test_invalid_base(monkeypatch,base):
    monkeypatch.setenv('MEDIAKIT_BASE_URL',base)
    with pytest.raises(ValueError): EnhanceClient()


@pytest.mark.parametrize('timeout', ['0','301','nan','inf','not-number'])
def test_invalid_timeout(monkeypatch,timeout):
    monkeypatch.setenv('MEDIAKIT_TIMEOUT_SECONDS',timeout)
    with pytest.raises(ValueError): EnhanceClient()


def test_failed_task_safe(monkeypatch):
    monkeypatch.setenv('MEDIAKIT_API_KEY','secret')
    handler=lambda r: httpx.Response(200,json={'success':True,'task_id':'amk-tool-test','status':'failed',
        'error':{'code':'DownloadFailed','message':'secret https://example.com/?secret'}})
    result=asyncio.run(EnhanceClient(transport=httpx.MockTransport(handler)).get('amk-tool-test'))
    assert result['task']['status']=='failed' and 'secret' not in json.dumps(result)


@pytest.mark.parametrize('mode', ['bad_method','insecure_url','bad_headers','upload_failure'])
def test_bad_upload_no_generation(monkeypatch,tmp_path,mode):
    monkeypatch.setenv('MEDIAKIT_API_KEY','secret')
    p=tmp_path/'input.mp4';p.write_bytes(b'123')
    calls=[]
    def handler(r):
        calls.append(r)
        result={'file_id':'abc','method':'PUT','upload_url':'https://upload.example/x','upload_headers':[]}
        if mode=='bad_method': result['method']='GET'
        if mode=='insecure_url': result['upload_url']='http://upload.example/x'
        if mode=='bad_headers': result['upload_headers']=[{'bad':'header'}]
        return httpx.Response(200,json={'success':True,'result':result})
    def upload_handler(r):
        assert 'authorization' not in r.headers
        return httpx.Response(403,text='secret')
    with pytest.raises(EnhanceError) as e:
        asyncio.run(EnhanceClient(transport=httpx.MockTransport(handler),upload_transport=httpx.MockTransport(upload_handler)).upload(str(p)))
    assert len(calls)==1 and 'secret' not in str(e.value)
