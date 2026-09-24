#!/usr/bin/env python3
"""Offline standard-library tests. All media fixtures are synthetic containers."""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
from urllib.error import HTTPError
from urllib.parse import quote
from http.client import IncompleteRead

SCRIPT = Path(__file__).with_name("download_video.py")
SPEC = importlib.util.spec_from_file_location("standalone_downloader", SCRIPT)
d = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(d)
VIDEO_ID = "1234567890123456789"
OTHER_ID = "9876543210987654321"
PAGE = f"https://www.douyin.com/video/{VIDEO_ID}"
MOBILE_PAGE = f"https://www.iesdouyin.com/share/video/{VIDEO_ID}/"
SHORT = "https://v.douyin.com/example/"
MEDIA = "https://media.example/video.mp4?signature=TEST_ONLY"


def item(video_id=VIDEO_ID, url=MEDIA, **extra):
    return {"aweme_id": video_id, "desc": "测试视频", "author": {"nickname": "Test"},
            "video": {"play_addr": {"url_list": [url]}}, **extra}


def page(data):
    return '<script>window._ROUTER_DATA = ' + json.dumps(data) + ';</script>'


def box(kind, payload, *, extended=False, to_end=False):
    if extended:
        return struct.pack(">I4sQ", 1, kind, len(payload) + 16) + payload
    return struct.pack(">I4s", 0 if to_end else len(payload) + 8, kind) + payload


def track(kind, width=1080, height=1920):
    tkhd = bytes(76) + struct.pack(">II", width << 16, height << 16)
    hdlr = bytes(8) + kind + bytes(12)
    return box(b"trak", box(b"tkhd", tkhd) + box(b"mdia", box(b"hdlr", hdlr)))


def media(*, audio=True, video=True, mvhd_version=0, extended=False, to_end=False,width=1080,height=1920):
    # This fixture exercises container parsing, not codec/packet decoding.
    mvhd = (bytes(12) + struct.pack(">II", 1000, 15000) if mvhd_version == 0
            else b"\x01" + bytes(19) + struct.pack(">IQ", 1000, 15000))
    tracks = (track(b"vide",width,height) if video else b"") + (track(b"soun", 0, 0) if audio else b"")
    return (box(b"ftyp", b"isom" + bytes(4) + b"isommp42")
            + box(b"moov", box(b"mvhd", mvhd) + tracks, extended=extended)
            + box(b"mdat", b"fixture-data-not-a-real-codec", to_end=to_end))


class Response(io.BytesIO):
    def __init__(self, data=b"", *, headers=None, url=MEDIA):
        super().__init__(data)
        self.headers = headers or {}
        self.url = url

    def geturl(self):
        return self.url


class FakeClient:
    def __init__(self, *, pages=(), responses=()):
        self.pages = list(pages)
        self.responses = list(responses)
        self.page_calls = []
        self.open_calls = []

    @staticmethod
    def take(queue):
        if not queue:
            raise AssertionError("unexpected extra request")
        value = queue.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def page(self, url, user_agent=None):
        self.page_calls.append(url)
        return self.take(self.pages)

    def open(self, url, **kwargs):
        self.open_calls.append(url)
        return self.take(self.responses)


class ErrorAssertions(unittest.TestCase):
    def assertError(self, code, function, *args, **kwargs):
        with self.assertRaises(d.DownloadError) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)


