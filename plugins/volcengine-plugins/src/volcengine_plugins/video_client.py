from __future__ import annotations

import base64
import asyncio
import hashlib
import io
import json
import re
from pathlib import Path
from urllib.parse import urlsplit, urlencode

import httpx
from mutagen.mp3 import MP3
from mutagen.wave import WAVE
from PIL import Image

from .client import SeedreamError
from .config import configuration_status, get_config
from .video_models import ALIASES, DEFAULT_VIDEO_MODEL, PROFILES, VideoLocalOptions, VideoRequest
from .task_io import TaskError, observation, safe_task, download

SOURCES = ["https://www.volcengine.com/docs/82379/1520757", "https://www.volcengine.com/docs/82379/2607688", "https://www.volcengine.com/docs/82379/1521309"]
TASK_PATH = "/contents/generations/tasks"
MAX_BODY_BYTES = 64 * 1024 * 1024
UNSUPPORTED = {"frames", "seed", "camera_fixed", "draft", "draft_task", "service_tier"}


def resolve_profile(model: str, asserted: str | None) -> tuple[str, str]:
    model = ALIASES.get(model, model)
    known = next((name for name, caps in PROFILES.items() if caps["model"] == model), None)
    if known and asserted and known != asserted:
        raise ValueError("capability_profile conflicts with the selected official model; no model substitution")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", model):
        raise ValueError("Invalid model ID")
    if not known and not asserted:
        raise ValueError("Unknown model / endpoint ID requires local.capability_profile; never infer capabilities from ep-* or silently fall back")
    return model, known or asserted


def web_url(value: str) -> bool:
    try:
        p = urlsplit(value)
        return p.scheme in ("https", "http") and bool(p.hostname) and not p.username and not p.password and not p.fragment
    except ValueError:
        return False


def media_bytes(raw: bytes, kind: str) -> tuple[str, float | None]:
    limit = (30 if kind == "image" else 15) * 1024 * 1024
    if len(raw) >= limit:
        raise ValueError(f"{kind} reference reaches the conservative {limit // 1024 // 1024} MiB size limit")
    if kind == "image":
        try:
            with Image.open(io.BytesIO(raw)) as im:
                w, h = im.size
                mime = Image.MIME.get(im.format or "")
                if im.format not in {"JPEG", "PNG", "WEBP", "BMP", "TIFF", "GIF"}:
                    raise ValueError
                im.verify()
        except Exception:
            raise ValueError("Cannot locally validate image. Use a supported image or provider-accessible URL/asset for HEIC/HEIF; no automatic conversion") from None
        if not (300 <= w <= 6000 and 300 <= h <= 6000 and 0.4 <= w / h <= 2.5):
            raise ValueError("Seedance image dimensions must be 300..6000 and width/height ratio 0.4..2.5")
        return mime, None
    try:
        if raw.startswith(b"RIFF") and raw[8:12] == b"WAVE":
            return "audio/wav", WAVE(io.BytesIO(raw)).info.length
        return "audio/mp3", MP3(io.BytesIO(raw)).info.length
    except Exception:
        raise ValueError("Reference audio must be a valid MP3 or WAV") from None


def normalize_media(value: str, kind: str, allow_local: bool) -> tuple[str, float | None, bool]:
    """Returns URI, known duration, and whether media metadata was validated locally."""
    if re.fullmatch(r"asset://[A-Za-z0-9_-]+", value):
        return value, None, False
    if web_url(value):
        return value, None, False  # No download or credentials forwarded; Ark validates remote metadata.
    if kind == "video":
        raise ValueError("Reference video requires an HTTP(S) URL or asset://ID. Native API does not accept base64 or local video paths; upload using your own asset/TOS workflow first")
    limit = (30 if kind == "image" else 15) * 1024 * 1024
    if value.startswith("data:"):
        if len(value) > limit * 4 // 3 + 128:
            raise ValueError("Reference data URI exceeds media size limit")
        try:
            header, encoded = value.split(",", 1)
            if not re.fullmatch(r"data:" + kind + r"/[a-z0-9.+-]+;base64", header):
                raise ValueError
            raw = base64.b64decode(encoded, validate=True)
        except ValueError:
            raise ValueError("Invalid reference base64 data URI") from None
        mime, duration = media_bytes(raw, kind)
        supplied = header[5:-7]
        if supplied != mime and not (mime == "audio/mp3" and supplied == "audio/mpeg"):
            raise ValueError("Reference data URI MIME type does not match file bytes")
        return value, duration, True
    if not allow_local:
        raise ValueError("Use an HTTP(S) URL, asset://ID, data URI, or explicitly enable local.allow_local_files for absolute image/audio paths")
    path = Path(value)
    if not path.is_absolute() or not path.is_file():
        raise ValueError("Local reference must be an existing absolute image/audio file path")
    try:
        if path.stat().st_size >= limit:
            raise ValueError("Local reference exceeds size limit")
        with path.open("rb") as handle:
            raw = handle.read(limit)
    except OSError:
        raise ValueError("Cannot read local reference file") from None
    mime, duration = media_bytes(raw, kind)
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii"), duration, True


