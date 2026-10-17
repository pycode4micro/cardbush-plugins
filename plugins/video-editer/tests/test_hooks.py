"""Account boundaries, paid submission recovery and lossless timeline ripple."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_editer import engine, hooks, media_ops, media_store, timing
from video_editer import mcp_server as s


class HookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='video-hook-test-')
        self.root = Path(self.tmp.name)
        self.patches = [patch.object(engine, 'DATA', self.root), patch.object(engine, 'PROJECTS', self.root/'projects'),
                        patch.object(engine, 'ffprobe', return_value={'duration':10.,'width':1080,'height':1920,'has_audio':True})]
        for p in self.patches: p.start()
        self.addCleanup(self.tmp.cleanup)
        for p in self.patches: self.addCleanup(p.stop)
        self.pid = s.project_create('synthetic hook contract fixture')['project_id']
        self.profile = {'display_name':'Account A', 'identity_description':'Synthetic fixture',
                        'identity_mode':'synthetic','voice_policy':'silent generated hook; original body audio', 'reference_assets':[]}
        s.hook_account_put('account-a', 0, self.profile)
        self.asset = self.add_asset('mat_source', b'source-fixture')
        project=s._read(self.pid);project['materials'][0]['probe']['has_audio']=True;s._write(self.pid,project)
        self.output = self.add_asset('mat_generated', b'output-fixture')
        self.plan = {'product_key':'fixture-product', 'product_description':'Blue white-collar fixture',
                     'opening_idea':'Explicit collar action', 'seam_description':'Hard cut to body',
                     'prompt':'Use the reference video for garment and motion.', 'keep_seconds':2.12,
                     'source_references':[{'asset_id':self.asset['id'],'start':1.,'end':5.,'purpose':'garment/action'}]}
        self.hook = s.hook_plan_create(self.pid, 'account-a', 1, self.plan)['hook_id']
        self.evidence = media_store.add(self.pid,'evidence',self.asset,{
            'operation':'reference','source_start':1.,'source_end':5.,
            'files':[{'path':str(self.root/'slice.mp4'),'sha256':'exported-bytes'}]})
        self.request = {'model':'2.5','duration':4,'content':[{'type':'text','text':self.plan['prompt']},
                        {'type':'video_url','role':'reference_video','video_url':{'url':'https://example.invalid/slice?secret=private'}}]}
        self.bindings = [{'content_index':1,'source':'slice','source_index':0,
                          'evidence_id':self.evidence['id'],'uploaded_sha256':'exported-bytes'}]

    def add_asset(self, aid, content):
        path = self.root/(aid+'.mp4'); path.write_bytes(content)
        digest, stat = media_ops.digest(path)
        asset={'id':aid,'kind':'video','name':path.name,'path':str(path),
               'probe':{'duration':10.,'width':1080,'height':1920,'has_audio':False},
               'identity':{'sha256':digest,'stat':stat}}
        project=s._read(self.pid);project['materials'].append(asset);s._write(self.pid,project)
        return asset

    def prepare(self):
        return s.hook_request_prepare(self.pid,self.hook,'seedance',self.request,self.bindings,'offline fixture preview; no real generation')

    def begin(self):
        prep=self.prepare()
        return s.hook_generation_begin(self.pid,self.hook,prep['request_sha256'],'synthetic test authorization')

    def succeed_and_review(self):
        attempt=self.begin()['attempt']
        s.hook_generation_record(self.pid,self.hook,attempt['attempt_id'],'succeeded','task-fixture',{'synthetic':True,'output_sha256':self.output['identity']['sha256']})
        return s.hook_output_review(self.pid,self.hook,self.output['id'],0,2.12,
                 {k:'pass' for k in ('person','garment','motion','seam','claims','audio')},
                 'Synthetic software fixture only; no real model result.', ['synthetic-frame-fixture'])

    def test_paid_claim_survives_unknown_and_never_permits_duplicate(self):
        first=self.begin();self.assertTrue(first['may_submit'])
        a=first['attempt']
        s.hook_generation_record(self.pid,self.hook,a['attempt_id'],'unknown',receipt={'error':'timeout'})
        repeated=s.hook_generation_begin(self.pid,self.hook,a['request_sha256'],'same authorization')
        self.assertFalse(repeated['may_submit'])
        s.hook_generation_record(self.pid,self.hook,a['attempt_id'],'queued','original-task',{'token':'SECRET','url':'https://example.invalid/private?sig=SECRET'})
        with self.assertRaisesRegex(ValueError,'replace the task'):
            s.hook_generation_record(self.pid,self.hook,a['attempt_id'],'running','different-task',{})
        saved=json.dumps(s.hook_get(self.pid,self.hook))
        self.assertNotIn('SECRET',saved);self.assertNotIn('sig=',saved);self.assertNotIn('secret=private',saved)
        s.hook_generation_record(self.pid,self.hook,a['attempt_id'],'failed','original-task',{})
        self.assertFalse(s.hook_generation_begin(self.pid,self.hook,a['request_sha256'],'still same')['may_submit'])

    def test_account_updates_invalidate_old_plan_without_touching_timeline(self):
        s.hook_account_put('account-a',1,{**self.profile,'display_name':'Updated A'})
        with self.assertRaisesRegex(ValueError,'profile changed'):self.prepare()
        self.assertEqual(s.hook_account_get('account-a',1)['profile']['display_name'],'Account A')
        with self.assertRaisesRegex(ValueError,'version conflict'):
            s.hook_account_put('account-a',1,self.profile)
        self.assertEqual(s.timeline_get(self.pid)['timeline']['revision'],0)

    def test_actual_video_reference_hash_range_and_prompt_are_required(self):
        original=copy.deepcopy(self.request)
        self.request['content']=self.request['content'][:1]
        with self.assertRaises(ValueError):self.prepare()
        self.request=copy.deepcopy(original);self.request['content'][0]['text']='different prompt'
        with self.assertRaisesRegex(ValueError,'prompt'):self.prepare()
        self.request=original;self.bindings[0]['uploaded_sha256']='wrong-bytes'
        with self.assertRaisesRegex(ValueError,'Uploaded bytes'):self.prepare()
        self.bindings[0]['uploaded_sha256']='exported-bytes'
        s.hook_request_prepare(self.pid,self.hook,'seedance',self.request,self.bindings,'fixture')
        Path(self.asset['path']).write_bytes(b'changed-source')
        with self.assertRaisesRegex(ValueError,'stat changed'):self.begin()

    def test_real_person_raw_video_blocked_and_other_accounts_face_rejected(self):
        person={**self.profile,'identity_mode':'authorized_person','reference_assets':[
            {'uri':'asset://person-a','kind':'image','status':'confirmed','verification_reference':'explicit user mapping'}]}
        s.hook_account_put('person-a',0,person)
        self.hook=s.hook_plan_create(self.pid,'person-a',1,self.plan)['hook_id']
        with self.assertRaisesRegex(ValueError,'Raw Seedance'):self.prepare()
        self.request['content'].append({'type':'image_url','role':'reference_image','image_url':{'url':'asset://person-b'}})
        self.bindings.append({'content_index':2,'source':'account'})
        self.bindings[0]['preprocessing']={'output_asset_id':self.output['id'],'source_evidence_id':self.evidence['id'],
             'coverage':'head','all_required_heads_covered':True,'garment_preserved':True,
             'review_evidence_refs':['frame fixture'],'job_receipt':'synthetic'}
        # Wrong identity cannot be rescued by a processed video.
        with self.assertRaises(ValueError):self.prepare()

    def test_ripple_preserves_audio_offsets_and_bound_animation_once(self):
        self.succeed_and_review()
        clip=s.clip_add(self.pid,self.asset['id'],0,4,transition='none')['clip']['id']
        bound=s.subtitle_add(self.pid,'bound',1,2)['subtitle']['id']
        s.event_bind(self.pid,bound,clip,1,2)
        s.subtitle_add(self.pid,'absolute',.5,2.5)
        project=s._read(self.pid);tl=project['mcp_timeline']
        tl['tracks']['audio']=[{'id':'aud','asset_id':self.asset['id'],'start':.2,'duration':2.,'source_start':3.,'volume':1.,'fade_in':0.,'fade_out':0.}]
        # Pure ripple also handles explicitly authored graphic keys and timed point cards.
        sample=copy.deepcopy(tl)
        sample['tracks']['overlays']=[{'id':'graphic','start':.5,'end':2.,'keyframes':[{'at':.5},{'at':1.9}]}]
        sample['callouts']=[{'id':'points','start':1.,'end':3.,'points':[{'text':'Point','start':1.1,'end':2.9}]}]
        moved,_,shift=hooks.prepend(sample,self.output['id'],0,2.12,self.hook)
        self.assertAlmostEqual(shift,2.12)
        self.assertAlmostEqual(moved['tracks']['overlays'][0]['keyframes'][1]['at'],4.02)
        self.assertAlmostEqual(moved['callouts'][0]['points'][0]['start'],3.22)
        self.assertAlmostEqual(timing.locate(moved,bound)[1]['start'],3.12)
        s._write(self.pid,project)
        rev=tl['revision'];before=copy.deepcopy(tl)
        result=s.hook_attach(self.pid,self.hook,rev,'silent')
        after=s.timeline_get(self.pid)['timeline']
        self.assertAlmostEqual(after['tracks']['audio'][0]['start'],2.32)
        self.assertEqual(after['tracks']['audio'][0]['source_start'],3.)
        self.assertEqual(after['tracks']['audio'][0]['duration'],2.)
        self.assertAlmostEqual(timing.locate(after,bound)[1]['start'],3.12)
        self.assertTrue(s.hook_attach(self.pid,self.hook,rev,'silent')['already_attached'])
        s.timeline_restore(self.pid,result['undo_snapshot'])
        restored=s.timeline_get(self.pid)['timeline']
        self.assertEqual(restored['tracks'],timing.resolve(before)['tracks'])

    def test_failed_review_stale_revision_locked_tracks_and_legacy_bed_do_not_mutate(self):
        self.succeed_and_review()
        s.clip_add(self.pid,self.asset['id'],0,4,transition='none')
        revision=s.timeline_get(self.pid)['timeline']['revision']
        with self.assertRaisesRegex(ValueError,'revision conflict'):
            s.hook_attach(self.pid,self.hook,revision-1,'silent')
        project=s._read(self.pid);project['mcp_timeline']['track_meta']['captions']['locked']=True;s._write(self.pid,project)
        before=(engine.project_dir(self.pid)/'project.json').read_bytes()
        with self.assertRaisesRegex(ValueError,'Unlock'):s.hook_attach(self.pid,self.hook,revision,'silent')
        self.assertEqual(before,(engine.project_dir(self.pid)/'project.json').read_bytes())
        project['mcp_timeline']['track_meta']['captions']['locked']=False
        project['mcp_timeline']['audio']={'bed_asset_id':self.asset['id'],'start_seconds':0};s._write(self.pid,project)
        with self.assertRaisesRegex(ValueError,'audio bed'):s.hook_attach(self.pid,self.hook,revision,'silent')
        s.hook_output_review(self.pid,self.hook,self.output['id'],0,2.12,
              {k:('fail' if k=='garment' else 'pass') for k in ('person','garment','motion','seam','claims','audio')},'Wrong collar',['fixture'])
        with self.assertRaisesRegex(ValueError,'accept the actual'):s.hook_attach(self.pid,self.hook,revision,'silent')

    def test_sped_hook_uses_output_duration_for_all_body_events(self):
        self.succeed_and_review()
        body=s.clip_add(self.pid,self.asset['id'],0,4,transition='none')['clip']['id']
        caption=s.subtitle_add(self.pid,'bound body',1,2)['subtitle']['id']
        s.event_bind(self.pid,caption,body,1,2)
        audio=s.audio_track_add(self.pid,self.asset['id'],start=.2,duration=2,source_start=3)['audio']
        revision=s.timeline_get(self.pid)['timeline']['revision']
        result=s.hook_attach(self.pid,self.hook,revision,'silent',playback_speed=1.25)
        current=s.timeline_get(self.pid)
        # 2.12 source seconds / 1.25, padded to the shared 25fps clock.
        self.assertAlmostEqual(result['shift_seconds'],1.72)
        self.assertEqual(current['timeline']['tracks']['main'][0]['playback_speed'],1.25)
        self.assertAlmostEqual(current['time_map']['duration'],5.72)
        self.assertAlmostEqual(timing.locate(current['timeline'],caption)[1]['start'],2.72)
        shifted=timing.locate(current['timeline'],audio['id'])[1]
        self.assertAlmostEqual(shifted['start'],1.92)
        self.assertEqual(shifted['source_start'],3)
        self.assertEqual(shifted['duration'],2)

    def test_audio_speed_consumes_source_seconds_without_mutating_on_rejection(self):
        s.clip_add(self.pid,self.asset['id'],0,10,transition='none')
        added=s.audio_track_add(self.pid,self.asset['id'],duration=6.4,source_start=2,playback_speed=1.25)['audio']
        self.assertEqual(added['duration'],6.4)
        self.assertEqual(added['playback_speed'],1.25)
        path=engine.project_dir(self.pid)/'project.json';before=path.read_bytes()
        with self.assertRaisesRegex(ValueError,'source bounds'):
            s.audio_track_add(self.pid,self.asset['id'],duration=6.5,source_start=2,playback_speed=1.25)
        self.assertEqual(before,path.read_bytes())
        for invalid in (0,True,2):
            with self.assertRaisesRegex(ValueError,'playback_speed'):
                s.audio_track_add(self.pid,self.asset['id'],duration=1,playback_speed=invalid)
        self.assertEqual(before,path.read_bytes())

    def test_preflight_checks_sped_audio_in_source_time(self):
        s.clip_add(self.pid,self.asset['id'],0,10,transition='none')
        s.audio_track_add(self.pid,self.asset['id'],duration=6.4,source_start=2,playback_speed=1.25)
        # These byte fixtures test timeline contracts, not decoded audio. Real
        # sound rendering and pitch/tail coverage are tested in delivery_audio.
        self.assertTrue(s.timeline_validate(self.pid,check_audio=False)['valid'])
        project=s._read(self.pid);project['mcp_timeline']['tracks']['audio'][0]['duration']=7
        s._write(self.pid,project)
        report=s.timeline_validate(self.pid,check_audio=False)
        self.assertFalse(report['valid'])
        self.assertTrue(any('source selection exceeds' in x for x in report['errors']))


if __name__=='__main__':unittest.main()
