"""Exercise the real Windows launcher without opening user-facing terminals."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from video_face_stylizer.processes import ffmpeg_executable,hidden_process_options


ROOT=Path(__file__).resolve().parents[1]
WINDOWS=sys.platform=='win32'


def ps_literal(value):
    return "'"+str(value).replace("'","''")+"'"


def run_script(tmp_path,body):
    script=tmp_path/'launcher with spaces.ps1'
    script.write_text("$ErrorActionPreference='Stop'\n. "+ps_literal(ROOT/'scripts/hidden_process.ps1')+'\n'+body,
                      encoding='utf-8-sig')
    return ['powershell.exe','-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(script)]


@pytest.mark.skipif(not WINDOWS or not shutil.which('powershell.exe'),reason='Windows PowerShell launcher')
def test_launcher_preserves_interactive_binary_stdio_and_eof(tmp_path):
    probe=tmp_path/'中文 input.py'
    probe.write_text('''import ctypes,json,sys
print(json.dumps({'console':ctypes.windll.kernel32.GetConsoleWindow(),'args':sys.argv[1:]}),flush=True)
sys.stderr.buffer.write(b'diagnostic\\n');sys.stderr.buffer.flush()
for line in sys.stdin.buffer:
    sys.stdout.buffer.write(line);sys.stdout.buffer.flush()
''',encoding='utf-8')
    arguments=[str(probe),'中文 path','quote"value','C:\\trailing\\','']
    command=run_script(tmp_path,'$code=Invoke-HiddenProcess -Program '+ps_literal(sys.executable)
        +' -Arguments @('+','.join(map(ps_literal,arguments))+') -ForwardInput\nexit $code')
    # UTF-8, embedded NUL and more than a pipe buffer must survive unchanged.
    payload=('中文 MCP\n\0\1'.encode('utf-8'))*16000
    with subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                          **hidden_process_options()) as child:
        # Wait for output before sending any input: the relay must not deadlock
        # by synchronously reading stdin before starting its output pumps.
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor() as executor:
            first=executor.submit(child.stdout.readline)
            try:
                data=json.loads(first.result(timeout=15))
                assert data=={'console':0,'args':arguments[1:]}
                child.stdin.write(b'ping before EOF\n');child.stdin.flush()
                assert executor.submit(child.stdout.readline).result(timeout=15)==b'ping before EOF\n'
                output,error=child.communicate(payload,timeout=15)
            except BaseException:
                child.kill();child.communicate(timeout=5)
                raise
    assert child.returncode==0,error
    assert output==payload
    assert error==b'diagnostic\n'


@pytest.mark.skipif(not WINDOWS or not shutil.which('powershell.exe'),reason='Windows PowerShell launcher')
@pytest.mark.parametrize('mode',['capture','diagnostics'])
def test_setup_commands_preserve_exit_code_and_stdout_channel(tmp_path,mode):
    probe=tmp_path/'status.py'
    probe.write_text("import sys;sys.stdout.buffer.write(b'output\\n');sys.stderr.buffer.write(b'warning\\n');sys.exit(7)",encoding='utf-8')
    invocation='Invoke-HiddenProcess -Program '+ps_literal(sys.executable)+' -Arguments @('+ps_literal(probe)+')'
    body=("$result="+invocation+' -CaptureOutput\n[Console]::Out.Write($result.Output)\nexit $result.ExitCode'
          if mode=='capture' else '$code='+invocation+' -OutputToError\nexit $code')
    result=subprocess.run(run_script(tmp_path,body),capture_output=True,timeout=20,**hidden_process_options())
    assert result.returncode==7,result.stderr
    if mode=='capture':
        assert result.stdout==b'output\n' and result.stderr==b'warning\n'
    else:
        assert result.stdout==b''
        assert sorted(result.stderr.splitlines())==[b'output',b'warning']


@pytest.mark.skipif(not WINDOWS,reason='Windows console policy')
def test_ffmpeg_discovery_does_not_spawn_a_probe(monkeypatch):
    monkeypatch.delenv('IMAGEIO_FFMPEG_EXE',raising=False)
    def unexpected(*args,**kwargs):raise AssertionError('Discovery must not launch an extra process')
    monkeypatch.setattr(subprocess,'Popen',unexpected)
    assert Path(ffmpeg_executable()).is_file()


def test_ffmpeg_explicit_override_is_respected(monkeypatch):
    monkeypatch.setenv('IMAGEIO_FFMPEG_EXE','custom ffmpeg.exe')
    assert ffmpeg_executable()=='custom ffmpeg.exe'


def test_bundled_ffmpeg_is_executable(monkeypatch):
    monkeypatch.delenv('IMAGEIO_FFMPEG_EXE',raising=False)
    result=subprocess.run([ffmpeg_executable(),'-version'],stdin=subprocess.DEVNULL,
        capture_output=True,timeout=15,**hidden_process_options())
    assert result.returncode==0,result.stderr
    assert result.stdout.startswith(b'ffmpeg version')
