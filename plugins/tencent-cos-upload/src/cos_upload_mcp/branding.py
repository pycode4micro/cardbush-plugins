"""Bundled MCP icons. No external URL requests or machine-specific file paths."""
from base64 import b64encode
from importlib.resources import files

from mcp.types import Icon


def plugin_icons() -> list[Icon]:
    data = files("cos_upload_mcp").joinpath("assets", "icon.png").read_bytes()
    return [Icon(src="data:image/png;base64," + b64encode(data).decode("ascii"),
                 mimeType="image/png", sizes=["128x128"])]
