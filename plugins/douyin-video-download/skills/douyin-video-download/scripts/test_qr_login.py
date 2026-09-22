#!/usr/bin/env python3
"""Offline QR lifecycle, private persistence and automatic reuse tests."""
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import download_video as d
import qr_login as q

FAKE_PNG = b"\x89PNG\r\n\x1a\nFAKE-QR-FIXTURE"


def cookie(**changes):
    return {"name":"sessionid", "value":"FAKE_QR_COOKIE", "domain":".douyin.com", "path":"/", "secure":True, "expires":time.time()+3600, **changes}


class FakeDriver:
    def __init__(self):
        self.opened = threading.Event()
        self.closed = threading.Event()
        self.state = {"status":"waiting_scan", "message":"Scan test fixture", "image":FAKE_PNG}
        self.refreshes = 0
        self.open_error = None

    def open(self):
        self.opened.set()
        if self.open_error is not None:
            raise self.open_error

    def snapshot(self):
        return self.state

    def refresh(self):
        self.refreshes += 1
        self.state = {"status":"waiting_scan", "message":"Fresh test fixture", "image":FAKE_PNG+b"2"}

    def close(self):
        self.closed.set()


class LoginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)/"auth"
        self.driver = FakeDriver()
        self.manager = q.LoginManager(self.directory, driver_factory=lambda: self.driver, poll_seconds=0.01)
        self.addCleanup(self.manager.close)
        isolated = patch.object(d, "login_state_dir", return_value=self.directory)
        isolated.start()
        self.addCleanup(isolated.stop)

    def start(self):
        state, image = self.manager.start(wait_seconds=1)
        self.assertTrue(self.driver.opened.is_set())
        self.assertEqual(state["status"], "waiting_scan")
        self.assertEqual(image, FAKE_PNG)
        return state["session_id"]

    def wait_for(self, session_id, status):
        deadline = time.monotonic()+2
        while time.monotonic() < deadline:
            state, image = self.manager.status(session_id)
            if state["status"] == status:
                return state, image
            time.sleep(0.01)
        self.fail(f"Session never reached {status}")

    def test_qr_display_is_not_a_saved_login(self):
        session_id = self.start()
        state, _ = self.manager.status(session_id)
        self.assertNotIn("cookies_file", state)
        self.assertFalse(self.directory.exists())

    def test_repeated_start_reuses_active_code(self):
        session_id = self.start()
        other, image = self.manager.start(wait_seconds=0)
        self.assertEqual(other["session_id"], session_id)
        self.assertEqual(image, FAKE_PNG)

    def test_guest_and_unrelated_cookies_never_confirm_login(self):
        for cookies in ([cookie(name="ttwid")], [cookie(domain=".other.example")], [cookie(value="")], [cookie(expires=time.time()-1)]):
            self.assertFalse(q.has_login_cookie(cookies))
            with self.assertRaises(d.DownloadError) as raised:
                q.save_login(cookies, self.directory)
            self.assertEqual(raised.exception.code, "login_unconfirmed")
        self.assertFalse(self.directory.exists())

    def test_confirmation_saved_privately_without_values_in_result(self):
        session_id = self.start()
        self.driver.state = {"status":"session_saved", "cookies":[cookie(),cookie(domain=".other.example")]}
        state, image = self.wait_for(session_id, "session_saved")
        self.assertIsNone(image)
        self.assertNotIn("FAKE_QR_COOKIE", json.dumps(state))
        saved = Path(state["cookies_file"])
        content = json.loads(saved.read_text("utf-8"))
        self.assertEqual(len(content["cookies"]), 1)
        self.assertEqual(content["source"], "douyin-qr-login")
        self.assertTrue(self.driver.closed.wait(1))
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(saved.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)

    def test_saved_login_automatically_reused_and_guest_can_skip(self):
        saved = q.save_login([cookie()], self.directory)
        selected = d.cookie_file_for_request()
        self.assertEqual(selected, saved)
        client = d.PublicHTTP(cookies_file=selected)
        self.assertEqual(client.imported_cookies, 1)
        self.assertIsNone(d.cookie_file_for_request(guest=True))
        explicit = Path(self.temp.name)/"chosen-export.json"
        self.assertEqual(d.cookie_file_for_request(explicit), explicit)

    def test_no_saved_login_stays_guest(self):
        self.assertIsNone(d.cookie_file_for_request())
        self.assertFalse(self.directory.exists())

    def test_expired_saved_cookie_requests_new_login(self):
        q.private_write(self.directory/"cookies.json", json.dumps({"cookies":[cookie(expires=time.time()-1)]}).encode())
        with self.assertRaises(d.DownloadError) as raised:
            d.PublicHTTP(cookies_file=d.cookie_file_for_request())
        self.assertEqual(raised.exception.code, "cookies_expired")

    def test_refresh_replaces_image_without_extending_deadline(self):
        session_id = self.start()
        deadline = self.manager.get(session_id).deadline
        state, image = self.manager.status(session_id, refresh=True)
        self.assertEqual(state["status"], "starting")
        self.assertIsNone(image)
        state, image = self.wait_for(session_id, "waiting_scan")
        self.assertEqual(image, FAKE_PNG+b"2")
        self.assertEqual(self.driver.refreshes, 1)
        self.assertEqual(self.manager.get(session_id).deadline, deadline)

    def test_refresh_count_is_bounded(self):
        session_id = self.start()
        for _ in range(3):
            self.manager.status(session_id, refresh=True)
            self.wait_for(session_id, "waiting_scan")
        with self.assertRaises(d.DownloadError) as raised:
            self.manager.status(session_id, refresh=True)
        self.assertEqual(raised.exception.code, "login_refresh_limit")

    def test_refresh_during_screenshot_does_not_return_old_code(self):
        session_id = self.start()
        in_snapshot, release = threading.Event(), threading.Event()
        original = self.driver.snapshot
        def delayed_snapshot():
            old_state = original()
            in_snapshot.set()
            release.wait(1)
            return old_state
        self.driver.snapshot = delayed_snapshot
        self.assertTrue(in_snapshot.wait(1))
        self.manager.status(session_id, refresh=True)
        self.driver.snapshot = original
        release.set()
        state, image = self.wait_for(session_id, "waiting_scan")
        self.assertEqual(image, FAKE_PNG+b"2")

    def test_cancel_preserves_old_login_and_closes_browser(self):
        saved = q.save_login([cookie(value="OLD_TEST_SESSION")], self.directory)
        previous = saved.read_bytes()
        session_id = self.start()
        state, image = self.manager.cancel(session_id)
        self.assertEqual(state["status"], "cancelled")
        self.assertIsNone(image)
        self.driver.state = {"status":"session_saved", "cookies":[cookie()]}
        self.assertTrue(self.driver.closed.wait(1))
        self.assertEqual(saved.read_bytes(), previous)
        self.assertEqual(self.manager.status(session_id)[0]["status"], "cancelled")

    def test_timeout_does_not_save_cookie_or_keep_qr(self):
        session_id = self.start()
        self.manager.get(session_id).deadline = time.monotonic()-1
        state, image = self.wait_for(session_id, "timeout")
        self.assertIsNone(image)
        self.assertFalse(self.directory.exists())
        self.assertTrue(self.driver.closed.wait(1))

    def test_challenge_stops_without_saving(self):
        session_id = self.start()
        self.driver.state = {"status":"verification_required", "message":"Manual verification required", "image":FAKE_PNG}
        state, image = self.wait_for(session_id, "verification_required")
        self.assertIsNotNone(image)
        self.assertFalse(self.directory.exists())
        self.assertTrue(self.driver.closed.wait(1))

    def test_missing_qr_is_reported_without_login_success(self):
        session_id = self.start()
        self.driver.state = {"status":"login_ui_unavailable", "message":"No QR detected", "image":FAKE_PNG}
        state, _ = self.wait_for(session_id, "login_ui_unavailable")
        self.assertFalse(self.directory.exists())

    def test_unexpected_browser_error_never_exposes_values(self):
        self.driver.open_error = RuntimeError("FAKE_QR_COOKIE and private URL")
        state, image = self.manager.start(wait_seconds=1)
        self.assertEqual(state["status"], "error")
        self.assertEqual(state["code"], "login_browser_error")
        self.assertNotIn("FAKE_QR_COOKIE", json.dumps(state))
        self.assertIsNone(image)
        self.assertTrue(self.driver.closed.wait(1))

    def test_dependency_error_is_actionable(self):
        self.driver.open_error = d.DownloadError("browser_dependency_missing", "Install optional dependencies")
        state, _ = self.manager.start(wait_seconds=1)
        self.assertEqual(state["code"], "browser_dependency_missing")

    def test_bad_session_and_timeout_are_structured(self):
        for action in (self.manager.status, self.manager.cancel):
            with self.assertRaises(d.DownloadError) as raised:
                action("not-a-session")
            self.assertEqual(raised.exception.code, "login_session_missing")
        for timeout in (0, 29, 601):
            with self.assertRaises(d.DownloadError) as raised:
                self.manager.start(timeout)
            self.assertEqual(raised.exception.code, "invalid_limit")

    def test_atomic_save_failure_keeps_previous_session(self):
        target = q.save_login([cookie(value="OLD_TEST_SESSION")], self.directory)
        previous = target.read_bytes()
        with patch.object(q.os, "replace", side_effect=OSError("FAKE_QR_COOKIE")):
            with self.assertRaises(d.DownloadError) as raised:
                q.save_login([cookie()], self.directory)
        self.assertEqual(raised.exception.code, "login_storage_error")
        self.assertNotIn("FAKE_QR_COOKIE", str(raised.exception))
        self.assertEqual(target.read_bytes(), previous)
        self.assertEqual(list(self.directory.iterdir()), [target])

    @unittest.skipUnless(os.name == "posix", "Requires unprivileged symlink support")
    def test_symlinks_do_not_redirect_saved_credentials(self):
        elsewhere = Path(self.temp.name)/"other"
        elsewhere.mkdir()
        self.directory.symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaises(d.DownloadError):
            q.save_login([cookie()], self.directory)
        self.assertEqual(list(elsewhere.iterdir()), [])
        with self.assertRaises(d.DownloadError):
            d.cookie_file_for_request()


if __name__ == "__main__":
    unittest.main(verbosity=2)
