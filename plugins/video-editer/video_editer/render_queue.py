"""Durable FIFO, one detached worker per data directory. Explicit cancellation."""
from . import engine, jobs, task_queue
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
    from . import task_queue
    return task_queue.start_worker()


def submit(attempt):
    with file_lock(task_queue.root()/'dispatch.lock'):
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
    with file_lock(task_queue.root()/'dispatch.lock'):
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
    # Backwards-compatible launcher for existing clients.
    task_queue.worker()
