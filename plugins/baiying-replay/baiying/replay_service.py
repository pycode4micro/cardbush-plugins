"""Account/product-scoped replay operations shared by the CLI and Codex plugin."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urljoin, urlsplit

import requests

if __package__:
    from . import client, diagnostics
else:
    import client
    import diagnostics


PLAN_TTL = 1800
MAX_VIDEO_BYTES = 1024 * 1024 * 1024
MEDIA_DOMAINS = ("zjcdn.com", "douyinvod.com", "ecombdvod.com", "bytecdn.cn")


def _integer(payload, key, default, minimum, maximum):
    value = payload.get(key, default)
    if isinstance(value, bool):
        raise ValueError(f"{key} 必须为整数。")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} 必须为整数。") from None
    if str(parsed) != str(value) or not minimum <= parsed <= maximum:
        raise ValueError(f"{key} 必须在 {minimum} 到 {maximum} 之间。")
    return parsed


def _identifier(payload, key):
    value = str(payload.get(key) or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        raise ValueError(f"请提供从查询结果中选择的 {key}。")
    return value


def list_accounts(payload: Mapping[str, Any]) -> dict:
    """Return selectable identities without exposing cookies or SSO tickets."""
    mobile = client.mobile_from_payload(payload)
    cookies = client.load_login_cookies(mobile)
    if not cookies:
        raise ValueError("请先用手机号完成百应登录。")
    subjects = client.get_login_subjects(client.new_session(cookies), payload)
    accounts = []
    for subject in subjects:
        accounts.append({
            "account_id": str(subject.get("account_id") or ""),
            "account_name": subject.get("account_name"),
            "subject_id": str(subject.get("subject_id") or ""),
            "member_id": str(subject.get("member_id") or subject.get("bus_member_id") or ""),
        })
    return {"account_count": len(accounts), "accounts": accounts}


def _account(payload):
    account_id = _identifier(payload, "account_id")
    matches = [a for a in list_accounts(payload)["accounts"] if a["account_id"] == account_id]
    if len(matches) != 1:
        raise ValueError("所选子账号不存在、重名身份不明确或不在该手机号可登录范围内。")
    account = matches[0]
    if not account["subject_id"] or not account["member_id"]:
        raise ValueError("该子账号缺少登录身份，请在百应中检查账号权限。")
    client.switch_subaccount({
        **payload, "account_id": account_id,
        "login_subject_uid": account["subject_id"], "user_identity_id": account["member_id"],
    })
    return {"account_id": account_id, "account_name": account["account_name"]}


def _catalog(payload):
    account = _account(payload)
    max_pages = _integer(payload, "max_pages", 100, 1, 1000)
    products = {}
    fetched, total, complete = 0, None, False
    seen_pages = set()
    for page_no in range(1, max_pages + 1):
        for attempt in range(3):
            try:
                page = client.fetch_replay_list({
                    **payload, "account_key": account["account_id"], "page_size": 20, "page_no": page_no,
                })
                break
            except client.ReplayServiceError as exc:
                if not exc.retryable or attempt == 2:
                    raise
                time.sleep(attempt + 1)
            except requests.exceptions.SSLError:
                raise
            except (requests.Timeout, requests.ConnectionError):
                if attempt == 2:
                    raise
                time.sleep(attempt + 1)
        models = page["videos"]
        fingerprint = hashlib.sha256(json.dumps(models, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if models and fingerprint in seen_pages:
            break
        seen_pages.add(fingerprint)
        if isinstance(page.get("total"), int):
            total = page["total"]
        fetched += len(models)
        for model in models:
            promotion = model.get("promotion_info") or {}
            product_id = str(promotion.get("product_id") or "")
            if not product_id:
                continue
            product = products.setdefault(product_id, {
                "product_id": product_id, "product_title": promotion.get("title"),
                "product_cover": promotion.get("cover"), "videos": {}, "fragments": {},
                "linked_fragments": set(),
            })
            for raw in model.get("fragment_infos") or []:
                _add_fragment(product, raw)
            # User-triggered auto_clip results arrive in author_cut_videos.
            # Both lists also contain pending entries without playable URLs.
            cuts = ((collection, raw)
                    for collection in ("auto_cut_videos", "author_cut_videos")
                    for raw in model.get(collection) or [])
            for collection, raw in cuts:
                related = raw.get("related_fragment_info") or {}
                fragment_id = str(raw.get("related_fragment_id") or related.get("fragment_id") or "")
                if fragment_id:
                    # Includes clips without a URL: they may still be processing.
                    product["linked_fragments"].add(fragment_id)
                    _add_fragment(product, {**related, "fragment_id": fragment_id})
                url = str(raw.get("play_addr") or "").strip()
                if not url:
                    continue
                video_id = str(raw.get("creative_work_id") or raw.get("vid") or
                               hashlib.sha256(urlsplit(url).path.encode()).hexdigest()[:24])
                product["videos"].setdefault(video_id, {
                    "video_id": video_id, "download_url": url, "cover_url": raw.get("cover"),
                    "duration_seconds": raw.get("duration"),
                    "generated_at": raw.get("generate_timestamp"),
                    "recommend_reasons": raw.get("recommend_reasons") or [],
                    "qualified": client.has_required_recommend_reasons(raw.get("recommend_reasons")),
                    "fragment_id": fragment_id, "clip_status": raw.get("clip_status"),
                    "provider_video_collection": collection,
                })
        if not models or (total is not None and fetched >= total):
            complete = total is None or fetched >= total
            break
    return account, products, {"pages_read": page_no, "replay_records_read": fetched,
                                "replay_total": total, "scan_complete": complete}


def _add_fragment(product, raw):
    fragment_id = str(raw.get("fragment_id") or "")
    if not fragment_id:
        return
    item = product["fragments"].setdefault(fragment_id, {"fragment_id": fragment_id})
    for key, value in {
        "download_url": raw.get("play_addr"), "cover_url": raw.get("cover"),
        "duration_seconds": raw.get("duration"), "generated_at": raw.get("generate_timestamp"),
    }.items():
        # A pending record may precede a later playable copy of the same
        # original. Empty values must not hide the subsequently returned URL.
        if value is not None and value != "" and not item.get(key):
            item[key] = value


def _summary(product):
    return {key: product[key] for key in ("product_id", "product_title", "product_cover")} | {
        "existing_video_count": len(product["videos"]),
        "qualified_video_count": sum(v["qualified"] for v in product["videos"].values()),
        "original_fragment_count": len(product["fragments"]),
        "original_downloadable_count": sum(bool(f.get("download_url")) for f in product["fragments"].values()),
        "unclipped_fragment_count": sum(f not in product["linked_fragments"] for f in product["fragments"]),
    }


def search_products(payload: Mapping[str, Any]) -> dict:
    limit = _integer(payload, "limit", 20, 1, 100)
    offset = _integer(payload, "offset", 0, 0, 100000)
    query = str(payload.get("query") or "").strip().casefold()
    account, products, scan = _catalog(payload)
    matches = [_summary(p) for p in products.values()
               if not query or query in str(p["product_title"]).casefold() or query in p["product_id"]]
    return {**account, **scan, "matched_product_count": len(matches),
            "products": matches[offset:offset + limit],
            "next_offset": offset + limit if offset + limit < len(matches) else None}


def _product(payload):
    product_id = _identifier(payload, "product_id")
    account, products, scan = _catalog(payload)
    if product_id not in products:
        if not scan["scan_complete"]:
            raise ValueError("尚未扫描完整个子账号，当前范围没有该商品；请增大 max_pages 后重试。")
        raise ValueError("该商品不在所选子账号的回放列表中，请先查询商品并核对 ID。")
    return account, products[product_id], scan


def get_product_videos(payload: Mapping[str, Any]) -> dict:
    limit = _integer(payload, "limit", 5, 1, 100)
    offset = _integer(payload, "offset", 0, 0, 100000)
    target = _integer(payload, "target_count", 5, 1, 100)
    fragment_limit = _integer(payload, "fragment_limit", 10, 1, 50)
    fragment_offset = _integer(payload, "fragment_offset", 0, 0, 100000)
    quality = payload.get("quality", "qualified")
    if quality not in {"qualified", "all"}:
        raise ValueError("quality 只能为 qualified 或 all。")
    account, product, scan = _product(payload)
    videos = [v for v in product["videos"].values() if quality == "all" or v["qualified"]]
    fragments = [{**f, "source": "original_fragment", "caption_status": "unchecked",
                  "has_clip_record": f["fragment_id"] in product["linked_fragments"],
                  "has_existing_clip": f["fragment_id"] in product["linked_fragments"]}
                 for f in product["fragments"].values()]
    summary = _summary(product)
    return {**account, **scan, **summary, "quality": quality, "target_count": target,
            "qualified_shortage": max(target - summary["qualified_video_count"], 0),
            "matching_video_count": len(videos), "videos": videos[offset:offset + limit],
            "next_offset": offset + limit if offset + limit < len(videos) else None,
            "original_fragments": fragments[fragment_offset:fragment_offset + fragment_limit],
            "next_fragment_offset": fragment_offset + fragment_limit if fragment_offset + fragment_limit < len(fragments) else None,
            "clipping_started": False,
            "next_actions": ["download_original_fragments", "prepare_original_materials", "download_selected_videos"],
            "links_refreshed_at": int(time.time())}


@contextmanager
def _state():
    configured = os.environ.get("BAIYING_REPLAY_STATE_PATH")
    path = Path(configured) if configured else client.CACHE_PATH.with_name("baiying_replay_state.sqlite3")
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    try:
        with connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS clip_plans (
                plan_id TEXT PRIMARY KEY, mobile_hash TEXT NOT NULL, created_at REAL NOT NULL,
                payload_json TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'prepared', result_json TEXT
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS clip_claims (
                mobile_hash TEXT NOT NULL, account_id TEXT NOT NULL, fragment_id TEXT NOT NULL,
                plan_id TEXT NOT NULL, state TEXT NOT NULL,
                PRIMARY KEY(mobile_hash, account_id, fragment_id))""")
            yield connection
    finally:
        connection.close()


