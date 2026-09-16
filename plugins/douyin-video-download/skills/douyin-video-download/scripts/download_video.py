#!/usr/bin/env python3
"""Download one public Douyin share video; Python 3.10+, standard library only."""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import struct
import sys
import tempfile
from html.parser import HTMLParser
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
    "Mobile/15E148 Safari/604.1"
)
DESKTOP_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
ID_PATTERN = re.compile(r"[0-9]{15,24}\Z")
URL_PATTERN = re.compile(r'''https?://[^\s<>"'`\[\]()（）【】，。]+''', re.I)
STATE_PATTERN = re.compile(
    r"(?:window\.)?(?:_ROUTER_DATA|__INIT_PROPS__|__INITIAL_STATE__|"
    r"routeInitialProps|__NEXT_DATA__)\s*=\s*"
)
MAX_HTML_BYTES = 8 * 1024 * 1024
MAX_REDIRECTS = 6


class DownloadError(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


def is_douyin_host(host: str) -> bool:
    return any(host == root or host.endswith("." + root)
               for root in ("douyin.com", "iesdouyin.com"))


def validate_url(url: str, *, page: bool, check_dns: bool = False) -> str:
    if len(url) > 16384 or re.search(r"[\x00-\x20\x7f]", url):
        raise DownloadError("invalid_url", "链接包含不支持的字符或过长")
    try:
        parsed = urlsplit(url)
        host, port = (parsed.hostname or "").lower(), parsed.port
    except ValueError:
        raise DownloadError("invalid_url", "链接格式无效") from None
    if (parsed.scheme not in {"http", "https"} or not host
            or parsed.username is not None or parsed.password is not None
            or port not in {None, 80, 443}):
        raise DownloadError("invalid_url", "仅支持不含账号信息的标准 HTTP(S) 链接")
    if page and not is_douyin_host(host):
        raise DownloadError("unsupported_host", "分享页面必须位于抖音官方域名")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if host == "localhost" or host.endswith((".localhost", ".local")):
        raise DownloadError("unsafe_url", "不访问本机或局域网地址")
    if address is not None and not address.is_global:
        raise DownloadError("unsafe_url", "不访问非公网 IP 地址")
    # Use encrypted transport; never downgrade a redirect to HTTP.
    authority = f"[{host}]" if isinstance(address, ipaddress.IPv6Address) else host
    normalized = urlunsplit(("https", authority, parsed.path or "/", parsed.query, ""))
    if check_dns:
        try:
            addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        except OSError:
            raise DownloadError("network_error", "无法解析远端主机，请检查网络") from None
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global
                                for item in addresses):
            raise DownloadError("unsafe_url", "目标主机解析到了非公网地址")
    return normalized


def extract_share_url(text: str) -> str:
    found = []
    for token in URL_PATTERN.findall(text):
        token = token.rstrip(".,;；!！?？\\")
        try:
            host = (urlsplit(token).hostname or "").lower()
        except ValueError:
            continue
        if is_douyin_host(host):
            value = validate_url(token, page=True)
            if value not in found:
                found.append(value)
    if not found:
        raise DownloadError("no_share_url", "未找到有效的抖音分享链接")
    if len(found) > 1:
        raise DownloadError("multiple_urls", "发现多条不同的抖音链接，请一次选择一条")
    return found[0]


def extract_video_id(url: str) -> str | None:
    parsed = urlsplit(url)
    if re.search(r"/(?:share/)?note/", parsed.path):
        raise DownloadError("unsupported_content", "此链接是图文页面，本 Skill 只下载视频")
    match = re.search(r"/(?:share/)?video/([0-9]{15,24})(?:/|$)", parsed.path)
    if match:
        return match[1]
    query = parse_qs(parsed.query)
    for key in ("modal_id", "aweme_id", "item_id"):
        for value in query.get(key, []):
            if ID_PATTERN.fullmatch(value):
                return value
    return None


