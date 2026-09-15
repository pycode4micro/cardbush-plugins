"""Portable source-tree entry point; runtime lives entirely in video_editer."""
import sys
from video_editer import mcp_server

if __name__ == "__main__":
    mcp_server.main()
else:
    # Compatibility for local callers that previously imported server directly.
    sys.modules[__name__] = mcp_server