def _ids(payload, key, *, required=True, maximum=20):
    values = payload.get(key)
    if values is None and not required:
        return None
    if not isinstance(values, list) or not values or len(values) > maximum:
        raise ValueError(f"{key} 需要包含 1 至 {maximum} 个已查询的 ID。")
    return list(dict.fromkeys(_identifier({key: value}, key) for value in values))


def prepare_clips(payload: Mapping[str, Any]) -> dict:
    """Build a short-lived product-bound plan; do not send any clipping request."""
    mobile = client.mobile_from_payload(payload)
    count = _integer(payload, "max_fragments", 1, 1, 5)
    explicit_ids = _ids(payload, "fragment_ids", required=False, maximum=5)
    account, product, scan = _product(payload)
    include_previous = client.payload_bool(payload, "include_previously_clipped")
    candidates = [fid for fid in product["fragments"]
                  if include_previous or fid not in product["linked_fragments"]]
    selected = explicit_ids if explicit_ids is not None else candidates[:count]
    if any(fid not in candidates for fid in selected):
        raise ValueError("片段不属于该商品或已有关联剪辑；如确需重剪，请明确选择 include_previously_clipped。")
    if not selected:
        return {**account, **_summary(product), **scan, "plan_id": None, "clipping_started": False,
                "message": "没有可用的未剪辑片段。可查看已有视频，或明确选择已有片段重新剪辑。"}
    plan_id = secrets.token_urlsafe(24)
    plan = {**account, "product_id": product["product_id"], "product_title": product["product_title"],
            "fragment_ids": selected, "baseline_video_ids": list(product["videos"]),
            "include_previously_clipped": include_previous}
    with _state() as connection:
        connection.execute("INSERT INTO clip_plans (plan_id, mobile_hash, created_at, payload_json) VALUES (?, ?, ?, ?)",
                           (plan_id, client.mobile_hash(mobile), time.time(), json.dumps(plan, ensure_ascii=False)))
    return {**account, **_summary(product), "plan_id": plan_id, "expires_in_seconds": PLAN_TTL,
            "clipping_started": False, "selected_fragment_count": len(selected),
            "selected_fragments": [product["fragments"][fid] for fid in selected],
            "message": "这是待执行计划。选择继续智能剪辑后再调用 start_clips；生成完成不保证全部符合推荐标签。"}