def prepare_video(request: VideoRequest, local: VideoLocalOptions, default_model: str = DEFAULT_VIDEO_MODEL) -> tuple[dict, list[str], list[dict]]:
    model, profile = resolve_profile(request.model or default_model, local.capability_profile)
    caps = PROFILES[profile]
    body = request.model_dump(exclude_none=True, exclude={"extra_body"})
    body["model"] = model
    reserved = set(VideoRequest.model_fields) | UNSUPPORTED | {"api_key", "authorization", "headers", "base_url", "url", "token", "secret"}
    if reserved.intersection(key.lower() for key in request.extra_body):
        raise ValueError("extra_body cannot override native fields, unsupported fields, or transport/auth settings")
    if request.extra_body and not local.allow_unverified_parameters:
        raise ValueError("Future fields require local.allow_unverified_parameters=true")
    body.update(request.extra_body)
    warnings = []
    if request.extra_body:
        warnings.append("Unverified extra_body fields forwarded; provider support is not guaranteed")
    if request.resolution and request.resolution not in caps["resolutions"]:
        raise ValueError(f"{profile} supports resolutions {caps['resolutions']}; no silent downgrade")
    if request.duration is not None and request.duration != -1 and not 4 <= request.duration <= caps["max_duration"]:
        raise ValueError(f"{profile} duration must be -1 or 4..{caps['max_duration']}; no splitting or truncation")
    if profile != "2.5" and (request.omni_reference_task_type is not None or request.output_format is not None):
        raise ValueError("omni_reference_task_type and output_format are 2.5-only; for 2.0 express edit/extend in prompt and omit these fields")
    if request.callback_url is not None and not web_url(request.callback_url):
        raise ValueError("callback_url requires an HTTP(S) URL without credentials or fragment")
    counts = {"image": 0, "video": 0, "audio": 0}
    image_roles = []
    known_audio_durations = []
    media_map = []
    has_unknown = False
    texts = []
    for index, item in enumerate(body["content"]):
        if item["type"] == "text":
            if not item["text"].strip():
                raise ValueError("Text content cannot be whitespace")
            texts.append(item["text"])
            continue
        kind = item["type"].removesuffix("_url")
        counts[kind] += 1
        uri, seconds, checked = normalize_media(item[item["type"]]["url"], kind, local.allow_local_files)
        item[item["type"]]["url"] = uri
        has_unknown |= not checked
        if seconds is not None:
            if not 2 <= seconds <= caps["max_duration"]:
                raise ValueError(f"{profile} reference audio must be 2..{caps['max_duration']} seconds")
            known_audio_durations.append(seconds)
        if kind == "image":
            image_roles.append(item.get("role", "first_frame"))
        media_map.append({"content_index": index, "label": {"image": "图片", "video": "视频", "audio": "音频"}[kind] + str(counts[kind]), "role": item.get("role", "first_frame"), "metadata_checked_locally": checked})
    if not texts and not sum(counts.values()):
        raise ValueError("Provide text or media content")
    if any(counts[kind] > caps[plural] for kind, plural in [("image", "images"), ("video", "videos"), ("audio", "audios")]) or sum(counts.values()) > caps["total"]:
        raise ValueError(f"{profile} reference count exceeds per-modality or combined limit; consult seedance_capabilities")
    if sum(known_audio_durations) > caps["max_duration"] + 0.001:
        raise ValueError(f"Total known reference audio duration exceeds {caps['max_duration']} seconds")
    frames = any(role != "reference_image" for role in image_roles)
    omni = counts["video"] or counts["audio"] or "reference_image" in image_roles
    if frames:
        if omni or sorted(image_roles) not in (["first_frame"], ["first_frame", "last_frame"]):
            raise ValueError("Use one first frame OR one first+last frame pair; never mix frame roles with omni reference images/video/audio")
        if len(image_roles) == 2 and any(item["type"] == "image_url" and "role" not in item for item in body["content"]):
            raise ValueError("Both image roles must be explicit in first+last frame mode")
        if profile == "2.5" and request.ratio not in (None, "adaptive"):
            raise ValueError("2.5 first/last-frame mode requires adaptive ratio (or omitted official default)")
    if profile != "2.5" and counts["audio"] and not (counts["image"] or counts["video"]):
        raise ValueError("2.0 series cannot use audio-only reference; add a reference image/video or explicitly select 2.5")
    task_type = request.omni_reference_task_type
    if task_type is not None and not omni:
        raise ValueError("omni_reference_task_type requires omni-reference media, not text-only or first/last-frame mode")
    if task_type in ("edit", "extend"):
        if not counts["video"]:
            raise ValueError("edit/extend requires at least one reference_video")
        if request.ratio not in (None, "adaptive"):
            raise ValueError("2.5 edit/extend requires adaptive ratio")
        if task_type == "edit" and request.duration not in (None, -1):
            raise ValueError("2.5 edit requires duration=-1 (or omitted official default)")
    if has_unknown:
        warnings.append(f"URL/asset media bytes are not fetched locally. Ark must validate format/dimensions/FPS, per-video duration and total video/audio durations <= {caps['max_duration']}s. Edit input videos must be 4..30s; other videos/audio >=2s. Asset permissions and human-reference policies still apply.")
    if profile == "2.5" and omni:
        warnings.append("2.5 still classifies task intent asynchronously; auto may resolve to edit/extend, and explicit intent can fail TaskTypeMismatch. Preview is not provider acceptance.")
    if request.generate_audio is None:
        warnings.append("generate_audio omitted: official model default applies; explicitly set true when sound is required")
    if len(json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")) > MAX_BODY_BYTES:
        raise ValueError("Encoded request exceeds 64 MiB; use URL/asset references")
    return body, warnings, media_map


def video_preview(body):
    if isinstance(body, dict):
        return {k: ("[redacted]" if re.search(r"key|token|secret|authorization", k, re.I) else video_preview(v)) for k, v in body.items()}
    if isinstance(body, list):
        return [video_preview(v) for v in body]
    if isinstance(body, str):
        if body.startswith("data:"):
            return f"[media data omitted; {len(body)} characters]"
        if web_url(body):
            return "[media/callback URL omitted]"
    return body


def video_capabilities() -> dict:
    config = configuration_status()
    return {"verified_on": "2026-09-11", "default_model": get_config("SEEDANCE_MODEL", DEFAULT_VIDEO_MODEL), "configured": config["variables"]["ARK_API_KEY"]["configured"], "configuration": config,
            "profiles": PROFILES, "aliases": ALIASES,
            "endpoint": "POST /api/v3/contents/generations/tasks", "get_endpoint": "GET /api/v3/contents/generations/tasks/{id}",
            "list_endpoint": "GET /api/v3/contents/generations/tasks", "download_tool": "seedance_download_task(task_id, dest)",
            "batch_query_tool": "seedance_get_tasks(task_ids)", "creation_preflight": "mandatory offline validation, no automatic paid retries",
            "all_models": ["text-to-video", "first-frame", "first+last-frame", "omni image/video/audio references", "native synchronized audio", "last-frame output", "web_search"],
            "2.5_only_fields": ["omni_reference_task_type", "output_format"], "unsupported_fields": sorted(UNSUPPORTED),
            "media_limits": {"image": "300..6000px per side; ratio 0.4..2.5; <30MB", "video": "mp4/mov; 24..60fps; 300..6000px; ratio 0.4..2.5; area 407696..8295044px; <=200MB; URL/asset only", "audio": "mp3/wav <=15MB; >=2s each; total duration <= model max_duration", "body": "<=64MB", "total_video_duration": "<= model max_duration; >=2s each, 2.5 edit >=4s each", "audio_only_reference": "2.5 only"},
            "defaults": "Omitted fields stay omitted; no rewriting, audio muting, model fallback, splitting, retiming, TTS, or local post-processing",
            "result": "Native provider task/usage/URLs; query tasks within 7 days, download URLs expire in 24h (2.5 max 100 downloads). Use seedance_download_task for explicit downloads; no automatic download.",
            "request_schema": VideoRequest.model_json_schema(), "local_options_schema": VideoLocalOptions.model_json_schema(), "sources": SOURCES,
            "verification_note": "Official documentation + offline tests. Account access, quotas, asset permission and real generation quality require an authorized live call."}


def safe_tag(value) -> str:
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", value) else "unavailable"


class SeedanceClient:
    def __init__(self, *, api_key: str | None = None, base_url: str | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self.api_key = api_key if api_key is not None else get_config("ARK_API_KEY")
        self.base_url = (base_url if base_url is not None else get_config("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")).rstrip("/")
        p = urlsplit(self.base_url)
        if not web_url(self.base_url) or p.scheme != "https" or p.query:
            raise ValueError("ARK_BASE_URL must be HTTPS without credentials, query or fragment")
        self.transport = transport

    async def call(self, method: str, path: str, body: dict | None = None) -> dict:
        if not self.api_key.strip():
            raise TaskError("ARK_API_KEY is not configured; no API request was sent", stage='configuration')
        try:
            timeout = float(get_config("SEEDANCE_TIMEOUT_SECONDS", "60"))
            if not 1 <= timeout <= 300:
                raise ValueError
        except ValueError:
            raise ValueError("SEEDANCE_TIMEOUT_SECONDS must be 1..300") from None
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=timeout, follow_redirects=False) as client:
                response = await client.request(method, self.base_url + path, json=body,
                                                headers={"Authorization": f"Bearer {self.api_key}"})
        except httpx.TransportError:
            state = "Task creation may have been accepted and charged; check Ark task history before resubmitting." if method == "POST" else "Query failed; this did not create a new task."
            raise TaskError("Network failure. " + state + " No automatic retry.", stage='transport', sent=method == 'POST') from None
        request_id = safe_tag(response.headers.get("x-request-id") or response.headers.get("x-tt-logid"))
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError
        except ValueError:
            raise TaskError(f"Unexpected provider response (HTTP {response.status_code}); no retry. A create task may already exist; check Ark task history.", stage='response', sent=method == 'POST', request_id=request_id) from None
        if response.status_code >= 300 or (method == "POST" and payload.get("error")):
            err = payload.get("error")
            code = safe_tag(err.get("code") if isinstance(err, dict) else None)
            raise TaskError(f"Ark HTTP {response.status_code}; code={code}; no retry. Check task history before repeating creation.", stage='provider', sent=method == 'POST', request_id=request_id)
        # Task failures are data (HTTP 200), not transport failures. Avoid echoing arbitrary provider error text.
        if payload.get("error"):
            error = payload["error"]
            payload["error"] = {"code": safe_tag(error.get("code") if isinstance(error, dict) else None), "message": "Provider task failed; consult Ark with task ID for full diagnostics (raw message omitted to protect media URLs and credentials)."}
        return {"task": payload, "request_id": request_id}

    async def create(self, request: VideoRequest, local: VideoLocalOptions) -> dict:
        try:
            body, warnings, media_map = prepare_video(request, local, get_config("SEEDANCE_MODEL", DEFAULT_VIDEO_MODEL))
        except ValueError as exc:
            raise TaskError(str(exc), stage='preflight') from None
        receipt = {'valid': True, 'paid_request_sent': False,
            'request_sha256': hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            'body': video_preview(body), 'warnings': warnings, 'media_map': media_map}
        result = await self.call("POST", TASK_PATH, body)
        if not isinstance(result["task"].get("id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", result["task"]["id"]):
            raise TaskError("Provider response has no valid task ID. Creation may have succeeded; inspect Ark task history. No retry.", stage='response', sent=True)
        return {**result, 'preflight': receipt, 'paid_request_sent': True, **observation(result['task']), "warnings": warnings, "media_map": media_map, "next_step": "Query seedance_get_task/seedance_get_tasks; list history with seedance_list_tasks. On success use seedance_download_task. Never recreate to poll."}

    async def get(self, task_id: str) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", task_id):
            raise ValueError("Invalid task_id")
        result = await self.call("GET", TASK_PATH + "/" + task_id)
        if result["task"].get("id") != task_id or result["task"].get("status") not in {"queued", "running", "succeeded", "failed", "cancelled", "expired"}:
            raise SeedreamError("Unexpected task ID/status in query response; do not infer success")
        return {**result, **observation(result['task']), 'paid_request_sent': False,
            "result_note": "Native video_url/last_frame_url expire in 24h; use seedance_download_task(task_id, dest) to save exact bytes. No automatic downloads or transformations."}

    async def list_tasks(self, page_num=1, page_size=20, status=None, task_ids=None, model=None):
        if not 1 <= page_num <= 500 or not 1 <= page_size <= 500:
            raise ValueError('page_num and page_size must be 1..500')
        if status is not None and status not in {'queued', 'running', 'cancelled', 'succeeded', 'failed'}:
            raise ValueError('Unsupported list status')
        pairs = [('page_num', page_num), ('page_size', page_size)]
        if status:
            pairs.append(('filter.status', status))
        if model:
            if not re.fullmatch(r'[A-Za-z0-9_.-]{1,160}', model):
                raise ValueError('Invalid model/endpoint filter')
            pairs.append(('filter.model', model))
        if task_ids is not None:
            self.check_ids(task_ids)
            pairs.extend(('filter.task_ids', i) for i in task_ids)
        result = await self.call('GET', TASK_PATH + '?' + urlencode(pairs))
        payload = result['task']
        if not isinstance(payload.get('items'), list) or any(not isinstance(t, dict) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', str(t.get('id', ''))) for t in payload['items']):
            raise TaskError('Unexpected task-list response', stage='response')
        items = [{**safe_task(t), 'observation': observation(t)} for t in payload['items']]
        return {'items': items, 'total': payload.get('total'), 'page_num': page_num, 'page_size': page_size,
            'request_id': result['request_id'], 'paid_request_sent': False, 'scope': 'Provider task history, subject to provider retention.'}

    @staticmethod
    def check_ids(task_ids):
        if not isinstance(task_ids, list) or not 1 <= len(task_ids) <= 100 or any(not isinstance(i, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', i) for i in task_ids):
            raise ValueError('task_ids requires 1..100 valid IDs')

    async def get_tasks(self, task_ids):
        self.check_ids(task_ids)
        semaphore = asyncio.Semaphore(4)
        async def one(task_id):
            async with semaphore:
                try:
                    return {'task_id': task_id, 'ok': True, **await self.get(task_id)}
                except SeedreamError as exc:
                    return {'task_id': task_id, 'ok': False, 'error': str(exc), 'paid_request_sent': False}
        items = await asyncio.gather(*(one(i) for i in dict.fromkeys(task_ids)))
        return {'items': items, 'paid_request_sent': False, 'failed_queries': sum(not i['ok'] for i in items)}

    async def download_task(self, task_id, dest, output='video', max_bytes=2*1024**3, retries=2):
        if output not in {'video', 'last_frame'}:
            raise ValueError('output must be video or last_frame')
        target = Path(dest).expanduser()
        if not target.is_absolute() or target.exists():
            raise ValueError('dest must be a new absolute file path')
        task = (await self.get(task_id))['task']
        if task['status'] != 'succeeded':
            raise SeedreamError('Task is not succeeded; no download or replacement generation performed')
        url = (task.get('content') or {}).get(output + '_url')
        if not isinstance(url, str) or not web_url(url):
            raise SeedreamError('Task has no valid requested output URL')
        return {'task_id': task_id, 'output': output, **await download(url, dest, transport=self.transport, max_bytes=max_bytes, retries=retries)}
