"""Optional MCP entrypoint using the same portable downloader as the Skill."""
from pathlib import Path
import base64
import json
import sys

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations

sys.path.insert(0,str(Path(__file__).resolve().parent/'skills/douyin-video-download/scripts'))
import download_video as downloader
from qr_login import LoginManager

server = FastMCP('douyin-video-download', instructions='Download one authorized Douyin video. For server-side login, use douyin_login_start and show its official QR image to the user; poll status after the user scans and confirms on their phone. Only session_saved confirms that the new session was saved; guest cookies or a displayed QR are not login success. Later downloads reuse this plugin-owned session unless guest=true. Explicit local cookies_file remains an alternative. Never read other browser profiles or expose cookie values. Stop on extra verification; do not solve CAPTCHAs or synthesize private login APIs. Resolve page metadata first; if JS-rendered metadata is unavailable, accept URLs actually observed in the target browser page. Never invent file IDs or bypass access restrictions. Compare candidate resolution/bitrate and verify saved files. Signed URLs must not be published.')
logins = LoginManager()


def invoke(function,*args):
    try:
        return function(*args)
    except downloader.DownloadError as exc:
        detail = {'status':'error','code':exc.code,'message':exc.message}
        return CallToolResult(isError=True,structuredContent=detail,content=[TextContent(type='text',text=exc.message)])


def make_client(cookies_file: str | None, guest: bool = False):
    if cookies_file is not None and guest:
        raise downloader.DownloadError('invalid_auth_options', 'cookies_file 与 guest 不能同时指定')
    if cookies_file is not None and not Path(cookies_file).is_absolute():
        raise downloader.DownloadError('cookie_file_error', 'cookies_file 必须是用户指定的本地文件绝对路径')
    selected = downloader.cookie_file_for_request(Path(cookies_file) if cookies_file is not None else None, guest=guest)
    return downloader.PublicHTTP(cookies_file=selected)


def login_result(state_and_image):
    state, png = state_and_image
    content = [TextContent(type='text', text=json.dumps(state, ensure_ascii=False))]
    if png is not None:
        content.append(ImageContent(type='image', data=base64.b64encode(png).decode('ascii'), mimeType='image/png'))
    return CallToolResult(isError=state['status'] in {'error','timeout','verification_required','login_ui_unavailable'},
                          structuredContent=state, content=content)


@server.tool(annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,openWorldHint=True))
def douyin_login_start(timeout_seconds: int = 180) -> CallToolResult:
    """Start official QR login in an isolated headless browser on the server. Show returned ImageContent to the user for phone scan/confirmation. No desktop or manual Cookie export required. Only session_saved means saved credentials; start/waiting states do not. Login state stays in this plugin's private local directory. Requires optional Playwright Chromium dependencies."""
    return invoke(lambda: login_result(logins.start(timeout_seconds)))


@server.tool(annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,openWorldHint=True))
def douyin_login_status(session_id: str, refresh: bool = False) -> CallToolResult:
    """Read login progress and current official QR image. Poll after the user scans, at most once every few seconds. refresh=true replaces an expired QR, at most three times without extending the session deadline. Never print cookie values or treat guest cookies as a completed login. Extra verification stops the flow."""
    return invoke(lambda: login_result(logins.status(session_id,refresh)))


@server.tool(annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,openWorldHint=False))
def douyin_login_cancel(session_id: str) -> CallToolResult:
    """Cancel this pending login and close its browser; keep any previously saved session unchanged."""
    return invoke(lambda: login_result(logins.cancel(session_id)))


@server.tool(annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False,openWorldHint=True))
def douyin_resolve(share_text: str, cookies_file: str | None = None, guest: bool = False) -> dict:
    """Resolve page metadata; no media download or JavaScript rendering. Reuse this plugin's saved QR login by default; guest=true skips it. Optional cookies_file is a user-selected local Netscape/JSON export, never raw cookie text. metadata_unavailable does not prove cookies are required."""
    return invoke(lambda: {'status':'resolved',**downloader.resolve_share(share_text,make_client(cookies_file,guest))})


@server.tool(annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,openWorldHint=True))
def douyin_download(share_text: str, output_dir: str, min_short_side: int = 0, max_candidates: int = 3, max_mb: int = 1024, cookies_file: str | None = None, guest: bool = False) -> dict:
    """Download and verify the best of at most 1..8 candidates without overwriting. Reuse this plugin's saved QR login by default; guest=true skips it. Optional cookies_file: explicit local Netscape/JSON export. min_short_side=1080 rejects 576p/720p substitutes. Per-candidate max_mb; no decoding/re-encoding."""
    if not Path(output_dir).is_absolute() or not 1 <= max_mb <= 10240:
        raise ValueError('Absolute output_dir and max_mb 1..10240 required')
    def run():
        client = make_client(cookies_file,guest)
        return downloader.download_info(downloader.resolve_share(share_text,client),Path(output_dir),client,max_mb*1024**2,min_short_side,max_candidates)
    return invoke(run)


@server.tool(annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,openWorldHint=True))
def douyin_download_candidates(page_url: str, candidates: list[dict], output_dir: str, min_short_side: int = 0, max_candidates: int = 3, max_mb: int = 1024, cookies_file: str | None = None, guest: bool = False) -> dict:
    """Download actual browser-observed candidates for the target page. Preserve file_id/signature/watermark parameters. Reuse this plugin's QR login unless guest=true. Optional cookies_file imports a user-selected local export; never reads other browser profiles. Caller must verify candidates belong to this video."""
    if not Path(output_dir).is_absolute() or not 1 <= max_mb <= 10240:
        raise ValueError('Absolute output_dir and max_mb 1..10240 required')
    return invoke(lambda: downloader.download_info(downloader.observed_info(page_url,candidates),Path(output_dir),make_client(cookies_file,guest),max_mb*1024**2,min_short_side,max_candidates))


if __name__ == '__main__':
    try:
        server.run()
    finally:
        logins.close()
