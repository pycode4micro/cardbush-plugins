"""Package only reviewed source/docs/manifests, never local configuration or media."""
import argparse
import json
import re
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    files = [root / p for p in (
        ".codex-plugin/plugin.json", ".mcp.json", "pyproject.toml", ".gitignore",
        "README.md", "README.en.md", "examples/generic-mcp.json",
        "scripts/package_plugin.py",
        "assets/logo.png", "assets/icon.png", "src/cos_upload_mcp/assets/icon.png",
    )]
    files += sorted((root / "src" / "cos_upload_mcp").glob("*.py"))
    files += sorted((root / "tests").glob("test_*.py"))
    for path in files:
        if not path.is_file() or not path.resolve().is_relative_to(root):
            raise SystemExit("Package entry missing or outside plugin root")
        if path.suffix == ".png":
            if not path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
                raise SystemExit("Invalid PNG asset")
            continue
        content = path.read_text(encoding="utf-8")
        if re.search(r"(?:[A-Za-z]:[/\\]Users[/\\])|(?:AKID[A-Za-z0-9]{20,})|(?:AKLT[A-Za-z0-9]{20,})", content):
            raise SystemExit("Potential local path or credential found; packaging stopped")
    manifest = json.loads((root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == root.name == "tencent-cos-upload"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(args.output, "x", compression=ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(root).as_posix())
    print(json.dumps({"archive": str(args.output.resolve()), "files": len(files)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
