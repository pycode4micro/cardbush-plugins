"""Rendering worker with durable status. Host process-tree termination is respected."""
from __future__ import annotations
from .processes import hidden_process_options
import json,os,queue,shutil,subprocess,sys,threading,time
from pathlib import Path
import psutil
from .jobs import JOB_FOLDER,write_json
from .models import VideoRequest,ProcessingOptions

def parse_event(line):
    try:event=json.loads(line)
    except (ValueError,TypeError):return None
    return event if isinstance(event,dict) else None

def terminate_owned_process(proc):
    try:
        parent=psutil.Process(proc.pid)
        children=parent.children(recursive=True)
        for child in children:
            try:child.terminate()
            except psutil.Error:pass
        try:parent.terminate()
        except psutil.Error:pass
        _,alive=psutil.wait_procs(children+[parent],timeout=3)
        for child in alive:
            try:child.kill()
            except psutil.Error:pass
    except psutil.Error:pass
    try:proc.wait(timeout=5)
    except subprocess.TimeoutExpired:proc.kill();proc.wait()

def publish_new(source:Path,target:Path):
    """Create a new output, refusing to replace any existing file."""
    created=False
    try:
        with target.open('xb') as dst:
            created=True
            with source.open('rb') as src:shutil.copyfileobj(src,dst)
    except Exception:
        if created:target.unlink(missing_ok=True)
        raise

