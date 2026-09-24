#!/usr/bin/env python3
"""Create a portable plugin ZIP from an explicit source-file allowlist."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SKILL = "skills/douyin-video-download/"
FILES = (
    ".codex-plugin/plugin.json",
    "README.md",
    ".mcp.json",
    "server.py",
    "requirements.txt",
    "scripts/test_mcp.py",
    "scripts/build_release.py",
    SKILL + "SKILL.md",
    SKILL + "agents/openai.yaml",
    SKILL + "scripts/download_video.py",
    SKILL + "scripts/test_download_video.py",
    SKILL + "scripts/test_cookies.py",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    if output.exists():
        parser.error("output already exists; choose a new filename")
    payloads = [(name, (ROOT / name).read_bytes()) for name in FILES]
    manifest = json.loads(payloads[0][1])
    if manifest["name"] != ROOT.name:
        parser.error("plugin folder and manifest name differ")
    output.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            created = True
            for name, data in payloads:
                entry = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.external_attr = 0o100644 << 16
                archive.writestr(entry, data)
        with zipfile.ZipFile(output) as archive:
            if archive.testzip() is not None or set(archive.namelist()) != set(FILES):
                raise ValueError("archive verification failed")
    except BaseException:
        if created:
            output.unlink(missing_ok=True)
        raise
    print(json.dumps({"file_path": str(output), "files": len(FILES),
                      "version": manifest["version"],
                      "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
