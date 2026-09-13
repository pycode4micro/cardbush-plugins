"""Local authorization selection; never persists bearer tokens or changes policy."""
import json
import os
import re
import tempfile
from pathlib import Path


def selected_token(settings):
    path = Path(settings.oauth_selection_file)
    if not path.exists():
        return settings.oauth_gateway_token_no
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("gateway") != settings.oauth_gateway_base_url:
            return settings.oauth_gateway_token_no
        token = data["token_no"]
        if not isinstance(token, str) or not re.fullmatch(r"qcot_[A-Za-z0-9_-]+", token):
            raise ValueError("Invalid token identifier")
        return token
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("Invalid OAuth selection file; restore or remove it before continuing.") from exc


def save_selection(settings, token_no):
    if not re.fullmatch(r"qcot_[A-Za-z0-9_-]+", token_no):
        raise ValueError("Invalid token identifier")
    path = Path(settings.oauth_selection_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".oauth-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"gateway": settings.oauth_gateway_base_url, "token_no": token_no}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
