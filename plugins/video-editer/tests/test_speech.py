"""ASR gate contracts. Synthetic transcript fixtures; no editing or model inference."""
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_editer import engine, media_jobs, media_ops, resources, speech
from video_editer import mcp_server as m
from video_editer.speech_worker import choose_device, load_pcm


class SpeechTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.patches=[patch.object(engine,'PROJECTS',self.root/'projects'),
                      patch.object(resources,'heavy_slot'),patch.object(media_jobs,'start_worker',return_value={}),
                      patch.object(speech,'runtime_status',return_value={'model_files_present':True,'missing_dependencies':[]})]
        for item in self.patches:item.start()
        self.pid=m.project_create('ASR gate fixture')['project_id']
        source=self.root/'source.mp4';source.write_bytes(b'not audiovisual media: synthetic fixture')
        self.asset={'id':'mat_fixture','path':str(source),'kind':'video','probe':{'duration':12,'has_audio':True},
                    'identity':{'sha256':media_ops.digest(source)[0],'stat':media_ops.stat_signature(source)}}
        project=m._read(self.pid);project['materials'].append(self.asset);m._write(self.pid,project)
        m.commerce_workflow_start(self.pid,'fixture account','fixture garment')

    def tearDown(self):
        for item in reversed(self.patches):item.stop()
        self.tmp.cleanup()

    def finished(self, segments=None, status='speech_detected', start=0,end=12):
        job=speech.submit(self.pid,self.asset['id'],start,end)
        if segments is None:
            segments=[{'text':'一件穿三季。','words':[
                {'word':'一件','start':1,'end':1.5,'probability':.95},
                {'word':'穿三季。','start':1.5,'end':3,'probability':.94}]},
                {'text':'里面搭吊带。','words':[{'word':'里面搭吊带。','start':3.5,'end':6,'probability':.95}]},
                {'text':'外面搭大衣。','words':[{'word':'外面搭大衣。','start':7,'end':9,'probability':.91}]}]
        words,sentences,errors=speech.build_sentences(segments,start,end)
        folder=media_jobs.folder(self.pid,job['job_id']);request=engine.read_json(folder/'request.json')
        value={'request':request,'asset_id':self.asset['id'],'source_sha256':self.asset['identity']['sha256'],
               'source_start':start,'source_end':end,'source_duration':12,'words':words,'sentences':sentences,
               'timestamp_errors':errors,'speech_status':status}
        engine.write_json(folder/'result.json',value)
        state=engine.read_json(folder/'state.json');state.update(status='succeeded',result_sha256=hashlib.sha256((folder/'result.json').read_bytes()).hexdigest());engine.write_json(folder/'state.json',state)
        return job['job_id']

    def test_no_asr_blocks_all_candidate_roles_and_points_to_step(self):
        state=m.commerce_workflow_status(self.pid)
        self.assertEqual(state['next_tool'],'speech_transcribe_submit')
        self.assertEqual(state['next_arguments']['asset_id'],self.asset['id'])
        for role in ('hook_picture','hook_voice','body'):
            with self.assertRaises(ValueError):m.commerce_candidate_submit(self.pid,self.asset['id'],0,3,role)
        self.assertEqual(media_jobs.records(self.pid),[])

    def test_queued_work_reused_resume_and_retry_never_runs_inline(self):
        a=speech.submit(self.pid,self.asset['id']);b=speech.submit(self.pid,self.asset['id'])
        self.assertEqual(a['job_id'],b['job_id']);self.assertEqual(len(media_jobs.records(self.pid)),1)
        self.assertEqual(m.commerce_workflow_status(self.pid)['next_tool'],'media_job_status')
        folder=media_jobs.folder(self.pid,a['job_id']);state=engine.read_json(folder/'state.json');state['status']='failed';engine.write_json(folder/'state.json',state)
        self.assertEqual(speech.submit(self.pid,self.asset['id'])['next_tool'],'media_job_retry')
        self.assertEqual(m.commerce_workflow_status(self.pid)['next_tool'],'media_job_retry')
        retry=m.media_job_retry(self.pid,a['job_id'])
        self.assertNotEqual(retry['job_id'],a['job_id']);self.assertEqual(retry['retry_of'],a['job_id'])
        self.assertEqual(retry['status'],'queued')

    def test_candidate_context_and_consecutive_sentence_selection(self):
        job=self.finished();c=speech.candidates_get(self.pid,job)
        self.assertEqual(c['items'][1]['context_before'],'一件穿三季。')
        self.assertEqual(c['items'][1]['context_after'],'外面搭大衣。')
        v=speech.range_validate(self.pid,job,['sentence_0000','sentence_0001'])
        self.assertTrue(v['timestamp_valid']);self.assertEqual(v['text'],'一件穿三季。里面搭吊带。')
        self.assertLess(v['start'],1);self.assertGreater(v['end'],6)
        for ids in (['sentence_0000','sentence_0002'],['sentence_0001','sentence_0000'],['sentence_9999']):
            with self.assertRaises(ValueError):speech.range_validate(self.pid,job,ids)

    def test_word_cut_and_neighbor_intrusion_blocked(self):
        job=self.finished()
        for a,b in ((1.2,3),(1,2.9),(1,4),(-.1,3)):
            with self.assertRaises(ValueError):speech.range_validate(self.pid,job,['sentence_0000'],a,b)

    def test_sentence_submit_binds_evidence_without_editing_timeline(self):
        job=self.finished();before=m._read(self.pid)['mcp_timeline']
        candidate=m.commerce_speech_candidate_submit(self.pid,job,['sentence_0000'],'hook_voice')
        request=engine.read_json(media_jobs.folder(self.pid,candidate['job_id'])/'request.json')
        self.assertEqual(request['speech_binding']['sentence_ids'],['sentence_0000'])
        self.assertEqual(request['start'],.96)
        self.assertEqual(before,m._read(self.pid)['mcp_timeline'])

    def test_corrupt_transcript_and_cross_source_evidence_blocked(self):
        job=self.finished();binding=speech.range_validate(self.pid,job,['sentence_0000'])
        with self.assertRaises(ValueError):speech.verify_binding(self.pid,binding,'mat_other',binding['start'],binding['end'],'body')
        path=media_jobs.folder(self.pid,job)/'result.json';value=engine.read_json(path);value['words'][0]['word']='改写的文案';engine.write_json(path,value)
        with self.assertRaisesRegex(ValueError,'changed'):speech.range_validate(self.pid,job,['sentence_0000'])

    def test_no_speech_only_qualifies_display_provenance(self):
        job=self.finished([],status='no_speech_detected')
        self.assertEqual(speech.candidates_get(self.pid,job)['items'],[])
        with self.assertRaisesRegex(ValueError,'No recognized'):speech.range_validate(self.pid,job,['sentence_0000'])
        self.assertTrue(speech.display_binding(self.pid,self.asset['id'],0,3,job)['display_only'])
        self.assertEqual(m.commerce_workflow_status(self.pid)['stage'],'materials_insufficient')
        self.assertIsNone(m.commerce_workflow_status(self.pid)['next_tool'])
        self.assertEqual(m.commerce_candidate_submit(self.pid,self.asset['id'],0,3,'hook_picture')['status'],'queued')

    def test_invalid_word_times_rejected_and_low_confidence_never_approved(self):
        for start,end,p in ((float('nan'),2,.9),(3,2,.9),(-1,2,.9),(1,15,.9),(1,2,float('inf'))):
            _,_,errors=speech.build_sentences([{'text':'测试','words':[{'word':'测试','start':start,'end':end,'probability':p}]}],0,12)
            self.assertTrue(errors)
        _,rows,_=speech.build_sentences([{'text':'不确定。','words':[{'word':'不确定。','start':1,'end':2,'probability':.2}]}],0,12)
        self.assertIn('low_word_confidence',rows[0]['warnings']);self.assertEqual(rows[0]['meaning_status'],'unreviewed')

    def test_source_change_and_missing_binding_cannot_be_deferred_away(self):
        job=self.finished();binding=speech.display_binding(self.pid,self.asset['id'],0,3,job)
        with self.assertRaisesRegex(ValueError,'Missing ASR'):speech.verify_binding(self.pid,None,self.asset['id'],0,3,'hook_picture')
        Path(self.asset['path']).write_bytes(b'changed customer source')
        with self.assertRaises(ValueError):speech.verify_binding(self.pid,binding,self.asset['id'],0,3,'hook_picture')

    def test_gpu_first_and_forbidden_cpu_fallback(self):
        policy={'allow_cpu_fallback':True,'encoder':'auto'}
        self.assertEqual(choose_device('auto',policy,1)[:2],('cuda','float16'))
        self.assertEqual(choose_device('auto',policy,0)[:2],('cpu','int8'))
        with self.assertRaises(ValueError):choose_device('auto',{**policy,'allow_cpu_fallback':False},0)
        with self.assertRaises(ValueError):choose_device('cuda',policy,0)
        with self.assertRaises(ValueError):choose_device('auto',{**policy,'encoder':'gpu_required'},0)

    def test_full_window_coverage_required_on_resume(self):
        self.finished(start=0,end=9)
        state=m.commerce_workflow_status(self.pid)
        self.assertEqual(state['next_arguments']['start'],9)
        self.assertEqual(state['next_arguments']['end'],12)

    def test_pcm_input_avoids_pyav_redecode_and_rejects_wrong_clock(self):
        import wave
        for rate in (16000,8000):
            audio=self.root/('pcm_%d.wav'%rate)
            with wave.open(str(audio),'wb') as stream:
                stream.setparams((1,2,rate,0,'NONE','not compressed'));stream.writeframes(b'\x00\x40'*rate)
            if rate==16000:
                samples=load_pcm(audio);self.assertEqual(len(samples),16000);self.assertAlmostEqual(float(samples[0]),.5)
            else:
                with self.assertRaisesRegex(ValueError,'16kHz'):load_pcm(audio)

    def test_english_words_preserve_spaces_and_low_confidence_points_to_word(self):
        job=self.finished([{'text':'A soft coat.','words':[
            {'word':'A','start':1,'end':1.2,'probability':.95},
            {'word':' soft','start':1.2,'end':1.6,'probability':.2},
            {'word':' coat.','start':1.6,'end':2,'probability':.95}]}])
        row=speech.candidates_get(self.pid,job)['items'][0]
        self.assertEqual(row['text'],'A soft coat.')
        self.assertEqual(row['low_confidence_words'][0]['word'],' soft')

    def test_mcp_polling_and_reuse_return_compact_receipt_not_all_words(self):
        job=self.finished()
        for value in (m.media_job_status(self.pid,job),speech.submit(self.pid,self.asset['id']),
                      m.media_job_list(self.pid)['items'][0]):
            self.assertEqual(value['result']['word_count'],4)
            self.assertNotIn('words',value['result']);self.assertNotIn('sentences',value['result'])
            self.assertTrue(Path(value['result']['transcript_path']).is_file())
        self.assertEqual(len(speech.report(self.pid,job)[0]['words']),4)

    def test_touching_zero_duration_tokens_preserve_quote_and_estimated_group(self):
        segments=[{'text':'拍就可以了。','words':[
            {'word':'拍','start':1,'end':1.3,'probability':.9},
            {'word':'就可以','start':1.3,'end':1.3,'probability':.85},
            {'word':'了。','start':1.3,'end':1.5,'probability':.9}]}]
        original=copy.deepcopy(segments)
        job=self.finished(segments)
        row=speech.candidates_get(self.pid,job)['items'][0]
        self.assertEqual(row['text'],'拍就可以了。')
        self.assertIn('grouped_word_timing_requires_listening',row['warnings'])
        self.assertEqual(row['grouped_word_timings'][0]['timestamp_components'][1]['word'],'就可以')
        self.assertEqual(row['meaning_status'],'unreviewed')
        self.assertEqual(segments,original)
        binding=speech.range_validate(self.pid,job,[row['sentence_id']])
        self.assertLessEqual(binding['start'],1);self.assertGreaterEqual(binding['end'],1.5)
        with self.assertRaisesRegex(ValueError,'cuts'):
            speech.range_validate(self.pid,job,[row['sentence_id']],1.3,1.5)

    def test_leading_zero_duration_group_and_isolated_zero_fail_closed(self):
        words,rows,errors=speech.build_sentences([{'text':'28度。','words':[
            {'word':'28','start':1,'end':1,'probability':.8},
            {'word':'度。','start':1,'end':1.3,'probability':.9}]}],0,12)
        self.assertFalse(errors);self.assertEqual(rows[0]['text'],'28度。')
        self.assertEqual((words[0]['start'],words[0]['end']),(1,1.3))
        _,_,errors=speech.build_sentences([{'text':'孤立','words':[
            {'word':'孤立','start':1,'end':1,'probability':.9}]}],0,12)
        self.assertTrue(errors)

    def test_decimal_not_split_at_period_pause_or_asr_segment_boundary(self):
        segments=[{'text':'14.','words':[{'word':'14.','start':1,'end':1.4,'probability':.9}]},
                  {'text':'3微米以内。','words':[{'word':'3','start':1.9,'end':2.1,'probability':.9},
                    {'word':'微米以内。','start':2.1,'end':3,'probability':.9}]}]
        job=self.finished(segments)
        rows=speech.candidates_get(self.pid,job)['items']
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['text'],'14.3微米以内。')
        self.assertEqual(speech.range_validate(self.pid,job,[rows[0]['sentence_id']])['text'],'14.3微米以内。')

    def test_legacy_transcript_cannot_select_only_fractional_digits(self):
        job=self.finished(); value,checksum=speech.report(self.pid,job)
        value['words']=[{'word':'14.','start':1,'end':1.4}, {'word':'3微米以内。','start':1.6,'end':3}]
        value['sentences']=[{'sentence_id':'sentence_0000','first_word':0,'last_word':0,'text':'14.','warnings':[]},
                            {'sentence_id':'sentence_0001','first_word':1,'last_word':1,'text':'3微米以内。','warnings':[]}]
        with patch.object(speech,'report',return_value=(value,checksum)):
            for ident in ['sentence_0000','sentence_0001']:
                with self.assertRaisesRegex(ValueError,'numeric expression'):
                    speech.range_validate(self.pid,job,[ident])
            result=speech.range_validate(self.pid,job,['sentence_0000','sentence_0001'])
        self.assertEqual(result['text'],'14.3微米以内。')


if __name__=='__main__':unittest.main()
