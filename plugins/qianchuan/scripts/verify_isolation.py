"""No official API calls, credentials, worker launch or real database writes."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="qianchuan-isolation-") as tmp:
    data = Path(tmp) / "plugin-data"
    env = dict(os.environ, QIANCHUAN_PLUGIN_DATA_DIR=str(data),
               QIANCHUAN_SERVICE_ROOT="Z:/nonexistent-development-checkout",
               DATABASE_URL="sqlite:///Z:/must-not-be-used.db",
               QIANCHUAN_WRITE_ENABLED="true", LOCAL_API_KEY="host-key-must-not-be-used",
               QIANCHUAN_ACCESS_TOKEN="host-token-must-not-be-used")
    command = ["uv", "run", "--frozen", "--script", str(root / "scripts" / "launch.py"), "--check"]
    def run():
        result = subprocess.run(command, cwd=tmp, env=env, check=True, capture_output=True, text=True)
        return json.loads(result.stdout)
    result = run()
    assert result["status"] == "setup_required", result
    data.mkdir(exist_ok=True)
    # Synthetic local config only, never a production credential.
    (data / "config.env").write_text("LOCAL_API_KEY=isolated-test-key-0123456789\nQIANCHUAN_ACCESS_TOKEN=test-only-not-used\n", encoding="utf-8")
    result = run()
    assert result["status"] == "configured", result
    assert Path(result["runtime_module"]).is_relative_to(root / "runtime")
    assert result["write_enabled"] is False
    assert "must-not-be-used" not in result["database"]
    assert "plugin-data" in result["database"]
    print(json.dumps({"isolation": "passed", "no_config": "setup_required",
                      "host_environment_ignored": True, "own_runtime_loaded": True}))
