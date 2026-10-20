"""Small synthetic media only: cache correctness, cut signals and derived media."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from video_editer import analysis_cache, commerce_inspection, engine, jobs, media_jobs, media_ops
from video_editer import resources, scene_analysis, speech, review_evidence
from video_editer import mcp_server as m
from review_fixtures import receipt


class OptimizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='video-editer-optimization-')
        cls.root = Path(cls.temp.name)
        cls.source = cls.root/'source.mp4'
        subprocess.run([engine.ffmpeg_bin(), '-nostdin', '-y',
                        '-f','lavfi','-i','testsrc2=size=256x384:rate=25:duration=4',
                        '-f','lavfi','-i','sine=frequency=440:duration=4',
                        '-c:v','libx264','-threads','1','-c:a','aac',str(cls.source)],
                       capture_output=True,check=True,timeout=60)
        cls.cuts = cls.root/'cuts.mp4'
        subprocess.run([engine.ffmpeg_bin(), '-nostdin','-y',
                        '-f','lavfi','-i','color=red:s=160x240:r=25:d=1.4',
                        '-f','lavfi','-i','color=blue:s=160x240:r=25:d=0.2',
                        '-f','lavfi','-i','color=red:s=160x240:r=25:d=2.4',
                        '-filter_complex','[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]',
                        '-map','[v]','-c:v','libx264','-threads','1',str(cls.cuts)],
                       capture_output=True,check=True,timeout=60)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.storage = tempfile.TemporaryDirectory(dir=self.root)
        self.data = Path(self.storage.name)
        self.patches = [patch.object(engine,'PROJECTS',self.data/'projects'),
                        patch.object(resources,'root',return_value=self.data),
                        patch.object(resources,'heavy_slot'),
                        patch.object(media_jobs,'start_worker',return_value={})]
        for p in self.patches: p.start()
        self.pid = m.project_create('Synthetic optimization test')['project_id']
        self.asset = self.register(self.pid,self.source)

    def register(self, project_id, path):
        job=m.media_register(project_id,str(path))
        media_jobs.execute(project_id,job['job_id'])
        state=media_jobs.status(project_id,job['job_id'])
        self.assertEqual(state['status'],'succeeded',state.get('error'))
        return state['result']['asset']['id']

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.storage.cleanup()

    def test_audio_only_edit_reuses_picture_stages_and_not_old_sound(self):
        m.canvas_configure(self.pid,256,384)
        m.clip_add(self.pid,self.asset,0,3,transition='none')
        cue=m.audio_track_add(self.pid,self.asset,.5,1,volume=.2,role='sfx')['audio']['id']
        start=time.perf_counter(); first=m.render_final(self.pid); cold=time.perf_counter()-start
        m.audio_track_update(self.pid,cue,volume=.4)
        start=time.perf_counter(); second=m.render_final(self.pid); warm=time.perf_counter()-start
        self.assertEqual(set(second['reused_stages']),{'base','graphics','captions'})
        first_state=jobs.status(self.pid,first['job_id'])
        second_state=jobs.status(self.pid,second['job_id'])
        self.assertEqual(first_state['stages']['captions']['sha256'],second_state['stages']['captions']['sha256'])
        self.assertNotEqual(first_state['stages']['audio']['sha256'],second_state['stages']['audio']['sha256'])
        self.assertFalse(m.delivery_review_get(self.pid,second['job_id'])['delivery_ready'])
        m.subtitle_add(self.pid,'test',.2,1.3)
        third=m.render_final(self.pid)
        self.assertEqual(set(third['reused_stages']),{'base','graphics','audio'})
        print('SYNTHETIC_INCREMENTAL='+json.dumps({'cold_seconds':round(cold,3),
               'audio_repair_seconds':round(warm,3),'reused':second['reused_stages']}),flush=True)

    def test_corrupt_stage_cache_is_a_miss(self):
        project=m._read(self.pid)
        first=jobs.Attempt(self.pid,project,False)
        output=first.root/'stage.mp4'; output.write_bytes(b'valid fixture')
        first.stage('base',lambda:(output,{}),inputs={'fixture':1})
        output.write_bytes(b'corrupt cache')
        second=jobs.Attempt(self.pid,project,False)
        target=second.root/'stage.mp4';target.write_bytes(b'fresh fixture')
        with patch.object(second,'reuse',wraps=second.reuse):
            second.stage('base',lambda:(target,{}),inputs={'fixture':1})
        self.assertFalse(second.state['stages']['base']['reused'])

    def test_internal_cut_scan_detects_flash_inside_one_clip(self):
        result=scene_analysis.inspect(self.cuts,0,4,lambda *a:None)
        self.assertTrue(result['requires_review'])
        self.assertTrue(any(.1 < s['duration'] < .3 for s in result['short_shots']))

    def test_source_range_cache_reuses_analysis_across_projects_and_invalidates_corruption(self):
        first_dir=self.data/'range-a'
        first=commerce_inspection.range_evidence(self.pid,self.asset,0,3,first_dir,lambda *a:None)
        other=m.project_create('Same bytes other project')['project_id']
        other_asset=self.register(other,self.source)
        with patch.object(scene_analysis,'inspect',side_effect=AssertionError('must reuse')):
            result=commerce_inspection.range_evidence(other,other_asset,0,3,self.data/'range-b',lambda *a:None)
        self.assertTrue(result['cache_hit'])
        self.assertEqual(result['asset_id'],other_asset)
        Path(first['preview']['path']).write_bytes(b'corrupt evidence')
        result=commerce_inspection.range_evidence(other,other_asset,0,3,self.data/'range-c',lambda *a:None)
        self.assertFalse(result['cache_hit'])

    def test_silent_asr_cache_rebinds_source_without_inheriting_approval(self):
        asset=self.register(self.pid,self.cuts)
        request=speech.transcription_request(self.pid,asset,0,4)
        directory=self.data/'asr-a';directory.mkdir()
        first=speech.execute(self.pid,request,directory,lambda *a:None)
        self.assertEqual(first['speech_status'],'no_audio')
        other=m.project_create('Same silent bytes')['project_id']
        aid=self.register(other,self.cuts)
        request=speech.transcription_request(other,aid,0,4)
        directory=self.data/'asr-b';directory.mkdir()
        result=speech.execute(other,request,directory,lambda *a:None)
        self.assertTrue(result['cache_hit'])
        self.assertEqual(result['asset_id'],aid)
        self.assertFalse(result['human_listened'])

    def test_audible_asr_cache_reuses_inference_and_invalidates_model_change(self):
        from video_editer import processes
        model=self.data/'synthetic-model'; model.mkdir()
        for name in speech.MODEL_FILES: (model/name).write_bytes(b'synthetic-model-fixture')
        real_run=processes.run
        inferred=[]
        def fake_inference(command, **kwargs):
            if 'video_editer.speech_worker' not in command:
                return real_run(command, **kwargs)
            inferred.append(command)
            engine.write_json(Path(command[-1]),{'engine':'synthetic-test-only','device':'cpu',
                'segments':[{'text':'fixture sentence.', 'words':[
                    {'word':'fixture sentence.','start':.2,'end':1.2,'probability':.99}]}]})
        with patch.object(speech,'model_dir',return_value=model), \
                patch.object(speech,'runtime_status',return_value={'missing_dependencies':[],'model_files_present':True}), \
                patch.object(processes,'run',side_effect=fake_inference):
            request=speech.transcription_request(self.pid,self.asset,0,3)
            first_dir=self.data/'asr-positive-a'; first_dir.mkdir()
            first=speech.execute(self.pid,request,first_dir,lambda *a:None)
            self.assertEqual(first['speech_status'],'speech_detected')
            other=m.project_create('Second ASR project')['project_id']
            aid=self.register(other,self.source)
            request=speech.transcription_request(other,aid,0,3)
            second_dir=self.data/'asr-positive-b'; second_dir.mkdir()
            second=speech.execute(other,request,second_dir,lambda *a:None)
            self.assertTrue(second['cache_hit']); self.assertEqual(len(inferred),1)
            (model/speech.MODEL_FILES[0]).write_bytes(b'changed model fixture')
            third_dir=self.data/'asr-positive-c'; third_dir.mkdir()
            speech.execute(other,request,third_dir,lambda *a:None)
            self.assertEqual(len(inferred),2)

    def test_conversion_runs_via_existing_media_job_and_keeps_audio(self):
        job=m.media_convert_submit(self.pid,self.asset,0,2,384,30)
        media_jobs.execute(self.pid,job['job_id'])
        result=media_jobs.status(self.pid,job['job_id'])
        self.assertEqual(result['status'],'succeeded',result.get('error'))
        self.assertTrue(result['result']['probe']['has_audio'])
        self.assertTrue(result['result']['requires_new_source_analysis'])
        again=m.media_convert_submit(self.pid,self.asset,0,2,384,30)
        self.assertEqual(again['job_id'],job['job_id'])

    def test_receipt_does_not_turn_frames_asr_or_wrong_range_into_continuous_review(self):
        proof=receipt(self.data,self.source,'frames',4)
        rows,files=review_evidence.load([proof])
        sha=rows[0]['media']['sha256']
        self.assertFalse(review_evidence.covers(rows,'continuous_review',{sha},0,4))
        proof=receipt(self.data,self.source,'audiovisual',1)
        rows,_=review_evidence.load([proof])
        self.assertFalse(review_evidence.covers(rows,'listening',{sha},0,4))
        self.assertFalse(review_evidence.covers(rows,'continuous_review',{'other'},0,1))
        self.assertTrue(review_evidence.covers(rows,'continuous_review_and_listening',{sha},0,1))
