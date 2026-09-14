"""Opt-in real STDIO/CPU/GPU lifecycle test. No video content is bundled."""
import asyncio,hashlib,json,os,subprocess,sys,tempfile,time
from contextlib import asynccontextmanager
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client

@asynccontextmanager
async def session():
    env=os.environ.copy();env['PYTHONUTF8']='1';env['PYTHONUNBUFFERED']='1'
    with open(os.devnull,'w') as err:
        async with stdio_client(StdioServerParameters(command=sys.executable,args=['-m','video_face_stylizer'],env=env),errlog=err) as (read,write):
            async with ClientSession(read,write) as client:
                await client.initialize()
                yield client

async def call(client,name,args):
    response=await client.call_tool(name,args)
    assert not response.isError,str(response)
    if response.structuredContent is not None:return response.structuredContent
    return json.loads(response.content[0].text)

async def poll(client,job,timeout=180):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        status=await call(client,'get_video_job',{'job_id':job['job_id'],'output_directory':job['output_directory'],'wait_seconds':10})
        if status['state'] in {'succeeded','failed','cancelled','interrupted'}:return status
    raise AssertionError('Job did not complete before test timeout.')

def check_video(status,expected_frames):
    assert status['state']=='succeeded',status
    import cv2,imageio_ffmpeg
    path=Path(status['output_path']);assert path.is_file()
    cap=cv2.VideoCapture(str(path));frames=int(cap.get(cv2.CAP_PROP_FRAME_COUNT));fps=cap.get(cv2.CAP_PROP_FPS);cap.release()
    assert frames==expected_frames,(frames,expected_frames)
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-i',str(path),'-f','null','-'],check=True,capture_output=True)
    assert status['benchmark']['frames_processed']==frames
    return {'mode':status['mode'],'frames':frames,'fps':fps,'seconds':frames/fps,
            'wall_seconds':status['job_wall_seconds'],'output':str(path),'benchmark':status['benchmark']}

async def main():
    source=Path(os.environ['VFS_TEST_VIDEO']).resolve()
    original_hash=hashlib.sha256(source.read_bytes()).hexdigest()
    base=Path(os.environ.get('VFS_TEST_OUTPUT',tempfile.gettempdir())).resolve()
    base.mkdir(parents=True,exist_ok=True)
    root=Path(tempfile.mkdtemp(prefix='mcp-smoke-',dir=base))
    report={'output_directory':str(root),'checks':[],'renders':[]}
    request={'input_path':str(source),'output_directory':str(root),'duration_seconds':1}
    async with session() as client:
        listed=await client.list_tools()
        names={t.name for t in listed.tools}
        assert names=={'video_face_capabilities','render_video_cpu','render_video_gpu','get_video_job','cancel_video_job'}
        assert all(t.icons for t in listed.tools)
        assert next(t for t in listed.tools if t.name=='get_video_job').annotations.readOnlyHint
        report['checks'].append('MCP initialize, five tools, JSON schemas, icons and annotations')
        caps=await call(client,'video_face_capabilities',{'check_gpu':True})
        assert caps['models_present'] and caps['gpu']['available'],caps
        report['capabilities']=caps
        print('MCP tool discovery and GPU capability passed.',flush=True)
        cpu=await call(client,'render_video_cpu',{'request':request})
        assert cpu['state']=='queued' and cpu['output_path'] is None
        done=await poll(client,cpu)
        report['renders'].append(check_video(done,30))
        print('CPU actual render passed.',flush=True)
        gpu=await call(client,'render_video_gpu',{'request':{**request,'start_seconds':5}})
        # Close this MCP connection deliberately while its worker is running.
    async with session() as client:
        done=await poll(client,gpu)
        if done['state']=='interrupted':
            report['checks'].append('MCP host terminated worker on disconnect; persistent query reports interrupted')
            gpu=await call(client,'render_video_gpu',{'request':{**request,'start_seconds':5}})
            done=await poll(client,gpu)
        else:
            report['checks'].append('GPU worker continues after this MCP client disconnects')
        report['renders'].append(check_video(done,30))
        report['checks'].append('Reconnect queries the existing durable job record')
        print('GPU actual render and reconnection passed.',flush=True)
        cancelled=await call(client,'render_video_gpu',{'request':{**request,'duration_seconds':30}})
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            progress=await call(client,'get_video_job',{'job_id':cancelled['job_id'],'output_directory':str(root),'wait_seconds':10})
            if progress.get('progress',{}).get('frames',0)>0:break
            assert progress['state'] in {'queued','running'},progress
        else:raise AssertionError('Cancel target never started rendering.')
        await call(client,'cancel_video_job',{'job_id':cancelled['job_id'],'output_directory':str(root)})
        stopped=await poll(client,cancelled,30)
        assert stopped['state']=='cancelled',stopped
        assert not Path(stopped['planned_output_path']).exists()
        report['checks'].append('Cancel active GPU task without publishing partial output')
        bad=root/'invalid.mp4';bad.write_bytes(b'not a video')
        invalid=await call(client,'render_video_cpu',{'request':{**request,'input_path':str(bad)}})
        failed=await poll(client,invalid,60)
        assert failed['state']=='failed',failed
        assert not Path(failed['planned_output_path']).exists()
        report['checks'].append('Invalid video becomes failed, with error and no completed output')
        outside=await call(client,'render_video_cpu',{'request':{**request,'start_seconds':99999}})
        failed=await poll(client,outside,60)
        assert failed['state']=='failed',failed
        report['checks'].append('Start beyond EOF fails instead of reporting empty video success')
    assert hashlib.sha256(source.read_bytes()).hexdigest()==original_hash
    # An externally killed worker may leave its reservation; it never owns a completed video.
    remaining=list((root/'.video-face-stylizer-jobs'/'reservations').glob('*.json'))
    for reservation in remaining:
        lock=json.loads(reservation.read_text())
        from video_face_stylizer.jobs import get_job
        assert get_job(lock['job_id'],str(root))['state']=='interrupted'
    report['checks']+=['Original source SHA256 unchanged','All completed/failed/cancelled reservations released','Both rendered videos fully decoded without FFmpeg error']
    report['passed']=True
    (root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':True,'report':str(root/'report.json'),'checks':report['checks']},ensure_ascii=False),flush=True)

if __name__=='__main__':asyncio.run(main())
