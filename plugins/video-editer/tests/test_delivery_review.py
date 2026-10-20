"""Evidence must survive real revision changes, incomplete reviews and masking warnings."""
import copy
from pathlib import Path
import tempfile
import unittest
from review_fixtures import receipt
from unittest.mock import patch

from video_editer import delivery_review as review, engine, jobs, media_jobs, media_ops, resources
from video_editer import mcp_server as m

REAL_INSPECTION_FINDINGS = review.inspection_findings


class DeliveryReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patches = [patch.object(engine, 'PROJECTS', self.root/'projects'),
                        patch.object(resources, 'heavy_slot'),
                        patch.object(review, 'inspection_findings', return_value=([], []))]
        for p in self.patches: p.start()
        self.pid = m.project_create('review fixtures')['project_id']
        self.video = self.root/'output.mp4'; self.video.write_bytes(b'output bytes')
        self.evidence = self.root/'observations.json'; self.evidence.write_text('{"fixture":true}')
        self.checks = [{'id': name, 'status': 'pass', 'note': 'Fixture-specific observation', 'method': 'user_feedback',
                        'evidence_paths': [str(self.evidence)]} for name in review.CHECKS]
        self.job = self.make_job()

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.tmp.cleanup()

    def make_job(self, preview=False, warnings=None):
        proof = receipt(self.root, self.video)
        for check in self.checks:
            check['review_receipt_paths'] = [proof]
        attempt = jobs.Attempt(self.pid, m._read(self.pid), preview)
        attempt.succeed({'path': str(self.video), 'has_audio': True, 'warnings': warnings or []})
        return attempt.job_id

    def test_missing_checks_and_render_success_do_not_pass(self):
        self.assertFalse(review.get(self.pid, self.job)['delivery_ready'])
        r = review.record(self.pid, self.job, self.checks[:1])
        self.assertEqual(r['status'], 'needs_review')
        self.assertEqual(len(r['pending_checks']), len(review.CHECKS)-1)
        bad = copy.deepcopy(self.checks); bad[0]['evidence_paths'] = []
        with self.assertRaises(ValueError): review.record(self.pid, self.job, bad)

    def test_method_labels_without_actual_receipts_stay_unchecked(self):
        checks=copy.deepcopy(self.checks)
        for row in checks: row.pop('review_receipt_paths')
        result=review.record(self.pid,self.job,checks)
        self.assertFalse(result['delivery_ready'])
        self.assertTrue(all(c['status']=='not_checked' for c in result['checks']))

    def test_failure_and_repair_are_retained(self):
        bad = copy.deepcopy(self.checks); bad[3].update(status='fail', repair_action='Use a complete original sentence')
        r = review.record(self.pid, self.job, bad)
        self.assertEqual(r['status'], 'needs_repair')
        with self.assertRaises(ValueError): review.record(self.pid, self.job, self.checks)
        p=m._read(self.pid);p['mcp_timeline']['revision'] += 1;m._write(self.pid,p)
        with self.assertRaises(ValueError): review.record(self.pid, self.make_job(), self.checks)
        p=m._read(self.pid);p['mcp_timeline']['audio_config']={'gain_db':-2};m._write(self.pid,p)
        self.video.write_bytes(b'repaired output bytes')
        fixed = review.record(self.pid, self.make_job(), self.checks)
        self.assertTrue(fixed['delivery_ready'])
        self.assertEqual(fixed['previous_review_id'], r['id'])
        saved = engine.read_json(engine.project_dir(self.pid)/'delivery_reviews'/(r['id']+'.json'))
        self.assertEqual(saved['checks'][3]['status'], 'fail')

    def test_changes_make_review_stale_and_new_render_is_unreviewed(self):
        for change in ('evidence', 'output', 'revision'):
            with self.subTest(change=change):
                proof = receipt(self.root, self.video)
                for check in self.checks: check['review_receipt_paths'] = [proof]
                review.record(self.pid, self.job, self.checks)
                if change == 'evidence': self.evidence.write_text('{"changed":1}')
                elif change == 'output': self.video.write_bytes(b'changed output')
                else:
                    p=m._read(self.pid); p['mcp_timeline']['revision'] += 1; m._write(self.pid,p)
                self.assertEqual(review.get(self.pid,self.job)['status'], 'stale')
        new = self.make_job()
        self.assertEqual(review.get(self.pid,new)['status'], 'needs_review')

    def test_unversioned_timeline_change_still_invalidates_review(self):
        review.record(self.pid,self.job,self.checks)
        p=m._read(self.pid);p['mcp_timeline']['audio_config']={'gain_db':-9};m._write(self.pid,p)
        self.assertEqual(review.get(self.pid,self.job)['status'],'stale')

    def test_warning_needs_specific_resolution_preview_never_delivery(self):
        job = self.make_job(preview=True, warnings=[{'code':'audio_headroom'}])
        r=review.record(self.pid,job,self.checks)
        self.assertEqual(r['status'],'needs_review')
        r=review.record(self.pid,job,self.checks,warning_resolutions=[{
            'code':'render:audio_headroom','note':'Measured true peak has headroom',
            'evidence_paths':[str(self.evidence)]}])
        self.assertEqual(r['status'],'preview_reviewed'); self.assertFalse(r['delivery_ready'])

    def test_audio_identity_signal_and_listening_are_not_interchangeable(self):
        identity,_=review.artifact(self.pid,self.job)
        report={'project_id':self.pid,'render_job_id':self.job,'revision':identity['revision'],
                'delivered_identity':{'sha256':identity['output']['sha256'],'saved_input_sha256':identity['input_sha256']},
                'cues':[{'audio_id':'sfx1','delivery':{'signal_presence':'confirmed'},
                         'warnings':['possible_masking_by_nearby_audio']}]}
        audio_path=self.root/'audio';audio_path.mkdir();engine.write_json(audio_path/'result.json',report)
        with patch.object(media_jobs,'status',return_value={'status':'succeeded','operation':'audio_review','result':report}), patch.object(media_jobs,'folder',return_value=audio_path):
            checks=copy.deepcopy(self.checks);checks[-1]['status']='not_checked'
            r=review.record(self.pid,self.job,checks,['media_test'])
            self.assertEqual(r['status'],'needs_review')
            with self.assertRaises(ValueError):
                review.record(self.pid,self.job,checks,['media_test'],[{'code':r['technical_findings'][0]['code'],'note':'waive','evidence_paths':[str(self.evidence)]}])
            report['cues'][0]['delivery']['signal_presence']='missing'
            self.assertEqual(review.record(self.pid,self.job,self.checks,['media_test'])['status'],'needs_repair')
            report['delivered_identity']['sha256']='other file'
            with self.assertRaises(ValueError): review.record(self.pid,self.job,self.checks,['media_test'])

    def test_signal_or_asr_cannot_certify_listening(self):
        checks=copy.deepcopy(self.checks);checks[-1]['method']='measurement'
        with self.assertRaises(ValueError): review.record(self.pid,self.job,checks)

    def test_pending_candidate_cannot_be_waived_as_a_warning(self):
        from video_editer import commerce
        job = self.make_job(preview=True)
        state = {'selection': {'preview_only': True, 'pictures': ['pending_hook']},
                 'reviews': {'pending_hook': {'status': 'pending'}}}
        with patch.object(commerce, 'path', return_value=self.evidence), \
             patch.object(commerce, 'load', return_value=state), \
             patch.object(commerce, 'preflight', return_value=[]):
            result = review.record(self.pid, job, self.checks)
            self.assertEqual(result['status'], 'needs_review')
            self.assertIn('candidate_pending:pending_hook', result['pending_checks'])
            with self.assertRaises(ValueError):
                review.record(self.pid, job, self.checks, warning_resolutions=[{
                    'code': 'candidate_pending:pending_hook', 'note': 'Treat unknown as pass',
                    'evidence_paths': [str(self.evidence)]}])

    def test_pending_does_not_hide_failed_or_stale_delivery(self):
        from video_editer import commerce
        state={'selection': {'preview_only': True, 'pictures': ['pending_hook']},
               'reviews': {'pending_hook': {'status': 'pending'}}}
        with patch.object(commerce, 'load', return_value=state), patch.object(commerce, 'preflight', return_value=[]):
            for status in ('needs_repair', 'stale', 'needs_review'):
                with self.subTest(status=status), patch.object(review, 'get', return_value={'status':status,'delivery_ready':False}):
                    result=m.commerce_delivery_get(self.pid,self.job)
                    self.assertEqual(result['status'],status)
                    self.assertFalse(result['delivery_ready'])
                    self.assertEqual(result['next_tool'],'commerce_workflow_status')

    def test_timestamp_guard_rejects_first_frame_mislabeled_as_later(self):
        info={'streams':[{'codec_type':'video','avg_frame_rate':'25/1'}]}
        with self.assertRaises(ValueError): media_ops.validate_frame_timestamp(12,0,info)
        with self.assertRaises(ValueError): media_ops.validate_frame_timestamp(12,9.96,info)
        self.assertTrue(media_ops.validate_frame_timestamp(12.01,12.04,info)['verified'])
        info['streams'][0]['avg_frame_rate']='1/1'
        self.assertTrue(media_ops.validate_frame_timestamp(2.1,3,info)['verified'])

    def test_all_not_applicable_is_not_delivery_ready(self):
        checks=copy.deepcopy(self.checks)
        for row in checks: row['method']='not_applicable'
        with self.assertRaisesRegex(ValueError,'required'): review.record(self.pid,self.job,checks)

    def test_missing_inspection_and_arbitrary_json_cannot_be_waived(self):
        with patch.object(review,'inspection_findings',side_effect=REAL_INSPECTION_FINDINGS):
            r=review.record(self.pid,self.job,self.checks)
            self.assertFalse(r['delivery_ready']);self.assertIn('missing_render_inspection',r['pending_checks'])
            with self.assertRaises(ValueError):
                review.record(self.pid,self.job,self.checks,warning_resolutions=[{
                    'code':'missing_render_inspection','note':'An unrelated JSON is enough',
                    'evidence_paths':[str(self.evidence)]}])

    def inspection(self, **changes):
        identity,_=review.artifact(self.pid,self.job)
        request={'operation':'delivery_inspect','render_job_id':self.job,'artifact':identity}
        job=media_jobs.submit(self.pid,request,start=False)
        folder=media_jobs.folder(self.pid,job['job_id'])
        report={'artifact':identity,'files':[review.fingerprint(self.evidence)],
                'levels':{'sample_peak_db':-5},'source_ranges':[],'final_segments':[]}
        report.update(changes);engine.write_json(folder/'result.json',report)
        state=engine.read_json(folder/'state.json');state['status']='succeeded';engine.write_json(folder/'state.json',state)
        return job['job_id']

    def test_bound_inspection_rejects_other_output_and_changed_evidence(self):
        inspect=self.inspection()
        with patch.object(review,'inspection_findings',side_effect=REAL_INSPECTION_FINDINGS):
            self.assertTrue(review.record(self.pid,self.job,self.checks,inspection_job_id=inspect)['delivery_ready'])
            self.video.write_bytes(b'another output')
            with self.assertRaises(ValueError):review.record(self.pid,self.job,self.checks,inspection_job_id=inspect)

    def test_even_with_inspection_unrelated_json_cannot_certify_checks(self):
        inspect=self.inspection();unrelated=self.root/'unrelated.json';unrelated.write_text('{"pass":true}')
        checks=copy.deepcopy(self.checks)
        proof=receipt(self.root, unrelated)
        for row in checks:
            row['evidence_paths']=[str(unrelated)]
            row['review_receipt_paths']=[proof]
        with patch.object(review,'inspection_findings',side_effect=REAL_INSPECTION_FINDINGS):
            with self.assertRaisesRegex(ValueError,'must cite'):
                review.record(self.pid,self.job,checks,inspection_job_id=inspect)
        inspect=self.inspection();self.evidence.write_text('changed evidence')
        with patch.object(review,'inspection_findings',side_effect=REAL_INSPECTION_FINDINGS):
            with self.assertRaises(ValueError):review.record(self.pid,self.job,self.checks,inspection_job_id=inspect)

    def test_silent_source_or_final_segment_blocks_delivery(self):
        for report in [
            {'source_ranges':[{'role':'body','clip_id':'c1','audio_eligibility':'silent'}]},
            {'final_segments':[{'clip_id':'c1','levels':{'sample_peak_db':None}}]},
            {'levels':{'sample_peak_db':None}},
        ]:
            inspect=self.inspection(**report)
            with patch.object(review,'inspection_findings',side_effect=REAL_INSPECTION_FINDINGS):
                self.assertEqual(review.record(self.pid,self.job,self.checks,inspection_job_id=inspect)['status'],'needs_repair')

    def test_review_form_never_prefills_approval(self):
        inspect=self.inspection()
        with patch.object(review,'inspection_findings',side_effect=REAL_INSPECTION_FINDINGS):
            form=review.form(self.pid,self.job,inspect)
        self.assertEqual(len(form['checks']),len(review.CHECKS))
        self.assertTrue(all(c['status']=='not_checked' for c in form['checks']))
        self.assertEqual(form['next_tool'],'delivery_review_record')
        form['checks'][0]['status']='pass'
        with self.assertRaisesRegex(ValueError,'actual observation'):
            review.normalize_checks(form['checks'])

    def test_corrupt_inspection_can_be_rebuilt_without_reusing_bad_files(self):
        old=self.inspection()
        self.evidence.write_text('{"changed":true}')
        with patch.object(review,'inspection_findings',side_effect=REAL_INSPECTION_FINDINGS), patch.object(media_jobs,'start_worker',return_value={}):
            new=review.inspection_submit(self.pid,self.job)
        self.assertNotEqual(old,new['job_id'])

    def test_changed_unrelated_audio_does_not_repair_hook_picture(self):
        checks=copy.deepcopy(self.checks);checks[2].update(status='fail',repair_action='Choose a non-talking full-body range')
        review.record(self.pid,self.job,checks)
        p=m._read(self.pid);p['mcp_timeline']['revision']+=1;p['mcp_timeline']['audio_config']={'gain_db':-3};m._write(self.pid,p)
        self.video.write_bytes(b'changed audio output')
        with self.assertRaisesRegex(ValueError,'relevant subject'):
            review.record(self.pid,self.make_job(),self.checks)

    def signature_fixture(self):
        p=m._read(self.pid)
        p['mcp_timeline']['tracks']['main']=[
            {'id':'hook','asset_id':'display','start':0,'end':3,'role':'hook_picture','audio_mute':True},
            {'id':'body','asset_id':'lecture','start':10,'end':25,'role':'body','audio_gain_db':0}]
        p['mcp_timeline']['tracks']['audio']=[
            {'id':'voice','asset_id':'quote','start':0,'duration':3,'source_start':20,'volume':1}]
        return p

    def test_hook_picture_repair_ignores_same_clip_sound_and_labels(self):
        p=self.signature_fixture();before=review.subject_signatures(p)
        p['mcp_timeline']['tracks']['main'][0].update(audio_gain_db=-5,shot_type='now_full_body')
        self.assertEqual(before['hook_picture'],review.subject_signatures(p)['hook_picture'])
        p['mcp_timeline']['tracks']['main'][0]['start']=.1
        self.assertNotEqual(before['hook_picture'],review.subject_signatures(p)['hook_picture'])

    def test_hook_voice_repair_ignores_later_body_and_late_sound(self):
        p=self.signature_fixture();before=review.subject_signatures(p)
        p['mcp_timeline']['tracks']['main'][1].update(start=30,end=45,audio_gain_db=-4)
        p['mcp_timeline']['tracks']['audio'].append(
            {'id':'late','asset_id':'whoosh','start':15,'duration':.5,'volume':.2})
        self.assertEqual(before['hook_voice'],review.subject_signatures(p)['hook_voice'])
        p['mcp_timeline']['tracks']['audio'][0]['source_start']=28
        self.assertNotEqual(before['hook_voice'],review.subject_signatures(p)['hook_voice'])


if __name__ == '__main__': unittest.main()
