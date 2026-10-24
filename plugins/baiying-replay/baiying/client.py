"""Reusable Baiying HTTP and cache operations; no workflow or UI dependencies."""

from __future__ import annotations

import getpass
import json
import os
import hashlib
import sqlite3
import time
from urllib.parse import parse_qs, urlsplit
from typing import Any, Mapping
from pathlib import Path

import requests
from requests.cookies import RequestsCookieJar, create_cookie

if __package__:
    from . import diagnostics
else:
    import diagnostics


SSO_BASE_URL = "https://buyin-daren-sso.jinritemai.com/passport/sso"
BUYIN_URL = "https://buyin.jinritemai.com"
REQUIRED_RECOMMEND_REASONS = frozenset({"商品展示直观", "讲解深度到位", "画面品质精良", "视频时长适宜"})
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)
CACHE_PATH = Path(os.environ.get("BAIYING_COOKIE_CACHE_PATH") or (Path(__file__).resolve().parent / "storage" / "baiying_cookie_cache.sqlite3"))


def code_trans(value: Any) -> str:
    """Apply Buyin's current XOR-then-hex encoding to a form value."""
    return "".join(f"{byte ^ 0x05:02x}" for byte in str(value).encode("utf-8"))


def mobile_from_payload(payload: Mapping[str, Any]) -> str:
    mobile = str(payload.get("mobile") or "").strip()
    if not mobile.isascii() or not mobile.isdigit() or len(mobile) != 11:
        raise ValueError("mobile must be an 11-digit mainland China phone number")
    return mobile


def mobile_hash(mobile: str) -> str:
    return hashlib.sha256(mobile.encode("utf-8")).hexdigest()


def cache_connection() -> sqlite3.Connection:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(CACHE_PATH)
    connection.execute(
        """CREATE TABLE IF NOT EXISTS baiying_cookie_cache (
        mobile_hash TEXT NOT NULL, account_key TEXT NOT NULL, cookie_json TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (mobile_hash, account_key))"""
    )
    return connection


def save_login_cookies(mobile: str, account_key: str, cookies: RequestsCookieJar) -> None:
    with cache_connection() as connection:
        connection.execute(
            """INSERT INTO baiying_cookie_cache (mobile_hash, account_key, cookie_json)
            VALUES (?, ?, ?) ON CONFLICT(mobile_hash, account_key) DO UPDATE SET
            cookie_json=excluded.cookie_json, updated_at=CURRENT_TIMESTAMP""",
            (mobile_hash(mobile), account_key, json.dumps(cookiejar_to_json(cookies), ensure_ascii=False)),
        )


def load_login_cookies(mobile: str, account_key: str = "__platform__") -> list[dict[str, Any]] | None:
    with cache_connection() as connection:
        row = connection.execute(
            "SELECT cookie_json FROM baiying_cookie_cache WHERE mobile_hash=? AND account_key=?",
            (mobile_hash(mobile), account_key),
        ).fetchone()
    return json.loads(row[0]) if row else None


def request_timeout(payload: Mapping[str, Any]) -> float:
    return max(float(payload.get("request_timeout_seconds") or 30), 1)


def payload_bool(payload: Mapping[str, Any], key: str, *, default: bool = False) -> bool:
    value = payload.get(key)
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def sms_tasker_base_url(payload: Mapping[str, Any]) -> str:
    return str(payload.get("sms_tasker_base_url") or os.environ.get("SMS_TASKER_BASE_URL") or "").rstrip("/")


def sms_tasker_token(payload: Mapping[str, Any]) -> str:
    return str(
        payload.get("sms_tasker_token") or os.environ.get("SMS_TASKER_TOKEN") or os.environ.get("SMS_API_TOKEN") or ""
    ).strip()


def sms_platforms(payload: Mapping[str, Any]) -> list[str]:
    raw = payload.get("sms_tasker_platform_candidates") or payload.get("platform_candidates") or [
        payload.get("sms_login_platform") or payload.get("sms_tasker_platform") or "baiying"
    ]
    if isinstance(raw, str):
        raw = raw.split(",")
    aliases = {"baiying": "巨量百应", "巨量百应": "巨量百应"}
    result: list[str] = []
    for value in raw if isinstance(raw, list) else []:
        normalized = aliases.get(str(value).strip(), str(value).strip())
        if normalized and normalized not in result:
            result.append(normalized)
    return result or ["巨量百应"]


