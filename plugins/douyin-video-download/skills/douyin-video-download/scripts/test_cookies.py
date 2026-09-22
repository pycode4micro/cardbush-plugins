#!/usr/bin/env python3
"""Offline cookie import and real urllib request/redirect pipeline tests."""
import contextlib
from email.message import Message
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.request import HTTPSHandler, Request, build_opener
from urllib.response import addinfourl

SPEC = importlib.util.spec_from_file_location("cookie_downloader", Path(__file__).with_name("download_video.py"))
d = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(d)
PAGE = "https://www.douyin.com/video/1234567890123456789"
DNS = [(0, 0, 0, "", ("8.8.8.8", 443))]


def exported(**changes):
    return {"name": "session_fixture", "value": "FAKE_TEST_VALUE", "domain": ".douyin.com",
            "path": "/", "secure": True, "expires": time.time() + 3600, **changes}


class FakeHTTPS(HTTPSHandler):
    def __init__(self, replies):
        super().__init__()
        self.replies, self.requests = list(replies), []

    def https_open(self, req):
        self.requests.append(req)
        if not self.replies:
            raise AssertionError("Unexpected request")
        code, header_items, body = self.replies.pop(0)
        headers = Message()
        for key, value in header_items:
            headers.add_header(key, value)
        response = addinfourl(io.BytesIO(body), headers, req.full_url, code)
        response.msg = "OK" if code == 200 else "Found"
        return response


