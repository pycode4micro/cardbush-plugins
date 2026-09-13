import asyncio
import json
import os
import sys
from types import SimpleNamespace

import httpx
import pytest

from seedream_mcp import config
from seedream_mcp.client import SeedreamClient, capabilities
from seedream_mcp.models import ImageRequest, LocalOptions
from seedream_mcp.video_client import SeedanceClient, video_capabilities
from seedream_mcp.video_models import VideoRequest, VideoLocalOptions

REAL_USER_READER = config._read_windows_user


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch):
    for name in config.CONFIG_NAMES | {config.USER_ENV_SWITCH}:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "_read_windows_user", lambda name: config.ConfigValue(None, "windows_user_missing"))


def user_values(monkeypatch, values):
    monkeypatch.setattr(config, "_read_windows_user", lambda name: config.ConfigValue(values.get(name), "windows_user" if name in values else "windows_user_missing"))


@pytest.mark.parametrize("value", ["client-secret", "", "   "])
def test_process_including_empty_always_wins(monkeypatch, value):
    user_values(monkeypatch, {"ARK_API_KEY": "user-secret"})
    monkeypatch.setenv("ARK_API_KEY", value)
    assert config.get_config("ARK_API_KEY") == value
    assert config.resolve_config("ARK_API_KEY").source == "process"


def test_user_fallback_is_live_and_not_copied(monkeypatch):
    values = {"ARK_API_KEY": "first-secret"}
    user_values(monkeypatch, values)
    assert config.get_config("ARK_API_KEY") == "first-secret"
    values["ARK_API_KEY"] = "rotated-secret"
    assert config.get_config("ARK_API_KEY") == "rotated-secret"
    del values["ARK_API_KEY"]
    assert config.get_config("ARK_API_KEY", "default") == "default"
    assert "ARK_API_KEY" not in os.environ


@pytest.mark.parametrize("switch", ["0", "false", "off", "no", "", "typo"])
def test_disable_does_not_even_read_registry(monkeypatch, switch):
    monkeypatch.setenv(config.USER_ENV_SWITCH, switch)
    monkeypatch.setattr(config, "_read_windows_user", lambda name: pytest.fail("registry should not be read"))
    assert config.get_config("ARK_API_KEY") == ""
    assert config.configuration_status()["variables"]["ARK_API_KEY"]["source"] == "windows_user_disabled"


def test_all_config_names_and_unknown_name(monkeypatch):
    values = {name: "test-value" for name in config.CONFIG_NAMES}
    user_values(monkeypatch, values)
    for name in config.CONFIG_NAMES:
        assert config.get_config(name) == "test-value"
    with pytest.raises(ValueError, match="allowlist"):
        config.get_config("UNRELATED_SECRET")
    with pytest.raises(ValueError, match="allowlist"):
        REAL_USER_READER("UNRELATED_SECRET")


def test_status_and_repr_never_show_values(monkeypatch):
    user_values(monkeypatch, {"ARK_API_KEY": "secret-never-display", "ARK_BASE_URL": "https://private-endpoint.example"})
    status = config.configuration_status()
    assert status["variables"]["ARK_API_KEY"] == {"configured": True, "source": "windows_user"}
    encoded = json.dumps(status) + repr(config.resolve_config("ARK_API_KEY"))
    assert "secret-never-display" not in encoded and "private-endpoint" not in encoded
    for cap in [capabilities(), video_capabilities()]:
        assert cap["configured"] is True
        assert cap["configuration"]["variables"]["ARK_API_KEY"]["source"] == "windows_user"
        assert "secret-never-display" not in json.dumps(cap)


def test_non_windows_does_not_import_winreg(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert REAL_USER_READER("ARK_API_KEY").source == "not_windows"


def fake_registry(monkeypatch, value="test-value", value_type=1, error=None):
    accesses = []
    class Key:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
    def open_key(root, path, reserved, access):
        accesses.append((root, path, reserved, access))
        if error:
            raise error
        return Key()
    fake = SimpleNamespace(HKEY_CURRENT_USER=100, KEY_READ=200, REG_SZ=1, REG_EXPAND_SZ=2,
                           OpenKey=open_key, QueryValueEx=lambda key, name: (value, value_type))
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(sys, "platform", "win32")
    return accesses


def test_registry_read_only_exact_current_user_key(monkeypatch):
    accesses = fake_registry(monkeypatch)
    result = REAL_USER_READER("ARK_API_KEY")
    assert result.value == "test-value" and result.source == "windows_user"
    assert accesses == [(100, "Environment", 0, 200)]


@pytest.mark.parametrize("error,source", [(FileNotFoundError("secret"), "windows_user_missing"), (PermissionError("secret"), "windows_user_unavailable"), (OSError("secret"), "windows_user_unavailable")])
def test_registry_errors_safe(monkeypatch, error, source):
    fake_registry(monkeypatch, error=error)
    result = REAL_USER_READER("ARK_API_KEY")
    assert result.value is None and result.source == source
    assert "secret" not in repr(result)


@pytest.mark.parametrize("value,value_type", [(123, 4), (["a"], 7), (b"secret", 3)])
def test_registry_rejects_non_strings(monkeypatch, value, value_type):
    fake_registry(monkeypatch, value, value_type)
    assert REAL_USER_READER("ARK_API_KEY").source == "windows_user_invalid_type"


def test_expand_paths_but_not_keys(monkeypatch):
    fake_registry(monkeypatch, "%SEEDREAM_TEST_DIR%/output", 2)
    monkeypatch.setenv("SEEDREAM_TEST_DIR", "C:/example")
    assert REAL_USER_READER("SEEDREAM_OUTPUT_DIR").value == "C:/example/output"
    assert REAL_USER_READER("ARK_API_KEY").value == "%SEEDREAM_TEST_DIR%/output"


def test_both_clients_use_resolved_credentials_and_config(monkeypatch, tmp_path):
    user_values(monkeypatch, {"ARK_API_KEY": "registry-test-secret", "ARK_BASE_URL": "https://ark.example/api/v3",
        "SEEDREAM_OUTPUT_DIR": str(tmp_path), "SEEDREAM_TIMEOUT_SECONDS": "21", "SEEDANCE_TIMEOUT_SECONDS": "22",
        "SEEDREAM_MODEL": "image-test-model", "SEEDANCE_MODEL": "2.0-fast"})
    seen = []
    def handler(req):
        seen.append(req)
        assert req.headers["authorization"] == "Bearer registry-test-secret"
        assert req.url.host == "ark.example"
        payload = json.loads(req.content)
        if "images/generations" in req.url.path:
            assert payload["model"] == "image-test-model"
            assert req.extensions["timeout"]["read"] == 21
            return httpx.Response(200, json={"data": [{"url": "https://result.example/a.png"}]})
        assert payload["model"] == "doubao-seedance-2-0-fast-260128"
        assert req.extensions["timeout"]["read"] == 22
        return httpx.Response(200, json={"id": "cgt-test"})
    async def run():
        image_client = SeedreamClient(transport=httpx.MockTransport(handler))
        assert image_client.output_dir == tmp_path
        await image_client.generate(ImageRequest(prompt="test", response_format="url"), LocalOptions())
        await SeedanceClient(transport=httpx.MockTransport(handler)).create(VideoRequest(content=[{"type": "text", "text": "test"}]), VideoLocalOptions())
    asyncio.run(run())
    assert len(seen) == 2
    for client in [SeedreamClient(api_key=""), SeedanceClient(api_key="")]:
        assert client.api_key == ""
    assert "ARK_API_KEY" not in os.environ
