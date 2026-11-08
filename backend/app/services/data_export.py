"""Offline exports from SQLite online snapshots; bounded streaming ZIP output."""
from contextlib import closing
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import zipfile

from app import db
from app.services.backups import _snapshot

_ENCODER = json.JSONEncoder(ensure_ascii=False, separators=(',', ':'))


class _ZipSink:
    """Unseekable output lets zipfile use ZIP data descriptors."""
    def __init__(self):
        self.chunks = []
        self.position = 0

    def write(self, chunk):
        # Deflate often buffers small JSON tokens without producing bytes.
        # Do not send an empty ASGI body (and thread-pool hop) for each token.
        if chunk:
            self.chunks.append(chunk)
        self.position += len(chunk)
        return len(chunk)

    def tell(self):
        return self.position

    def seek(self, *args):
        raise io.UnsupportedOperation('stream')

    def flush(self):
        pass

    def drain(self):
        chunks, self.chunks = self.chunks, []
        return chunks


def _array(rows):
    yield '['
    first = True
    for row in rows:
        if not first:
            yield ','
        first = False
        yield from _ENCODER.iterencode(dict(row))
    yield ']'


def _messages(conn, conversation_id):
    return conn.execute('''SELECT id,role,content,translation,turn_id,revision,created_at
        FROM messages WHERE conversation_id=? ORDER BY id''', (conversation_id,))


def _conversations(conn, conversation_id):
    where, params = (' WHERE id=?', (conversation_id,)) if conversation_id else ('', ())
    return conn.execute('''SELECT id,session_id,title,identity_mode,is_pinned,provider_id,model_id,
        created_at,updated_at FROM conversations''' + where + ' ORDER BY created_at,id', params)


def _conversation_json(conn, conversation_id):
    yield '"conversations":['
    first = True
    for row in _conversations(conn, conversation_id):
        if not first:
            yield ','
        first = False
        yield json.dumps(dict(row), ensure_ascii=False)[:-1] + ',"messages":'
        yield from _array(_messages(conn, row['id']))
        yield '}'
    yield ']'


def _markdown(conn, worldline, conversation_id):
    yield f'# AMADEUS · {worldline}\n\n'
    for row in _conversations(conn, conversation_id):
        yield f'## {row["title"]}\n\n{row["created_at"]} · {row["id"]}\n\n'
        for message in _messages(conn, row['id']):
            yield f'### {message["role"]} · {message["created_at"]}\n\n{message["content"]}\n\n'
            if message['translation']:
                yield f'{message["translation"]}\n\n'


# A source can disappear before the async erasure worker finishes. Inspect both
# receipts and the history snapshot; never re-export retained raw evidence.
_SOURCE_DELETED = '''(
    s.source_state != 'present'
    OR EXISTS (SELECT 1 FROM memory_deleted_sources d
        WHERE d.session_id=o.session_id AND d.identity_mode=o.identity_mode
          AND d.conversation_id=s.conversation_id AND d.source_message_id=s.source_message_id)
    OR NOT EXISTS (SELECT 1 FROM history.messages m
        WHERE m.id=s.source_message_id AND m.conversation_id=s.conversation_id
          AND m.session_id=o.session_id AND m.role='user')
)'''


