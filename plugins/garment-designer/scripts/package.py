"""Build an allowlisted, source-included, dependency-bundled plugin ZIP."""
import json
import sys
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

root = Path(sys.argv[1]).resolve()
manifest = json.loads((root / '.codex-plugin/plugin.json').read_text(encoding='utf-8'))
target = root / 'release' / f"garment-designer-{manifest['version']}.zip"
target.parent.mkdir(exist_ok=True)
files = [root / f for f in ['.mcp.json', 'package.json', 'package-lock.json', 'README.md', 'TESTING.md', 'THIRD_PARTY_NOTICES.txt']]
for folder in ['.codex-plugin', 'skills', 'assets', 'dist', 'src', 'ui', 'scripts', 'test', 'third-party']:
    files.extend(p for p in (root / folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts)
with ZipFile(target, 'w', ZIP_DEFLATED) as archive:
    for filename in sorted(files):
        archive.write(filename, filename.relative_to(root).as_posix())
with ZipFile(target) as archive:
    assert '.codex-plugin/plugin.json' in archive.namelist()
    assert 'dist/index_bg.wasm' in archive.namelist()
    assert not any(name.startswith(('node_modules/', '.test-data/')) for name in archive.namelist())
print(f'Packaged {target} ({target.stat().st_size:,} bytes)')
