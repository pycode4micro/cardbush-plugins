import base64
import io
import json
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

from PIL import Image

from seedream_mcp.branding import plugin_icons

ROOT = Path(__file__).resolve().parents[1]


def test_embedded_icon_is_small_portable_png():
    icon, = plugin_icons()
    assert icon.mimeType == "image/png"
    assert icon.sizes == ["128x128"]
    header, encoded = icon.src.split(",", 1)
    assert header == "data:image/png;base64"
    data = base64.b64decode(encoded, validate=True)
    assert len(data) < 64 * 1024
    assert data == (ROOT / "assets/icon.png").read_bytes()
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "PNG"
        assert image.size == (128, 128)
        assert not image.getexif()
        assert not getattr(image, "text", {})


def test_codex_manifest_images_exist():
    manifest = json.loads((ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    for field in ("composerIcon", "logo", "logoDark"):
        relative = manifest["interface"][field]
        assert relative.startswith("./assets/")
        with Image.open(ROOT / relative) as image:
            assert image.format == "PNG"
            assert image.width == image.height


def test_release_contains_manifest_and_runtime_icons(tmp_path):
    output = tmp_path / "plugin.zip"
    subprocess.run([sys.executable, str(ROOT / "scripts/package_plugin.py"), str(output)],
                   check=True, capture_output=True)
    with ZipFile(output) as archive:
        names = set(archive.namelist())
        required = {"assets/logo.png", "assets/icon.png", "src/seedream_mcp/assets/icon.png"}
        assert required <= names
        assert archive.read("assets/icon.png") == archive.read("src/seedream_mcp/assets/icon.png")
        assert not any("__pycache__" in name or ".env" in Path(name).name for name in names)
