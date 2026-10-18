"""Small synthetic audio-only clock test. No video is edited or encoded."""
import tempfile
import unittest
from pathlib import Path

from video_editer import engine, processes, resources, speech
from video_editer.speech_worker import load_pcm


class SpeechClockTests(unittest.TestCase):
    def test_pcm_preserves_delayed_audio_container_origin_and_trim_offset(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as folder, resources.heavy_slot():
            root=Path(folder);source=root/'delayed.mka'
            # Absolute audio PTS starts at 2.5s; conceptual video container starts
            # at 2s. ASR must keep the .5s visual-relative silence, not shift speech.
            processes.run([engine.ffmpeg_bin(),'-nostdin','-v','error','-n','-f','lavfi','-i',
                'sine=frequency=440:sample_rate=16000:duration=2','-af','asetpts=PTS+2.5/TB',
                '-c:a','pcm_s16le','-copyts',str(source)],capture_output=True,check=True,timeout=30)
            full=root/'full.wav';speech.extract_audio(source,full,2,0,3)
            samples=load_pcm(full)
            self.assertEqual(len(samples),48000)
            self.assertLess(float(np.max(np.abs(samples[:7900]))),.0001)
            self.assertGreater(float(np.max(np.abs(samples[8100:16000]))),.05)
            self.assertLess(float(np.max(np.abs(samples[40500:]))),.0001)
            trimmed=root/'trimmed.wav';speech.extract_audio(source,trimmed,2,.25,1.25)
            selected=load_pcm(trimmed)
            self.assertEqual(len(selected),16000)
            self.assertLess(float(np.max(np.abs(selected[:3900]))),.0001)
            self.assertGreater(float(np.max(np.abs(selected[4100:]))),.05)


if __name__=='__main__':unittest.main()
