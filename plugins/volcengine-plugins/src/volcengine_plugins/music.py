"""Volcengine music v5.0: signed OpenAPI calls, task queries and exact downloads."""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import httpx
from pydantic import ValidationError

from .client import SeedreamError
from .config import configuration_status, get_config
from .music_models import BGMRequest, BillingMode, MUSIC_MODEL, SongRequest
from .task_io import TaskError, download

HOST = "open.volcengineapi.com"
API_VERSION = "2024-08-12"
REGION = "cn-beijing"
SERVICE = "imagination"
SOURCES = {name: f"https://www.volcengine.com/docs/84992/{doc}" for name, doc in {
    "song": "2091679", "bgm": "2100970", "query": "2100960", "auth": "1967910",
    "options": "2097647", "pricing": "1404661", "lyrics": "2100963", "errors": "1404675",
}.items()}
ACTIONS = {"song": {"postpaid": "GenSongForTime", "prepaid": "GenSongV4"},
           "bgm": {"postpaid": "GenBGMForTime", "prepaid": "GenBGM"}}
ERROR_HINTS = {
    100001: "Provider internal error.", 100010: "Provider rejected request parameters.",
    100011: "Provider restricts overseas source IPs.", 200020: "Invalid AK/SK signature.",
    200021: "Music authorization expired.", 200022: "Resource package exhausted.",
    200023: "Provider QPS limit reached.", 200024: "Music service is disabled for this account.",
    200026: "Invalid TOS bucket.", 200027: "Resource package expired.",
    200028: "No available resource package; check the selected billing mode.",
    300030: "Provider algorithm error.", 300052: "Task not found.",
    300061: "Input lyrics failed copyright checks.", 300062: "Output lyrics failed copyright checks.",
    300063: "Input lyrics failed content checks.", 300064: "Output lyrics failed content checks.",
    300065: "Provider requires Chinese input.", 300066: "Unsupported input language.",
    300067: "Provider semantic processing failed.", 400040: "Provider queue is full.",
    50000001: "Provider copyright check failed.",
}


def music_request(value, kind):
    model = {"song": SongRequest, "bgm": BGMRequest}[kind]
    try:
        return model.model_validate(value)
    except ValidationError as exc:
        # Never echo rejected lyrics, prompts, callback URLs or credential-like fields.
        errors = [{"field": ".".join(map(str, e["loc"])), "type": e["type"]}
                  for e in exc.errors()]
        raise TaskError(f"Invalid music {kind} request fields: {errors}. See music_capabilities; only v5.0 is supported.",
                        stage="preflight") from None


def prepare_music(request: SongRequest | BGMRequest, billing_mode: BillingMode = "postpaid"):
    kind = "song" if isinstance(request, SongRequest) else "bgm"
    if billing_mode not in ACTIONS[kind]:
        raise TaskError("billing_mode must be postpaid or prepaid", stage="preflight")
    return ACTIONS[kind][billing_mode], request.model_dump(exclude_none=True)


def music_preview(request: SongRequest | BGMRequest, billing_mode: BillingMode = "postpaid") -> dict:
    action, body = prepare_music(request, billing_mode)
    for field in ("CallbackURL", "TosBucket", "ImplicitWaterMark"):
        if field in body:
            body[field] = "[redacted]"
    warnings = [
        "v5.0 is sent explicitly. No older-model, alternate billing-mode or automatic paid retry fallback.",
        "Preview does not verify account permissions or music service activation.",
        "Pricing lists standard songs/BGM, without a separate v5.0 rate; the reference below is not a confirmed v5.0 quote.",
    ]
    seconds = request.Duration
    if isinstance(request, BGMRequest):
        if request.Segments:
            seconds = sum(s.Duration for s in request.Segments)
        warnings.append("BGM duration precedence: sum(Segments.Duration) > duration written in Text > outer Duration. Billing uses actual output seconds.")
    else:
        warnings.append("v5.0 ignores legacy Genre/Mood/Gender/Timbre and related style fields; this wrapper rejects them. Use Prompt for the creative description.")
    return {"model_version": MUSIC_MODEL, "method": "POST", "host": HOST,
            "query": {"Action": action, "Version": API_VERSION}, "body": body,
            "billing_mode": billing_mode, "paid_request_sent": False, "warnings": warnings,
            "billing_reference": {"currency": "CNY", "published_per_second": 0.002,
                "scope": "Published standard song/BGM postpaid tariff; not a separately verified v5.0 price",
                "duration_basis_seconds": seconds,
                "reference_amount": round(seconds * 0.002, 6) if seconds is not None and billing_mode == "postpaid" else None,
                "confirmed_v5_price": False, "source": SOURCES["pricing"],
                "note": "Prepaid and postpaid services are independent. No package auto-deduction/fallback. Actual generated duration controls postpaid billing."}}


