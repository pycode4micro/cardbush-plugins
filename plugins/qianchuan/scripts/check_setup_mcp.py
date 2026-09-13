"""MCP integration test: invoke using an environment containing the MCP client SDK."""
import asyncio
import json
import os
from pathlib import Path
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="qianchuan-mcp-setup-") as tmp:
        params = StdioServerParameters(command="uv", args=["run", "--frozen", "--script", str(root / "scripts/launch.py")],
            env={**os.environ, "QIANCHUAN_PLUGIN_DATA_DIR": tmp, "QIANCHUAN_SERVICE_ROOT": "Z:/not-a-project"})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                tools = await client.list_tools()
                assert [t.name for t in tools.tools] == ["qianchuan_setup_status"]
                result = await client.call_tool("qianchuan_setup_status", {})
                assert not result.isError
                assert result.structuredContent["status"] == "setup_required"
                print(json.dumps({"mcp_setup": "passed", "independent_runtime": True}))


if __name__ == "__main__":
    asyncio.run(main())