class URLs(ErrorAssertions):
    def test_share_message_and_markdown(self):
        self.assertEqual(d.extract_share_url(f"分享！[{SHORT}]({SHORT})。复制链接"), SHORT)

    def test_unrelated_link_is_not_selected(self):
        self.assertEqual(d.extract_share_url(f"https://example.com/ {SHORT}；"), SHORT)

    def test_multiple_different_links_require_selection(self):
        self.assertError("multiple_urls", d.extract_share_url, SHORT + " " + PAGE)

    def test_fake_domain_rejected(self):
        self.assertError("no_share_url", d.extract_share_url, "https://douyin.com.example/video/1")

    def test_credentials_and_bad_ports_rejected(self):
        for url in ("https://user:password@www.douyin.com/", "https://www.douyin.com:9000/"):
            with self.subTest(url=url):
                self.assertError("invalid_url", d.validate_url, url, page=True)

    def test_private_and_local_media_rejected(self):
        for host in ("127.0.0.1", "10.1.2.3", "169.254.169.254", "[::1]", "localhost", "a.local"):
            with self.subTest(host=host):
                self.assertError("unsafe_url", d.validate_url, f"https://{host}/x", page=False)

    def test_dns_check_rejects_private_resolution(self):
        with patch.object(d.socket, "getaddrinfo", return_value=[(0, 0, 0, "", ("10.0.0.1", 443))]):
            self.assertError("unsafe_url", d.validate_url, MEDIA, page=False, check_dns=True)

    def test_dns_check_accepts_public_resolution(self):
        with patch.object(d.socket, "getaddrinfo", return_value=[(0, 0, 0, "", ("8.8.8.8", 443))]):
            self.assertEqual(d.validate_url(MEDIA, page=False, check_dns=True), MEDIA)

    def test_https_ipv6_and_fragment_normalization(self):
        self.assertEqual(d.validate_url("http://[2606:4700:4700::1111]/v#x", page=False),
                         "https://[2606:4700:4700::1111]/v")

    def test_id_from_canonical_and_modal(self):
        for url in (PAGE, MOBILE_PAGE, f"https://www.douyin.com/?modal_id={VIDEO_ID}"):
            self.assertEqual(d.extract_video_id(url), VIDEO_ID)

    def test_arbitrary_number_is_not_video_id(self):
        self.assertIsNone(d.extract_video_id(f"https://www.douyin.com/?tracking={VIDEO_ID}"))

    def test_note_is_not_video(self):
        self.assertError("unsupported_content", d.extract_video_id, f"https://www.douyin.com/note/{VIDEO_ID}")


class Metadata(ErrorAssertions):
    def test_high_quality_variant_ranked_above_default_player(self):
        target = item()
        target['video']['play_addr'].update(width=576,height=1024)
        target['video']['bit_rate'] = [{'bit_rate':4000000,'play_addr':{'width':1080,'height':1920,'url_list':[MEDIA+'&hd=1']}}]
        info = d.extract_info([target],VIDEO_ID)
        self.assertEqual(info['download_urls'][0],MEDIA+'&hd=1')

    def test_browser_observations_keep_signed_query_unchanged(self):
        url = 'https://www.douyin.com/aweme/v1/play/?file_id=OBSERVED&signature=x%2By&watermark=1'
        info = d.observed_info(PAGE,[{'url':url,'width':1080,'height':1920}])
        self.assertEqual(info['download_urls'],[url])
        self.assertIn('caller_supplied',info['provenance'])

    def test_browser_observations_require_target_page(self):
        self.assertError('id_unavailable',d.observed_info,'https://www.douyin.com/',[{'url':MEDIA}])

    def test_router_state_json(self):
        result = d.extract_info(d.parse_states(page({"details": [item()]})), VIDEO_ID)
        self.assertEqual(result["title"], "测试视频")
        self.assertEqual(result["download_urls"], [MEDIA])

    def test_percent_encoded_render_data(self):
        html = '<script id="RENDER_DATA">' + quote(json.dumps(item())) + '</script>'
        self.assertIsNotNone(d.extract_info(d.parse_states(html), VIDEO_ID))

    def test_json_parse_string(self):
        value = {"data": item(desc='包含 } 和 " 引号')}
        html = '<script>window.__INITIAL_STATE__ = JSON.parse(' + json.dumps(json.dumps(value)) + ');</script>'
        self.assertEqual(d.extract_info(d.parse_states(html), VIDEO_ID)["title"], '包含 } 和 " 引号')

    def test_multiple_states_recommendation_and_music_ignored(self):
        target = item()
        target["music"] = {"play_addr": {"url_list": ["https://media.example/music.mp3"]}}
        html = page({"recommended": item(OTHER_ID)}) + page(target)
        result = d.extract_info(d.parse_states(html), VIDEO_ID)
        self.assertEqual(result["video_id"], VIDEO_ID)
        self.assertEqual(result["download_urls"], [MEDIA])

    def test_no_fallback_to_wrong_video(self):
        self.assertIsNone(d.extract_info([item(OTHER_ID)], VIDEO_ID))

    def test_malformed_javascript_not_executed(self):
        self.assertEqual(d.parse_states('<script>window._ROUTER_DATA = doSomething();</script>'), [])

    def test_camelcase_and_watermark_preserved(self):
        url = "http://media.example/playwm/?video_id=test&watermark=1"
        target = {"awemeId": VIDEO_ID, "video": {"playAddr": {"urlList": [url]}}}
        result = d.extract_info([target], VIDEO_ID)
        self.assertEqual(result["download_urls"], [url.replace("http:", "https:", 1)])

    def test_hls_only_is_not_mp4(self):
        self.assertIsNone(d.extract_info([item(url="https://media.example/master.m3u8")], VIDEO_ID))

    def test_image_post_rejected(self):
        self.assertError("unsupported_content", d.extract_info, [{"aweme_id": VIDEO_ID, "images": [{}]}], VIDEO_ID)

    def test_private_candidate_skipped(self):
        value = item()
        value["video"]["play_addr"]["url_list"] = ["http://127.0.0.1/private", MEDIA, MEDIA]
        self.assertEqual(d.extract_info([value], VIDEO_ID)["download_urls"], [MEDIA])


