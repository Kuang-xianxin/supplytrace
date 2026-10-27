import gc
import json
import sqlite3
import threading
import time
import tracemalloc
from pathlib import Path
from uuid import uuid4

import pytest
from supplytrace.models import WorkflowError
from supplytrace.storage import Store
from supplytrace.workbench import Workbench, Mapping, parse_csv
from supplytrace.telemetry import Metrics

BASE=Path(__file__).resolve().parents[1]/'verification'/'reliability'

def make():return Workbench(Store(BASE/str(uuid4())))

def ready(wb):
    r=wb.create(b'Order,Qty\nA,1\nA,3\n','test.csv','utf-8-sig',',',str(uuid4()))
    end=time.monotonic()+10
    while wb.get(r['id'])['state']=='parsing' and time.monotonic()<end:time.sleep(.01)
    wb.analyze(r['id'],Mapping(keys=['Order'],quantity='Qty',expected_revision=0))
    while wb.get(r['id'])['state']=='analyzing' and time.monotonic()<end:time.sleep(.01)
    assert wb.get(r['id'])['state']=='ready'
    return r['id']

def test_connections_closed_after_context():
    store=Store(BASE/str(uuid4()))
    with store.connect() as db:db.execute('SELECT 1')
    with pytest.raises(sqlite3.ProgrammingError):db.execute('SELECT 1')

def test_queue_backpressure_cancel_and_stale_worker():
    wb=make();gate=threading.Event();wb.pool.submit(gate.wait,5)
    ids=[wb.create(b'A,B\n1,2\n',f'{i}.csv','utf-8-sig',',',str(uuid4()))['id'] for i in range(6)]
    with pytest.raises(WorkflowError) as e:wb.create(b'A,B\n1,2','more.csv','utf-8-sig',',',str(uuid4()))
    assert e.value.code=='queue_full'
    wb.cancel(ids[0]);assert wb.get(ids[0])['state']=='cancelled'
    with pytest.raises(WorkflowError) as full:wb.retry_parse(ids[0])
    assert full.value.code=='queue_full' and wb.get(ids[0])['state']=='cancelled'
    gate.set();end=time.monotonic()+10
    while any(wb.get(x)['state']=='parsing' for x in ids[1:]) and time.monotonic()<end:time.sleep(.01)
    wb.retry_parse(ids[0]);wb.pool.shutdown(wait=True)
    assert wb.get(ids[0])['state']=='draft' and wb.get(ids[0])['generation']==1
    with wb.store.connect() as db:assert db.execute('SELECT count(*) FROM import_rows WHERE import_id=?',(ids[0],)).fetchone()[0]==1

def test_export_singleflight_slot_released_on_error():
    wb=make();ident=ready(wb);wb.export_slots.acquire()
    try:
        with pytest.raises(WorkflowError) as e:wb.export(ident)
        assert e.value.code=='export_busy'
    finally:wb.export_slots.release()
    with pytest.raises(WorkflowError):wb.export('missing')
    assert wb.export(ident).startswith(b'PK')
    wb.pool.shutdown()

def test_old_generation_cannot_publish_or_fail_new_job():
    wb=make();ident=ready(wb)
    with wb.store.connect() as db:db.execute("UPDATE imports SET generation=2,state='analyzing' WHERE id=?",(ident,))
    wb.process(ident,1,{'keys':['Order'],'quantity':'Qty'})
    wb.fail(ident,'old_failure',generation=1)
    assert wb.get(ident)['state']=='analyzing'
    wb.process(ident,2,{'keys':['Order'],'quantity':'Qty'})
    assert wb.get(ident)['state']=='ready'
    wb.pool.shutdown()

def test_bounds_and_metrics():
    with pytest.raises(WorkflowError) as e:parse_csv(b'A,B\n'+b'1,2\n'*50001,'utf-8-sig',',')
    assert e.value.code=='row_limit'
    metric=Metrics()
    for _ in range(1000):metric.record(200,.001)
    assert metric.snapshot()['latency_window']==512 and metric.snapshot()['request_classes']=={'2xx':1000}

def test_read_memory_window_and_query_plans():
    wb=make();ident=ready(wb)
    tracemalloc.start()
    for _ in range(10):wb.get(ident);wb.list();wb.groups(ident)
    gc.collect();baseline=tracemalloc.get_traced_memory()[0]
    for _ in range(100):wb.get(ident);wb.list();wb.groups(ident)
    gc.collect();final,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    with wb.store.connect() as db:
        plans=[r[3] for r in db.execute('EXPLAIN QUERY PLAN SELECT * FROM import_rows WHERE import_id=? ORDER BY row_no LIMIT 30',(ident,))]
        list_plans=[r[3] for r in db.execute('EXPLAIN QUERY PLAN SELECT id FROM imports ORDER BY created_at DESC LIMIT 30')]
    record={'baseline_python_bytes':baseline,'final_python_bytes':final,'growth_bytes':final-baseline,'peak_python_bytes':peak,'iterations':100,'row_plan':plans,'list_plan':list_plans,'scope':'single-process Python allocations, not RSS or production capacity'}
    (BASE/'memory-and-plans.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
    assert final-baseline<2_000_000
    assert any('INDEX' in p for p in plans) and any('imports_created' in p for p in list_plans)
    wb.pool.shutdown()

def test_cancel_retry_cannot_grow_real_executor_queue():
    wb=make();gate=threading.Event();wb.pool.submit(gate.wait,5)
    ident=wb.create(b'A,B\n1,2\n','retry.csv','utf-8-sig',',',str(uuid4()))['id']
    accepted=0;rejected=False
    try:
        for _ in range(20):
            wb.cancel(ident)
            try:wb.retry_parse(ident);accepted+=1
            except WorkflowError as e:
                assert e.code=='queue_full';rejected=True;break
        pending=wb.pool._work_queue.qsize()
        assert rejected and accepted<=5 and pending<=6
    finally:
        gate.set();wb.pool.shutdown(wait=True)
