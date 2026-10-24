"""Transport-neutral phone login actions for workflow forms and future plugins.

Cookies stay in the local cache. A short-lived challenge binds completion to
the exact SMS request, even when requests for the same phone overlap.
"""
from __future__ import annotations

import json
import secrets
from typing import Any, Mapping

if __package__:
    from . import client
else:
    import client


CHALLENGE_TTL_SECONDS = 600
CHALLENGE_PREFIX = "__sms_challenge__:"


def prepare(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = client.prepare_login(payload)
    return {
        key: result[key]
        for key in ("mobile", "needs_sms_code", "cookies_ready", "cache_status")
    }


def send(payload: Mapping[str, Any]) -> dict[str, Any]:
    mobile = client.mobile_from_payload(payload)
    result = client.send_code(payload)
    challenge_id = secrets.token_urlsafe(32)
    client.save_login_cookies(
        mobile,
        CHALLENGE_PREFIX + challenge_id,
        client.json_to_cookiejar(result["activation_cookies"]),
    )
    with client.cache_connection() as connection:
        connection.execute(
            """DELETE FROM baiying_cookie_cache
            WHERE account_key LIKE ?
              AND strftime('%s', 'now') - strftime('%s', updated_at) > ?""",
            (CHALLENGE_PREFIX + "%", CHALLENGE_TTL_SECONDS),
        )
    masked_mobile = mobile[:3] + "****" + mobile[-4:]
    return {
        "mobile": mobile,
        "masked_mobile": masked_mobile,
        "sms_sent": True,
        "challenge_id": challenge_id,
        "expires_in_seconds": CHALLENGE_TTL_SECONDS,
        "message": f"验证码已发送至 {masked_mobile}，请填写手机收到的 6 位短信验证码。",
    }


def complete(payload: Mapping[str, Any]) -> dict[str, Any]:
    mobile = client.mobile_from_payload(payload)
    code = str(payload.get("verification_code") or "").strip()
    if not code.isascii() or not code.isdigit() or len(code) != 6:
        raise ValueError("请输入 6 位数字短信验证码，请勿填写手机号。")
    challenge_id = str(payload.get("challenge_id") or "").strip()
    if not challenge_id:
        raise ValueError("缺少短信登录会话，请先获取验证码。")
    key = CHALLENGE_PREFIX + challenge_id
    with client.cache_connection() as connection:
        row = connection.execute(
            """SELECT cookie_json,
                      strftime('%s', 'now') - strftime('%s', updated_at)
               FROM baiying_cookie_cache WHERE mobile_hash=? AND account_key=?""",
            (client.mobile_hash(mobile), key),
        ).fetchone()
    if row is None or row[1] is None or row[1] > CHALLENGE_TTL_SECONDS:
        raise ValueError("短信登录会话已失效，请重新获取验证码。")

    # Complete this challenge only; never resend SMS or use another run's cookies.
    result = client.login({
        **payload,
        "mobile": mobile,
        "verification_code": code,
        "activation_cookies": json.loads(row[0]),
        "account_key": "__platform__",
    })
    if not result.get("authenticated"):
        raise RuntimeError("百应登录未成功，请重新获取验证码后再试。")
    with client.cache_connection() as connection:
        connection.execute(
            "DELETE FROM baiying_cookie_cache WHERE mobile_hash=? AND account_key=?",
            (client.mobile_hash(mobile), key),
        )
    return {"mobile": mobile, "cookies_ready": True, "cache_status": "refreshed"}
