"""Exercise installed-package tools through actual MCP stdio, with networking blocked."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from video_editer import engine


async def main():
    review = Path(sys.argv[1]).resolve()
    review.mkdir(parents=True, exist_ok=False)
    source = review / 'synthetic.mp4'
    overlay = review / 'overlay.png'
    subprocess.run([engine.ffmpeg_bin(), '-y', '-f', 'lavfi', '-i', 'testsrc2=s=320x480:r=25:d=3',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=3',
                    '-c:v', 'libx264', '-c:a', 'aac', str(source)], check=True, capture_output=True, timeout=90)
    subprocess.run([engine.ffmpeg_bin(), '-y', '-f', 'lavfi', '-i', 'color=c=lime:s=100x100',
                    '-frames:v', '1', str(overlay)], check=True, capture_output=True, timeout=60)
    env = dict(os.environ, VIDEO_EDITER_DATA_DIR=str(review / 'plugin data'))
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    # This child imports the installed wheel, not the extracted source tree.
    # Windows asyncio uses a loopback socket pair internally. Permit only that
    # local transport, while rejecting external addresses and DNS hostnames.
    code = '''import ipaddress, socket
original_connect = socket.socket.connect
def local_only(sock, address):
    if isinstance(address, tuple):
        try:
            if ipaddress.ip_address(address[0]).is_loopback:
                return original_connect(sock, address)
        except ValueError:
            pass
    raise RuntimeError("external network forbidden")
socket.socket.connect = local_only
from video_editer.mcp_server import main
main()
'''
    params = StdioServerParameters(command=sys.executable, args=['-c', code], env=env, cwd=str(review))
    calls = []
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            names = [t.name for t in (await session.list_tools()).tools]

            async def call(name, **arguments):
                result = await session.call_tool(name, arguments)
                if result.isError:
                    raise RuntimeError(f'{name}: {result.content}')
                calls.append(name)
                if result.structuredContent is not None:
                    return result.structuredContent
                return json.loads(next(item.text for item in result.content if item.type == 'text'))

            pid = (await call('project_create', title='Relocated standalone plugin'))['project_id']
            media = (await call('media_import', project_id=pid, source_path=str(source)))['asset']['id']
            image = (await call('media_import', project_id=pid, source_path=str(overlay)))['asset']['id']
            await call('media_inspect', project_id=pid)
            await call('media_extract_frames', project_id=pid, asset_id=media, frame_count=3)
            first = (await call('clip_add', project_id=pid, asset_id=media, start=0, end=1.4, transition='none'))['clip']['id']
            await call('clip_add', project_id=pid, asset_id=media, start=1.4, end=3, transition='none')
            await call('transition_list', season='summer')
            await call('transition_apply', project_id=pid, clip_id=first, transition='paint_flash')
            points = (await call('callout_points_add', project_id=pid, points=[
                {'text': 'Point A', 'start': .15, 'end': .65},
                {'text': 'Point B', 'start': .8, 'end': 1.25}], position='product_left'))['callout']['id']
            await call('event_bind', project_id=pid, event_id=points, clip_id=first, source_start=.15, source_end=1.25)
            await call('clip_update', project_id=pid, clip_id=first, playback_speed=1.25)
            await call('timeline_time_map', project_id=pid)
            await call('callout_add', project_id=pid, text='TEST', start=1.4, end=2.1,
                       position='product_right', template='comic_burst')
            graphic = (await call('overlay_add', project_id=pid, asset_id=image, start=.2, end=1.2,
                                  x=.8, y=.72, width=.12, height=.12))['overlay']['id']
            await call('overlay_keyframe', project_id=pid, overlay_id=graphic, at=.8, x=.65, y=.72)
            await call('subtitle_add', project_id=pid, text='Standalone MCP render', start=.2, end=2.1)
            await call('audio_duck_source', project_id=pid, volume=.7)
            await call('audio_track_add', project_id=pid, asset_id=media, start=.3, duration=1, source_start=.2, volume=.2)
            invalid = await session.call_tool('callout_add', {'project_id': pid, 'text': '|',
                                                             'start': 0, 'end': 1.2,
                                                             'position': 'product_left', 'template': 'point_list'})
            if not invalid.isError:
                raise AssertionError('Empty point-list was accepted')
            validation = await call('timeline_validate', project_id=pid)
            if not validation['valid']:
                raise AssertionError(validation)
            result = await call('render_final', project_id=pid)
            status = await call('render_job_status', project_id=pid, job_id=result['job_id'])
            if status['status'] != 'succeeded':
                raise AssertionError(status)
            await call('audio_inspect', media_path=result['path'])
            metadata = await call('output_inspect', video_path=result['path'])
            await call('quality_inspect', video_path=result['path'])
            if not metadata['valid'] or not metadata['has_audio']:
                raise AssertionError(metadata)
            if not 2.2 < metadata['probe']['duration'] < 2.45:
                raise AssertionError('Explicit timing changed: ' + str(metadata))
            if not Path(result['path']).is_relative_to(review / 'plugin data'):
                raise AssertionError('Output escaped standalone data directory')
            required={'canvas_configure','clip_camera_set','overlay_transform_set','render_submit','render_queue_list','render_cancel','render_queue_start'}
            if not required <= set(names):
                raise AssertionError('Advanced tools missing: '+str(required-set(names)))
            advanced_pid=(await call('project_create',title='Advanced relocated rendering'))['project_id']
            await call('canvas_configure',project_id=advanced_pid,width=640,height=360,fit='cover')
            still=(await call('media_import',project_id=advanced_pid,source_path=str(overlay)))['asset']['id']
            video=(await call('media_import',project_id=advanced_pid,source_path=str(source)))['asset']['id']
            shot=(await call('clip_add',project_id=advanced_pid,asset_id=still,start=0,end=1.4,transition='summer_liquid_flow'))['clip']['id']
            await call('clip_add',project_id=advanced_pid,asset_id=video,start=0,end=1.4,transition='none')
            await call('clip_camera_set',project_id=advanced_pid,clip_id=shot,camera={
                'start_zoom':1,'end_zoom':1.4,'start_x':.5,'end_x':.5,'start_y':.5,'end_y':.5,'easing':'ease_in_out'})
            await call('callout_list_templates')
            await call('callout_add',project_id=advanced_pid,text='轻盈自在',start=.9,end=1.8,position='product_left',template='minimal_label')
            oid=(await call('overlay_add',project_id=advanced_pid,asset_id=still,start=.2,end=1.8,x=.65,y=.1,width=.12,height=.15))['overlay']['id']
            await call('overlay_transform_set',project_id=advanced_pid,overlay_id=oid,mask='rounded_rect',keyframes=[
                {'at':.2,'scale':.5,'opacity':0,'rotation':-20},
                {'at':1,'scale':1.2,'opacity':1,'rotation':30,'easing':'ease_out'}])
            invalid_transform=await session.call_tool('overlay_transform_set',{'project_id':advanced_pid,'overlay_id':oid,'keyframes':[{'at':.5,'scale':20}]})
            if not invalid_transform.isError: raise AssertionError('Illegal transform accepted')
            queued=await call('render_submit',project_id=advanced_pid)
            for _ in range(240):
                state=await call('render_job_status',project_id=advanced_pid,job_id=queued['job_id'])
                if state['status'] in {'succeeded','failed','cancelled','interrupted'}: break
                await asyncio.sleep(.25)
            if state['status']!='succeeded': raise AssertionError(state)
            advanced=state['result']
            if (advanced['quality']['width'],advanced['quality']['height'])!=(640,360): raise AssertionError(advanced)
            if abs(advanced['quality']['duration']-2)>.045: raise AssertionError(advanced)
            await call('render_queue_list',project_id=advanced_pid)
            terminal=await call('render_cancel',project_id=advanced_pid,job_id=queued['job_id'])
            if terminal['cancel_accepted']: raise AssertionError('Already succeeded job was cancelled')
            async def media_wait(project_id,job):
                for _ in range(240):
                    current=await call('media_job_status',project_id=project_id,job_id=job['job_id'])
                    if current['status'] in {'succeeded','failed','cancelled','interrupted'}:
                        if current['status']!='succeeded': raise AssertionError(current)
                        return current['result']
                    await asyncio.sleep(.1)
                raise AssertionError('Media job timeout')
            evidence_pid=(await call('project_create',title='Relocated evidence tools'))['project_id']
            registered=await media_wait(evidence_pid,await call('media_register',project_id=evidence_pid,source_path=str(source),mode='reference'))
            aid=registered['asset']['id']
            await call('media_window_list',project_id=evidence_pid,asset_id=aid,window_seconds=2,overlap_seconds=.5)
            proxy=await media_wait(evidence_pid,await call('media_segment_preview',project_id=evidence_pid,asset_id=aid,source_start=0,source_end=3,max_edge=320))
            if not proxy['probe']['has_audio']: raise AssertionError('Evidence audio missing')
            frames=await media_wait(evidence_pid,await call('media_frames_at',project_id=evidence_pid,asset_id=aid,timestamps=[.4,1.2,2.4]))
            annotation=await call('segment_index_upsert',project_id=evidence_pid,asset_id=aid,annotation={
                'source_start':0,'source_end':3,'summary':'Synthetic test pattern; fixture metadata, not model recognition',
                'observation':'visual_frames','evidence_refs':[{'id':frames['id'],'version':1}],
                'tags':['synthetic'],'quotes':[],'uncertainty':'No speech assessment','sampling_note':'three targeted frames'})
            found=await call('segment_index_search',project_id=evidence_pid,query='Synthetic')
            if found['total']!=1: raise AssertionError(found)
            candidate=await call('candidate_add',project_id=evidence_pid,asset_id=aid,candidate={
                'source_start':.5,'source_end':2.5,'segment_refs':[{'id':annotation['id'],'version':1}],
                'reason':'Execution smoke fixture','visual_evidence':'Test pattern','tags':['synthetic'],'quotes':[],'risks':[],
                'boundary_review':{'opening':'unknown','ending':'unknown','speech':'not_applicable','action':'unknown','note':'No semantic approval'}})
            context=await call('candidate_preview',project_id=evidence_pid,candidate_id=candidate['id'])
            await media_wait(evidence_pid,context['job'])
            coverage=await call('media_coverage_get',project_id=evidence_pid,asset_id=aid)
            if coverage['coverage']['agent_audiovisual']['seconds']!=0: raise AssertionError('Frames treated as AV understanding')
            bad_index=await session.call_tool('segment_index_upsert',{'project_id':evidence_pid,'asset_id':aid,'annotation':{'summary':''}})
            if not bad_index.isError: raise AssertionError('Empty index accepted')
            evidence_report={'registered':registered,'preview':proxy,'frames':frames,'candidate':candidate,'coverage':coverage,'model_calls':0}
    report = {'success': True, 'python': sys.executable, 'engine': engine.__file__,
              'assets': str(engine.ASSETS), 'tools': len(names), 'calls': calls,
              'external_network_blocked_in_stdio_server': True, 'invalid_data_rejected': True, 'render': result,
              'advanced_background_render':advanced,'long_media_workflow':evidence_report}
    (review / 'verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