def work(directory:Path):
    directory=directory.resolve()
    if directory.parent.name!=JOB_FOLDER:raise ValueError('Invalid job directory.')
    data=json.loads((directory/'request.json').read_text(encoding='utf-8'))
    request=VideoRequest.model_validate(data['request'])
    target=Path(data['output_path']).resolve();out=Path(request.output_directory)
    if target.parent!=out or target.suffix.lower()!='.mp4' or target==Path(request.input_path):
        raise ValueError('Invalid output destination.')
    if data['mode'] not in {'cpu','gpu'}:raise ValueError('Invalid mode.')
    state=json.loads((directory/'status.json').read_text(encoding='utf-8'))
    proc=None
    def update(**values):
        state.update(values);state['updated_at']=time.time()
        write_json(directory/'status.json',state)
    import hashlib
    reservation=out/JOB_FOLDER/'reservations'/(hashlib.sha256(target.name.casefold().encode()).hexdigest()+'.json')
    try:
        update(state='running',worker_pid=os.getpid(),worker_created=psutil.Process().create_time())
        if (directory/'cancel.request').exists():update(state='cancelled');return
        engine=Path(__file__).parent/'engine'/'plaster_face.py'
        temporary=directory/'rendering.mp4'
        cmd=[sys.executable,'-u',str(engine),request.input_path,str(temporary),
             '--renderer',data['mode'],'--start',str(request.start_seconds),
             '--max-height',str(request.max_height),
             '--settings',json.dumps(request.model_dump(include=set(ProcessingOptions.model_fields)))]
        if request.duration_seconds is not None:cmd+=['--seconds',str(request.duration_seconds)]
        env=os.environ.copy();env['PYTHONUTF8']='1';env['PYTHONUNBUFFERED']='1'
        env['NUMBA_CACHE_DIR']=str(out/JOB_FOLDER/'cpu-cache')
        kwargs=hidden_process_options()
        lines=queue.Queue();ended=threading.Event()
        with (directory/'engine.stderr.log').open('w',encoding='utf-8') as errlog:
            proc=subprocess.Popen(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=errlog,
                                  text=True,encoding='utf-8',errors='replace',env=env,**kwargs)
            def read_lines():
                try:
                    for line in proc.stdout:lines.put(line)
                finally:ended.set()
            reader=threading.Thread(target=read_lines,daemon=True);reader.start()
            with (directory/'engine.stdout.log').open('w',encoding='utf-8') as trace:
                while not (ended.is_set() and lines.empty() and proc.poll() is not None):
                    if (directory/'cancel.request').exists():
                        terminate_owned_process(proc);update(state='cancelled',error=None);return
                    try:line=lines.get(timeout=.25)
                    except queue.Empty:continue
                    trace.write(line);trace.flush()
                    event=parse_event(line)
                    if event is None:continue
                    if event.get('stage')=='start':
                        update(progress={'frames':0,'total_frames':event['frames'],'percent':0},
                               video={'width':event['size'][0],'height':event['size'][1],'fps':event['fps']},
                               renderer=event['renderer'])
                    elif 'frame' in event and 'total' in event:
                        update(progress={'frames':event['frame'],'total_frames':event['total'],
                               'percent':round(100*event['frame']/max(event['total'],1),2)},
                               pipeline_elapsed_seconds=event['elapsed_s'],detected_frames=event['detected'],coverage=event.get('coverage',{}))
            reader.join(timeout=2);proc.wait()
        if proc.returncode:
            detail=(directory/'engine.stderr.log').read_text(encoding='utf-8',errors='replace')[-4000:]
            raise RuntimeError(f'Render process failed ({proc.returncode}): {detail}')
        if (directory/'cancel.request').exists():update(state='cancelled');return
        benchmark=json.loads(temporary.with_suffix('.benchmark.json').read_text(encoding='utf-8'))
        import cv2
        cap=cv2.VideoCapture(str(temporary),cv2.CAP_FFMPEG,[cv2.CAP_PROP_HW_ACCELERATION,cv2.VIDEO_ACCELERATION_NONE])
        readable=cap.isOpened();frames=int(cap.get(cv2.CAP_PROP_FRAME_COUNT));cap.release()
        if not readable or frames!=benchmark['frames_processed'] or frames==0:
            raise RuntimeError('Rendered video failed output verification.')
        benchmark['output']=str(target)
        benchmark['job_id']=state['job_id']
        benchmark['job_wall_seconds']=time.time()-state['created_at']
        staging=directory/'result.benchmark.json';write_json(staging,benchmark)
        publish_new(staging,target.with_suffix('.benchmark.json'))
        try:publish_new(temporary,target)
        except Exception:
            # This sidecar was created exclusively by this worker above.
            target.with_suffix('.benchmark.json').unlink(missing_ok=True)
            raise
        update(state='succeeded',output_path=str(target),benchmark_path=str(target.with_suffix('.benchmark.json')),
               progress={'frames':frames,'total_frames':frames,'percent':100},benchmark=benchmark,
               detected_frames=benchmark['faces_detected_frames'],pipeline_elapsed_seconds=benchmark['wall_seconds'],
               coverage=benchmark['coverage'],review_intervals=benchmark['review_intervals'],
               job_wall_seconds=time.time()-state['created_at'],
               notes=['Head mode covers hair and face; face mode retains hair. Source video is unchanged.',
                      'Review coverage and review_intervals. missed_frames counts frames without a face mesh, including successful head fallback.',
                      'coverage.no_mask_frames counts frames with no mask (including empty shots); fallback_only_frames counts masked frames without a face mesh, once per frame.',
                      'A frame with a mask is not proof that every head is covered; no guarantee about downstream video generation.'])
    except Exception as exc:
        if proc is not None and proc.poll() is None:terminate_owned_process(proc)
        update(state='failed',error=str(exc),job_wall_seconds=time.time()-state['created_at'])
    finally:
        if reservation.exists():
            try:
                lock=json.loads(reservation.read_text(encoding='utf-8'))
                if lock.get('job_id')==state['job_id']:reservation.unlink()
            except (ValueError,OSError):pass

if __name__=='__main__':
    directory=Path(sys.argv[1]).resolve()
    try:
        work(directory)
    except Exception as exc:
        # Report failures even if input validation fails before the engine starts.
        # Only touch the existing, owned record and its matching reservation.
        if directory.parent.name!=JOB_FOLDER:raise
        status_path=directory/'status.json'
        if not status_path.is_file():raise
        status=json.loads(status_path.read_text(encoding='utf-8'))
        if status.get('job_id')!=directory.name:raise
        status.update(state='failed',error=str(exc),updated_at=time.time())
        write_json(status_path,status)
        for reservation in (directory.parent/'reservations').glob('*.json'):
            try:
                if json.loads(reservation.read_text(encoding='utf-8')).get('job_id')==directory.name:
                    reservation.unlink()
            except (OSError,ValueError):pass
