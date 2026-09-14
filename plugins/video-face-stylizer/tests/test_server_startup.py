"""A fresh interpreter must initialize settings without hiding compatibility warnings."""
import os
import subprocess
import sys
from video_face_stylizer.processes import hidden_process_options


def test_first_server_startup_resolves_settings_before_reading_sources():
    code = '''
import asyncio
from mcp.server.fastmcp.server import Settings
from video_face_stylizer.server import create_server
server = create_server()
assert Settings.__pydantic_complete__
settings_from_env = Settings(**{k: v for k, v in server.settings.model_dump().items() if k != "log_level"})
assert settings_from_env.log_level == "WARNING"
assert server.settings.lifespan is None
names = {tool.name for tool in asyncio.run(server.list_tools())}
assert names == {"video_face_capabilities", "render_video_cpu", "render_video_gpu", "get_video_job", "cancel_video_job"}
create_server()
print("fresh startup and tool schemas passed")
'''
    env = {**os.environ, "FASTMCP_LOG_LEVEL": "WARNING", "PYTHONUTF8": "1"}
    result = subprocess.run([sys.executable, "-W", "error", "-c", code],
                            env=env, capture_output=True, text=True, timeout=30, **hidden_process_options())
    assert result.returncode == 0, result.stderr
    assert result.stderr == "", result.stderr
    assert "fresh startup and tool schemas passed" in result.stdout
