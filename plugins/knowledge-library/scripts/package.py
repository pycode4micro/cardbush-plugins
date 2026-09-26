"""Package only source, bundled runtime, documentation and synthetic examples."""
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

root = Path(__file__).resolve().parent.parent
version = json.loads((root / 'package.json').read_text(encoding='utf-8'))['version']
target = root / 'release' / f'knowledge-library-{version}-demo.zip'
target.parent.mkdir(exist_ok=True)
files = [root / name for name in ['plugin.json', 'mcp.json', '.mcp.json', 'package.json', 'package-lock.json', 'README.md', 'THIRD_PARTY_NOTICES.txt']]
for folder in ['.codex-plugin', 'skills', 'assets', 'runtime', 'src', 'ui', 'scripts', 'test']:
    files.extend(p for p in (root / folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts)
assert (root / 'runtime/server.mjs').is_file()
with ZipFile(target, 'w', ZIP_DEFLATED) as archive:
    for filename in sorted(files):
        archive.write(filename, filename.relative_to(root).as_posix())
with ZipFile(target) as archive:
    names = archive.namelist()
    assert 'runtime/server.mjs' in names and 'runtime/extract-worker.mjs' in names
    assert not any(name.startswith(('node_modules/', '.demo-data/', '.test-data/')) or '.sqlite' in name for name in names)
print(f'{target} ({target.stat().st_size:,} bytes)')
