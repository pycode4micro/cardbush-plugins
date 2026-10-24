import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from video_editer import engine, commerce, hook_framing, delivery_review
from video_editer import mcp_server as m


class FramingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(engine, 'PROJECTS', Path(self.tmp.name))
        self.patch.start()
        self.pid = m.project_create('framing contract')['project_id']
        commerce.start(self.pid, 'account', 'product')

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_default_remains_full_body_and_empty_override_rejected(self):
        self.assertIn('full_body_visible', hook_framing.keys(self.pid, 'hook_picture'))
        with self.assertRaises(ValueError): hook_framing.configure(self.pid, 'garment_display', '')
        self.assertNotIn('hook_framing', commerce.load(self.pid))

    def test_explicit_choice_changes_only_composition_fact(self):
        result = hook_framing.configure(self.pid, 'garment_display', '本次允许无话服装展示，继续试剪')
        self.assertIn('garment_display_visible', result['required_hook_facts'])
        self.assertIn('no_continuous_speaking', result['required_hook_facts'])
        self.assertIn('burned_captions_compatible', result['required_hook_facts'])
        self.assertEqual(hook_framing.keys(self.pid, 'body'), commerce.FACTS['body'])

    def test_rejected_candidate_cannot_be_relabeled_by_changing_framing(self):
        state = commerce.load(self.pid)
        state['reviews']['candidate'] = {'role': 'hook_picture', 'status': 'rejected'}
        commerce.save(self.pid, state)
        with self.assertRaisesRegex(ValueError, 'locked'):
            hook_framing.configure(self.pid, 'garment_display', 'new framing')

    def test_selection_and_output_signature_bind_framing(self):
        timeline = m._read(self.pid)['mcp_timeline']
        before = commerce.base_signature(timeline)
        timeline['commerce_hook_framing'] = {'mode': 'garment_display', 'user_instruction': 'explicit request'}
        self.assertNotEqual(before, commerce.base_signature(timeline))
        state = commerce.load(self.pid); state['selection'] = {'pictures': []}; commerce.save(self.pid, state)
        with self.assertRaisesRegex(ValueError, 'locked'):
            hook_framing.configure(self.pid, 'garment_display', 'explicit request')

    def test_legacy_full_body_signatures_remain_valid(self):
        timeline = m._read(self.pid)['mcp_timeline']
        expected = commerce.signature({'main': [], 'voice': [], 'canvas': timeline['canvas']})
        self.assertEqual(expected, commerce.base_signature(timeline))

    def test_framing_choice_never_waives_speaking_or_actual_review(self):
        evidence = Path(self.tmp.name) / 'observation.txt'
        evidence.write_text('Synthetic review-format fixture, not a real media approval.', encoding='utf8')
        check = {'id': 'hook_picture', 'status': 'pass', 'method': 'continuous_review',
                 'note': 'Synthetic observations', 'evidence_paths': [str(evidence)],
                 'observations': {'garment_display_visible': True, 'natural_display': True,
                                  'no_continuous_speaking': True, 'burned_captions_compatible': True}}
        rule = {'mode': 'garment_display', 'user_instruction': 'Explicit framing choice'}
        rows = delivery_review.normalize_checks([check], {'hook_picture'}, True, rule)
        self.assertEqual(next(r for r in rows if r['id'] == 'hook_picture')['status'], 'not_checked')
        check['observations']['no_continuous_speaking'] = False
        with self.assertRaisesRegex(ValueError, 'no_continuous_speaking'):
            delivery_review.normalize_checks([check], {'hook_picture'}, True, rule)
        check['observations']['no_continuous_speaking'] = True
        with self.assertRaisesRegex(ValueError, 'full_body_visible'):
            delivery_review.normalize_checks([check], {'hook_picture'}, True)


if __name__ == '__main__': unittest.main()
