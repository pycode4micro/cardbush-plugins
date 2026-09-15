"""Offline contracts plus real FFmpeg pixel/audio/output-isolation regressions.

Run: python -m unittest discover -s tests -v
Fixtures are synthetic; no paid API, credentials or user media are used.
"""
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server as s
from edit_contract import check_overlay, timeline_changes


def ffmpeg(*args):
    result = subprocess.run([s.engine.ffmpeg_bin(), '-hide_banner', '-y', *map(str, args)], capture_output=True, timeout=120)
    if result.returncode:
        raise AssertionError(result.stderr.decode(errors='replace')[-5000:])
    return result.stdout


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Kept for manual review; never clean or delete user data.
        cls.root = Path(tempfile.mkdtemp(prefix='video-editer-regression-'))
        cls.projects = cls.root / 'projects'
        cls.video = cls.root / 'source.mp4'
        cls.silent = cls.root / 'silent.mp4'
        cls.red = cls.root / 'red.png'
        cls.black = cls.root / 'black.mp4'
        ffmpeg('-f', 'lavfi', '-i', 'testsrc2=s=320x480:r=25:d=4', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=4', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', cls.video)
        ffmpeg('-f', 'lavfi', '-i', 'color=c=black:s=320x480:r=25:d=4', '-c:v', 'libx264', cls.silent)
        ffmpeg('-f', 'lavfi', '-i', 'color=c=red:s=100x100', '-frames:v', '1', cls.red)
        ffmpeg('-f', 'lavfi', '-i', 'color=c=black:s=320x480:r=25:d=1', '-c:v', 'libx264', cls.black)
        print('\nREVIEW_ARTIFACTS=' + str(cls.root), flush=True)

    def setUp(self):
        self.project_patch = patch.object(s.engine, 'PROJECTS', self.projects)
        self.project_patch.start()
        self.pid = s.project_create('offline regression')['project_id']
        self.asset = s.media_import(self.pid, str(self.video))['asset']['id']
        self.image = s.media_import(self.pid, str(self.red))['asset']['id']
        self.clip = s.clip_add(self.pid, self.asset, 0, 4, transition='none')['clip']['id']

    def tearDown(self):
        self.project_patch.stop()

    def unchanged_on_error(self, operation):
        path = s._folder(self.pid) / 'project.json'
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            operation()
        self.assertEqual(before, path.read_bytes())

    def test_insert_and_speed_aware_split(self):
        s.clip_update(self.pid, self.clip, playback_speed=1.25, transition='slide_push')
        previous = s.timeline_get(self.pid)['timeline']['tracks']['main'][0]
        result = s.clip_split(self.pid, self.clip, 1.2)
        self.assertAlmostEqual(result['left']['end'], 1.5)
        self.assertEqual(result['left']['transition'], 'none')
        self.assertEqual(result['right']['transition'], previous['transition'])
        self.assertEqual(result['right']['playback_speed'], 1.25)
        self.assertEqual(result['right']['end'], 4)
        insert = s.clip_insert_at(self.pid, 1, self.asset, 2, 3)
        self.assertEqual(s.timeline_get(self.pid)['timeline']['tracks']['main'][1]['id'], insert['clip']['id'])
        self.unchanged_on_error(lambda: s.clip_insert_at(self.pid, 99, self.asset, 0, 1))
        self.unchanged_on_error(lambda: s.clip_split(self.pid, self.clip, 0))
        self.unchanged_on_error(lambda: s.clip_split(self.pid, self.clip, 1.2))

    def test_diff_properties_order_and_custom_tracks(self):
        other = s.clip_add(self.pid, self.asset, 0, 1, transition='none')['clip']['id']
        snap = s.timeline_snapshot(self.pid, 'before')['snapshot_id']
        s.clip_update(self.pid, self.clip, playback_speed=1.25, end=3)
        s.clip_reorder(self.pid, [other, self.clip])
        s.track_create(self.pid, 'music', 'audio')
        s.audio_duck_source(self.pid, .4)
        diff = s.timeline_diff(self.pid, snap)
        self.assertTrue(diff['clips']['order_changed'])
        self.assertEqual(diff['clips']['modified'][self.clip]['playback_speed']['after'], 1.25)
        self.assertEqual(diff['clips']['modified'][self.clip]['end']['after'], 3)
        self.assertIn('music', diff['tracks_added'])
        self.assertIn('audio_config', diff['settings'])

    def test_restore_is_monotonic_and_undoable(self):
        snap = s.timeline_snapshot(self.pid, 'before')['snapshot_id']
        s.clip_update(self.pid, self.clip, end=2)
        s.audio_duck_source(self.pid, .2)
        previous = s.timeline_get(self.pid)['timeline']
        result = s.timeline_restore(self.pid, snap)
        restored = s.timeline_get(self.pid)['timeline']
        self.assertEqual(result['revision'], previous['revision'] + 1)
        self.assertEqual(restored['tracks']['main'][0]['end'], 4)
        self.assertEqual(restored['history'][:-1], previous['history'])
        undone = s.timeline_restore(self.pid, result['undo_snapshot'])
        self.assertEqual(undone['revision'], result['revision'] + 1)
        self.assertEqual(s.timeline_get(self.pid)['timeline']['tracks']['main'][0]['end'], 2)

    def test_invalid_edits_never_write(self):
        self.unchanged_on_error(lambda: s.clip_update(self.pid, self.clip, end=9))
        self.unchanged_on_error(lambda: s.clip_insert_at(self.pid, 0, self.asset, 2, 1))
        self.unchanged_on_error(lambda: s.overlay_add(self.pid, self.image, 0, 2, .9, 0, .2, .2))
        self.unchanged_on_error(lambda: s.overlay_add(self.pid, self.image, 0, 2, 0, 0, .2, float('nan')))
        self.unchanged_on_error(lambda: s.subtitle_add(self.pid, 'text', -1, 2))
        self.unchanged_on_error(lambda: s.audio_track_add(self.pid, self.asset, volume=float('nan')))
        self.unchanged_on_error(lambda: s.audio_track_add(self.pid, self.asset, source_start=3, duration=2))
        callout = s.callout_add(self.pid, 'test', .2, 1, 'product_left')['callout']['id']
        self.unchanged_on_error(lambda: s.callout_update(self.pid, callout, text=''))
        subtitle = s.subtitle_add(self.pid, 'text', .2, 1)['subtitle']['id']
        self.unchanged_on_error(lambda: s.subtitle_update(self.pid, subtitle, start=-1))

    def test_keyframe_replacement_and_bounds(self):
        item = s.overlay_add(self.pid, self.image, .4, 3.2, .1, .1, .2, .1)['overlay']
        s.overlay_keyframe(self.pid, item['id'], 1.6001, .5, .5)
        result = s.overlay_keyframe(self.pid, item['id'], 1.6002, .4, .4)
        self.assertEqual(len(result['overlay']['keyframes']), 1)
        self.assertEqual(result['overlay']['keyframes'][0]['x'], .4)
        self.unchanged_on_error(lambda: s.overlay_keyframe(self.pid, item['id'], 2, .9, .2))
        broken = copy.deepcopy(result['overlay'])
        broken['keyframes'] *= 2
        with self.assertRaises(ValueError):
            check_overlay(broken)

    def test_media_inspection_read_only_and_all_frames_exist(self):
        before = (s._folder(self.pid) / 'project.json').read_bytes()
        s.media_inspect(self.pid, self.asset)
        self.assertEqual(before, (s._folder(self.pid) / 'project.json').read_bytes())
        frames = s.media_extract_frames(self.pid, self.asset, 4)['frames']
        self.assertEqual(len(frames), 4)
        self.assertTrue(all(Path(f).is_file() and Path(f).stat().st_size > 0 for f in frames))

    def test_real_pixel_motion_size_and_visibility(self):
        graphic = {'path': str(self.red), 'kind': 'image', 'start': .4, 'end': 3.2,
                   'x': .1, 'y': .1, 'width': .2, 'height': .1, 'opacity': 1,
                   'keyframes': [{'at': 1.2, 'x': .5, 'y': .1}, {'at': 2., 'x': .5, 'y': .6}, {'at': 2.8, 'x': .1, 'y': .6}]}
        output = self.root / 'keyframe-verified.mp4'
        s._apply_graphics(self.silent, output, [graphic])
        raw = ffmpeg('-i', output, '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-')
        frames = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 480, 320, 3)
        for timestamp, x, y in [(.4, .1, .1), (.8, .3, .1), (1.2, .5, .1), (1.6, .5, .35), (2, .5, .6), (2.4, .3, .6), (2.8, .1, .6), (3.04, .1, .6)]:
            rgb = frames[round(timestamp * 25)]
            yy, xx = np.where((rgb[:, :, 0] > 150) & (rgb[:, :, 1] < 80) & (rgb[:, :, 2] < 80))
            self.assertGreater(len(xx), 0, timestamp)
            self.assertAlmostEqual(int(xx.min()), x*320, delta=3, msg=str(timestamp))
            self.assertAlmostEqual(int(yy.min()), y*480, delta=3, msg=str(timestamp))
            self.assertAlmostEqual(int(xx.max()-xx.min()+1), 64, delta=3)
            self.assertAlmostEqual(int(yy.max()-yy.min()+1), 48, delta=3)
        for timestamp in [0, .2, 3.2, 3.6]:
            self.assertLess(int(frames[round(timestamp*25)].max()), 20, timestamp)
        ffmpeg('-i', output, '-vf', 'fps=2,scale=160:240,tile=4x2', '-frames:v', '1', self.root / 'keyframe-contact.png')

    def test_real_audio_gain_and_silent_base(self):
        lowered = self.root / 'source-lowered.mp4'
        s._mix_audio_tracks(self.video, lowered, [], .25)
        def rms(path):
            raw = ffmpeg('-i', path, '-vn', '-ac', '1', '-ar', '48000', '-f', 'f32le', '-')
            pcm = np.frombuffer(raw, dtype=np.float32)[4800:-4800]
            return float(np.sqrt(np.mean(pcm**2)))
        ratio = rms(lowered) / rms(self.video)
        self.assertAlmostEqual(ratio, .25, delta=.035)
        output = self.root / 'silent-with-music.mp4'
        s._mix_audio_tracks(self.silent, output, [{'path': str(self.video), 'start': .4, 'duration': 2, 'source_start': 1, 'volume': .5, 'fade_in': .1, 'fade_out': .2}])
        self.assertTrue(s.output_inspect(str(output))['has_audio'])
        self.assertGreater(rms(output), .01)
        self.assertAlmostEqual(s.engine.ffprobe(output)['duration'], 4, delta=.1)

    def test_real_preview_never_overwrites_final_and_no_model(self):
        s.clip_update(self.pid, self.clip, end=1, role='hook')
        s.clip_insert_at(self.pid, 1, self.asset, 2, 3, role='closing')
        legacy = s._folder(self.pid) / 'render' / 'final.mp4'
        legacy.parent.mkdir()
        legacy.write_bytes(b'existing user delivery - preserve')
        old_hash = digest(legacy)
        # No model client remains in the engine; block network access as well.
        self.assertFalse(hasattr(s.engine, 'ask_seed'))
        with patch('socket.socket.connect', side_effect=AssertionError('NO NETWORK')):
            final = s.render_final(self.pid)
            final_hash = digest(final['path'])
            s.subtitle_add(self.pid, 'preview only', .2, .8)
            preview = s.render_preview(self.pid)
        self.assertNotEqual(preview['job_id'], final['job_id'])
        self.assertGreater(preview['revision'], final['revision'])
        self.assertEqual(final_hash, digest(final['path']))
        self.assertEqual(old_hash, digest(legacy))
        self.assertTrue(preview['has_audio'])
        self.assertEqual((preview['quality']['width'], preview['quality']['height']), (540, 960))
        self.assertNotIn('video_url', preview)
        self.assertAlmostEqual(final['quality']['duration'], 2, delta=.2)
        saved = json.loads(Path(preview['timeline_path']).read_text(encoding='utf-8'))
        self.assertEqual(saved['revision'], preview['revision'])
        self.assertEqual(final['quality']['width'], 1080)
        self.assertEqual(final['quality']['height'], 1920)

    def test_explicit_callouts_not_retimed_or_limited_to_eight(self):
        events = [{'text': f'point-{i}', 'start': i*.4, 'end': i*.4+.32,
                   'position': 'product_left', 'template': 'recommend_arrow', 'entrance': 'none'} for i in range(9)]
        work = self.root / 'exact-callouts'
        work.mkdir(exist_ok=True)
        plan = {'visual_effects': events, 'planner': {'visual_text_source': 'mcp_tool_execution'}}
        self.assertFalse(hasattr(s.engine, '_layout_product_callouts'))
        with patch('socket.socket.connect', side_effect=AssertionError('NO NETWORK')):
            self.assertTrue(s.engine._apply_visual_effects(self.video, work / 'output.mp4', plan, work))
        ass = (work / 'effects.ass').read_text(encoding='utf-8-sig')
        self.assertEqual(ass.count('Dialogue:'), 9)
        self.assertIn('0:00:00.00,0:00:00.32', ass)
        self.assertIn('point-8', ass)
        self.unchanged_on_error(lambda: s.callout_add(self.pid, 'x'*17, 0, 1, 'product_left'))

    def test_silent_and_audible_main_clips_assemble(self):
        silent_id = s.media_import(self.pid, str(self.silent))['asset']['id']
        s.clip_update(self.pid, self.clip, end=.6)
        s.clip_insert_at(self.pid, 0, silent_id, 0, .6)
        result = s.render_final(self.pid)
        self.assertTrue(result['has_audio'])
        self.assertAlmostEqual(result['quality']['duration'], 1.2, delta=.15)

    def test_missing_requested_callout_asset_is_failure(self):
        plan = {'visual_effects': [{'text': 'test', 'start': .1, 'end': .8, 'template': 'comic_burst'}],
                'planner': {'visual_text_source': 'mcp_tool_execution'}}
        with patch.object(s.engine, '_apply_template_stickers', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'sticker failed'):
                s.engine._apply_visual_effects(self.video, self.root / 'never.mp4', plan, self.root)

    def test_quality_rejects_corrupt_and_flags_black(self):
        broken = self.root / 'corrupt.mp4'
        broken.write_bytes(b'not a video')
        bad = s.quality_inspect(str(broken))
        self.assertFalse(bad['pass'])
        self.assertIn('decode/detection failed', bad['warnings'])
        black = s.quality_inspect(str(self.black), .2, .4)
        self.assertFalse(black['pass'])
        self.assertIn('no audio stream', black['warnings'])
        self.assertIn('black frames detected', black['warnings'])

    def test_mcp_stdio_real_tool_calls(self):
        async def exercise():
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            params = StdioServerParameters(command=sys.executable, args=[str(Path(__file__).resolve().parents[1] / 'server.py')], env=dict(os.environ))
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    names = {tool.name for tool in (await session.list_tools()).tools}
                    self.assertTrue({'clip_split', 'clip_insert_at', 'render_preview'} <= names)
                    result = await session.call_tool('transition_list', {'season': 'winter'})
                    self.assertFalse(result.isError)
                    rejected = await session.call_tool('timeline_get', {'project_id': '../invalid'})
                    self.assertTrue(rejected.isError)
        asyncio.run(exercise())


if __name__ == '__main__':
    unittest.main()
