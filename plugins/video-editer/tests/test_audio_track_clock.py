"""Different source sample rates must not change an audio event's timeline offset."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
from video_editer import engine, mcp_server as s


class AudioTrackClockTests(unittest.TestCase):
    def test_mixed_rates_keep_speech_windows_and_tail(self):
        with tempfile.TemporaryDirectory(prefix='video-editer-audio-clock-') as folder:
            root=Path(folder)
            with patch.object(engine,'PROJECTS',root/'projects'):
                pid=s.project_create('mixed sample rate timing')['project_id']
                still=root/'still.png';Image.new('RGB',(32,32),'white').save(still)
                image=s.media_import(pid,str(still))['asset']['id']
                s.clip_add(pid,image,0,3,transition='none',audio_mute=True)
                for rate,start in [(44100,.5),(32000,1.8)]:
                    tone=root/f'tone-{rate}.wav'
                    s.processes.run([engine.ffmpeg_bin(),'-nostdin','-v','error','-y','-f','lavfi','-i',
                        f'sine=frequency=431:sample_rate={rate}:duration=1','-c:a','pcm_f32le',str(tone)],
                        capture_output=True,check=True,timeout=60)
                    asset=s.media_import(pid,str(tone))['asset']['id']
                    s.audio_track_add(pid,asset,start=start,source_start=.1,duration=.4)
                result=s.render_audio_only(pid)
                decoded=s.processes.run([engine.ffmpeg_bin(),'-nostdin','-v','error','-i',result['path'],
                    '-ac','1','-ar','48000','-f','f32le','-'],capture_output=True,check=True,timeout=60)
                samples=np.frombuffer(decoded.stdout,dtype='<f4')
                self.assertEqual(len(samples),3*48000)
                for start in [.5,1.8]:
                    region=samples[round((start-.1)*48000):round((start+.5)*48000)]
                    active=np.flatnonzero(np.abs(region)>.002)
                    self.assertGreater(len(active),100)
                    onset=start-.1+active[0]/48000
                    offset=start-.1+active[-1]/48000
                    self.assertAlmostEqual(onset,start,delta=.002)
                    self.assertAlmostEqual(offset,start+.4,delta=.002)
                self.assertLess(float(np.max(np.abs(samples[round(2.3*48000):]))),.0001)


if __name__=='__main__':unittest.main()
