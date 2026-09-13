from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    app_env: Literal["dev", "test", "prod"] = "dev"
    app_host: str = "127.0.0.1"
    app_port: int = Field(default=8090, ge=1, le=65535)
    local_api_key: str = Field(min_length=16)
    mcp_host: str = "127.0.0.1"
    mcp_port: int = Field(default=8091, ge=1, le=65535)
    mcp_operator_id: str = Field(default="mcp-agent", min_length=1, max_length=64)

    database_url: str = "sqlite:///./data/qianchuan_tool_service.db"

    qianchuan_api_base_url: str = "https://api.oceanengine.com/open_api"
    outbound_http_trust_env: bool = False
    qianchuan_access_token: str | None = None
    qianchuan_request_timeout_seconds: int = Field(default=30, ge=1)
    qianchuan_allowed_advertiser_ids: Annotated[list[str], NoDecode] = Field(
        default_factory=list
    )
    # Explicit production opt-in for all accounts returned by the selected OAuth token.
    # Account Policy remains independently required for write operations.
    qianchuan_allow_all_authorized_advertisers: bool = False
    qianchuan_write_enabled: bool = False
    qianchuan_default_target_roi: Decimal = Field(default=Decimal("8"), gt=Decimal("0"), le=Decimal("100"))
    qianchuan_default_min_spend: Decimal = Field(default=Decimal("100"), ge=Decimal("0"))
    qianchuan_default_min_clicks: int = Field(default=30, ge=0)
    qianchuan_default_min_orders: int = Field(default=1, ge=0)
    qianchuan_write_cooldown_minutes: int = Field(default=360, ge=0)
    qianchuan_roi_write_cooldown_minutes: int = Field(default=5, ge=0)
    qianchuan_status_write_cooldown_minutes: int = Field(default=5, ge=0)
    qianchuan_require_account_policy_for_writes: bool = True
    qianchuan_mcp_admin_tools_enabled: bool = False
    qianchuan_video_upload_root: str = "./data/video_uploads"
    qianchuan_video_upload_max_mb: int = Field(default=500, ge=1, le=2048)
    qianchuan_image_upload_root: str = "./data/image_uploads"
    qianchuan_image_upload_max_mb: float = Field(default=1.5, gt=0, le=1.5)

    qianchuan_autonomous_enabled: bool = False
    qianchuan_autonomous_dry_run: bool = True
    qianchuan_autonomous_advertiser_id: str | None = None
    qianchuan_autonomous_operator_id: str = Field(
        default="autonomous-budget",
        min_length=1,
        max_length=64,
    )
    qianchuan_autonomous_lookback_days: int = Field(default=3, ge=1, le=30)
    qianchuan_autonomous_max_actions_per_run: int = Field(default=3, ge=1, le=10)
    qianchuan_autonomous_max_actions_per_day: int = Field(default=6, ge=1, le=100)
    qianchuan_autonomous_increase_rate: Decimal = Field(
        default=Decimal("0.10"),
        gt=Decimal("0"),
        le=Decimal("0.20"),
    )
    qianchuan_autonomous_decrease_rate: Decimal = Field(
        default=Decimal("0.10"),
        gt=Decimal("0"),
        le=Decimal("0.20"),
    )
    qianchuan_autonomous_min_budget: Decimal = Field(default=Decimal("100"), gt=Decimal("0"))
    qianchuan_autonomous_roi_safety_margin_rate: Decimal = Field(
        default=Decimal("0.10"),
        ge=Decimal("0"),
        le=Decimal("1"),
    )
    qianchuan_autonomous_kill_switch_file: str = "./data/AUTONOMY_STOP"

    oauth_gateway_base_url: str | None = None
    oauth_gateway_token_no: str | None = None
    oauth_selection_file: str = "./data/oauth_selection.json"
    oauth_gateway_hmac_secret: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("qianchuan_api_base_url", "oauth_gateway_base_url", mode="after")
    @classmethod
    def strip_trailing_slash(cls, value: str | None) -> str | None:
        return value.rstrip("/") if isinstance(value, str) else value

    @field_validator("qianchuan_allowed_advertiser_ids", mode="before")
    @classmethod
    def parse_advertiser_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, (list, tuple, set)):
            return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
        return value

    def validate_for_startup(self) -> None:
        if self.app_env == "prod" and self.app_host != "127.0.0.1":
            raise ValueError("In prod, keep APP_HOST=127.0.0.1 and expose through a private proxy only.")
        if self.app_env == "prod" and self.mcp_host != "127.0.0.1":
            raise ValueError("In prod, keep MCP_HOST=127.0.0.1 and expose through a private proxy only.")
        has_direct_token = bool(self.qianchuan_access_token)
        has_gateway = bool(self.oauth_gateway_base_url and self.oauth_gateway_hmac_secret)
        if not has_direct_token and not has_gateway:
            raise ValueError("Configure QIANCHUAN_ACCESS_TOKEN or OAuth gateway base URL/HMAC secret.")
        if (
            self.app_env == "prod"
            and not self.qianchuan_allow_all_authorized_advertisers
            and not self.qianchuan_allowed_advertiser_ids
        ):
            raise ValueError("QIANCHUAN_ALLOWED_ADVERTISER_IDS is required in prod.")
        if any(not item.isdigit() for item in self.qianchuan_allowed_advertiser_ids):
            raise ValueError("QIANCHUAN_ALLOWED_ADVERTISER_IDS must contain numeric IDs.")
        self.validate_autonomous_settings()

    def validate_autonomous_settings(self) -> None:
        if not self.qianchuan_autonomous_enabled:
            return
        advertiser_id = (self.qianchuan_autonomous_advertiser_id or "").strip()
        if not advertiser_id or not advertiser_id.isdigit():
            raise ValueError(
                "QIANCHUAN_AUTONOMOUS_ADVERTISER_ID is required and must be numeric."
            )
        if (
            not self.qianchuan_allow_all_authorized_advertisers
            and advertiser_id not in set(self.qianchuan_allowed_advertiser_ids)
        ):
            raise ValueError(
                "QIANCHUAN_AUTONOMOUS_ADVERTISER_ID must be in "
                "QIANCHUAN_ALLOWED_ADVERTISER_IDS."
            )
        if self.qianchuan_autonomous_dry_run:
            return
        if not self.qianchuan_write_enabled:
            raise ValueError("Live autonomy requires QIANCHUAN_WRITE_ENABLED=true.")


@lru_cache
def get_settings() -> Settings:
    return Settings()
