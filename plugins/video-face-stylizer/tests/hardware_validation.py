"""Explicit real GPU acceptance using a caller-supplied public portrait fixture.

python tests/hardware_validation.py --portrait portrait.jpg --output NEW_DIRECTORY
Optional --executable exercises the installed portable launcher. No downloads,
private media or machine configuration changes are made by this harness.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import cv2
import numpy as np
from video_face_stylizer.processes import ffmpeg_executable, hidden_process_options


async def face_scaled(source, root, executable):
    # Portable Python's isolated ._pth intentionally omits the script directory.
    spec = importlib.util.spec_from_file_location('vfs_hardware_smoke', Path(__file__).with_name('mcp_smoke.py'))
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    previous = os.environ.get('VFS_TEST_EXECUTABLE')
    if executable:
        os.environ['VFS_TEST_EXECUTABLE'] = str(executable)
    try:
        async with smoke.session() as client:
            job = await smoke.call(client, 'render_video_gpu', {'request': {
                'input_path': str(source), 'output_directory': str(root), 'duration_seconds': .2,
                'start_seconds': .5, 'max_height': 240, 'coverage': 'face'}})
            status = await smoke.poll(client, job)
        result = smoke.check_video(status, 6)
        benchmark = result['benchmark']
        assert (benchmark['width'], benchmark['height']) == (320, 240)
        assert benchmark['faces_detected_frames'] == 6
        assert benchmark['coverage']['covered_frames'] == 6
        assert benchmark['fallback_used'] is False
        assert set(benchmark['pipeline_devices'].values()) == {'gpu'}
        return result
    finally:
        if previous is None:
            os.environ.pop('VFS_TEST_EXECUTABLE', None)
        else:
            os.environ['VFS_TEST_EXECUTABLE'] = previous


def validate(root, portrait, executable=None):
    root.mkdir(parents=True, exist_ok=False)
    plugin = Path(__file__).resolve().parents[1]
    env = {**os.environ, 'PYTHONUTF8': '1', 'VFS_TEST_GPU': '1',
           'VFS_TEST_VIDEO': str(root/'public portrait.mp4'), 'VFS_TEST_OUTPUT': str(root)}
    if executable:
        env['VFS_TEST_EXECUTABLE'] = str(executable)
    def run(name, command, timeout):
        with (root/(name+'.log')).open('w', encoding='utf-8') as log:
            result = subprocess.run(command, cwd=plugin, env=env, stdout=log, stderr=log,
                                    timeout=timeout, **hidden_process_options())
        if result.returncode:
            raise RuntimeError(name+' failed: '+(root/(name+'.log')).read_text(encoding='utf-8',errors='replace')[-3000:])
        print(name+' passed', flush=True)
    run('pytest', [sys.executable, '-m', 'pytest', '-q'], 300)
    integration = [sys.executable, str(plugin/'tests/integration_smoke.py'), '--output', str(root/'integration')]
    integration += ['--executable', str(executable)] if executable else ['--python-entry']
    run('integration', integration, 300)
    run('fixture', [ffmpeg_executable(), '-v', 'error', '-loop', '1', '-i', str(portrait),
        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100', '-t', '30',
        '-vf', 'scale=640:480:force_original_aspect_ratio=decrease,pad=640:480:(ow-iw)/2:(oh-ih)/2',
        '-r', '30', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', env['VFS_TEST_VIDEO']], 90)
    run('lifecycle', [sys.executable, str(plugin/'tests/mcp_smoke.py')], 600)
    complete_acceptance(root, executable)


def complete_acceptance(root, executable=None):
    integration = json.loads((root/'integration/report.json').read_text(encoding='utf-8'))
    assert integration['passed'] and integration['unsupported_gpu_codec_failed_without_fallback']
    scaled = asyncio.run(face_scaled(root/'public portrait.mp4', root, executable))
    reports = list(root.glob('mcp-smoke-*/report.json'))
    if len(reports) != 1:
        raise RuntimeError('Missing or ambiguous lifecycle receipt')
    report = json.loads(reports[0].read_text(encoding='utf-8'))
    assert report['passed']
    for item in report['renders']:
        benchmark = item['benchmark']
        assert benchmark['faces_detected_frames'] > 0, benchmark
        assert benchmark['coverage']['covered_frames'] == 30, benchmark
        assert benchmark['fallback_used'] is False
        assert set(benchmark['pipeline_devices'].values()) == {item['mode']}
        capture = cv2.VideoCapture(item['output'])
        ok, frame = capture.read()
        capture.release()
        assert ok
        cv2.imencode('.png', frame)[1].tofile(root/(item['mode']+'-preview.png'))
    assert report['capabilities']['gpu']['available']
    receipt = {'passed': True, 'capabilities': report['capabilities']['gpu'],
               'pytest': (root/'pytest.log').read_text(encoding='utf-8').strip(),
               'lifecycle_report': str(reports[0]), 'renders': report['renders'], 'scaled_face': scaled}
    (root/'acceptance.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(json.dumps({'passed': True, 'report': str(root/'acceptance.json')}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--portrait', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--executable', type=Path)
    args = parser.parse_args()
    validate(args.output.resolve(), args.portrait.resolve(), args.executable.resolve() if args.executable else None)
