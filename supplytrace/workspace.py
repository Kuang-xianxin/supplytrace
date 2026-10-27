"""Transactional local workspace; large payloads stay on disk throughout the pipeline."""
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

from .cache import SingleFlight
from .csv_engine import Analysis, BlobReader, MAX_BYTES, MAX_ROWS, batches, packed, records
from .models import WorkflowError
from .storage import now
from .tracing import Tracing
from .workspace_schema import migrate

META = 'id,created_at,filename,sha256,encoding,delimiter,origin,state,error,headers,row_count,progress,mapping,generation,revision,group_count,error_count,error_line,reviewed_count,byte_size'
WORK_TIMEOUT = 180


def fingerprint(value):
    return hashlib.sha256(packed(value).encode()).hexdigest()


def admitted_job(function):
    @wraps(function)
    def run(self, *args, **kwargs):
        if not self.job_slots.acquire(blocking=False):
            raise WorkflowError('queue_full', 429)
        permit = {'submitted':False}
        try:
            return function(self,*args,_permit=permit,**kwargs)
        finally:
            if not permit['submitted']:
                self.job_slots.release()
    return run


class Workbench:
    def __init__(self, store):
        self.store = store
        self.directory = store.path.parent
        self.artifacts = self.directory/'workfiles'
        self.artifacts.mkdir(exist_ok=True)
        self.upload_slots, self.export_slots, self.job_slots = (threading.BoundedSemaphore(n) for n in (2,1,6))
        self.pool = ThreadPoolExecutor(max_workers=1,thread_name_prefix='csv-review')
        self.lock, self.stop = threading.Lock(), threading.Event()
        self.futures, self.active_engines = {}, {}
        self.supervisor = None
        self.cache = SingleFlight()
        self.tracing = Tracing(self.directory)
        self.worker_errors = 0
        self.lease = None
        self.wal_anchor = None
        migrate(store)

    def start(self):
        if self.supervisor and self.supervisor.is_alive():
            return
        from .workspace_lock import WorkspaceLock
        self.lease = WorkspaceLock(self.directory/'workspace.lock')
        self.lease.acquire()
        # Keep a connection across requests. Otherwise the last close can take an
        # exclusive WAL cleanup lock after a long streaming read, blocking a new
        # reader while hundreds of MB are checkpointed. This holds NO transaction.
        self.wal_anchor = sqlite3.connect(self.store.path,timeout=1.5,check_same_thread=False)
        self.wal_anchor.execute('SELECT 1 FROM schema_migrations LIMIT 1').fetchone()
        with self.store.connect() as db:
            # Startup is under a process workspace lock. No previous worker can be alive.
            db.execute("UPDATE work_jobs SET state='queued' WHERE state='running'")
        self.stop.clear()
        self.supervisor = threading.Thread(target=self._supervise, name='csv-supervisor',daemon=True)
        self.supervisor.start()

    def close(self):
        self.stop.set()
        if self.supervisor:
            self.supervisor.join(timeout=3)
        with self.lock:
            for engine in self.active_engines.values():
                engine.db.interrupt()
        try:
            self.pool.shutdown(wait=True,cancel_futures=False)
            self.tracing.provider.shutdown()
        finally:
            if self.wal_anchor:
                self.wal_anchor.close()
                self.wal_anchor = None
            if self.lease:
                self.lease.release()

    def _supervise(self):
        while not self.stop.is_set():
            try:
                with self.store.connect() as db:
                    pending = db.execute("SELECT import_id,generation,operation,state,attempts FROM work_jobs WHERE state IN ('queued','running') ORDER BY created_at LIMIT 6").fetchall()
                for job in pending:
                    key = (job['import_id'],job['generation'],job['operation'])
                    with self.lock:
                        if key in self.futures:
                            continue
                    if job['state']=='running':
                        # If exception cleanup also lost the DB connection, the finished
                        # future is gone but its persisted lease can still say running.
                        with self.store.connect() as db:
                            db.execute('BEGIN IMMEDIATE')
                            if job['attempts']>=3:
                                db.execute("UPDATE work_jobs SET state='interrupted' WHERE import_id=? AND generation=? AND operation=?",key)
                                db.execute("UPDATE imports SET state='interrupted',error='interrupted' WHERE id=? AND generation=? AND state IN ('parsing','analyzing')",key[:2])
                                continue
                            db.execute("UPDATE work_jobs SET state='queued' WHERE import_id=? AND generation=? AND operation=? AND state='running'",key)
                    if not self.job_slots.acquire(blocking=False):
                        break
                    permit = {'submitted':False}
                    try:
                        self._submit(permit,*key)
                    finally:
                        if not permit['submitted']:
                            self.job_slots.release()
            except Exception:
                # A temporary disk/DB error must not kill recovery supervision.
                self.worker_errors += 1
            self.stop.wait(.5)

    def _job(self, db, ident, generation, operation):
        if self.supervisor is not None and not self.supervisor.is_alive():
            raise WorkflowError('worker_unavailable',503)
        if db.execute("SELECT count(*) FROM work_jobs WHERE state IN ('queued','running')").fetchone()[0] >= 6:
            raise WorkflowError('queue_full',429)
        db.execute('INSERT INTO work_jobs VALUES(?,?,?,?,?,?,?,?)',
                   (ident,generation,operation,'queued',0,0,0,now()))

    def _submit(self, permit, ident, generation, operation):
        key = (ident,generation,operation)
        with self.lock:
            if key in self.futures:
                return
            def run():
                try:
                    with self.store.connect() as db:
                        changed = db.execute("UPDATE work_jobs SET state='running',heartbeat=?,attempts=attempts+1 WHERE import_id=? AND generation=? AND operation=? AND state='queued'",(time.time(),*key)).rowcount
                    if not changed:
                        return
                    if operation == 'parse':
                        self.parse(ident,generation)
                    else:
                        with self.store.connect() as db:
                            mapping = json.loads(self.item(db,ident)['mapping'])
                        self.process(ident,generation,mapping)
                    with self.store.connect() as db:
                        state = self.item(db,ident)['state']
                        db.execute('UPDATE work_jobs SET state=?,heartbeat=? WHERE import_id=? AND generation=? AND operation=?',
                            ('done' if state in ('draft','ready') else state,time.time(),*key))
                except Exception:
                    self.worker_errors += 1
                    # Avoid a cleanup failure masking recovery: leave durable work queued.
                    try:
                        with self.store.connect() as db:
                            db.execute("UPDATE work_jobs SET state=CASE WHEN attempts>=3 THEN 'interrupted' ELSE 'queued' END WHERE import_id=? AND generation=? AND operation=? AND state='running'",key)
                            db.execute("UPDATE imports SET state='interrupted',error='interrupted' WHERE id=? AND generation=? AND EXISTS(SELECT 1 FROM work_jobs WHERE import_id=? AND generation=? AND operation=? AND state='interrupted')",(ident,generation,*key))
                    except sqlite3.Error:
                        pass
                finally:
                    self.job_slots.release()
            try:
                future = self.pool.submit(run)
            except RuntimeError:
                raise WorkflowError('worker_unavailable',503) from None
            self.futures[key] = future
            permit['submitted'] = True
        def complete(_):
            with self.lock:
                self.futures.pop(key,None)
        future.add_done_callback(complete)

    def item(self, db, ident, include_raw=False):
        row = db.execute('SELECT '+('*' if include_raw else META)+ ' FROM imports WHERE id=?',(ident,)).fetchone()
        if row is None:
            raise WorkflowError('import_not_found',404)
        return row

    @staticmethod
    def public(row):
        return {k:json.loads(row[k]) if k in ('headers','mapping') and row[k] else row[k]
                for k in row.keys() if k not in ('raw','request_id','traceparent')}

    @staticmethod
    def row(row):
        return {'row_no':row['row_no'],'line_start':row['line_start'],'line_end':row['line_end'],
                'cells':json.loads(row['cells']),'parse_error':row['parse_error']}

    def disk_guard(self, reserve=0):
        # Includes abandoned/failed artifacts. Nothing is silently deleted to reclaim space.
        if shutil.disk_usage(self.directory).free < reserve + 256*1024*1024:
            raise WorkflowError('disk_full',507)
        used,count = 0,0
        for path in self.artifacts.iterdir():
            if path.is_file():
                used += path.stat().st_size;count += 1
                if count>=4096:
                    raise WorkflowError('workspace_full',507)
        if used+reserve > 2*1024*1024*1024:
            raise WorkflowError('workspace_full',507)

    def new_upload(self):
        self.disk_guard(MAX_BYTES)
        return self.artifacts/(uuid.uuid4().hex+'.upload')

    def create(self, raw, filename, encoding, delimiter, request_id, origin='upload'):
        # Small programmatic/example entry. HTTP upload writes each network chunk to disk.
        if not raw or len(raw)>MAX_BYTES:
            raise WorkflowError('file_size',413)
        path = self.new_upload()
        with path.open('xb') as target:
            target.write(raw)
        return self.create_file(path,filename,encoding,delimiter,request_id,origin)

    @admitted_job
    def create_file(self,path,filename,encoding,delimiter,request_id,origin='upload',_permit=None):
        size = path.stat().st_size
        if not size or size>MAX_BYTES:
            raise WorkflowError('file_size',413)
        if not re.fullmatch(r'[a-zA-Z0-9-]{8,80}',request_id):
            raise WorkflowError('request_id_required',422)
        if encoding not in ('utf-8-sig','gb18030') or delimiter not in (',',';','\t'):
            raise WorkflowError('invalid_csv_options',422)
        filename = ''.join(c for c in filename.replace('\\','/').rsplit('/',1)[-1][:180] if ord(c)>=32) or 'upload.csv'
        with path.open('rb') as raw:
            sha = hashlib.file_digest(raw,'sha256').hexdigest()
        ident = str(uuid.uuid4())
        with self.tracing.span('csv.import'):
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                previous = db.execute('SELECT '+META+' FROM imports WHERE request_id=?',(request_id,)).fetchone()
                if previous:
                    if (previous['sha256'],previous['encoding'],previous['delimiter'],previous['filename']) != (sha,encoding,delimiter,filename):
                        raise WorkflowError('idempotency_conflict',409)
                    return self.public(previous)
                usage = db.execute('SELECT bytes,imports FROM workspace_usage WHERE id=1').fetchone()
                if usage['imports']>=1000 or usage['bytes']+size>1024*1024*1024:
                    raise WorkflowError('workspace_full',507)
                db.execute('''INSERT INTO imports(id,created_at,filename,sha256,raw,encoding,delimiter,origin,state,request_id,byte_size,traceparent)
                    VALUES(?,?,?,?,X'',?,?,?,?,?,?,?)''',
                    (ident,now(),filename,sha,encoding,delimiter,origin,'parsing',request_id,size,self.tracing.parent()))
                cursor = db.execute('INSERT INTO import_sources VALUES(?,zeroblob(?),?,?)',(ident,size,sha,size))
                with db.blobopen('import_sources','raw',cursor.lastrowid) as blob, path.open('rb') as raw:
                    while chunk := raw.read(65536):
                        blob.write(chunk)
                db.execute('UPDATE workspace_usage SET bytes=bytes+?,imports=imports+1 WHERE id=1',(size,))
                self._job(db,ident,0,'parse')
        self._submit(_permit,ident,0,'parse')
        return self.get(ident)

    def copy(self,ident,request_id):
        path = self.new_upload()
        with path.open('xb') as out:
            for chunk in self.original(ident):
                out.write(chunk)
        with self.store.connect() as db:
            r = self.item(db,ident)
        return self.create_file(path,r['filename'],r['encoding'],r['delimiter'],request_id,r['origin'])

    @contextmanager
    def open_original_blob(self,db,ident):
        source = db.execute('SELECT rowid FROM import_sources WHERE import_id=?',(ident,)).fetchone()
        table = 'import_sources' if source else 'imports'
        rowid = source[0] if source else db.execute('SELECT rowid FROM imports WHERE id=?',(ident,)).fetchone()[0]
        with db.blobopen(table,'raw',rowid,readonly=True) as blob:
            yield blob

    def original(self,ident):
        with self.store.connect() as db:
            self.item(db,ident)
            with self.open_original_blob(db,ident) as blob:
                while chunk := blob.read(65536):
                    yield chunk

    def original_file(self,ident):
        with self.store.connect() as db:
            r = self.item(db,ident)
        target = self.artifacts/(ident+'.original.csv')
        if target.is_file():
            with target.open('rb') as stream:
                if hashlib.file_digest(stream,'sha256').hexdigest()!=r['sha256']:
                    raise WorkflowError('source_hash_mismatch',422)
            return target
        self.disk_guard(r['byte_size'])
        path = self.artifacts/(ident+'.'+uuid.uuid4().hex+'.original.partial')
        digest = hashlib.sha256()
        with path.open('xb') as output:
            for chunk in self.original(ident):
                digest.update(chunk);output.write(chunk)
            output.flush();os.fsync(output.fileno())
        if digest.hexdigest()!=r['sha256']:
            raise WorkflowError('source_hash_mismatch',422)
        try:
            os.rename(path,target)
        except FileExistsError:
            pass  # another bounded request published identical bytes; partial retained
        return target

    def _alive(self,db,ident,generation,state,started,progress=None):
        current = self.item(db,ident)
        if current['generation']!=generation or current['state']!=state or self.stop.is_set():
            raise WorkflowError('interrupted',409)
        if time.monotonic()-started>WORK_TIMEOUT:
            raise WorkflowError('task_timeout',408)
        if progress is not None:
            db.execute('UPDATE imports SET progress=? WHERE id=?',(progress,ident))
            db.execute("UPDATE work_jobs SET heartbeat=?,checkpoint=? WHERE import_id=? AND generation=? AND state='running'",(time.time(),progress,ident,generation))

    def parse(self,ident,generation=0):
        started = time.monotonic()
        try:
            with self.store.connect() as source:
                r = self.item(source,ident)
                self._alive(source,ident,generation,'parsing',started)
                parent = source.execute('SELECT traceparent FROM imports WHERE id=?',(ident,)).fetchone()[0]
                with self.tracing.span('csv.parse',parent), self.open_original_blob(source,ident) as blob:
                    raw = io.BufferedReader(BlobReader(blob),buffer_size=65536)
                    iterator = records(raw,r['encoding'],r['delimiter'])
                    try:
                        headers = next(iterator)
                        count = 0
                        # Verify original bytes independently on every replay, before declaring success.
                        for chunk in batches(iterator):
                            count = chunk[-1]['row_no']
                            with self.store.connect() as db:
                                db.execute('BEGIN IMMEDIATE')
                                self._alive(db,ident,generation,'parsing',started,count)
                                db.executemany('INSERT OR IGNORE INTO import_rows VALUES(?,?,?,?,?,?)',
                                    ((ident,x['row_no'],x['line_start'],x['line_end'],packed(x['cells']),x['error']) for x in chunk))
                        digest = hashlib.sha256()
                        blob.seek(0)
                        while part := blob.read(65536):
                            digest.update(part)
                        if digest.hexdigest()!=r['sha256']:
                            raise WorkflowError('source_hash_mismatch',422)
                        with self.store.connect() as db:
                            db.execute('BEGIN IMMEDIATE')
                            self._alive(db,ident,generation,'parsing',started)
                            db.execute("UPDATE imports SET state='draft',headers=?,row_count=?,progress=?,error=NULL,error_line=NULL WHERE id=?",(packed(headers),count,count,ident))
                    finally:
                        iterator.close()
                        raw.close()
        except WorkflowError as exc:
            self.fail(ident,exc.code,(exc.details or {}).get('line'),generation)
        except Exception as exc:
            self.fail(ident,'disk_full' if getattr(exc,'sqlite_errorcode',None)==sqlite3.SQLITE_FULL or getattr(exc,'errno',None)==28 else 'parse_failed',generation=generation)

    def fail(self,ident,code,line=None,generation=None):
        with self.store.connect() as db:
            state = 'interrupted' if code=='interrupted' else 'failed'
            db.execute("UPDATE imports SET state=?,error=?,error_line=? WHERE id=? AND generation=? AND state IN ('parsing','analyzing')",(state,code,line,ident,generation))

    @admitted_job
    def analyze(self,ident,mapping,_permit=None):
        data = mapping.model_dump(exclude={'expected_revision'})
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            r = self.item(db,ident)
            if r['state']=='analyzing' and json.loads(r['mapping'])==data:
                return self.public(r)
            if r['revision']!=mapping.expected_revision or r['revision'] or r['state'] not in ('draft','interrupted','failed','cancelled'):
                raise WorkflowError('mapping_locked',409)
            headers = json.loads(r['headers'])
            if not headers or len(set(mapping.keys))!=len(mapping.keys) or any(k not in headers for k in mapping.keys) or mapping.quantity not in headers:
                raise WorkflowError('invalid_mapping',422)
            generation = r['generation']+1
            db.execute("UPDATE imports SET state='analyzing',mapping=?,generation=?,progress=0,error=NULL,error_line=NULL WHERE id=?",(packed(data),generation,ident))
            self._job(db,ident,generation,'analyze')
        self._submit(_permit,ident,generation,'analyze')
        return self.get(ident)

    @admitted_job
    def retry_parse(self,ident,_permit=None):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            r = self.item(db,ident)
            if r['state'] not in ('interrupted','cancelled','failed') or r['headers']!='[]':
                raise WorkflowError('cannot_retry_parse',409)
            generation = r['generation']+1
            db.execute("UPDATE imports SET state='parsing',error=NULL,error_line=NULL,generation=? WHERE id=?",(generation,ident))
            self._job(db,ident,generation,'parse')
        self._submit(_permit,ident,generation,'parse')
        return self.get(ident)

    def process(self,ident,generation,mapping):
        engine = None
        started = time.monotonic()
        watchdog_done = threading.Event()
        try:
            with self.store.connect() as db:
                self._alive(db,ident,generation,'analyzing',started)
                r = self.item(db,ident)
                parent = db.execute('SELECT traceparent FROM imports WHERE id=?',(ident,)).fetchone()[0]
            self.disk_guard(min(MAX_BYTES*4, r['byte_size']*8))
            path = self.artifacts/(f'{ident}-{generation}-{uuid.uuid4().hex}.duckdb')
            engine = Analysis(path)
            with self.lock:
                self.active_engines[ident] = engine
            def deadline():
                if not watchdog_done.wait(WORK_TIMEOUT):
                    engine.db.interrupt()
            threading.Thread(target=deadline,name='csv-deadline',daemon=True).start()
            with self.tracing.span('csv.analyze',parent,engine='duckdb-arrow'):
                with self.store.connect() as source:
                    rows = (self.row(x) for x in source.execute('SELECT * FROM import_rows WHERE import_id=? ORDER BY row_no',(ident,)))
                    for chunk in batches(rows):
                        issues = engine.append(chunk,json.loads(r['headers']),mapping)
                        with self.store.connect() as db:
                            db.execute('BEGIN IMMEDIATE')
                            self._alive(db,ident,generation,'analyzing',started,chunk[-1]['row_no'])
                            # Idempotent checkpoint replay; no old-generation records are removed.
                            for n,field,code in issues:
                                db.execute('''INSERT INTO row_issues SELECT ?,?,?,?,? WHERE NOT EXISTS(
                                    SELECT 1 FROM row_issues WHERE import_id=? AND generation=? AND row_no=? AND field=? AND code=?)''',
                                    (ident,generation,n,field,code,ident,generation,n,field,code))
                with self.tracing.span('csv.aggregate',parent):
                    engine.group()
                for chunk in engine.stream('SELECT group_id,key_value,kind,row_count FROM grouped ORDER BY group_id'):
                    with self.store.connect() as db:
                        db.execute('BEGIN IMMEDIATE')
                        self._alive(db,ident,generation,'analyzing',started)
                        db.executemany('''INSERT OR IGNORE INTO review_groups(import_id,generation,group_id,key_value,kind,row_ids,evidence_sha256,row_count,evidence_format)
                            VALUES(?,?,?,?,?,'[]','',?,'ordered-row-hashes-v1')''',
                            ((ident,generation,gid,key,kind,count) for gid,key,kind,count in chunk))
                last, digest, finalized = None, None, []
                for chunk in engine.stream('SELECT g.group_id,f.row_no,f.row_hash FROM facts f JOIN grouped g USING(key_value) ORDER BY g.group_id,f.row_no'):
                    members = []
                    for gid,n,sha in chunk:
                        if gid!=last:
                            if last is not None:
                                finalized.append((digest.hexdigest(),ident,generation,last))
                            digest,last = hashlib.sha256(),gid
                        digest.update((sha+'\n').encode('ascii'))
                        members.append((ident,generation,gid,n))
                    with self.store.connect() as db:
                        db.execute('BEGIN IMMEDIATE')
                        self._alive(db,ident,generation,'analyzing',started)
                        db.executemany('INSERT OR IGNORE INTO group_members VALUES(?,?,?,?)',members)
                        db.executemany('UPDATE review_groups SET evidence_sha256=? WHERE import_id=? AND generation=? AND group_id=?',finalized)
                    finalized.clear()
                with self.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    self._alive(db,ident,generation,'analyzing',started)
                    if last is not None:
                        db.execute('UPDATE review_groups SET evidence_sha256=? WHERE import_id=? AND generation=? AND group_id=?',(digest.hexdigest(),ident,generation,last))
                    count = db.execute('SELECT count(*) FROM review_groups WHERE import_id=? AND generation=?',(ident,generation)).fetchone()[0]
                    errors = db.execute('SELECT count(*) FROM row_issues WHERE import_id=? AND generation=?',(ident,generation)).fetchone()[0]
                    db.execute("UPDATE imports SET state='ready',progress=row_count,group_count=?,error_count=?,error=NULL WHERE id=?",(count,errors,ident))
        except WorkflowError as exc:
            self.fail(ident,exc.code,generation=generation)
        except Exception:
            self.fail(ident,'task_timeout' if time.monotonic()-started>=WORK_TIMEOUT else 'analysis_failed',generation=generation)
        finally:
            watchdog_done.set()
            with self.lock:
                self.active_engines.pop(ident,None)
            if engine:
                engine.close()

    def cancel(self,ident):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            r = self.item(db,ident)
            db.execute("UPDATE imports SET state='cancelled' WHERE id=? AND state IN ('parsing','analyzing')",(ident,))
            db.execute("UPDATE work_jobs SET state='cancelled' WHERE import_id=? AND generation=? AND state IN ('queued','running')",(ident,r['generation']))
        with self.lock:
            if ident in self.active_engines:
                self.active_engines[ident].db.interrupt()
        # A cancelled-but-queued future still holds its real executor slot.
        return self.get(ident)

    def get(self,ident):
        with self.store.connect() as db:
            db.execute('BEGIN')
            out = self.public(self.item(db,ident))
            out['events'] = [json.loads(x[0]) for x in db.execute('SELECT body FROM review_events WHERE import_id=? ORDER BY revision DESC LIMIT 30',(ident,))][::-1]
            out['events_total'] = out['revision']
            out['preview'] = [self.row(x) for x in db.execute('SELECT * FROM import_rows WHERE import_id=? ORDER BY row_no LIMIT 5',(ident,))]
            return out

    def list(self,q='',offset=0):
        with self.store.connect() as db:
            total = db.execute('SELECT count(*) FROM imports WHERE instr(lower(filename),lower(?))>0',(q,)).fetchone()[0]
            rows = db.execute('SELECT '+META+' FROM imports WHERE instr(lower(filename),lower(?))>0 ORDER BY created_at DESC LIMIT 30 OFFSET ?',(q,offset))
            return {'total':total,'items':[self.public(r) for r in rows]}

    def groups(self,ident,kind='',status='',q='',offset=0):
        # Read the authoritative revision before consulting a cached immutable query snapshot.
        with self.store.connect() as db:
            r = self.item(db,ident)
            generation,revision = r['generation'],r['revision']
            if r['state']!='ready':
                raise WorkflowError('analysis_not_ready',409)
        def load():
            with self.store.connect() as db:
                db.execute('BEGIN')
                deadline = time.monotonic()+2
                db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
                actual = self.item(db,ident)
                if (actual['generation'],actual['revision'])!=(generation,revision):
                    raise WorkflowError('review_stale_reload_required',409)
                clause = 'g.import_id=? AND g.generation=?'
                params = [ident,generation]
                if kind:
                    clause += ' AND g.kind=?';params.append(kind)
                if status:
                    clause += ' AND g.disposition=?';params.append(status)
                if q:
                    clause += ' AND instr(lower(g.key_value),lower(?))>0';params.append(q)
                total = db.execute('SELECT count(*) FROM review_groups g WHERE '+clause,params).fetchone()[0]
                rows = db.execute('''SELECT g.group_id,g.key_value,g.kind,g.row_count,g.disposition,g.evidence_sha256,c.body
                    FROM review_groups g LEFT JOIN review_current c ON c.import_id=g.import_id AND c.generation=g.generation AND c.group_id=g.group_id
                    WHERE '''+clause+' ORDER BY g.group_id LIMIT 30 OFFSET ?',params+[offset])
                return {'total':total,'items':[{**{k:x[k] for k in x.keys() if k!='body'},'key_value':json.loads(x['key_value']), 'review':json.loads(x['body']) if x['body'] else None} for x in rows],'revision':revision}
        try:
            return self.cache.get((ident,generation,revision,kind,status,q,offset),load)
        except sqlite3.OperationalError as exc:
            if 'interrupt' in str(exc).lower():
                raise WorkflowError('query_timeout',503) from None
            raise

    def events(self,ident,gid,offset=0):
        with self.store.connect() as db:
            self.item(db,ident)
            total = db.execute('SELECT count(*) FROM group_events WHERE import_id=? AND group_id=?',(ident,gid)).fetchone()[0]
            rows = db.execute('''SELECT e.body FROM group_events ge JOIN review_events e ON e.import_id=ge.import_id AND e.revision=ge.revision
                WHERE ge.import_id=? AND ge.group_id=? ORDER BY ge.revision DESC LIMIT 30 OFFSET ?''',(ident,gid,offset))
            return {'total':total,'items':[json.loads(x[0]) for x in rows]}

    def detail(self,ident,gid,offset=0):
        with self.store.connect() as db:
            db.execute('BEGIN')
            r = self.item(db,ident)
            if r['state']!='ready':
                raise WorkflowError('analysis_not_ready',409)
            g = db.execute('SELECT group_id,key_value,kind,row_count,evidence_sha256,evidence_format FROM review_groups WHERE import_id=? AND generation=? AND group_id=?',(ident,r['generation'],gid)).fetchone()
            if not g:
                raise WorkflowError('group_not_found',404)
            rows = db.execute('''SELECT r.* FROM group_members m JOIN import_rows r ON r.import_id=m.import_id AND r.row_no=m.row_no
                WHERE m.import_id=? AND m.generation=? AND m.group_id=? ORDER BY m.row_no LIMIT 30 OFFSET ?''',(ident,r['generation'],gid,offset))
            review = db.execute('SELECT body FROM review_current WHERE import_id=? AND generation=? AND group_id=?',(ident,r['generation'],gid)).fetchone()
            result = {**dict(g),'key_value':json.loads(g['key_value']),'total':g['row_count'],'rows':[self.row(x) for x in rows],
                'headers':json.loads(r['headers']),'revision':r['revision'],'review':json.loads(review[0]) if review else None}
        result['history'] = self.events(ident,gid)
        return result

    def issues(self,ident,offset=0):
        with self.store.connect() as db:
            r = self.item(db,ident)
            items = db.execute('''SELECT i.*,r.line_start,r.line_end,r.cells FROM row_issues i JOIN import_rows r
                ON r.import_id=i.import_id AND r.row_no=i.row_no WHERE i.import_id=? AND i.generation=? ORDER BY i.row_no LIMIT 30 OFFSET ?''',(ident,r['generation'],offset))
            return {'total':r['error_count'],'items':[{**dict(x),'cells':json.loads(x['cells'])} for x in items]}

    def review(self,ident,review):
        payload = review.model_dump(exclude={'expected_revision','request_id'})
        sha = fingerprint(payload)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            r = self.item(db,ident)
            old = db.execute('SELECT payload_sha256 FROM review_events WHERE import_id=? AND request_id=?',(ident,review.request_id)).fetchone()
            if old:
                if old[0]!=sha:
                    raise WorkflowError('idempotency_conflict',409)
                return {'revision':r['revision'],'replayed':True}
            if r['state']!='ready' or r['revision']!=review.expected_revision:
                raise WorkflowError('review_stale_reload_required',409)
            if r['revision']>=20000:
                raise WorkflowError('review_history_full',507)
            ids = ','.join('?' for _ in review.group_ids)
            previous = db.execute('SELECT group_id,disposition FROM review_groups WHERE import_id=? AND generation=? AND group_id IN ('+ids+')',[ident,r['generation']]+review.group_ids).fetchall()
            if len(previous)!=len(review.group_ids):
                raise WorkflowError('group_not_found',404)
            revision = r['revision']+1
            event = {**payload,'revision':revision,'created_at':now(),'source_sha256':r['sha256']}
            body = packed(event)
            db.execute('INSERT INTO review_events VALUES(?,?,?,?,?,?)',(ident,revision,review.request_id,sha,event['created_at'],body))
            db.executemany('INSERT INTO group_events VALUES(?,?,?)',((ident,gid,revision) for gid in review.group_ids))
            db.executemany('''INSERT INTO review_current VALUES(?,?,?,?,?,?) ON CONFLICT(import_id,generation,group_id)
                DO UPDATE SET disposition=excluded.disposition,revision=excluded.revision,body=excluded.body''',
                ((ident,r['generation'],gid,review.disposition,revision,body) for gid in review.group_ids))
            db.executemany('UPDATE review_groups SET disposition=? WHERE import_id=? AND generation=? AND group_id=?',((review.disposition,ident,r['generation'],gid) for gid in review.group_ids))
            difference = sum(int(review.disposition!='pending')-int(x['disposition']!='pending') for x in previous)
            db.execute('UPDATE imports SET revision=?,reviewed_count=reviewed_count+? WHERE id=?',(revision,difference,ident))
            return {'revision':revision,'replayed':False}

    def export_file(self,ident):
        from .workspace_export import build_export
        if not self.export_slots.acquire(blocking=False):
            raise WorkflowError('export_busy',429)
        try:
            return build_export(self,ident)
        finally:
            self.export_slots.release()

    def export(self,ident):
        # Compatibility for explicit small in-process callers; HTTP serves the file.
        return self.export_file(ident).read_bytes()
