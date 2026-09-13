import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_real_stdio_handshake_no_cloud():
    async def check():
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
               "COS_UPLOAD_READ_USER_ENV": "0", "TENCENT_COS_SECRET_ID": "",
               "TENCENT_COS_SECRET_KEY": ""}
        params = StdioServerParameters(command=sys.executable, args=["-m", "cos_upload_mcp"], env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                assert init.serverInfo.name == "tencent-cos-upload"
                assert init.serverInfo.icons[0].src.startswith("data:image/png;base64,")
                listed = await session.list_tools()
                assert [tool.name for tool in listed.tools] == ["upload_video", "download_object", "delete_object", "rename_object"]
                assert listed.tools[0].icons[0].src == init.serverInfo.icons[0].src
                result = await session.call_tool("upload_video", {"file_path": "missing.mp4"})
                assert "rejected" in str(result.content)
    asyncio.run(check())
