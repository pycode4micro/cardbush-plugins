"""Allowlisted configuration: process (even empty) > current Windows user > default.

No .env discovery, factory config imports, registry writes, or secret logging.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from urllib.parse import urlsplit

ALIASES = {f"TENCENT_COS_{key}": f"COS_{key}" for key in
           ("SECRET_ID", "SECRET_KEY", "TOKEN", "BUCKET", "REGION")}
NAMES = frozenset(ALIASES) | frozenset(ALIASES.values()) | {
    "TENCENT_COS_PREFIX", "TENCENT_COS_PUBLIC_BASE_URL",
    "TENCENT_COS_USE_PRESIGNED_URL", "TENCENT_COS_PRESIGNED_EXPIRES_SECONDS",
    "COS_UPLOAD_ALLOWED_ROOT", "COS_UPLOAD_TIMEOUT_SECONDS",
}


class ConfigError(ValueError):
    pass


def read_user(name: str) -> str | None:
    if name not in NAMES:
        raise ConfigError("Unknown configuration name")
    if sys.platform != "win32":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ) as key:
            value, kind = winreg.QueryValueEx(key, name)
        if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) and isinstance(value, str):
            return value  # Literal values, never expand secrets.
    except (OSError, ImportError):
        pass
    return None


def resolve(name: str, default: str = "") -> str:
    if name not in NAMES:
        raise ConfigError("Unknown configuration name")
    names = (name, ALIASES[name]) if name in ALIASES else (name,)
    for candidate in names:
        if candidate in os.environ:
            return os.environ[candidate]
    if os.environ.get("COS_UPLOAD_READ_USER_ENV", "1").lower().strip() in {"1", "true", "yes", "on"}:
        for candidate in names:
            value = read_user(candidate)
            if value is not None:
                return value
    return default


def clean_prefix(value: str) -> str:
    result = value.strip().replace("\\", "/").strip("/")
    if any(ord(c) < 32 or ord(c) == 127 for c in result) or len(result.encode("utf-8")) > 512:
        raise ConfigError("Object prefix is too long or contains control characters")
    if result and any(part in {"", ".", ".."} for part in result.split("/")):
        raise ConfigError("Object prefix cannot contain empty, dot or parent segments")
    return result


def integer(name: str, default: str, low: int, high: int) -> int:
    try:
        value = int(resolve(name, default))
    except ValueError:
        raise ConfigError(f"{name} must be an integer") from None
    if not low <= value <= high:
        raise ConfigError(f"{name} is outside the supported range")
    return value


@dataclass(frozen=True)
class Settings:
    secret_id: str = field(repr=False)
    secret_key: str = field(repr=False)
    token: str = field(repr=False)
    bucket: str
    region: str
    prefix: str
    presign: bool
    expires_seconds: int
    public_base_url: str
    allowed_root: str
    timeout: int

    @classmethod
    def load(cls) -> "Settings":
        required = {k: resolve(f"TENCENT_COS_{k}").strip() for k in
                    ("SECRET_ID", "SECRET_KEY", "BUCKET", "REGION")}
        missing = [f"TENCENT_COS_{key}" for key, value in required.items() if not value]
        if missing:
            raise ConfigError("Missing configuration: " + ", ".join(missing))
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*-\d+", required["BUCKET"]):
            raise ConfigError("TENCENT_COS_BUCKET must be bucketname-appid")
        if not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)+", required["REGION"]):
            raise ConfigError("TENCENT_COS_REGION must be a COS region identifier")
        flag = resolve("TENCENT_COS_USE_PRESIGNED_URL", "true").strip().lower()
        if flag not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
            raise ConfigError("TENCENT_COS_USE_PRESIGNED_URL must be true or false")
        base = resolve("TENCENT_COS_PUBLIC_BASE_URL").strip().rstrip("/")
        if base:
            try:
                parts = urlsplit(base)
                valid = (parts.scheme == "https" and parts.hostname and not parts.username
                         and not parts.password and not parts.query and not parts.fragment
                         and not any(c.isspace() for c in base))
            except ValueError:
                valid = False
            if not valid:
                raise ConfigError("TENCENT_COS_PUBLIC_BASE_URL must be HTTPS without credentials, query or fragment")
        return cls(
            secret_id=required["SECRET_ID"], secret_key=required["SECRET_KEY"],
            token=resolve("TENCENT_COS_TOKEN").strip(), bucket=required["BUCKET"], region=required["REGION"],
            prefix=clean_prefix(resolve("TENCENT_COS_PREFIX", "reference-videos")),
            presign=flag in {"1", "true", "yes", "on"},
            expires_seconds=integer("TENCENT_COS_PRESIGNED_EXPIRES_SECONDS", "86400", 60, 604800),
            public_base_url=base, allowed_root=resolve("COS_UPLOAD_ALLOWED_ROOT"),
            timeout=integer("COS_UPLOAD_TIMEOUT_SECONDS", "120", 5, 1800),
        )
