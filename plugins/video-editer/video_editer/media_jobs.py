"""Durable media jobs dispatched through the shared media/render FIFO."""
import hashlib
import subprocess
import time
import uuid
from pathlib import Path
from . import engine, media_ops, processes, task_queue, resources, audio_review
from .locking import file_lock


def root():
    path=engine.PROJECTS.parent/'media_queue'; path.mkdir(parents=True,exist_ok=True)
    return path


def folder(project_id,job_id):
    if not isinstance(job_id,str) or not job_id.startswith('media_') or not job_id[6:].isalnum(): raise ValueError('Invalid media job ID')
    path=engine.project_dir(project_id)/'media_jobs'/job_id
    if not (path/'state.json').is_file(): raise ValueError('Unknown media job')
    return path


def records(project_id=None):
    projects=[engine.project_dir(project_id)] if project_id else sorted(engine.PROJECTS.glob('prj_*'))
    rows=[]
    for project in projects:
        for path in (project/'media_jobs').glob('media_*/state.json'):
            value=engine.read_json(path)
            rows.append(value)
    return sorted(rows,key=lambda x:(x['created_at'],x['job_id']))


def status(project_id,job_id):
    path=folder(project_id,job_id); state=engine.read_json(path/'state.json')
    if state['status']=='running':
        try:
            with file_lock(path/'running.lock'):
                state=engine.read_json(path/'state.json')
                if state['status']=='running': state={**state,'status':'interrupted'}
        except ValueError:
            pass
    if state['status']=='succeeded': state['result']=engine.read_json(path/'result.json')
    return state


def start_worker():
    return task_queue.start_worker()


def submit(project_id, request, retry_of=None, start=True):
    directory=engine.project_dir(project_id)/'media_jobs'/('media_'+uuid.uuid4().hex[:20])
    directory.mkdir(parents=True,exist_ok=False)
    engine.write_json(directory/'request.json',request)
    state={'schema_version':1,'project_id':project_id,'job_id':directory.name,'status':'queued',
           'operation':request['operation'],'created_at':time.time(),'retry_of':retry_of,
           'request_sha256':hashlib.sha256((directory/'request.json').read_bytes()).hexdigest(),
           'progress':{'stage':'queued','completed':0,'total':None}}
    with file_lock(task_queue.root()/'dispatch.lock'):
        engine.write_json(directory/'state.json',state)
        if start:
            try: start_worker()
            except Exception as exc:
                state.update(status='failed',error={'type':type(exc).__name__,'message':str(exc)})
                engine.write_json(directory/'state.json',state)
                raise
    return state


def submit_unique(project_id, request):
    """Idempotent preparation; failures return their existing retry target."""
    with file_lock(engine.project_dir(project_id) / 'media-submit.lock'):
        for row in reversed(records(project_id)):
            if row['operation'] != request['operation']:
                continue
            path = folder(project_id, row['job_id'])
            if engine.read_json(path / 'request.json') != request:
                continue
            saved = status(project_id, row['job_id'])
            if saved['status'] == 'succeeded':
                try:
                    if media_ops.digest(path / 'result.json')[0] != saved.get('result_sha256'):
                        continue
                    if any(media_ops.digest(Path(f['path']))[0] != f['sha256'] for f in saved['result']['files']):
                        continue
                except (OSError, ValueError, KeyError):
                    continue
            return {**saved, 'reused': True,
                    'next_tool': 'media_job_retry' if saved['status'] in ('failed','cancelled','interrupted') else 'media_job_status'}
        return submit(project_id, request)


def cancel(project_id,job_id):
    path=folder(project_id,job_id); current=status(project_id,job_id)
    if current['status'] in ('succeeded','failed','cancelled','interrupted'):
        return {**current,'cancel_accepted':False}
    engine.write_json(path/'cancel.json',{'requested_at':time.time()})
    try:
        with file_lock(path/'running.lock'):
            state=engine.read_json(path/'state.json')
            if state['status']=='queued':
                state.update(status='cancelled',finished_at=time.time()); engine.write_json(path/'state.json',state)
    except ValueError:
        pass
    return {**status(project_id,job_id),'cancel_accepted':True}


def retry(project_id,job_id):
    previous=status(project_id,job_id)
    if previous['status'] not in ('failed','cancelled','interrupted'): raise ValueError('Only unsuccessful terminal jobs can be retried')
    path=folder(project_id,job_id); request_path=path/'request.json'
    if hashlib.sha256(request_path.read_bytes()).hexdigest()!=previous['request_sha256']: raise ValueError('Saved request checksum mismatch')
    return submit(project_id,engine.read_json(request_path),retry_of=job_id)


def start():
    with file_lock(task_queue.root()/'dispatch.lock'): return start_worker()


@resources.heavy_operation
def execute(project_id,job_id):
    path=folder(project_id,job_id)
    with file_lock(path/'running.lock'):
        state=engine.read_json(path/'state.json')
        if state['status']!='queued': return
        state['execution_resources']=resources.current_policy()
        def progress(stage,completed,total):
            latest=engine.read_json(path/'state.json')
            if latest.get('resource_events'): state['resource_events']=latest['resource_events']
            state['progress']={'stage':stage,'completed':completed,'total':total}
            engine.write_json(path/'state.json',state)
        try:
            state.update(status='running',started_at=time.time()); progress('starting',0,1)
            if hashlib.sha256((path/'request.json').read_bytes()).hexdigest()!=state['request_sha256']: raise ValueError('Saved request checksum mismatch')
            request=engine.read_json(path/'request.json')
            with processes.cancellation(path/'cancel.json'):
                if request['operation'] in ('speech_model_prepare', 'speech_transcribe'):
                    from . import speech
                    result=speech.execute(project_id,request,path,progress)
                elif request['operation']=='audio_review': result=audio_review.execute(request,path,progress)
                elif request['operation'] in ('commerce_inspect', 'delivery_inspect'):
                    from . import commerce_inspection
                    result=commerce_inspection.execute(project_id,request,path,progress)
                elif request['operation']=='register': result=media_ops.register(project_id,request,path,progress)
                else: result=media_ops.prepare(project_id,request,path,progress)
            # Cancellation after an atomic commit does not undo committed artifacts.
            engine.write_json(path/'result.json',result)
            state['result_sha256']=hashlib.sha256((path/'result.json').read_bytes()).hexdigest()
            latest=engine.read_json(path/'state.json')
            if latest.get('resource_events'): state['resource_events']=latest['resource_events']
            state.update(status='succeeded',finished_at=time.time()); progress('complete',1,1)
        except Exception as exc:
            message=str(exc)
            latest=engine.read_json(path/'state.json')
            if latest.get('resource_events'): state['resource_events']=latest['resource_events']
            if isinstance(exc,subprocess.CalledProcessError):
                message+=' '+(exc.stderr or b'').decode('utf-8','replace')[-2000:]
            state.update(status='cancelled' if isinstance(exc,processes.RenderCancelled) else 'failed',
                         finished_at=time.time(),error={'type':type(exc).__name__,'message':message[-4000:]})
            engine.write_json(path/'state.json',state)


def worker():
    task_queue.worker()
