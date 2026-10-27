"""Local annotation workflow; no model or sponsor platform calls."""
import json
from copy import deepcopy
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from supplytrace.app import create_app
from supplytrace.config import ROOT,Settings
from supplytrace.storage import Store
from scripts.check_order_identity import evaluate


def setup_case():
    runtime=ROOT/'runtime/test-artifacts'/('conflict-review-'+str(uuid4()))
    app=create_app(Settings(runtime=runtime,meterless=False))
    sample=json.loads((ROOT/'data/real-samples/uci-order-identities.json').read_text(encoding='utf8'))
    result=evaluate(sample)[0]
    run=app.state.store.reserve({'operation':'review_test_on_real_public_rows'},100)
    result['run_id']=run;app.state.store.finish(run,result,'failed')
    return app,run,deepcopy(result)


def payload(state,**changes):
    return dict(conflict_index=0,expected_revision=state['revision'],evidence_sha256=state['evidence_sha256'],
        disposition='unresolved',reviewer='Automated test, not source owner',note='CSV 115/127: quantities 1 and 3 may be separate lines.',**changes)


def test_append_review_reopen_export_preserves_original_and_unresolved():
    app,run,original=setup_case();url=f'/api/runs/{run}/review'
    with TestClient(app) as client:
        state=client.get(url).json()
        first=client.post(url,json=payload(state));assert first.status_code==200
        second=payload(first.json());second.update(disposition='retain_both',note='保留原行；未取得来源负责人确认。')
        assert client.post(url,json=second).json()['revision']==2
        response=client.get(url+'-export')
        assert response.headers['content-disposition']=='attachment; filename="supplytrace-conflict-review.json"'
        export=response.json()
        assert export['original_run']['result']==original
        assert export['original_run']['status']=='failed'
        assert [e['disposition'] for e in export['review']['events']]==['unresolved','retain_both']
        assert export['review']['business_decision_allowed'] is False
        assert export['review']['events'][0]['conflict']['row_sha256']==original['data_quality']['conflicts'][0]['row_sha256']
        assert export['original_run']['result']['evidence'][0]['source_row_numbers']==[115,127]
    reopened=Store(app.state.settings.runtime)
    assert reopened.review_state(run)['events']==export['review']['events']
    assert reopened.get(run)['result']==original and 'decision' not in original


def test_stale_revision_and_wrong_evidence_rejected_without_append():
    app,run,_=setup_case();url=f'/api/runs/{run}/review'
    with TestClient(app) as client:
        state=client.get(url).json();body=payload(state)
        wrong=dict(body,evidence_sha256='0'*64)
        assert client.post(url,json=wrong).status_code==409
        assert client.post(url,json=body).status_code==200
        assert client.post(url,json=body).status_code==409
        assert client.get(url).json()['revision']==1


@pytest.mark.parametrize('change',[{'note':'   '},{'reviewer':'\n'},{'disposition':'fraud'},{'disposition':'deduplicate'},{'decision':{'can_promise':True}},{'conflict_index':True}])
def test_invalid_annotation_rejected(change):
    app,run,_=setup_case();url=f'/api/runs/{run}/review'
    with TestClient(app) as client:
        body=payload(client.get(url).json());body.update(change)
        assert client.post(url,json=body).status_code==422
        assert client.get(url).json()['events']==[]


def test_missing_conflict_and_cross_origin_rejected():
    app,run,_=setup_case();url=f'/api/runs/{run}/review'
    with TestClient(app) as client:
        body=payload(client.get(url).json());body['conflict_index']=99
        assert client.post(url,json=body).status_code==404
        body['conflict_index']=0
        assert client.post(url,json=body,headers={'origin':'https://unrelated.example'}).status_code==403
        assert client.get('/api/runs/missing/review-export').status_code==404
        assert client.get(url).json()['revision']==0


def test_each_pair_is_separate_and_no_review_implies_no_annotation():
    app,run,original=setup_case()
    original['data_quality']['conflicts'].append(deepcopy(original['data_quality']['conflicts'][0]))
    original['data_quality']['conflicts'][1]['row_numbers']=[1,3]
    original['evidence'][0]['rows'].append(deepcopy(original['evidence'][0]['rows'][1]))
    app.state.store.finish(run,original,'failed')
    with TestClient(app) as client:
        url=f'/api/runs/{run}/review';state=client.get(url).json()
        assert len(state['conflicts'])==2 and state['events']==[]
        body=payload(state);body['conflict_index']=1
        saved=client.post(url,json=body).json()
        assert [e['conflict_index'] for e in saved['events']]==[1]
        assert client.get(url+'-export').json()['original_run']['result']==original


def test_nonconflict_failure_has_no_annotation_workflow():
    app,run,_=setup_case();app.state.store.finish(run,{'error':'model_invalid_plan'},'failed')
    with TestClient(app) as client:
        assert client.get(f'/api/runs/{run}/review').status_code==409
