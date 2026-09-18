import asyncio
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def tool_payload(result):
    return result.structuredContent or json.loads(result.content[0].text)


def test_real_stdio_handshake_and_preview():
    async def run():
        # No real credentials enter the test child process.
        env = {"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"), "ARK_API_KEY": "", "MEDIAKIT_API_KEY": "", "ARK_READ_USER_ENV": "0"}
        parameters = StdioServerParameters(command=sys.executable, args=["-m", "volcengine_plugins"], env=env)
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=20)) as session:
                initialized = await session.initialize()
                assert initialized.serverInfo.name == "volcengine-plugins"
                assert initialized.serverInfo.icons
                assert initialized.serverInfo.icons[0].src.startswith("data:image/png;base64,")
                tools = await session.list_tools()
                music_tools = {"music_capabilities", "music_preview_song", "music_preview_bgm", "music_create_song", "music_create_bgm", "music_get_task", "music_download_task"}
                assert music_tools <= {tool.name for tool in tools.tools}
                assert all(tool.icons == initialized.serverInfo.icons for tool in tools.tools)
                assert {tool.name for tool in tools.tools} - music_tools == {"seedream_capabilities", "seedream_preview_request", "seedream_generate", "seedance_capabilities", "seedance_preview_request", "seedance_create_task", "seedance_get_task", "seedance_list_tasks", "seedance_get_tasks", "seedance_download_task", "video_enhance_capabilities", "video_enhance_preview_request", "video_enhance_upload", "video_enhance_create_task", "video_enhance_get_task", "video_subtitle_erase_capabilities", "video_subtitle_erase_preview_request", "video_subtitle_erase_upload", "video_subtitle_erase_create_task", "video_subtitle_erase_get_task", "video_subtitle_erase_download_task", "video_media_preflight", "video_subtitle_erase_plan", "video_subtitle_erase_qc", "video_export_publish"}
                music = await session.call_tool("music_capabilities", {})
                assert not music.isError and tool_payload(music)["model_version"] == "v5.0"
                for name, request, field in [("music_preview_song", {"Lyrics": "[verse]\n晚风陪你走过长街"}, "ModelVersion"),
                                             ("music_preview_bgm", {"Text": "安静的钢琴背景音乐", "Duration": 120}, "Version")]:
                    result = await session.call_tool(name, {"request": request})
                    assert not result.isError
                    assert tool_payload(result)["body"][field] == "v5.0"
                    assert tool_payload(result)["paid_request_sent"] is False
                bad_music = await session.call_tool("music_create_song", {"request": {"Prompt": "private-prompt", "Genre": "Pop"}})
                assert bad_music.isError and tool_payload(bad_music)["paid_request_sent"] is False
                assert "private-prompt" not in json.dumps(tool_payload(bad_music))
                missing_music = await session.call_tool("music_create_song", {"request": {"Prompt": "温暖的民谣歌曲"}})
                assert missing_music.isError and tool_payload(missing_music)["stage"] == "preflight"
                for item in tools.tools:
                    if item.name in {"music_create_song", "music_create_bgm"}:
                        assert item.annotations.readOnlyHint is False and item.annotations.idempotentHint is False
                erased = await session.call_tool("video_subtitle_erase_preview_request", {"request": {"video_url": "https://example.com/a.mp4?secret=hidden"}})
                assert not erased.isError
                erase_payload = erased.structuredContent or json.loads(erased.content[0].text)
                assert erase_payload["body"]["model_version"] == "v5"
                assert erase_payload["paid_request_sent"] is False and "hidden" not in json.dumps(erase_payload)
                invalid_erase = await session.call_tool("video_subtitle_erase_create_task", {"request": {"video_url": "private-source", "fps": 30}})
                assert invalid_erase.isError
                invalid_payload = invalid_erase.structuredContent or json.loads(invalid_erase.content[0].text)
                assert invalid_payload["stage"] == "preflight" and invalid_payload["paid_request_sent"] is False
                assert "private-source" not in json.dumps(invalid_payload)
                missing_erase = await session.call_tool("video_subtitle_erase_create_task", {"request": {"video_url": "https://example.com/a.mp4"}})
                assert missing_erase.isError
                for variant in ["standard", "generative"]:
                    enhanced = await session.call_tool("video_enhance_preview_request", arguments={"request": {
                        "variant":variant,"video_url":"https://example.com/a.mp4","resolution":"1080p"}})
                    assert not enhanced.isError
                    enhanced_payload = enhanced.structuredContent or json.loads(enhanced.content[0].text)
                    assert enhanced_payload["paid_request_sent"] is False
                unsupported = await session.call_tool("video_enhance_preview_request", arguments={"request": {
                    "variant":"professional","video_url":"https://example.com/a.mp4"}})
                assert unsupported.isError
                for model in ["2.0", "2.0-fast", "2.0-mini", "2.5-pro"]:
                    video = await session.call_tool("seedance_preview_request", arguments={"request": {
                        "model": model, "content": [{"type": "text", "text": "海边日落"}], "duration": 4, "generate_audio": True}})
                    assert not video.isError
                    video_payload = video.structuredContent or json.loads(video.content[0].text)
                    assert video_payload["paid_request_sent"] is False
                    assert video_payload["body"]["generate_audio"] is True
                video_missing = await session.call_tool("seedance_create_task", arguments={"request": {"content": [{"type": "text", "text": "test"}]}})
                assert video_missing.isError
                bad_content = await session.call_tool('seedance_create_task', arguments={'request': {'content': [{'text': 'private prompt'}]}})
                assert bad_content.isError
                bad_payload = bad_content.structuredContent or json.loads(bad_content.content[0].text)
                assert bad_payload['paid_request_sent'] is False
                assert bad_payload['billing']['charged'] is False
                assert bad_payload['stage'] == 'preflight'
                assert 'private prompt' not in json.dumps(bad_payload)
                result = await session.call_tool("seedream_preview_request", arguments={
                    "request": {"prompt": "白底羊绒衫商品摄影", "watermark": False},
                    "local": {"aspect_ratio": "3:4", "resolution": "2K"}})
                assert not result.isError
                payload = result.structuredContent
                if not payload:
                    payload = json.loads(result.content[0].text)
                assert payload["paid_request_sent"] is False
                assert payload["body"]["size"] == "1776x2368"
                assert payload["body"]["watermark"] is False
                missing = await session.call_tool("seedream_generate", arguments={"request": {"prompt": "test"}})
                assert missing.isError
    asyncio.run(run())
