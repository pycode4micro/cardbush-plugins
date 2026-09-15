"""Synthetic regressions for 57-cut delivery, audible joins and honest frame/audio reports."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from video_editer import engine, mcp_server as s, audio, quality


def ffmpeg(*args):
    return s.processes.run([engine.ffmpeg_bin(), '-nostdin', '-v', 'error', '-y', *map(str, args)],
        capture_output=True, check=True, timeout=180).stdout


class DeliveryAudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix='video-editer-delivery-'))
        cls.source = cls.root/'source.mov'
        ffmpeg('-f', 'lavfi', '-i', 'testsrc2=s=160x96:r=24:d=3', '-f', 'lavfi', '-i',
            'sine=frequency=431:sample_rate=48000:duration=3', '-c:v', 'libx264', '-c:a', 'pcm_f32le', cls.source)
        cls.original = audio.measure(cls.source)
        print('DELIVERY_ARTIFACTS='+str(cls.root), flush=True)

    def setUp(self):
        self.patch = patch.object(engine, 'PROJECTS', self.root/'projects')
        self.patch.start()
        self.pid = s.project_create('delivery regression')['project_id']
        s.canvas_configure(self.pid, 432, 256)
        self.asset = s.media_import(self.pid, str(self.source))['asset']['id']

    def tearDown(self):
        self.patch.stop()

    def add(self, start=0, end=.5, **kwargs):
        return s.clip_add(self.pid, self.asset, start, end, transition='none', **kwargs)['clip']

    def test_57_cuts_no_gain_drift_and_complete_moving_tail(self):
        for _ in range(56):
            self.add(audio_fade_in_ms=0, audio_fade_out_ms=0)
        self.add(end=3, audio_fade_in_ms=0, audio_fade_out_ms=0)
        result = s.render_final(self.pid)
        stream = result['video_stream']
        self.assertEqual(stream['decoded_frame_count'], 56*13+75)
        self.assertAlmostEqual(stream['video_duration_seconds'], (56*13+75)/25, places=4)
        self.assertAlmostEqual(result['audio']['integrated_lufs'], self.original['integrated_lufs'], delta=.5)
        self.assertLess(result['audio']['true_peak_dbfs'], -10)
        self.assertEqual(result['audio']['applied_master_gain_db'], 0)
        tail = ffmpeg('-sseof', '-2', '-i', result['path'], '-an', '-vf', 'scale=32:24', '-f', 'rawvideo', '-pix_fmt', 'gray', '-')
        frames = np.frombuffer(tail, np.uint8).reshape(-1, 32*24)
        self.assertGreater(np.count_nonzero(np.mean(np.abs(np.diff(frames.astype(float), axis=0)), axis=1) > .5), 30)
        (self.root/'57-cut-measurements.json').write_text(json.dumps({'source': self.original, 'output': result['audio'], 'video_stream': stream}, indent=2), encoding='utf-8')

    def test_gain_mute_fades_and_config_are_measured(self):
        clip = self.add(end=3, audio_fade_in_ms=0, audio_fade_out_ms=0)
        plain = s.render_audio_only(self.pid)
        s.clip_update(self.pid, clip['id'], audio_gain_db=-6)
        quiet = s.render_audio_only(self.pid)
        self.assertAlmostEqual(quiet['audio']['rms_db']-plain['audio']['rms_db'], -6, delta=.05)
        s.clip_update(self.pid, clip['id'], audio_mute=True)
        muted = s.render_audio_only(self.pid)
        self.assertIsNone(muted['audio']['integrated_lufs'])
        s.clip_update(self.pid, clip['id'], audio_mute=False, audio_gain_db=0)
        s.audio_configure(self.pid, gain_db=24, limiter=True, true_peak_limit_dbfs=-2)
        limited = s.render_final(self.pid)
        self.assertLess(limited['audio']['true_peak_dbfs'], -1)
        self.assertEqual(limited['audio']['limiter'], True)

    def test_join_warnings_fade_suppression_and_level_difference(self):
        # Constant opposite sample values put a deterministic discontinuity exactly at the cut.
        media=[]
        for index, value in enumerate((.4, -.4)):
            path=self.root/f'dc-{index}.mov'
            ffmpeg('-f','lavfi','-i','color=s=160x96:r=25:d=1','-f','lavfi','-i',
                f'aevalsrc={value}:s=48000:d=1','-c:v','libx264','-c:a','pcm_f32le',path)
            media.append(s.media_import(self.pid,str(path))['asset']['id'])
        clips=[s.clip_add(self.pid, a, 0, 1, transition='none', audio_fade_in_ms=0, audio_fade_out_ms=0)['clip'] for a in media]
        raw=s.timeline_validate(self.pid)
        self.assertIn('audio_join_discontinuity', {i['code'] for i in raw['issues']})
        for c in clips:
            s.clip_update(self.pid,c['id'],audio_fade_in_ms=25,audio_fade_out_ms=25)
        faded=s.timeline_validate(self.pid)
        self.assertNotIn('audio_join_discontinuity', {i['code'] for i in faded['issues']})
        s.clip_update(self.pid,clips[1]['id'],audio_gain_db=-20)
        quiet=s.timeline_validate(self.pid)
        self.assertIn('audio_level_jump', {i['code'] for i in quiet['issues']})
        self.assertTrue(quiet['audio']['checked'])

    def test_labels_dedup_frame_policies_and_overlap(self):
        duplicate=s.media_import(self.pid, self.source.name, base_dir=str(self.root))
        self.assertTrue(duplicate['deduplicated'])
        self.assertEqual(duplicate['asset']['id'],self.asset)
        first=self.add(0,.5,role='establishing',shot_type='wide')
        second=self.add(.4,.9,frame_alignment='floor')
        updated=s.clip_update(self.pid,first['id'],shot_type='close-up',role='custom opening')
        self.assertEqual(updated['clip']['shot_type'],'close-up')
        self.assertEqual(updated['clip']['role'],'custom opening')
        report=s.timeline_validate(self.pid,check_audio=False)
        self.assertIn('source_range_overlap',{i['code'] for i in report['issues']})
        self.assertEqual(report['time_map'][0]['frame_count'],13)
        self.assertEqual(report['time_map'][1]['frame_count'],12)
        self.assertEqual(report['time_map'][1]['start_timecode'],'00:00:00:13')
        with self.assertRaises(ValueError):
            s.clip_update(self.pid,first['id'],audio_gain_db=float('nan'))

    def test_remux_preserves_video_packets_and_existing_destination(self):
        self.add(end=3)
        rendered=s.render_final(self.pid)
        sound=s.render_audio_only(self.pid)
        dest=self.root/'remux.mp4'
        result=s.audio_replace(rendered['path'],sound['path'],str(dest))
        packet_hash=lambda path: ffmpeg('-i',path,'-map','0:v:0','-c:v','copy','-f','hash','-')
        self.assertEqual(packet_hash(dest),packet_hash(rendered['path']))
        self.assertEqual(result['video_codec_mode'],'copy')
        with self.assertRaises(ValueError):
            s.audio_replace(rendered['path'],sound['path'],str(dest))

    def test_repeated_source_cadence_and_missing_tail_are_reported(self):
        repeated=self.root/'repeated.mp4'
        ffmpeg('-f','lavfi','-i','testsrc2=s=160x96:r=8:d=3','-r','24','-c:v','libx264',repeated)
        inspected=s.media_cadence_inspect(str(repeated))
        self.assertGreater(inspected['cadence']['near_duplicate_fraction'],.55)
        self.assertAlmostEqual(inspected['cadence']['estimated_updates_per_second'],8,delta=1)
        missing=self.root/'missing-tail.mp4'
        ffmpeg('-f','lavfi','-i','testsrc2=s=160x96:r=25:d=1','-f','lavfi','-i','sine=duration=3',
            '-c:v','libx264','-c:a','aac',missing)
        self.assertFalse(quality.video_stream(missing)['valid'])
        # A container's longer audio duration must not authorize seconds of
        # frozen picture padding during render.
        broken_asset=s.media_import(self.pid,str(missing))['asset']['id']
        s.clip_add(self.pid,broken_asset,0,3,transition='none')
        with self.assertRaisesRegex(RuntimeError,'video coverage'):
            s.render_final(self.pid)

    def test_mcp_schema_exposes_audio_and_shot_update(self):
        tools=asyncio.run(s.mcp.list_tools())
        update=next(t for t in tools if t.name=='clip_update').inputSchema['properties']
        self.assertTrue({'audio_gain_db','audio_fade_in_ms','audio_fade_out_ms','audio_mute','shot_type','frame_alignment'} <= update.keys())


if __name__ == '__main__':
    unittest.main()
