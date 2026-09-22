#!/usr/bin/env python3
"""Real headless browser smoke test using locally fulfilled HTML; no real login/network."""
import json
from pathlib import Path
import sys
import time

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"skills/douyin-video-download/scripts"))
from qr_login import BrowserQRDriver, LOGIN_URL

PIXEL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="


def fixture(message="", hidden=False):
    display = "none" if hidden else "block"
    return f'''<!doctype html><meta charset="utf-8"><body>
    <button onclick="document.querySelector('.panel').style.display='block';this.remove()">登录</button>
    <div class="panel" style="display:{display};width:440px;height:480px;margin:20px;padding:20px">
    <h2>扫码登录</h2><img width="200" height="200" src="{PIXEL}" alt="离线测试图片，不是真实登录二维码">
    <p>{message}</p></div></body>'''


def main():
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True, chromium_sandbox=True)
        try:
            context = browser.new_context(viewport={"width":1280,"height":900})
            routed = []
            def offline(route):
                routed.append(route.request.url)
                if route.request.url == LOGIN_URL:
                    route.fulfill(status=200, content_type="text/html; charset=utf-8", body=fixture(hidden=True))
                else:
                    route.abort()
            context.route("**/*", offline)
            driver = BrowserQRDriver()
            driver.context = context
            driver.page = context.new_page()
            driver.page.goto(LOGIN_URL)
            driver.opened_at = time.monotonic()
            assert driver.snapshot()["status"] == "starting"
            waiting = driver.snapshot()
            assert waiting["status"] == "waiting_scan"
            assert waiting["image"].startswith(b"\x89PNG\r\n\x1a\n")
            driver.page.set_content(fixture("扫码成功，请在手机上确认登录"))
            assert driver.snapshot()["status"] == "waiting_confirmation"
            driver.page.set_content(fixture("二维码已过期，点击刷新"))
            assert driver.snapshot()["status"] == "qr_expired"
            driver.page.set_content("<body><p>个人主页</p></body>")
            driver.opened_at = time.monotonic()-21
            assert driver.snapshot()["status"] == "login_ui_unavailable"
            context.add_cookies([{"name":"sessionid", "value":"FAKE_BROWSER_FIXTURE", "domain":".douyin.com",
                                 "path":"/", "secure":True, "httpOnly":True, "expires":time.time()+3600}])
            driver.page.set_content(fixture())
            assert driver.snapshot()["status"] == "waiting_scan", "Cookie alone must not complete an open login panel"
            driver.page.set_content("<body><p>个人主页</p></body>")
            completed = driver.snapshot()
            assert completed["status"] == "session_saved"
            assert "image" not in completed
            driver.page.set_content("<body><p>请完成安全验证</p></body>")
            assert driver.snapshot()["status"] == "verification_required"
            print(json.dumps({"headless_browser":"passed", "cases":8, "external_requests":0,
                              "local_fixture_routes":len(routed), "real_account_login_tested":False}))
        finally:
            browser.close()


if __name__ == "__main__":
    main()
