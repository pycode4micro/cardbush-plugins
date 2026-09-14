"""Console-free subprocesses for the plugin's non-interactive Windows runtime."""
from __future__ import annotations

import os
import subprocess
import sys
from importlib.resources import files
from pathlib import Path


def hidden_process_options(*, new_session: bool = False) -> dict:
    if sys.platform == 'win32':
        # SW_HIDE alone can still allocate a console (and activate Windows
        # Terminal). CREATE_NO_WINDOW prevents the console from being created.
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        return {'creationflags': subprocess.CREATE_NO_WINDOW, 'startupinfo': startup}
    return {'start_new_session': True} if new_session else {}


def ffmpeg_executable() -> str:
    override = os.environ.get('IMAGEIO_FFMPEG_EXE')
    if override:
        return override
    if sys.platform == 'win32':
        # The pinned imageio-ffmpeg wheel includes exactly one executable.
        # Its general-purpose auto-discovery runs a separate console probe;
        # setup already checks our bundled encoder, so use it directly.
        binaries = files('imageio_ffmpeg.binaries')
        candidates = [file for file in binaries.iterdir()
                      if file.name.startswith('ffmpeg-') and file.name.endswith('.exe') and file.is_file()]
        if len(candidates) != 1:
            raise RuntimeError('Bundled FFmpeg is missing or ambiguous. Run scripts/setup.ps1 to repair the runtime.')
        return str(Path(str(candidates[0])).resolve())
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()