def music_capabilities() -> dict:
    return {"model_version": MUSIC_MODEL, "supported_model_versions": [MUSIC_MODEL],
            "verified_date": "2026-09-18", "host": HOST, "api_version": API_VERSION,
            "region": REGION, "service": SERVICE, "default_billing_mode": "postpaid",
            "actions": ACTIONS, "query_action": "QuerySong",
            "configured": bool(get_config("VOLCENGINE_ACCESS_KEY_ID").strip()
                               and get_config("VOLCENGINE_SECRET_ACCESS_KEY").strip()),
            "configuration": configuration_status(),
            "schemas": {"song": SongRequest.model_json_schema(), "bgm": BGMRequest.model_json_schema()},
            "limits": {"song_seconds": [30, 240], "bgm_seconds": [30, 120],
                       "bgm_segment_seconds": [5, 120], "bgm_segment_total_seconds": [30, 120],
                       "languages": ["Chinese", "English", "Cantonese"],
                       "song_requested_formats": ["wav", "mp3"], "published_qps": 2},
            "sources": SOURCES,
            "notes": [
                "Uses VOLCENGINE_ACCESS_KEY_ID + VOLCENGINE_SECRET_ACCESS_KEY; optional VOLCENGINE_SESSION_TOKEN. Ark and MediaKit keys are not accepted.",
                "Vocal songs send ModelVersion=v5.0; BGM sends body Version=v5.0. The query API Version remains 2024-08-12.",
                "GenSongV4 is the official PREPAID action name even for ModelVersion=v5.0; it is not a model downgrade.",
                "BGM v5.0 supports 30..120 seconds; the pricing page still mentions the older 60-second limit.",
                "Song: at least Lyrics or Prompt; both allowed for v5.0. Prompt/Lyrics are preserved, with no local rewriting or splitting.",
                "The separate GenLyrics API currently documents only v4.0 and requires prepaid entitlement. It is intentionally not exposed by this v5.0 integration.",
                "QuerySong returns status 0=queued, 1=running, 2=succeeded, 3=failed. Code=0 only means the query succeeded.",
                "Lyrics, Captions and v5.0 StyleInfo are returned when supplied. Captions/StyleInfo keep the provider's native representation; no guessed LRC/SRT conversion.",
                "The query API warns that audio URLs can contain MP4 despite a WAV request. Downloads preserve bytes and report the observed format; no automatic conversion.",
                "Official result URLs are for transfer to your own storage, not application hotlinking. Download promptly; documented validity is one year.",
            ]}