class Resolver(ErrorAssertions):
    def test_resolve_share_directly(self):
        client = FakeClient(pages=[(MOBILE_PAGE, page(item()))])
        self.assertEqual(d.resolve_share(SHORT, client)["video_id"], VIDEO_ID)
        self.assertEqual(len(client.page_calls), 1)

    def test_desktop_fallback_skips_duplicate_mobile_page(self):
        client = FakeClient(pages=[(MOBILE_PAGE, "<html></html>"), (PAGE, page(item()))])
        self.assertEqual(d.resolve_share(SHORT, client)["page_url"], PAGE)
        self.assertEqual(client.page_calls, [SHORT, PAGE])

    def test_source_redirect_id_change_stops(self):
        client = FakeClient(pages=[(PAGE.replace(VIDEO_ID, OTHER_ID), page(item(OTHER_ID)))])
        self.assertError("id_mismatch", d.resolve_share, PAGE, client)

    def test_fallback_id_change_stops(self):
        client = FakeClient(pages=[(MOBILE_PAGE, ""), (PAGE.replace(VIDEO_ID, OTHER_ID), page(item(OTHER_ID)))])
        self.assertError("id_mismatch", d.resolve_share, SHORT, client)

    def test_no_id_not_guessed_from_recommendations(self):
        client = FakeClient(pages=[("https://www.douyin.com/", page(item()))])
        self.assertError("id_unavailable", d.resolve_share, SHORT, client)

    def test_access_denial_no_retry(self):
        client = FakeClient(pages=[d.DownloadError("access_required", "denied")])
        self.assertError("access_required", d.resolve_share, SHORT, client)
        self.assertEqual(len(client.page_calls), 1)

    def test_shell_page_is_not_download_success(self):
        client = FakeClient(pages=[(MOBILE_PAGE, page({"itemId": VIDEO_ID})), (PAGE, "<html></html>")])
        self.assertError("metadata_unavailable", d.resolve_share, SHORT, client)


class Containers(ErrorAssertions):
    def inspect(self, content):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture.mp4"
            path.write_bytes(content)
            return d.inspect_mp4(path)

    def test_tracks_dimensions_duration(self):
        result = self.inspect(media())
        self.assertEqual((result["width"], result["height"], result["duration_seconds"]), (1080, 1920, 15.0))
        self.assertTrue(result["has_audio"])
        self.assertTrue(result["has_video"])

    def test_silent_video_is_distinguished(self):
        self.assertFalse(self.inspect(media(audio=False))["has_audio"])

    def test_audio_only_rejected(self):
        self.assertError("invalid_video", self.inspect, media(video=False))

    def test_truncated_file_rejected(self):
        self.assertError("invalid_video", self.inspect, media()[:-3])

    def test_html_rejected(self):
        self.assertError("invalid_video", self.inspect, b"<html>Please log in</html>")

    def test_extended_boxes_and_v1_time(self):
        self.assertEqual(self.inspect(media(extended=True, mvhd_version=1))["duration_seconds"], 15.0)

    def test_size_zero_last_box(self):
        self.assertTrue(self.inspect(media(to_end=True))["has_video"])

    def test_incomplete_header_rejected(self):
        for suffix in (b"x", struct.pack(">I4s", 1, b"free")):
            self.assertError("invalid_video", self.inspect, media() + suffix)