def _load_plan(payload):
    mobile = client.mobile_from_payload(payload)
    plan_id = _identifier(payload, "plan_id")
    with _state() as connection:
        row = connection.execute("SELECT created_at, payload_json, state, result_json FROM clip_plans WHERE plan_id=? AND mobile_hash=?",
                                 (plan_id, client.mobile_hash(mobile))).fetchone()
    if row is None:
        raise ValueError("剪辑计划不存在或不属于当前手机号。")
    return plan_id, row, json.loads(row[1])


def start_clips(payload: Mapping[str, Any]) -> dict:
    plan_id, row, plan = _load_plan(payload)
    if row[2] != "prepared":
        return json.loads(row[3]) if row[3] else {"plan_id": plan_id, "state": row[2], "resubmitted": False}
    if time.time() - row[0] > PLAN_TTL:
        raise ValueError("剪辑计划已过期，请重新查询商品并准备计划。")
    # Check membership again immediately before the external action. Use saved
    # scope, never caller-supplied account/product identifiers at submission time.
    account, product, _scan = _product({**payload, "account_id": plan["account_id"], "product_id": plan["product_id"]})
    if any(fid not in product["fragments"] for fid in plan["fragment_ids"]):
        raise ValueError("部分片段已不在该商品回放中，请重新准备剪辑计划。")
    if not plan["include_previously_clipped"] and any(fid in product["linked_fragments"] for fid in plan["fragment_ids"]):
        raise ValueError("所选片段已有剪辑记录，原计划已失效；请先刷新商品视频。")
    with _state() as connection:
        claimed = connection.execute("UPDATE clip_plans SET state='submitting' WHERE plan_id=? AND state='prepared'", (plan_id,)).rowcount
    if not claimed:
        return {"plan_id": plan_id, "state": "submitting", "resubmitted": False}
    results = []
    for fragment_id in plan["fragment_ids"]:
        owner = client.mobile_hash(client.mobile_from_payload(payload))
        # A lost response must not be bypassed with another local plan/job.
        with _state() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute("SELECT plan_id,state FROM clip_claims WHERE mobile_hash=? AND account_id=? AND fragment_id=?",
                                          (owner, plan["account_id"], fragment_id)).fetchone()
            if previous and previous[1] not in {"completed", "rejected"}:
                old = connection.execute("SELECT payload_json FROM clip_plans WHERE plan_id=?", (previous[0],)).fetchone()
                baseline = set(json.loads(old[0])["baseline_video_ids"]) if old else set(product["videos"])
                completed = any(v["fragment_id"] == fragment_id and v["video_id"] not in baseline for v in product["videos"].values())
                if not completed:
                    results.append({"fragment_id": fragment_id, "state": "unknown", "existing_plan_id": previous[0],
                                    "message": "该片段已有未确认完成的提交，请继续查询已有计划，不重复发送。"})
                    continue
            connection.execute("INSERT INTO clip_claims VALUES (?,?,?,?,?) ON CONFLICT(mobile_hash,account_id,fragment_id) "
                               "DO UPDATE SET plan_id=excluded.plan_id,state=excluded.state",
                               (owner, plan["account_id"], fragment_id, plan_id, "submitting"))
        try:
            response = client.trigger_auto_clip({
                "mobile": payload["mobile"], "account_key": plan["account_id"],
                "fragment_ids": [fragment_id], "request_timeout_seconds": client.request_timeout(payload),
            })
            item = response["results"][0]
            body = item.get("response") or {}
            results.append({"fragment_id": fragment_id,
                            "state": "submitted" if item.get("accepted") else "rejected",
                            "provider_code": body.get("code"),
                            "message": body.get("msg") or body.get("message")})
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            # A lost POST response cannot be treated as a safe-to-retry failure.
            results.append({"fragment_id": fragment_id, "state": "unknown",
                            "message": "未能确认百应是否接收，请先查询剪辑结果；本计划不会自动重发。",
                            "error_detail": diagnostics.record_error(exc, operation="baiying_start_clips", next_action="check_clips")})
        with _state() as connection:
            connection.execute("UPDATE clip_claims SET state=? WHERE mobile_hash=? AND account_id=? AND fragment_id=? AND plan_id=?",
                               (results[-1]["state"], owner, plan["account_id"], fragment_id, plan_id))
        interim = {**account, "product_id": plan["product_id"], "plan_id": plan_id,
                   "state": "submitting", "results": results, "resubmitted": False}
        with _state() as connection:
            connection.execute("UPDATE clip_plans SET result_json=? WHERE plan_id=?", (json.dumps(interim, ensure_ascii=False), plan_id))
    result = {**account, "product_id": plan["product_id"], "plan_id": plan_id, "results": results,
              "resubmitted": False, "state": "submission_finished",
              "submitted_count": sum(r["state"] == "submitted" for r in results),
              "rejected_count": sum(r["state"] == "rejected" for r in results),
              "unknown_count": sum(r["state"] == "unknown" for r in results),
              "generation_complete": False, "next_action": "check_clips"}
    with _state() as connection:
        connection.execute("UPDATE clip_plans SET state='submission_finished', result_json=? WHERE plan_id=?",
                           (json.dumps(result, ensure_ascii=False), plan_id))
    return result


