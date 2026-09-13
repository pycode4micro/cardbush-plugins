import asyncio
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_real_stdio_handshake_and_preview():
    async def run():
        # No real credentials enter the test child process.
        env = {"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"), "ARK_API_KEY": "", "ARK_READ_USER_ENV": "0"}
        parameters = StdioServerParameters(command=sys.executable, args=["-m", "seedream_mcp"], env=env)
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=20)) as session:
                initialized = await session.initialize()
                assert initialized.serverInfo.icons
                assert initialized.serverInfo.icons[0].src.startswith("data:image/png;base64,")
                tools = await session.list_tools()
                assert all(tool.icons == initialized.serverInfo.icons for tool in tools.tools)
                assert {tool.name for tool in tools.tools} == {"seedream_capabilities", "seedream_preview_request", "seedream_generate", "seedance_capabilities", "seedance_preview_request", "seedance_create_task", "seedance_get_task", "video_enhance_capabilities", "video_enhance_preview_request", "video_enhance_upload", "video_enhance_create_task", "video_enhance_get_task"}
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
