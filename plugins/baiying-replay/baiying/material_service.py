"""Durable, bounded material preparation jobs, independent of platform/editor."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import requests

if __package__:
    from . import client, diagnostics, media_validation as media, replay_service as replay
else:
    import client
    import diagnostics
    import media_validation as media
    import replay_service as replay


SCHEMA_VERSION = "baiying-materials/v1"
SOURCE_POLICIES = {"original_only", "original_first", "smart_clip_first"}


def _source_policy(options):
    # Jobs created before this field existed keep their saved source order.
    return options.get("source_policy", "smart_clip_first")


def _saved_config(options):
    return _config({"source_policy": "smart_clip_first", **options})


@contextmanager
def _db():
    with replay._state() as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS material_jobs (
            job_id TEXT PRIMARY KEY, mobile_hash TEXT NOT NULL, request_key TEXT,
            data_json TEXT NOT NULL, run_token TEXT, worker_pid INTEGER,
            started_at REAL, cancel_requested INTEGER NOT NULL DEFAULT 0,
            UNIQUE(mobile_hash, request_key))""")
        yield connection


def _load(payload):
    job_id = replay._identifier(payload, "job_id")
    owner = client.mobile_hash(client.mobile_from_payload(payload))
    with _db() as connection:
        row = connection.execute("SELECT data_json, run_token, worker_pid, started_at, cancel_requested "
                                 "FROM material_jobs WHERE job_id=? AND mobile_hash=?", (job_id, owner)).fetchone()
    if row is None:
        raise ValueError("素材任务不存在或不属于当前登录手机号。")
    data = json.loads(row[0])
    data["options"] = _saved_config(data["options"])
    return data, row


def _alive(pid):
    if not pid:
        return False
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _running(row):
    return bool(row[1]) and (_alive(row[2]) or (not row[2] and time.time() - (row[3] or 0) < 30))


class Cancelled(Exception):
    pass


def _save(data, token):
    data["updated_at"] = int(time.time())
    with _db() as connection:
        row = connection.execute("SELECT run_token, cancel_requested FROM material_jobs WHERE job_id=?",
                                 (data["job_id"],)).fetchone()
        if row is None or row[0] != token:
            raise Cancelled()
        if row[1] and data["status"] != "cancelled":
            raise Cancelled()
        connection.execute("UPDATE material_jobs SET data_json=? WHERE job_id=? AND run_token=?",
                           (json.dumps(data, ensure_ascii=False), data["job_id"], token))


def _config(payload):
    raw_only = payload.get("source_policy", "original_only") == "original_only"
    options = {key: replay._integer(payload, key, default, low, high) for key, default, low, high in (
        ("target_count", 1 if raw_only else 5, 0, 100), ("target_seconds", 0, 0, 36000),
        ("min_source_count", 0, 0, 100), ("min_duration_seconds", 2, 1, 3600),
        ("max_downloads", 30, 1, 100), ("download_attempts", 2, 1, 3),
        ("max_file_mb", 2048, 1, 8192), ("max_pages", 100, 1, 1000),
        ("max_total_mb", 4096, 1, 102400),
        ("max_clip_rounds", 2, 1, 5), ("fragments_per_round", 1, 1, 5),
        ("clip_wait_seconds", 300, 30, 1800),
    )}
    if not options["target_count"] and not options["target_seconds"]:
        raise ValueError("目标条数与目标时长至少有一个大于零。")
    if options["target_count"] > options["max_downloads"]:
        raise ValueError("max_downloads 不能小于目标条数。")
    for key, default in (("allow_originals", True), ("allow_intelligent_clipping", False),
                         ("include_previously_clipped", False), ("require_audio", False), ("allow_similar_visuals", False)):
        value = payload.get(key, default)
        if type(value) is not bool:
            raise ValueError(f"{key} 必须为布尔值。")
        options[key] = value
    options["quality"] = payload.get("quality", "prefer_qualified")
    if options["quality"] not in {"prefer_qualified", "qualified_only"}:
        raise ValueError("quality 需要为 prefer_qualified 或 qualified_only。")
    options["source_policy"] = payload.get("source_policy", "original_only")
    if options["source_policy"] not in SOURCE_POLICIES:
        raise ValueError("source_policy 需要为 original_only、original_first 或 smart_clip_first。")
    if options["source_policy"] != "smart_clip_first" and not options["allow_originals"]:
        raise ValueError("原片策略要求 allow_originals=true；仅取剪辑成品请显式选 smart_clip_first。")
    if options["source_policy"] == "original_only" and options["allow_intelligent_clipping"]:
        raise ValueError("original_only 不提交百应智能剪辑；需要成品时先显式更改 source_policy。")
    return options


