"""Small deterministic fault tests; artifacts are retained, never bulk cleaned."""
import asyncio
import hashlib
import io
import json
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from supplytrace.boundary import LocalBoundary
from supplytrace.cache import SingleFlight
from supplytrace.csv_engine import records, quantity_token
from supplytrace.models import WorkflowError
from supplytrace.storage import Store
from supplytrace.telemetry import Metrics
from supplytrace.workbench import Workbench, Mapping, Review
from supplytrace.workspace_lock import WorkspaceLock

BASE=Path(__file__).resolve().parents[1]/'verification'/'fault-data'

@pytest.fixture
def wb():
    obj=Workbench(Store(BASE/str(uuid.uuid4())))
    yield obj
    obj.close()

def wait(wb,ident,states):
    end=time.monotonic()+15
    while time.monotonic()<end:
        record=wb.get(ident)
        if record['state'] in states:return record
        time.sleep(.01)
    raise AssertionError('job deadline exceeded')

def ready(wb,raw=b'Order,Qty,Note\nA,1,a\nA,3,b\nB,2,c\nB,2,c\n'):
    ident=wb.create(raw,'fixture.csv','utf-8-sig',',',str(uuid.uuid4()))['id']
    assert wait(wb,ident,('draft','failed'))['state']=='draft'
    wb.analyze(ident,Mapping(keys=['Order'],quantity='Qty',expected_revision=0))
    assert wait(wb,ident,('ready','failed'))['state']=='ready'
    return ident

def review(wb,ident,n=0,groups=[1],**kw):
    return wb.review(ident,Review(group_ids=groups,disposition=kw.get('disposition','retain_all'),reviewer='fault-test',note=kw.get('note','Retain source rows'),expected_revision=n,request_id=kw.get('request_id',str(uuid.uuid4()))))

def test_streaming_reader_never_requests_full_input():
    class Bounded(io.BytesIO):
        def read(self,size=-1):
            assert 0<=size<=65536
            return super().read(size)
        def read1(self,size=-1):
            assert 0<=size<=65536
            return super().read1(size)
    source=Bounded(('A,Q\n'+'x,1\n'*10000).encode())
    data=records(source,'utf-8-sig',',')
    assert next(data)==['A','Q']
    assert sum(1 for _ in data)==10000

@pytest.mark.parametrize('a,b',[('1','1.00'),('-0','0E999999'),('1E100000','10E99999'),('123456789012345678901234567890','123456789012345678901234567890.000')])
def test_decimal_tokens_exact_no_context_rounding(a,b):
    assert quantity_token(a)==quantity_token(b)
    assert quantity_token('NaN')[1] is False

def test_field_and_multiline_record_boundaries():
    with pytest.raises(WorkflowError) as e:
        list(records(io.BytesIO(b'A,Q\n'+b'x'*40000+b',1'),'utf-8-sig',','))
    assert e.value.code=='malformed_csv'
    assert e.value.details['line']==2
    with pytest.raises(WorkflowError) as e:
        list(records(io.BytesIO(b'A,Q\n"'+(b'x'*1000+b'\n')*300+b'",1'),'utf-8-sig',','))
    assert e.value.code in ('malformed_csv','record_too_large')

def test_bad_encoding_reports_decoded_ahead_physical_line():
    with pytest.raises(WorkflowError) as error:
        list(records(io.BytesIO(b'A,Q\r\nx,1\r\ny,2\r\nz,\xff\r\n'),'utf-8-sig',','))
    assert error.value.code=='encoding_error' and error.value.details['line']==4

def test_projection_and_history_pagination(wb):
    ident=ready(wb)
    for n in range(35):review(wb,ident,n,note=f'Observation {n}')
    assert len(wb.get(ident)['events'])==30
    assert wb.get(ident)['reviewed_count']==1
    assert wb.detail(ident,1)['history']['total']==35
    assert len(wb.events(ident,1,30)['items'])==5
    assert wb.detail(ident,1)['review']['revision']==35
    assert wb.groups(ident,status='retain_all')['total']==1
    review(wb,ident,35,disposition='pending')
    assert wb.get(ident)['reviewed_count']==0
    assert wb.groups(ident,status='retain_all')['total']==0

def test_singleflight_and_failure_release_and_budget():
    cache=SingleFlight(entries=2,max_bytes=100,ttl=10,wait=.03)
    started,release=threading.Event(),threading.Event()
    def slow():started.set();release.wait(1);return {'ok':1}
    with ThreadPoolExecutor(2) as pool:
        owner=pool.submit(cache.get,'a',slow);started.wait()
        with pytest.raises(WorkflowError) as error:cache.get('a',slow)
        assert error.value.code=='query_timeout'
        release.set();assert owner.result()=={'ok':1}
    assert cache.get('a',lambda:1)=={'ok':1}
    with pytest.raises(ValueError):cache.get('bad',lambda:(_ for _ in ()).throw(ValueError('sanitized')))
    assert cache.snapshot()['flights']==0
    for n in range(10):cache.get(n,lambda:{'some':'value'})
    assert cache.snapshot()['entries']<=2 and cache.snapshot()['bytes']<=100

