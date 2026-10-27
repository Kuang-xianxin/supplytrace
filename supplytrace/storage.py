import json, sqlite3, uuid
from datetime import datetime, timezone
from pathlib import Path
from .config import ROOT
from .models import WorkflowError

def now(): return datetime.now(timezone.utc).isoformat()

class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()

class Store:
    def __init__(self,runtime: Path):
        runtime.mkdir(parents=True,exist_ok=True)
        self.path=runtime/'supplytrace.sqlite3'
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS source_rows(tool TEXT, id TEXT, sku TEXT, body TEXT, PRIMARY KEY(tool,id));
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, created_at TEXT, status TEXT, request TEXT, result TEXT);
            CREATE TABLE IF NOT EXISTS usage(day TEXT PRIMARY KEY, count INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS conflict_reviews(
                run_id TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL,
                PRIMARY KEY(run_id,revision));''')
            if db.execute('SELECT count(*) FROM source_rows').fetchone()[0]==0:
                seed=json.loads((ROOT/'data/synthetic.json').read_text(encoding='utf8'))
                for tool in ('inventory','orders','shipments','supplier'):
                    db.executemany('INSERT INTO source_rows VALUES(?,?,?,?)',[(tool,r['id'],r['sku'],json.dumps(r)) for r in seed[tool]])

    def connect(self):
        db=sqlite3.connect(self.path,timeout=1.5,factory=ClosingConnection)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=1500')
        db.execute('PRAGMA cache_size=-2048')
        return db

    def reserve(self,request:dict,limit:int):
        # BEGIN IMMEDIATE makes concurrent quota checks one transaction, before any AI call.
        run_id=str(uuid.uuid4());stamp=now();day=stamp[:10]
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT count FROM usage WHERE day=?',(day,)).fetchone()
            used=row[0] if row else 0
            if used>=limit: raise WorkflowError('daily_quota_exceeded',429)
            db.execute('INSERT INTO usage VALUES(?,1) ON CONFLICT(day) DO UPDATE SET count=count+1',(day,))
            db.execute('INSERT INTO runs VALUES(?,?,?,?,NULL)',(run_id,stamp,'running',json.dumps(request)))
        return run_id

    def finish(self,run_id,result,status='complete'):
        with self.connect() as db:
            db.execute('UPDATE runs SET status=?,result=? WHERE id=?',(status,json.dumps(result,ensure_ascii=False),run_id))

    def get(self,run_id):
        with self.connect() as db:r=db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
        if not r:raise WorkflowError('run_not_found',404)
        return {**dict(r),'request':json.loads(r['request']),'result':json.loads(r['result']) if r['result'] else None}

    def recent(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT id,created_at,status FROM runs ORDER BY created_at DESC LIMIT 20')]

    def _review_state(self,db,run_id):
        from .adapters import digest
        row=db.execute('SELECT result FROM runs WHERE id=?',(run_id,)).fetchone()
        if not row:raise WorkflowError('run_not_found',404)
        result=json.loads(row['result']) if row['result'] else None
        quality=(result or {}).get('data_quality')
        if not quality or not quality.get('conflicts'):
            raise WorkflowError('no_reviewable_conflicts',409)
        events=[json.loads(r['body']) for r in db.execute(
            'SELECT body FROM conflict_reviews WHERE run_id=? ORDER BY revision',(run_id,))]
        return {'run_id':run_id,'evidence_sha256':digest(result),'revision':len(events),
            'conflicts':quality['conflicts'],'events':events,
            'business_decision_allowed':False,'source_rows_modified':False,
            'reviewer_identity':'self_declared_local_label_not_authenticated',
            'scope':'Annotations only. Retain both does not prove valid distinct business lines or release the failed check.'}

    def review_state(self,run_id):
        with self.connect() as db:
            db.execute('BEGIN')
            return self._review_state(db,run_id)

    def add_review(self,run_id,review):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state=self._review_state(db,run_id)
            if review.evidence_sha256!=state['evidence_sha256'] or review.expected_revision!=state['revision']:
                raise WorkflowError('review_stale_reload_required',409)
            if review.conflict_index>=len(state['conflicts']):
                raise WorkflowError('conflict_not_found',404)
            event={k:v for k,v in review.model_dump().items() if k!='expected_revision'}
            event.update(revision=state['revision']+1,created_at=now(),
                conflict=state['conflicts'][review.conflict_index])
            db.execute('INSERT INTO conflict_reviews VALUES(?,?,?)',
                (run_id,event['revision'],json.dumps(event,ensure_ascii=False)))
            return self._review_state(db,run_id)

    def review_export(self,run_id):
        with self.connect() as db:
            db.execute('BEGIN')
            state=self._review_state(db,run_id)
            row=db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
            original={**dict(row),'request':json.loads(row['request']),'result':json.loads(row['result'])}
            return {'schema':'supplytrace-conflict-review-v1','exported_at':now(),
                'original_run':original,'review':state}

    def usage(self):
        with self.connect() as db:r=db.execute('SELECT count FROM usage WHERE day=?',(now()[:10],)).fetchone()
        return r[0] if r else 0
