import json
import os
import shutil
import sys
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from feishu_bot.client import FeishuClient
from feishu_bot.server import create_server
from feishu_bot.settings import Settings

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "read,write,expected_count", [(True, False, 14), (True, True, 28), (False, False, 1), (False, True, 14)]
)
async def test_tool_registration_and_annotations(read, write, expected_count):
    settings = Settings(allow_read=read, allow_write=write)
    client = FeishuClient(settings)
    try:
        server = create_server(settings, client=client)
        tools = {t.name: t for t in await server.list_tools()}
        assert len(tools) == expected_count
        assert ("feishu_sheet_write" in tools) == write
        assert ("feishu_sheet_read" in tools) == read
        assert ("feishu_sheet_route_append" in tools) == (read and write)
        if read:
            assert tools["feishu_bitable_records_search"].annotations.read_only_hint is True
        if write:
            assert tools["feishu_sheet_delete"].annotations.destructive_hint is True
            assert tools["feishu_sheet_append"].annotations.idempotent_hint is False
        assert tools["feishu_status"].annotations.open_world_hint is False
        for tool in tools.values():
            assert "allow_write" not in tool.input_schema.get("properties", {})
    finally:
        await client.close()


async def test_tool_error_is_structured_and_redacted():
    settings = Settings(tenant_access_token="private-token")
    client = FeishuClient(
        settings,
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"code": 123, "msg": "invalid private-token"})
        ),
    )
    try:
        server = create_server(settings, client=client)
        result = await server.call_tool("feishu_bot_info", {})
        assert result.is_error
        assert result.structured_content["error"]["code"] == 123
        assert "private-token" not in result.model_dump_json()
    finally:
        await client.close()


def clean_environment(env_file):
    env = {k: v for k, v in os.environ.items() if not k.startswith("FEISHU_")}
    env.update(FEISHU_ENV_FILE=str(env_file), PYTHONUTF8="1")
    return env


@pytest.mark.parametrize(
    "flags,env_write,expected_write",
    [([], "false", False), (["--allow-write"], "false", True), (["--no-allow-write"], "true", False)],
)
async def test_real_stdio_handshake_and_flag_precedence(tmp_path, flags, env_write, expected_write):
    env_file = tmp_path / "empty.env"
    env_file.write_text("", encoding="utf-8")
    env = clean_environment(env_file)
    env["FEISHU_ALLOW_WRITE"] = env_write
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "feishu_bot", *flags], env=env, cwd=str(tmp_path)
    )
    async with (
        stdio_client(params) as (reader, writer),
        ClientSession(reader, writer, read_timeout_seconds=30) as session,
    ):
        initialized = await session.initialize()
        assert initialized.server_info.name == "feishu-bot"
        tools = (await session.list_tools()).tools
        assert ("feishu_message_send" in {t.name for t in tools}) == expected_write
        result = await session.call_tool("feishu_status", {})
        assert not result.is_error
        data = result.structured_content or json.loads(result.content[0].text)
        assert data["allow_write"] == expected_write
        if not expected_write:
            blocked = await session.call_tool(
                "feishu_message_send", {"receive_id": "oc_test", "content": {"text": "do not send"}}
            )
            assert blocked.is_error


async def test_relocated_portable_manifest_starts_without_original_project(tmp_path):
    if not shutil.which("uv"):
        pytest.skip("uv is required for the portable launcher integration test")
    plugin = tmp_path / "relocated plugin" / "feishu-bot"
    shutil.copytree(
        ROOT,
        plugin,
        ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "dist", "*.egg-info"),
    )
    data_dir = tmp_path / "plugin data"
    data_dir.mkdir()
    env_file = tmp_path / "empty.env"
    env_file.write_text("", encoding="utf-8")
    env = clean_environment(env_file)
    env.pop("PYTHONPATH", None)
    server = json.loads((plugin / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]["feishu"]

    def expand(value):
        return value.replace("${PLUGIN_ROOT}", str(plugin)).replace("${PLUGIN_DATA}", str(data_dir))

    env.update({k: expand(v) for k, v in server["env"].items()})
    args = [expand(value) for value in server["args"]]
    params = StdioServerParameters(command=server["command"], args=args, env=env, cwd=str(tmp_path))
    async with (
        stdio_client(params) as (reader, writer),
        ClientSession(reader, writer, read_timeout_seconds=90) as session,
    ):
        await session.initialize()
        result = await session.call_tool("feishu_status", {})
        assert not result.is_error
        data = result.structured_content or json.loads(result.content[0].text)
        assert data["allow_read"] and not data["allow_write"]
    assert (data_dir / "venv/pyvenv.cfg").is_file()
    assert not (plugin / ".venv").exists()


def test_manifest_consistency_and_no_fixed_machine_paths():
    portable = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    legacy = json.loads((ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    assert portable["name"] == ROOT.name == legacy["name"] == "feishu-bot"
    assert portable["version"] == legacy["version"] == "1.0.0"
    assert portable["extensions"]["com.openai"]["interface"] == legacy["interface"]
    for name in ("mcp.json", ".mcp.json"):
        text = (ROOT / name).read_text(encoding="utf-8")
        server = json.loads(text)["mcpServers"]["feishu"]
        assert not any(flag in server["args"] for flag in ("--allow-write", "--no-allow-read"))
        assert "C:/Users/" not in text and "zjfs_data_platform" not in text
