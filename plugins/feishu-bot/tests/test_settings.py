import json

import pytest

from feishu_bot.settings import ConfigurationError, Settings, load_settings, parser


def load(args, env, home):
    return load_settings(parser().parse_args(args), env, home)


def test_default_configuration_and_no_project_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("FEISHU_ALLOW_WRITE=true\nFEISHU_APP_SECRET=project-secret", encoding="utf-8")
    settings = load([], {}, tmp_path)
    assert settings.allow_read and not settings.allow_write
    assert not settings.app_secret


@pytest.mark.parametrize("read,write", [(True, False), (True, True), (False, True), (False, False)])
def test_environment_matrix(read, write, tmp_path):
    settings = load([], {"FEISHU_ALLOW_READ": str(read), "FEISHU_ALLOW_WRITE": str(write)}, tmp_path)
    assert (settings.allow_read, settings.allow_write) == (read, write)


@pytest.mark.parametrize(
    "args,env,expected",
    [
        (["--allow-write"], {"FEISHU_ALLOW_WRITE": "false"}, (True, True)),
        (["--no-allow-write"], {"FEISHU_ALLOW_WRITE": "true"}, (True, False)),
        (["--no-allow-read"], {"FEISHU_ALLOW_READ": "true"}, (False, False)),
        (["--allow-read"], {"FEISHU_ALLOW_READ": "false"}, (True, False)),
        (["--no-allow-write"], {"FEISHU_ALLOW_WRITE": "invalid"}, (True, False)),
    ],
)
def test_startup_flags_override_environment(args, env, expected, tmp_path):
    settings = load(args, env, tmp_path)
    assert (settings.allow_read, settings.allow_write) == expected


def test_external_file_lower_priority_and_no_interpolation(tmp_path):
    env_file = tmp_path / "bot.env"
    env_file.write_text(
        "FEISHU_ALLOW_WRITE=true\nFEISHU_ALLOW_READ=false\nFEISHU_APP_SECRET=${OTHER_SECRET}\n", encoding="utf-8-sig"
    )
    settings = load(["--env-file", str(env_file), "--allow-read"], {"FEISHU_ALLOW_WRITE": "false"}, tmp_path)
    assert settings.allow_read and not settings.allow_write
    assert settings.app_secret == "${OTHER_SECRET}"
    assert settings.config_loaded


def test_named_default_config(tmp_path):
    path = tmp_path / ".config/feishu-bot/.env"
    path.parent.mkdir(parents=True)
    path.write_text("FEISHU_ALLOW_WRITE=on", encoding="utf-8")
    assert load([], {}, tmp_path).allow_write


@pytest.mark.parametrize(
    "env",
    [
        {"FEISHU_ALLOW_WRITE": "maybe"},
        {"FEISHU_ALLOW_READ": ""},
        {"FEISHU_API_BASE_URL": "https://attacker.example"},
        {"FEISHU_TIMEOUT_SECONDS": "NaN"},
        {"FEISHU_TIMEOUT_SECONDS": "0"},
        {"FEISHU_ENV_FILE": "missing-file.env"},
    ],
)
def test_invalid_configuration_fails_closed(env, tmp_path):
    with pytest.raises(ConfigurationError):
        load([], env, tmp_path)


def test_status_and_repr_never_expose_credentials():
    settings = Settings(
        app_secret="secret-app",
        tenant_access_token="secret-token",
        webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/secret-hook",
        webhook_secret="secret-sign",
    )
    output = repr(settings) + json.dumps(settings.status())
    assert all(secret not in output for secret in ("secret-app", "secret-token", "secret-hook", "secret-sign"))


@pytest.mark.parametrize(
    "url",
    [
        "http://open.feishu.cn/open-apis/bot/v2/hook/token",
        "https://open.feishu.cn.evil.example/open-apis/bot/v2/hook/token",
        "https://evil@open.feishu.cn/open-apis/bot/v2/hook/token",
        "https://open.feishu.cn/open-apis/bot/v2/hook/token?redirect=other",
    ],
)
def test_webhook_destination_validation(url):
    with pytest.raises(ConfigurationError):
        Settings(webhook_url=url)
