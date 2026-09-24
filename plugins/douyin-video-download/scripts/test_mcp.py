"""Real stdio handshake with synthetic Cookie files and no network requests."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    root = Path(__file__).resolve().parents[1]
    configuration = json.loads((root / '.mcp.json').read_text(encoding='utf-8'))['mcpServers']['douyin_video_download']
    args = [arg.replace('${PLUGIN_ROOT}', str(root)) for arg in configuration['args']]
    with tempfile.TemporaryDirectory() as folder:
        params = StdioServerParameters(command=sys.executable, args=args, cwd=folder)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                assert initialized.serverInfo.name == 'douyin-video-download'
                listed = await session.list_tools()
                assert [tool.name for tool in listed.tools] == ['douyin_download']
                schema = listed.tools[0].inputSchema
                assert set(schema['properties']) == {'share_text', 'cookies_file', 'output_dir'}
                assert set(schema['required']) == set(schema['properties'])
                output = str(Path(folder) / 'download')
                base = {'share_text': 'not a share link', 'output_dir': output}
                missing = await session.call_tool('douyin_download', base)
                assert missing.isError
                absent = await session.call_tool('douyin_download', {**base, 'cookies_file': ''})
                assert absent.isError and absent.structuredContent['code'] == 'cookies_required'
                path = Path(folder) / 'cookies.txt'
                path.write_text('Cookie: session_fixture=FAKE_MCP_COOKIE', encoding='utf-8')
                invalid_link = await session.call_tool('douyin_download', {**base, 'cookies_file': str(path)})
                assert invalid_link.isError and invalid_link.structuredContent['code'] == 'no_share_url'
                path.write_text('[{FAKE_MCP_COOKIE', encoding='utf-8')
                malformed = await session.call_tool('douyin_download', {**base, 'cookies_file': str(path)})
                assert malformed.isError and malformed.structuredContent['code'] == 'invalid_cookies'
                assert 'FAKE_MCP_COOKIE' not in malformed.model_dump_json()
                relative = await session.call_tool('douyin_download', {**base, 'cookies_file': 'cookies.txt'})
                assert relative.isError and relative.structuredContent['code'] == 'cookie_file_error'
                invalid_output = await session.call_tool('douyin_download', {**base, 'cookies_file': str(path), 'output_dir': 'relative'})
                assert invalid_output.isError and invalid_output.structuredContent['code'] == 'invalid_output_dir'
                assert not Path(output).exists()
    print(json.dumps({'stdio': 'passed', 'tools': 1, 'network_requests': 0}))


if __name__ == '__main__':
    asyncio.run(main())
