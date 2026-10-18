"""Regression evidence for missing/quiet cues, equal-level A/B and nonblocking inspection."""
import asyncio
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from video_editer import audio, audio_review, engine, jobs, media_jobs, resources
from video_editer import mcp_server as s


class AudioReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='video-cue-tests-')
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'VIDEO_EDITER_RESOURCE_DIR': str(self.root/'resources')})
        self.env.start()
        # Tiny audio fixtures only; never change the production host policy.
        resources.configure({'encoder': 'cpu', 'cpu_threads': 2, 'reserve_memory_percent': 5,
                             'min_free_memory_mb': 128, 'max_system_cpu_percent': 100})
        self.projects = patch.object(engine, 'PROJECTS', self.root/'projects')
        self.projects.start()
        self.worker = patch.object(media_jobs, 'start_worker')
        self.worker.start()
        self.pid = s.project_create('cue regression')['project_id']
        still = self.root/'still.png'
        Image.new('RGB', (32, 32), 'white').save(still)
        voice, cue = self.root/'voice.wav', self.root/'cue.wav'
        for path, seconds, frequency, amplitude in [(voice, 3, 431, .12), (cue, .3, 1400, .08)]:
            t = np.arange(round(seconds*audio.RATE))/audio.RATE
            values = amplitude*np.sin(2*np.pi*frequency*t)
            audio_review._write_wave(path, np.column_stack([values, values]))
        project = s._read(self.pid)
        project['materials'] = [
            {'id': 'still', 'kind': 'image', 'path': str(still), 'probe': {}},
            {'id': 'voice', 'kind': 'audio', 'path': str(voice), 'probe': {'duration': 3, 'has_audio': True}},
            {'id': 'cue', 'kind': 'audio', 'path': str(cue), 'probe': {'duration': .3, 'has_audio': True}}]
        tl = s._timeline(project)
        tl['tracks']['main'] = [{'id': 'clip1', 'asset_id': 'still', 'start': 0, 'end': 3,
                                'playback_speed': 1, 'transition': 'none', 'audio_mute': True}]
        tl['tracks']['audio'] = [
            {'id': 'voice1', 'asset_id': 'voice', 'start': 0, 'duration': 3, 'volume': 1, 'role': 'voiceover'},
            {'id': 'sfx1', 'asset_id': 'cue', 'start': 1, 'duration': .3, 'volume': 1, 'role': 'sfx',
             'label': 'reveal', 'custom': {'preserve': True}}]
        s._write(self.pid, project)

    def tearDown(self):
        self.worker.stop()
        self.projects.stop()
        self.env.stop()
        self.temp.cleanup()

    def run_review(self, **kwargs):
        state = s.audio_review_submit(self.pid, **kwargs)
        self.assertEqual(state['status'], 'queued')
        media_jobs.execute(self.pid, state['job_id'])
        result = media_jobs.status(self.pid, state['job_id'])
        self.assertEqual(result['status'], 'succeeded', result.get('error'))
        return result['result']

    def save_delivery(self, project, delivered_path):
        attempt = jobs.Attempt(self.pid, project, False)
        attempt.bind_media()
        attempt.succeed({'path': str(delivered_path), 'revision': project['mcp_timeline']['revision']})
        return attempt.job_id

    def test_cached_inspection_never_waits_or_probes_and_refresh_is_explicit(self):
        before = (engine.project_dir(self.pid)/'project.json').read_bytes()
        with patch.object(resources, 'heavy_slot', side_effect=AssertionError('must not wait')), \
             patch.object(engine, 'ffprobe', side_effect=AssertionError('must not probe')):
            result = s.media_inspect(self.pid)
        self.assertEqual(result['inspection'], 'stored_metadata')
        self.assertFalse(result['metadata_refreshed'])
        self.assertFalse(result['content_verified'])
        with patch.object(resources, 'heavy_slot') as slot, patch.object(engine, 'ffprobe', return_value={'duration': .3}) as probe:
            fresh = s.media_inspect(self.pid, 'cue', refresh=True)
        slot.assert_called_once()
        probe.assert_called_once()
        self.assertTrue(fresh['metadata_refreshed'])
        self.assertEqual(before, (engine.project_dir(self.pid)/'project.json').read_bytes())

    def test_update_preserves_binding_identity_metadata_and_undo(self):
        s.event_bind(self.pid, 'sfx1', 'clip1', 1, 1.3)
        before = s.timeline_get(self.pid)['timeline']['tracks']['audio'][1]
        result = s.audio_track_update(self.pid, 'sfx1', volume=.4, label='same cue, new level')
        after = result['audio']
        self.assertEqual(after['id'], before['id'])
        self.assertEqual(after['time_binding'], before['time_binding'])
        self.assertEqual(after['custom'], before['custom'])
        self.assertEqual(after['volume'], .4)
        saved = (engine.project_dir(self.pid)/'project.json').read_bytes()
        for changes in [{'start': 1.1}, {'volume': float('nan')}, {'volume': True}, {'source_start': .29}, {'label': 'x'*161}, {'role': 'guessed'}]:
            with self.assertRaises(ValueError):
                s.audio_track_update(self.pid, 'sfx1', **changes)
            self.assertEqual(saved, (engine.project_dir(self.pid)/'project.json').read_bytes())
        s.timeline_restore(self.pid, result['undo_snapshot'])
        self.assertEqual(s.timeline_get(self.pid)['timeline']['tracks']['audio'][1], before)

    def test_low_cue_is_flagged_despite_audible_mix_and_no_auto_gain_change(self):
        s.audio_track_update(self.pid, 'sfx1', volume=.02)
        before = (engine.project_dir(self.pid)/'project.json').read_bytes()
        result = self.run_review()
        row = result['cues'][0]
        self.assertIn('low_cue_level', row['warnings'])
        self.assertIn('possible_masking_by_nearby_audio', row['warnings'])
        self.assertLess(row['cue_rms_dbfs'], -50)
        self.assertGreater(row['other_audio_active_context_p75_dbfs'], -25)
        self.assertFalse(result['human_listened'])
        self.assertEqual(row['delivery']['signal_presence'], 'unchecked')
        self.assertEqual(before, (engine.project_dir(self.pid)/'project.json').read_bytes())
        full = audio_review._decode(row['samples']['with'], 0, row['window']['end']-row['window']['start'])
        without = audio_review._decode(row['samples']['without'], 0, len(full)/audio.RATE)
        contribution = audio_review._decode(row['samples']['contribution'], 0, len(full)/audio.RATE)
        np.testing.assert_allclose(full-without, contribution, atol=2e-8)

    def test_muted_cue_and_overlapping_second_cue_are_independent(self):
        s.audio_track_update(self.pid, 'sfx1', volume=0)
        added = s.audio_track_add(self.pid, 'cue', start=1, duration=.3, role='sfx', label='overlap')['audio']
        report = self.run_review()
        rows = {row['audio_id']: row for row in report['cues']}
        self.assertIn('muted_cue', rows['sfx1']['warnings'])
        self.assertIn('silent_or_negligible_contribution', rows['sfx1']['warnings'])
        self.assertIsNone(rows['sfx1']['cue_rms_dbfs'])
        self.assertGreater(rows[added['id']]['cue_rms_dbfs'], -30)

    def test_delivered_review_uses_saved_revision_and_finds_missing_cue(self):
        project = s._read(self.pid)
        delivered = self.root/'delivered.wav'
        with resources.heavy_slot():
            audio.render(project, project['mcp_timeline'], delivered)
        jid = self.save_delivery(project, delivered)
        s.audio_track_update(self.pid, 'sfx1', volume=0)  # The live edit must not contaminate review.
        report = self.run_review(render_job_id=jid)
        self.assertEqual(report['revision'], project['mcp_timeline']['revision'])
        self.assertEqual(report['cues'][0]['volume'], 1)
        self.assertEqual(report['cues'][0]['delivery']['signal_presence'], 'confirmed')
        bad = copy.deepcopy(project)
        bad['mcp_timeline']['tracks']['audio'].pop()
        missing = self.root/'missing.wav'
        with resources.heavy_slot():
            audio.render(bad, bad['mcp_timeline'], missing)
        missing_job = self.save_delivery(project, missing)
        row = self.run_review(render_job_id=missing_job)['cues'][0]
        self.assertEqual(row['delivery']['signal_presence'], 'not_detected')

    def test_normalization_is_frozen_for_comparison_not_recomputed(self):
        s.audio_configure(self.pid, target_lufs=-18, limiter=True)
        real_render, calls = audio.render, []
        def capture(*args, **kwargs):
            result = real_render(*args, **kwargs)
            calls.append(result)
            return result
        with patch.object(audio, 'render', side_effect=capture):
            self.run_review()
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]['applied_master_gain_db'], calls[1]['applied_master_gain_db'])
        self.assertEqual(calls[1]['normalization_mode'], 'fixed comparison gain')

    def test_aac_delivery_is_compared_without_claiming_perceptual_approval(self):
        project = s._read(self.pid)
        pcm, aac = self.root/'mix.wav', self.root/'encoded.m4a'
        with resources.heavy_slot():
            audio.render(project, project['mcp_timeline'], pcm)
            s.processes.run([engine.ffmpeg_bin(), '-nostdin', '-v', 'error', '-y', '-i', str(pcm),
                             '-c:a', 'aac', '-b:a', '192k', str(aac)], capture_output=True, check=True, timeout=60)
        jid = self.save_delivery(project, aac)
        report = self.run_review(render_job_id=jid)
        self.assertEqual(report['cues'][0]['delivery']['signal_presence'], 'confirmed')
        self.assertFalse(report['human_listened'])
        self.assertEqual(report['perceptual_review'], 'needs_listening')

    def test_transient_failure_retries_saved_snapshot_after_live_edit(self):
        state = s.audio_review_submit(self.pid)
        with patch.object(audio, 'render', side_effect=RuntimeError('temporary decoder failure')):
            media_jobs.execute(self.pid, state['job_id'])
        self.assertEqual(media_jobs.status(self.pid, state['job_id'])['status'], 'failed')
        s.audio_track_update(self.pid, 'sfx1', volume=0)
        retry = s.media_job_retry(self.pid, state['job_id'])
        self.assertEqual(retry['status'], 'queued')
        media_jobs.execute(self.pid, retry['job_id'])
        result = media_jobs.status(self.pid, retry['job_id'])
        self.assertEqual(result['status'], 'succeeded', result.get('error'))
        self.assertEqual(result['result']['cues'][0]['volume'], 1)
        self.assertNotEqual(result['result']['revision'], s._read(self.pid)['mcp_timeline']['revision'])

    def test_submission_is_lightweight_and_changed_input_retry_stays_queued(self):
        with patch.object(audio, 'render', side_effect=AssertionError('not at submit')), \
             patch.object(engine, 'ffprobe', side_effect=AssertionError('not at submit')), \
             patch.object(jobs, 'digest', side_effect=AssertionError('no source hashing at submit')), \
             patch.object(resources, 'heavy_slot', side_effect=AssertionError('not at submit')):
            state = s.audio_review_submit(self.pid)
        source = self.root/'cue.wav'
        source.write_bytes(source.read_bytes()+b'changed')
        media_jobs.execute(self.pid, state['job_id'])
        failed = media_jobs.status(self.pid, state['job_id'])
        self.assertEqual(failed['status'], 'failed')
        self.assertIn('changed', failed['error']['message'])
        retry = s.media_job_retry(self.pid, state['job_id'])
        self.assertEqual(retry['status'], 'queued')
        self.assertEqual(retry['retry_of'], state['job_id'])
        self.assertNotEqual(retry['job_id'], state['job_id'])
        self.assertEqual(engine.read_json(media_jobs.folder(self.pid, retry['job_id'])/'request.json'),
                         engine.read_json(media_jobs.folder(self.pid, state['job_id'])/'request.json'))

    def test_request_rejects_ambiguous_selection_and_schema_exposes_new_tools(self):
        for options in [{'audio_ids': []}, {'audio_ids': ['sfx1', 'sfx1']}, {'audio_ids': ['absent']},
                        {'context_seconds': float('nan')}, {'context_seconds': True}]:
            with self.assertRaises(ValueError):
                s.audio_review_submit(self.pid, **options)
        tools = {tool.name: tool for tool in asyncio.run(s.mcp.list_tools())}
        self.assertIn('audio_review_submit', tools)
        self.assertIn('audio_track_update', tools)
        self.assertFalse(tools['media_inspect'].inputSchema['properties']['refresh']['default'])


if __name__ == '__main__':
    unittest.main()
