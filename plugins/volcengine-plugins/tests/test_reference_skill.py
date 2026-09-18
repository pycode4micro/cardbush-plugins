"""Offline packaging and request-contract checks; no upload or paid generation."""
import json
from pathlib import Path
import re
import subprocess
import sys
from zipfile import ZipFile

import pytest
import yaml

from volcengine_plugins.video_client import prepare_video
from volcengine_plugins.video_models import PROFILES, VideoLocalOptions, VideoRequest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/reference-video-production"


def test_skill_metadata_and_local_links():
    manifest = json.loads((ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    assert (ROOT / manifest["skills"]).resolve() == SKILL.parent.resolve()
    body = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(body.split("---", 2)[1])
    assert frontmatter["name"] == SKILL.name
    assert frontmatter["description"]
    assert "TODO" not in body
    for relative in re.findall(r"\]\((references/[^)]+)\)", body):
        target = (SKILL / relative.split("#", 1)[0]).resolve()
        assert target.is_relative_to(SKILL.resolve()) and target.is_file()
    metadata = yaml.safe_load((SKILL / "agents/openai.yaml").read_text(encoding="utf-8"))
    assert "$reference-video-production" in metadata["interface"]["default_prompt"]


def test_package_contains_complete_portable_skill(tmp_path):
    output = tmp_path / "release.zip"
    subprocess.run([sys.executable, str(ROOT / "scripts/package_plugin.py"), str(output)],
                   check=True, capture_output=True)
    with ZipFile(output) as archive:
        assert archive.testzip() is None
        names = set(archive.namelist())
        required = {path.relative_to(ROOT).as_posix() for path in SKILL.rglob("*") if path.is_file()}
        assert len(required) == 5
        assert required <= names
        for name in required:
            assert archive.read(name) == (ROOT / name).read_bytes()
            content = archive.read(name).decode("utf-8")
            assert not re.search(r"[A-Za-z]:[\\/]Users[\\/]|asset-\d{8}|group-\d{8}", content)
        assert not any(Path(name).suffix in {".mp4", ".mp3", ".task", ".tflite"} for name in names)


@pytest.mark.parametrize("profile", PROFILES)
def test_reference_request_mapping_and_audio_are_preserved(profile):
    request = VideoRequest(model=profile, duration=PROFILES[profile]["max_duration"],
        generate_audio=True, content=[
            {"type": "text", "text": "视频1保留节奏，图片1控制商品，音频1仅参考音色。"},
            {"type": "image_url", "image_url": {"url": "https://example.com/product.png"}, "role": "reference_image"},
            {"type": "video_url", "video_url": {"url": "https://example.com/processed.mp4"}, "role": "reference_video"},
            {"type": "audio_url", "audio_url": {"url": "https://example.com/voice.wav"}, "role": "reference_audio"}])
    body, warnings, mapping = prepare_video(request, VideoLocalOptions())
    assert body["generate_audio"] is True
    assert body["content"][2]["video_url"]["url"] == "https://example.com/processed.mp4"
    assert body["content"][2]["role"] == "reference_video"
    assert [item["label"] for item in mapping] == ["图片1", "视频1", "音频1"]
    assert warnings  # Remote media durations are deliberately not claimed verified.