class Downloads(ErrorAssertions):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "output"
        self.info = d.extract_info([item()], VIDEO_ID)
        self.body = media()

    def download(self, response, **kwargs):
        client = FakeClient(responses=[response])
        return d.download_info(self.info, self.folder, client, kwargs.get("limit", 1024 * 1024))

    def test_bytes_hash_and_metadata(self):
        result = self.download(Response(self.body, headers={"Content-Length": str(len(self.body))}))
        self.assertEqual(result["status"], "downloaded")
        self.assertEqual(Path(result["file_path"]).read_bytes(), self.body)
        self.assertEqual(result["sha256"], hashlib.sha256(self.body).hexdigest())
        self.assertNotIn("download_urls", result)
        self.assertEqual(list(self.folder.iterdir()), [self.folder / f"{VIDEO_ID}.mp4"])

    def test_valid_low_resolution_is_not_accepted_before_comparison(self):
        info = {**self.info,'download_urls':[MEDIA,MEDIA+'&hd=1']}
        client = FakeClient(responses=[Response(media(width=576,height=1024)),Response(self.body)])
        result = d.download_info(info,self.folder,client,10000,min_short_side=1080)
        self.assertEqual(result['width'],1080)
        self.assertEqual(result['quality']['selected_candidate_index'],1)
        self.assertEqual(len(client.open_calls),2)
        self.assertNotIn('signature',json.dumps(result))

    def test_low_quality_only_leaves_no_final_file(self):
        client = FakeClient(responses=[Response(media(width=576,height=1024))])
        self.assertError('quality_too_low',d.download_info,self.info,self.folder,client,10000,min_short_side=1080)
        self.assertEqual(list(self.folder.iterdir()),[])

    def test_verified_dimensions_override_false_candidate_metadata(self):
        info = {**self.info,'download_urls':[MEDIA,MEDIA+'&other=1'],
                'candidates':[{'url':MEDIA,'width':2160,'height':3840}]}
        client = FakeClient(responses=[Response(media(width=576,height=1024)),Response(self.body)])
        result = d.download_info(info,self.folder,client,10000)
        self.assertEqual(result['quality']['selected_candidate_index'],1)
        self.assertEqual(Path(result['file_path']).read_bytes(),self.body)

    def test_access_denial_after_valid_candidate_stops_and_cleans(self):
        info = {**self.info,'download_urls':[MEDIA,MEDIA+'&other=1']}
        client = FakeClient(responses=[Response(self.body),d.DownloadError('access_required','denied')])
        self.assertError('access_required',d.download_info,info,self.folder,client,10000)
        self.assertEqual(list(self.folder.iterdir()),[])

    def test_existing_file_untouched_and_no_request(self):
        self.folder.mkdir()
        output = self.folder / f"{VIDEO_ID}.mp4"
        output.write_bytes(b"original")
        client = FakeClient()
        self.assertError("output_exists", d.download_info, self.info, self.folder, client, 10000)
        self.assertEqual(output.read_bytes(), b"original")
        self.assertEqual(client.open_calls, [])

    def test_bad_id_never_creates_output(self):
        bad = {**self.info, "video_id": "../escape"}
        self.assertError("invalid_video_id", d.download_info, bad, self.folder, FakeClient(), 10000)
        self.assertFalse(self.folder.exists())

    def test_content_type_html_rejected(self):
        self.assertError("invalid_video", self.download, Response(b"HTML", headers={"Content-Type": "text/html"}))
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_size_header_limit_before_write(self):
        self.assertError("too_large", self.download, Response(self.body, headers={"Content-Length": "999999"}), limit=1000)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_stream_limit_cleans_temp(self):
        self.assertError("too_large", self.download, Response(self.body), limit=40)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_length_mismatch_cleans_temp(self):
        self.assertError("invalid_video", self.download, Response(self.body, headers={"Content-Length": str(len(self.body) + 1)}))
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_invalid_length_header(self):
        for header in ("invalid", "-1"):
            self.assertError("invalid_video", self.download, Response(self.body, headers={"Content-Length": header}))

    def test_candidate_fallback_is_bounded(self):
        info = {**self.info, "download_urls": [MEDIA] * 4}
        client = FakeClient(responses=[Response(b"bad"), Response(b"bad"), Response(b"bad")])
        self.assertError("invalid_video", d.download_info, info, self.folder, client, 10000)
        self.assertEqual(len(client.open_calls), 3)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_valid_second_candidate(self):
        info = {**self.info, "download_urls": [MEDIA, MEDIA + "&backup=1"]}
        client = FakeClient(responses=[Response(b"bad"), Response(self.body)])
        self.assertEqual(d.download_info(info, self.folder, client, 10000)["status"], "downloaded")

    def test_access_denial_no_media_retry(self):
        for code in ("access_required", "rate_limited"):
            client = FakeClient(responses=[d.DownloadError(code, "stopped")])
            self.assertError(code, d.download_info, {**self.info, "download_urls": [MEDIA] * 3}, self.folder, client, 10000)
            self.assertEqual(len(client.open_calls), 1)

    def test_interrupted_response_cleans_temp(self):
        class BrokenResponse(Response):
            def read(self, size=-1):
                raise IncompleteRead(b"partial")
        self.assertError("network_error", self.download, BrokenResponse())
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_exclusive_copy_fallback(self):
        with patch.object(d.os, "link", side_effect=OSError("hardlinks not supported")):
            result = self.download(Response(self.body))
        self.assertEqual(Path(result["file_path"]).read_bytes(), self.body)

    def test_publish_race_never_overwrites(self):
        source = Path(self.temp.name) / "source.part"
        target = Path(self.temp.name) / "target.mp4"
        source.write_bytes(b"new")
        target.write_bytes(b"existing")
        with patch.object(d.os, "link", side_effect=OSError("use fallback")):
            self.assertError("output_exists", d.publish_new, source, target)
        self.assertEqual(target.read_bytes(), b"existing")


