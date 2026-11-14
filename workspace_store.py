"""Persistent local comparison history and append-only buyer review events."""
from contextlib import contextmanager
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat()


class WorkspaceStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'rfxdesk.sqlite3'
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS comparisons (id TEXT PRIMARY KEY, document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS drafts (id TEXT PRIMARY KEY, document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, kind TEXT NOT NULL,
                    quote_id TEXT NOT NULL, source_hash TEXT NOT NULL, created_at TEXT NOT NULL,
                    document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, created_at TEXT NOT NULL,
                    document TEXT NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def comparisons(self):
        with self.connect() as db:
            return {r['id']: json.loads(r['document']) for r in db.execute('SELECT * FROM comparisons ORDER BY rowid')}

    def recover_legacy(self, uploads, profiles):
        known = self.comparisons()
        for folder in sorted(Path(uploads).glob('*')):
            if not folder.is_dir() or folder.name in known or len(folder.name) != 32:
                continue
            candidates = [p for p in folder.glob('*/rfx.json') if list((p.parent / 'outputs').glob('V*.json'))]
            if not candidates:
                continue
            path = max(candidates, key=lambda p: p.stat().st_mtime)
            try:
                snapshot = json.loads(path.read_text())
                label = next((label for label, document in profiles.items() if document == snapshot), None)
                if label:
                    known[folder.name] = self.save_comparison(folder.name, {
                        'name': f'Recovered comparison {len(known) + 1}', 'rfx_choice': label,
                        'rfx_snapshots': {label: snapshot}, 'settings': {}})
            except (ValueError, OSError):
                continue

    def save_comparison(self, identifier, document):
        if len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier):
            raise ValueError('Invalid comparison ID.')
        existing = self.comparisons().get(identifier, {})
        value = {**existing, **document, 'created_at': existing.get('created_at', now()), 'updated_at': now()}
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO comparisons VALUES (?, ?)',
                       (identifier, json.dumps(value, default=str)))
        return value

    def save_draft(self, document, identifier=None):
        if identifier and self.drafts().get(identifier, {}).get('buyer_approved'):
            if self.drafts()[identifier] != document:
                identifier = None
        identifier = identifier or uuid4().hex
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO drafts VALUES (?, ?)', (identifier, json.dumps(document)))
        destination = self.directory / 'rfx' / f'{identifier}.json'
        destination.parent.mkdir(exist_ok=True)
        destination.write_text(json.dumps(document, indent=2), encoding='utf-8')
        return identifier, destination

    def drafts(self):
        with self.connect() as db:
            return {r['id']: json.loads(r['document']) for r in db.execute('SELECT * FROM drafts ORDER BY rowid')}

    def add_events(self, scope, kind, quote, documents):
        with self.connect() as db:
            for document in documents:
                db.execute('INSERT INTO events(scope,kind,quote_id,source_hash,created_at,document) VALUES (?,?,?,?,?,?)',
                           (scope, kind, quote['quote_id'], quote['metadata']['source_sha256'], now(), json.dumps(document)))

    def events(self, scope, kind=None):
        with self.connect() as db:
            sql = 'SELECT * FROM events WHERE scope=?'
            params = [scope]
            if kind:
                sql += ' AND kind=?'
                params.append(kind)
            return [{**dict(row), 'document': json.loads(row['document'])}
                    for row in db.execute(sql + ' ORDER BY id', params)]

    def save_answer(self, scope, document):
        with self.connect() as db:
            db.execute('INSERT INTO conversations(scope,created_at,document) VALUES (?,?,?)',
                       (scope, now(), json.dumps(document, default=str)))

    def answers(self, scope):
        with self.connect() as db:
            return [{**json.loads(r['document']), 'created_at': r['created_at']}
                    for r in db.execute('SELECT * FROM conversations WHERE scope=? ORDER BY id', (scope,))]