def _summary(data):
    assets = [a for a in data["assets"].values() if _eligible(data, a)]
    eligible_keys = {a["asset_key"] for a in assets}
    counted = [a for a in assets if data["options"].get("allow_similar_visuals") or
               not (set(a.get("similar_to", [])) & eligible_keys)]
    groups = {}
    known = set()
    for asset in counted:
        fragment = asset.get("fragment_id")
        if fragment:
            known.add(fragment)
        key = fragment or asset["asset_key"]
        groups[key] = max(groups.get(key, 0), asset["media"]["duration_seconds"])
    count, seconds = len(counted), round(sum(groups.values()), 3)
    options = data["options"]
    shortage = {"video_count": max(0, options["target_count"] - count),
                "duration_seconds": round(max(0, options["target_seconds"] - seconds), 3),
                "source_count": max(0, options["min_source_count"] - len(known))}
    return {"verified_video_count": len(assets), "counted_video_count": count, "known_source_count": len(known),
            "source_policy": _source_policy(options),
            "original_video_count": sum(a["source"] == "original_fragment" for a in assets),
            "smart_clip_video_count": sum(a["source"] == "smart_clip" for a in assets),
            "total_duration_seconds": round(sum(a["media"]["duration_seconds"] for a in assets), 3),
            "conservative_duration_seconds": seconds,
            "duplicate_count": sum(a["status"] == "duplicate" for a in data["assets"].values()),
            "visually_similar_count": sum(bool(a.get("similar_to")) for a in assets),
            "unreviewed_similar_count": len(assets) - count,
            "failed_count": sum(a["status"] == "failed" for a in data["assets"].values()),
            "shortage": shortage, "targets_met": not any(shortage.values()),
            "semantic_review_required": True,
            "duration_basis": "同一原片段及其切片只计最长一条；不同片段间重叠和内容适用性仍需剪辑审阅。"}


def _eligible(data, asset):
    if asset["status"] != "verified":
        return False
    if asset["source"] == "original_fragment":
        return data["options"]["allow_originals"]
    if _source_policy(data["options"]) == "original_only":
        return False
    return data["options"]["quality"] != "qualified_only" or asset.get("qualified")


def _asset_public(asset):
    result = dict(asset)
    if "media" in result:
        result["media"] = {k: v for k, v in result["media"].items() if k not in {"visual_signatures", "mtime_ns"}}
    return result


def _view(data, row=None, *, offset=0, limit=20):
    running = _running(row) if row else False
    state = data["status"]
    if row and row[1] and not running:
        state = "interrupted"
    assets = list(data["assets"].values())
    return {"schema_version": SCHEMA_VERSION, "job_id": data["job_id"], "status": state,
            "phase": data.get("phase"), "running": running,
            "cancel_requested": bool(row[4]) if row else False,
            **data["scope"], "targets_and_policy": data["options"], "summary": _summary(data),
            "inventory": data.get("inventory"), "reason": data.get("reason"),
            "last_error": data.get("last_error"),
            "current_transfer": data.get("current_transfer"),
            "alternatives": data.get("alternatives", []), "clip_plans": data["clip_plans"],
            "output_dir": data["output_dir"], "manifest_path": data.get("manifest_path"),
            "editor_handoff_path": data.get("editor_handoff_path"),
            "assets": [_asset_public(a) for a in assets[offset:offset + limit]],
            "next_offset": offset + limit if offset + limit < len(assets) else None,
            "created_at": data["created_at"], "updated_at": data["updated_at"],
            "next_action": "material_status" if running else ("import_verified_assets" if state == "ready" else "resume_materials_or_review_shortage")}


