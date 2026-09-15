"""Real local A/V evidence plus host-supplied lawful/malformed index contracts."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from video_editer import engine, media_ops, media_jobs, media_store, segments, jobs, processes
from video_editer import mcp_server as s


class MediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=Path(tempfile.mkdtemp(prefix='video-editer-media-'))
        cls.source=cls.root/'source.mp4'
        subprocess.run([engine.ffmpeg_bin(),'-nostdin','-y','-f','lavfi','-i','testsrc2=s=320x240:r=25:d=6',
                        '-f','lavfi','-i','sine=duration=6','-c:v','libx264','-g','150','-c:a','aac',str(cls.source)],capture_output=True,check=True)
        cls.offset=cls.root/'offset.mp4'
        subprocess.run([engine.ffmpeg_bin(),'-nostdin','-y','-i',str(cls.source),'-vf','setpts=PTS+9/TB',
                        '-af','asetpts=PTS+9/TB','-fps_mode','passthrough','-c:v','libx264','-c:a','aac',str(cls.offset)],capture_output=True,check=True)
        cls.vfr=cls.root/'vfr.mp4'
        subprocess.run([engine.ffmpeg_bin(),'-nostdin','-y','-i',str(cls.source),'-vf',
                        "select='if(lt(t,3),1,not(mod(n,3)))'",'-fps_mode','vfr','-c:v','libx264','-c:a','copy',str(cls.vfr)],capture_output=True,check=True)
        print('\nMEDIA_REVIEW='+str(cls.root),flush=True)

    def setUp(self):
        self.p=patch.object(engine,'PROJECTS',self.root/'data'/'projects'); self.p.start()
        self.pid=s.project_create('Long source evidence')['project_id']
        self.worker=patch.object(media_jobs,'start_worker',return_value={}); self.worker.start()
        self.a=self.run_job(s.media_register(self.pid,str(self.source),'reference'))['asset']

    def tearDown(self):
        self.worker.stop(); self.p.stop()

    def run_job(self,request):
        media_jobs.execute(self.pid,request['job_id'])
        result=s.media_job_status(self.pid,request['job_id'])
        self.assertEqual(result['status'],'succeeded',result)
        return result['result']

    def preview(self,start=1,end=4):
        return self.run_job(s.media_segment_preview(self.pid,self.a['id'],start,end,320))

    def annotation(self,e,**updates):
        value={'source_start':1,'source_end':4,'summary':'人工测试标注：展示领口细节',
               'observation':'audiovisual','evidence_refs':[{'id':e['id'],'version':e['version']}],
               'tags':['领口','细节'],'quotes':[{'text':'测试台词，不代表合成音频有语音','source_start':1.5,'source_end':3}],
               'uncertainty':'合成测试数据；插件不能验证台词真假'}
        value.update(updates); return value

    def test_reference_and_managed_copy_original_unchanged(self):
        original=self.source.read_bytes()
        b=self.run_job(s.media_register(self.pid,str(self.source)))['asset']
        self.assertEqual(b['identity']['sha256'],self.a['identity']['sha256'])
        self.assertNotEqual(b['path'],str(self.source))
        self.assertEqual(self.source.read_bytes(),original)
        self.assertEqual(Path(b['path']).read_bytes(),original)

    def test_preview_audio_mapping_cache_and_corrupt_cache(self):
        e=self.preview(); cached=self.preview()
        self.assertTrue(e['probe']['has_audio']); self.assertEqual(e['source_mapping']['offset'],1)
        self.assertEqual(cached['id'],e['id']); self.assertTrue(cached['cache_hit'])
        Path(e['path']).write_bytes(b'invalid derived file')
        fresh=self.preview()
        self.assertFalse(fresh['cache_hit']); self.assertNotEqual(fresh['path'],e['path'])
        self.assertEqual(Path(e['path']).read_bytes(),b'invalid derived file')

    def test_actual_frame_pts_and_legacy_nonoverwrite(self):
        r=self.run_job(s.media_frames_at(self.pid,self.a['id'],[.13,4.17],320))
        for f in r['frames']:
            self.assertGreaterEqual(f['actual_source_time']+.000001,f['requested_source_time'])
            self.assertLess(f['actual_source_time']-f['requested_source_time'],.041)
            self.assertTrue(Path(f['path']).is_file())
        x=s.media_extract_frames(self.pid,self.a['id'],1); y=s.media_extract_frames(self.pid,self.a['id'],1)
        self.assertNotEqual(x['frames'],y['frames']); self.assertTrue(Path(x['frames'][0]).exists())

    def test_offset_source_frames_and_preview(self):
        a=self.run_job(s.media_register(self.pid,str(self.offset),'reference'))['asset']
        self.assertGreater(a['probe']['container_start'],8)
        r=self.run_job(s.media_frames_at(self.pid,a['id'],[1.04,2.56],320))
        for f in r['frames']:
            self.assertLess(abs(f['actual_source_time']-f['requested_source_time']),.065)
        e=self.run_job(s.media_segment_preview(self.pid,a['id'],1,3,320))
        self.assertTrue(e['probe']['has_audio']); self.assertAlmostEqual(e['probe']['duration'],2,delta=.1)

    def test_vfr_actual_pts(self):
        a=self.run_job(s.media_register(self.pid,str(self.vfr),'reference'))['asset']
        r=self.run_job(s.media_frames_at(self.pid,a['id'],[2.1,4.09],320))
        self.assertLess(r['frames'][0]['actual_source_time']-2.1,.05)
        self.assertGreater(r['frames'][1]['actual_source_time']-4.09,.04)
        self.assertLess(r['frames'][1]['actual_source_time']-4.09,.13)

    def test_range_and_resource_budgets(self):
        for call in (lambda:s.media_segment_preview(self.pid,self.a['id'],-1,2),
                     lambda:s.media_segment_preview(self.pid,self.a['id'],0,7),
                     lambda:s.media_segment_preview(self.pid,self.a['id'],2,2),
                     lambda:s.media_frames_at(self.pid,self.a['id'],[float('nan')]),
                     lambda:s.media_frames_at(self.pid,self.a['id'],[6]),
                     lambda:s.media_frames_at(self.pid,self.a['id'],[1]*49),
                     lambda:s.media_segment_preview(self.pid,self.a['id'],0,2,1919),
                     lambda:s.media_prepare(self.pid,self.a['id'],'preview',{'start':0,'end':2,'shell':'x'})):
            with self.assertRaises(ValueError): call()

    def test_window_pagination_and_storyboard(self):
        p=s.media_window_list(self.pid,self.a['id'],2,.5,0,2)
        self.assertEqual(p['total'],4); self.assertEqual(p['next_offset'],2)
        last=s.media_window_list(self.pid,self.a['id'],2,.5,2,2)
        self.assertEqual(last['windows'][-1]['source_end'],6)
        board=s.media_storyboard_page(self.pid,self.a['id'],2,.5,0,2)
        self.assertEqual(len(self.run_job(board['job'])['frames']),6)
        with self.assertRaises(ValueError): s.media_window_list(self.pid,self.a['id'],2,1.5)

    def test_scan_does_not_mark_understood(self):
        e=self.run_job(s.media_prepare(self.pid,self.a['id'],'scan',{'start':0,'end':3}))
        self.assertEqual(e['operation'],'scan')
        cov=s.media_coverage_get(self.pid,self.a['id'])['coverage']
        self.assertEqual(cov['technical_scan']['seconds'],3)
        self.assertEqual(cov['agent_audiovisual']['seconds'],0)

    def test_annotation_search_versions_and_coverage(self):
        e=self.preview(); value=self.annotation(e)
        row=s.segment_index_upsert(self.pid,self.a['id'],value)
        found=s.segment_index_search(self.pid,'领口',tags=['细节'])
        self.assertEqual(found['items'][0]['id'],row['id'])
        self.assertEqual(s.segment_index_search(self.pid,'不存在')['total'],0)
        value['summary']='第二次观察领口'
        new=s.segment_index_upsert(self.pid,self.a['id'],value,row['id'],1)
        self.assertEqual(new['version'],2)
        self.assertNotEqual(s.segment_index_get(self.pid,row['id'],1)['summary'],new['summary'])
        with self.assertRaises(ValueError): s.segment_index_upsert(self.pid,self.a['id'],value,row['id'],1)
        cov=s.media_coverage_get(self.pid,self.a['id'])['coverage']
        self.assertEqual(cov['agent_audiovisual']['uncovered'],[[0.0,1.0],[4.0,6.0]])

    def test_frame_review_has_no_speech_or_interval_coverage(self):
        e=self.run_job(s.media_frames_at(self.pid,self.a['id'],[1,2,3],320))
        value=self.annotation(e,observation='visual_frames',quotes=[],sampling_note='每秒一帧')
        s.segment_index_upsert(self.pid,self.a['id'],value)
        cov=s.media_coverage_get(self.pid,self.a['id'])
        self.assertEqual(cov['sampled_frames']['count'],3)
        self.assertEqual(cov['coverage']['agent_audiovisual']['seconds'],0)
        with self.assertRaises(ValueError): s.segment_index_upsert(self.pid,self.a['id'],self.annotation(e))

    def test_invalid_annotation_never_commits(self):
        e=self.preview(); base=self.annotation(e)
        for change in ({'summary':''},{'source_end':5},{'source_start':float('inf')},{'evidence_refs':[]},
                       {'quotes':[{'text':'x','source_start':0,'source_end':1}]},{'observation':'model_said_so'},
                       {'tags':['']},{'uncertainty':''},{'extra':'execute this'}):
            with self.assertRaises((ValueError,TypeError)): s.segment_index_upsert(self.pid,self.a['id'],{**base,**change})
        self.assertEqual(s.segment_index_search(self.pid)['total'],0)

    def test_cross_source_evidence_rejected(self):
        e=self.preview(); b=self.run_job(s.media_register(self.pid,str(self.source),'reference'))['asset']
        with self.assertRaises(ValueError): s.segment_index_upsert(self.pid,b['id'],self.annotation(e))

    def test_same_content_cache_alias_pins_new_asset(self):
        e=self.preview(); b=self.run_job(s.media_register(self.pid,str(self.source),'reference'))['asset']
        alias=self.run_job(s.media_segment_preview(self.pid,b['id'],1,4,320))
        self.assertTrue(alias['cache_hit']); self.assertEqual(alias['path'],e['path'])
        self.assertNotEqual(alias['id'],e['id']); self.assertEqual(alias['asset_id'],b['id'])
        self.assertEqual(s.segment_index_upsert(self.pid,b['id'],self.annotation(alias))['asset_id'],b['id'])

    def test_evidence_must_be_intact_and_pinned(self):
        e=self.preview(); base=self.annotation(e)
        for version in (None,True,0,-1,1.5):
            data={**base,'evidence_refs':[{'id':e['id'],'version':version}]}
            with self.assertRaises(ValueError): s.segment_index_upsert(self.pid,self.a['id'],data)
        Path(e['path']).write_bytes(b'corrupted evidence')
        with self.assertRaises(ValueError): s.segment_index_upsert(self.pid,self.a['id'],base)

    def test_nested_annotations_and_explicit_missing_speech(self):
        e=self.preview(); p=s.segment_index_upsert(self.pid,self.a['id'],self.annotation(e))
        data=self.annotation(e,source_start=1.5,source_end=3,parent_ref={'id':p['id'],'version':1})
        child=s.segment_index_upsert(self.pid,self.a['id'],data)
        self.assertEqual(child['parent_ref']['id'],p['id'])
        with self.assertRaises(ValueError): s.segment_index_upsert(self.pid,self.a['id'],{**data,'source_start':0})

    def test_cancel_during_copy_retains_partial_and_no_import(self):
        req=s.media_register(self.pid,str(self.source),'managed_copy')
        folder=media_jobs.folder(self.pid,req['job_id']); request=engine.read_json(folder/'request.json')
        def progress(stage,completed,total):
            if stage=='copying': engine.write_json(folder/'cancel.json',{'stop':True})
        before=len(s._read(self.pid)['materials'])
        with self.assertRaises(processes.RenderCancelled):
            with processes.cancellation(folder/'cancel.json'):
                media_ops.register(self.pid,request,folder,progress)
        self.assertEqual(len(s._read(self.pid)['materials']),before)
        self.assertTrue((folder/'source.mp4').is_file())

    def test_registration_commit_preserves_parallel_timeline_edit(self):
        req=s.media_register(self.pid,str(self.source),'managed_copy')
        folder=media_jobs.folder(self.pid,req['job_id']); request=engine.read_json(folder/'request.json')
        changed=[]
        def progress(stage,completed,total):
            if stage=='copying' and not changed:
                s.canvas_configure(self.pid,640,360); changed.append(True)
        media_ops.register(self.pid,request,folder,progress)
        self.assertEqual(s.timeline_get(self.pid)['timeline']['canvas']['width'],640)

    def test_candidate_roundtrip_context_versions_no_timeline_edit(self):
        e=self.preview(); seg=s.segment_index_upsert(self.pid,self.a['id'],self.annotation(e))
        before=s.timeline_get(self.pid)
        data={'source_start':1.5,'source_end':3,'segment_refs':[{'id':seg['id'],'version':1}],
              'reason':'测试选择理由','visual_evidence':'测试画面证据','tags':['领口'],'quotes':seg['quotes'],'risks':[],
              'boundary_review':{'opening':'complete','ending':'complete','speech':'complete','action':'unknown','note':'人工合成测试判断'}}
        c=s.candidate_add(self.pid,self.a['id'],data)
        self.assertEqual(s.timeline_get(self.pid),before)
        p=s.candidate_preview(self.pid,c['id'],context_before=1,context_after=1)
        preview=self.run_job(p['job']); self.assertEqual(preview['source_start'],.5)
        self.assertEqual(p['candidate_range_in_preview'],[1,2.5])
        updated=s.candidate_update(self.pid,c['id'],1,{**data,'reason':'另一种比较'})
        self.assertEqual(updated['version'],2)
        self.assertEqual(s.candidate_list(self.pid,include_history=True)['total'],2)
        with self.assertRaises(ValueError): s.candidate_update(self.pid,c['id'],1,data)
        with self.assertRaises(ValueError): s.candidate_add(self.pid,self.a['id'],{**data,'source_end':5})
        with self.assertRaises(ValueError): s.candidate_add(self.pid,self.a['id'],{**data,'quotes':[]})
        s.canvas_configure(self.pid,320,256)
        s.clip_add(self.pid,self.a['id'],c['source_start'],c['source_end'],transition='none')
        self.assertTrue(s.render_final(self.pid)['has_audio'])

    def test_legacy_fingerprint_upgrade(self):
        legacy=s.media_import(self.pid,str(self.source),deduplicate=False)['asset']['id']
        with self.assertRaises(ValueError): s.media_segment_preview(self.pid,legacy,0,1)
        result=self.run_job(s.media_prepare(self.pid,legacy,'fingerprint'))
        self.assertIn('identity',result['asset'])

    def test_changed_source_rejects_index_cache_and_render(self):
        mutable=self.root/(self.pid+'.mp4'); mutable.write_bytes(self.source.read_bytes())
        a=self.run_job(s.media_register(self.pid,str(mutable),'reference'))['asset']
        old=mutable.stat()
        with mutable.open('r+b') as stream: stream.seek(-2,2); stream.write(b'xx')
        os.utime(mutable,ns=(old.st_atime_ns,old.st_mtime_ns))
        with self.assertRaises(ValueError): media_ops.asset(self.pid,a['id'],strong=True)
        req=s.media_prepare(self.pid,a['id'],'verify'); media_jobs.execute(self.pid,req['job_id'])
        self.assertEqual(s.media_job_status(self.pid,req['job_id'])['status'],'failed')
        project=s._read(self.pid); project['mcp_timeline']['tracks']['main']=[{'asset_id':a['id']}]
        with self.assertRaises(ValueError): jobs.media_manifest(project)

    def test_missing_reference_and_invalid_job_path(self):
        # Never remove an original; point a test-only project record at an absent file.
        project=s._read(self.pid); project['materials'][0]['path']=str(self.root/'absent.mp4'); s._write(self.pid,project)
        with self.assertRaises(ValueError): s.media_window_list(self.pid,self.a['id'])
        with self.assertRaises(ValueError): s.media_job_status(self.pid,'../project.json')

    def test_cancel_queued_retry_and_partial_files_retained(self):
        req=s.media_segment_preview(self.pid,self.a['id'],1,2)
        folder=media_jobs.folder(self.pid,req['job_id']); partial=folder/'partial.txt'; partial.write_text('retain')
        self.assertTrue(s.media_job_cancel(self.pid,req['job_id'])['cancel_accepted'])
        self.assertEqual(s.media_job_status(self.pid,req['job_id'])['status'],'cancelled')
        new=s.media_job_retry(self.pid,req['job_id']); self.assertNotEqual(new['job_id'],req['job_id'])
        self.run_job(new); self.assertEqual(partial.read_text(),'retain')
        self.assertFalse(s.media_job_cancel(self.pid,new['job_id'])['cancel_accepted'])

    def test_saved_request_tamper_and_interrupted_retry(self):
        req=s.media_segment_preview(self.pid,self.a['id'],1,2); folder=media_jobs.folder(self.pid,req['job_id'])
        state=engine.read_json(folder/'state.json'); state['status']='running'; engine.write_json(folder/'state.json',state)
        self.assertEqual(s.media_job_status(self.pid,req['job_id'])['status'],'interrupted')
        new=s.media_job_retry(self.pid,req['job_id']); self.run_job(new)
        engine.write_json(folder/'request.json',{'operation':'malicious'})
        with self.assertRaises(ValueError): s.media_job_retry(self.pid,req['job_id'])

    def test_corrupt_media_failure_and_disk_full(self):
        bad=self.root/(self.pid+'.bad'); bad.write_bytes(b'not a video')
        req=s.media_register(self.pid,str(bad),'reference'); media_jobs.execute(self.pid,req['job_id'])
        state=s.media_job_status(self.pid,req['job_id']); self.assertEqual(state['status'],'failed'); self.assertIn('error',state)
        req=s.media_register(self.pid,str(self.source),'managed_copy')
        with patch.object(media_ops,'check_space',side_effect=ValueError('disk full')): media_jobs.execute(self.pid,req['job_id'])
        self.assertEqual(s.media_job_status(self.pid,req['job_id'])['status'],'failed')
        self.assertTrue(self.source.exists())

    def test_cancellation_while_hashing(self):
        cancel=self.root/(self.pid+'.cancel'); cancel.write_text('stop')
        with processes.cancellation(None):
            with self.assertRaises(processes.RenderCancelled):
                with processes.cancellation(cancel): media_ops.digest(self.source)

    def test_real_detached_media_worker(self):
        self.worker.stop()
        req=s.media_segment_preview(self.pid,self.a['id'],1,2)
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            result=s.media_job_status(self.pid,req['job_id'])
            if result['status'] in ('succeeded','failed','interrupted'): break
            time.sleep(.1)
        self.assertEqual(result['status'],'succeeded',result)
        self.worker.start()


if __name__=='__main__': unittest.main()
