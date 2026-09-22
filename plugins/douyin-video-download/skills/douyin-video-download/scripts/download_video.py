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
import time
from html.parser import HTMLParser
from http.client import HTTPException
from http.cookiejar import Cookie, CookieJar, DefaultCookiePolicy
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener

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
MAX_COOKIE_BYTES = 1024 * 1024


class DownloadError(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


def is_douyin_host(host: str) -> bool:
    return any(host == root or host.endswith("." + root)
               for root in ("douyin.com", "iesdouyin.com"))


def login_state_dir() -> Path:
    """Plugin-owned state only; never inspect another browser's profile."""
    return Path.home() / ".douyin-video-download" / "auth"


def cookie_file_for_request(explicit: Path | None = None, *, guest: bool = False) -> Path | None:
    if explicit is not None:
        return explicit
    if guest:
        return None
    saved = login_state_dir() / "cookies.json"
    if saved.is_symlink() or saved.parent.is_symlink():
        raise DownloadError("cookie_file_error", "插件登录状态路径不能是符号链接")
    return saved if saved.is_file() else None


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
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            # CookieProcessor will select cookies again for the destination domain/path.
            redirected.remove_header("Cookie")
        return redirected


class DouyinCookiePolicy(DefaultCookiePolicy):
    """Accept and return cookies only for official Douyin hosts over HTTPS."""
    def __init__(self):
        super().__init__(strict_ns_domain=DefaultCookiePolicy.DomainStrictNonDomain)

    @staticmethod
    def in_scope(cookie, request):
        host = (urlsplit(request.full_url).hostname or "").lower()
        return (request.type == "https" and is_douyin_host(host)
                and is_douyin_host(cookie.domain.lstrip(".").lower()))

    def set_ok(self, cookie, request):
        return self.in_scope(cookie, request) and super().set_ok(cookie, request)

    def return_ok(self, cookie, request):
        return self.in_scope(cookie, request) and super().return_ok(cookie, request)


def load_cookie_file(path: Path, jar: CookieJar) -> int:
    """Import an explicitly selected Netscape or browser-export JSON file; never log values."""
    try:
        with path.expanduser().open("rb") as handle:
            data = handle.read(MAX_COOKIE_BYTES + 1)
        if len(data) > MAX_COOKIE_BYTES:
            raise DownloadError("invalid_cookies", "Cookie 文件超过 1 MiB 限制")
        source = data.decode("utf-8-sig")
    except (OSError, UnicodeError, ValueError):
        raise DownloadError("cookie_file_error", "无法读取指定的 UTF-8 Cookie 文件") from None
    try:
        if source.lstrip().startswith(("[", "{")):
            entries = json.loads(source)
            if isinstance(entries, dict):
                entries = entries.get("cookies")
            if not isinstance(entries, list) or not all(isinstance(c, dict) for c in entries):
                raise ValueError
        else:
            entries = []
            for line in source.splitlines():
                if line.startswith("#HttpOnly_"):
                    line = line[len("#HttpOnly_"):]
                elif not line.strip() or line.startswith("#"):
                    continue
                domain, subdomains, cookie_path, secure, expires, name, value = line.split("\t")
                if subdomains not in {"TRUE", "FALSE"} or secure not in {"TRUE", "FALSE"}:
                    raise ValueError
                entries.append({"domain": domain, "hostOnly": subdomains == "FALSE",
                                "path": cookie_path, "secure": secure == "TRUE",
                                "expires": int(expires) if expires else None,
                                "name": name, "value": value})
        imported, expired = [], 0
        for entry in entries:
            domain = entry.get("domain", "")
            if not isinstance(domain, str):
                raise ValueError
            domain = domain.lower()
            host = domain.lstrip(".")
            if not is_douyin_host(host):
                continue
            if (domain.startswith("..") or not all(re.fullmatch(r"[a-z0-9-]+", part) for part in host.split("."))):
                raise ValueError
            name, value, cookie_path = entry.get("name"), entry.get("value"), entry.get("path", "/")
            if (not isinstance(name, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name)
                    or not isinstance(value, str) or not value.isascii() or re.search(r"[\x00-\x20\x7f;]", value)
                    or not isinstance(cookie_path, str) or not cookie_path.startswith("/")
                    or re.search(r"[\x00-\x20\x7f]", cookie_path)):
                raise ValueError
            host_only = entry.get("hostOnly", not domain.startswith("."))
            secure = entry.get("secure", True)
            if not isinstance(host_only, bool) or not isinstance(secure, bool):
                raise ValueError
            expiry = entry.get("expires", entry.get("expirationDate"))
            if expiry is not None:
                if isinstance(expiry, bool):
                    raise ValueError
                expiry = float(expiry)
                if not math.isfinite(expiry) or expiry < -1:
                    raise ValueError
                expiry = int(expiry) if expiry > 0 else None
            if expiry is not None and expiry <= time.time():
                expired += 1
                continue
            stored_domain = host if host_only else "." + host
            imported.append(Cookie(0, name, value, None, False, stored_domain, not host_only,
                                   not host_only, cookie_path, True, secure, expiry, expiry is None,
                                   None, None, {}, False))
        if not imported:
            if expired:
                raise DownloadError("cookies_expired", "文件中的抖音 Cookie 均已过期，请重新导出")
            raise DownloadError("cookies_unavailable", "文件中没有可用的抖音 Cookie")
        for cookie in imported:
            jar.set_cookie(cookie)
        return len(imported)
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise DownloadError("invalid_cookies", "Cookie 文件格式无效；请使用 Netscape 或浏览器导出的 JSON 文件") from None


class PublicHTTP:
    """In-memory guest session, optionally seeded by an explicitly supplied cookie file."""
    def __init__(self, timeout: float = 30, cookies_file: Path | None = None):
        self.timeout = timeout
        self.cookies = CookieJar(policy=DouyinCookiePolicy())
        self.imported_cookies = load_cookie_file(cookies_file, self.cookies) if cookies_file is not None else 0
        self._openers = {}

    def open(self, url: str, *, page: bool, user_agent: str = USER_AGENT):
        url = validate_url(url, page=page, check_dns=True)
        headers = {"User-Agent": user_agent, "Accept-Encoding": "identity"}
        if not page:
            headers["Referer"] = "https://www.douyin.com/"
        if page not in self._openers:
            self._openers[page] = build_opener(CheckedRedirect(page), HTTPCookieProcessor(self.cookies))
        opener = self._openers[page]
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


def positive_number(value) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else 0
    except (ValueError, TypeError):
        return 0


def rank_candidates(candidates: list[dict]) -> list[dict]:
    unique = {}
    for candidate in candidates:
        try:
            address = str(candidate.get("url", ""))
            if address.startswith("//"):
                address = "https:" + address
            url = validate_url(address, page=False)
        except DownloadError:
            continue
        if urlsplit(url).path.lower().endswith((".m3u8", ".mpd")):
            continue
        item = {"url": url, **{field: positive_number(candidate.get(field)) for field in ("width", "height", "bitrate", "bytes")}}
        item["source"] = str(candidate.get("source", "observed"))[:80]
        previous = unique.get(url)
        if previous:
            for field in ("width", "height", "bitrate", "bytes"):
                item[field] = max(item[field], previous[field])
        unique[url] = item
    return sorted(unique.values(), key=lambda c: (c["width"]*c["height"], c["bitrate"], c["bytes"]), reverse=True)


def observed_info(page_url: str, candidates: list[dict]) -> dict:
    """Accept explicitly supplied observations; never read a browser profile or invent file IDs."""
    page_url = validate_url(page_url, page=True)
    video_id = extract_video_id(page_url)
    if not video_id:
        raise DownloadError("id_unavailable", "浏览器取源需提供含目标视频 ID 的页面地址")
    if not 1 <= len(candidates) <= 30 or any(not isinstance(c, dict) for c in candidates):
        raise DownloadError("invalid_candidates", "请提供从该页面实际观察到的 1..30 个候选地址")
    ranked = rank_candidates(candidates)
    if not ranked:
        raise DownloadError("metadata_unavailable", "没有有效的公网视频候选地址")
    return {"video_id": video_id, "title": video_id, "author": "", "page_url": page_url,
            "download_urls": [c["url"] for c in ranked], "candidates": ranked,
            "provenance": "caller_supplied_browser_observations; target-content binding requires visual verification"}


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
        candidates = []
        def add(value, context, label):
            address = value if isinstance(value, dict) else {}
            for url in address_urls(value):
                candidates.append({"url":url, "width":address.get("width",context.get("width")),
                    "height":address.get("height",context.get("height")),
                    "bitrate":context.get("bit_rate",context.get("bitRate")),
                    "bytes":address.get("data_size",address.get("dataSize")), "source":label})
        # Do not search arbitrary descendants: those may contain music or recommendations.
        for key in ("play_addr", "playAddr", "play_addr_h264", "play_addr_265", "playApi"):
            add(video.get(key), {}, key)
        variants = video.get("bit_rate") or video.get("bitRate") or []
        if isinstance(variants, list):
            for variant in variants:
                if isinstance(variant, dict):
                    add(variant.get("play_addr") or variant.get("playAddr"), variant, "bit_rate")
        for key in ("play_addr_lowbr", "download_addr", "downloadAddr"):
            add(video.get(key), {}, key)
        candidates = rank_candidates(candidates)
        urls = [c["url"] for c in candidates]
        if not urls:
            continue
        author = node.get("author") or {}
        return {
            "video_id": video_id,
            "title": str(node.get("desc") or node.get("title") or video_id),
            "author": str(author.get("nickname") or "") if isinstance(author, dict) else "",
            "page_url": f"https://www.douyin.com/video/{video_id}",
            "download_urls": urls,
            "candidates": candidates,
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
    raise DownloadError("metadata_unavailable", "页面中未找到目标视频播放地址；可能需要浏览器渲染、登录验证，或内容/页面结构已变化，不能仅凭此判断缺少 Cookie")


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


def download_info(info: dict, output_dir: Path, client: PublicHTTP, max_bytes: int,
                  min_short_side: int = 0, max_candidates: int = 3) -> dict:
    if not ID_PATTERN.fullmatch(str(info.get("video_id", ""))):
        raise DownloadError("invalid_video_id", "视频 ID 无效")
    if max_bytes < 1 or not 0 <= min_short_side <= 8192 or not 1 <= max_candidates <= 8:
        raise DownloadError("invalid_limit", "大小上限需为正数；最小短边 0..8192；候选数 1..8")
    output_dir = output_dir.expanduser().resolve()
    target = output_dir / f"{info['video_id']}.mp4"
    if os.path.lexists(target):
        raise DownloadError("output_exists", "目标文件已存在，不覆盖，请选择其他输出目录")
    output_dir.mkdir(parents=True, exist_ok=True)
    last_error, best = None, None
    temporary, checked = [], []
    try:
        for index, url in enumerate(info["download_urls"][:max_candidates]):
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
                    temporary.append(temp_path)
                    count, digest = 0, hashlib.sha256()
                    with os.fdopen(fd, "wb") as dest:
                        while chunk := response.read(256 * 1024):
                            count += len(chunk)
                            if count > max_bytes:
                                raise DownloadError("too_large", "视频超过指定的下载大小上限")
                            dest.write(chunk)
                            digest.update(chunk)
                    if not count or expected is not None and count != expected:
                        raise DownloadError("invalid_video", "视频下载为空或未完成")
                probe = inspect_mp4(temp_path)
                width, height, duration = probe["width"], probe["height"], probe["duration_seconds"]
                if not width or not height or not duration or duration <= 0:
                    raise DownloadError("quality_unverified", "候选视频的分辨率或时长不可核实")
                bitrate = count * 8 / duration
                checked.append({"candidate_index":index,"width":width,"height":height,"bytes":count,
                                "duration_seconds":duration,"average_bitrate":round(bitrate),"status":"verified"})
                if min(width,height) < min_short_side:
                    checked[-1]["status"] = "below_minimum_resolution"
                    last_error = DownloadError("quality_too_low", "所有可用候选源均未达到指定的最小短边，未交付低清替代品")
                    continue
                score = (width*height,bitrate)
                if best is None or score > best[0]:
                    if best is not None:
                        best[1].unlink(missing_ok=True)
                    best = (score,temp_path,probe,count,digest.hexdigest(),index)
                else:
                    temp_path.unlink(missing_ok=True)
            except DownloadError as exc:
                if exc.code not in {"invalid_video", "http_error", "network_error", "quality_unverified"}:
                    raise
                last_error = exc
                checked.append({"candidate_index":index,"status":"failed","code":exc.code})
            except HTTPException:
                last_error = DownloadError("network_error", "视频响应读取中断，未保存不完整视频")
                checked.append({"candidate_index":index,"status":"failed","code":last_error.code})
            except (OSError, TimeoutError):
                raise DownloadError("io_error", "下载读写失败，请检查网络、磁盘空间和目录权限") from None
            finally:
                if temp_path is not None and (best is None or temp_path != best[1]):
                    temp_path.unlink(missing_ok=True)
        if best is None:
            raise last_error or DownloadError("download_failed", "没有可用的视频下载地址")
        _, best_path, probe, count, checksum, selected = best
        publish_new(best_path,target)
        warnings = ["只比较已检查的候选源；分辨率和码率不等于原画证明，也不能验证视频内容属于目标页面。"]
        if min(probe["width"],probe["height"]) < 1080:
            warnings.append("选中源低于 1080 短边；高质量去字幕前应确认是否还有更清晰的授权来源。")
        if len(info["download_urls"]) > max_candidates:
            warnings.append("候选数达到本次检查上限，仍有线路未核实；可显式提高 max_candidates，最多 8。")
        return {"status":"downloaded", **{key:value for key,value in info.items() if key not in {"download_urls","candidates"}},
                "file_path":str(target),"bytes":count,"sha256":checksum,**probe,
                "quality":{"selection":"highest_verified_resolution_then_bitrate", "selected_candidate_index":selected,
                           "checked_candidates":checked,"min_short_side":min_short_side,
                           "all_candidates_checked":len(checked)==len(info["download_urls"])},"warnings":warnings}
    finally:
        for path in temporary:
            path.unlink(missing_ok=True)


def positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return parsed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("share_text", nargs="?", help="a Douyin link or complete share text")
    parser.add_argument("--input-file", type=Path, help="read share text from a UTF-8 file instead")
    parser.add_argument("--candidates-file", type=Path, help="JSON with page_url and candidates observed in an authorized browser")
    authentication = parser.add_mutually_exclusive_group()
    authentication.add_argument("--cookies-file", type=Path, help="optional user-selected Netscape/JSON cookie file; otherwise reuse this plugin's QR login")
    authentication.add_argument("--guest", action="store_true", help="ignore this plugin's saved login and use a fresh guest session")
    parser.add_argument("--output-dir", type=Path, default=Path("downloads"))
    parser.add_argument("--resolve-only", action="store_true")
    parser.add_argument("--timeout", type=positive_float, default=30)
    parser.add_argument("--max-mb", type=positive_float, default=1024)
    parser.add_argument("--min-short-side", type=int, default=0, help="reject lower-resolution sources, e.g. 1080")
    parser.add_argument("--max-candidates", type=int, default=3, help="compare at most 1..8 candidate downloads")
    args = parser.parse_args(argv)
    if sum(value is not None for value in (args.share_text,args.input_file,args.candidates_file)) != 1:
        parser.error("provide exactly one of share_text, --input-file or --candidates-file")
    if args.timeout > 3600 or args.max_mb > 1024 * 1024 or args.max_mb * 1024 * 1024 < 1:
        parser.error("timeout must be at most 3600 seconds; max-mb must describe 1 byte to 1 TiB")
    try:
        client = PublicHTTP(args.timeout, cookies_file=cookie_file_for_request(args.cookies_file, guest=args.guest))
        if args.candidates_file is not None:
            data = json.loads(args.candidates_file.read_text(encoding="utf-8-sig"))
            if not isinstance(data,dict) or not isinstance(data.get("candidates"),list):
                raise DownloadError("invalid_candidates", "候选 JSON 必须包含 page_url 和 candidates 数组")
            info = observed_info(data.get("page_url", ""),data["candidates"])
        else:
            text = args.share_text if args.share_text is not None else args.input_file.read_text(encoding="utf-8-sig")
            info = resolve_share(text, client)
        result = ({"status": "resolved", **info} if args.resolve_only else
                  download_info(info, args.output_dir, client, int(args.max_mb * 1024 * 1024),args.min_short_side,args.max_candidates))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except DownloadError as exc:
        result = {"status": "error", "code": exc.code, "message": exc.message}
    except (ValueError, TypeError):
        result = {"status": "error", "code": "invalid_candidates", "message": "候选 JSON 或参数类型无效"}
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
