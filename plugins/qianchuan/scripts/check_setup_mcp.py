"""MCP integration test: invoke using an environment containing the MCP client SDK."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="qianchuan-mcp-setup-") as tmp:
        entry = json.loads((root / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]["qianchuan"]
        env = {**os.environ, "QIANCHUAN_PLUGIN_DATA_DIR": tmp, "QIANCHUAN_SERVICE_ROOT": "Z:/not-a-project"}
        if "--restricted-path" in sys.argv:
            for key in list(env):
                if key.upper() == "PATH":
                    del env[key]
            windows = Path(os.environ.get("SystemRoot", "C:/Windows"))
            env["PATH"] = str(windows / "System32") + ";" + str(windows / "System32/WindowsPowerShell/v1.0")
        params = StdioServerParameters(command=entry["command"], args=[a.replace("${PLUGIN_ROOT}", str(root)) for a in entry["args"]], env=env)
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
    asyncio.run(asyncio.wait_for(main(), timeout=40))