def material_status(payload):
    data, row = _load(payload)
    return _view(data, row, offset=replay._integer(payload, "offset", 0, 0, 100000),
                 limit=replay._integer(payload, "limit", 20, 1, 100))


def list_material_jobs(payload):
    owner = client.mobile_hash(client.mobile_from_payload(payload))
    limit = replay._integer(payload, "limit", 10, 1, 50)
    with _db() as connection:
        rows = connection.execute("SELECT data_json,run_token,worker_pid,started_at,cancel_requested FROM material_jobs "
                                  "WHERE mobile_hash=? ORDER BY rowid DESC LIMIT ?", (owner, limit)).fetchall()
    jobs = []
    for row in rows:
        view = _view(json.loads(row[0]), row)
        jobs.append({k: view[k] for k in ("job_id", "status", "running", "account_id", "product_id", "summary",
                                         "manifest_path", "updated_at")})
    return {"jobs": jobs}


def _launch(job_id, token, mobile):
    kwargs = {"stdin": subprocess.PIPE, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
              "cwd": str(Path(__file__).resolve().parent)}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--worker", job_id, token], **kwargs)
    # Phone is passed privately through stdin, never command-line arguments.
    process.stdin.write(json.dumps({"mobile": mobile}).encode("utf-8"))
    process.stdin.close()


def _start(payload, data, updates=None):
    token = secrets.token_hex(16)
    with _db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT data_json, run_token, worker_pid, started_at, cancel_requested "
                                 "FROM material_jobs WHERE job_id=?", (data["job_id"],)).fetchone()
        if _running(row):
            return _view(json.loads(row[0]), row)
        data = json.loads(row[0])
        data["options"] = _saved_config(data["options"])
        if updates:
            data["options"] = _config({**data["options"], **updates})
        if payload.get("retry_failed"):
            for asset in data["assets"].values():
                if asset["status"] == "failed":
                    asset["attempts"] = 0
        data.update(status="queued", phase="queued", reason=None, updated_at=int(time.time()))
        data.pop("last_error", None)
        connection.execute("UPDATE material_jobs SET data_json=?, run_token=?, worker_pid=NULL, started_at=?, "
                           "cancel_requested=0 WHERE job_id=?", (json.dumps(data, ensure_ascii=False), token,
                                                               time.time(), data["job_id"]))
    try:
        _launch(data["job_id"], token, client.mobile_from_payload(payload))
    except (OSError, ValueError) as exc:
        data.update(status="failed", reason="无法启动素材后台任务，可重试原任务。")
        data["last_error"] = diagnostics.record_error(exc, operation="baiying_prepare_materials", stage="launching_worker", next_action="inspect_error_then_resume_materials")
        _save(data, token)
        with _db() as connection:
            connection.execute("UPDATE material_jobs SET run_token=NULL WHERE job_id=? AND run_token=?", (data["job_id"], token))
    return material_status({**payload, "job_id": data["job_id"]})


def prepare_materials(payload):
    options = _config(payload)
    media.ffmpeg_binary()
    scope = {key: replay._identifier(payload, key) for key in ("account_id", "product_id")}
    owner = client.mobile_hash(client.mobile_from_payload(payload))
    request_key = replay._identifier(payload, "request_id") if payload.get("request_id") else None
    root = Path(payload["output_dir"]).expanduser() if payload.get("output_dir") else (
        Path.home() / "Downloads" / "baiying" / scope["account_id"] / scope["product_id"])
    if not root.is_absolute():
        raise ValueError("output_dir 必须是本地或 NAS 的绝对路径。")
    root = root.resolve()
    job_id = "mat_" + secrets.token_hex(12)
    now = int(time.time())
    data = {"job_id": job_id, "scope": scope, "options": options, "output_root": str(root),
            "output_dir": str(root / job_id), "status": "queued", "phase": "queued",
            "created_at": now, "updated_at": now, "assets": {}, "clip_plans": []}
    with _db() as connection:
        try:
            connection.execute("INSERT INTO material_jobs (job_id,mobile_hash,request_key,data_json) VALUES (?,?,?,?)",
                               (job_id, owner, request_key, json.dumps(data, ensure_ascii=False)))
        except sqlite3.IntegrityError:
            row = connection.execute("SELECT data_json FROM material_jobs WHERE mobile_hash=? AND request_key=?",
                                     (owner, request_key)).fetchone()
            if not row:
                raise
            previous = json.loads(row[0])
            if {k: previous["scope"][k] for k in scope} != scope or previous["options"] != options or previous["output_root"] != str(root):
                raise ValueError("request_id 已用于其他参数；请继续原任务或使用新的 request_id。") from None
            return material_status({**payload, "job_id": previous["job_id"]})
    return _start(payload, data)


