"""Real MCP + worker + CPU/GPU + encoding check on a generated local fixture.

Run explicitly: python tests/integration_smoke.py --output ABSOLUTE_DIRECTORY
No private video or network service is used.
"""
import argparse,asyncio,hashlib,json,os,subprocess,sys,time
from pathlib import Path
import cv2,numpy as np
from video_face_stylizer.processes import ffmpeg_executable,hidden_process_options
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client


def fixture(path):
    frames=[]
    for idx in range(20):
        frame=np.full((120,160,3),(25,45,65),np.uint8)
        cv2.rectangle(frame,(10,90),(25+idx,105),(90,120,180),-1)
        frames.append(frame)
    command=[ffmpeg_executable(),'-v','error','-f','rawvideo','-pixel_format','bgr24',
        '-video_size','160x120','-framerate','10','-i','pipe:0','-f','lavfi','-i','sine=frequency=440:sample_rate=44100',
        '-t','2','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(path)]
    result=subprocess.run(command,input=b''.join(frame.tobytes() for frame in frames),capture_output=True,timeout=30,**hidden_process_options())
    assert result.returncode==0,result.stderr


async def main(root,plugin=None):
    root.mkdir(parents=True,exist_ok=False)
    source=root/'source video.mp4';fixture(source)
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    report={'renders':[]}
    plugin=plugin or Path(__file__).resolve().parents[1]
    env={**os.environ,'PYTHONUTF8':'1','PYTHONUNBUFFERED':'1'}
    # Exercise the portable manifest's actual PowerShell entrypoint, not just Python imports.
    params=StdioServerParameters(command='powershell.exe',args=['-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(plugin/'scripts/start.ps1')],env=env)
    with (root/'mcp.stderr.log').open('w',encoding='utf-8') as err:
        async with stdio_client(params,errlog=err) as (reader,writer):
            async with ClientSession(reader,writer) as client:
                await client.initialize()
                async def call(name,args):
                    result=await client.call_tool(name,args)
                    assert not result.isError,str(result)
                    return result.structuredContent if result.structuredContent is not None else json.loads(result.content[0].text)
                tools=await client.list_tools()
                assert len(tools.tools)==5
                schema=next(tool.inputSchema for tool in tools.tools if tool.name=='render_video_cpu')
                assert 'head_regions' in json.dumps(schema) and 'min_face_detection_confidence' in json.dumps(schema)
                caps=await call('video_face_capabilities',{'check_gpu':True});report['capabilities']=caps
                for mode in ['cpu']+(['gpu'] if caps['gpu']['available'] else []):
                    job=await call('render_video_'+mode,{'request':{'input_path':str(source),'output_directory':str(root),
                        'start_seconds':.5,'duration_seconds':1,'tiled_detection':False,'tiled_segmentation':False,'coverage':'head',
                        'head_regions':[{'start_seconds':.7,'end_seconds':1.2,'keyframes':[{'seconds':.7,'box':[.25,.15,.6,.8]}]}]}})
                    deadline=time.monotonic()+120
                    while time.monotonic()<deadline:
                        status=await call('get_video_job',{'job_id':job['job_id'],'output_directory':str(root),'wait_seconds':10})
                        if status['state'] in {'succeeded','failed','cancelled','interrupted'}:break
                    assert status['state']=='succeeded',status
                    assert status['benchmark']['frames_processed']==10
                    assert status['coverage']['manual_mask_frames']==5,status['coverage']
                    assert status['coverage']['uncovered_frames']==5,status['coverage']
                    assert status['coverage']['no_mask_frames']==5,status['coverage']
                    assert status['coverage']['fallback_only_frames']==5,status['coverage']
                    assert status['benchmark']['missed_frames']==10,status['benchmark']
                    assert status['review_intervals']==[
                        {'start_seconds':.5,'end_seconds':.7,'reason':'person_not_detected'},
                        {'start_seconds':1.2,'end_seconds':1.5,'reason':'person_not_detected'}],status['review_intervals']
                    output=Path(status['output_path'])
                    result=subprocess.run([ffmpeg_executable(),'-v','error','-i',str(output),'-f','null','-'],capture_output=True,timeout=30,**hidden_process_options())
                    assert result.returncode==0,result.stderr
                    audio=subprocess.run([ffmpeg_executable(),'-v','error','-i',str(output),'-map','0:a:0','-f','null','-'],capture_output=True,timeout=30,**hidden_process_options())
                    assert audio.returncode==0,'Audio stream was lost'
                    cap=cv2.VideoCapture(str(output));images=[]
                    while True:
                        ok,frame=cap.read()
                        if not ok:break
                        images.append(frame)
                    cap.release()
                    assert images[0][50,65].max()<100 and images[3][50,65].min()>140 and images[-1][50,65].max()<100
                    report['renders'].append({'mode':mode,'frames':len(images),'coverage':status['coverage'],'path':str(output)})
    assert hashlib.sha256(source.read_bytes()).hexdigest()==digest
    report['passed']=True
    (root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--plugin-root',type=Path)
    args=parser.parse_args()
    asyncio.run(main(args.output.resolve(),args.plugin_root.resolve() if args.plugin_root else None))
