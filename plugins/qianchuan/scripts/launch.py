# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "fastapi>=0.115,<1.0", "uvicorn[standard]>=0.34,<1.0",
#   "httpx>=0.28,<1.0", "sqlalchemy>=2.0,<3.0",
#   "pydantic-settings>=2.6,<3.0", "python-dotenv>=1.0,<2.0",
#   "mcp[cli]>=1.27,<2",
# ]
# ///
"""Self-contained plugin entrypoint; never imports a desktop development checkout."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def isolate():
    base = os.environ.get("QIANCHUAN_PLUGIN_DATA_DIR")
    if not base:
        base = str(Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share"))) / "qianchuan-plugin")
    data = Path(base).expanduser().resolve()
    data.mkdir(parents=True, exist_ok=True)
    # Only the plugin's own config is authoritative. Do not inherit a host project's
    # database, OAuth secrets, write switches, Python path or upload roots.
    prefixes = ("QIANCHUAN_", "OAUTH_", "APP_", "MCP_")
    exact = {"LOCAL_API_KEY", "DATABASE_URL", "OUTBOUND_HTTP_TRUST_ENV", "PYTHONPATH", "PYTHONHOME"}
    for key in list(os.environ):
        if key.upper().startswith(prefixes) or key.upper() in exact:
            os.environ.pop(key, None)
    os.environ["QIANCHUAN_PLUGIN_DATA_DIR"] = str(data)
    os.chdir(data)
    sys.path.insert(0, str(ROOT / "runtime"))
    return data


def load_settings(data):
    from app.config import Settings
    # Explicit locations cannot be redirected into the development service via config.
    settings = Settings(_env_file=data / "config.env",
        database_url=f"sqlite:///{(data / 'data' / 'qianchuan.db').as_posix()}",
        oauth_selection_file=str(data / "data" / "oauth_selection.json"),
        qianchuan_image_upload_root=str(data / "materials" / "images"),
        qianchuan_video_upload_root=str(data / "materials" / "videos"),
        qianchuan_autonomous_kill_switch_file=str(data / "AUTONOMY_STOP"))
    settings.validate_for_startup()
    return settings


def run_setup(data, missing):
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations
    server = FastMCP("Qianchuan Plugin Setup")
    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    def qianchuan_setup_status() -> dict[str, Any]:
        """Show missing plugin configuration. No credentials are read from another service.

        Configure the indicated file locally; do not paste secrets into chat. Reconnect
        this plugin after configuration to load business tools. No local REST required.
        """
        return {"status": "setup_required", "configuration_file": str(data / "config.env"),
                "missing_fields": missing, "message": "请仅在该文件配置网关地址/HMAC及本地安全参数，完成后重新连接插件。无需桌面投流服务。",
                "example_file": str(ROOT / "config.example.env"), "restart_required": True}
    server.run()


def main():
    data = isolate()
    from pydantic import ValidationError
    try:
        settings = load_settings(data)
    except ValidationError as exc:
        missing = sorted({str(e["loc"][0]) for e in exc.errors()})
        if "--check" in sys.argv:
            import json
            print(json.dumps({"status": "setup_required", "missing_fields": missing,
                              "runtime": str(ROOT / "runtime"), "data": str(data)}))
            return
        if "--worker" in sys.argv:
            raise SystemExit("Plugin worker configuration invalid")
        run_setup(data, missing)
        return
    except ValueError:
        if "--check" in sys.argv:
            import json
            print(json.dumps({"status": "setup_required", "runtime": str(ROOT / "runtime"), "data": str(data)}))
            return
        if "--worker" in sys.argv:
            raise SystemExit("Plugin worker configuration invalid")
        run_setup(data, ["gateway_or_direct_token", "startup_safety_configuration"])
        return
    # Both entrypoints consume the same isolated Settings object.
    import app.config
    app.config.get_settings = lambda: settings
    if "--check" in sys.argv:
        import json
        import app.mcp_server
        print(json.dumps({"status": "configured", "runtime_module": app.mcp_server.__file__,
                          "database": settings.database_url, "write_enabled": settings.qianchuan_write_enabled,
                          "data": str(data)}))
        return
    for folder in (data / "materials" / "images", data / "materials" / "videos"):
        folder.mkdir(parents=True, exist_ok=True)
    if "--worker" in sys.argv:
        from app.deadline_worker import main as worker_main
        worker_main()
        return
    from app.db import build_engine, init_db, create_session_factory
    from app.deadline import worker_status
    engine = build_engine(settings)
    init_db(engine)
    online = worker_status(create_session_factory(engine))["online"]
    engine.dispose()
    if not online:
        options = {"cwd": str(data), "stdin": subprocess.DEVNULL,
                   "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
        else:
            options["start_new_session"] = True
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--worker"], **options)
    from app.mcp_server import main as server_main
    server_main()


if __name__ == "__main__":
    main()