def check_clips(payload: Mapping[str, Any]) -> dict:
    plan_id, row, plan = _load_plan(payload)
    account, product, scan = _product({**payload, "account_id": plan["account_id"], "product_id": plan["product_id"]})
    # Only report new videos associated with the chosen fragments, not clips
    # another person generated for other fragments while this plan was running.
    fresh = [v for v in product["videos"].values() if v["video_id"] not in plan["baseline_video_ids"]
             and v["fragment_id"] in plan["fragment_ids"]]
    with _state() as connection:
        for fragment_id in {v["fragment_id"] for v in fresh}:
            connection.execute("UPDATE clip_claims SET state='completed' WHERE plan_id=? AND fragment_id=?", (plan_id, fragment_id))
    return {**account, **scan, **_summary(product), "plan_id": plan_id, "submission_state": row[2],
            "submission_result": json.loads(row[3]) if row[3] else None,
            "state": "new_videos_available" if fresh else "no_new_videos_yet",
            "new_video_count": len(fresh), "new_videos": fresh,
            "message": "以实际出现的视频为准；没有新视频时不会重复提交剪辑。"}


def _media_url(url):
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443)
            or not any(host == domain or host.endswith("." + domain) for domain in MEDIA_DOMAINS)):
        raise ValueError("视频地址不是支持的百应视频 CDN 地址，请重新获取链接。")
    return url


