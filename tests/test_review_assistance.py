"""Contract tests for evidence-bound UI assistance; model replies here are fixtures."""
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from supplytrace.app import create_app
from supplytrace.config import Settings
from test_workbench import ready, body


def setup(handler, **config):
    directory=Path(__file__).resolve().parents[1]/'verification'/'review-assistance'/uuid.uuid4().hex
    app=create_app(Settings(runtime=directory,meterless=False,model='fixture-local',**config),
                   model_transport=httpx.MockTransport(handler))
    return app, TestClient(app), directory


def response(request):
    payload=json.loads(request.content)
    source=json.loads(payload['messages'][-1]['content'])['evidence']
    numbers=[r['row_no'] for r in source['rows']]
    answer={'observations':[{'row_numbers':numbers,'field':'Quantity','text':'The displayed quantities differ.'}],
            'questions':['What are the original line-item identifiers for these rows?']}
    return httpx.Response(200,json={'model':'fixture-local','choices':[{'message':{'content':json.dumps(answer)}}]})


def test_assistance_is_cached_bound_to_source_and_does_not_review():
    calls=[]
    def handler(request):
        calls.append(request)
        return response(request)
    app,client,directory=setup(handler)
    with client:
        ident,state=ready(client)
        endpoint=f'/api/workbench/imports/{ident}/groups/1/investigate'
        request={'expected_revision':0,'locale':'en'}
        original=client.get(f'/api/workbench/imports/{ident}/original').content
        result=client.post(endpoint,json=request)
        assert result.status_code==200
        data=result.json()
        assert data['status']=='generated_unverified' and not data['review_modified']
        assert data['shown_rows']==data['group_rows']==2
        assert client.get(f'/api/workbench/imports/{ident}').json()['revision']==0
        assert client.get(f'/api/workbench/imports/{ident}/original').content==original
        assert client.post(endpoint,json=request).json()['cached'] and len(calls)==1
        assert client.post(f'/api/workbench/imports/{ident}/review',json=body()).status_code==200
        assert client.post(endpoint,json=request).status_code==409
        assert client.post(endpoint,json={**request,'expected_revision':1}).json()['cached']
        assert len(list((directory/'assistant-evidence').glob('*.json')))==1


def test_overlapping_call_is_rejected_until_real_inference_finishes():
    entered,release=threading.Event(),threading.Event()
    def handler(request):
        entered.set()
        assert release.wait(5)
        return response(request)
    app,client,_=setup(handler)
    with client:
        ident,_=ready(client)
        endpoint=f'/api/workbench/imports/{ident}/groups/1/investigate'
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(client.post,endpoint,json={'expected_revision':0})
            assert entered.wait(5)
            second=client.post(endpoint,json={'expected_revision':0})
            assert second.status_code==429 and second.json()['error']=='assistant_busy'
            release.set()
            assert first.result().status_code==200


def test_failed_attempt_is_preserved_and_does_not_modify_review():
    app,client,directory=setup(lambda request:httpx.Response(503),daily_limit=1)
    with client:
        ident,_=ready(client)
        endpoint=f'/api/workbench/imports/{ident}/groups/1/investigate'
        first=client.post(endpoint,json={'expected_revision':0})
        assert first.json()['error']=='model_http_error'
        assert len(list((directory/'assistant-evidence').glob('failed-*.json')))==1
        assert client.post(endpoint,json={'expected_revision':0}).status_code==429
        assert client.get(f'/api/workbench/imports/{ident}').json()['revision']==0


def test_wrong_row_citation_is_rejected_and_file_navigation_is_available():
    def handler(request):
        answer={'observations':[{'row_numbers':[9999],'field':'Quantity','text':'Unsupported row'}],'questions':['Which line?']}
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(answer)}}]})
    app,client,_=setup(handler)
    with client:
        ident,_=ready(client)
        r=client.post(f'/api/workbench/imports/{ident}/groups/1/investigate',json={'expected_revision':0})
        assert r.json()['error']=='invalid_model_output'
        assert client.get('/promises').status_code==200
        assert 'href="/promises"' in client.get('/').text
