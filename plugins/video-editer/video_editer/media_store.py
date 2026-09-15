"""Project-scoped, append-only evidence records. No semantic inference or models."""
from contextlib import contextmanager
import json
import math
import sqlite3
import time
import uuid
from . import engine

SCHEMA = 1


@contextmanager
def db(project_id):
    path = engine.project_dir(project_id) / 'media-index.sqlite3'
    con = sqlite3.connect(path, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        version = con.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, SCHEMA):
            raise ValueError('Unsupported media index schema; migration required')
        if version == 0:
            con.execute('PRAGMA journal_mode=WAL')
        con.execute('''CREATE TABLE IF NOT EXISTS records (
            kind TEXT NOT NULL, id TEXT NOT NULL, version INTEGER NOT NULL,
            asset_id TEXT NOT NULL, sha256 TEXT NOT NULL, start REAL, end REAL,
            created REAL NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY(kind,id,version))''')
        con.execute('CREATE INDEX IF NOT EXISTS by_asset ON records(kind,asset_id,start,end)')
        con.execute('PRAGMA user_version=1')
        con.commit()
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def number(value, name, low=0, high=1e9):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{name} must be finite in [{low}, {high}]')
    return float(value)


def text(value, name, maximum=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f'{name} requires nonblank text, max {maximum} characters')
    return value.strip()


def strings(value, name, maximum=40):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f'{name} requires a list of at most {maximum} strings')
    return [text(v, name, 300) for v in value]


def bounds(start, end, duration):
    start, end = number(start, 'source_start'), number(end, 'source_end', high=duration)
    if start >= end:
        raise ValueError('source_start must be before source_end')
    return start, end


def add(project_id, kind, asset, payload, record_id=None, expected_version=0):
    """CAS append; an update never destroys the old version."""
    if not isinstance(expected_version, int) or isinstance(expected_version, bool) or expected_version < 0:
        raise ValueError('expected_version must be a nonnegative integer')
    if record_id is None:
        if expected_version != 0:
            raise ValueError('New records require expected_version=0')
        record_id = kind + '_' + uuid.uuid4().hex[:16]
    elif not isinstance(record_id, str) or not record_id.startswith(kind+'_') or not record_id[len(kind)+1:].isalnum():
        raise ValueError('Invalid record ID')
    with db(project_id) as con:
        con.execute('BEGIN IMMEDIATE')
        previous = con.execute('SELECT * FROM records WHERE kind=? AND id=? ORDER BY version DESC LIMIT 1', (kind, record_id)).fetchone()
        if (previous['version'] if previous else 0) != expected_version:
            raise ValueError('Version conflict; reload before updating')
        if previous and (previous['asset_id'] != asset['id'] or previous['sha256'] != asset['identity']['sha256']):
            raise ValueError('A record cannot change its source identity')
        if not previous and expected_version:
            raise ValueError('Record does not exist')
        result = {**payload, 'schema_version': SCHEMA, 'id': record_id, 'version': expected_version+1,
                  'asset_id': asset['id'], 'source_sha256': asset['identity']['sha256'], 'created_at': time.time()}
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode('utf-8')) > 131072:
            raise ValueError('Evidence record exceeds 128 KiB; split the annotation')
        con.execute('INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?)',
                    (kind, record_id, result['version'], asset['id'], result['source_sha256'],
                     result.get('source_start'), result.get('source_end'), result['created_at'], encoded))
    return result


def get(project_id, kind, record_id, version=None):
    if version is not None and (not isinstance(version,int) or isinstance(version,bool) or version<1):
        raise ValueError('Record version must be a positive integer')
    with db(project_id) as con:
        row = con.execute('SELECT payload FROM records WHERE kind=? AND id=?'+
                          (' AND version=?' if version is not None else '')+' ORDER BY version DESC LIMIT 1',
                          (kind,record_id,version) if version is not None else (kind,record_id)).fetchone()
    if not row:
        raise ValueError(f'Unknown {kind} record/version')
    return json.loads(row[0])


def rows(project_id, kind, asset_id=None, history=False):
    # Streaming cursor: do not load the full long-video index into RAM.
    with db(project_id) as con:
        sql = 'SELECT r.payload FROM records r WHERE r.kind=?'
        args = [kind]
        if asset_id:
            sql += ' AND r.asset_id=?'; args.append(asset_id)
        if not history:
            sql += ' AND r.version=(SELECT MAX(x.version) FROM records x WHERE x.kind=r.kind AND x.id=r.id)'
        sql += ' ORDER BY r.start,r.id,r.version'
        for row in con.execute(sql,args):
            yield json.loads(row[0])


def search(project_id, kind, query='', tags=None, asset_id=None, offset=0, limit=20, history=False):
    number(offset,'offset',high=1000000); number(limit,'limit',1,100)
    if not isinstance(offset,int) or not isinstance(limit,int):
        raise ValueError('Pagination must use integers')
    if not isinstance(query,str) or len(query)>500:
        raise ValueError('query must be at most 500 characters')
    tags = strings([] if tags is None else tags,'tags')
    terms = query.casefold().split()
    hits=[]; count=0
    for record in rows(project_id,kind,asset_id,history):
        # Literal Unicode substring matching supports Chinese without a tokenizer/model.
        hay = json.dumps({k:v for k,v in record.items() if k in (
            'summary','tags','quotes','reason','visual_evidence','context','risks','uncertainty','claims')},ensure_ascii=False).casefold()
        if any(term not in hay for term in terms) or not set(tags).issubset(record.get('tags',[])):
            continue
        if offset <= count < offset+limit:
            hits.append({**record,'match_reasons':{'literal_terms':terms,'exact_tags':tags}})
        count += 1
    return {'items':hits,'total':count,'next_offset':offset+limit if count>offset+limit else None,
            'ranking':'source order; literal matches only, no semantic ranking'}


def merge(intervals):
    result=[]
    for start,end in sorted(intervals):
        if result and start <= result[-1][1]:
            result[-1][1]=max(end,result[-1][1])
        else:
            result.append([start,end])
    return result


def gaps(intervals, duration):
    result=[]; cursor=0.0
    for start,end in merge(intervals):
        if start>cursor: result.append([cursor,start])
        cursor=max(cursor,end)
    if cursor<duration: result.append([cursor,duration])
    return result
