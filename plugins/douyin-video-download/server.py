"""Optional MCP entrypoint using the same portable downloader as the Skill."""
from pathlib import Path
import sys

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations

sys.path.insert(0,str(Path(__file__).resolve().parent/'skills/douyin-video-download/scripts'))
import download_video as downloader

server = FastMCP('douyin-video-download', instructions='Download one authorized Douyin video. Resolve public metadata first; if JS-rendered metadata is unavailable, accept explicitly supplied URLs actually observed in the target browser page. Never collect browser cookies, invent file IDs or bypass access restrictions. Compare candidate resolution/bitrate and verify the saved file. Browser currentSrc is not proof of highest quality. Resolved URLs may be signed/private; do not publish them.')


def invoke(function,*args):
    try:
        return function(*args)
    except downloader.DownloadError as exc:
        detail = {'status':'error','code':exc.code,'message':exc.message}
        return CallToolResult(isError=True,structuredContent=detail,content=[TextContent(type='text',text=exc.message)])


@server.tool(annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False,openWorldHint=True))
def douyin_resolve(share_text: str) -> dict:
    """Resolve public page metadata and ranked video candidates; no media download. Does not execute browser JavaScript. If metadata_unavailable, use authorized browser observations or an existing file."""
    return invoke(lambda: {'status':'resolved',**downloader.resolve_share(share_text,downloader.PublicHTTP())})


@server.tool(annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,openWorldHint=True))
def douyin_download(share_text: str, output_dir: str, min_short_side: int = 0, max_candidates: int = 3, max_mb: int = 1024) -> dict:
    """Download and compare at most 1..8 candidates, verify MP4/tracks/size/dimensions/duration/hash, save best verified source without overwriting. Set min_short_side=1080 to reject 576p/720p substitutes. Per-candidate max_mb; no decoding/re-encoding."""
    if not Path(output_dir).is_absolute() or not 1 <= max_mb <= 10240:
        raise ValueError('Absolute output_dir and max_mb 1..10240 required')
    client = downloader.PublicHTTP()
    return invoke(lambda: downloader.download_info(downloader.resolve_share(share_text,client),Path(output_dir),client,max_mb*1024**2,min_short_side,max_candidates))


@server.tool(annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,openWorldHint=True))
def douyin_download_candidates(page_url: str, candidates: list[dict], output_dir: str, min_short_side: int = 0, max_candidates: int = 3, max_mb: int = 1024) -> dict:
    """Download actual browser-observed public candidates for the specified target page. Each object: url and optional width/height/bitrate/bytes/source. Preserve exact file_id/signature/watermark parameters; never synthesize them. Metadata is a ranking hint; saved bytes are measured. No cookies/browser-profile access. Caller must verify all candidates belong to this video."""
    if not Path(output_dir).is_absolute() or not 1 <= max_mb <= 10240:
        raise ValueError('Absolute output_dir and max_mb 1..10240 required')
    return invoke(lambda: downloader.download_info(downloader.observed_info(page_url,candidates),Path(output_dir),downloader.PublicHTTP(),max_mb*1024**2,min_short_side,max_candidates))


if __name__ == '__main__':
    server.run()
