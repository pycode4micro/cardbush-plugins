"""Content-addressed analysis reuse. Cached measurements never include approvals."""
import copy
import hashlib
import json
from pathlib import Path

from . import engine, jobs, processes


def key(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


def implementation(*names):
    root = Path(__file__).parent
    return {name: jobs.digest(root / name) for name in names}


def location(namespace, identity):
    if namespace not in ('source_range', 'speech'):
        raise ValueError('Unknown analysis cache namespace')
    return engine.PROJECTS.parent / 'analysis_cache' / namespace / (key(identity) + '.json')


def read(namespace, identity):
    path = location(namespace, identity)
    try:
        record = engine.read_json(path)
        if not record or record['identity'] != identity or key(record['result']) != record['result_sha256']:
            return None
        for file in record['files']:
            processes.check()
            if jobs.digest(file['path']) != file['sha256']:
                return None
        return copy.deepcopy(record['result'])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write(namespace, identity, result, files):
    path = location(namespace, identity)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Refer to immutable evidence in its originating job; deletion or corruption
    # yields a miss, not a stale approval or a broken permanent cache entry.
    unique = {str(Path(f['path']).resolve()): f['sha256'] for f in files}
    engine.write_json(path, {'identity': identity, 'result': result, 'result_sha256': key(result),
                            'files': [{'path': p, 'sha256': sha} for p, sha in unique.items()]})
