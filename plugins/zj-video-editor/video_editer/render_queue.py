"""Durable FIFO, one detached worker per data directory. Explicit cancellation."""
from contextlib import ExitStack
import os
import subprocess
import sys
import time
import threading
from . import engine, jobs, processes
from .locking import file_lock


def root():
    path=engine.PROJECTS.parent/'render_queue'
    path.mkdir(parents=True,exist_ok=True)
    return path


def records(project_id=None):
    paths=([engine.project_dir(project_id)] if project_id else sorted(engine.PROJECTS.glob('prj_*')))
    result=[]
    for project in paths:
        for path in (project/'mcp_renders').glob('*/state.json'):
            item=engine.read_json(path)
            if item.get('background'):
                result.append(item)
    return sorted(result,key=lambda r:(r['created_at'],r['job_id']))


def start_worker():
    # Caller holds dispatch.lock so the empty-queue shutdown cannot lose a submit.
    directory=root()
    env=dict(os.environ)
    env['VIDEO_EDITER_DATA_DIR']=str(engine.PROJECTS.parent)
    code='from video_editer.render_queue import worker; worker()'
    command=[sys.executable,'-c',code]
    options={'creationflags':getattr(subprocess,'CREATE_NO_WINDOW',0)} if os.name=='nt' else {'start_new_session':True}
    with (directory/'worker.log').open('ab') as log:
        child=subprocess.Popen(command,cwd=str(__import__('pathlib').Path(__file__).resolve().parent.parent),
                               env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,**options)
    # Reap when the host stays alive; detached worker still survives host exit.
    threading.Thread(target=child.wait,daemon=True,name='video-render-reaper').start()
    return {'worker_pid':child.pid,'queue_policy':'FIFO; one active background render per data directory'}


def submit(attempt):
    with file_lock(root()/'dispatch.lock'):
        attempt.state['background']=True
        attempt.save()
        try:
            worker_info=start_worker()
        except Exception as exc:
            attempt.state.update(status='failed',error={'type':type(exc).__name__,'message':str(exc)},finished_at=jobs.now())
            attempt.save()
            raise
    return {**jobs.status(attempt.project['id'],attempt.job_id),**worker_info}


def start():
    with file_lock(root()/'dispatch.lock'):
        return start_worker()


def cancel(project_id,job_id):
    path=jobs.folder(project_id,job_id)
    current=jobs.status(project_id,job_id)
    if current['status'] in {'succeeded','failed','cancelled','interrupted'}:
        return {**current,'cancel_accepted':False,'note':'Job is already terminal; no files were changed'}
    engine.write_json(path/'cancel.json',{'requested_at':jobs.now()})
    try:
        with file_lock(path/'running.lock'):
            state=engine.read_json(path/'state.json')
            if state['status']=='queued':
                state.update(status='cancelled',finished_at=jobs.now())
                engine.write_json(path/'state.json',state)
    except ValueError:
        pass
    return {**jobs.status(project_id,job_id),'cancel_accepted':True}


def worker():
    from .mcp_server import _execute_attempt
    directory=root()
    with ExitStack() as stack:
        try:
            stack.enter_context(file_lock(directory/'worker.lock'))
        except ValueError:
            return  # Existing worker will see submissions before releasing dispatch.lock.
        while True:
            pending=[r for r in records() if r['status']=='queued']
            if not pending:
                try:
                    with file_lock(directory/'dispatch.lock'):
                        if not any(r['status']=='queued' for r in records()):
                            stack.close()  # Release worker lock before allowing another submission.
                            return
                except ValueError:
                    time.sleep(.05)
                continue
            state=pending[0]
            pid,jid=state['project_id'],state['job_id']
            path=jobs.folder(pid,jid)
            try:
                # Atomic claim against queued cancellation or another process.
                with file_lock(path/'running.lock'):
                    latest=engine.read_json(path/'state.json')
                    if latest['status']!='queued': continue
                    attempt=jobs.Attempt.load(pid,jid)
                    _execute_attempt(attempt)
            except Exception as exc:
                with file_lock(path/'running.lock'):
                    latest=engine.read_json(path/'state.json')
                    if latest['status']=='queued':
                        latest.update(status='failed',finished_at=jobs.now(),error={'type':type(exc).__name__,'message':str(exc)[-4000:]})
                        engine.write_json(path/'state.json',latest)
                print(f'{jid}: {type(exc).__name__}: {exc}',flush=True)
