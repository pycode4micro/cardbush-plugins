"""Original ASR coverage/gap contracts; no model inference or editing."""
import unittest
from unittest.mock import patch

from video_editer import speech, speech_timeline


class TimelineTests(unittest.TestCase):
    def value(self, start=10, end=20, words=None):
        return {'source_start': start, 'source_end': end, 'source_duration': 100,
                'asset_id': 'a', 'source_sha256': 'source', 'speech_status': 'speech_detected',
                'words': words or [], 'sentences': []}

    def test_no_speech_gap_is_only_actual_coverage_and_not_approval(self):
        with patch.object(speech, 'report', return_value=(self.value(), 'hash')):
            result = speech_timeline.get('p', 'j')
        gap = result['items'][0]
        self.assertEqual((gap['start'], gap['end']), (10, 20))
        self.assertFalse(gap['audio_silence_verified'])
        self.assertFalse(gap['natural_display_verified'])
        self.assertEqual(result['transcript_sha256'], 'hash')

    def test_overlapping_words_union_and_gap_boundary(self):
        value = self.value(words=[{'start': 11, 'end': 13}, {'start': 12, 'end': 14}, {'start': 16, 'end': 18}])
        with patch.object(speech, 'report', return_value=(value, 'hash')):
            result = speech_timeline.get('p', 'j', 2)
        self.assertEqual([(r['start'], r['end']) for r in result['items']], [(14, 16), (18, 20)])

    def test_sentences_do_not_hide_internal_word_gaps_and_pagination(self):
        value = self.value(words=[{'start': 10, 'end': 12}, {'start': 16, 'end': 20}])
        value['sentences'] = [{'sentence_id': 's', 'start': 10, 'end': 20, 'text': 'full phrase'}]
        with patch.object(speech, 'report', return_value=(value, 'hash')):
            first = speech_timeline.get('p', 'j', limit=1)
            second = speech_timeline.get('p', 'j', offset=1, limit=1)
        self.assertEqual(first['next_offset'], 1)
        self.assertEqual(second['items'][0]['kind'], 'asr_uncovered')
        self.assertIsNone(second['next_offset'])

    def test_evidence_integrity_errors_are_not_swallowed(self):
        with patch.object(speech, 'report', side_effect=ValueError('Transcript evidence changed')):
            with self.assertRaisesRegex(ValueError, 'changed'):
                speech_timeline.get('p', 'j')

    def test_bad_filters_rejected(self):
        for value in (True, float('nan'), float('inf'), 0, 61, '2'):
            with self.assertRaises(ValueError):
                speech_timeline.get('p', 'j', value)
        with self.assertRaises(ValueError):
            speech_timeline.get('p', 'j', offset=-1)


if __name__ == '__main__':
    unittest.main()
