"""Real MCP transcription smoke test, with no customer editing or video render.

Explicit local source required. Results stay in --output, outside the plugin.
The user's shared heavy resource directory is preserved.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--plugin',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--source-model-dir',type=Path)
    parser.add_argument('--model-dir',type=Path)
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args()
    root=args.output.resolve();root.mkdir(parents=True,exist_ok=args.resume)
    environment=dict(os.environ,VIDEO_EDITER_DATA_DIR=str(root/'data'),
                     VIDEO_EDITER_ASR_MODEL_DIR=str(args.model_dir.resolve() if args.model_dir else root/'models/faster-whisper-small'),PYTHONUTF8='1')
    calls=json.loads((root/'calls.json').read_text('utf-8')) if args.resume else []
    with (root/'stdio.log').open('w',encoding='utf-8') as log:
        params=StdioServerParameters(command=sys.executable,args=[str(args.plugin.resolve()/'server.py')],
                                    cwd=str(args.plugin.resolve()),env=environment)
        async with stdio_client(params,errlog=log) as streams:
            async with ClientSession(*streams) as client:
                await client.initialize();listing=await client.list_tools()
                required={'speech_runtime_status','speech_model_prepare','speech_transcribe_submit',
                          'speech_candidates_get','speech_range_validate','commerce_speech_candidate_submit'}
                assert required <= {t.name for t in listing.tools}
                async def call(name,arguments,expect_error=False):
                    result=await client.call_tool(name,arguments)
                    value=result.structuredContent or (json.loads(result.content[0].text) if not result.isError else result.content[0].text)
                    calls.append({'tool':name,'arguments':arguments,'isError':bool(result.isError),'result':value})
                    (root/'calls.json').write_text(json.dumps(calls,ensure_ascii=False,indent=2),encoding='utf-8')
                    assert bool(result.isError)==expect_error,(name,value)
                    return value
                async def finish(job):
                    deadline=time.monotonic()+1800;last_print=0
                    while time.monotonic()<deadline:
                        status=await call('media_job_status',{'project_id':pid,'job_id':job['job_id']})
                        if status['status'] in ('succeeded','failed','cancelled','interrupted'):
                            assert status['status']=='succeeded',status
                            return status
                        if time.monotonic()-last_print>20:
                            print(json.dumps({'job':job['job_id'],'status':status['status'],'progress':status.get('progress'),'resource_wait':status.get('resource_wait')}),flush=True)
                            last_print=time.monotonic()
                        await asyncio.sleep(2)
                    raise TimeoutError('ASR validation job still pending; continue its saved job ID, do not resubmit')
                if args.resume:
                    pid=next(c['result']['project_id'] for c in calls if c['tool']=='project_create')
                    import_job=next(c['result'] for c in calls if c['tool']=='media_register')
                else:
                    pid=(await call('project_create',{'title':'ASR validation only - no edit'}))['project_id']
                    import_job=await call('media_register',{'project_id':pid,'source_path':str(args.source.resolve()),'mode':'reference'})
                imported=await finish(import_job)
                asset=imported['result']['asset'];aid=asset['id']
                await call('commerce_workflow_start',{'project_id':pid,'account':'local ASR test','product':'existing authorized local source'})
                before=await call('timeline_get',{'project_id':pid})
                await call('commerce_candidate_submit',{'project_id':pid,'asset_id':aid,'start':0,'end':3,'role':'hook_voice'},True)
                prep={'project_id':pid}
                if args.source_model_dir:prep['source_model_dir']=str(args.source_model_dir.resolve())
                await finish(await call('speech_model_prepare',prep))
                job=await call('speech_transcribe_submit',{'project_id':pid,'asset_id':aid})
                duplicate=await call('speech_transcribe_submit',{'project_id':pid,'asset_id':aid})
                assert job['job_id']==duplicate['job_id']
                if job['status'] in ('failed','cancelled','interrupted'):
                    job=await call('media_job_retry',{'project_id':pid,'job_id':job['job_id']})
                done=await finish(job)
                candidates=await call('speech_candidates_get',{'project_id':pid,'job_id':job['job_id'],'limit':100})
                assert candidates['items'],'Real source must produce sentence proposals'
                first=candidates['items'][0]
                good=await call('speech_range_validate',{'project_id':pid,'job_id':job['job_id'],'sentence_ids':[first['sentence_id']]})
                await call('speech_range_validate',{'project_id':pid,'job_id':job['job_id'],'sentence_ids':[first['sentence_id']],
                           'start':first['start']+.05,'end':good['end']},True)
                resumed=await call('commerce_workflow_status',{'project_id':pid})
                after=await call('timeline_get',{'project_id':pid})
                assert before==after,'ASR tools must not edit the timeline'
                assert not list((root/'data').rglob('mcp_renders/*/state.json'))
                jobs=await call('media_job_list',{'project_id':pid,'limit':100})
                assert not any(j['operation'] in ('commerce_inspect','delivery_inspect') for j in jobs['items'])
                resource=await call('resource_status',{})
                summary={'tool_count':len(listing.tools),'project_id':pid,'transcript_job_id':job['job_id'],
                    'sentence_count':candidates['total'],'word_count':done['result']['word_count'],
                    'backend':done['result']['backend'],'source_sha256':done['result']['source_sha256'],
                    'heavy_concurrency':resource['effective']['heavy_concurrency'],
                    'pre_asr_selection_blocked':True,'word_truncation_blocked':True,'submission_deduplicated':True,
                    'queued_retry_of':done.get('retry_of'),
                    'resume_next_tool':resumed['next_tool'],'timeline_unchanged':True,'render_submitted':False,
                    'candidate_preview_submitted':False,'listening_reviewed':False}
                (root/'verification.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps({k:v for k,v in summary.items() if k!='backend'},ensure_ascii=False),flush=True)
                print(json.dumps({'asr_device':summary['backend']['device'],'fallback_reason':summary['backend']['fallback_reason']},ensure_ascii=False),flush=True)


if __name__=='__main__':asyncio.run(main())