def _memory_json(conn):
    # Only open display versions and visible experience history, in both identity
    # modes. semantic_json, old versions, observations and source excerpts stay out.
    facts = conn.execute(f'''SELECT f.fact_id,f.session_id,f.identity_mode,f.topic_id,
        f.is_pinned,f.created_at,f.updated_at,v.version_no,v.display_text,v.confidence
        FROM stable_facts f JOIN stable_fact_versions v
          ON v.fact_id=f.fact_id AND v.version_no=f.active_version AND v.invalid_at IS NULL
        WHERE f.state='active' AND f.deleted_at IS NULL
          AND NOT EXISTS (SELECT 1 FROM memory_tombstones t WHERE t.fact_id=f.fact_id
              AND t.session_id=f.session_id AND t.identity_mode=f.identity_mode)
          AND NOT EXISTS (SELECT 1 FROM memory_deleted_sources d WHERE d.fact_id=f.fact_id
              AND d.session_id=f.session_id AND d.identity_mode=f.identity_mode)
          AND NOT EXISTS (SELECT 1 FROM stable_fact_evidence e
              JOIN memory_observations o ON o.observation_id=e.observation_id
              JOIN memory_observation_sources s ON s.observation_id=o.observation_id
              WHERE e.fact_id=f.fact_id AND e.version_no=v.version_no AND {_SOURCE_DELETED})
        ORDER BY f.created_at,f.fact_id''')
    yield '"stable_facts":'
    yield from _array(facts)
    experiences = conn.execute(f'''SELECT x.experience_id,x.session_id,x.conversation_id,
        x.identity_mode,x.display_text,x.confidence,x.status,x.expires_at,x.is_pinned,x.created_at
        FROM experiences x JOIN memory_observations o ON o.observation_id=x.observation_id
        WHERE x.status IN ('active','expired') AND x.deleted_at IS NULL AND o.status!='deleted'
          AND NOT EXISTS (SELECT 1 FROM memory_observation_sources s
              WHERE s.observation_id=o.observation_id AND {_SOURCE_DELETED})
        ORDER BY x.created_at,x.experience_id''')
    yield ',"experiences":'
    yield from _array(experiences)


class PreparedExport:
    def __init__(self, kind, format, worldline, conversation_id):
        self.kind, self.format = kind, format
        self.conversation_id = conversation_id
        self.worldlines = ('sg', 'beta') if worldline == 'all' else (worldline,)
        self.created_at = datetime.now(timezone.utc)
        scope = worldline + ('-conversation' if conversation_id else '')
        self.filename = f'amadeus-{self.created_at.strftime("%Y%m%dT%H%M%SZ")}-{kind}-{scope}.zip'
        self.temp = tempfile.TemporaryDirectory(prefix='amadeus-export-')
        self.root = Path(self.temp.name)

    def close(self):
        self.temp.cleanup()

    def prepare(self):
        try:
            matches = 0
            for wl in self.worldlines:
                kinds = ('history', 'memory') if self.kind == 'memory' else ('history',)
                for kind in kinds:
                    _snapshot(Path(db._resolve_path(wl, kind)), self.root / f'{wl}-{kind}.sqlite3')
                # Check selectors/schema before HTTP headers are committed.
                with closing(self._connect(wl)) as conn:
                    if self.kind == 'conversations':
                        matches += sum(1 for _ in _conversations(conn, self.conversation_id))
                    else:
                        for _ in _memory_json(conn):
                            pass
            if self.conversation_id and not matches:
                raise LookupError('conversation_not_found')
            return self
        except BaseException:
            self.close()
            raise

    def _connect(self, wl):
        kind = 'history' if self.kind == 'conversations' else 'memory'
        # Starlette advances sync generators on its thread pool; access remains
        # sequential, but individual next() calls may run on different threads.
        conn = sqlite3.connect((self.root / f'{wl}-{kind}.sqlite3').as_uri() + '?mode=ro', uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        if self.kind == 'memory':
            conn.execute('ATTACH DATABASE ? AS history', ((self.root / f'{wl}-history.sqlite3').as_uri() + '?mode=ro',))
        return conn

    def _text(self, conn, wl):
        if self.format == 'md':
            yield from _markdown(conn, wl, self.conversation_id)
            return
        yield json.dumps({'format': f'amadeus.{self.kind}', 'format_version': 1,
                          'exported_at': self.created_at.isoformat(), 'worldline': wl}, ensure_ascii=False)[:-1] + ','
        if self.kind == 'conversations':
            yield from _conversation_json(conn, self.conversation_id)
        else:
            yield from _memory_json(conn)
        yield '}'

    def stream(self):
        sink = _ZipSink()
        try:
            with zipfile.ZipFile(sink, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for wl in self.worldlines:
                    with closing(self._connect(wl)) as conn:
                        with archive.open(f'{wl}/{self.kind}.{self.format}', 'w', force_zip64=True) as entry:
                            for text in self._text(conn, wl):
                                data = text.encode('utf-8')
                                for start in range(0, len(data), 65536):
                                    entry.write(data[start:start+65536])
                                    yield from sink.drain()
                        yield from sink.drain()
            yield from sink.drain()
        finally:
            self.close()
