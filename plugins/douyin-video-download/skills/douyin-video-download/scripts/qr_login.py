#!/usr/bin/env python3
"""Headless official-page QR login. Only the user scans and confirms on their phone."""
from __future__ import annotations

import argparse
import atexit
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import uuid

import download_video as downloader

LOGIN_URL = "https://www.douyin.com/"
TERMINAL = {"session_saved", "cancelled", "timeout", "error", "verification_required", "login_ui_unavailable"}
AUTH_COOKIE_NAMES = {"sessionid", "sessionid_ss"}
QR_TEXT = re.compile(r"扫码登录|使用抖音.*扫码|打开抖音.*扫|抖音.*扫一扫")
CHALLENGE_TEXT = re.compile(r"请完成安全验证|拖动.*滑块|请完成下方验证|验证码.*验证|安全验证中")


def scoped_cookies(cookies: list[dict]) -> list[dict]:
    now = time.time()
    result = []
    for cookie in cookies:
        domain = str(cookie.get("domain", "")).lstrip(".").lower()
        if not downloader.is_douyin_host(domain):
            continue
        expires = cookie.get("expires", -1)
        if not isinstance(expires, (int, float)) or not math.isfinite(expires):
            continue
        if expires > 0 and expires <= now:
            continue
        result.append(cookie)
    return result


def has_login_cookie(cookies: list[dict]) -> bool:
    return any(c.get("name") in AUTH_COOKIE_NAMES and bool(c.get("value")) for c in scoped_cookies(cookies))


def private_directory(directory: Path) -> None:
    # Do not follow a replacement symlink into a project or somebody else's directory.
    if any(parent.is_symlink() for parent in [directory, *directory.parents]):
        raise downloader.DownloadError("login_storage_error", "登录状态目录不能经过符号链接")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(directory, 0o700)


