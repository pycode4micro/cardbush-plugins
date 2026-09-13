"""Durable immutable render attempts with checksum-verified stage reuse."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import uuid

from . import engine, timing, processes
from .locking import file_lock


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            result.update(block)
    return result.hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def folder(project_id, job_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', job_id):
        raise ValueError('Invalid job_id')
    root = (engine.project_dir(project_id) / 'mcp_renders').resolve()
    path = (root / job_id).resolve()
    if path.parent != root or not (path / 'state.json').is_file():
        raise ValueError('Unknown render job')
    return path


def media_manifest(project):
    timeline = project['mcp_timeline']
    used = {c['asset_id'] for c in timeline['tracks']['main']}
    used.update(e['asset_id'] for _, e in timing.entries(timeline) if e.get('asset_id'))
    if timeline.get('audio', {}).get('bed_asset_id'):
        used.add(timeline['audio']['bed_asset_id'])
    assets = {a['id']: a for a in project['materials']}
    manifest = {}
    for asset_id in sorted(used):
        item = assets[asset_id]
        checksum = digest(item['path'])
        if item.get('identity') and item['identity']['sha256'] != checksum:
            raise ValueError(f'Source changed since indexing: {asset_id}; register a new asset and review its evidence')
        manifest[asset_id] = {'path': item['path'], 'sha256': checksum}
    return manifest


def verify_media(manifest):
    for asset_id, item in manifest.items():
        path = Path(item['path'])
        if not path.is_file() or digest(path) != item['sha256']:
            raise ValueError(f'Media changed or missing since the saved render: {asset_id}; start a new render explicitly')


def runtime_fingerprint():
    root = Path(__file__).parent
    value = hashlib.sha256()
    for path in sorted(root.glob('*.py')) + sorted((root / 'assets').glob('*.png')):
        value.update(path.name.encode())
        value.update(digest(path).encode())
    # Font changes affect typography, and a different FFmpeg may encode differently.
    import os
    value.update(os.environ.get('VIDEO_EDITER_FONT', 'Microsoft YaHei').encode())
    font=os.environ.get('VIDEO_EDITER_FONT_FILE')
    if font:
        value.update(str(font).encode())
        value.update(digest(font).encode())
    else:
        from .visuals import font_file
        try:
            value.update(digest(font_file()).encode())
        except ValueError:
            value.update(b'no-procedural-font')
    binary = Path(shutil.which(engine.ffmpeg_bin()) or engine.ffmpeg_bin()).resolve()
    value.update((str(binary) + str(binary.stat().st_size) + str(binary.stat().st_mtime_ns)).encode())
    return value.hexdigest()


class Attempt:
    def __init__(self, project_id, project, preview, prior=None):
        self.prior = prior
        revision = project['mcp_timeline']['revision']
        self.job_id = f"{'preview' if preview else 'final'}-r{revision}-{uuid.uuid4().hex[:12]}"
        self.root = engine.project_dir(project_id) / 'mcp_renders' / self.job_id
        self.root.mkdir(parents=True, exist_ok=False)
        self.project = project
        self.preview = preview
        self.state = {'job_id': self.job_id, 'project_id': project_id, 'revision': revision,
                      'preview': preview, 'status': 'queued', 'created_at': now(), 'stages': {},
                      'retry_of': prior.name if prior else None, 'runtime': runtime_fingerprint()}
        self.manifest = media_manifest(project)
        snapshot = {'project': project, 'preview': preview, 'media': self.manifest}
        engine.write_json(self.root / 'input.json', snapshot)
        self.state['input_sha256'] = digest(self.root / 'input.json')
        engine.write_json(self.root / 'timeline.json', project['mcp_timeline'])
        self.save()

    def save(self):
        self.state['updated_at'] = now()
        engine.write_json(self.root / 'state.json', self.state)

    @classmethod
    def load(cls, project_id, job_id):
        root=folder(project_id,job_id)
        state=engine.read_json(root/'state.json')
        if digest(root/'input.json')!=state['input_sha256']:
            raise ValueError('Saved render input checksum mismatch')
        snapshot=engine.read_json(root/'input.json')
        if state['runtime']!=runtime_fingerprint():
            raise ValueError('Runtime changed after submission; submit a new job explicitly')
        obj=cls.__new__(cls)
        obj.root,obj.state,obj.job_id=root,state,job_id
        obj.project,obj.preview,obj.manifest=snapshot['project'],snapshot['preview'],snapshot['media']
        obj.prior=folder(project_id,state['retry_of']) if state.get('retry_of') else None
        return obj

    def reuse(self, stage):
        if not self.prior:
            return None
        previous = engine.read_json(self.prior / 'state.json')
        if previous.get('runtime') != self.state['runtime']:
            return None
        record = previous.get('stages', {}).get(stage)
        if not record:
            return None
        source = (self.prior / record['path']).resolve()
        if not source.is_relative_to(self.prior.resolve()) or not source.is_file() or digest(source) != record['sha256']:
            return None
        target = self.root / f'reused-{stage}.mp4'
        shutil.copy2(source, target)
        self.checkpoint(stage, target, record.get('metadata', {}), reused=True)
        return target, record.get('metadata', {})

    def checkpoint(self, stage, path, metadata=None, reused=False):
        self.state['stages'][stage] = {'path': Path(path).relative_to(self.root).as_posix(),
                                       'sha256': digest(path), 'metadata': metadata or {}, 'reused': reused}
        self.save()

    def stage(self, name, execute):
        processes.check()
        self.state['current_stage'] = name
        self.save()
        reused = self.reuse(name)
        if reused:
            return reused
        path, metadata = execute()
        self.checkpoint(name, path, metadata)
        return path, metadata

    @contextmanager
    def running(self):
        with file_lock(self.root / 'running.lock'):
            self.state.update(status='running', started_at=now())
            self.save()
            try:
                yield self
            except BaseException as exc:
                self.state.update(status='cancelled' if isinstance(exc,processes.RenderCancelled) else 'failed', error={'type': type(exc).__name__, 'message': str(exc)[-4000:]}, finished_at=now())
                self.save()
                raise

    def succeed(self, result):
        engine.write_json(self.root / 'result.json', result)
        self.state.update(status='succeeded', result=result, finished_at=now())
        self.save()


def status(project_id, job_id):
    path = folder(project_id, job_id)
    value = engine.read_json(path / 'state.json')
    value['cancel_requested']=(path/'cancel.json').is_file()
    if value['status'] == 'running':
        try:
            with file_lock(path / 'running.lock'):
                # A job may have finished between the initial read and lock.
                value=engine.read_json(path/'state.json')
                value['cancel_requested']=(path/'cancel.json').is_file()
                if value['status']=='running':
                    value['status'] = 'interrupted'
                    value['note'] = 'No worker holds the job lock. Retry uses the saved snapshot.'
        except ValueError:
            pass
    return value


def retry_input(project_id, job_id):
    path = folder(project_id, job_id)
    with file_lock(path / 'running.lock'):
        state = engine.read_json(path / 'state.json')
        if state['status'] == 'succeeded':
            raise ValueError('Job already succeeded; use render_final/render_preview for a new edit')
        if state['status']=='queued':
            raise ValueError('Job is queued; cancel it before retrying')
        if digest(path / 'input.json') != state['input_sha256']:
            raise ValueError('Saved render input checksum mismatch')
        snapshot = engine.read_json(path / 'input.json')
        verify_media(snapshot['media'])
        return snapshot, path
