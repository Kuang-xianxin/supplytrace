import json,re,uuid
import httpx
from fastapi.testclient import TestClient
from supplytrace.app import create_app
from supplytrace.config import ROOT,Settings

def test_platform_transport_does_not_turn_synthetic_fixture_into_customer_data():
    seed=json.loads((ROOT/'data/synthetic.json').read_text(encoding='utf8'))
    runtime=ROOT/'runtime/test-artifacts'/str(uuid.uuid4())
    def platform(request):
        if request.method=='DELETE':return httpx.Response(204)
        table=re.search(r'FROM supplytrace\.(\w+)',json.loads(request.content)['select']).group(1)
        rows=[r for r in seed[table] if r['sku']=='LAMP-01']
        return httpx.Response(200,json={'queryToken':'fixture','totalCount':len(rows),'totalPages':1,'pageNumber':0,'records':rows})
    settings=Settings(runtime=runtime,backend='zetaris',zetaris_key='fixture',zetaris_org='42',meterless=False)
    with TestClient(create_app(settings,zetaris_transport=httpx.MockTransport(platform))) as client:
        assert client.get('/api/status').json()['last_platform_read'] is None
        result=client.post('/api/check',json={'sku':'LAMP-01','quantity':30,'as_of':'2026-10-22','promised_date':'2026-10-28','mode':'rules'}).json()
        assert result['data_source']=='zetaris-rest' and result['synthetic'] is True
        assert result['dataset_kind']=='original_synthetic_demo'
        assert result['decision']['capacity']==55
        assert client.get('/api/status').json()['last_platform_read']==result['evidence'][-1]['retrieved_at']