def test_disk_full_and_corruption_preserve_artifacts(wb):
    ident=ready(wb)
    with patch.object(wb,'disk_guard',side_effect=WorkflowError('disk_full',507)):
        with pytest.raises(WorkflowError) as exc:wb.export_file(ident)
        assert exc.value.code=='disk_full'
    result=wb.export_file(ident)
    with result.open('ab') as stream:stream.write(b'corrupt')
    with pytest.raises(WorkflowError) as exc:wb.export_file(ident)
    assert exc.value.code=='export_hash_mismatch'
    assert wb.get(ident)['state']=='ready'

def test_source_row_tamper_blocks_export(wb):
    ident=ready(wb)
    with wb.store.connect() as db:db.execute("UPDATE import_rows SET cells='[\"A\",\"999\",\"tampered\"]' WHERE import_id=? AND row_no=1",(ident,))
    with pytest.raises(WorkflowError) as exc:wb.export_file(ident)
    assert exc.value.code=='evidence_hash_mismatch'
    assert list(wb.artifacts.glob('*.partial'))

def test_source_blob_tamper_blocks_export(wb):
    ident=ready(wb)
    with wb.store.connect() as db:db.execute("UPDATE import_sources SET raw=cast('bad' as blob) WHERE import_id=?",(ident,))
    with pytest.raises(WorkflowError) as exc:wb.export_file(ident)
    assert exc.value.code=='source_hash_mismatch'

def test_sqlite_busy_timeout_and_recovery(wb):
    ident=ready(wb)
    with wb.store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        start=time.monotonic()
        with pytest.raises(sqlite3.OperationalError):review(wb,ident)
        assert .8<time.monotonic()-start<3
    assert review(wb,ident)['revision']==1

def test_cancel_retry_real_future_capacity(wb):
    release=threading.Event();wb.pool.submit(release.wait,5)
    ident=wb.create(b'A,Q\nx,1\n','q.csv','utf-8-sig',',',str(uuid.uuid4()))['id']
    try:
        for n in range(6):
            wb.cancel(ident)
            try:wb.retry_parse(ident)
            except WorkflowError as exc:
                assert exc.code=='queue_full';break
        assert n==5 and len(wb.futures)<=6 and wb.pool._work_queue.qsize()<=6
    finally:release.set()

def test_persisted_job_recovery_and_projection_migration(wb):
    ident=ready(wb)
    with wb.store.connect() as db:
        db.execute("UPDATE imports SET state='analyzing',generation=2,progress=0 WHERE id=?",(ident,))
        db.execute("INSERT INTO work_jobs VALUES(?,2,'analyze','running',0,1,0,'test')",(ident,))
    wb.start()
    assert wait(wb,ident,('ready','failed'))['state']=='ready'
    with wb.store.connect() as db:
        assert db.execute('SELECT count(*) FROM group_members WHERE import_id=? AND generation=2',(ident,)).fetchone()[0]==4

def test_supervisor_survives_storage_failure(wb):
    wb.start()
    with patch.object(wb.store,'connect',side_effect=sqlite3.OperationalError('injected')):
        time.sleep(.7)
        assert wb.supervisor.is_alive()
    assert wb.worker_errors>0
    ident=ready(wb)
    assert wb.get(ident)['state']=='ready'

def test_trace_chain_redacted(wb):
    ident=ready(wb,b'Order,Qty,Note\nA,1,SECRET-CELL\nA,2,b\n')
    wb.export_file(ident)
    data=(wb.directory/'traces.jsonl').read_text()
    spans=[json.loads(x) for x in data.splitlines()]
    assert {x['name'] for x in spans}>={'csv.import','csv.parse','csv.analyze','csv.aggregate','csv.export'}
    assert len({x['trace_id'] for x in spans})==1
    assert 'SECRET-CELL' not in data and ident not in data

def test_process_lease_rejects_second_owner():
    path=BASE/str(uuid.uuid4());path.mkdir(parents=True)
    one,two=WorkspaceLock(path/'lease'),WorkspaceLock(path/'lease')
    one.acquire()
    try:
        with pytest.raises(RuntimeError):two.acquire()
    finally:one.release()
    two.acquire();two.release()

def test_asgi_admission_holds_until_response_body_and_cancel():
    async def run():
        entered,release=asyncio.Event(),asyncio.Event();messages=[]
        async def app(scope,receive,send):
            await send({'type':'http.response.start','status':200,'headers':[]})
            entered.set();await release.wait()
            await send({'type':'http.response.body','body':b'done'})
        middleware=LocalBoundary(app,Metrics(),limit=1)
        scope={'type':'http','method':'GET','path':'/large','headers':[]}
        async def receive():return {'type':'http.request','body':b''}
        async def send(msg):messages.append(msg)
        task=asyncio.create_task(middleware(scope,receive,send));await entered.wait()
        assert middleware.active==1
        await middleware(scope,receive,send)
        assert any(x.get('status')==503 for x in messages)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert middleware.active==0
        release.set();await middleware(scope,receive,send);assert middleware.active==0
    asyncio.run(run())

