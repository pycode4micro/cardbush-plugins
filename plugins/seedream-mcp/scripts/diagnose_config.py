"""Offline installed-MCP configuration check. Emits source/boolean, never values.

--user-env-only deliberately omits business variables from child env to test
Windows-user fallback. Does not modify registry, launch generation, or call Ark.
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import timedelta

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from seedream_mcp.config import CONFIG_NAMES, USER_ENV_SWITCH


async def run(user_only: bool):
    env = {USER_ENV_SWITCH: "1"} if user_only else {
        k: os.environ[k] for k in CONFIG_NAMES | {USER_ENV_SWITCH} if k in os.environ
    }
    params = StdioServerParameters(command=sys.executable, args=["-m", "seedream_mcp"], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=20)) as session:
            await session.initialize()
            for tool in ["seedream_capabilities", "seedance_capabilities"]:
                result = await session.call_tool(tool, {})
                if result.isError:
                    print(json.dumps({"tool": tool, "diagnostic_failed": True}))
                    raise SystemExit(1)
                data = result.structuredContent or json.loads(result.content[0].text)
                print(json.dumps({"tool": tool, "configured": data["configured"],
                                  "configuration": data["configuration"], "paid_requests": 0}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-env-only", action="store_true")
    asyncio.run(run(parser.parse_args().user_env_only))
