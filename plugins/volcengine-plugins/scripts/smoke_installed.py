"""Exercise installed runtime from the actual plugin command; never call paid API."""
import asyncio
import json
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["volcengine"]
    parameters = StdioServerParameters(command=config["command"], args=config["args"], env={"ARK_API_KEY": "", "MEDIAKIT_API_KEY": "", "ARK_READ_USER_ENV": "0"})
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=20)) as session:
            initialized = await session.initialize()
            assert initialized.serverInfo.name == "volcengine-plugins"
            assert initialized.serverInfo.icons
            assert initialized.serverInfo.icons[0].src.startswith("data:image/png;base64,")
            result = await session.call_tool("seedream_preview_request", {
                "request": {"prompt": "产品摄影", "size": "2K"}})
            assert not result.isError
            tools = await session.list_tools()
            assert len(tools.tools) == 21
            assert all(tool.icons == initialized.serverInfo.icons for tool in tools.tools)
            for model in ["2.0", "2.0-fast", "2.0-mini", "2.5-pro"]:
                preview = await session.call_tool("seedance_preview_request", {"request": {
                    "model": model, "content": [{"type": "text", "text": "海边日落"}], "duration": 4, "generate_audio": True}})
                assert not preview.isError
                payload = preview.structuredContent or json.loads(preview.content[0].text)
                assert payload["paid_request_sent"] is False
            erased = await session.call_tool("video_subtitle_erase_preview_request", {"request": {
                "video_url": "https://example.com/input.mp4"}})
            assert not erased.isError
            erase_payload = erased.structuredContent or json.loads(erased.content[0].text)
            assert erase_payload["body"]["model_version"] == "v5"
            assert erase_payload["body"]["mode"] == "Subtitle"
            assert erase_payload["paid_request_sent"] is False
            print(json.dumps({"installed_runtime": "passed", "tools": [tool.name for tool in tools.tools],
                              "paid_requests": 0}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