def private_write(path: Path, data: bytes) -> None:
    temporary = None
    try:
        private_directory(path.parent)
        if path.is_symlink():
            raise downloader.DownloadError("login_storage_error", "登录状态文件不能是符号链接")
        fd, name = tempfile.mkstemp(prefix=".login-", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "posix":
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError:
        raise downloader.DownloadError("login_storage_error", "无法安全保存登录状态，请检查当前用户的目录权限") from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def save_login(cookies: list[dict], directory: Path) -> Path:
    selected = scoped_cookies(cookies)
    if not has_login_cookie(selected):
        raise downloader.DownloadError("login_unconfirmed", "尚未检测到扫码后的登录会话，未保存 Cookie")
    payload = {"source": "douyin-qr-login", "saved_at": int(time.time()), "cookies": selected}
    target = directory / "cookies.json"
    private_write(target, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    return target


class BrowserQRDriver:
    """Use the normal official login UI, with no private API/signature synthesis."""
    def __init__(self):
        self.runtime = self.browser = self.context = self.page = None
        self.clicked_login = False
        self.opened_at = 0.0

    def open(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise downloader.DownloadError("browser_dependency_missing", "扫码登录需安装 requirements-browser.txt 及 Playwright Chromium") from None
        self.runtime = sync_playwright().start()
        try:
            self.browser = self.runtime.chromium.launch(headless=True, chromium_sandbox=True)
        except Exception:
            raise downloader.DownloadError("browser_launch_failed", "无法启动无头 Chromium；请安装浏览器及 Linux 系统依赖，并使用支持浏览器沙箱的普通用户运行") from None
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 900}, locale="zh-CN", accept_downloads=False)
        self.page = self.context.new_page()
        self.page.set_default_timeout(3000)
        self.page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
        self.opened_at = time.monotonic()

    @staticmethod
    def visible(locator, limit=12):
        for index in range(min(locator.count(), limit)):
            candidate = locator.nth(index)
            if candidate.is_visible():
                yield candidate

    def official_frames(self):
        from urllib.parse import urlsplit
        return [frame for frame in self.page.frames
                if urlsplit(frame.url).scheme == "https"
                and downloader.is_douyin_host((urlsplit(frame.url).hostname or "").lower())]

    def login_panel(self):
        for frame in self.official_frames():
            for label in self.visible(frame.get_by_text(QR_TEXT)):
                parent = label
                for _ in range(6):
                    parent = parent.locator("xpath=..")
                    bounds = parent.bounding_box()
                    if not bounds or bounds["width"] > 1100 or bounds["height"] > 880:
                        break
                    if bounds["width"] < 180 or bounds["height"] < 180:
                        continue
                    for graphic in self.visible(parent.locator("img, canvas, svg")):
                        box = graphic.bounding_box()
                        if box and 100 <= box["width"] <= 420 and 0.8 <= box["height"] / box["width"] <= 1.2:
                            return parent
        return None

    def visible_text(self):
        return "\n".join(frame.locator("body").inner_text(timeout=2000)[:30000] for frame in self.official_frames())

    def snapshot(self) -> dict:
        text = self.visible_text()
        if CHALLENGE_TEXT.search(text):
            return {"status": "verification_required", "message": "官方页面要求额外验证，本版无法在聊天中完成该验证，已停止；可使用交互式浏览器登录后导入 Cookie 的备用方式。", "image": self.page.screenshot()}
        cookies = scoped_cookies(self.context.cookies())
        panel = self.login_panel()
        if has_login_cookie(cookies) and panel is None:
            return {"status": "session_saved", "cookies": cookies,
                    "message": "扫码后的登录会话已出现，登录面板已关闭；即将保存供后续下载复用。"}
        if panel is not None:
            panel_text = panel.inner_text()
            if re.search(r"二维码.*失效|二维码.*过期|点击刷新|刷新二维码", panel_text):
                status, message = "qr_expired", "二维码已过期，调用登录状态工具并设置 refresh=true 获取新二维码。"
            elif re.search(r"扫码成功|扫描成功|请在手机上确认登录|请打开手机确认", panel_text):
                status, message = "waiting_confirmation", "请在手机抖音上确认本次登录。"
            else:
                status, message = "waiting_scan", "用手机抖音扫描官方登录面板中的二维码，并在手机上确认；会话将保存在本服务器供后续下载复用。"
            return {"status": status, "message": message, "image": panel.screenshot()}
        if not self.clicked_login:
            for frame in self.official_frames():
                for button in self.visible(frame.get_by_text("登录", exact=True)):
                    button.click()
                    self.clicked_login = True
                    return {"status": "starting", "message": "正在等待官方扫码登录面板加载。"}
        if time.monotonic() - self.opened_at > 20:
            return {"status": "login_ui_unavailable", "message": "未识别到官方二维码登录面板，可能是页面改版或访问受限；未保存登录状态。", "image": self.page.screenshot()}
        return {"status": "starting", "message": "正在加载官方扫码登录页面。"}

    def refresh(self):
        self.page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
        self.clicked_login = False
        self.opened_at = time.monotonic()

    def close(self):
        try:
            if self.browser is not None:
                self.browser.close()
        finally:
            if self.runtime is not None:
                self.runtime.stop()


class LoginSession:
    def __init__(self, timeout: int):
        self.id = uuid.uuid4().hex
        self.deadline = time.monotonic() + timeout
        self.data = {"session_id": self.id, "status": "starting", "message": "正在启动服务器上的无头浏览器。"}
        self.image = None
        self.lock = threading.RLock()
        self.cancelled = threading.Event()
        self.refresh_requested = threading.Event()
        self.ready = threading.Event()
        self.refreshes = 0
        self.thread = None

    def update(self, state: dict):
        with self.lock:
            self.image = state.get("image")
            self.data = {"session_id": self.id, **{key: value for key, value in state.items() if key not in {"image", "cookies"}}}
            if state["status"] != "starting":
                self.ready.set()

    def result(self):
        with self.lock:
            remaining = max(0, math.ceil(self.deadline - time.monotonic()))
            return {**self.data, "expires_in_seconds": remaining}, self.image


class LoginManager:
    def __init__(self, directory: Path | None = None, driver_factory=BrowserQRDriver, poll_seconds: float = 2):
        self.directory = directory
        self.driver_factory = driver_factory
        self.poll_seconds = poll_seconds
        self.sessions = {}
        self.lock = threading.RLock()

    def start(self, timeout: int = 180, wait_seconds: float = 8):
        if not 30 <= timeout <= 600:
            raise downloader.DownloadError("invalid_limit", "扫码等待时间需在 30 到 600 秒之间")
        with self.lock:
            for active in self.sessions.values():
                if active.data["status"] not in TERMINAL or (active.thread is not None and active.thread.is_alive()):
                    # Repeated calls reuse the same browser and QR rather than changing the code under the user.
                    return active.result()
            while len(self.sessions) >= 8:
                self.sessions.pop(next(iter(self.sessions)))
            session = LoginSession(timeout)
            self.sessions[session.id] = session
            session.thread = threading.Thread(target=self._run, args=(session,), name="douyin-qr-login", daemon=True)
            session.thread.start()
        session.ready.wait(min(wait_seconds, 15))
        return session.result()

    def _run(self, session: LoginSession):
        driver = None
        try:
            driver = self.driver_factory()
            driver.open()
            while not session.cancelled.is_set():
                if time.monotonic() >= session.deadline:
                    session.update({"status": "timeout", "message": "扫码登录等待超时；未修改已保存的登录会话。"})
                    return
                if session.refresh_requested.is_set():
                    session.refresh_requested.clear()
                    driver.refresh()
                state = driver.snapshot()
                if session.refresh_requested.is_set():
                    # A refresh requested during a screenshot must not redisplay the old QR.
                    continue
                if state["status"] == "session_saved":
                    with session.lock:
                        if session.cancelled.is_set():
                            break
                        if time.monotonic() >= session.deadline:
                            session.update({"status": "timeout", "message": "扫码登录等待超时；未修改已保存的登录会话。"})
                            return
                        path = save_login(state["cookies"], self.directory or downloader.login_state_dir())
                        session.update({"status": "session_saved", "cookies_file": str(path),
                                        "message": "已保存扫码后的登录会话。后续下载会自动复用；无需手动导出或传入 Cookie。"})
                    return
                with session.lock:
                    if session.cancelled.is_set():
                        break
                    session.update(state)
                if state["status"] in TERMINAL:
                    return
                session.cancelled.wait(self.poll_seconds)
            session.update({"status": "cancelled", "message": "已取消扫码登录；未修改已保存的登录会话。"})
        except downloader.DownloadError as exc:
            if not session.cancelled.is_set():
                session.update({"status": "error", "code": exc.code, "message": exc.message})
        except Exception:
            if not session.cancelled.is_set():
                session.update({"status": "error", "code": "login_browser_error", "message": "浏览器登录流程失败；请检查服务器网络、浏览器依赖或官方页面是否可访问。"})
        finally:
            try:
                if driver is not None:
                    driver.close()
            except Exception:
                pass
            session.ready.set()

    def get(self, session_id: str):
        with self.lock:
            if session_id not in self.sessions:
                raise downloader.DownloadError("login_session_missing", "未找到此登录会话；服务重启后需要重新发起扫码登录")
            return self.sessions[session_id]

    def status(self, session_id: str, refresh: bool = False):
        session = self.get(session_id)
        with session.lock:
            if refresh and session.data["status"] in {"waiting_scan", "qr_expired", "waiting_confirmation"}:
                if session.refreshes >= 3:
                    raise downloader.DownloadError("login_refresh_limit", "本次登录已刷新三次，请取消后重新发起")
                session.refreshes += 1
                session.update({"status": "starting", "message": "正在刷新官方二维码；等待时间不会延长。"})
                session.refresh_requested.set()
            return session.result()

    def cancel(self, session_id: str):
        session = self.get(session_id)
        with session.lock:
            if session.data["status"] not in TERMINAL:
                session.cancelled.set()
                session.update({"status": "cancelled", "message": "已取消扫码登录，正在关闭浏览器；已有会话保持不变。"})
            return session.result()

    def close(self):
        with self.lock:
            sessions = list(self.sessions.values())
        for session in sessions:
            session.cancelled.set()
        for session in sessions:
            if session.thread is not None:
                session.thread.join(timeout=3)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args(argv)
    manager = LoginManager()
    atexit.register(manager.close)
    qr_path = None
    try:
        result, png = manager.start(args.timeout)
        qr_path = downloader.login_state_dir() / ("qr-" + result["session_id"] + ".png")
        previous = None
        while True:
            if png is not None:
                private_write(qr_path, png)
                result["qr_image_path"] = str(qr_path)
            summary = {key: value for key, value in result.items() if key != "expires_in_seconds"}
            if summary != previous:
                print(json.dumps(result, ensure_ascii=False), flush=True)
                previous = summary
            if result["status"] in TERMINAL:
                return 0 if result["status"] == "session_saved" else 2
            time.sleep(2)
            result, png = manager.status(result["session_id"])
    except downloader.DownloadError as exc:
        print(json.dumps({"status": "error", "code": exc.code, "message": exc.message}, ensure_ascii=False), flush=True)
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"status": "cancelled", "message": "已取消扫码登录"}, ensure_ascii=False), flush=True)
        return 2
    finally:
        manager.close()
        if qr_path is not None:
            qr_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
