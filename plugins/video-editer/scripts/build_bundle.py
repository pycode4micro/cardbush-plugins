"""Build an allowlisted, independently installable Codex/MCP distribution ZIP."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit
import zipfile


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', type=Path, default=root / 'dist')
    args = parser.parse_args()
    manifest = json.loads((root / '.codex-plugin' / 'plugin.json').read_text(encoding='utf-8'))
    files = [root / name for name in (
        '.codex-plugin/plugin.json', '.mcp.json', 'README.md', 'pyproject.toml',
        'requirements.txt', 'server.py', 'edit_contract.py', 'skills/video-editer/SKILL.md',
        'assets/video_editer-logo-v4.png',
        'assets/logo-market.png', 'assets/icon.png',
        'skills/video-editer/long-media.md',
        'skills/video-editer/audio-and-delivery.md', 'VALIDATION.md',
        'skills/video-editer/resources.md', 'skills/video-editer/generated-hooks.md',
        'skills/baiying-dialogue-video/SKILL.md',
        'skills/baiying-dialogue-video/agents/openai.yaml',
        'skills/baiying-dialogue-video/assets/workflow-plan.json',
        'skills/baiying-dialogue-video/references/components.md',
        'skills/baiying-dialogue-video/references/demonstration-editing.md',
        'skills/baiying-dialogue-video/references/editorial.md',
        'skills/baiying-dialogue-video/references/hook-options.md',
        'skills/baiying-dialogue-video/references/source-audio.md',
        'skills/baiying-dialogue-video/references/typography-layout.md',
        'skills/baiying-dialogue-video/references/typography-sound.md',
        'skills/baiying-dialogue-video/references/quality-and-repair.md',
        'skills/baiying-dialogue-video/references/source-only-stages.md',
        'skills/baiying-dialogue-video/references/generated-route.md',
        'skills/baiying-dialogue-video/references/tested-case.md',
        'skills/baiying-dialogue-video/references/workflow.md',
    )]
    for folder, pattern in [('video_editer', '*.py'), ('video_editer/assets', '*.png'),
                            ('tests', 'test_*.py'), ('scripts', '*.py'),
                            ('skills/baiying-dialogue-video/assets/quality-examples', '*.png'),
                            ('skills/baiying-dialogue-video/assets/quality-examples', '*.jpg'),
                            ('skills/baiying-dialogue-video/assets/quality-examples', '*.mp4'),
                            ('skills/baiying-dialogue-video/assets/quality-examples', '*.json')]:
        files.extend(sorted((root / folder).glob(pattern)))
    files = sorted(set(files))
    if not all(path.is_file() for path in files):
        raise ValueError('A required bundle file is missing')
    packaged = {path.resolve() for path in files}
    for source in files:
        if source.suffix != '.md' or not source.is_relative_to(root / 'skills'):
            continue
        for href in re.findall(r'\[[^\]]+\]\(([^)]+)\)', source.read_text('utf-8')):
            link = urlsplit(href.strip('<>'))
            if link.scheme or link.netloc or not link.path:
                continue
            target = (source.parent / unquote(link.path)).resolve()
            if target not in packaged:
                raise ValueError(f'Skill reference missing from bundle: {source.relative_to(root)} -> {href}')
    checksums = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    target = args.output_dir / f"{manifest['name']}-{manifest['version']}-standalone.zip"
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
