"""Bounded local media access. All times are seconds relative to container start."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid
from . import engine, processes, media_store as store
from .locking import file_lock

VERSION = 'media-evidence-1'


def stat_signature(path):
    if not path.is_file():
        raise ValueError(f'Source unavailable: {path}')
    s = path.stat()
    return {'size':s.st_size,'mtime_ns':s.st_mtime_ns,'ctime_ns':s.st_ctime_ns}


def digest(path, progress=None):
    before=stat_signature(path); h=hashlib.sha256(); count=0; last=0
    with path.open('rb') as stream:
        while block := stream.read(4*1024*1024):
            processes.check(); h.update(block); count+=len(block)
            if progress and time.monotonic()-last>.5:
                progress('hashing',count,before['size']); last=time.monotonic()
    if before!=stat_signature(path):
        raise ValueError('Source changed during fingerprinting; register a new version')
    return h.hexdigest(),before


def asset(project_id, asset_id, strong=False, progress=None):
    project=engine.read_json(engine.project_dir(project_id)/'project.json')
    result=next((a for a in project['materials'] if a['id']==asset_id),None)
    if not result: raise ValueError('Unknown asset_id')
    if not result.get('identity'):
        raise ValueError('Legacy asset has no identity; run media_prepare(operation="fingerprint") first')
    path=Path(result['path']); current=stat_signature(path)
    if strong:
        checksum,_=digest(path,progress)
        if checksum!=result['identity']['sha256']:
            raise ValueError('Source content changed; old evidence cannot be used. Register as a new asset')
    elif current!=result['identity']['stat']:
        raise ValueError('Source stat changed; use media_prepare(operation="verify") to verify bytes')
    return result


def metadata(path):
    """Prefer ffprobe; portable fallback decodes one frame and reports precision."""
    probe=os.environ.get('FFPROBE_BIN') or shutil.which('ffprobe')
    if probe:
        p=processes.run([probe,'-v','error','-show_format','-show_streams','-of','json',str(path)],capture_output=True,check=True,timeout=120)
        raw=json.loads(p.stdout); fmt=raw.get('format',{}); streams=raw.get('streams',[])
        videos=[s for s in streams if s.get('codec_type')=='video']
        v=videos[0] if videos else {}
        duration=float(fmt.get('duration',v.get('duration',0)))
        start=float(fmt.get('start_time',0))
        details=[{k:s[k] for k in ('index','codec_type','time_base','start_time','duration','avg_frame_rate','r_frame_rate') if k in s} for s in streams]
        result={'duration':duration,'width':int(v.get('width',0)),'height':int(v.get('height',0)),
                'has_audio':any(s.get('codec_type')=='audio' for s in streams),'container_start':start,
                'streams':details,'metadata_precision_seconds':None,'metadata_backend':'ffprobe'}
    else:
        p=processes.run([engine.ffmpeg_bin(),'-nostdin','-hide_banner','-i',str(path),
                        '-map','0:v:0','-an','-vf','showinfo','-frames:v','1','-f','null','-'],capture_output=True,timeout=120)
        log=p.stderr.decode('utf-8','replace')
        dur=re.search(r'Duration: (\d+):(\d+):(\d+\.\d+), start: ([-\d.]+)',log)
        size=re.search(r'Video: .*?\b(\d{2,5})x(\d{2,5})\b',log)
        tb=re.search(r'config in time_base:\s*(\d+/\d+)',log)
        if p.returncode or not dur or not size or not tb:
            raise ValueError('Cannot probe/decode video (install ffprobe for additional formats): '+log[-1500:])
        duration=int(dur[1])*3600+int(dur[2])*60+float(dur[3]); start=float(dur[4])
        result={'duration':duration,'width':int(size[1]),'height':int(size[2]),'has_audio':'Audio:' in log,
                'container_start':start,'streams':[{'codec_type':'video','time_base':tb[1]}],
                'metadata_precision_seconds':.01,'metadata_backend':'ffmpeg-first-frame'}
    store.number(result['duration'],'media duration',.001,1e9)
    store.number(result['container_start'],'container start',-1e9,1e9)
    if not result['width'] or not result['height']: raise ValueError('A video stream is required')
    result['time_origin']='container-relative seconds; absolute PTS = source seconds + container_start'
    return result


def check_space(directory, required):
    if shutil.disk_usage(directory).free < required+64*1024*1024:
        raise ValueError('Insufficient disk space; no files were removed')


def register(project_id, request, directory, progress):
    source=Path(request['source_path']); mode=request['mode']; aid=request['asset_id']
    if mode not in ('reference','managed_copy'): raise ValueError('Invalid import mode')
    signature=stat_signature(source)
    if signature!=request['source_stat']: raise ValueError('Source changed after submission')
    progress('probing',0,1); info=metadata(source)
    target=source
    if mode=='managed_copy':
        check_space(directory,signature['size'])
        # Each retry gets a new directory, so interrupted partial copies are retained.
        target=directory/('source'+source.suffix.lower())
        h=hashlib.sha256(); done=0; last=0
        with source.open('rb') as incoming, target.open('xb') as outgoing:
            while block:=incoming.read(4*1024*1024):
                processes.check(); outgoing.write(block); h.update(block); done+=len(block)
                if time.monotonic()-last>.5:
                    progress('copying',done,signature['size']); last=time.monotonic()
        if stat_signature(source)!=signature: raise ValueError('Source changed during copy')
        checksum=h.hexdigest()
    else:
        checksum,_=digest(source,progress)
    processes.check()
    item={'id':aid,'name':source.name,'kind':'video','path':str(target),'probe':info,
          'identity':{'schema_version':1,'algorithm':'sha256','sha256':checksum,'stat':stat_signature(target)},
          'import_mode':mode,'original_path':str(source)}
    # Import commit rereads the live project, not the queued timeline snapshot.
    with file_lock(engine.project_dir(project_id)/'writer.lock'):
        from .mcp_server import _read, _snapshot, _commit
        project=_read(project_id)
        previous=next((a for a in project['materials'] if a['id']==aid),None)
        if previous:
            if previous.get('identity',{}).get('sha256')!=checksum: raise ValueError('Retry source identity conflict')
            return {'asset':previous,'already_committed':True}
        undo=_snapshot(project,'before media_register'); project['materials'].append(item)
        result=_commit(project_id,project,'media_register',{'asset_id':aid,'mode':mode},undo)
    return {**result,'asset':item,'warning':'Reference mode requires the original to remain available and unchanged' if mode=='reference' else None}


def fingerprint(project_id, request, progress):
    with file_lock(engine.project_dir(project_id)/'writer.lock'):
        project=engine.read_json(engine.project_dir(project_id)/'project.json')
        item=next((a for a in project['materials'] if a['id']==request['asset_id']),None)
        if not item or item['kind']!='video': raise ValueError('A video asset is required')
        item=dict(item)
    checksum,signature=digest(Path(item['path']),progress)
    if item.get('identity') and item['identity']['sha256']!=checksum:
        raise ValueError('Source content changed; register a new asset instead of rebinding old evidence')
    info=metadata(Path(item['path']))
    with file_lock(engine.project_dir(project_id)/'writer.lock'):
        from .mcp_server import _read, _snapshot, _commit
        project=_read(project_id); live=next(a for a in project['materials'] if a['id']==item['id'])
        if live['path']!=item['path'] or stat_signature(Path(item['path']))!=signature: raise ValueError('Concurrent source change')
        undo=_snapshot(project,'before media fingerprint')
        live.update(identity={'schema_version':1,'algorithm':'sha256','sha256':checksum,'stat':signature},probe=info)
        _commit(project_id,project,'media_fingerprint',{'asset_id':item['id']},undo)
    return {'asset':live}


def cache_key(a, operation, options):
    # Include implementation and executable identity; a changed renderer cannot reuse old proxies.
    binary=Path(shutil.which(engine.ffmpeg_bin()) or engine.ffmpeg_bin()).resolve()
    runtime={'implementation':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
             'binary':str(binary),'binary_stat':stat_signature(binary)}
    return hashlib.sha256(json.dumps([VERSION,a['identity']['sha256'],operation,options,runtime],sort_keys=True,allow_nan=False).encode()).hexdigest()


def cached(project_id, a, key):
    for item in store.rows(project_id,'evidence'):
        if item.get('cache_key')!=key: continue
        try:
            if all(digest(Path(f['path']))[0]==f['sha256'] for f in item['files']):
                if item['asset_id']!=a['id']:
                    payload={k:v for k,v in item.items() if k not in ('id','version','created_at','asset_id','source_sha256','schema_version')}
                    payload.update(cache_hit=True,reused_evidence={'id':item['id'],'version':item['version']})
                    return store.add(project_id,'evidence',a,payload)
                return {**item,'cache_hit':True}
        except ValueError:
            pass
    return None


def prepare(project_id, request, directory, progress):
    operation=request['operation']
    if operation in ('fingerprint','verify'): return fingerprint(project_id,request,progress)
    a=asset(project_id,request['asset_id'],strong=True,progress=progress)
    info=a['probe']; options=request['options']; source=Path(a['path'])
    key=cache_key(a,operation,options); prior=cached(project_id,a,key)
    if prior: return prior
    check_space(directory,32*1024*1024)
    common={'cache_key':key,'operation':operation,'time_origin':info['time_origin'],'source_container_start':info['container_start'],
            'files':[],'agent_reviewed':False,'cache_hit':False,'options':options}
    if operation=='preview':
        start,end=store.bounds(options['start'],options['end'],info['duration'])
        edge=options['max_edge']; output=directory/'preview.mp4'
        vf=f"scale=w='min({edge},iw)':h='min({edge},ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1"
        progress('encoding_preview',0,end-start)
        processes.run([engine.ffmpeg_bin(),'-nostdin','-n','-ss',f'{start:.9f}','-i',str(source),
                       '-t',f'{end-start:.9f}','-map','0:v:0','-map','0:a:0?','-vf',vf,
                       '-r','25','-c:v','libx264','-preset','ultrafast','-crf','29','-pix_fmt','yuv420p',
                       '-c:a','aac','-b:a','96k','-movflags','+faststart',str(output)],capture_output=True,check=True,timeout=max(180,30*(end-start)))
        actual=metadata(output)
        if info['has_audio'] and not actual['has_audio']: raise ValueError('Preview lost the source audio stream')
        if abs(actual['duration']-(end-start))>.15: raise ValueError('Preview duration mismatch; inspect source timestamps')
        common.update(source_start=start,source_end=end,path=str(output),probe=actual,
                      source_mapping={'scale':1.0,'offset':start,'output_frame_grid_seconds':.04,
                                      'source_frame_precision_seconds':None,'note':'Nominal viewing map, not a frame-exact inverse. VFR/low-fps boundaries require actual source PTS from media_frames_at'},
                      files=[{'path':str(output),'sha256':digest(output)[0]}])
    elif operation=='frames':
        frames=[]
        for i,stamp in enumerate(options['timestamps']):
            processes.check(); progress('extracting_frames',i,len(options['timestamps']))
            output=directory/f'frame-{i:03d}.jpg'; edge=options['max_edge']
            result=processes.run([engine.ffmpeg_bin(),'-nostdin','-n','-hide_banner','-copyts','-ss',f'{stamp:.9f}',
                                 '-i',str(source),'-an','-frames:v','1','-vf',
                                 f"showinfo,scale=w='min({edge},iw)':h='min({edge},ih)':force_original_aspect_ratio=decrease",'-q:v','3',str(output)],capture_output=True,check=True,timeout=180)
            log=result.stderr.decode('utf-8','replace')
            pts=re.search(r'\bn:\s*0\s+pts:\s*(-?\d+)\s+pts_time:([-\d.e+]+)',log)
            if not output.is_file() or not pts: raise ValueError('No decoded frame/timestamp at requested position')
            actual=float(pts[2])-info['container_start']
            frames.append({'requested_source_time':stamp,'actual_source_time':actual,'absolute_pts':int(pts[1]),
                           'time_base':next(s.get('time_base') for s in info['streams'] if s.get('codec_type')=='video'),'path':str(output)})
            common['files'].append({'path':str(output),'sha256':digest(output)[0]})
        common.update(frames=frames,source_start=min(options['timestamps']),source_end=max(options['timestamps']))
    elif operation=='scan':
        start,end=store.bounds(options['start'],options['end'],info['duration']); progress('technical_scan',0,end-start)
        # Signals only. No scene detector chooses editorial clips or implies full understanding.
        out=directory/'scan.log'
        with out.open('xb') as log:
            processes.run([engine.ffmpeg_bin(),'-nostdin','-hide_banner','-ss',str(start),'-i',str(source),
                           '-t',str(end-start),'-map','0:v:0','-map','0:a:0?',
                           '-vf','scale=160:-2,blackdetect=d=0.2:pix_th=0.10',
                           '-af','silencedetect=noise=-35dB:d=0.3','-f','null','-'],stdout=log,stderr=log,check=True,timeout=max(180,30*(end-start)))
        log=out.read_text('utf-8',errors='replace')
        events=[]
        for match in re.finditer(r'(black_start|black_end|silence_start|silence_end):\s*([-\d.]+)',log):
            events.append({'type':match[1],'source_time':start+float(match[2])})
        common.update(source_start=start,source_end=end,signals=events,files=[{'path':str(out),'sha256':digest(out)[0]}],
                      note='Black/silence signals only; not sentence boundaries, speech transcription or semantic review')
    else:
        raise ValueError('Unsupported media operation')
    # Recheck fast change hint after decode; strong check was done before work/cache access.
    if stat_signature(source)!=a['identity']['stat']: raise ValueError('Source changed during evidence generation')
    processes.check()
    return store.add(project_id,'evidence',a,common)


def validate_options(project_id, asset_id, operation, options):
    if operation in ('fingerprint','verify'):
        if options: raise ValueError('Fingerprint/verify takes no options')
        return
    a=asset(project_id,asset_id); duration=a['probe']['duration']
    if operation in ('preview','scan'):
        allowed={'start','end','max_edge'} if operation=='preview' else {'start','end'}
        if set(options)-allowed or not {'start','end'}<=options.keys(): raise ValueError('Unexpected or missing range options')
        start,end=store.bounds(options['start'],options['end'],duration)
        if end-start>600: raise ValueError('One evidence window may be at most 600s; use paginated windows')
    elif operation=='frames':
        if set(options)-{'timestamps','max_edge'} or not isinstance(options.get('timestamps'),list) or not 1<=len(options['timestamps'])<=48:
            raise ValueError('frames requires 1-48 explicit timestamps')
        for stamp in options['timestamps']:
            store.number(stamp,'timestamp',high=duration)
            if stamp>=duration: raise ValueError('Frame timestamp must precede source end')
    else: raise ValueError('Unknown operation')
    if operation in ('preview','frames'):
        edge=options.get('max_edge',640)
        if not isinstance(edge,int) or isinstance(edge,bool) or edge%2 or not 160<=edge<=1920:
            raise ValueError('max_edge must be even, 160..1920')
        options['max_edge']=edge


def windows(project_id,asset_id,window_seconds=120,overlap_seconds=2,offset=0,limit=12):
    a=asset(project_id,asset_id); duration=a['probe']['duration']
    window=store.number(window_seconds,'window_seconds',2,600)
    overlap=store.number(overlap_seconds,'overlap_seconds',0,window/2)
    store.number(offset,'offset',0,1000000); store.number(limit,'limit',1,100)
    if not isinstance(offset,int) or not isinstance(limit,int): raise ValueError('Pagination must be integers')
    import math
    step=window-overlap; total=max(1,math.ceil(max(0,duration-window)/step)+1)
    items=[{'window_index':i,'source_start':i*step,'source_end':min(i*step+window,duration)} for i in range(offset,min(offset+limit,total))]
    return {'asset_id':asset_id,'source_sha256':a['identity']['sha256'],'windows':items,'total':total,
            'next_offset':offset+limit if offset+limit<total else None,
            'note':'Directory only; no windows have been decoded or reviewed by this call'}
