import csv
import hashlib
import io
import json
import sqlite3
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from supplytrace.app import create_app
from supplytrace.config import ROOT, Settings
from supplytrace.workbench import parse_csv, Workbench
from supplytrace.storage import Store
from supplytrace.assistant import suggest
from scripts.database import backup

ARTIFACTS=ROOT/'verification'/'test-artifacts'

def setup():
    path=ARTIFACTS/str(uuid.uuid4());app=create_app(Settings(runtime=path,meterless=False))
    return app,TestClient(app),path

def wait(client,ident,states=('draft','failed')):
    end=time.monotonic()+10
    while time.monotonic()<end:
        data=client.get('/api/workbench/imports/'+ident).json()
        if data['state'] in states:return data
        time.sleep(.01)
    raise AssertionError('Background processing timed out')

def upload(client,raw,**params):
    return client.post('/api/workbench/imports',params={'filename':'采购.csv',**params},content=raw,
        headers={'Idempotency-Key':str(uuid.uuid4()),'Content-Type':'text/csv'})

def ready(client,raw=b'Order,SKU,Quantity,Note\r\nA,S,1,first\r\nA,S,3,second\r\nB,T,2,ok\r\nB,T,2,ok\r\nC,U,4,only\r\n'):
    r=upload(client,raw);assert r.status_code==200
    ident=r.json()['id'];wait(client,ident)
    r=client.post(f'/api/workbench/imports/{ident}/analyze',json={'keys':['Order','SKU'],'quantity':'Quantity','expected_revision':0});assert r.status_code==200
    return ident,wait(client,ident,('ready','failed'))

def body(groups=[1],**kw):
    return {'group_ids':groups,'disposition':'retain_all','reviewer':'QA tester','note':'May be legitimate split lines. Retain originals.','expected_revision':0,'request_id':str(uuid.uuid4()),**kw}

def test_full_import_review_export_roundtrip():
    app,c,path=setup();ident,state=ready(c);assert state['state']=='ready';assert state['group_count']==2
    groups=c.get(f'/api/workbench/imports/{ident}/groups').json()['items']
    assert [g['kind'] for g in groups]==['quantity_difference','identical_content']
    original=c.get(f'/api/workbench/imports/{ident}/original').content
    assert c.post(f'/api/workbench/imports/{ident}/review',json=body()).status_code==200
    z=zipfile.ZipFile(io.BytesIO(c.get(f'/api/workbench/imports/{ident}/export').content))
    assert z.read('original.csv')==original
    assert len(json.loads(z.read('evidence.json'))['rows'])==5
    meta=json.loads(z.read('manifest.json'));assert meta['pending_groups']==1
    assert meta['source_rows_modified'] is False and meta['business_decision_allowed'] is False
    assert meta['import']['sha256']==hashlib.sha256(original).hexdigest()
    reopened=Workbench(Store(path));assert reopened.get(ident)['reviewed_count']==1
    assert reopened.get(ident)['events'][0]['note']=='May be legitimate split lines. Retain originals.'

def test_atomic_bulk_review_cas_and_idempotency():
    app,c,_=setup();ident,_=ready(c);url=f'/api/workbench/imports/{ident}/review';b=body([1,2])
    assert c.post(url,json=b).json()['revision']==1
    assert c.post(url,json=b).json()['replayed'] is True
    assert c.post(url,json={**b,'note':'changed'}).status_code==409
    assert c.post(url,json=body()).status_code==409
    assert c.post(url,json=body([1,99],expected_revision=1)).status_code==404
    assert c.get('/api/workbench/imports/'+ident).json()['revision']==1

def test_concurrent_review_has_one_winner():
    app,c,_=setup();ident,_=ready(c)
    def save(n):return c.post(f'/api/workbench/imports/{ident}/review',json=body(note=str(n))).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(save,[1,2]))
    assert sorted(results)==[200,409]

@pytest.mark.parametrize('data,code',[(b'A,A\n1,2\n','invalid_headers'),(b'A,B\n','empty_csv'),(b'A,B\n"oops,2\n','malformed_csv'),(b'A,B\n\x00,2','binary_file'),(b'A,B\n\xff,2','encoding_error')])
def test_invalid_files_retained(data,code):
    app,c,_=setup();r=upload(c,data);ident=r.json()['id'];state=wait(c,ident)
    assert state['state']=='failed' and state['error']==code
    assert c.get(f'/api/workbench/imports/{ident}/original').content==data

def test_multiline_and_unicode_physical_locations():
    headers,rows=parse_csv('订单,数量,备注\r\nA,1,"第一行\n第二行"\r\nA,3,完成\r\n'.encode('gb18030'),'gb18030',',')
    assert headers==['订单','数量','备注'];assert [(x['line_start'],x['line_end']) for x in rows]==[(2,3),(4,4)]
    assert rows[0]['cells'][2]=='第一行\n第二行'