def resume_materials(payload):
    data, _ = _load(payload)
    permitted = {"source_policy", "allow_originals", "allow_intelligent_clipping", "include_previously_clipped", "quality", "max_pages",
                 "allow_similar_visuals", "max_downloads", "max_file_mb", "max_total_mb", "download_attempts",
                 "max_clip_rounds", "target_count", "target_seconds", "min_source_count"}
    updates = {key: payload[key] for key in permitted if key in payload}
    _config({**data["options"], **updates})
    return _start(payload, data, updates)


def cancel_materials(payload):
    data, row = _load(payload)
    with _db() as connection:
        connection.execute("UPDATE material_jobs SET cancel_requested=1 WHERE job_id=?", (data["job_id"],))
        if not _running(row):
            data.update(status="cancelled", phase="cancelled", reason="用户停止了素材任务；已下载文件保留。")
            connection.execute("UPDATE material_jobs SET run_token=NULL, data_json=? WHERE job_id=?",
                               (json.dumps(data, ensure_ascii=False), data["job_id"]))
    return material_status(payload)


def _refresh(data, mobile, token):
    data.update(status="running", phase="scanning")
    _save(data, token)
    scope = {"mobile": mobile, **data["scope"], "max_pages": data["options"]["max_pages"]}
    account, product, scan = replay._product(scope)
    data["scope"].update(account, product_title=product["product_title"])
    downloadable = {v["fragment_id"] for v in product["videos"].values() if v.get("fragment_id")}
    data["inventory"] = {**scan, **replay._summary(product),
                         "unresolved_fragment_count": len(product["linked_fragments"] - downloadable),
                         "original_downloadable_count": sum(bool(f.get("download_url")) for f in product["fragments"].values())}
    _save(data, token)
    return product


def _candidates(data, product):
    options = data["options"]
    policy = _source_policy(options)
    clips = [{**v, "source": "smart_clip", "caption_status": "unchecked"} for v in product["videos"].values()
             if options["quality"] != "qualified_only" or v["qualified"]]
    clips.sort(key=lambda v: not v["qualified"])
    originals = []
    if options["allow_originals"]:
        originals = [{**f, "video_id": f["fragment_id"], "source": "original_fragment", "qualified": None,
                      "caption_status": "unchecked", "has_clip_record": f["fragment_id"] in product["linked_fragments"]}
                  for f in product["fragments"].values()]
    items = originals if policy == "original_only" else (originals + clips if policy == "original_first" else clips + originals)
    return [dict(v, asset_key=f"{v['source']}:{v['video_id']}") for v in items if v.get("download_url")]


def _validate_asset(data, asset, token):
    evidence = media.validate_video(asset["path"], checkpoint=lambda: _save(data, token))
    asset["media"] = evidence
    if evidence["duration_seconds"] < data["options"]["min_duration_seconds"]:
        raise ValueError("视频短于要求的最小时长。")
    if data["options"]["require_audio"] and not evidence["has_audio"]:
        raise ValueError("视频没有音轨，当前任务要求有声音。")
    asset.update(status="verified", verified_at=int(time.time()))
    asset.pop("error", None)
    asset.pop("error_detail", None)
    asset.pop("duplicate_of", None)
    asset["similar_to"] = []
    for other in data["assets"].values():
        if other is asset or other["status"] != "verified":
            continue
        if other["media"]["sha256"] == evidence["sha256"]:
            asset.update(status="duplicate", duplicate_of=other["asset_key"])
            break
        if media.similar_visuals(evidence, other["media"]):
            asset["similar_to"].append(other["asset_key"])