def _download(url, target, timeout, *, max_bytes=MAX_VIDEO_BYTES, checkpoint=None):
    """Download without forwarding login cookies; validate redirects and MP4 data."""
    part = target.with_name(target.name + "." + secrets.token_hex(6) + ".part")
    size = 0
    try:
        with requests.Session() as session:
            session.headers.update({"User-Agent": client.USER_AGENT, "Referer": client.BUYIN_URL + "/"})
            current = _media_url(url)
            for _ in range(6):
                response = session.get(current, stream=True, allow_redirects=False, timeout=timeout)
                if response.status_code in {301, 302, 303, 307, 308}:
                    current = _media_url(urljoin(current, response.headers.get("location", "")))
                    response.close()
                    continue
                break
            else:
                raise ValueError("视频下载跳转次数过多。")
            with response:
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "").split(";")[0].lower()
                if content_type and content_type not in {"video/mp4", "video/octet-stream", "application/octet-stream"}:
                    raise ValueError("视频地址返回了非视频内容，请刷新链接。")
                expected = int(response.headers.get("Content-Length") or 0)
                if expected > max_bytes:
                    raise ValueError("单个视频超过本次配置的下载大小上限。")
                with part.open("xb") as output:
                    for chunk in response.iter_content(chunk_size=256 * 1024):
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > max_bytes:
                            raise ValueError("单个视频超过本次配置的下载大小上限。")
                        output.write(chunk)
                        if checkpoint:
                            checkpoint(size, expected)
                if not size or (expected and size != expected):
                    raise ValueError("视频下载不完整，请刷新链接后重试。")
                with part.open("rb") as probe:
                    if b"ftyp" not in probe.read(64):
                        raise ValueError("返回内容不是可识别的 MP4 文件。")
            # Exclusive creation also works on NAS shares without hardlinks.
            try:
                with target.open("xb") as output:
                    try:
                        with part.open("rb") as source:
                            shutil.copyfileobj(source, output)
                    except BaseException:
                        output.close()
                        target.unlink(missing_ok=True)
                        raise
            except FileExistsError:
                return {"status": "already_exists", "path": str(target), "bytes": target.stat().st_size}
        return {"status": "downloaded", "path": str(target), "bytes": size}
    finally:
        part.unlink(missing_ok=True)


