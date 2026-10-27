"""Additive migrations; never removes original source/event tables."""
from .storage import now

VERSION = 4


def migrate(store):
    with store.connect() as db:
        db.executescript('''BEGIN IMMEDIATE;
        CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS imports(
          id TEXT PRIMARY KEY, created_at TEXT NOT NULL, filename TEXT NOT NULL,
          sha256 TEXT NOT NULL, raw BLOB NOT NULL, encoding TEXT NOT NULL, delimiter TEXT NOT NULL,
          origin TEXT NOT NULL, state TEXT NOT NULL, error TEXT, headers TEXT NOT NULL DEFAULT '[]',
          row_count INTEGER NOT NULL DEFAULT 0, progress INTEGER NOT NULL DEFAULT 0,
          mapping TEXT, generation INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 0,
          group_count INTEGER NOT NULL DEFAULT 0, error_count INTEGER NOT NULL DEFAULT 0,
          request_id TEXT UNIQUE NOT NULL);
        CREATE TABLE IF NOT EXISTS import_rows(
          import_id TEXT NOT NULL REFERENCES imports(id), row_no INTEGER NOT NULL,
          line_start INTEGER NOT NULL, line_end INTEGER NOT NULL, cells TEXT NOT NULL,
          parse_error TEXT, PRIMARY KEY(import_id,row_no));
        CREATE TABLE IF NOT EXISTS review_groups(
          import_id TEXT NOT NULL REFERENCES imports(id), generation INTEGER NOT NULL,
          group_id INTEGER NOT NULL, key_value TEXT NOT NULL, kind TEXT NOT NULL,
          row_ids TEXT NOT NULL, evidence_sha256 TEXT NOT NULL,
          PRIMARY KEY(import_id,generation,group_id));
        CREATE TABLE IF NOT EXISTS row_issues(
          import_id TEXT NOT NULL REFERENCES imports(id), generation INTEGER NOT NULL,
          row_no INTEGER NOT NULL, field TEXT NOT NULL, code TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS row_issues_lookup ON row_issues(import_id,generation,row_no);
        CREATE INDEX IF NOT EXISTS imports_created ON imports(created_at DESC);
        CREATE INDEX IF NOT EXISTS imports_state ON imports(state);
        CREATE TABLE IF NOT EXISTS review_events(
          import_id TEXT NOT NULL REFERENCES imports(id), revision INTEGER NOT NULL,
          request_id TEXT NOT NULL, payload_sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
          body TEXT NOT NULL, PRIMARY KEY(import_id,revision), UNIQUE(import_id,request_id));
        CREATE TABLE IF NOT EXISTS group_members(
          import_id TEXT NOT NULL, generation INTEGER NOT NULL, group_id INTEGER NOT NULL,
          row_no INTEGER NOT NULL, PRIMARY KEY(import_id,generation,group_id,row_no));
        CREATE UNIQUE INDEX IF NOT EXISTS members_by_row ON group_members(import_id,generation,row_no);
        CREATE TABLE IF NOT EXISTS review_current(
          import_id TEXT NOT NULL, generation INTEGER NOT NULL, group_id INTEGER NOT NULL,
          disposition TEXT NOT NULL CHECK(disposition IN ('pending','retain_all','follow_up')),
          revision INTEGER NOT NULL, body TEXT NOT NULL,
          PRIMARY KEY(import_id,generation,group_id));
        CREATE INDEX IF NOT EXISTS current_status ON review_current(import_id,generation,disposition,group_id);
        CREATE TABLE IF NOT EXISTS group_events(
          import_id TEXT NOT NULL, group_id INTEGER NOT NULL, revision INTEGER NOT NULL,
          PRIMARY KEY(import_id,group_id,revision));
        CREATE TABLE IF NOT EXISTS work_jobs(
          import_id TEXT NOT NULL, generation INTEGER NOT NULL, operation TEXT NOT NULL,
          state TEXT NOT NULL, heartbeat REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
          checkpoint INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
          PRIMARY KEY(import_id,generation,operation));
        CREATE INDEX IF NOT EXISTS jobs_state ON work_jobs(state,created_at);
        CREATE TABLE IF NOT EXISTS workspace_usage(id INTEGER PRIMARY KEY CHECK(id=1), bytes INTEGER NOT NULL, imports INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS import_sources(
          import_id TEXT PRIMARY KEY REFERENCES imports(id), raw BLOB NOT NULL,
          sha256 TEXT NOT NULL, byte_size INTEGER NOT NULL);
        ''')
        columns = {r[1] for r in db.execute('PRAGMA table_info(imports)')}
        for name, definition in [('error_line','INTEGER'), ('traceparent',"TEXT NOT NULL DEFAULT ''"),
                                 ('reviewed_count','INTEGER NOT NULL DEFAULT 0'), ('byte_size','INTEGER NOT NULL DEFAULT 0')]:
            if name not in columns:
                db.execute(f'ALTER TABLE imports ADD COLUMN {name} {definition}')
        columns = {r[1] for r in db.execute('PRAGMA table_info(review_groups)')}
        for name, definition in [('row_count','INTEGER NOT NULL DEFAULT 0'),
                                 ('disposition',"TEXT NOT NULL DEFAULT 'pending'"),
                                 ('evidence_format',"TEXT NOT NULL DEFAULT 'legacy-json-array-sha256'")]:
            if name not in columns:
                db.execute(f'ALTER TABLE review_groups ADD COLUMN {name} {definition}')
        db.execute('CREATE INDEX IF NOT EXISTS groups_filter ON review_groups(import_id,generation,kind,disposition,group_id)')
        db.execute('CREATE INDEX IF NOT EXISTS groups_status ON review_groups(import_id,generation,disposition,group_id)')
        if not db.execute('SELECT 1 FROM schema_migrations WHERE version=3').fetchone():
            # SQLite json_each expands old arrays in the engine, not a Python mega-list.
            db.execute('''INSERT OR IGNORE INTO group_members SELECT import_id,generation,group_id,value
                          FROM review_groups,json_each(review_groups.row_ids)''')
            db.execute('UPDATE review_groups SET row_count=json_array_length(row_ids)')
            for event in db.execute('SELECT e.import_id,e.revision,e.body,i.generation FROM review_events e JOIN imports i ON i.id=e.import_id ORDER BY e.import_id,e.revision'):
                import json
                body = json.loads(event['body'])
                db.executemany('INSERT OR IGNORE INTO group_events VALUES(?,?,?)',
                    ((event['import_id'],gid,event['revision']) for gid in body['group_ids']))
                db.executemany('INSERT OR REPLACE INTO review_current VALUES(?,?,?,?,?,?)',
                    ((event['import_id'],event['generation'],gid,body['disposition'],event['revision'],event['body']) for gid in body['group_ids']))
            db.execute('''UPDATE review_groups SET disposition=coalesce((SELECT disposition FROM review_current c
                          WHERE c.import_id=review_groups.import_id AND c.generation=review_groups.generation AND c.group_id=review_groups.group_id),'pending')''')
            db.execute("UPDATE imports SET byte_size=length(raw),reviewed_count=(SELECT count(*) FROM review_current c WHERE c.import_id=imports.id AND c.disposition!='pending')")
        db.execute('INSERT OR IGNORE INTO workspace_usage SELECT 1,coalesce(sum(byte_size),0),count(*) FROM imports')
        for version in (1,2,3,4):
            db.execute('INSERT OR IGNORE INTO schema_migrations VALUES(?,?)',(version,now()))
        # Preserve legacy interrupted state; persisted v3 jobs are resumed by start().
        db.execute("UPDATE imports SET state='interrupted',error='interrupted' WHERE state IN ('parsing','analyzing') AND NOT EXISTS(SELECT 1 FROM work_jobs j WHERE j.import_id=imports.id AND j.generation=imports.generation AND j.state IN ('queued','running'))")
