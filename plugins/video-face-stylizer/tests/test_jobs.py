import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from video_face_stylizer.models import VideoRequest
from video_face_stylizer import jobs
from video_face_stylizer.worker import publish_new,parse_event

@pytest.mark.parametrize('line',['"scalar string"','3','null','[]','not json','    "key": "value",'])
def test_progress_parser_ignores_non_events(line):
    assert parse_event(line) is None

def test_progress_parser_accepts_frame_event():
    assert parse_event('{"frame": 30, "total": 90}')=={'frame':30,'total':90}

@pytest.fixture
def request_data(tmp_path):
    source=tmp_path/'source.mp4';source.write_bytes(b'original source')
    return {'input_path':str(source),'output_directory':str(tmp_path/'result')}

@pytest.mark.parametrize('changes',[
    {'input_path':'relative.mp4'},{'input_path':'https://example.com/video.mp4'},
    {'output_directory':'relative'},{'output_filename':'../escape.mp4'},
    {'output_filename':'sub\\escape.mp4'},{'output_filename':'CON.mp4'},
    {'output_filename':'result.avi'},{'output_filename':'bad?.mp4'},
    {'duration_seconds':0},{'duration_seconds':float('nan')},
    {'start_seconds':-1},{'max_height':65},{'max_height':2},
])
def test_invalid_requests(request_data,changes):
    with pytest.raises(ValidationError):VideoRequest(**{**request_data,**changes})

def test_defaults(request_data):
    request=VideoRequest(**request_data)
    assert request.max_height==0 and request.duration_seconds is None

@pytest.mark.parametrize('sidecar',[False,True])
def test_existing_output_untouched(request_data,sidecar):
    directory=Path(request_data['output_directory']);directory.mkdir()
    existing=directory/('result.benchmark.json' if sidecar else 'result.mp4')
    existing.write_bytes(b'keep me')
    with pytest.raises(ValueError):
        jobs.start_job(VideoRequest(**request_data,output_filename='result.mp4'),'cpu')
    assert existing.read_bytes()==b'keep me'

def test_source_cannot_be_replaced(request_data):
    source=Path(request_data['input_path'])
    request_data['output_directory']=str(source.parent)
    with pytest.raises(ValueError):
        jobs.start_job(VideoRequest(**request_data,output_filename=source.name),'gpu')
    assert source.read_bytes()==b'original source'

def test_concurrent_filename_reservation(request_data,monkeypatch):
    launches=[]
    monkeypatch.setattr(jobs.subprocess,'Popen',lambda *a,**kw:launches.append(a))
    request=VideoRequest(**request_data,output_filename='reserved.mp4')
    first=jobs.start_job(request,'cpu')
    with pytest.raises(FileExistsError):jobs.start_job(request,'gpu')
    assert len(launches)==1
    assert jobs.get_job(first['job_id'],first['output_directory'])['state']=='queued'
    assert jobs.cancel_job(first['job_id'],first['output_directory'])['cancellation_requested']
    assert (jobs.job_directory(first['job_id'],first['output_directory'])/'cancel.request').is_file()

def test_stale_queued_task_is_interrupted(request_data,monkeypatch):
    monkeypatch.setattr(jobs.subprocess,'Popen',lambda *a,**kw:None)
    result=jobs.start_job(VideoRequest(**request_data),'cpu')
    status=jobs.job_directory(result['job_id'],result['output_directory'])/'status.json'
    original=json.loads(status.read_text());original['created_at']=0;jobs.write_json(status,original)
    assert jobs.get_job(result['job_id'],result['output_directory'])['state']=='interrupted'
    assert json.loads(status.read_text())['state']=='queued'  # Query remains read-only.

def test_worker_killed_before_start_is_detected(request_data,monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(jobs.subprocess,'Popen',lambda *a,**kw:SimpleNamespace(pid=999999999))
    result=jobs.start_job(VideoRequest(**request_data),'cpu')
    assert jobs.get_job(result['job_id'],result['output_directory'])['state']=='interrupted'

def test_unknown_job_cannot_escape(tmp_path):
    with pytest.raises(ValueError):jobs.get_job('../anything',str(tmp_path))

def test_publish_race_preserves_other_file(tmp_path):
    source=tmp_path/'render.mp4';source.write_bytes(b'new')
    target=tmp_path/'result.mp4';target.write_bytes(b'existing')
    with pytest.raises(FileExistsError):publish_new(source,target)
    assert target.read_bytes()==b'existing'

def test_failed_launch_releases_reservation(request_data,monkeypatch):
    def fail(*a,**kw):raise OSError('launch failed')
    monkeypatch.setattr(jobs.subprocess,'Popen',fail)
    with pytest.raises(OSError):jobs.start_job(VideoRequest(**request_data),'cpu')
    base=Path(request_data['output_directory'])/jobs.JOB_FOLDER
    assert not list((base/'reservations').glob('*.json'))
    assert json.loads(next(base.glob('*/status.json')).read_text())['state']=='failed'


def test_status_publish_survives_temporary_windows_read_lock(tmp_path,monkeypatch):
    path=tmp_path/'status.json';path.write_text('{"state":"running"}')
    replace=jobs.os.replace;attempts=[]
    def locked_then_available(source,target):
        attempts.append(1)
        if len(attempts)<3:
            error=PermissionError('busy reader');error.winerror=32;raise error
        replace(source,target)
    monkeypatch.setattr(jobs.os,'replace',locked_then_available)
    monkeypatch.setattr(jobs.time,'sleep',lambda seconds:None)
    jobs.write_json(path,{'state':'succeeded'})
    assert len(attempts)==3 and json.loads(path.read_text())['state']=='succeeded'
    assert not list(tmp_path.glob('*.tmp'))


def test_permanent_status_write_failure_preserves_previous_record(tmp_path,monkeypatch):
    path=tmp_path/'status.json';path.write_text('{"state":"running"}')
    attempts=[]
    def locked(*args):
        attempts.append(1);error=PermissionError('access denied');error.winerror=5;raise error
    monkeypatch.setattr(jobs.os,'replace',locked)
    monkeypatch.setattr(jobs.time,'sleep',lambda seconds:None)
    with pytest.raises(PermissionError):jobs.write_json(path,{'state':'succeeded'})
    assert len(attempts)==8 and json.loads(path.read_text())['state']=='running'
    assert not list(tmp_path.glob('*.tmp'))
