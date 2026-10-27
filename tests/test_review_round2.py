import json
import httpx
import pytest
from test_workflow import runtime, raw, request
from supplytrace.adapters import ZetarisSource
from supplytrace.config import Settings
from supplytrace.models import CheckRequest, WorkflowError
from supplytrace.planner import model_plan
from supplytrace.verifier import verify

@pytest.mark.parametrize('kind,code', [('close','zetaris_close_failed'),('shape','zetaris_invalid_response'),('pages','zetaris_invalid_response')])
def test_zetaris_invalid_lifecycle_fails_closed(runtime,kind,code):
    calls=[]
    def handler(req):
        calls.append(req.method)
        if req.method=='DELETE':return httpx.Response(500 if kind=='close' else 200)
        if kind=='shape':return httpx.Response(200,json=[])
        return httpx.Response(200,json={'queryToken':'review-q','totalCount':1,'totalPages':0 if kind=='pages' else 1,'pageNumber':1,'records':[{'id':'one'}]})
    source=ZetarisSource(Settings(runtime=runtime,zetaris_key='fixture',zetaris_org='42'),httpx.MockTransport(handler))
    with pytest.raises(WorkflowError,match=code):source.query('inventory','LAMP-01')
    if kind!='shape':assert calls[-1]=='DELETE'

def test_unicode_prompt_budget_rejected_before_model_call(runtime):
    calls=[]
    def handler(req):
        calls.append(True)
        return httpx.Response(500)
    with pytest.raises(WorkflowError,match='model_input_budget_exceeded'):
        model_plan(CheckRequest(**request(mode='ai',question='采购到货' * 300)),Settings(runtime=runtime,model='fixture'),httpx.MockTransport(handler))
    assert calls==[]

def test_prefix_shortage_not_erased_by_later_inbound():
    data=raw();data['orders'][0]['quantity']=75
    result=verify(CheckRequest(**request(promised_date='2026-10-28')),data)
    assert result['capacity']>=30 and result['verdict']=='review'
    assert any(x['code']=='existing_commitment_shortage' and x['date']=='2026-10-24' for x in result['issues'])

@pytest.mark.parametrize('day,capacity',[('2026-10-26',15),('2026-10-27',55)])
def test_arrival_requires_handling_and_buffer(day,capacity):
    result=verify(CheckRequest(**request(promised_date=day)),raw())
    assert result['capacity']==capacity
