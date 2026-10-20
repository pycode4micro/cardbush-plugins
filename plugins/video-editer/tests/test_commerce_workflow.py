"""Isolated contracts; no customer media, encoding, worker or paid call is started."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_editer import commerce, engine, media_jobs, media_ops, resources, speech
from video_editer import mcp_server as m
from review_fixtures import receipt


class CommerceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.patches = [patch.object(engine, 'PROJECTS', self.root/'projects'),
                        patch.object(resources, 'heavy_slot'), patch.object(media_jobs, 'start_worker', return_value={}),
                        patch.object(engine, 'ffprobe', return_value={'duration':120,'width':1080,'height':1920,'has_audio':True})]
        for p in self.patches: p.start()
        self.pid = m.project_create('commerce fixture')['project_id']
        source = self.root/'source.mp4'; source.write_bytes(b'isolated fixture, no decoder invoked')
        self.asset = {'id': 'mat_source', 'kind': 'video', 'path': str(source),
            'identity': {'sha256': media_ops.digest(source)[0], 'stat': media_ops.stat_signature(source)},
            'probe': {'duration': 120, 'width': 1080, 'height': 1920, 'has_audio': True}}
        p=m._read(self.pid);p['materials'].append(self.asset);m._write(self.pid,p)
        m.commerce_workflow_start(self.pid, 'fixture account', 'fixture product')
        # Explicit synthetic transcript, not an ASR inference or customer evidence.
        request={'operation':'speech_transcribe','asset_id':self.asset['id'],
                 'source_sha256':self.asset['identity']['sha256'],'start':0,'end':120}
        self.transcript=media_jobs.submit(self.pid,request,start=False)['job_id']
        folder=media_jobs.folder(self.pid,self.transcript)
        segments=[{'text':'测试句。','words':[{'word':'测试句。','start':i+.1,'end':i+.9,'probability':.99}]} for i in range(120)]
        words,sentences,issues=speech.build_sentences(segments,0,120)
        engine.write_json(folder/'result.json',{'operation':'speech_transcribe','request':request,
            'asset_id':self.asset['id'],'source_sha256':self.asset['identity']['sha256'],
            'source_start':0,'source_end':120,'source_duration':120,'words':words,'sentences':sentences,
            'timestamp_errors':issues,'speech_status':'speech_detected'})
        state=engine.read_json(folder/'state.json');state.update(status='succeeded',result_sha256=media_ops.digest(folder/'result.json')[0]);engine.write_json(folder/'state.json',state)

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.tmp.cleanup()

    def candidate(self, role, start, end, audio='audible_signal', approve=True):
        ids=['sentence_%04d'%i for i in range(int(start),int(end))]
        job=m.commerce_candidate_submit(self.pid,self.asset['id'],start,end,role,self.transcript,ids if role!='hook_picture' else None)
        folder=media_jobs.folder(self.pid,job['job_id']); req=engine.read_json(folder/'request.json')
        preview=folder/'preview.mp4';preview.write_bytes(b'fixture preview')
        frame=folder/'frame.jpg';frame.write_bytes(b'fixture frame')
        file=lambda p:{'path':str(p),'sha256':media_ops.digest(p)[0]}
        result={'operation':'commerce_inspect','project_id':self.pid,'request':req,'role':role,
                'asset_id':self.asset['id'],'source_sha256':self.asset['identity']['sha256'],
                'source_start':start,'source_end':end,'audio_eligibility':audio,
                'preview':{'path':str(preview),'files':[file(preview)]},'frames':{'files':[file(frame)]}}
        engine.write_json(folder/'result.json',result)
        state=engine.read_json(folder/'state.json');state['status']='succeeded';engine.write_json(folder/'state.json',state)
        if approve:
            proof = receipt(folder, preview, 'audiovisual', end-start)
            m.commerce_candidate_review(self.pid,job['job_id'],{k:True for k in commerce.FACTS[role]},commerce.METHODS[role],'Synthetic observed fixture facts',[proof])
        return job['job_id']

    def plan(self):
        picture=self.candidate('hook_picture',0,3)
        voice=self.candidate('hook_voice',4,7)
        body=[self.candidate('body',a,b) for a,b in [(10,20),(20,30),(30,41),(41,52)]]
        return picture,voice,body

    def apply(self):
        h,v,b=self.plan()
        return m.commerce_timeline_apply(self.pid,h,v,b,expected_revision=0)

    def test_start_resume_scope_and_no_worker(self):
        resumed=m.commerce_workflow_status(self.pid)
        self.assertEqual(resumed['stage'],'candidates')
        self.assertEqual(resumed['account'],'fixture account')
        self.assertEqual(len(resumed['reference_files']),3)
        first=commerce.load(self.pid)['state_revision']
        m.commerce_workflow_start(self.pid,'fixture account','fixture product')
        self.assertEqual(first,commerce.load(self.pid)['state_revision'])
        with self.assertRaises(ValueError): m.commerce_workflow_start(self.pid,'another','fixture product')

    def test_audio_image_misdirection_is_rejected_before_queue_submission(self):
        with patch.object(media_jobs, 'submit') as submit:
            for suffix in ('.wav', '.MP3', '.png'):
                source = self.root / ('non-video' + suffix)
                source.write_bytes(b'fixture')
                with self.assertRaisesRegex(ValueError, 'use media_import'):
                    m.media_register(self.pid, str(source))
            submit.assert_not_called()

    def test_candidate_repeat_reuses_job_and_queue(self):
        a=m.commerce_candidate_submit(self.pid,'mat_source',0,3,'hook_picture')
        b=m.commerce_candidate_submit(self.pid,'mat_source',0,3,'hook_picture')
        self.assertEqual(a['job_id'],b['job_id']);self.assertTrue(b['reused'])

    def test_method_label_without_received_evidence_stays_pending(self):
        job=self.candidate('hook_picture',0,3,approve=False)
        result=m.commerce_candidate_review(self.pid,job,{k:True for k in commerce.FACTS['hook_picture']},
                                           'continuous_review','Claim without actual review record')
        self.assertEqual(result['candidate']['status'],'pending')
        self.assertIn('actual_review_evidence',result['candidate']['pending_facts'])

    def test_batch_validates_all_before_submitting_and_wait_is_compact(self):
        with patch.object(media_jobs,'submit') as submit:
            with self.assertRaises(ValueError):
                m.commerce_candidates_submit(self.pid,[
                    {'asset_id':'mat_source','start':0,'end':3,'role':'hook_picture'},
                    {'asset_id':'mat_source','start':10,'end':11,'role':'hook_picture'}])
            submit.assert_not_called()
        with patch.object(media_jobs,'start',return_value={}) as worker:
            result=m.commerce_candidates_submit(self.pid,[
                {'asset_id':'mat_source','start':0,'end':3,'role':'hook_picture'},
                {'asset_id':'mat_source','start':10,'end':13,'role':'hook_picture'}])
            self.assertEqual(worker.call_count,1)
        report=m.media_jobs_wait(**result['next_arguments'],timeout_seconds=0)
        self.assertFalse(report['complete'])
        self.assertEqual(len(report['jobs']),2)

    def test_missing_custom_typography_is_explicitly_unchecked(self):
        self.apply()
        timeline=m._read(self.pid)['mcp_timeline']
        timeline['tracks']['overlays']=[{'id':'custom','start':0,'end':3}]
        findings=commerce.presentation_findings(timeline,{'hook_text_ids':['custom'],'body_text_ids':[]})
        self.assertEqual(findings[0]['status'],'not_checked')

    def test_batch_asr_dispatches_once_and_pool_excludes_unused_imports(self):
        project=m._read(self.pid)
        project['materials'].append({**copy.deepcopy(self.asset),'id':'mat_unused'})
        m._write(self.pid,project)
        with patch.object(speech,'runtime_status',return_value={'missing_dependencies':[],'model_files_present':True}), \
                patch.object(media_jobs,'start',return_value={}) as worker:
            result=m.commerce_materials_prepare(self.pid,['mat_source'])
            self.assertEqual(worker.call_count,1)
        self.assertEqual(commerce.load(self.pid)['analysis_asset_ids'],['mat_source'])
        self.assertNotIn('mat_unused',str(result))
        self.assertIsNone(speech.workflow_progress(self.pid)['missing'])

    def test_conversion_deduplication_and_failure_retry_target(self):
        a=m.media_convert_submit(self.pid,'mat_source',0,3,640,25)
        b=m.media_convert_submit(self.pid,'mat_source',0,3,640,25)
        self.assertEqual(a['job_id'],b['job_id'])
        folder=media_jobs.folder(self.pid,a['job_id'])
        state=engine.read_json(folder/'state.json'); state['status']='failed'; engine.write_json(folder/'state.json',state)
        self.assertEqual(m.media_convert_submit(self.pid,'mat_source',0,3,640,25)['next_tool'],'media_job_retry')

    def test_deferred_candidate_is_preview_only_and_not_accepted(self):
        picture, voice, body = self.plan()
        facts={k:None for k in commerce.FACTS['hook_voice']}
        m.commerce_candidate_defer(self.pid,voice,facts,'Audio input unavailable; ASR is not listening')
        with self.assertRaisesRegex(ValueError,'accepted'):
            m.commerce_timeline_apply(self.pid,picture,voice,body,expected_revision=0)
        result=m.commerce_timeline_apply(self.pid,picture,voice,body,expected_revision=0,preview_only=True)
        self.assertEqual(result['stage'],'presentation')
        self.assertTrue(result['preview_only'])
        self.assertEqual(commerce.load(self.pid)['reviews'][voice]['status'],'pending')
        with patch.object(commerce,'validate_presentation',return_value=[]):
            state=commerce.load(self.pid);state['presentation']={'fixture':True};commerce.save(self.pid,state)
            commerce.render_guard(self.pid,m._read(self.pid),preview=True)
            commerce.render_guard(self.pid,m._read(self.pid),preview=False)
            self.assertEqual(commerce.load(self.pid)['reviews'][voice]['status'], 'pending')

    def test_deferral_cannot_mask_known_defect_or_silent_body(self):
        job=self.candidate('body',10,20,audio='silent',approve=False)
        with self.assertRaisesRegex(ValueError,'Silent'):
            m.commerce_candidate_defer(self.pid,job,{k:None for k in commerce.FACTS['body']},'No listening')
        job=self.candidate('hook_picture',0,3,approve=False)
        facts={k:None for k in commerce.FACTS['hook_picture']};facts['full_body_visible']=False
        with self.assertRaisesRegex(ValueError,'Known defects'):
            m.commerce_candidate_defer(self.pid,job,facts,'Cropped feet')

    def test_rejected_candidate_cannot_be_deferred(self):
        job=self.candidate('hook_picture',0,3,approve=False)
        facts={k:True for k in commerce.FACTS['hook_picture']};facts['no_continuous_speaking']=False
        m.commerce_candidate_review(self.pid,job,facts,'continuous_review','Sustained talking')
        with self.assertRaisesRegex(ValueError,'rejected'):
            m.commerce_candidate_defer(self.pid,job,{k:None for k in facts},'Cannot review')

    def test_corrupt_candidate_evidence_can_be_reinspected(self):
        old=self.candidate('hook_picture',0,3)
        (media_jobs.folder(self.pid,old)/'preview.mp4').write_bytes(b'changed fixture')
        new=m.commerce_candidate_submit(self.pid,'mat_source',0,3,'hook_picture')
        self.assertNotEqual(old,new['job_id'])

    def test_talking_or_cropped_hook_cannot_be_accepted(self):
        job=self.candidate('hook_picture',0,3,approve=False)
        facts={k:True for k in commerce.FACTS['hook_picture']};facts['no_continuous_speaking']=False
        r=m.commerce_candidate_review(self.pid,job,facts,'continuous_review','Mouth continuously speaks during all 3 seconds')
        self.assertEqual(r['candidate']['status'],'rejected')
        facts['no_continuous_speaking']=True
        with self.assertRaises(ValueError): m.commerce_candidate_review(self.pid,job,facts,'continuous_review','Relabel attempt')

    def test_unknown_facts_and_wrong_review_method_rejected(self):
        job=self.candidate('hook_picture',0,3,approve=False)
        with self.assertRaises(ValueError): m.commerce_candidate_review(self.pid,job,{},'continuous_review','missing')
        with self.assertRaises(ValueError): m.commerce_candidate_review(self.pid,job,{k:True for k in commerce.FACTS['hook_picture']},'listening','not picture evidence')

    def test_silent_body_rejected_silent_hook_allowed(self):
        body=self.candidate('body',10,20,audio='silent')
        hook=self.candidate('hook_picture',0,3,audio='silent')
        rows=commerce.load(self.pid)['reviews']
        self.assertEqual(rows[body]['status'],'rejected');self.assertEqual(rows[hook]['status'],'accepted')

    def test_atomic_plan_original_speed_and_voice_routes(self):
        r=self.apply();p=m._read(self.pid);t=p['mcp_timeline']
        self.assertEqual(r['stage'],'presentation')
        self.assertTrue(t['tracks']['main'][0]['audio_mute'])
        self.assertTrue(all(not c['audio_mute'] for c in t['tracks']['main'][1:]))
        self.assertEqual({c['playback_speed'] for c in t['tracks']['main']},{1.0})
        self.assertEqual(t['tracks']['audio'][0]['source_start'],4)
        self.assertEqual(sum(c['transition']!='none' for c in t['tracks']['main']),1)

    def test_stale_revision_does_not_replace_timeline(self):
        h,v,b=self.plan();before=m._read(self.pid)
        with self.assertRaises(ValueError):m.commerce_timeline_apply(self.pid,h,v,b,expected_revision=7)
        self.assertEqual(before,m._read(self.pid))

    def test_changed_evidence_blocks_plan(self):
        h,v,b=self.plan();folder=media_jobs.folder(self.pid,h);(folder/'frame.jpg').write_bytes(b'changed')
        with self.assertRaises(ValueError):m.commerce_timeline_apply(self.pid,h,v,b,expected_revision=0)

    def test_raw_render_cannot_skip_workflow_stages(self):
        with patch('video_editer.render_queue.submit') as queue:
            with self.assertRaises(ValueError):m.render_submit(self.pid)
            queue.assert_not_called()
        self.apply()
        with self.assertRaises(ValueError):m.render_submit(self.pid)

    def test_typography_transparency_center_and_caption_exclusion(self):
        from PIL import Image
        self.apply();p=m._read(self.pid)
        p['mcp_timeline']['tracks']['captions']=[{'id':'subtitle_original','start':0,'end':3,'text':'fixture'}]
        m._write(self.pid,p)
        r=m.commerce_typography_add(self.pid,'穿上就好看','好看','','hook_center',.2,2.8,[.19,.60,.81,.71],['#FFF4DA','#FFAE80'],[])
        overlay=r['overlay'];self.assertAlmostEqual(overlay['x']+overlay['width']/2,.5)
        with Image.open(r['asset']['path']) as im:self.assertEqual(im.getchannel('A').getpixel((0,0)),0)
        captions=m._read(self.pid)['mcp_timeline']['tracks']['captions']
        self.assertEqual([(c['start'],c['end']) for c in captions],[(0,.2),(2.8,3)])

    def test_text_rejects_reserved_boxes_left_hook_and_theme_drift(self):
        self.apply()
        args=(self.pid,'好看','好看','','hook_center',.2,2.8,[.19,.60,.81,.71],['#FFF4DA','#FFAE80'])
        with self.assertRaises(ValueError):m.commerce_typography_add(*args,[[.2,.62,.8,.70]])
        with self.assertRaises(ValueError):m.commerce_typography_add(self.pid,'好看','','','hook_center',.2,2.8,[.1,.60,.7,.71],['#FFFFFF'],[])
        m.commerce_typography_add(*args,[])
        with self.assertRaises(ValueError):m.commerce_typography_add(self.pid,'细节','','','keyword_stack',4,6,[.1,.1,.6,.25],['#00FF00'],[])

    def test_typography_support_stays_close_to_actual_glyphs(self):
        self.apply()
        r=m.commerce_typography_add(self.pid,'细腻丝滑','丝滑','精纺小开衫','detail_underline',4,6,[.05,.3,.36,.55],['#FFF4DA','#FFAE80'],[])
        layout=r['overlay']['commerce_text']['layout']
        self.assertLess(layout['support_ink_top']-layout['main_ink_bottom'], layout['main_font_px']*.35)
        self.assertLessEqual(layout['block_height_px']+layout['padding_px']*2, 480)

    def test_tiny_effect_label_rejected_without_mutating_project(self):
        self.apply();before=m._read(self.pid)
        with self.assertRaisesRegex(ValueError,'Readable lettering'):
            m.commerce_typography_add(self.pid,'肩线往内收','往内收','溜肩也友好','detail_underline',4,6,
                                     [.02,.315,.30,.408],['#FFF7E8','#E86A33'],[])
        self.assertEqual(before,m._read(self.pid))

    def test_two_line_keyword_and_later_shrink_are_checked_at_presentation(self):
        from video_editer import canvas, commerce_typography
        self.apply()
        item=m.commerce_typography_add(self.pid,'肩线\n往内收','往内收','','keyword_stack',4,6,
                                      [.02,.3,.30,.48],['#FFF7E8','#E86A33'],[])['overlay']
        self.assertEqual(item['commerce_text']['layout']['line_count'],2)
        settings=canvas.settings(m._read(self.pid)['mcp_timeline']['canvas'])
        self.assertFalse(commerce_typography.readability_errors(item,settings))
        item['width']*=.5;item['height']*=.5
        self.assertTrue(commerce_typography.readability_errors(item,settings))

    def test_presentation_and_render_submission_resume_without_encoding(self):
        self.apply();palette=['#FFF4DA','#FFAE80']
        hook=m.commerce_typography_add(self.pid,'好看','好看','','hook_center',.2,2.8,[.19,.60,.81,.71],palette,[])['overlay']['id']
        body=m.commerce_typography_add(self.pid,'细节','细节','看得清','detail_underline',4,6,[.1,.1,.65,.24],palette,[])['overlay']['id']
        cue=m.audio_track_add(self.pid,'mat_source',start=.4,duration=.2,role='sfx')['audio']['id']
        result=m.commerce_presentation_record(self.pid,palette,[hook],[body],[cue])
        self.assertEqual(result['stage'],'render')
        with patch('video_editer.render_queue.start_worker',return_value={}):
            job=m.commerce_render_submit(self.pid)
        self.assertEqual(job['status'],'queued')
        self.assertFalse(job['preview'])
        status=m.commerce_workflow_status(self.pid)
        self.assertEqual(status['next_tool'],'render_job_status');self.assertEqual(status['render_job_id'],job['job_id'])
        from video_editer import jobs
        path=jobs.folder(self.pid,job['job_id'])/'state.json'
        state=engine.read_json(path);state['status']='succeeded';engine.write_json(path,state)
        with patch('video_editer.delivery_review.get',return_value={
            'status':'needs_repair','failed_checks':['hook_picture'],
            'checks':[{'id':'hook_picture','status':'fail','note':'Sustained talking','repair_action':'Replace with non-speaking full display'}]}):
            status=m.commerce_workflow_status(self.pid)
        self.assertEqual(status['next_tool'],'commerce_candidate_submit')
        self.assertEqual(status['repair_actions'][0]['repair_action'],'Replace with non-speaking full display')

    def test_generated_hook_route_is_blocked_for_source_only_project(self):
        with self.assertRaisesRegex(ValueError,'source-only'):
            m.hook_plan_create(self.pid,'irrelevant',1,{})
        with self.assertRaisesRegex(ValueError,'Pure source'):
            m.hook_attach(self.pid,'irrelevant',0,'silent')

    def test_delivery_inspection_never_increments_edit_revision(self):
        from video_editer import commerce_inspection, delivery_review, jobs
        output=self.root/'fake-output.mp4';output.write_bytes(b'fixture output')
        attempt=jobs.Attempt(self.pid,m._read(self.pid),True)
        attempt.succeed({'path':str(output),'has_audio':False})
        identity,result=delivery_review.artifact(self.pid,attempt.job_id)
        directory=self.root/'inspection';directory.mkdir()
        frame=directory/'frame.jpg';frame.write_bytes(b'fixture frame')
        frame_result={'files':[{'path':str(frame),'sha256':media_ops.digest(frame)[0]}]}
        before=m._read(self.pid)
        with patch.object(media_ops,'metadata',return_value={'duration':3,'has_audio':False}), patch.object(media_ops,'prepare',return_value=frame_result) as prepare:
            report=commerce_inspection.execute(self.pid,{'operation':'delivery_inspect','render_job_id':attempt.job_id,
                'artifact':identity,'inspection_asset_id':'mat_inspection'},directory,lambda *args:None)
        self.assertEqual(before,m._read(self.pid))
        self.assertEqual(report['artifact'],identity)
        self.assertIn('evidence_asset',prepare.call_args.kwargs)

    def test_failed_candidate_can_be_skipped_without_retry_loop(self):
        job=m.commerce_candidate_submit(self.pid,'mat_source',0,3,'hook_picture')
        folder=media_jobs.folder(self.pid,job['job_id']);s=engine.read_json(folder/'state.json');s['status']='failed';engine.write_json(folder/'state.json',s)
        status=m.commerce_workflow_status(self.pid);self.assertEqual(status['alternative_tool'],'commerce_candidate_skip')
        r=m.commerce_candidate_skip(self.pid,job['job_id'],'Source unavailable; use another authorized candidate')
        self.assertEqual(r['stage'],'candidates');self.assertEqual(r['accepted_counts']['hook_picture'],0)

    def test_own_display_audio_is_not_an_independent_voice(self):
        h,v,b=self.plan();same_voice=self.candidate('hook_voice',0,3)
        with self.assertRaisesRegex(ValueError,'another source range'):
            m.commerce_timeline_apply(self.pid,h,same_voice,b,expected_revision=0)

    def test_duplicate_body_windows_are_not_extra_material(self):
        h,v,b=self.plan();overlap=self.candidate('body',11,19)
        with self.assertRaisesRegex(ValueError,'overlap'):
            m.commerce_timeline_apply(self.pid,h,v,b+[overlap],expected_revision=0)


if __name__=='__main__':unittest.main()
