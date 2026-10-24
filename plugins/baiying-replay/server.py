"""Standalone stdio MCP server. Login material lives outside the plugin package."""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path


def configure_storage():
    data = Path(os.environ.get("BAIYING_DATA_DIR") or
                (Path(os.environ.get("LOCALAPPDATA") or (Path.home() / ".local/share")) / "baiying-replay"))
    # Reuse the user's existing local-platform login when that optional cache
    # exists. No dependency on the platform process or database is required.
    previous = Path.home() / "Desktop/zjfs_data_platform/workflow_scripts/baiying_login/storage/baiying_cookie_cache.sqlite3"
    if not os.environ.get("BAIYING_COOKIE_CACHE_PATH"):
        os.environ["BAIYING_COOKIE_CACHE_PATH"] = str(previous if previous.is_file() else data / "cookies.sqlite3")
    if not os.environ.get("BAIYING_REPLAY_STATE_PATH"):
        os.environ["BAIYING_REPLAY_STATE_PATH"] = str(data / "replay.sqlite3")
    return data


DATA_DIR = configure_storage()
PLUGIN_VERSION = json.loads((Path(__file__).resolve().parent / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))["version"]
if __package__:
    from .baiying import login_service, replay_service, material_service, diagnostics
else:
    from baiying import login_service, replay_service, material_service, diagnostics


PHONE = {"type": "string", "pattern": "^[0-9]{11}$", "description": "百应登录手机号；查询工具省略时复用插件上次使用的手机号"}
IDENTIFIER = {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,80}$"}
ACCOUNT = {**IDENTIFIER, "description": "从 list_accounts 选择的 account_id，不能填写账号名称"}
PRODUCT = {**IDENTIFIER, "description": "从 search_products 选择的 product_id，不能填写款号"}
LIMIT = {"type": "integer", "minimum": 1, "maximum": 100, "default": 5}
OFFSET = {"type": "integer", "minimum": 0, "maximum": 100000, "default": 0}
IDS = {"type": "array", "minItems": 1, "maxItems": 20, "items": IDENTIFIER}
SCOPE = {"mobile": PHONE, "account_id": ACCOUNT, "product_id": PRODUCT}
PAGING = {"limit": LIMIT, "offset": OFFSET, "max_pages": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}}
MATERIAL_POLICY = {
    "source_policy": {"type": "string", "enum": ["original_only", "original_first", "smart_clip_first"], "default": "original_only", "description": "后续自行剪辑默认 original_only：仅下载商品原回放，保留展示停顿，不混入成品也不自动智能剪辑。original_first 允许原片不足时补成品；smart_clip_first 显式恢复成品优先。来源不认证无字幕，须后续看图检查。"},
    "quality": {"type": "string", "enum": ["prefer_qualified", "qualified_only"], "default": "prefer_qualified"},
    "allow_originals": {"type": "boolean", "default": True, "description": "允许同商品原回放；原片策略必须为true。原片无声/未识别讲话的区间需保留，后续仅可作配独立声音的展示。"},
    "allow_intelligent_clipping": {"type": "boolean", "default": False, "description": "仅在用户已允许继续百应智能切片时开启"},
    "include_previously_clipped": {"type": "boolean", "default": False, "description": "仅在用户明确允许重剪已剪过片段时开启"},
    "allow_similar_visuals": {"type": "boolean", "default": False, "description": "默认不将高度相似画面重复计入数量；内容审阅确认有区别后可开启"},
    "max_pages": PAGING["max_pages"],
}


def tool(name, description, properties, required, handler, *, read=True, idempotent=True):
    return {"name": "baiying_" + name, "description": description,
            "inputSchema": {"type": "object", "additionalProperties": False,
                            "properties": properties, "required": required},
            "annotations": {"readOnlyHint": read, "destructiveHint": False,
                            "idempotentHint": idempotent, "openWorldHint": True}}, handler


