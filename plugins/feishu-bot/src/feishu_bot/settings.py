"""Explicit, process-local configuration. Never discovers another project's .env."""

from __future__ import annotations

import argparse
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from dotenv import dotenv_values

Access = Literal["read", "write"]
API_ORIGINS = ("https://open.feishu.cn", "https://open.larksuite.com")


class ConfigurationError(ValueError):
    pass


class CapabilityDisabled(PermissionError):
    pass


def boolean(value: str | bool, name: str) -> bool:
    if isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} 必须为 true/false、1/0、yes/no 或 on/off。")


@dataclass(frozen=True)
class Settings:
    allow_read: bool = True
    allow_write: bool = False
    app_id: str = field(default="", repr=False)
    app_secret: str = field(default="", repr=False)
    tenant_access_token: str = field(default="", repr=False)
    webhook_url: str = field(default="", repr=False)
    webhook_secret: str = field(default="", repr=False)
    api_base_url: str = "https://open.feishu.cn"
    timeout_seconds: float = 30.0
    config_loaded: bool = False

    def __post_init__(self) -> None:
        if type(self.allow_read) is not bool or type(self.allow_write) is not bool:
            raise ConfigurationError("能力开关必须为布尔值。")
        if self.api_base_url not in API_ORIGINS:
            raise ConfigurationError("FEISHU_API_BASE_URL 只支持飞书或 Lark 官方 HTTPS 地址。")
        if not 1 <= self.timeout_seconds <= 120:
            raise ConfigurationError("FEISHU_TIMEOUT_SECONDS 必须在 1 到 120 之间。")
        if self.webhook_url:
            url = urlsplit(self.webhook_url)
            if (
                f"{url.scheme}://{url.netloc}" not in API_ORIGINS
                or not url.path.startswith("/open-apis/bot/v2/hook/")
                or not re.fullmatch(r"[A-Za-z0-9_-]+", url.path.removeprefix("/open-apis/bot/v2/hook/"))
                or url.query
                or url.fragment
            ):
                raise ConfigurationError("FEISHU_WEBHOOK_URL 必须是官方自定义机器人 webhook 地址。")

    def require(self, access: Access) -> None:
        if not (self.allow_read if access == "read" else self.allow_write):
            flag = "--allow-read" if access == "read" else "--allow-write"
            raise CapabilityDisabled(f"{access} 能力已关闭；由用户通过环境变量或启动参数 {flag} 开启后重启插件。")

    def status(self) -> dict:
        return {
            "allow_read": self.allow_read,
            "allow_write": self.allow_write,
            "application_credentials_configured": bool(self.app_id and self.app_secret),
            "static_token_configured": bool(self.tenant_access_token),
            "authentication_mode": "static_token" if self.tenant_access_token else "internal_app",
            "webhook_configured": bool(self.webhook_url),
            "webhook_signing_configured": bool(self.webhook_secret),
            "api_base_url": self.api_base_url,
            "external_config_loaded": self.config_loaded,
            "precedence": "startup arguments > process environment > external env file > defaults",
        }


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="独立飞书机器人 MCP 服务（stdio）")
    p.add_argument("--allow-read", action=argparse.BooleanOptionalAction, default=None)
    p.add_argument("--allow-write", action=argparse.BooleanOptionalAction, default=None)
    p.add_argument("--env-file", help="外部 .env 文件；不自动读取当前工作目录中的 .env")
    p.add_argument("--print-config", action="store_true", help="打印脱敏配置并退出，不联网")
    return p


def load_settings(
    args: argparse.Namespace, environ: Mapping[str, str] | None = None, home: Path | None = None
) -> Settings:
    process = dict(os.environ if environ is None else environ)
    explicit_path = args.env_file or process.get("FEISHU_ENV_FILE")
    path = Path(explicit_path).expanduser() if explicit_path else (home or Path.home()) / ".config/feishu-bot/.env"
    if explicit_path and not path.is_file():
        raise ConfigurationError("指定的 FEISHU_ENV_FILE / --env-file 不存在或不是文件。")
    values = dict(dotenv_values(path, interpolate=False, encoding="utf-8-sig")) if path.is_file() else {}
    values.update(process)

    def value(key: str, default: str = "") -> str:
        raw = values.get(key, default)
        if raw is None:
            raise ConfigurationError(f"{key} 缺少值。")
        return str(raw).strip()

    try:
        timeout = float(value("FEISHU_TIMEOUT_SECONDS", "30"))
    except ValueError:
        raise ConfigurationError("FEISHU_TIMEOUT_SECONDS 必须是数字。") from None
    return Settings(
        allow_read=args.allow_read
        if args.allow_read is not None
        else boolean(value("FEISHU_ALLOW_READ", "true"), "FEISHU_ALLOW_READ"),
        allow_write=args.allow_write
        if args.allow_write is not None
        else boolean(value("FEISHU_ALLOW_WRITE", "false"), "FEISHU_ALLOW_WRITE"),
        app_id=value("FEISHU_APP_ID"),
        app_secret=value("FEISHU_APP_SECRET"),
        tenant_access_token=value("FEISHU_TENANT_ACCESS_TOKEN"),
        webhook_url=value("FEISHU_WEBHOOK_URL"),
        webhook_secret=value("FEISHU_WEBHOOK_SECRET"),
        api_base_url=value("FEISHU_API_BASE_URL", "https://open.feishu.cn").rstrip("/"),
        timeout_seconds=timeout,
        config_loaded=path.is_file(),
    )