class Cookies(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "cookies.txt"
        isolated_login = patch.object(d, "login_state_dir", return_value=Path(self.temp.name)/"managed")
        isolated_login.start()
        self.addCleanup(isolated_login.stop)

    def client(self, data):
        self.path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")
        return d.PublicHTTP(cookies_file=self.path)

    def header(self, client, url=PAGE):
        request = Request(url)
        client.cookies.add_cookie_header(request)
        return request.get_header("Cookie")

    def error(self, code, data):
        with self.assertRaises(d.DownloadError) as raised:
            self.client(data)
        self.assertEqual(raised.exception.code, code)
        self.assertNotIn("FAKE_TEST_VALUE", str(raised.exception))

    @contextlib.contextmanager
    def network(self, replies):
        handler = FakeHTTPS(replies)
        def factory(*handlers):
            return build_opener(*handlers, handler)
        with patch.object(d, "build_opener", side_effect=factory), patch.object(d.socket, "getaddrinfo", return_value=DNS):
            yield handler

    def test_default_session_never_loads_file_or_profile(self):
        with patch.object(d, "load_cookie_file", side_effect=AssertionError("Unexpected cookie file access")):
            client = d.PublicHTTP()
            self.assertEqual(len(client.cookies), 0)
            self.assertIsNone(self.header(client))

    def test_json_array_and_storage_state(self):
        for data in ([exported()], {"cookies": [exported()], "origins": [{"localStorage": []}]}):
            client = self.client(data)
            self.assertEqual(self.header(client), "session_fixture=FAKE_TEST_VALUE")
            self.assertEqual(client.imported_cookies, 1)

    def test_editor_expiration_date_and_utf8_bom(self):
        entry = exported()
        del entry["expires"]
        entry["expirationDate"] = time.time() + 3600
        self.assertIsNotNone(self.header(self.client("\ufeff" + json.dumps([entry]))))

    def test_netscape_httponly_and_session(self):
        source = "# Netscape HTTP Cookie File\n#HttpOnly_.douyin.com\tTRUE\t/\tTRUE\t0\tsession_fixture\tFAKE_TEST_VALUE\n"
        client = self.client(source)
        self.assertEqual(self.header(client), "session_fixture=FAKE_TEST_VALUE")
        self.assertTrue(next(iter(client.cookies)).discard)

    def test_empty_cookie_value_in_last_netscape_line(self):
        client = self.client(".douyin.com\tTRUE\t/\tTRUE\t0\tempty\t")
        self.assertEqual(self.header(client), "empty=")

    def test_only_official_domains_imported(self):
        client = self.client([exported(), exported(domain=".example.com"), exported(domain=".douyin.com.evil.example")])
        self.assertEqual(len(client.cookies), 1)
        for url in ("https://cdn.example/video.mp4", "https://douyin.com.evil.example/video", "https://www.iesdouyin.com/share/video/1/"):
            self.assertIsNone(self.header(client, url))

    def test_other_official_domain_keeps_own_scope(self):
        client = self.client([exported(domain=".iesdouyin.com")])
        self.assertIsNone(self.header(client))
        self.assertIsNotNone(self.header(client, "https://www.iesdouyin.com/share/video/1/"))

    def test_host_only_cookie_never_expands_to_subdomains(self):
        for entry in (exported(domain="www.douyin.com"), exported(domain=".www.douyin.com", hostOnly=True)):
            client = self.client([entry])
            self.assertIsNotNone(self.header(client))
            self.assertIsNone(self.header(client, "https://sub.www.douyin.com/video/1"))
            self.assertIsNone(self.header(client, "https://v.douyin.com/abc/"))

    def test_path_boundary_and_https_only(self):
        client = self.client([exported(path="/video", secure=False)])
        self.assertIsNotNone(self.header(client))
        for url in ("https://www.douyin.com/videosecret/1", "https://www.douyin.com/other", PAGE.replace("https:", "http:")):
            self.assertIsNone(self.header(client, url))

    def test_expired_filtered_and_explicit_all_expired_error(self):
        entry = exported(name="expired", expires=time.time() - 1)
        client = self.client([entry, exported()])
        self.assertEqual(len(client.cookies), 1)
        self.error("cookies_expired", [entry])

    def test_session_expiry_conventions(self):
        for expiry in (None, -1, 0):
            client = self.client([exported(expires=expiry)])
            self.assertIsNotNone(self.header(client))
            self.assertTrue(next(iter(client.cookies)).discard)

    def test_malformed_secret_sanitized(self):
        for data in ("session=FAKE_TEST_VALUE", "[{FAKE_TEST_VALUE", {}, [42],
                     [exported(value="FAKE_TEST_VALUE\r\nX-Leak: yes")],
                     [exported(value="FAKE_TEST_VALUE; injected=yes")],
                     [exported(value="FAKE_TEST_VALUE😀")], [exported(name="invalid;name")],
                     [exported(path="no-slash")], [exported(secure="TRUE")],
                     [exported(hostOnly="false")], [exported(expires=float("inf"))]):
            self.error("invalid_cookies", data)

    def test_no_scoped_cookies_reported(self):
        for data in ([], [exported(domain=".example.com")], "# Empty export\n"):
            self.error("cookies_unavailable", data)

    def test_missing_unreadable_and_oversized_files(self):
        with self.assertRaises(d.DownloadError) as raised:
            d.PublicHTTP(cookies_file=self.path)
        self.assertEqual(raised.exception.code, "cookie_file_error")
        self.path.write_bytes(b"\xff")
        with self.assertRaises(d.DownloadError) as raised:
            d.PublicHTTP(cookies_file=self.path)
        self.assertEqual(raised.exception.code, "cookie_file_error")
        self.error("invalid_cookies", "x" * (d.MAX_COOKIE_BYTES + 1))

    def test_guest_set_cookie_reused_and_updated_without_disk_write(self):
        client = d.PublicHTTP()
        with self.network([
            (200, [("Set-Cookie", "guest=first; Domain=.douyin.com; Path=/; Secure; HttpOnly")], b"first"),
            (200, [("Set-Cookie", "guest=second; Domain=.douyin.com; Path=/; Secure")], b"second"),
            (200, [], b"last"),
        ]) as fake:
            for _ in range(3):
                with client.open(PAGE, page=True) as response:
                    response.read()
        self.assertEqual([req.get_header("Cookie") for req in fake.requests], [None, "guest=first", "guest=second"])
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_redirect_receives_set_cookie_for_matching_destination(self):
        client = d.PublicHTTP()
        with self.network([
            (302, [("Location", PAGE), ("Set-Cookie", "guest=redirect; Domain=.douyin.com; Path=/; Secure")], b""),
            (200, [], b"target"),
        ]) as fake:
            with client.open("https://v.douyin.com/test/", page=True) as response:
                response.read()
        self.assertIsNone(fake.requests[0].get_header("Cookie"))
        self.assertEqual(fake.requests[1].get_header("Cookie"), "guest=redirect")

    def test_media_redirect_does_not_leak_login_cookie(self):
        client = self.client([exported()])
        with self.network([
            (302, [("Location", "https://cdn.example/video.mp4")], b""),
            (200, [("Set-Cookie", "injected=bad; Domain=.douyin.com; Path=/")], b"video"),
        ]) as fake:
            with client.open("https://www.douyin.com/aweme/v1/play/", page=False) as response:
                response.read()
        self.assertEqual(fake.requests[0].get_header("Cookie"), "session_fixture=FAKE_TEST_VALUE")
        self.assertIsNone(fake.requests[1].get_header("Cookie"))
        self.assertEqual(len(client.cookies), 1)

    def test_page_to_media_uses_same_session(self):
        client = d.PublicHTTP()
        with self.network([
            (200, [("Set-Cookie", "guest=kept; Domain=.douyin.com; Path=/; Secure")], b"page"),
            (200, [], b"video"),
        ]) as fake:
            client.page(PAGE)
            with client.open("https://www.douyin.com/aweme/v1/play/", page=False) as response:
                response.read()
        self.assertEqual(fake.requests[1].get_header("Cookie"), "guest=kept")

    def test_server_removes_expired_cookie(self):
        client = self.client([exported()])
        with self.network([
            (200, [("Set-Cookie", "session_fixture=; Domain=.douyin.com; Path=/; Max-Age=0; Secure")], b"page"),
            (200, [], b"next"),
        ]) as fake:
            client.page(PAGE)
            client.page(PAGE)
        self.assertIsNotNone(fake.requests[0].get_header("Cookie"))
        self.assertIsNone(fake.requests[1].get_header("Cookie"))

    def test_different_tasks_do_not_share_sessions(self):
        first = self.client([exported()])
        self.assertIsNotNone(self.header(first))
        self.assertIsNone(self.header(d.PublicHTTP()))

    def test_cli_import_and_error_redaction(self):
        self.client([exported()])
        html = '<script>window._ROUTER_DATA = ' + json.dumps({
            "aweme_id": "1234567890123456789", "video": {"play_addr": {"url_list": ["https://cdn.example/video.mp4"]}}}) + ';</script>'
        output = io.StringIO()
        with self.network([(200, [], html.encode())]) as fake, contextlib.redirect_stdout(output):
            result = d.main([PAGE, "--resolve-only", "--cookies-file", str(self.path)])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "resolved")
        self.assertEqual(fake.requests[0].get_header("Cookie"), "session_fixture=FAKE_TEST_VALUE")
        self.assertNotIn("FAKE_TEST_VALUE", output.getvalue())
        self.path.write_text("[{FAKE_TEST_VALUE", encoding="utf-8")
        output = io.StringIO()
        with self.network([]) as fake, contextlib.redirect_stdout(output):
            self.assertEqual(d.main([PAGE, "--cookies-file", str(self.path)]), 2)
        self.assertEqual(json.loads(output.getvalue())["code"], "invalid_cookies")
        self.assertNotIn("FAKE_TEST_VALUE", output.getvalue())
        self.assertEqual(fake.requests, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
