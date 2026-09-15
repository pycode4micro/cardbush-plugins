"""Isolation and bundled-render regressions; all fixtures are synthetic."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from video_editer import engine
from video_editer import mcp_server as s


class StandaloneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix='video-editer-independent-'))
        cls.media = []
        for index, color in enumerate(('red', 'blue', 'green')):
            path = cls.root / f'{color}.mp4'
            subprocess.run([engine.ffmpeg_bin(), '-y', '-f', 'lavfi', '-i',
                            f'color=c={color}:s=320x480:r=25:d=2',
                            '-f', 'lavfi', '-i', f'sine=frequency={330+index*110}:duration=2',
                            '-c:v', 'libx264', '-c:a', 'aac', str(path)],
                           check=True, capture_output=True, timeout=60)
            cls.media.append(path)
        print('\nINDEPENDENT_REVIEW=' + str(cls.root), flush=True)

    def setUp(self):
        self.project_patch = patch.object(engine, 'PROJECTS', self.root / 'independent data' / 'projects')
        self.project_patch.start()

    def tearDown(self):
        self.project_patch.stop()

    def project(self):
        pid = s.project_create('explicit atomic render', 'Metadata only; do not plan')['project_id']
        assets = [s.media_import(pid, str(path))['asset']['id'] for path in self.media]
        return pid, assets

    def test_no_external_platform_or_model_imports(self):
        package = Path(engine.__file__).parent
        allowed = sys.stdlib_module_names | {'video_editer', 'mcp', 'PIL', 'numpy', 'imageio_ffmpeg'}
        for source in package.glob('*.py'):
            text = source.read_text(encoding='utf-8')
            tree = ast.parse(text)
            imports = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(n.name.split('.')[0] for n in node.names)
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    imports.add((node.module or '').split('.')[0])
            self.assertFalse(imports - allowed, (source.name, imports - allowed))
            self.assertNotIn('sys.path.insert', text)
        self.assertFalse(hasattr(engine, 'ask_seed'))
        self.assertFalse(hasattr(engine, '_layout_product_callouts'))

    def test_every_ffmpeg_command_leaves_mcp_stdin_alone(self):
        for source in Path(engine.__file__).parent.glob('*.py'):
            for node in ast.walk(ast.parse(source.read_text(encoding='utf-8'))):
                if not isinstance(node, ast.List) or not node.elts or not isinstance(node.elts[0], ast.Call):
                    continue
                function = node.elts[0].func
                name = function.id if isinstance(function, ast.Name) else function.attr if isinstance(function, ast.Attribute) else ''
                if name == 'ffmpeg_bin':
                    flags = [e.value for e in node.elts if isinstance(e, ast.Constant)]
                    self.assertIn('-nostdin', flags, (source.name, node.lineno))

    def test_assets_are_owned_by_installed_package(self):
        self.assertEqual(engine.ASSETS.parent, Path(engine.__file__).parent)
        names = {'comic-burst-v3.png', 'number-3d-v3.png', 'point-list-row1.png', 'point-list-row2.png',
                 *{f'water-phase-{i}.png' for i in range(1, 5)}}
        self.assertTrue(names <= {p.name for p in engine.ASSETS.glob('*.png')})
        self.assertTrue(all((engine.ASSETS / n).stat().st_size > 1000 for n in names))

    def test_real_local_assets_and_multiple_transitions(self):
        pid, assets = self.project()
        for asset, transition in zip(assets, ('paint_flash', 'winter_frost_wipe', 'none')):
            s.clip_add(pid, asset, 0, 1.5, transition=transition)
        s.callout_add(pid, '第一点|第二点', .05, 1.6, 'product_left', template='point_list')
        s.callout_add(pid, '测试花字', 1.7, 2.45, 'product_left', template='comic_burst')
        s.callout_add(pid, '重点', 2.5, 3.2, 'product_right', template='number_3d')
        s.subtitle_add(pid, 'Independent plugin / local render', .2, 3.3)
        self.assertTrue(s.timeline_validate(pid)['valid'])
        with patch('socket.socket.connect', side_effect=AssertionError('NO NETWORK AT RUNTIME')):
            result = s.render_final(pid)
        self.assertTrue(result['paint_wipes_applied'])
        self.assertTrue(result['visual_effects_applied'])
        self.assertTrue(result['has_audio'])
        self.assertAlmostEqual(result['quality']['duration'], 3.5, delta=.15)
        self.assertTrue(Path(result['path']).is_relative_to(self.root / 'independent data'))
        saved = json.loads(Path(result['timeline_path']).read_text(encoding='utf-8'))
        self.assertEqual([c['transition'] for c in saved['tracks']['main']],
                         ['paint_flash', 'winter_frost_wipe', 'none'])
        self.assertEqual([e['text'] for e in saved['callouts']], ['第一点|第二点', '测试花字', '重点'])
        work = Path(result['path']).parent
        subprocess.run([engine.ffmpeg_bin(), '-y', '-i', result['path'],
                        '-vf', 'fps=4,scale=162:288,tile=7x2', '-frames:v', '1',
                        str(self.root / 'independent-effects-contact.png')], check=True, capture_output=True, timeout=90)
        self.assertTrue((work / 'base' / 'point-list-plates.mp4').is_file())
        (self.root / 'review-result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')

    def test_short_clips_reject_overlap_without_changing_timeline(self):
        pid, assets = self.project()
        clip = s.clip_add(pid, assets[0], 0, .2, transition='none')['clip']['id']
        s.clip_add(pid, assets[1], 0, 1, transition='none')
        path = engine.project_dir(pid) / 'project.json'
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'too short'):
            s.transition_apply(pid, clip, 'paint_flash')
        self.assertEqual(path.read_bytes(), before)

    def test_short_audio_bed_never_truncates_picture(self):
        pid, assets = self.project()
        s.clip_add(pid, assets[0], 0, 2, transition='none')
        s.clip_add(pid, assets[1], 0, 2, transition='none')
        s.audio_set_bed(pid, assets[2], 1)
        result = s.render_final(pid)
        self.assertAlmostEqual(result['quality']['duration'], 4, delta=.15)
        self.assertTrue(result['continuous_dialogue_bed'])

    def test_engine_accepts_explicit_plan_without_planner_credentials(self):
        pid, assets = self.project()
        plan = {'clips': [{'asset_id': assets[0], 'start': 0, 'end': 1.25,
                           'playback_speed': 1.25, 'transition': 'none'}]}
        with patch('socket.socket.connect', side_effect=AssertionError('NO NETWORK')):
            result = engine.render_project(pid, plan)
        self.assertAlmostEqual(result['quality']['duration'], 1, delta=.1)
        with self.assertRaises(ValueError):
            engine.render_project(pid, {})


if __name__ == '__main__':
    unittest.main()