def _verify_saved(data, token):
    for asset in data["assets"].values():
        if asset["status"] in {"downloading", "queued"}:
            asset.update(status="queued", attempts=max(0, asset["attempts"] - 1))
        if asset["status"] not in {"verified", "duplicate"}:
            continue
        data["phase"] = "checking_saved_files"
        _save(data, token)
        try:
            if not Path(asset["path"]).is_file() or media.file_hash(asset["path"]) != asset["media"]["sha256"]:
                raise ValueError("已下载文件丢失或内容发生变化，需要重新下载。")
        except (OSError, ValueError) as exc:
            asset.update(status="failed", error="已下载文件丢失或变化，未计入可用素材。", attempts=0)
            asset["error_detail"] = diagnostics.record_error(exc, operation="baiying_prepare_materials", stage="checking_saved_files", next_action="resume_materials_retry_failed")
    # A duplicate may be the remaining healthy copy of a removed original.
    for asset in data["assets"].values():
        if asset["status"] == "duplicate" and data["assets"].get(asset.get("duplicate_of"), {}).get("status") != "verified":
            asset.update(status="verified")
            asset.pop("duplicate_of", None)
    _save(data, token)


def _download_candidates(data, product, mobile, token):
    folder = Path(data["output_dir"])
    folder.mkdir(parents=True, exist_ok=True)
    for candidate in _candidates(data, product):
        if _summary(data)["targets_met"]:
            break
        key = candidate["asset_key"]
        previous = data["assets"].get(key)
        if previous and previous["status"] in {"verified", "duplicate"}:
            continue
        if not previous and len(data["assets"]) >= data["options"]["max_downloads"]:
            continue
        replay._identifier({"video_id": candidate["video_id"]}, "video_id")
        asset = previous or {**candidate, "status": "queued", "attempts": 0}
        # URLs are refreshed at each pass and retry, not trusted from saved jobs.
        asset.update(download_url=candidate["download_url"], links_refreshed_at=int(time.time()))
        data["assets"][key] = asset
        while asset["attempts"] < data["options"]["download_attempts"]:
            asset["attempts"] += 1
            target = folder / f"{asset['source']}_{asset['video_id']}.mp4"
            if asset.get("status") == "failed" and target.exists():
                target = folder / f"{asset['source']}_{asset['video_id']}_retry_{secrets.token_hex(3)}.mp4"
            asset.update(path=str(target), status="downloading")
            data["phase"] = "downloading"
            _save(data, token)
            try:
                if not target.exists():
                    used_bytes = sum(p.stat().st_size for p in folder.iterdir() if p.is_file())
                    remaining_bytes = data["options"]["max_total_mb"] * 1024 * 1024 - used_bytes
                    if remaining_bytes <= 0:
                        raise ValueError("已达到本任务下载总容量上限。")
                    last_update = [0.0]

                    def progress(received, expected):
                        if time.monotonic() - last_update[0] >= 1:
                            data["current_transfer"] = {"asset_key": key, "received_bytes": received, "expected_bytes": expected or None}
                            _save(data, token)
                            last_update[0] = time.monotonic()

                    replay._download(asset["download_url"], target, 30,
                                     max_bytes=min(data["options"]["max_file_mb"] * 1024 * 1024, remaining_bytes), checkpoint=progress)
                data.pop("current_transfer", None)
                data["phase"] = "verifying"
                _save(data, token)
                _validate_asset(data, asset, token)
                _save(data, token)
                break
            except (requests.RequestException, ValueError, OSError, subprocess.SubprocessError) as exc:
                detail = diagnostics.record_error(exc, operation="baiying_prepare_materials", stage=data.get("phase"), next_action="resume_materials_retry_failed")
                asset.update(status="failed", error=detail["message"], error_detail=detail)
                _save(data, token)
                if asset["attempts"] < data["options"]["download_attempts"]:
                    product = _refresh(data, mobile, token)
                    fresh = next((c for c in _candidates(data, product) if c["asset_key"] == key), None)
                    if not fresh:
                        asset["error"] = "来源已不可用或不再符合当前素材策略。"
                        break
                    asset.update(download_url=fresh["download_url"], links_refreshed_at=int(time.time()))
        _save(data, token)
    return product


