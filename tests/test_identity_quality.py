"""Synthetic boundary tests, NOT real Zetaris/model acceptance."""
import json
from copy import deepcopy
import httpx
import pytest
from fastapi.testclient import TestClient
from supplytrace.app import create_app
from supplytrace.config import ROOT, Settings
from supplytrace.models import CheckRequest, WorkflowError
from supplytrace.verifier import verify


def seed():
    raw=json.loads((ROOT/'data/synthetic.json').read_text(encoding='utf8'))
    return {k:[r for r in raw[k] if r['sku']=='LAMP-01'] for k in ('inventory','orders','shipments','supplier')}


def request():
    return CheckRequest(sku='LAMP-01',quantity=30,as_of='2026-10-22',promised_date='2026-10-28')


def test_conflicting_identity_is_actionable_before_arithmetic():
    raw=seed();other=deepcopy(raw['inventory'][0]);other['on_hand']+=9;raw['inventory'].append(other)
    original=deepcopy(raw)
    with pytest.raises(WorkflowError) as caught:verify(request(),raw)
    assert caught.value.code=='source_identity_conflict'
    quality=caught.value.details
    assert quality['tool']=='inventory' and quality['identity_key']=='id'
    conflict=quality['conflicts'][0]
    assert conflict['row_numbers']==[1,2] and conflict['changed_fields']==['on_hand']
    assert len(set(conflict['row_sha256']))==2 and conflict['kind']=='conflict'
    assert quality['resolution']=='source_owner_required' and quality['auto_resolved'] is False
    assert raw==original


def test_identical_replay_has_distinct_diagnosis():
    raw=seed();raw['orders'].append(deepcopy(raw['orders'][0]))
    with pytest.raises(WorkflowError) as caught:verify(request(),raw)
    assert caught.value.code=='duplicate_evidence'
    conflict=caught.value.details['conflicts'][0]
    assert conflict['kind']=='duplicate' and conflict['changed_fields']==[]
    assert len(set(conflict['row_sha256']))==1


def test_different_line_ids_are_not_merged_by_sku():
    raw=seed();other=deepcopy(raw['orders'][0]);other['id']='INDEPENDENT-LINE';raw['orders'].append(other)
    decision=verify(request(),raw)
    assert decision['capacity']==5 and decision['verdict']=='review'
    assert any(i['code']=='existing_commitment_shortage' for i in decision['issues'])
    assert 'orders:INDEPENDENT-LINE' in decision['citations']


def test_valid_reference_scenario_unchanged():
    decision=verify(request(),seed())
    assert decision['capacity']==55 and decision['verdict']=='can_promise'


def test_http_failure_persists_conflict_and_query_evidence():
    from uuid import uuid4
    runtime=ROOT/'runtime/test-artifacts'/str(uuid4())
    raw=seed();other=deepcopy(raw['inventory'][0]);other['on_hand']+=9;raw['inventory'].append(other)
    calls=[]
    def fixture_transport(req):
        calls.append(req.method)
        if req.method=='DELETE':return httpx.Response(200)
        sql=json.loads(req.content)['select'];tool=sql.split('supplytrace.')[1].split()[0]
        rows=raw[tool]
        return httpx.Response(200,json={'queryToken':'fixture-'+tool,'pageNumber':1,'totalPages':1,'totalCount':len(rows),'records':rows})
    settings=Settings(runtime=runtime,meterless=False,backend='zetaris',zetaris_org='42',zetaris_key='fixture-only')
    with TestClient(create_app(settings,zetaris_transport=httpx.MockTransport(fixture_transport))) as client:
        response=client.post('/api/check',json=request().model_dump(mode='json'))
        assert response.status_code==422
        result=response.json()
        assert result['error']=='source_identity_conflict' and 'decision' not in result
        assert result['data_quality']['conflicts'][0]['changed_fields']==['on_hand']
        trace=next(q for q in result['evidence'] if q['tool']=='inventory')
        assert trace['rows']==raw['inventory'] and trace['cursor']=='closed'
        saved=client.get('/api/runs/'+result['run_id']).json()
        assert saved['status']=='failed' and saved['result']==result
        assert result['ai']['status']=='not_requested' and calls.count('DELETE')==4