class NetworkAndCLI(ErrorAssertions):
    def test_http_errors_are_sanitized(self):
        for status, code in ((403, "access_required"), (429, "rate_limited"), (500, "http_error")):
            opener = Mock()
            opener.open.side_effect = HTTPError(MEDIA, status, "private-url", {}, io.BytesIO())
            with patch.object(d, "build_opener", return_value=opener), patch.object(d.socket, "getaddrinfo", return_value=[(0, 0, 0, "", ("8.8.8.8", 443))]):
                self.assertError(code, d.PublicHTTP().open, MEDIA, page=False)

    def test_page_incomplete_read_is_structured(self):
        client = d.PublicHTTP()
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.side_effect = IncompleteRead(b"partial")
        with patch.object(client, "open", return_value=response):
            self.assertError("network_error", client.page, PAGE)

    def test_cli_download_uses_shared_cookie_entrypoint(self):
        client = FakeClient(pages=[(MOBILE_PAGE, page(item()))], responses=[Response(media())])
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as folder, patch.object(d, "PublicHTTP", return_value=client) as create, contextlib.redirect_stdout(output):
            cookies = str(Path(folder) / 'cookies.txt')
            code = d.main([SHORT, '--cookies-file', cookies, '--output-dir', folder])
            create.assert_called_once_with(cookies_file=Path(cookies))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "downloaded")
        self.assertEqual(len(client.open_calls), 1)

    def test_cli_input_file_utf8_bom(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "input.txt"
            source.write_text("分享链接 " + SHORT, encoding="utf-8-sig")
            client = FakeClient(pages=[(MOBILE_PAGE, page(item()))], responses=[Response(media())])
            with patch.object(d, "PublicHTTP", return_value=client), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(d.main(["--input-file", str(source), '--cookies-file', str(Path(folder) / 'cookies.txt'), '--output-dir', folder]), 0)

    def test_cli_usage_rejects_ambiguous_input_and_removed_options(self):
        for args in ([], [SHORT, "--input-file", "a.txt"], [SHORT, "--guest"], [SHORT, "--resolve-only"], [SHORT, "--max-candidates", "8"]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                d.main(args)
            self.assertEqual(raised.exception.code, 2)

    def test_portable_script_in_isolated_python_and_unrelated_cwd(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            script_dir = root / "standalone"
            script_dir.mkdir()
            copy = script_dir / SCRIPT.name
            shutil.copy2(SCRIPT, copy)
            cwd = root / "unrelated"
            cwd.mkdir()
            result = subprocess.run([sys.executable, "-I", "-B", str(copy), "--help"], cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--cookies-file", result.stdout)
            result = subprocess.run([sys.executable, "-I", "-B", str(copy), "not a link"], cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=15)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(json.loads(result.stdout)["code"], "cookies_required")
            cookies = root / 'cookies.txt'
            cookies.write_text('session_fixture=FAKE_VALUE', encoding='utf-8')
            result = subprocess.run([sys.executable, '-I', '-B', str(copy), 'not a link', '--cookies-file', str(cookies)], cwd=cwd, capture_output=True, text=True, encoding='utf-8', timeout=15)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(json.loads(result.stdout)['code'], 'no_share_url')
            self.assertFalse((cwd / "downloads").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