def signed_request(action: str, body: dict, access_key: str, secret_key: str,
                   session_token: str = "", *, now: datetime | None = None):
    """Sign the exact bytes sent, following the official HMAC-SHA256 specification."""
    data = json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    query = urlencode(sorted({"Action": action, "Version": API_VERSION}.items()))
    date = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    digest = hashlib.sha256(data).hexdigest()
    headers = {"content-type": "application/json", "host": HOST,
               "x-content-sha256": digest, "x-date": date}
    if session_token:
        headers["x-security-token"] = session_token
    names = sorted(headers)
    signed_headers = ";".join(names)
    canonical = "\n".join(["POST", "/", query,
                            "\n".join(f"{name}:{headers[name]}" for name in names), "",
                            signed_headers, digest])
    scope = f"{date[:8]}/{REGION}/{SERVICE}/request"
    signing = "\n".join(["HMAC-SHA256", date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = secret_key.encode("utf-8")
    for part in (date[:8], REGION, SERVICE, "request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, signing.encode(), hashlib.sha256).hexdigest()
    headers["authorization"] = f"HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed_headers}, Signature={signature}"
    return f"https://{HOST}/?{query}", headers, data


def _tag(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value) else None


def _task_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise TaskError("task_id must be a provider task ID (1..128 letters, digits, underscore or hyphen)", stage="preflight")
    return value


def _new_path(value):
    path = Path(value).expanduser()
    if not path.is_absolute() or path.exists() or path.is_symlink():
        raise TaskError("Output paths must be absolute new files; no overwrite", stage="preflight")
    return path


def _write_metadata(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.part")
    try:
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        os.link(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _audio_info(path):
    # Header/container inspection only; this does not claim playback or quality QC.
    import mutagen
    try:
        with path.open("rb") as stream:
            head = stream.read(16)
        fmt = ("wav" if head[:4] == b"RIFF" and head[8:12] == b"WAVE" else
               "mp4" if head[4:8] == b"ftyp" else "flac" if head[:4] == b"fLaC" else
               "ogg" if head[:4] == b"OggS" else "mp3" if head[:3] == b"ID3" or
               (len(head) >= 2 and head[0] == 255 and head[1] & 224 == 224) else "unknown")
        media = mutagen.File(path)
        info = getattr(media, "info", None)
        return {"detected_format": fmt, "duration_seconds": getattr(info, "length", None),
                "sample_rate": getattr(info, "sample_rate", None), "channels": getattr(info, "channels", None),
                "header_readable": info is not None, "playback_qc_performed": False}
    except Exception:
        return {"detected_format": "unknown", "header_readable": False, "playback_qc_performed": False}


class MusicClient:
    def __init__(self, *, access_key=None, secret_key=None, session_token=None, timeout=None, transport=None):
        self.access_key = get_config("VOLCENGINE_ACCESS_KEY_ID") if access_key is None else access_key
        self.secret_key = get_config("VOLCENGINE_SECRET_ACCESS_KEY") if secret_key is None else secret_key
        self.session_token = get_config("VOLCENGINE_SESSION_TOKEN") if session_token is None else session_token
        self.timeout = get_config("VOLCENGINE_MUSIC_TIMEOUT_SECONDS", "60") if timeout is None else timeout
        self.transport = transport

    async def _request(self, action, body, *, paid=False):
        if not self.access_key.strip() or not self.secret_key.strip():
            raise TaskError("Configure VOLCENGINE_ACCESS_KEY_ID and VOLCENGINE_SECRET_ACCESS_KEY and enable the selected music billing service. Ark/MediaKit keys are not music credentials.", stage="preflight")
        if (not re.fullmatch(r"[A-Za-z0-9_-]+", self.access_key)
                or any(ord(c) < 33 or ord(c) > 126 for c in self.secret_key + self.session_token)):
            raise TaskError("Invalid music credential format; values are omitted", stage="preflight")
        try:
            timeout = float(self.timeout)
            if not math.isfinite(timeout) or not 1 <= timeout <= 300:
                raise ValueError
        except (ValueError, TypeError):
            raise TaskError("VOLCENGINE_MUSIC_TIMEOUT_SECONDS must be 1..300", stage="preflight") from None
        url, headers, data = signed_request(action, body, self.access_key, self.secret_key, self.session_token)
        stage = "submission" if paid else "query"
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=httpx.Timeout(timeout, connect=min(timeout, 20)),
                                        follow_redirects=False) as client:
                response = await client.post(url, content=data, headers=headers)
        except httpx.HTTPError:
            raise TaskError("Music request transport failure; submission outcome may be uncertain. No automatic retry. Query an existing TaskID or inspect provider task history before resubmitting.",
                            stage=stage, sent=paid) from None
        try:
            payload = response.json()
        except (ValueError, UnicodeError):
            raise TaskError(f"Music returned a non-JSON response (HTTP {response.status_code}); no automatic retry.", stage=stage, sent=paid) from None
        if not isinstance(payload, dict):
            raise TaskError("Invalid music response envelope", stage=stage, sent=paid)
        metadata = payload.get("ResponseMetadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        request_id = _tag(metadata.get("RequestId"))
        code = payload.get("Code")
        if response.status_code != 200 or type(code) is not int or code != 0 or metadata.get("Error"):
            hint = ERROR_HINTS.get(code, "Provider rejected the request; inspect the music console.") if type(code) is int else "Provider returned an invalid/error envelope."
            safe_code = code if type(code) is int else "unavailable"
            raise TaskError(f"Music API error (HTTP {response.status_code}, code {safe_code}). {hint} No automatic retry; provider error text omitted.",
                            stage=stage, sent=paid, request_id=request_id)
        if not isinstance(payload.get("Result"), dict):
            raise TaskError("Music response has no Result; submission outcome may be uncertain. No automatic retry.",
                            stage=stage, sent=paid, request_id=request_id)
        return payload["Result"], request_id

    async def create(self, request: SongRequest | BGMRequest, billing_mode: BillingMode = "postpaid"):
        action, body = prepare_music(request, billing_mode)
        result, request_id = await self._request(action, body, paid=True)
        try:
            task_id = _task_id(result.get("TaskID"))
        except TaskError:
            raise TaskError("Music response lacks a valid TaskID; submission outcome uncertain. Do not automatically recreate the task.",
                            stage="submission", sent=True, request_id=request_id) from None
        eta = result.get("PredictedWaitTime")
        return {"task": {"id": task_id, "predicted_wait_seconds": eta if type(eta) in (int, float) and math.isfinite(eta) and eta >= 0 else None},
                "model_version": MUSIC_MODEL, "action": action, "billing_mode": billing_mode,
                "request_id": request_id, "paid_request_sent": True, "automatic_retry": False,
                "billing": {"charged": None, "status": "not reported by creation API"},
                "next_step": "Query this task.id with music_get_task; creation is not completion. Never recreate to poll."}

    async def get(self, task_id: str):
        task_id = _task_id(task_id)
        result, request_id = await self._request("QuerySong", {"TaskID": task_id})
        if result.get("TaskID") != task_id:
            raise TaskError("Music response TaskID is missing or does not match the requested task", stage="query", request_id=request_id)
        raw_status = result.get("Status")
        status = {0: "queued", 1: "running", 2: "succeeded", 3: "failed"}.get(raw_status, "unknown") if type(raw_status) is int else "unknown"
        progress = result.get("Progress")
        progress = progress if type(progress) in (int, float) and math.isfinite(progress) and 0 <= progress <= 100 else None
        detail = result.get("SongDetail")
        song = None
        if isinstance(detail, dict):
            song = {target: detail.get(source) for source, target in {
                "AudioUrl": "audio_url", "Lyrics": "lyrics", "Captions": "captions",
                "Duration": "duration_seconds", "StyleInfo": "style_info", "Lang": "language",
            }.items()}
        failure = result.get("FailureReason")
        failure_code = failure.get("Code") if isinstance(failure, dict) else None
        failure_code = failure_code if type(failure_code) is int else None
        return {"task": {"id": task_id, "status": status,
                         "native_status": raw_status if type(raw_status) is int else None,
                         "progress": progress, "song": song,
                         "failure": {"code": failure_code, "message": ERROR_HINTS.get(failure_code, "Provider task failure; raw text omitted.")} if status == "failed" else None},
                "request_id": request_id, "paid_request_sent": False,
                "suggested_poll_after_seconds": 10 if status in {"queued", "running"} else None,
                "billing": {"charged": None, "status": "not reported by query API"}}

    async def download_task(self, task_id: str, dest: str, *, metadata_dest: str | None = None,
                            max_bytes: int = 512 * 1024**2, retries: int = 2, download_transport=None):
        target = _new_path(dest)
        sidecar = _new_path(metadata_dest) if metadata_dest is not None else None
        if sidecar is not None and target.resolve() == sidecar.resolve():
            raise TaskError("Audio and metadata output paths must differ", stage="preflight")
        if type(max_bytes) is not int or not 1 <= max_bytes <= 8 * 1024**3 or type(retries) is not int or not 0 <= retries <= 3:
            raise TaskError("max_bytes must be 1..8 GiB; download retries must be 0..3", stage="preflight")
        task = (await self.get(task_id))["task"]
        if task["status"] != "succeeded":
            raise TaskError("Music task is not succeeded; query the same TaskID. No file downloaded.", stage="download")
        song = task["song"]
        if not song or not isinstance(song.get("audio_url"), str):
            raise TaskError("Succeeded task has no audio URL", stage="download")
        try:
            saved = await download(song["audio_url"], str(target), transport=download_transport, max_bytes=max_bytes, retries=retries)
        except (ValueError, SeedreamError, OSError):
            raise TaskError("Music download failed; no new generation was requested. Check the destination and query the same task for its output URL.", stage="download") from None
        saved["audio"] = _audio_info(target)
        fmt = saved["audio"]["detected_format"]
        suffixes = {"wav": {".wav"}, "mp3": {".mp3"}, "mp4": {".mp4", ".m4a"}, "ogg": {".ogg"}, "flac": {".flac"}}
        saved["warnings"] = []
        if fmt == "unknown" or not saved["audio"]["header_readable"]:
            saved["warnings"].append("Audio header could not be verified; inspect the saved file before use.")
        elif target.suffix.lower() not in suffixes.get(fmt, set()):
            saved["warnings"].append(f"Saved bytes are {fmt}, which differs from the requested filename suffix; no conversion was performed.")
        saved["task_id"] = task_id
        if sidecar is not None:
            metadata = {"task_id": task_id, "audio": saved["audio"],
                        **{key: value for key, value in song.items() if key != "audio_url"}}
            try:
                _write_metadata(sidecar, metadata)
                saved["metadata_path"] = str(sidecar)
            except (OSError, ValueError):
                saved["metadata_path"] = None
                saved["warnings"].append("Audio was saved, but metadata could not be written. Keep the audio and use music_get_task to retrieve lyrics/captions; do not regenerate.")
        return saved
