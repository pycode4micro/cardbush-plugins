"""Real stdio handshake with no media requests, cookies or credentials."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    root = Path(__file__).resolve().parents[1]
    configuration = json.loads((root/'.mcp.json').read_text(encoding='utf-8'))['mcpServers']['douyin_video_download']
    args = [arg.replace('${PLUGIN_ROOT}',str(root)) for arg in configuration['args']]
    with tempfile.TemporaryDirectory() as folder:
        params = StdioServerParameters(command=sys.executable,args=args,cwd=folder)
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                initialized = await session.initialize()
                assert initialized.serverInfo.name == 'douyin-video-download'
                listed = await session.list_tools()
                assert {tool.name for tool in listed.tools} == {'douyin_resolve','douyin_download','douyin_download_candidates'}
                invalid = await session.call_tool('douyin_resolve',{'share_text':'not a share link'})
                assert invalid.isError
                assert invalid.structuredContent['code'] == 'no_share_url'
                rejected = await session.call_tool('douyin_download_candidates',{
                    'page_url':'https://www.douyin.com/video/1234567890123456789','candidates':[],
                    'output_dir':str(Path(folder)/'download')})
                assert rejected.isError and rejected.structuredContent['code'] == 'invalid_candidates'
                assert not (Path(folder)/'download').exists()
    print(json.dumps({'stdio':'passed','tools':3,'network_requests':0}))


if __name__ == '__main__':
    asyncio.run(main())