def fetch_sms_tasker_code(payload: Mapping[str, Any]) -> str | None:
    """Use the same latest-code / consume protocol as doudian_order_download."""
    base_url, token = sms_tasker_base_url(payload), sms_tasker_token(payload)
    if not base_url:
        return None
    if not token:
        raise ValueError("sms_tasker_token is required when sms_tasker_base_url is configured")
    attempts = max(int(payload.get("sms_code_poll_attempts") or 1), 1)
    delay = max(float(payload.get("sms_code_poll_delay_seconds") or 3), 0)
    purpose = str(payload.get("sms_login_purpose_contains") or "登录").strip()
    session = requests.Session()
    headers = {"Authorization": f"Bearer {token}"}
    for attempt in range(attempts):
        for platform in sms_platforms(payload):
            params: dict[str, Any] = {"consume": "false", "token": token, "platform": platform}
            response = session.get(f"{base_url}/api/codes/latest", params=params, headers=headers, timeout=10)
            response.raise_for_status()
            body = response.json()
            item = body.get("item") if isinstance(body, dict) else None
            code = str(item.get("code") or "").strip() if isinstance(item, dict) and body.get("found") else ""
            # Keep the purpose check local because some older sms-tasker versions
            # do not accept purpose_contains as a query parameter.
            message = str(item.get("message") or item.get("content") or "") if isinstance(item, dict) else ""
            if code.isdigit() and len(code) == 6 and (not message or not purpose or purpose in message):
                if payload_bool(payload, "sms_tasker_consume", default=True):
                    try:
                        session.get(
                            f"{base_url}/api/codes/latest",
                            params={**params, "consume": "true"},
                            headers=headers,
                            timeout=10,
                        ).raise_for_status()
                    except requests.RequestException:
                        pass
                return code
        if attempt < attempts - 1 and delay:
            time.sleep(delay)
    return None


def resolve_verification_code(payload: Mapping[str, Any]) -> str:
    for key in ("verification_code", "login_verification_code"):
        code = str(payload.get(key) or "").strip()
        if code:
            break
    else:
        code = fetch_sms_tasker_code(payload) or ""
    if not code and payload_bool(payload, "allow_manual_prompt", default=True):
        try:
            code = getpass.getpass("请输入百应登录短信验证码: ").strip()
        except (EOFError, OSError):
            code = ""
    if not code.isdigit() or len(code) != 6:
        raise ValueError("a 6-digit verification_code is required (or configure sms_tasker)")
    return code


def cookiejar_to_json(cookies: RequestsCookieJar) -> list[dict[str, Any]]:
    return [
        {
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain,
            "path": cookie.path,
            "secure": cookie.secure,
            "expires": cookie.expires,
        }
        for cookie in cookies
    ]


def json_to_cookiejar(value: Any) -> RequestsCookieJar:
    if not isinstance(value, list):
        raise ValueError("activation_cookies must be the cookie list returned by send-code")
    jar = RequestsCookieJar()
    for item in value:
        if not isinstance(item, dict):
            continue
        name, cookie_value = str(item.get("name") or ""), str(item.get("value") or "")
        if not name:
            continue
        jar.set_cookie(
            create_cookie(
                name=name,
                value=cookie_value,
                domain=str(item.get("domain") or ""),
                path=str(item.get("path") or "/"),
                secure=bool(item.get("secure")),
                expires=item.get("expires"),
            )
        )
    if not jar:
        raise ValueError("activation_cookies is empty")
    return jar


def merge_cookie_jars(*jars: RequestsCookieJar) -> RequestsCookieJar:
    merged = RequestsCookieJar()
    for jar in jars:
        if jar:
            merged.update(jar)
    return merged


def response_chain_cookies(response: requests.Response) -> RequestsCookieJar:
    """Collect Set-Cookie values from every redirect hop, including 302s."""
    return merge_cookie_jars(*(item.cookies for item in [*response.history, response]))


def new_session(initial_cookies: Any = None) -> requests.Session:
    session = requests.Session()
    session.headers.update({"user-agent": USER_AGENT})
    if initial_cookies is not None:
        session.cookies.update(json_to_cookiejar(initial_cookies))
    return session


