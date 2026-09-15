"""Opt-in 2-hour / >200MB synthetic media benchmark; retains every artifact.

This measures I/O, deep seeking, explicit annotation retrieval and rendering,
NOT model understanding or semantic recall on real commerce footage.
"""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import time
from video_editer import engine, media_ops
from video_editer import mcp_server as s


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    root=args.output.resolve() if args.output else Path(tempfile.mkdtemp(prefix='video-editer-two-hours-'))
    root.mkdir(parents=True,exist_ok=True)
    source=root/'two-hours-synthetic.mkv'
    engine.PROJECTS=root/'data'/'projects'
    timings={}
    started=time.monotonic()
    # PCM audio makes a genuine decodable >200MB file, not padding/fake metadata.
    subprocess.run([engine.ffmpeg_bin(),'-nostdin','-n','-f','lavfi','-i','testsrc2=s=320x180:r=2:d=7200',
                    '-f','lavfi','-i','sine=frequency=440:sample_rate=16000:duration=7200',
                    '-c:v','libx264','-preset','ultrafast','-crf','32','-g','120','-c:a','pcm_s16le',str(source)],
                   capture_output=True,check=True,timeout=600)
    timings['fixture_seconds']=time.monotonic()-started
    assert source.stat().st_size>200_000_000
    checksum=media_ops.digest(source)[0]
    pid=s.project_create('2-hour synthetic evidence benchmark')['project_id']

    def wait(job,name):
        start=time.monotonic()
        while time.monotonic()-start<300:
            state=s.media_job_status(pid,job['job_id'])
            if state['status'] in ('succeeded','failed','cancelled','interrupted'):
                assert state['status']=='succeeded',state
                timings[name]=time.monotonic()-start
                return state['result']
            time.sleep(.15)
        raise RuntimeError('Background media benchmark timeout')

    a=wait(s.media_register(pid,str(source),'reference'),'reference_register_seconds')['asset']
    assert abs(a['probe']['duration']-7200)<1
    directory=s.media_window_list(pid,a['id'],120,3,0,8)
    evidence=wait(s.media_segment_preview(pid,a['id'],7190,7198,640),'deep_preview_seconds')
    assert evidence['probe']['has_audio']
    again=wait(s.media_segment_preview(pid,a['id'],7190,7198,640),'verified_cache_seconds')
    assert again['cache_hit'] and again['path']==evidence['path']
    frames=wait(s.media_frames_at(pid,a['id'],[7192,7194,7196],640),'deep_frames_seconds')
    assert all(abs(f['actual_source_time']-f['requested_source_time'])<.51 for f in frames['frames'])
    label=s.segment_index_upsert(pid,a['id'],{
        'source_start':7190,'source_end':7198,'summary':'人工注入的基准标注：尾部测试图案，不是模型识别',
        'observation':'visual_frames','evidence_refs':[{'id':frames['id'],'version':1}],
        'tags':['基准关键点'],'quotes':[],'uncertainty':'只有三张合成图案帧，无语音语义判断',
        'sampling_note':'明确指定 7192/7194/7196 秒'})
    found=s.segment_index_search(pid,'尾部测试图案')
    assert found['total']==1
    candidate=s.candidate_add(pid,a['id'],{
        'source_start':7192,'source_end':7197,'segment_refs':[{'id':label['id'],'version':1}],
        'reason':'验证小时深处候选能回指原片','visual_evidence':'合成测试图案','tags':['基准关键点'],
        'quotes':[],'risks':['不是实拍商品或语义评测'],
        'boundary_review':{'opening':'unknown','ending':'unknown','speech':'not_applicable','action':'unknown','note':'仅测试工具执行链'}})
    s.canvas_configure(pid,320,256)
    s.clip_add(pid,a['id'],candidate['source_start'],candidate['source_end'],transition='none')
    start=time.monotonic(); rendered=s.render_final(pid); timings['deep_original_render_seconds']=time.monotonic()-start
    assert rendered['has_audio'] and abs(rendered['quality']['duration']-5)<.1
    assert media_ops.digest(source)[0]==checksum
    report={'passed':True,'fixture':'synthetic 2-hour test pattern + PCM tone; not real commerce footage',
            'source':str(source),'source_bytes':source.stat().st_size,'source_duration':a['probe']['duration'],
            'source_sha256':checksum,'original_unchanged':True,'gop_seconds':60,'input_fps':2,
            'timings_seconds':timings,'project_id':pid,'directory':directory,'evidence':evidence,'frames':frames,
            'coverage':s.media_coverage_get(pid,a['id']),'candidate':candidate,'render':rendered,
            'semantic_recall_measured':False,'model_calls':0,'peak_memory_measured':False}
    engine.write_json(root/'benchmark.json',report)
    print(json.dumps({'report':str(root/'benchmark.json'),'passed':True,'bytes':source.stat().st_size,'timings':timings},ensure_ascii=False,indent=2))


if __name__=='__main__': main()
