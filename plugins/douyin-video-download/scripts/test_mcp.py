"""Real stdio handshake with synthetic cookie files and no network requests."""
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
                download_tools = {'douyin_resolve','douyin_download','douyin_download_candidates'}
                assert {tool.name for tool in listed.tools} == download_tools | {'douyin_login_start','douyin_login_status','douyin_login_cancel'}
                for tool in listed.tools:
                    if tool.name in download_tools:
                        assert 'cookies_file' in tool.inputSchema['properties']
                        assert 'guest' in tool.inputSchema['properties']
                        assert 'cookies_file' not in tool.inputSchema.get('required', [])
                invalid = await session.call_tool('douyin_resolve',{'share_text':'not a share link','guest':True})
                assert invalid.isError
                assert invalid.structuredContent['code'] == 'no_share_url'
                rejected = await session.call_tool('douyin_download_candidates',{
                    'page_url':'https://www.douyin.com/video/1234567890123456789','candidates':[],
                    'output_dir':str(Path(folder)/'download'),'guest':True})
                assert rejected.isError and rejected.structuredContent['code'] == 'invalid_candidates'
                assert not (Path(folder)/'download').exists()
                for name in ('douyin_login_status','douyin_login_cancel'):
                    missing = await session.call_tool(name, {'session_id':'missing-offline-fixture'})
                    assert missing.isError and missing.structuredContent['code'] == 'login_session_missing'
                invalid_wait = await session.call_tool('douyin_login_start', {'timeout_seconds':0})
                assert invalid_wait.isError and invalid_wait.structuredContent['code'] == 'invalid_limit'
                malformed = Path(folder)/'cookies.json'
                malformed.write_text('[{FAKE_MCP_COOKIE', encoding='utf-8')
                invalid_cookie = await session.call_tool('douyin_resolve', {'share_text':'not a share link','cookies_file':str(malformed)})
                assert invalid_cookie.isError and invalid_cookie.structuredContent['code'] == 'invalid_cookies'
                assert 'FAKE_MCP_COOKIE' not in invalid_cookie.model_dump_json()
                relative_cookie = await session.call_tool('douyin_resolve', {'share_text':'not a share link','cookies_file':'cookies.txt'})
                assert relative_cookie.isError and relative_cookie.structuredContent['code'] == 'cookie_file_error'
                for name, args in [
                    ('douyin_download', {'share_text':'not a share link','output_dir':str(Path(folder)/'download')}),
                    ('douyin_download_candidates', {'page_url':'https://www.douyin.com/video/1234567890123456789',
                     'candidates':[{'url':'https://media.example/video.mp4'}],'output_dir':str(Path(folder)/'download')})]:
                    result = await session.call_tool(name, {**args,'cookies_file':str(malformed)})
                    assert result.isError and result.structuredContent['code'] == 'invalid_cookies'
                    assert 'FAKE_MCP_COOKIE' not in result.model_dump_json()
                assert not (Path(folder)/'download').exists()
    print(json.dumps({'stdio':'passed','tools':6,'network_requests':0}))


if __name__ == '__main__':
    asyncio.run(main())