def parse_json(response: requests.Response, step: str) -> dict[str, Any]:
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        exc.baiying_step = step
        raise
    try:
        data = response.json()
    except ValueError as exc:
        raise diagnostics.invalid_response(step, response) from exc
    if not isinstance(data, dict):
        raise diagnostics.invalid_response(step, response)
    return data


def require_sso_success(body: Mapping[str, Any], step: str) -> None:
    error_code = body.get("error_code")
    if error_code not in {None, 0, "0"}:
        raise diagnostics.ProviderError(step, error_code, body.get('description') or body.get('message'))


def send_code(payload: Mapping[str, Any]) -> dict[str, Any]:
    mobile = mobile_from_payload(payload)
    session = new_session()
    response = session.post(
        f"{SSO_BASE_URL}/send_activation_code/v2/",
        params={"aid": "2191", "language": "zh", "account_sdk_source": "web"},
        data={
            "mix_mode": "1",
            "service": BUYIN_URL,
            "type": "3731",
            "mobile": code_trans(mobile),
            "captcha_key": "",
            "is6Digits": "1",
        },
        timeout=request_timeout(payload),
    )
    body = parse_json(response, "baiying.send_activation_code")
    require_sso_success(body, "baiying.send_activation_code")
    # The SSO quick_login request must reuse the cookies issued while sending
    # the SMS (not a fresh requests.Session), just like doudian's workflow.
    save_login_cookies(mobile, "__activation__", session.cookies)
    return {
        "mobile": mobile,
        "response": body,
        "activation_cookies": cookiejar_to_json(session.cookies),
        "activation_cookie_cache_saved": True,
    }


def check_login(session: requests.Session, timeout: float) -> dict[str, Any]:
    response = session.get(
        f"{SSO_BASE_URL}/aff/check_login/",
        params={
            "aid": "2191",
            "language": "zh",
            "account_sdk_source": "sso",
            "service": "",
            "subject_aid": "6077",
            "need_ticket": "false",
        },
        timeout=timeout,
    )
    return parse_json(response, "baiying.check_login")


def common_login_check(session: requests.Session, *, check_body: Mapping[str, Any], timeout: float) -> dict[str, Any]:
    """Materialize the SSO identity as a Buyin business-domain session.

    This mirrors doudian_order_download's post-ticket login phase.  The ticket
    callback alone is insufficient for Buyin's API endpoints.
    """
    sec_user_id = str(check_body.get("sec_user_id") or check_body.get("user_id") or "").strip()
    if not sec_user_id:
        raise diagnostics.invalid_response("baiying.check_login")
    response = session.get(
        f"{BUYIN_URL}/ecomauth/loginv1/common_login_check",
        params={
            "check_type": "3", "bus_type": "24", "login_source": "buyin",
            "sec_user_id": sec_user_id, "sec_subject_uid": "", "entry_source": "0", "ecom_login_extra": "",
        },
        headers={"referer": f"{BUYIN_URL}/mpa/account/login"},
        timeout=timeout,
    )
    body = parse_json(response, "baiying.common_login_check")
    if body.get("code") not in {None, 0, "0"}:
        raise diagnostics.ProviderError("baiying.common_login_check", body.get("code"), body.get("msg") or body.get("message"), response=response)
    return body


