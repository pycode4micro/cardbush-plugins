"""Explicit synchronization, cross-track diagnostics and failure recovery contracts."""
import copy
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np

from video_editer import engine, jobs, timing
from video_editer import mcp_server as s
from video_editer.locking import file_lock


def ffmpeg(*args):
    result = subprocess.run([engine.ffmpeg_bin(), '-nostdin', '-y', *map(str, args)], capture_output=True, timeout=120)
    if result.returncode:
        raise AssertionError(result.stderr.decode(errors='replace')[-4000:])
    return result.stdout


class TimingRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix='video-editer-timing-recovery-'))
        cls.video = cls.root / 'blue.mp4'
        cls.image = cls.root / 'red.png'
        cls.hot = cls.root / 'hot.wav'
        ffmpeg('-f', 'lavfi', '-i', 'color=c=blue:s=320x480:r=25:d=6', '-f', 'lavfi', '-i',
               'sine=frequency=440:sample_rate=48000:duration=6', '-c:v', 'libx264', '-c:a', 'aac', cls.video)
        ffmpeg('-f', 'lavfi', '-i', 'color=c=red:s=80x80', '-frames:v', '1', cls.image)
        ffmpeg('-f', 'lavfi', '-i', 'sine=frequency=440:duration=1', '-af', 'volume=10', '-c:a', 'pcm_f32le', cls.hot)
        print('\nTIMING_RECOVERY_REVIEW=' + str(cls.root), flush=True)

    def setUp(self):
        self.storage = patch.object(engine, 'PROJECTS', self.root / 'projects')
        self.storage.start()
        self.pid = s.project_create('explicit anchor regression')['project_id']
        self.asset = s.media_import(self.pid, str(self.video))['asset']['id']
        self.image_id = s.media_import(self.pid, str(self.image))['asset']['id']
        self.clip = s.clip_add(self.pid, self.asset, 0, 4, transition='none')['clip']['id']

    def tearDown(self):
        self.storage.stop()

    def tl(self):
        return s.timeline_get(self.pid)['timeline']

    def unchanged(self, operation):
        path = engine.project_dir(self.pid) / 'project.json'
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            operation()
        self.assertEqual(before, path.read_bytes())

    def test_binding_follows_speed_reorder_and_transition(self):
        second = s.clip_add(self.pid, self.asset, 2, 5, transition='none')['clip']['id']
        s.transition_apply(self.pid, self.clip, 'paint_flash')
        event = s.subtitle_add(self.pid, 'exact source phrase', 4.58, 5.58)['subtitle']['id']
        s.event_bind(self.pid, event, second, 3, 4)
        s.clip_update(self.pid, second, playback_speed=1.25)
        item = timing.locate(self.tl(), event)[1]
        self.assertAlmostEqual(item['start'], 4.8-engine.transition_overlap_seconds('paint_flash'))
        self.assertAlmostEqual(item['end'], 5.6-engine.transition_overlap_seconds('paint_flash'))
        s.clip_reorder(self.pid, [second, self.clip])
        item = timing.locate(self.tl(), event)[1]
        self.assertAlmostEqual(item['start'], .8)
        self.assertAlmostEqual(item['end'], 1.6)
        self.assertAlmostEqual(s.timeline_time_map(self.pid)['duration'], 6.4)

    def test_trim_and_remove_cannot_destroy_bound_content(self):
        event = s.subtitle_add(self.pid, 'keep complete', 1, 3)['subtitle']['id']
        s.event_bind(self.pid, event, self.clip, 1, 3)
        self.unchanged(lambda: s.clip_update(self.pid, self.clip, end=2))
        self.unchanged(lambda: s.clip_remove(self.pid, self.clip))
        self.unchanged(lambda: s.subtitle_update(self.pid, event, start=1.2))
        s.clip_update(self.pid, self.clip, start=.5, end=3.5)
        self.assertAlmostEqual(timing.locate(self.tl(), event)[1]['start'], .5)

    def test_split_rebinds_right_events_and_rejects_spanning_events(self):
        event = s.subtitle_add(self.pid, 'right phrase', 2.4, 3.5)['subtitle']['id']
        s.event_bind(self.pid, event, self.clip, 2.4, 3.5)
        split = s.clip_split(self.pid, self.clip, 2)
        item = timing.locate(self.tl(), event)[1]
        self.assertEqual(item['time_binding']['clip_id'], split['right']['id'])
        self.assertAlmostEqual(item['start'], 2.4)
        self.unchanged(lambda: s.clip_split(self.pid, split['right']['id'], 1))

    def test_unbind_freezes_current_times_and_restore_preserves_anchor(self):
        event = s.subtitle_add(self.pid, 'anchor', 1, 2)['subtitle']['id']
        s.event_bind(self.pid, event, self.clip, 1, 2)
        s.clip_update(self.pid, self.clip, playback_speed=1.25)
        value = s.event_unbind(self.pid, event)
        self.assertAlmostEqual(value['event']['start'], .8)
        s.clip_update(self.pid, self.clip, playback_speed=1)
        self.assertAlmostEqual(timing.locate(self.tl(), event)[1]['start'], .8)
        s.timeline_restore(self.pid, value['undo_snapshot'])
        self.assertIn('time_binding', timing.locate(self.tl(), event)[1])

    def test_overlay_keys_and_audio_duration_follow_explicit_binding(self):
        graphic = s.overlay_add(self.pid, self.image_id, .5, 2.5, .1, .1, .1, .1)['overlay']['id']
        s.overlay_keyframe(self.pid, graphic, 1.5, .5, .1)
        s.event_bind(self.pid, graphic, self.clip, .5, 2.5)
        audio = s.audio_track_add(self.pid, self.asset, start=.5, duration=2, volume=.2)['audio']['id']
        s.event_bind(self.pid, audio, self.clip, .5, 2.5)
        s.clip_update(self.pid, self.clip, playback_speed=1.25)
        graphic_row = timing.locate(self.tl(), graphic)[1]
        self.assertAlmostEqual(graphic_row['keyframes'][0]['at'], 1.2)
        self.assertAlmostEqual(graphic_row['start'], .4)
        self.assertAlmostEqual(timing.locate(self.tl(), audio)[1]['duration'], 1.6)
        self.unchanged(lambda: s.overlay_keyframe(self.pid, graphic, 1.3, .4, .1))

    def test_point_beats_follow_source_not_a_fixed_delay(self):
        event = s.callout_points_add(self.pid, [
            {'text': '第一点', 'start': .4, 'end': 1.3},
            {'text': '第二点', 'start': 2, 'end': 3.3}])['callout']['id']
        s.event_bind(self.pid, event, self.clip, .4, 3.3)
        s.clip_update(self.pid, self.clip, playback_speed=1.25)
        points = timing.locate(self.tl(), event)[1]['points']
        self.assertAlmostEqual(points[0]['start'], .32)
        self.assertAlmostEqual(points[1]['start'], 1.6)
        self.assertAlmostEqual(points[1]['end'], 2.64)
        self.unchanged(lambda: s.callout_update(self.pid, event, text='hardcoded'))
        self.unchanged(lambda: s.callout_update(self.pid, event, points=points))

    def test_point_updates_and_invalid_data_are_atomic(self):
        points = [{'text': 'A', 'start': .2, 'end': 1}, {'text': 'B', 'start': 2, 'end': 3}]
        event = s.callout_points_add(self.pid, points)['callout']['id']
        for bad in ([], [{'text': '', 'start': 0, 'end': 1}],
                    [{'text': 'A', 'start': float('nan'), 'end': 1}],
                    [{'text': 'A', 'start': 2, 'end': 1}],
                    [{'text': 'too long text', 'start': 0, 'end': 1}],
                    [{'text': 'A', 'start': .1, 'end': 1}]*5):
            self.unchanged(lambda bad=bad: s.callout_update(self.pid, event, points=bad))
        changed = s.callout_update(self.pid, event, points=[{'text': 'new', 'start': .3, 'end': 1.3}])
        self.assertEqual(changed['callout']['text'], 'new')

    def test_cross_track_overlap_reports_ids_without_repositioning(self):
        a = s.overlay_add(self.pid, self.image_id, .4, 2, .1, .1, .2, .2)['overlay']['id']
        b = s.overlay_add(self.pid, self.image_id, 1, 3, .2, .2, .2, .2)['overlay']['id']
        before = (engine.project_dir(self.pid) / 'project.json').read_bytes()
        report = s.timeline_validate(self.pid)
        self.assertTrue(report['valid'])
        self.assertTrue(any(set(i['event_ids']) == {a, b} and i['code'] == 'possible_visual_overlap' for i in report['warnings']))
        self.assertEqual(before, (engine.project_dir(self.pid) / 'project.json').read_bytes())

    def test_out_of_output_event_is_not_renderable(self):
        s.subtitle_add(self.pid, 'outside', 4.5, 5)
        report = s.timeline_validate(self.pid)
        self.assertFalse(report['valid'])
        self.assertIn('event_outside_output', [i['code'] for i in report['issues']])
        with patch.object(engine, 'render_project', side_effect=AssertionError('Must stop before rendering')):
            with self.assertRaises(ValueError):
                s.render_final(self.pid)

    def test_audio_peak_warning_and_invalid_bed(self):
        self.assertTrue(s.audio_inspect(str(self.hot))['near_full_scale'])
        self.assertFalse(s.audio_inspect(str(self.video))['near_full_scale'])
        self.unchanged(lambda: s.audio_set_bed(self.pid, self.asset, -1))
        self.unchanged(lambda: s.audio_set_bed(self.pid, self.asset, float('nan')))

    def test_many_short_cuts_do_not_accumulate_timing_drift(self):
        s.clip_update(self.pid, self.clip, start=.17, end=.8, playback_speed=1.25)
        for index in range(7):
            s.clip_add(self.pid, self.asset, .31, 1.04, playback_speed=1.25 if index % 2 else 1, transition='none')
        duration = s.timeline_time_map(self.pid)['duration']
        result = s.render_final(self.pid)
        self.assertAlmostEqual(result['quality']['duration'], duration, delta=.045)

    def test_real_three_point_visibility_matches_caller_beats(self):
        event = s.callout_points_add(self.pid, [
            {'text': '第一点', 'start': .25, 'end': 1.3},
            {'text': '第二点', 'start': 1.7, 'end': 3.5},
            {'text': '第三点', 'start': 2.65, 'end': 3.5}])['callout']
        result = s.render_final(self.pid)
        ffmpeg('-i', result['path'], '-vf', 'fps=2,scale=162:288,tile=4x2', '-frames:v', '1', self.root / 'explicit-points-contact.png')
        ass = (Path(result['path']).parent / 'base' / 'effects.ass').read_text(encoding='utf-8-sig')
        self.assertIn('0:00:01.70,0:00:03.50', ass)
        self.assertIn('0:00:02.65,0:00:03.50', ass)
        raw = ffmpeg('-i', result['path'], '-vf', 'scale=270:480', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-')
        frames = np.frombuffer(raw, np.uint8).reshape(-1, 480, 270, 3)
        def white(timestamp, row):
            top = round((640+232*row)/4)
            area = frames[round(timestamp*25), top:top+48, 8:133]
            return int(np.count_nonzero((area[:,:,0] > 150) & (area[:,:,1] > 150)))
        self.assertEqual(white(1.4, 1), 0)
        self.assertGreater(white(2., 1), 500)
        self.assertEqual(white(2.4, 2), 0)
        self.assertGreater(white(2.9, 2), 500)
        self.assertEqual(white(1.5, 0), 0)
        self.assertEqual(white(3.8, 1), 0)
        ffmpeg('-i', result['path'], '-vf', 'fps=2,scale=162:288,tile=4x2', '-frames:v', '1', self.root / 'explicit-points-contact.png')
        (self.root / 'points-result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')

    def failed_job(self):
        s.clip_update(self.pid, self.clip, end=2)
        s.subtitle_add(self.pid, 'saved snapshot', .2, 1.5)
        with patch.object(s, '_apply_captions', side_effect=RuntimeError('injected caption failure')):
            with self.assertRaises(RuntimeError) as caught:
                s.render_final(self.pid)
        return re.search(r'job_id=([^;]+)', str(caught.exception)).group(1)

    def test_failed_render_retry_uses_saved_revision_and_completed_stages(self):
        job = self.failed_job()
        status = s.render_job_status(self.pid, job)
        self.assertEqual(status['status'], 'failed')
        self.assertEqual(status['current_stage'], 'captions')
        old = jobs.folder(self.pid, job)
        old_hash = jobs.digest(old / status['stages']['base']['path'])
        s.clip_update(self.pid, self.clip, end=1.8)
        with patch.object(engine, 'render_project', side_effect=AssertionError('Reuse completed base')):
            result = s.render_retry(self.pid, job)
        self.assertEqual(result['revision'], status['revision'])
        self.assertIn('base', result['reused_stages'])
        self.assertIn('graphics', result['reused_stages'])
        self.assertNotEqual(result['job_id'], job)
        self.assertEqual(old_hash, jobs.digest(old / status['stages']['base']['path']))
        self.assertAlmostEqual(result['quality']['duration'], 2, delta=.1)
        self.assertEqual(s.render_job_status(self.pid, result['job_id'])['status'], 'succeeded')
        self.assertEqual(s.render_job_status(self.pid, job)['status'], 'failed')

    def test_corrupt_checkpoint_is_rerendered_not_reused(self):
        job = self.failed_job()
        state = s.render_job_status(self.pid, job)
        base = jobs.folder(self.pid, job) / state['stages']['base']['path']
        base.write_bytes(b'corrupt test checkpoint')
        with patch.object(engine, 'render_project', wraps=engine.render_project) as called:
            result = s.render_retry(self.pid, job)
        self.assertEqual(called.call_count, 1)
        self.assertNotIn('base', result['reused_stages'])

    def test_changed_source_rejects_retry(self):
        job = self.failed_job()
        imported = Path(s.media_inspect(self.pid, self.asset)['materials'][0]['path'])
        with imported.open('ab') as stream:
            stream.write(b'changed-after-snapshot')
        with self.assertRaisesRegex(ValueError, 'Media changed'):
            s.render_retry(self.pid, job)

    def test_interrupted_status_is_readonly_and_retryable(self):
        job = self.failed_job()
        state_file = jobs.folder(self.pid, job) / 'state.json'
        state = engine.read_json(state_file)
        state['status'] = 'running'  # Simulate a crashed worker whose OS lock was released.
        engine.write_json(state_file, state)
        before = state_file.read_bytes()
        self.assertEqual(s.render_job_status(self.pid, job)['status'], 'interrupted')
        self.assertEqual(before, state_file.read_bytes())
        self.assertEqual(s.render_retry(self.pid, job)['status'], 'succeeded')

    def test_cross_process_write_lock_prevents_lost_update(self):
        project_file = engine.project_dir(self.pid) / 'project.json'
        before = project_file.read_bytes()
        code = ('from video_editer import engine; from video_editer import mcp_server as s; from pathlib import Path; '
                f'engine.PROJECTS=Path({str(engine.PROJECTS)!r});\n'
                f'try:\n s.subtitle_add({self.pid!r}, "racing edit", .1, .5)\n'
                'except ValueError as e:\n print(str(e))\nelse:\n raise AssertionError("lock was ignored")')
        with file_lock(engine.project_dir(self.pid) / 'writer.lock'):
            child = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=30)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertIn('busy', child.stdout)
        self.assertEqual(before, project_file.read_bytes())


if __name__ == '__main__':
    unittest.main()