class CheckedRedirect(HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS
    max_repeats = 2

    def __init__(self, page: bool):
        self.page = page

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        newurl = validate_url(urljoin(req.full_url, newurl), page=self.page, check_dns=True)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class PublicHTTP:
    """No cookies, account sessions, browser profiles or third-party resolvers."""
    def __init__(self, timeout: float = 30):
        self.timeout = timeout

    def open(self, url: str, *, page: bool, user_agent: str = USER_AGENT):
        url = validate_url(url, page=page, check_dns=True)
        headers = {"User-Agent": user_agent, "Accept-Encoding": "identity"}
        if not page:
            headers["Referer"] = "https://www.douyin.com/"
        opener = build_opener(CheckedRedirect(page))
        try:
            return opener.open(Request(url, headers=headers), timeout=self.timeout)
        except HTTPError as exc:
            status = exc.code
            exc.close()
            if status in {401, 403}:
                raise DownloadError("access_required", "远端拒绝访问；请提供可公开访问的链接或已有视频") from None
            if status == 429:
                raise DownloadError("rate_limited", "远端限流，请稍后再试，不要反复请求") from None
            raise DownloadError("http_error", f"远端返回 HTTP {status}") from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise DownloadError("network_error", "网络请求失败，请检查网络或稍后再试") from None

    def page(self, url: str, user_agent: str = USER_AGENT) -> tuple[str, str]:
        try:
            with self.open(url, page=True, user_agent=user_agent) as response:
                body = response.read(MAX_HTML_BYTES + 1)
                if len(body) > MAX_HTML_BYTES:
                    raise DownloadError("page_too_large", "分享页超过解析大小限制")
                final_url = validate_url(response.geturl(), page=True)
                return final_url, body.decode("utf-8", errors="replace")
        except (HTTPException, OSError):
            raise DownloadError("network_error", "分享页响应读取失败或不完整") from None


class ScriptCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.scripts: list[tuple[dict, str]] = []
        self.current: tuple[dict, list[str]] | None = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "script":
            self.current = (dict(attrs), [])

    def handle_data(self, data):
        if self.current is not None:
            self.current[1].append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "script" and self.current is not None:
            self.scripts.append((self.current[0], "".join(self.current[1])))
            self.current = None


def parse_states(html: str) -> list:
    collector = ScriptCollector()
    collector.feed(html)
    decoder = json.JSONDecoder()
    result = []
    for attrs, source in collector.scripts:
        candidates = []
        script_id = attrs.get("id", "")
        if script_id == "RENDER_DATA":
            candidates.append(unquote(source).lstrip())
        elif script_id in {"__NEXT_DATA__", "__INITIAL_STATE__"} or attrs.get("type") == "application/json":
            candidates.append(source.lstrip())
        candidates.extend(source[match.end():].lstrip() for match in STATE_PATTERN.finditer(source))
        for candidate in candidates:
            try:
                if candidate.startswith("JSON.parse("):
                    encoded, _ = decoder.raw_decode(candidate[len("JSON.parse("):].lstrip())
                    value = json.loads(encoded) if isinstance(encoded, str) else None
                else:
                    value, _ = decoder.raw_decode(candidate)
                if isinstance(value, (dict, list)):
                    result.append(value)
            except (ValueError, TypeError, RecursionError):
                continue
    return result


def walk_dicts(value):
    pending = [value]
    visited = 0
    while pending:
        node = pending.pop()
        visited += 1
        if visited > 200000:
            raise DownloadError("metadata_too_large", "页面数据过于复杂")
        if isinstance(node, dict):
            yield node
            pending.extend(reversed(list(node.values())))
        elif isinstance(node, list):
            pending.extend(reversed(node))


def address_urls(value) -> list[str]:
    if isinstance(value, str):
        return [value] if value.startswith(("https://", "http://", "//")) else []
    if isinstance(value, list):
        return [url for child in value for url in address_urls(child)]
    if isinstance(value, dict):
        return address_urls(value.get("url_list") or value.get("urlList") or value.get("url") or [])
    return []


def extract_info(states: list, video_id: str) -> dict | None:
    matched_nonvideo = False
    for node in walk_dicts(states):
        ids = [str(node[key]) for key in ("aweme_id", "awemeId", "item_id", "itemId", "id") if key in node]
        if video_id not in ids:
            continue
        video = node.get("video") or node.get("video_info") or node.get("videoInfo")
        if not isinstance(video, dict):
            matched_nonvideo = matched_nonvideo or bool(node.get("images"))
            continue
        addresses = []
        # Do not search arbitrary descendants: those may contain music or recommendations.
        for key in ("play_addr", "playAddr", "play_addr_h264", "play_addr_265", "playApi"):
            addresses.extend(address_urls(video.get(key)))
        variants = video.get("bit_rate") or video.get("bitRate") or []
        if isinstance(variants, list):
            for variant in variants:
                if isinstance(variant, dict):
                    addresses.extend(address_urls(variant.get("play_addr") or variant.get("playAddr")))
        for key in ("play_addr_lowbr", "download_addr", "downloadAddr"):
            addresses.extend(address_urls(video.get(key)))
        urls = []
        for address in addresses:
            if address.startswith("//"):
                address = "https:" + address
            try:
                normalized = validate_url(address, page=False)
            except DownloadError:
                continue
            if urlsplit(normalized).path.lower().endswith((".m3u8", ".mpd")):
                continue
            if normalized not in urls:
                urls.append(normalized)
        if not urls:
            continue
        author = node.get("author") or {}
        return {
            "video_id": video_id,
            "title": str(node.get("desc") or node.get("title") or video_id),
            "author": str(author.get("nickname") or "") if isinstance(author, dict) else "",
            "page_url": f"https://www.douyin.com/video/{video_id}",
            "download_urls": urls,
        }
    if matched_nonvideo:
        raise DownloadError("unsupported_content", "目标是图文内容，不是可直接下载的视频")
    return None


def resolve_share(text: str, client: PublicHTTP) -> dict:
    source_url = extract_share_url(text)
    source_id = extract_video_id(source_url)
    final_url, html = client.page(source_url)
    final_id = extract_video_id(final_url)
    if source_id and final_id and source_id != final_id:
        raise DownloadError("id_mismatch", "页面跳转改变了目标视频 ID，已停止")
    video_id = final_id or source_id
    if not video_id:
        raise DownloadError("id_unavailable", "分享页跳转后仍没有明确的视频 ID")
    info = extract_info(parse_states(html), video_id)
    if info:
        return info
    pages = [
        (f"https://www.iesdouyin.com/share/video/{video_id}/", USER_AGENT),
        (f"https://www.douyin.com/video/{video_id}", DESKTOP_AGENT),
    ]
    for page_url, agent in pages:
        if urlsplit(page_url).path.rstrip("/") == urlsplit(final_url).path.rstrip("/") and urlsplit(page_url).hostname == urlsplit(final_url).hostname:
            continue
        try:
            resolved, html = client.page(page_url, agent)
            actual = extract_video_id(resolved)
            if actual and actual != video_id:
                raise DownloadError("id_mismatch", "备用页面返回了其他视频，已停止")
            info = extract_info(parse_states(html), video_id)
            if info:
                return info
        except DownloadError as exc:
            if exc.code not in {"network_error", "http_error"}:
                raise
    raise DownloadError("metadata_unavailable", "未找到目标视频的公开播放地址；可能涉及登录验证、不可用内容或页面结构变化")


def boxes(handle, start: int, end: int):
    offset, count = start, 0
    while offset < end:
        count += 1
        if count > 100000 or end - offset < 8:
            raise DownloadError("invalid_video", "MP4 容器结构不完整")
        handle.seek(offset)
        data = handle.read(8)
        if len(data) != 8:
            raise DownloadError("invalid_video", "MP4 容器头读取不完整")
        size, kind = struct.unpack(">I4s", data)
        header = 8
        if size == 1:
            if end - offset < 16:
                raise DownloadError("invalid_video", "MP4 扩展容器头不完整")
            data = handle.read(8)
            if len(data) != 8:
                raise DownloadError("invalid_video", "MP4 扩展容器头读取不完整")
            size = struct.unpack(">Q", data)[0]
            header = 16
        elif size == 0:
            size = end - offset
        if size < header or offset + size > end:
            raise DownloadError("invalid_video", "MP4 容器长度无效，文件可能未下载完整")
        yield kind, offset + header, offset + size
        offset += size


def inspect_mp4(path: Path) -> dict:
    tracks = []
    duration = None
    width = height = None
    with path.open("rb") as handle:
        roots = list(boxes(handle, 0, path.stat().st_size))
        kinds = {kind for kind, _, _ in roots}
        if not {b"ftyp", b"moov", b"mdat"} <= kinds or not any(kind == b"mdat" and end > start for kind, start, end in roots):
            raise DownloadError("invalid_video", "下载内容不是完整的 MP4 视频容器")
        for kind, start, end in roots:
            if kind != b"moov":
                continue
            for child, left, right in boxes(handle, start, end):
                if child == b"mvhd":
                    handle.seek(left)
                    header = handle.read(min(36, right - left))
                    if len(header) >= 20 and header[0] == 0:
                        scale, ticks = struct.unpack(">II", header[12:20])
                        duration = ticks / scale if scale and ticks != 0xFFFFFFFF else None
                    elif len(header) >= 32 and header[0] == 1:
                        scale, ticks = struct.unpack(">IQ", header[20:32])
                        duration = ticks / scale if scale and ticks != 0xFFFFFFFFFFFFFFFF else None
                if child != b"trak":
                    continue
                track_kind, dimensions = None, None
                for item, a, b in boxes(handle, left, right):
                    if item == b"tkhd" and b - a >= 84:
                        handle.seek(b - 8)
                        x, y = struct.unpack(">II", handle.read(8))
                        dimensions = (round(x / 65536), round(y / 65536))
                    if item == b"mdia":
                        for sub, c, d in boxes(handle, a, b):
                            if sub == b"hdlr" and d - c >= 12:
                                handle.seek(c + 8)
                                track_kind = handle.read(4)
                if track_kind:
                    tracks.append(track_kind)
                if track_kind == b"vide" and dimensions:
                    width, height = dimensions
    if b"vide" not in tracks:
        raise DownloadError("invalid_video", "容器没有视频轨道，未作为视频保存")
    return {"validation": "mp4_structure_and_video_track", "has_video": True,
            "has_audio": b"soun" in tracks, "duration_seconds": duration,
            "width": width, "height": height}


def publish_new(source: Path, target: Path) -> None:
    try:
        os.link(source, target)
        return
    except FileExistsError:
        raise DownloadError("output_exists", "目标文件已存在，不覆盖，请选择其他输出目录") from None
    except OSError:
        # Some filesystems do not support hardlinks. Exclusive creation still avoids overwrites.
        created = False
        try:
            with target.open("xb") as dest:
                created = True
                with source.open("rb") as src:
                    shutil.copyfileobj(src, dest)
        except FileExistsError:
            raise DownloadError("output_exists", "目标文件已存在，不覆盖，请选择其他输出目录") from None
        except BaseException:
            if created:
                target.unlink(missing_ok=True)
            raise


def download_info(info: dict, output_dir: Path, client: PublicHTTP, max_bytes: int) -> dict:
    if not ID_PATTERN.fullmatch(str(info.get("video_id", ""))):
        raise DownloadError("invalid_video_id", "视频 ID 无效")
    if max_bytes < 1:
        raise DownloadError("invalid_limit", "下载大小上限必须至少为 1 字节")
    output_dir = output_dir.expanduser().resolve()
    target = output_dir / f"{info['video_id']}.mp4"
    if os.path.lexists(target):
        raise DownloadError("output_exists", "目标文件已存在，不覆盖，请选择其他输出目录")
    output_dir.mkdir(parents=True, exist_ok=True)
    last_error = None
    for url in info["download_urls"][:3]:
        temp_path = None
        try:
            with client.open(url, page=False) as response:
                content_type = (response.headers.get("Content-Type") or "").split(";")[0].lower()
                if content_type.startswith("text/") or content_type in {"application/json", "application/xml"}:
                    raise DownloadError("invalid_video", "播放地址返回了网页或错误信息，而非视频")
                length = response.headers.get("Content-Length")
                try:
                    expected = int(length) if length is not None else None
                except ValueError:
                    raise DownloadError("invalid_video", "远端文件长度无效") from None
                if expected is not None and expected < 0:
                    raise DownloadError("invalid_video", "远端文件长度无效")
                if expected is not None and expected > max_bytes:
                    raise DownloadError("too_large", "视频超过指定的下载大小上限")
                fd, temp_name = tempfile.mkstemp(prefix=f".{info['video_id']}-", suffix=".part", dir=output_dir)
                temp_path = Path(temp_name)
                count, digest = 0, hashlib.sha256()
                with os.fdopen(fd, "wb") as dest:
                    while True:
                        chunk = response.read(256 * 1024)
                        if not chunk:
                            break
                        count += len(chunk)
                        if count > max_bytes:
                            raise DownloadError("too_large", "视频超过指定的下载大小上限")
                        dest.write(chunk)
                        digest.update(chunk)
                if not count or expected is not None and count != expected:
                    raise DownloadError("invalid_video", "视频下载为空或未完成")
            probe = inspect_mp4(temp_path)
            publish_new(temp_path, target)
            return {"status": "downloaded", **{key: value for key, value in info.items() if key != "download_urls"},
                    "file_path": str(target), "bytes": count, "sha256": digest.hexdigest(), **probe}
        except DownloadError as exc:
            if exc.code not in {"invalid_video", "http_error", "network_error"}:
                raise
            last_error = exc
        except HTTPException:
            last_error = DownloadError("network_error", "视频响应读取中断，未保存不完整视频")
        except (OSError, TimeoutError):
            raise DownloadError("io_error", "下载读写失败，请检查网络、磁盘空间和目录权限") from None
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
    raise last_error or DownloadError("download_failed", "没有可用的视频下载地址")


def positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return parsed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("share_text", nargs="?", help="a Douyin link or complete share text")
    parser.add_argument("--input-file", type=Path, help="read share text from a UTF-8 file instead")
    parser.add_argument("--output-dir", type=Path, default=Path("downloads"))
    parser.add_argument("--resolve-only", action="store_true")
    parser.add_argument("--timeout", type=positive_float, default=30)
    parser.add_argument("--max-mb", type=positive_float, default=1024)
    args = parser.parse_args(argv)
    if (args.share_text is None) == (args.input_file is None):
        parser.error("provide share_text OR --input-file, not both")
    if args.timeout > 3600 or args.max_mb > 1024 * 1024 or args.max_mb * 1024 * 1024 < 1:
        parser.error("timeout must be at most 3600 seconds; max-mb must describe 1 byte to 1 TiB")
    try:
        text = args.share_text if args.share_text is not None else args.input_file.read_text(encoding="utf-8-sig")
        client = PublicHTTP(args.timeout)
        info = resolve_share(text, client)
        result = ({"status": "resolved", **info} if args.resolve_only else
                  download_info(info, args.output_dir, client, int(args.max_mb * 1024 * 1024)))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except DownloadError as exc:
        result = {"status": "error", "code": exc.code, "message": exc.message}
    except (OSError, UnicodeError):
        result = {"status": "error", "code": "io_error", "message": "无法读写输入或输出文件，请检查编码和目录权限"}
    except KeyboardInterrupt:
        result = {"status": "error", "code": "cancelled", "message": "用户已取消下载"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