def get_login_subjects(session: requests.Session, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return the Buyin child accounts made available by the logged-in phone."""
    params: dict[str, Any] = {
        "bus_type": "24",
        "login_source": "buyin",
        "entry_source": "0",
        "bus_child_type": "0",
    }
    # These are browser anti-bot values, not identity credentials.  They are
    # optional because a normal authenticated server session may not need them.
    for key in ("_lid", "msToken", "a_bogus"):
        if payload.get(key):
            params[key] = str(payload[key])
    response = session.get(
        f"{BUYIN_URL}/ecomauth/loginv1/get_login_subject",
        params=params,
        headers={"referer": f"{BUYIN_URL}/mpa/account/login"},
        timeout=request_timeout(payload),
    )
    body = parse_json(response, "baiying.get_login_subject")
    if body.get("code") not in {None, 0, "0"}:
        raise diagnostics.ProviderError("baiying.get_login_subject", body.get("code"), body.get("msg") or body.get("message"), response=response)
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    raw_subjects = data.get("login_subject_list") if isinstance(data.get("login_subject_list"), list) else []
    return [item for item in raw_subjects if isinstance(item, dict) and item.get("can_login")]


def login(payload: Mapping[str, Any]) -> dict[str, Any]:
    mobile = mobile_from_payload(payload)
    verification_code = str(payload.get("verification_code") or "").strip()
    if not verification_code.isascii() or not verification_code.isdigit() or len(verification_code) != 6:
        raise ValueError("verification_code must be a 6-digit SMS code")

    activation_cookies = payload.get("activation_cookies") or load_login_cookies(mobile, "__activation__")
    if not activation_cookies:
        raise ValueError("activation cookies are missing; run send-code before login")
    session = new_session(activation_cookies)
    timeout = request_timeout(payload)
    response = session.post(
        f"{SSO_BASE_URL}/quick_login/v2/",
        params={"aid": "2191", "language": "zh", "account_sdk_source": "web"},
        data={
            "mix_mode": "1",
            "service": f"{BUYIN_URL}/mpa/account/login?log_out=1&type=24",
            "mobile": code_trans(mobile),
            "code": code_trans(verification_code),
            "captcha_key": "",
            "biz_extra": json.dumps(
                {
                    "referer": f"{BUYIN_URL}/mpa/account/login",
                    "path": "/mpa/account/login",
                    "domain": "buyin.jinritemai.com",
                },
                separators=(",", ":"),
            ),
            "seraph_did": str(payload.get("seraph_did") or ""),
            "pc_did": str(payload.get("pc_did") or ""),
        },
        timeout=timeout,
        allow_redirects=False,
    )
    login_body = parse_json(response, "baiying.quick_login")
    require_sso_success(login_body, "baiying.quick_login")
    redirect_url = str(login_body.get("redirect_url") or "").strip()
    if not redirect_url:
        raise diagnostics.invalid_response("baiying.quick_login")

    login_ticket = str((parse_qs(urlsplit(redirect_url).query).get("ticket") or [""])[0]).strip()
    if not login_ticket:
        raise diagnostics.invalid_response("baiying.quick_login")
    # Complete the ticket exchange on the Buyin business domain.  Following
    # the SSO redirect directly does not establish the Buyin-domain session.
    callback = session.get(
        f"{BUYIN_URL}/passport/sso/login/callback/",
        params={
            "next": f"{BUYIN_URL}/mpa/account/login?log_out=1&type=24",
            "ticket": login_ticket,
            "ecom_login_uid": "1",
        },
        headers={"referer": f"{BUYIN_URL}/mpa/account/login"},
        timeout=timeout,
        allow_redirects=True,
    )
    callback.raise_for_status()
    login_status = check_login(session, timeout)
    common_login_status = common_login_check(session, check_body=login_status, timeout=timeout)
    data = login_status.get("data") if isinstance(login_status.get("data"), dict) else login_status
    account_key = str(payload.get("account_key") or "__platform__")
    save_login_cookies(mobile, account_key, session.cookies)
    try:
        subjects = get_login_subjects(session, payload)
    except Exception as exc:
        subjects = []
        subject_error = str(exc)
    else:
        subject_error = None
    return {
        "mobile": mobile,
        "login_response": login_body,
        "callback_url": callback.url,
        "login_status": login_status,
        "common_login_status": common_login_status,
        "authenticated": bool(data.get("sec_user_id") or data.get("user_id")),
        "baiying_cookies": cookiejar_to_json(session.cookies),
        "cookie_cache_saved": True,
        "account_key": account_key,
        "login_subjects": subjects,
        "login_subject_error": subject_error,
    }


def validate_login(payload: Mapping[str, Any]) -> dict[str, Any]:
    session = new_session(payload.get("baiying_cookies"))
    body = check_login(session, request_timeout(payload))
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    return {
        "authenticated": bool(data.get("sec_user_id") or data.get("user_id")),
        "login_status": body,
        "baiying_cookies": cookiejar_to_json(session.cookies),
    }


class ReplayServiceError(diagnostics.ProviderError):
    """A provider failure is neither an empty catalog nor a login verdict."""

    def __init__(self, code, message, *, response=None):
        super().__init__("baiying.smart_video_list", code, message, response=response)
        # Catalog retries are short and bounded; rate limits require a later
        # caller retry instead of consuming the same tight retry loop.
        self.retryable = self.category == "provider_unavailable"


def fetch_replay_list(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Fetch the 百应直播回放 list with a previously authenticated session."""
    mobile = mobile_from_payload(payload)
    account_key = str(payload.get("account_key") or "__platform__")
    cached = load_login_cookies(mobile, account_key)
    if not cached:
        raise ValueError("no login-derived cached Baiying cookies; run login for this mobile/account first")
    session = new_session(cached)
    response = session.get(
        f"{BUYIN_URL}/api/anchor/explain_replay/smart_video_list",
        params={
            "scene": str(payload.get("scene") or 1),
            "page_size": str(payload.get("page_size") or 20),
            "page_no": str(payload.get("page_no") or 1),
            "use_new_list": str(payload.get("use_new_list") or "true").lower(),
        },
        timeout=request_timeout(payload),
    )
    body = parse_json(response, "baiying.smart_video_list")
    if body.get("code") not in {None, 0, "0"}:
        raise ReplayServiceError(body.get("code"), body.get("msg") or body.get("message"), response=response)
    data = body.get("data")
    empty_list = isinstance(data, dict) and data.get("total") == 0 and data.get("smart_video_models") is None
    if not isinstance(data, dict) or (not isinstance(data.get("smart_video_models"), list) and not empty_list):
        raise diagnostics.invalid_response("baiying.smart_video_list", response)
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    videos = data.get("smart_video_models") if isinstance(data.get("smart_video_models"), list) else []
    return {
        "http_status": response.status_code,
        "total": data.get("total"),
        "room_id": data.get("room_id"),
        "video_count": len(videos),
        "videos": videos,
        "response": body,
        "baiying_cookies": cookiejar_to_json(session.cookies),
    }


def list_login_subjects(payload: Mapping[str, Any]) -> dict[str, Any]:
    mobile = mobile_from_payload(payload)
    cached = load_login_cookies(mobile)
    if not cached:
        raise ValueError("no login-derived cached Baiying cookies; run login first")
    subjects = get_login_subjects(new_session(cached), payload)
    return {
        "mobile": mobile,
        "subject_count": len(subjects),
        "subjects": [
            {
                key: item.get(key)
                for key in ("account_id", "account_name", "member_id", "subject_id", "identity_type_desc", "can_login")
            }
            for item in subjects
        ],
    }


def switch_subaccount(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Create and cache a login-derived Cookie jar for one Buyin child account."""
    mobile = mobile_from_payload(payload)
    login_subject_uid = str(payload.get("login_subject_uid") or "").strip()
    user_identity_id = str(payload.get("user_identity_id") or "").strip()
    account_key = str(payload.get("account_id") or user_identity_id).strip()
    if not login_subject_uid or not user_identity_id:
        raise ValueError("login_subject_uid and user_identity_id are required for a Buyin child-account switch")
    platform_cookies = load_login_cookies(mobile)
    if not platform_cookies:
        raise ValueError("no platform Cookie from SMS login; run login for this mobile first")
    session = new_session(platform_cookies)
    params: dict[str, Any] = {
        "subject_aid": "6077", "aid": "2191", "language": "zh", "account_sdk_source": "web",
    }
    for key in ("fp", "account_sdk_source_info", "msToken", "a_bogus"):
        if payload.get(key):
            params[key] = str(payload[key])
    response = session.post(
        f"{SSO_BASE_URL}/aff/subject/login/",
        params=params,
        data={
            "service": BUYIN_URL,
            "fp": str(payload.get("fp") or ""),
            "subject_aid": "6077",
            "mix_mode": "1",
            "login_subject_uid": code_trans(login_subject_uid),
            "user_identity_id": code_trans(user_identity_id),
        },
        timeout=request_timeout(payload),
        allow_redirects=False,
    )
    body = parse_json(response, "baiying.subject_login")
    require_sso_success(body, "baiying.subject_login")
    redirect_url = str(body.get("redirect_url") or "").strip()
    if not redirect_url:
        raise diagnostics.invalid_response("baiying.subject_login", response)
    callback_query = parse_qs(urlsplit(redirect_url).query)
    ticket = str((callback_query.get("ticket") or [""])[0]).strip()
    next_url = str((callback_query.get("next") or [BUYIN_URL])[0]).strip()
    if not ticket:
        raise diagnostics.invalid_response("baiying.subject_login", response)
    # subject/login returns next + ticket.  Browser's actual business callback
    # additionally requires these three fixed Buyin member-login parameters to
    # issue SASID / BUYIN_SASID and ecom_us_lt cookies.
    callback = session.get(
        f"{BUYIN_URL}/passport/sso/aff/login/callback/",
        params={
            "next": next_url,
            "ticket": ticket,
            "aid": "2191",
            "subject_aid": "6077",
            "login_member": "1",
        },
        headers={"referer": BUYIN_URL},
        timeout=request_timeout(payload),
        allow_redirects=False,
    )
    callback.raise_for_status()
    # After SSO has issued ucas_c0_buyin, establish the Buyin app context.
    # This response is what writes SASID/BUYIN_SASID and ecom_us_lt_*.
    app_callback = session.get(
        f"{BUYIN_URL}/ecomauth/loginv1/callback",
        params={
            "login_source": "buyin",
            "subject_aid": "6077",
            "bus_child_type": "1128",
            "entry_source": "1",
            "ecom_login_extra": "",
            "encode_member_id": "",
            "action_type": "1",
        },
        headers={"referer": BUYIN_URL},
        timeout=request_timeout(payload),
        allow_redirects=False,
    )
    app_callback.raise_for_status()
    child_cookies = merge_cookie_jars(
        session.cookies,
        response.cookies,
        callback.cookies,
        app_callback.cookies,
    )
    save_login_cookies(mobile, account_key, child_cookies)
    return {
        "mobile": mobile,
        "account_key": account_key,
        "user_identity_id": user_identity_id,
        "callback_url": callback.url,
        "app_callback_url": app_callback.url,
        "cookie_cache_saved": True,
    }


def has_required_recommend_reasons(value: Any) -> bool:
    """Require all four requested reason/tips pairs; do not accept partial tags."""
    if not isinstance(value, list):
        return False
    matched = {
        str(item.get("reason") or "").strip()
        for item in value
        if isinstance(item, dict) and str(item.get("tips") or "") == ""
    }
    return REQUIRED_RECOMMEND_REASONS.issubset(matched)


def fetch_all_replay_models(payload: Mapping[str, Any]) -> tuple[list[dict[str, Any]], int | None]:
    # The Buyin endpoint returns an empty data envelope above its supported
    # page size.  Keep the documented/observed maximum of 20 and paginate.
    page_size = max(min(int(payload.get("page_size") or 20), 20), 1)
    max_pages = max(int(payload.get("max_pages") or 100), 1)
    all_models: list[dict[str, Any]] = []
    total: int | None = None
    for page_no in range(1, max_pages + 1):
        page = fetch_replay_list({**payload, "page_no": page_no, "page_size": page_size})
        response = page["response"]
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        models = data.get("smart_video_models") if isinstance(data.get("smart_video_models"), list) else []
        all_models.extend(item for item in models if isinstance(item, dict))
        total_value = data.get("total")
        if isinstance(total_value, int):
            total = total_value
        if not models or (total is not None and len(all_models) >= total):
            break
    return all_models, total


def model_fragment_ids(model: Mapping[str, Any]) -> list[str]:
    result: list[str] = []
    for key in ("fragment_id", "related_fragment_id"):
        value = str(model.get(key) or "").strip()
        if value and value not in result:
            result.append(value)
    for video in model.get("auto_cut_videos") if isinstance(model.get("auto_cut_videos"), list) else []:
        if not isinstance(video, dict):
            continue
        for key in ("fragment_id", "related_fragment_id"):
            value = str(video.get(key) or "").strip()
            if value and value not in result:
                result.append(value)
        info = video.get("related_fragment_info")
        if isinstance(info, dict):
            value = str(info.get("fragment_id") or "").strip()
            if value and value not in result:
                result.append(value)
    for info in model.get("fragment_infos") if isinstance(model.get("fragment_infos"), list) else []:
        if isinstance(info, dict):
            value = str(info.get("fragment_id") or "").strip()
            if value and value not in result:
                result.append(value)
    return result


def build_replay_plan(payload: Mapping[str, Any]) -> dict[str, Any]:
    required_count = max(int(payload.get("required_video_count") or 5), 1)
    models, total = fetch_all_replay_models(payload)
    products: dict[str, dict[str, Any]] = {}
    for model in models:
        promotion = model.get("promotion_info") if isinstance(model.get("promotion_info"), dict) else {}
        product_id = str(promotion.get("product_id") or "").strip()
        if not product_id:
            continue
        bucket = products.setdefault(product_id, {"product_id": product_id, "product_title": promotion.get("title"), "videos": [], "fragment_ids": []})
        for fragment_id in model_fragment_ids(model):
            if fragment_id not in bucket["fragment_ids"]:
                bucket["fragment_ids"].append(fragment_id)
        for video in model.get("auto_cut_videos") if isinstance(model.get("auto_cut_videos"), list) else []:
            if not isinstance(video, dict) or not str(video.get("play_addr") or "").strip():
                continue
            if not has_required_recommend_reasons(video.get("recommend_reasons")):
                continue
            identity = str(video.get("creative_work_id") or video.get("vid") or video.get("play_addr"))
            if identity not in {item["id"] for item in bucket["videos"]}:
                bucket["videos"].append({"id": identity, "play_addr": video["play_addr"], "cover": video.get("cover"), "duration": video.get("duration"), "recommend_reasons": video.get("recommend_reasons")})
    planned = []
    for product in products.values():
        selected = product["videos"][:required_count]
        shortage = max(required_count - len(selected), 0)
        planned.append({**product, "videos": selected, "qualified_video_count": len(product["videos"]), "shortage": shortage, "trigger_fragment_ids": product["fragment_ids"][:shortage]})
    return {"required_video_count": required_count, "replay_total": total, "product_count": len(planned), "products": planned}


def collect_subaccount_replay_videos(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Collect a complete product/video plan for every login-derived child account.

    Child-account identifiers are returned by ``get_login_subject``: ``subject_id``
    is the subject-login UID and ``member_id`` is the user identity.  The function
    deliberately starts each switch from the cached platform session so the child
    cookies remain isolated under their account IDs.
    """
    mobile = mobile_from_payload(payload)
    subjects = get_login_subjects(new_session(load_login_cookies(mobile) or []), payload)
    accounts: list[dict[str, Any]] = []
    for subject in subjects:
        account_id = str(subject.get("account_id") or "").strip()
        subject_id = str(subject.get("subject_id") or "").strip()
        member_id = str(subject.get("member_id") or subject.get("bus_member_id") or "").strip()
        base = {
            "account_id": account_id,
            "account_name": subject.get("account_name"),
            "subject_id": subject_id,
            "member_id": member_id,
        }
        if not account_id or not subject_id or not member_id:
            accounts.append({**base, "status": "skipped", "error": "child account lacks subject_id or member_id"})
            continue
        try:
            switched = switch_subaccount(
                {**payload, "account_id": account_id, "login_subject_uid": subject_id, "user_identity_id": member_id}
            )
            plan = build_replay_plan({**payload, "account_key": account_id})
            accounts.append({**base, "status": "success", "session_cached": bool(switched.get("cookie_cache_saved")), **plan})
        except Exception as exc:
            accounts.append({**base, "status": "failed", "error": str(exc)})
    successful = [item for item in accounts if item["status"] == "success"]
    return {
        "mobile": mobile,
        "subject_count": len(subjects),
        "successful_account_count": len(successful),
        "failed_account_count": len(accounts) - len(successful),
        "required_video_count": max(int(payload.get("required_video_count") or 5), 1),
        "accounts": accounts,
    }


def trigger_auto_clip(payload: Mapping[str, Any]) -> dict[str, Any]:
    mobile = mobile_from_payload(payload)
    account_key = str(payload.get("account_key") or "__platform__")
    cached = load_login_cookies(mobile, account_key)
    if not cached:
        raise ValueError("no login-derived cached Baiying cookies; run login for this mobile/account first")
    session = new_session(cached)
    fragment_ids = payload.get("fragment_ids") or ([payload.get("fragment_id")] if payload.get("fragment_id") else [])
    if not isinstance(fragment_ids, list) or not fragment_ids:
        raise ValueError("fragment_id or fragment_ids is required")
    csrf = str(payload.get("x_secsdk_csrf_token") or "").strip()
    if not csrf:
        csrf = fetch_clip_csrf(session, request_timeout(payload))
    results = []
    for fragment_id in dict.fromkeys(str(value).strip() for value in fragment_ids if str(value).strip()):
        response = session.post(
            f"{BUYIN_URL}/api/anchor/explain_replay/auto_clip",
            json={"fragment_id": fragment_id, "operate_source": 1},
            headers={"x-secsdk-csrf-token": csrf, "x-secsdk-csrf-version": "1.2.22",
                     "origin": BUYIN_URL, "referer": f"{BUYIN_URL}/dashboard/live/slice"},
            timeout=request_timeout(payload), allow_redirects=False,
        )
        body = parse_json(response, "baiying.auto_clip")
        # HTTP 200 alone does not mean the provider accepted a clipping task.
        accepted = body.get("code") in {0, "0"} and body.get("success") is not False
        results.append({"fragment_id": fragment_id, "http_status": response.status_code, "accepted": accepted, "response": body})
    save_login_cookies(mobile, account_key, session.cookies)
    return {"triggered_count": sum(item["accepted"] for item in results), "results": results, "cookie_cache_saved": True, "account_key": account_key}


def fetch_clip_csrf(session: requests.Session, timeout: float) -> str:
    """Follow the SecSDK 1.2.22 handshake used by Buyin's replay page.

    Its response is status,token,maxAge,message,sessionId (not just a token).
    The page's own SDK sends DOWNGRADE when this host has no registered CSRF
    configuration. Other token failures are surfaced instead of retried.
    """
    response = session.head(
        f"{BUYIN_URL}/api/anchor/explain_replay/auto_clip",
        headers={"x-secsdk-csrf-request": "1", "x-secsdk-csrf-version": "1.2.22",
                 "origin": BUYIN_URL, "referer": f"{BUYIN_URL}/dashboard/live/slice"},
        timeout=timeout,
    )
    response.raise_for_status()
    parts = response.headers.get("x-ware-csrf-token", "").split(",")
    if len(parts) > 1 and parts[0] == "0" and parts[1]:
        return parts[1]
    if len(parts) > 3 and parts[0] == "403" and parts[3] == "api domain not register or cors rejected":
        return "DOWNGRADE"
    raise RuntimeError("百应剪辑会话校验失败，请重新登录后再试。")


def prepare_login(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate workflow-provided cached cookies and request SMS only if needed."""
    mobile = mobile_from_payload(payload)
    account_key = str(payload.get("account_key") or "__platform__")
    cookies = load_login_cookies(mobile, account_key)
    if payload_bool(payload, "force_refresh_login", default=False):
        return {"mobile": mobile, "needs_sms_code": True, "cookies_ready": False, "cache_status": "force_refresh"}
    if not cookies:
        return {"mobile": mobile, "needs_sms_code": True, "cookies_ready": False, "cache_status": "missing"}
    try:
        validation = validate_login({**payload, "baiying_cookies": cookies})
        if validation["authenticated"]:
            return {
                "mobile": mobile,
                "needs_sms_code": False,
                "cookies_ready": True,
                "cache_status": "hit",
                "baiying_cookies": validation["baiying_cookies"],
                "account_key": account_key,
            }
        return {"mobile": mobile, "needs_sms_code": True, "cookies_ready": False, "cache_status": "invalid"}
    except Exception as exc:
        return {
            "mobile": mobile,
            "needs_sms_code": True,
            "cookies_ready": False,
            "cache_status": "invalid_validation_failed",
            "validation_error": str(exc),
        }


def run_login(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Cookie reuse → SMS send → sms-tasker/manual code → callback login fallback."""
    prepared = prepare_login(payload)
    if prepared["cookies_ready"]:
        return prepared
    activation = send_code(payload)
    verification_code = resolve_verification_code(payload)
    result = login({**payload, "activation_cookies": activation["activation_cookies"], "verification_code": verification_code})
    return {
        "mobile": result["mobile"],
        "needs_sms_code": False,
        "cookies_ready": result["authenticated"],
        "cache_status": "refreshed",
        "baiying_cookies": result["baiying_cookies"],
        "login_status": result["login_status"],
        "callback_url": result["callback_url"],
    }