DEFINITIONS = [
    tool("login_status", "检查百应手机号登录状态，不发送短信。提供手机号且登录有效时记住该手机号供后续查询使用。",
         {"mobile": PHONE}, [], login_service.prepare),
    tool("send_code", "向用户指定的百应手机号发送一次短信验证码，返回 challenge_id；不自动轮询短信。",
         {"mobile": PHONE}, ["mobile"], login_service.send, read=False, idempotent=False),
    tool("complete_login", "使用此次短信的 challenge_id 和六位验证码完成登录；不会重发短信或返回 Cookie。",
         {"mobile": PHONE, "challenge_id": IDENTIFIER,
          "verification_code": {"type": "string", "pattern": "^[0-9]{6}$"}},
         ["mobile", "challenge_id", "verification_code"], login_service.complete, read=False, idempotent=False),
    tool("list_accounts", "列出该手机号可登录的子账号 ID 和名称；不遍历采集所有账号。",
         {"mobile": PHONE}, [], replay_service.list_accounts),
    tool("search_products", "只在指定子账号的百应回放中搜索商品名称、款号或商品 ID，返回商品 ID 和现有切片数量。",
         {"mobile": PHONE, "account_id": ACCOUNT, "query": {"type": "string", "maxLength": 200}, **PAGING,
          "limit": {**LIMIT, "default": 20}},
         ["account_id"], replay_service.search_products),
    tool("get_product_videos", "获取指定子账号、指定商品的最新视频链接、封面、时长和原回放片段。默认只返回四项推荐标签合格的视频，最多5条；quality=all 可看全部已有智能切片。只查询，不下载、不自动剪辑。",
         {**SCOPE, **PAGING, "quality": {"type": "string", "enum": ["qualified", "all"], "default": "qualified"},
          "target_count": LIMIT, "fragment_offset": OFFSET,
          "fragment_limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10}},
         ["account_id", "product_id"], replay_service.get_product_videos),
    tool("download_videos", "下载用户选择的商品视频 ID，下载前刷新链接并检查归属。默认下载智能切片；original_fragment 下载原回放片段，此时 video_ids 填 fragment_id。保存MP4和下载清单，已存在文件不覆盖。",
         {**SCOPE, "video_ids": IDS,
          "source": {"type": "string", "enum": ["smart_clip", "original_fragment"], "default": "smart_clip"},
          "output_dir": {"type": "string", "maxLength": 1000, "description": "本地或NAS绝对目录，省略时为用户Downloads/baiying/账号ID/商品ID"}},
         ["account_id", "product_id", "video_ids"], replay_service.download_videos, read=False),
    tool("prepare_clips", "准备指定商品的智能剪辑计划并返回待剪片段；不会向百应提交任务。默认选1个尚无剪辑记录的片段；已有剪辑的片段仅在明确要求重剪时选入。",
         {**SCOPE, "fragment_ids": {**IDS, "maxItems": 5},
          "max_fragments": {"type": "integer", "minimum": 1, "maximum": 5, "default": 1},
          "include_previously_clipped": {"type": "boolean", "default": False}},
         ["account_id", "product_id"], replay_service.prepare_clips),
    tool("start_clips", "用户已选择继续智能剪辑时，提交 prepare_clips 返回的计划。每计划最多5个片段，同一计划不会重复提交；返回受理状态，不代表视频已生成。",
         {"mobile": PHONE, "plan_id": IDENTIFIER}, ["plan_id"], replay_service.start_clips, read=False),
    tool("check_clips", "查询已提交计划对应商品的新视频。只报告所选片段新增的视频；无新视频时不会再次发起剪辑，也不会无限等待。",
         {"mobile": PHONE, "plan_id": IDENTIFIER}, ["plan_id"], replay_service.check_clips),
    tool("prepare_materials", "为后续剪辑准备指定账号、商品的原始素材包：默认 source_policy=original_only，直接下载智能剪辑前的商品原回放，已有剪辑记录的原片也保留。后台完整解码、去重并报告缺口，不因成品够数跳过原片、不提交智能剪辑。原片不足不静默混入成品；需显式改 source_policy。快速返回job_id，持续查询material_status。ready仅为技术达标；下一步在原片上做ASR时间线并检查展示/字幕/选区声音。",
         {**SCOPE, **MATERIAL_POLICY, "request_id": IDENTIFIER,
          "target_count": {"type": "integer", "minimum": 0, "maximum": 100, "default": 1},
          "target_seconds": {"type": "integer", "minimum": 0, "maximum": 36000, "default": 0, "description": "同原片段只累计最长视频；0不限制，条数与时长不能同时0"},
          "min_source_count": {"type": "integer", "minimum": 0, "maximum": 100, "default": 0},
          "min_duration_seconds": {"type": "integer", "minimum": 1, "maximum": 3600, "default": 2},
          "require_audio": {"type": "boolean", "default": False},
          "output_dir": {"type": "string", "maxLength": 1000},
          "max_downloads": {"type": "integer", "minimum": 1, "maximum": 100, "default": 30},
          "download_attempts": {"type": "integer", "minimum": 1, "maximum": 3, "default": 2},
          "max_file_mb": {"type": "integer", "minimum": 1, "maximum": 8192, "default": 2048},
          "max_total_mb": {"type": "integer", "minimum": 1, "maximum": 102400, "default": 4096},
          "max_clip_rounds": {"type": "integer", "minimum": 1, "maximum": 5, "default": 2},
          "fragments_per_round": {"type": "integer", "minimum": 1, "maximum": 5, "default": 1},
          "clip_wait_seconds": {"type": "integer", "minimum": 30, "maximum": 1800, "default": 300}},
         ["account_id", "product_id"], material_service.prepare_materials, read=False, idempotent=False),
    tool("material_status", "读取素材任务进度、实际验证通过的数量和时长、缺口、下载链接及剪辑交接清单。不会启动下载或提交剪辑。",
         {"mobile": PHONE, "job_id": IDENTIFIER, "limit": {**LIMIT, "default": 20}, "offset": OFFSET},
         ["job_id"], material_service.material_status),
    tool("resume_materials", "继续同一素材任务，复核已有文件并刷新来源；复用原剪辑计划，不重复提交状态未知的任务。可按用户偏好调整补充来源，retry_failed 重新尝试失败下载。",
         {"mobile": PHONE, "job_id": IDENTIFIER,
          **{key: {k: v for k, v in spec.items() if k != "default"} for key, spec in MATERIAL_POLICY.items()},
          "target_count": {"type": "integer", "minimum": 0, "maximum": 100},
          "target_seconds": {"type": "integer", "minimum": 0, "maximum": 36000},
          "min_source_count": {"type": "integer", "minimum": 0, "maximum": 100},
          "max_downloads": {"type": "integer", "minimum": 1, "maximum": 100},
          "max_file_mb": {"type": "integer", "minimum": 1, "maximum": 8192},
          "max_total_mb": {"type": "integer", "minimum": 1, "maximum": 102400},
          "download_attempts": {"type": "integer", "minimum": 1, "maximum": 3},
          "max_clip_rounds": {"type": "integer", "minimum": 1, "maximum": 5},
          "retry_failed": {"type": "boolean", "default": False}},
         ["job_id"], material_service.resume_materials, read=False),
    tool("cancel_materials", "按用户要求停止素材任务；保留已下载文件，无法撤销已向百应提交的生成任务。",
         {"mobile": PHONE, "job_id": IDENTIFIER}, ["job_id"], material_service.cancel_materials, read=False),
    tool("list_material_jobs", "找回该登录手机号最近的素材任务，返回任务ID、状态和素材清单位置。",
         {"mobile": PHONE, "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10}},
         [], material_service.list_material_jobs),
]
TOOLS = [item[0] for item in DEFINITIONS]
HANDLERS = {item[0]["name"]: item[1] for item in DEFINITIONS}


def _validate_value(key, value, spec):
    kind = spec["type"]
    if kind == "string":
        valid = isinstance(value, str) and len(value) <= spec.get("maxLength", 1000)
        valid = valid and ("pattern" not in spec or re.fullmatch(spec["pattern"], value) is not None)
        valid = valid and ("enum" not in spec or value in spec["enum"])
    elif kind == "integer":
        valid = type(value) is int and spec.get("minimum", 0) <= value <= spec.get("maximum", 100000)
    elif kind == "boolean":
        valid = type(value) is bool
    elif kind == "array":
        valid = isinstance(value, list) and spec.get("minItems", 0) <= len(value) <= spec.get("maxItems", 100)
        if valid:
            for item in value:
                _validate_value(key, item, spec["items"])
    else:
        valid = False
    if not valid:
        raise ValueError(f"参数 {key} 格式不正确。")


def validate(name, arguments):
    definition = next((item for item in TOOLS if item["name"] == name), None)
    if definition is None:
        raise ValueError("Unknown tool")
    schema = definition["inputSchema"]
    if not isinstance(arguments, dict) or set(arguments) - set(schema["properties"]) or set(schema["required"]) - set(arguments):
        raise ValueError("工具参数缺失或包含不支持的字段。")
    for key, value in arguments.items():
        _validate_value(key, value, schema["properties"][key])
    return dict(arguments)


def _profile(mobile=None):
    with replay_service._state() as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS active_profile (id INTEGER PRIMARY KEY CHECK(id=1), mobile TEXT NOT NULL)")
        if mobile:
            connection.execute("INSERT INTO active_profile VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET mobile=excluded.mobile", (mobile,))
            return mobile
        row = connection.execute("SELECT mobile FROM active_profile WHERE id=1").fetchone()
        return row[0] if row else None


def call_tool(name, arguments):
    payload = validate(name, arguments)
    if not payload.get("mobile"):
        payload["mobile"] = _profile()
        if not payload["mobile"]:
            raise ValueError("首次使用请提供百应登录手机号；有效登录状态会自动复用。")
    result = HANDLERS[name](payload)
    if name in {"baiying_complete_login", "baiying_list_accounts"} or (
        name == "baiying_login_status" and result.get("cookies_ready")
    ):
        _profile(payload["mobile"])
    if "mobile" in result:
        result = dict(result)
        mobile = result.pop("mobile")
        result["masked_mobile"] = mobile[:3] + "****" + mobile[-4:]
    return result


def error(message_id, code, message):
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def handle(request):
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
        return error(None, -32600, "Invalid Request")
    if "id" not in request:
        return None
    message_id, method = request["id"], request["method"]
    if method == "initialize":
        params = request.get("params") or {}
        version = params.get("protocolVersion", "2025-03-26")
        if version not in {"2024-11-05", "2025-03-26", "2025-06-18"}:
            version = "2025-03-26"
        result = {"protocolVersion": version, "capabilities": {"tools": {}},
                  "serverInfo": {"name": "baiying-replay", "version": PLUGIN_VERSION}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = request.get("params")
        if not isinstance(params, dict) or params.get("name") not in HANDLERS:
            return error(message_id, -32602, "Unknown tool")
        try:
            data = call_tool(params["name"], params.get("arguments", {}))
            result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
                      "structuredContent": data, "isError": False}
        except Exception as exc:
            definition = next(item for item in TOOLS if item["name"] == params["name"])
            detail = diagnostics.record_error(exc, operation=params["name"],
                                              read_only=definition["annotations"]["readOnlyHint"])
            data = {"ok": False, "error": detail}
            result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
                      "structuredContent": data, "isError": True}
    else:
        return error(message_id, -32601, "Method not found")
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def main():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Baiying replay MCP server")
    parser.add_argument("--call", choices=list(HANDLERS), help="一次调用，参数从 stdin JSON 读取")
    args = parser.parse_args()
    if args.call:
        response = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": args.call, "arguments": json.loads(sys.stdin.read() or "{}"),
        }})
        print(json.dumps(response, ensure_ascii=False))
        return 1 if response.get("error") or response.get("result", {}).get("isError") else 0
    for line in sys.stdin:
        try:
            response = handle(json.loads(line))
        except json.JSONDecodeError:
            response = error(None, -32700, "Parse error")
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
