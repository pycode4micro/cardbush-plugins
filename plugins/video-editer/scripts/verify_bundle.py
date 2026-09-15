"""Relocate a bundle and verify wheel installation plus rendering in a clean venv.

Requires package-index access only for installation; artifacts are retained.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import venv
import zipfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('bundle', type=Path)
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix='video editer relocated '))
    extracted = root / 'plugin source'
    with zipfile.ZipFile(args.bundle.resolve()) as archive:
        for member in archive.namelist():
            if not (extracted / member).resolve().is_relative_to(extracted.resolve()):
                raise ValueError('Unsafe bundle entry')
        archive.extractall(extracted)
    checksums = json.loads((extracted / 'BUNDLE-SHA256.json').read_text(encoding='utf-8'))
    for name, expected in checksums.items():
        if hashlib.sha256((extracted / name).read_bytes()).hexdigest() != expected:
            raise ValueError('Bundle checksum mismatch: ' + name)
    environment = root / 'clean python'
    print('RELOCATION_ROOT=' + str(root), flush=True)
    venv.EnvBuilder(with_pip=True, system_site_packages=False).create(environment)
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    env = {k: v for k, v in os.environ.items() if k.upper() not in
           {'PYTHONPATH', 'PYTHONHOME', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'}}
    env['NO_PROXY'] = '*'
    subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check', str(extracted)],
                   env=env, check=True, timeout=300)
    # Smoke client runs outside both the original plugin and the extracted source.
    result = subprocess.run([str(python), str(extracted / 'scripts' / 'smoke_client.py'), str(root / 'review')],
                            env=env, cwd=root, capture_output=True, text=True, timeout=300)
    (root / 'smoke-stdout.log').write_text(result.stdout, encoding='utf-8')
    (root / 'smoke-stderr.log').write_text(result.stderr, encoding='utf-8')
    print(result.stdout, flush=True)
    if result.returncode:
        print(result.stderr, flush=True)
        result.check_returncode()
    print('VERIFIED_REPORT=' + str(root / 'review' / 'verification.json'), flush=True)


if __name__ == '__main__':
    main()
