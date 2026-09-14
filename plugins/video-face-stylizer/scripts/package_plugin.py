"""Build a clean portable ZIP, optionally bound to an installed local Python."""
from __future__ import annotations
import argparse,json,os,subprocess,sys,zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from video_face_stylizer.processes import hidden_process_options

EXCLUDE={'.venv','.tools','.python','__pycache__','.pytest_cache','.git','build','dist','validation'}

def source_files(root):
    for folder,directories,files in os.walk(root,followlinks=False):
        directories[:]=sorted(name for name in directories if name not in EXCLUDE and not name.endswith('.egg-info'))
        for name in directories:
            if (Path(folder)/name).is_symlink():raise ValueError('Plugin packages must not contain links.')
        for name in sorted(files):
            file=Path(folder)/name
            if file.is_symlink():raise ValueError('Plugin packages must not contain links.')
            yield file

def package(root:Path,output:Path,python:Path|None=None):
    root=root.resolve();output=output.resolve()
    if python:
        python=python.resolve()
        if not python.is_file():raise ValueError('Python executable does not exist.')
        subprocess.run([str(python),'-c','from video_face_stylizer.server import create_server; create_server()'],check=True,**hidden_process_options())
    output.parent.mkdir(parents=True,exist_ok=True)
    # Refuse to overwrite any prior deliverable.
    with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for file in source_files(root):
            relative=file.relative_to(root)
            if file.is_symlink():raise ValueError('Plugin packages must not contain links.')
            if not file.is_file() or file.resolve()==output:continue
            if any(p in EXCLUDE or p.endswith('.egg-info') for p in relative.parts):continue
            if file.suffix in {'.pyc','.pyo','.zip','.log'}:continue
            name=relative.as_posix()
            if name=='.runtime-ready.json':continue
            if python and name in {'mcp.json','.mcp.json'}:
                data=json.loads(file.read_text(encoding='utf-8'))
                for server in data['mcpServers'].values():
                    server['command']=str(python)
                    server['args']=['-m','video_face_stylizer']
                archive.writestr(name,json.dumps(data,ensure_ascii=False,indent=2)+'\n')
            else:archive.write(file,name)
        if python:
            archive.writestr('LOCAL_RUNTIME.txt',
                'This ZIP is configured for this computer. Keep this Python environment in place:\n'
                +str(python)+'\nFor another computer, extract the source package and run setup.cmd.\n')
    return output

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--python',type=Path)
    args=parser.parse_args()
    print(package(Path(__file__).resolve().parents[1],args.output,args.python))