def _atomic_json(path, value):
    temporary = path.with_name(path.name + "." + secrets.token_hex(4) + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _export(data):
    folder = Path(data["output_dir"])
    folder.mkdir(parents=True, exist_ok=True)
    manifest, handoff = folder / "materials.json", folder / "editor_handoff.json"
    data.update(manifest_path=str(manifest), editor_handoff_path=str(handoff))
    _atomic_json(manifest, _view(data, limit=100))
    verified = [a for a in data["assets"].values() if _eligible(data, a)]
    _atomic_json(handoff, {"schema_version": "baiying-editor-handoff/v1", "job_id": data["job_id"],
                          "scope": data["scope"], "status": data["status"], "summary": _summary(data),
                          "source_policy": _source_policy(data["options"]),
                          "selection_workflow": "original_source_asr_timeline_then_visual_review",
                          "source_caption_review_required": True,
                          "manifest_path": str(manifest), "editorial_review_required": True,
                          "assets": [{"source_path": a["path"], "asset_key": a["asset_key"],
                                      "source": a["source"], "caption_status": a.get("caption_status", "unchecked"),
                                      "has_clip_record": a.get("has_clip_record"),
                                      "fragment_id": a.get("fragment_id"), "sha256": a["media"]["sha256"],
                                      "media": _asset_public(a)["media"],
                                      "suggested_import_tool": "media_register",
                                      "import_arguments": {"source_path": a["path"], "mode": "managed_copy"},
                                      "similar_to": a.get("similar_to", [])} for a in verified],
                          "instructions": "在剪辑插件创建或选择项目后，将 project_id 与每项 import_arguments 合并交给 media_register 异步队列。保存 asset_key、sha256、project_id、job_id 的映射，重复交接先查原任务，避免重复导入。用 media_job_status 等待 succeeded，再核对 result.asset.identity.sha256；失败用 media_job_retry 排队新尝试并保存新 job_id。素材不足不妨碍导入已验证部分，但必须保留 summary 缺口。仅检查交接时停止在导入，不创建时间线或渲染任务；继续剪辑前先对原片完成 ASR 时间线、完整句及时间戳校验；保留展示停顿，ASR 空档不证明无声或闭嘴，仍需实际看听；caption_status=unchecked 不认证无字幕。"})


def _poll_plan(data, entry, mobile, token):
    payload = {"mobile": mobile, "plan_id": entry["plan_id"]}
    while True:
        data.update(status="waiting_for_clips", phase="waiting_for_clips")
        _save(data, token)
        result = replay.check_clips(payload)
        entry["new_video_count"] = result["new_video_count"]
        found = {v.get("fragment_id") for v in result["new_videos"]}
        rejected = {r["fragment_id"] for r in entry.get("submission", {}).get("results", []) if r["state"] == "rejected"}
        if set(entry["fragment_ids"]) - rejected <= found:
            entry["state"] = "videos_available"
            return True
        if time.time() >= entry["deadline"]:
            entry["state"] = "timed_out"
            return bool(found)
        # Status/cancel remain available in another process; no MCP call waits.
        for _ in range(15):
            _save(data, token)
            time.sleep(1)


def _clipping(data, product, mobile, token):
    if _source_policy(data["options"]) == "original_only":
        return product
    for entry in data["clip_plans"]:
        if entry["state"] in {"videos_available", "rejected"}:
            continue
        if entry["state"] == "prepared":
            if not data["options"]["allow_intelligent_clipping"]:
                return product
            _, row, old_plan = replay._load_plan({"mobile": mobile, "plan_id": entry["plan_id"]})
            if row[2] == "prepared" and time.time() - row[0] > replay.PLAN_TTL:
                # No POST was started: only this expired, unsubmitted plan can
                # be safely replaced. Submitting/unknown plans are never reset.
                fresh = replay.prepare_clips({"mobile": mobile, **data["scope"],
                    "max_pages": data["options"]["max_pages"], "fragment_ids": entry["fragment_ids"],
                    "include_previously_clipped": old_plan["include_previously_clipped"]})
                entry.update(replaced_unsubmitted_plan_id=entry["plan_id"], plan_id=fresh["plan_id"],
                             deadline=time.time() + data["options"]["clip_wait_seconds"])
                _save(data, token)
            entry["submission"] = replay.start_clips({"mobile": mobile, "plan_id": entry["plan_id"]})
            entry["state"] = "rejected" if entry["submission"].get("rejected_count") == len(entry["fragment_ids"]) else "submitted"
            _save(data, token)
            if entry["state"] == "rejected":
                return product
        _poll_plan(data, entry, mobile, token)
        product = _refresh(data, mobile, token)
        product = _download_candidates(data, product, mobile, token)
        if _summary(data)["targets_met"]:
            return product
        if entry["state"] == "timed_out":
            return product
    options = data["options"]
    while options["allow_intelligent_clipping"] and len(data["clip_plans"]) < options["max_clip_rounds"]:
        if _summary(data)["targets_met"] or len(data["assets"]) >= options["max_downloads"]:
            break
        if not data["inventory"]["scan_complete"]:
            break
        attempted = {f for p in data["clip_plans"] for f in p["fragment_ids"]}
        candidates = [f for f in product["fragments"] if f not in attempted and (
            options["include_previously_clipped"] or f not in product["linked_fragments"])]
        if not candidates:
            break
        plan = replay.prepare_clips({"mobile": mobile, **data["scope"], "max_pages": options["max_pages"],
                                     "fragment_ids": candidates[:options["fragments_per_round"]],
                                     "include_previously_clipped": options["include_previously_clipped"]})
        entry = {"plan_id": plan["plan_id"], "fragment_ids": [f["fragment_id"] for f in plan["selected_fragments"]],
                 "state": "prepared", "deadline": time.time() + options["clip_wait_seconds"]}
        data["clip_plans"].append(entry)
        _save(data, token)  # Persist before any external mutation for crash recovery.
        entry["submission"] = replay.start_clips({"mobile": mobile, "plan_id": entry["plan_id"]})
        submission = entry["submission"]
        entry["state"] = "rejected" if submission.get("rejected_count") == len(entry["fragment_ids"]) else "submitted"
        _save(data, token)
        if entry["state"] == "rejected":
            break
        _poll_plan(data, entry, mobile, token)
        product = _refresh(data, mobile, token)
        product = _download_candidates(data, product, mobile, token)
        if entry["state"] == "timed_out":
            break
    return product


def _finish(data, product):
    data["phase"] = "finished"
    data.pop("current_transfer", None)
    if _summary(data)["targets_met"]:
        data.update(status="ready", reason="已达到设定的文件数量、时长和来源目标；可交给剪辑做内容审阅。")
        return
    inventory, options = data["inventory"], data["options"]
    alternatives = []
    if _summary(data)["unreviewed_similar_count"]:
        alternatives.append("审阅画面高度相似的视频；确认内容有区别后允许计入目标")
    if not options["allow_originals"] and inventory["original_downloadable_count"]:
        alternatives.append("允许使用同商品原回放片段")
    original_only = _source_policy(options) == "original_only"
    if not original_only and options["quality"] == "qualified_only" and inventory["existing_video_count"] > inventory["qualified_video_count"]:
        alternatives.append("将其他已有切片纳入候选，交由剪辑审阅")
    if not original_only and inventory["unclipped_fragment_count"] and not options["allow_intelligent_clipping"]:
        alternatives.append("允许百应智能切片后继续原任务")
    data["alternatives"] = alternatives
    if not inventory["scan_complete"]:
        data.update(status="partial", reason="回放未扫描完整，不能认定素材已经找完；提高 max_pages 后继续原任务。")
    elif not original_only and any(p["state"] == "timed_out" for p in data["clip_plans"]):
        data.update(status="partial", reason="等待生成已到期限；结果可能稍后出现。保留原计划查询，不自动重新提交。")
    elif not original_only and any(p["state"] == "rejected" for p in data["clip_plans"]):
        data.update(status="partial", reason="百应拒绝了智能切片任务；请查看计划结果，已下载素材保留。")
    elif any(a["status"] == "failed" for a in data["assets"].values()):
        data.update(status="partial", reason="部分素材未能下载或通过验证；可在修复网络、空间或来源问题后重试失败项。")
    elif len(data["assets"]) >= options["max_downloads"]:
        data.update(status="partial", reason="已达到本任务的下载数量上限，尚未满足目标。")
    elif alternatives:
        data.update(status="needs_input", reason="当前策略下素材不足，可以选择补充来源后继续原任务。")
    elif options["allow_intelligent_clipping"] and len(data["clip_plans"]) >= options["max_clip_rounds"]:
        data.update(status="partial", reason="已达到智能切片轮数上限，尚未满足目标。")
    elif not original_only and inventory["unresolved_fragment_count"]:
        data.update(status="partial", reason="部分片段有关联剪辑记录但没有视频链接，状态尚不明确；可稍后刷新。")
    else:
        data.update(status="source_exhausted", reason="已扫描的该账号、该商品可用来源不足；已交付现有素材与缺口，未混入其他商品或账号。")


def run_worker(job_id, token, mobile):
    payload = {"mobile": mobile, "job_id": job_id}
    data, _ = _load(payload)
    with _db() as connection:
        claimed = connection.execute("UPDATE material_jobs SET worker_pid=? WHERE job_id=? AND run_token=? AND worker_pid IS NULL",
                                     (os.getpid(), job_id, token)).rowcount
    if not claimed:
        return
    try:
        _verify_saved(data, token)
        product = None
        if not _summary(data)["targets_met"]:
            media.ffmpeg_binary()
            product = _refresh(data, mobile, token)
            product = _download_candidates(data, product, mobile, token)
            if not _summary(data)["targets_met"]:
                product = _clipping(data, product, mobile, token)
        _finish(data, product)
        _save(data, token)
    except Cancelled:
        data.update(status="cancelled", phase="cancelled", reason="任务已停止；已下载文件保留，已提交的百应任务不会被撤销。")
    except Exception as exc:
        detail = diagnostics.record_error(exc, operation="baiying_prepare_materials", stage=data.get("phase"), next_action="inspect_error_then_resume_materials")
        data["last_error"] = {"type": type(exc).__name__, **detail}
        data.update(status="failed", phase="failed", reason=detail["message"] + " 已保存进度，处理后继续原任务；不会自动重发已提交剪辑。")
    finally:
        with _db() as connection:
            active = connection.execute("SELECT run_token FROM material_jobs WHERE job_id=?", (job_id,)).fetchone()
        if active is None or active[0] != token:
            return
        try:
            _export(data)
        except OSError as exc:
            data.update(status="failed", reason="素材清单写入失败，请检查磁盘或 NAS 连接；素材记录已保留。")
            data["last_error"] = diagnostics.record_error(exc, operation="baiying_prepare_materials", stage="exporting_manifest", next_action="inspect_error_then_resume_materials")
            data.pop("manifest_path", None)
            data.pop("editor_handoff_path", None)
        data["updated_at"] = int(time.time())
        with _db() as connection:
            connection.execute("UPDATE material_jobs SET data_json=?, run_token=NULL, worker_pid=NULL WHERE job_id=? AND run_token=?",
                               (json.dumps(data, ensure_ascii=False), job_id, token))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", nargs=2, metavar=("JOB_ID", "TOKEN"), required=True)
    args = parser.parse_args()
    run_worker(*args.worker, json.loads(sys.stdin.buffer.read())["mobile"])