def test_chunked_body_limit_without_content_length():
    async def run():
        async def app(scope,receive,send):await receive()
        middleware=LocalBoundary(app,Metrics());messages=[]
        async def receive():return {'type':'http.request','body':b'x'*10001}
        async def send(msg):messages.append(msg)
        await middleware({'type':'http','method':'POST','path':'/review','headers':[]},receive,send)
        assert messages[0]['status']==413 and middleware.active==0
    asyncio.run(run())

def test_model_failures_and_context_bound():
    import httpx
    from supplytrace.assistant import suggest
    evidence={'headers':['Q'],'rows':[{'row_no':1,'cells':['1']}]}
    def call(handler, data=evidence):return suggest('http://127.0.0.1:9999/v1','local',data,transport=httpx.MockTransport(handler))
    assert call(lambda r:httpx.Response(503))['error']=='model_http_error'
    assert call(lambda r:httpx.Response(200,content=b'not JSON'))['error']=='invalid_model_output'
    assert call(lambda r:httpx.Response(200,content=b'x'*70000))['error']=='model_output_limit'
    def timeout(request):raise httpx.ReadTimeout('private-server-message')
    assert call(timeout)=={'status':'failed','error':'model_timeout'}
    assert call(timeout,{'headers':['Q'],'rows':[{'row_no':1,'cells':['x'*17000]}]})['error']=='model_context_limit'

def test_enqueue_submission_failure_remains_recoverable(wb):
    with patch.object(wb.pool,'submit',side_effect=RuntimeError('dead worker')):
        with pytest.raises(WorkflowError) as error:
            wb.create(b'A,Q\nx,1\n','queue.csv','utf-8-sig',',',str(uuid.uuid4()))
        assert error.value.code=='worker_unavailable'
    ident=wb.list()['items'][0]['id']
    wb.start()
    assert wait(wb,ident,('draft','failed'))['state']=='draft'

def test_admission_releases_after_upload_timeout():
    async def run():
        async def app(scope,receive,send):await receive()
        middleware=LocalBoundary(app,Metrics());messages=[]
        async def receive():raise asyncio.TimeoutError()
        async def send(msg):messages.append(msg)
        await middleware({'type':'http','method':'POST','path':'/api/workbench/imports','headers':[]},receive,send)
        assert messages[0]['status']==408 and middleware.active==0
    asyncio.run(run())

def test_xss_sql_payload_is_only_source_data(wb):
    ident=ready(wb,b'Order,Qty,Note\n<script>alert(1)</script>,1,a\n<script>alert(1)</script>,2,b\n')
    assert wb.groups(ident,q="' OR 1=1 --")['total']==0
    assert wb.detail(ident,1)['rows'][0]['cells'][0]=='<script>alert(1)</script>'
    with pytest.raises(WorkflowError):wb.get("' OR 1=1 --")

def test_failed_cleanup_running_lease_recovered(wb):
    ident=ready(wb)
    wb.start()
    with wb.store.connect() as db:
        db.execute("UPDATE imports SET state='parsing',headers='[]',generation=2 WHERE id=?",(ident,))
        db.execute("INSERT INTO work_jobs VALUES(?,2,'parse','running',0,1,2,'cleanup-failure')",(ident,))
    # No live future exists for this persisted running lease.
    assert wait(wb,ident,('draft','failed'))['state']=='draft'

def test_dead_supervisor_refuses_new_work(wb):
    dead=threading.Thread(target=lambda:None);dead.start();dead.join();wb.supervisor=dead
    with pytest.raises(WorkflowError) as exc:wb.create(b'A,Q\nx,1\n','test.csv','utf-8-sig',',',str(uuid.uuid4()))
    assert exc.value.code=='worker_unavailable'
    assert wb.list()['total']==0

@pytest.mark.parametrize('case',['normal','busy','origin','length','body','upload_timeout'])
def test_all_response_paths_have_send_deadline(case):
    async def run():
        calls=[]
        async def app(scope,receive,send):
            if case in ('body','upload_timeout'):await receive()
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b'ok'})
        middleware=LocalBoundary(app,Metrics(),limit=0 if case=='busy' else 1)
        middleware.send_timeout=.02
        headers=[]
        if case=='origin':headers=[(b'origin',b'https://invalid.test')]
        if case=='length':headers=[(b'content-length',b'invalid')]
        async def receive():
            if case=='upload_timeout':raise asyncio.TimeoutError()
            return {'type':'http.request','body':b'x'*10001}
        async def send(message):
            calls.append(message)
            await asyncio.Event().wait()
        started=time.monotonic()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(middleware({'type':'http','method':'POST','path':'/review','headers':headers},receive,send),.25)
        assert time.monotonic()-started<.15
        assert len(calls)==1 and middleware.active==0
    asyncio.run(run())
