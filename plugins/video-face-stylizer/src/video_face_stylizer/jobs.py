"""Durable per-output-directory job records and local worker launches."""
from __future__ import annotations
from .processes import hidden_process_options
import json,os,re,subprocess,sys,time,uuid
from pathlib import Path
import psutil
from .models import VideoRequest

JOB_FOLDER='.video-face-stylizer-jobs'
TERMINAL={'succeeded','failed','cancelled','interrupted'}

def write_json(path:Path,data:dict):
    tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        # A Windows reader/virus scanner can briefly hold status.json without
        # FILE_SHARE_DELETE. Retry only this atomic metadata publish, never rendering.
        for attempt in range(8):
            try:
                os.replace(tmp,path)
                break
            except PermissionError as exc:
                if getattr(exc,'winerror',None) not in {5,32,33} or attempt==7:raise
                time.sleep(min(.01*2**attempt,.1))
    finally:
        if tmp.exists():tmp.unlink()

def job_directory(job_id:str,output_directory:str)->Path:
    if not re.fullmatch(r'[a-f0-9]{32}',job_id):raise ValueError('Invalid job_id. Use the ID returned by a render tool.')
    root=Path(output_directory)
    if not root.is_absolute():raise ValueError('output_directory must be absolute.')
    base=(root.resolve()/JOB_FOLDER).resolve()
    path=(base/job_id).resolve()
    if path.parent!=base or not path.is_dir():raise ValueError('Job not found in this output directory.')
    return path

def get_job(job_id:str,output_directory:str)->dict:
    path=job_directory(job_id,output_directory)
    record=json.loads((path/'status.json').read_text(encoding='utf-8'))
    if record.get('job_id')!=job_id or record.get('format')!='video-face-stylizer-job-v1':
        raise ValueError('Invalid job record.')
    process_record=record
    if not record.get('worker_pid') and (path/'process.json').is_file():
        process_record=json.loads((path/'process.json').read_text(encoding='utf-8'))
    if record['state'] not in TERMINAL and process_record.get('worker_pid'):
        try:
            proc=psutil.Process(process_record['worker_pid'])
            if abs(proc.create_time()-process_record['worker_created'])>.1 or not proc.is_running():raise psutil.NoSuchProcess(proc.pid)
        except psutil.Error:
            record={**record,'state':'interrupted','error':'The worker stopped before completing this video.'}
    elif record['state']=='queued' and time.time()-record['created_at']>120:
        record={**record,'state':'interrupted','error':'Worker did not start within 120 seconds.'}
    return record

def start_job(request:VideoRequest,mode:str)->dict:
    if mode not in {'cpu','gpu'}:raise ValueError('Mode must be cpu or gpu.')
    out=Path(request.output_directory)
    out.mkdir(parents=True,exist_ok=True)
    job_id=uuid.uuid4().hex
    stem=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',Path(request.input_path).stem)[:80]
    name=request.output_filename or f'plaster_{stem}_{mode}_{job_id[:8]}.mp4'
    target=out/name
    if target.resolve()==Path(request.input_path).resolve():raise ValueError('Input video cannot be overwritten.')
    if target.exists() or target.with_suffix('.benchmark.json').exists():raise ValueError('Output or benchmark already exists. Choose a new output_filename.')
    # Cooperative lock also prevents two MCP sessions from reserving the same name.
    locks=out/JOB_FOLDER/'reservations';locks.mkdir(parents=True,exist_ok=True)
    import hashlib
    reservation=locks/(hashlib.sha256(name.casefold().encode()).hexdigest()+'.json')
    with reservation.open('x',encoding='utf-8') as f:json.dump({'job_id':job_id,'output':str(target)},f)
    directory=out/JOB_FOLDER/job_id
    try:
        directory.mkdir()
        data={'format':'video-face-stylizer-job-v1','job_id':job_id,'state':'queued',
              'mode':mode,'created_at':time.time(),'updated_at':time.time(),
              'input_path':request.input_path,'output_directory':str(out),
              'planned_output_path':str(target),'output_path':None,'benchmark_path':None,
              'progress':{'frames':0,'total_frames':None,'percent':0},
              'next_poll_seconds':10}
        write_json(directory/'request.json',{'request':request.model_dump(),'mode':mode,
                   'output_path':str(target),'reservation_path':str(reservation)})
        write_json(directory/'status.json',data)
        env=os.environ.copy();env['PYTHONUTF8']='1';env['PYTHONUNBUFFERED']='1'
        kwargs=hidden_process_options(new_session=True)
        with (directory/'worker.log').open('ab') as log:
            worker=subprocess.Popen([sys.executable,'-m','video_face_stylizer.worker',str(directory)],
               stdin=subprocess.DEVNULL,stdout=log,stderr=log,env=env,**kwargs)
        if getattr(worker,'pid',None):
            try:created=psutil.Process(worker.pid).create_time()
            except psutil.Error:created=0
            # Separate file prevents a fast worker's running status being overwritten.
            write_json(directory/'process.json',{'worker_pid':worker.pid,'worker_created':created})
        return data
    except Exception:
        if reservation.exists():reservation.unlink()
        if directory.is_dir() and (directory/'status.json').exists():
            write_json(directory/'status.json',{**data,'state':'failed','error':'Failed to launch worker.'})
        raise

def cancel_job(job_id:str,output_directory:str)->dict:
    record=get_job(job_id,output_directory)
    if record['state'] in TERMINAL:return record
    directory=job_directory(job_id,output_directory)
    (directory/'cancel.request').touch(exist_ok=True)
    return {**record,'cancellation_requested':True,'note':'Cancellation requested; query this same job until it reaches a terminal state.'}
