"""Build an allowlisted, independently installable Codex/MCP distribution ZIP."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', type=Path, default=root / 'dist')
    args = parser.parse_args()
    manifest = json.loads((root / '.codex-plugin' / 'plugin.json').read_text(encoding='utf-8'))
    files = [root / name for name in (
        '.codex-plugin/plugin.json', '.mcp.json', 'README.md', 'pyproject.toml',
        'requirements.txt', 'server.py', 'edit_contract.py', 'skills/zj-video-editor/SKILL.md',
        'assets/video_editer-logo-v4.png',
        'skills/zj-video-editor/long-media.md',
    )]
    for folder, pattern in [('video_editer', '*.py'), ('video_editer/assets', '*.png'),
                            ('tests', 'test_*.py'), ('scripts', '*.py')]:
        files.extend(sorted((root / folder).glob(pattern)))
    files = sorted(set(files))
    if not all(path.is_file() for path in files):
        raise ValueError('A required bundle file is missing')
    checksums = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    target = args.output_dir / f"video_editer-{manifest['version']}-standalone.zip"
    # Never silently overwrite a previously handed-off bundle.
    with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in files:
            bundle.write(path, path.relative_to(root).as_posix())
        bundle.writestr('BUNDLE-SHA256.json', json.dumps(checksums, indent=2))
    print(json.dumps({'path': str(target.resolve()), 'files': len(files)+1,
                      'bytes': target.stat().st_size,
                      'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}, indent=2))


if __name__ == '__main__':
    main()