def download_videos(payload: Mapping[str, Any]) -> dict:
    video_ids = _ids(payload, "video_ids")
    source = payload.get("source", "smart_clip")
    if source not in {"smart_clip", "original_fragment"}:
        raise ValueError("source 只能为 smart_clip 或 original_fragment。")
    account, product, scan = _product(payload)
    available = product["videos"] if source == "smart_clip" else product["fragments"]
    if any(vid not in available or not available[vid].get("download_url") for vid in video_ids):
        raise ValueError("所选视频不属于该账号和商品，或已经没有可用地址；请刷新查询结果。")
    configured = payload.get("output_dir")
    folder = (Path(str(configured)).expanduser() if configured else
              Path.home() / "Downloads" / "baiying" / account["account_id"] / product["product_id"])
    if not folder.is_absolute():
        raise ValueError("output_dir 必须是本地或 NAS 的绝对路径。")
    folder = folder.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    results = []
    for video_id in video_ids:
        target = folder / f"{account['account_id']}_{product['product_id']}_{source}_{video_id}.mp4"
        try:
            if target.exists():
                result = {"status": "already_exists", "path": str(target), "bytes": target.stat().st_size}
            else:
                result = _download(available[video_id]["download_url"], target, client.request_timeout(payload))
        except (requests.RequestException, ValueError, OSError) as exc:
            detail = diagnostics.record_error(exc, operation="baiying_download_videos", next_action="refresh_links_then_retry_failed_downloads")
            result = {"status": "failed", "error": detail["message"], "error_detail": detail}
        results.append({"video_id": video_id, **result})
    result = {**account, **scan, "product_id": product["product_id"], "product_title": product["product_title"],
              "source": source, "output_dir": str(folder), "files": results,
              "downloaded_count": sum(r["status"] == "downloaded" for r in results),
              "existing_count": sum(r["status"] == "already_exists" for r in results),
              "failed_count": sum(r["status"] == "failed" for r in results)}
    manifest = folder / f"download_{time.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}.json"
    manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**result, "manifest_path": str(manifest)}
