"""One Cookie-authenticated download tool, shared with the portable CLI."""
from pathlib import Path
import sys

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations

sys.path.insert(0, str(Path(__file__).resolve().parent / 'skills/douyin-video-download/scripts'))
import download_video as downloader

server = FastMCP('douyin-video-download', instructions=(
    'Download one authorized Douyin video using a user-provided Cookie file. '
    'If no file is available, explain how to copy the Cookie request header from an '
    'authenticated www.douyin.com page into a UTF-8 file. Pass only its absolute '
    'path, never Cookie values. Do not attempt guest downloads, scan browser '
    'profiles or create another login flow. The tool handles resolution, bounded '
    'source comparison and file verification; report its actual result.'
))


@server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True))
def douyin_download(share_text: str, cookies_file: str, output_dir: str) -> dict:
    """Download one video from a Douyin link/share text. Required cookies_file: absolute path to a user-provided UTF-8 Cookie header, Netscape or JSON file on this host. Required output_dir: absolute destination directory. No browser/login setup. Selects the best verified source from up to 3 candidates (1 GiB each), checks MP4 tracks/dimensions/duration, and never overwrites. Cookie expiry or page restrictions can still prevent download."""
    try:
        return downloader.download_video(share_text, cookies_file, output_dir)
    except downloader.DownloadError as exc:
        detail = {'status': 'error', 'code': exc.code, 'message': exc.message}
        return CallToolResult(isError=True, structuredContent=detail,
                              content=[TextContent(type='text', text=exc.message)])


if __name__ == '__main__':
    server.run()
