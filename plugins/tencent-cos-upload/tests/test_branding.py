import base64
import json
import struct
from pathlib import Path

from cos_upload_mcp.branding import plugin_icons

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_logos_exist():
    manifest = json.loads((ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    for name in ("composerIcon", "logo", "logoDark"):
        path = (ROOT / manifest["interface"][name]).resolve()
        assert path.is_relative_to(ROOT)
        assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_portable_mcp_icon():
    icon = plugin_icons()[0]
    assert icon.mimeType == "image/png"
    assert icon.sizes == ["128x128"]
    assert icon.src.startswith("data:image/png;base64,")
    data = base64.b64decode(icon.src.split(",", 1)[1], validate=True)
    assert data == (ROOT / "assets/icon.png").read_bytes()
    assert struct.unpack(">II", data[16:24]) == (128, 128)