def test_issues_negative_quantity_and_formula_export():
    app,c,_=setup();raw=b'Order,SKU,Quantity,Note\nA,S,-2,=1+1\nA,S,3,@x\n,T,4,blank\nB,T,sNaN,bad\nC,U,4\n'
    ident,state=ready(c,raw);assert state['state']=='ready';assert state['error_count']==3
    issues=c.get(f'/api/workbench/imports/{ident}/issues').json()
    assert {i['code'] for i in issues['items']}=={'column_count','blank_key','invalid_quantity'}
    z=zipfile.ZipFile(io.BytesIO(c.get(f'/api/workbench/imports/{ident}/export').content))
    assert z.read('original.csv')==raw
    derived=z.read('reviewed-rows.csv').decode('utf-8-sig');assert "'=1+1" in derived and "'-2" in derived

def test_many_rows_pagination_and_no_pair_explosion():
    app,c,_=setup();raw=('Order,SKU,Quantity\n'+'A,S,1\n'*85).encode();ident,state=ready(c,raw)
    assert state['group_count']==1
    first=c.get(f'/api/workbench/imports/{ident}/groups/1').json();last=c.get(f'/api/workbench/imports/{ident}/groups/1?offset=60').json()
    assert first['total']==85 and len(first['rows'])==30 and len(last['rows'])==25
    assert last['rows'][-1]['line_start']==86

def test_upload_idempotency_and_boundary():
    app,c,_=setup();key=str(uuid.uuid4());headers={'Idempotency-Key':key};raw=b'A,B\n1,2\n'
    a=c.post('/api/workbench/imports',content=raw,headers=headers)
    b=c.post('/api/workbench/imports',content=raw,headers=headers)
    assert a.json()['id']==b.json()['id']
    assert c.post('/api/workbench/imports',content=raw+b'3,4\n',headers=headers).status_code==409
    assert c.post('/api/workbench/imports',content=raw,headers={**headers,'Origin':'https://untrusted.example'}).status_code==403
    assert c.post('/api/workbench/imports',content=b'x',headers={**headers,'Content-Length':'bad'}).status_code==400

def test_backup_restore_preserves_old_runs_and_imports():
    app,c,path=setup();ident,_=ready(c)
    old=app.state.store.reserve({'old':'run'},100);app.state.store.finish(old,{'preserve':'evidence'},'failed')
    dest=ARTIFACTS/str(uuid.uuid4())/'backup.sqlite3';restored=ARTIFACTS/str(uuid.uuid4())/'supplytrace.sqlite3'
    result=backup(path/'supplytrace.sqlite3',dest);assert result['integrity_check']=='ok'
    backup(dest,restored);store=Store(restored.parent);wb=Workbench(store)
    assert store.get(old)['result']=={'preserve':'evidence'} and wb.get(ident)['row_count']==5
    assert len(wb.export(ident))>100
    with pytest.raises(ValueError):backup(dest,restored)

def test_interruption_is_recoverable_and_no_old_runs_changed():
    app,c,path=setup();ident,state=ready(c)
    # The original process must finish its durable job bookkeeping before we
    # simulate an interrupted old-format import in another workspace instance.
    app.state.workbench.close()
    with app.state.store.connect() as db:db.execute("UPDATE imports SET state='analyzing' WHERE id=?",(ident,))
    wb=Workbench(Store(path));assert wb.get(ident)['state']=='interrupted'
    from supplytrace.workbench import Mapping
    wb.analyze(ident,Mapping(keys=['Order','SKU'],quantity='Quantity',expected_revision=0))
    wb.pool.shutdown(wait=True)
    assert wb.get(ident)['state']=='ready' and wb.get(ident)['generation']==2

def test_model_adapter_refuses_remote_and_invalid_citations():
    import httpx
    evidence={'headers':['Q'],'rows':[{'row_no':2,'cells':['1']}]}
    with pytest.raises(ValueError):suggest('https://paid.example/v1','x',evidence)
    transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'observations':[{'row_numbers':[99],'field':'Q','text':'x'}],'questions':[]})}}]}))
    rejected=suggest('http://127.0.0.1:9999/v1','test',evidence,transport=transport)
    assert rejected['error']=='invalid_model_output'
    assert json.loads(rejected['raw_output'])['observations'][0]['row_numbers']==[99]

def test_credentials_not_in_repr():
    value=Settings(api_key='secret-sentinel',zetaris_key='sponsor-sentinel')
    assert 'secret-sentinel' not in repr(value) and 'sponsor-sentinel' not in repr(value)

def test_new_field_batch_preserves_original_and_review():
    app,c,_=setup();ident,_=ready(c)
    c.post(f'/api/workbench/imports/{ident}/review',json=body()).raise_for_status()
    key=str(uuid.uuid4())
    copy=c.post(f'/api/workbench/imports/{ident}/copy',json={'request_id':key}).json()
    repeat=c.post(f'/api/workbench/imports/{ident}/copy',json={'request_id':key}).json()
    assert copy['id']==repeat['id'] and copy['id']!=ident
    assert wait(c,copy['id'])['state']=='draft'
    assert c.get(f'/api/workbench/imports/{ident}').json()['revision']==1
    assert c.get(f'/api/workbench/imports/{copy["id"]}/original').content==c.get(f'/api/workbench/imports/{ident}/original').content
