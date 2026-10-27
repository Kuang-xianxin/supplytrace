import hashlib, json, re, time, uuid
from urllib.parse import quote, urlparse
import httpx
from .models import TOOLS, ROW_MODELS, WorkflowError
from .storage import now

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

class LocalSource:
    name='synthetic-sqlite'
    def __init__(self,store):self.store=store
    def query(self,tool,sku):
        if tool not in TOOLS:raise WorkflowError('tool_not_allowed',422)
        sql='SELECT body FROM source_rows WHERE tool = ? AND sku = ? ORDER BY id'
        # SQLite enforces read-only at the connection, in addition to parameterized queries.
        with self.store.connect() as db:
            db.execute('PRAGMA query_only=ON')
            rows=[json.loads(r[0]) for r in db.execute(sql,(tool,sku))]
        return rows,{'query':sql,'parameters':[tool,sku],'read_only':True,'complete':True}

class ZetarisSource:
    name='zetaris-rest'
    def __init__(self,settings,transport=None,*,page_limit=100):
        if type(page_limit)!=int or not 1<=page_limit<=100:raise ValueError('page_limit must be an integer from 1 to 100')
        self.s,self.transport,self.page_limit=settings,transport,page_limit
    def query(self,tool,sku):
        s=self.s
        if not (s.zetaris_key and s.zetaris_org) and not (s.zetaris_username and s.zetaris_password):
            raise WorkflowError('zetaris_not_configured')
        if s.zetaris_prefix not in ('/api/v1.0','/api/proxy'):
            raise WorkflowError('zetaris_invalid_endpoint',422)
        endpoint=urlparse(s.zetaris_base)
        if endpoint.username or endpoint.password or endpoint.path not in ('','/') or endpoint.query or endpoint.fragment or not (
            endpoint.scheme=='https' or endpoint.scheme=='http' and endpoint.hostname in ('127.0.0.1','localhost','::1')):
            raise WorkflowError('zetaris_invalid_endpoint',422)
        if tool not in TOOLS or not re.fullmatch(r'[A-Z0-9][A-Z0-9_-]{0,39}',sku):raise WorkflowError('tool_not_allowed',422)
        if not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)*',s.zetaris_catalog):
            raise WorkflowError('zetaris_invalid_catalog',422)
        # SQL is generated ONLY by trusted code. No model SQL or arbitrary column/table names.
        sql=f"SELECT * FROM {s.zetaris_catalog}.{tool} WHERE sku = '{sku}' ORDER BY id"
        token=None;close_status=None
        try:
            with httpx.Client(base_url=s.zetaris_base.rstrip('/'),timeout=30,transport=self.transport,trust_env=False) as client:
                key,org=s.zetaris_key,s.zetaris_org
                if not key:
                    login=client.post('/api/auth/login',json={'username':s.zetaris_username,'password':s.zetaris_password})
                    self.check_status(login)
                    payload=login.json()
                    if not isinstance(payload,dict):raise WorkflowError('zetaris_invalid_response')
                    key=payload.get('idToken') or payload.get('token') or payload.get('accessToken')
                    org=str(payload.get('orgId',''))
                if not isinstance(key,str) or not key:raise WorkflowError('zetaris_auth_failed')
                if not org.isdigit():raise WorkflowError('zetaris_invalid_org',422)
                client.headers.update({'Authorization':f'Bearer {key}','X-Org-ID':org,'X-Request-ID':str(uuid.uuid4())})
                query_path=s.zetaris_prefix+'/query/sql'
                try:
                    res=client.post(query_path+'/start',json={'select':sql,'pageLimit':self.page_limit})
                    self.check_status(res)
                    first=res.json()
                    if not isinstance(first,dict):raise WorkflowError('zetaris_invalid_response')
                    candidate=first.get('queryToken')
                    if not isinstance(candidate,str) or not candidate:raise WorkflowError('zetaris_invalid_response')
                    token=candidate
                    total,pages,start=first['totalCount'],first['totalPages'],first['pageNumber']
                    if any(type(x)!=int for x in (total,pages,start)) or total<0 or pages<0 or start not in (0,1):
                        raise WorkflowError('zetaris_invalid_response')
                    if total>500 or pages>5:raise WorkflowError('zetaris_result_limit')
                    if total>0 and pages==0:raise WorkflowError('zetaris_invalid_response')
                    rows=first.get('records',[])
                    if not isinstance(rows,list):raise WorkflowError('zetaris_invalid_response')
                    for page in range(start+1,start+pages):
                        r=client.get(query_path+'/page',params={'queryToken':token,'pageLimit':self.page_limit,'pageNumber':page})
                        self.check_status(r);payload=r.json()
                        if not isinstance(payload,dict):raise WorkflowError('zetaris_invalid_response')
                        if payload.get('queryToken')!=token or payload.get('pageNumber')!=page or payload.get('totalCount')!=total or payload.get('totalPages')!=pages:
                            raise WorkflowError('zetaris_snapshot_changed')
                        batch=payload.get('records',[])
                        if not isinstance(batch,list):raise WorkflowError('zetaris_invalid_response')
                        rows.extend(batch)
                    if len(rows)!=total:raise WorkflowError('zetaris_incomplete_results')
                finally:
                    if token:
                        try:
                            r=client.delete(query_path+'/close/'+quote(token,safe=''))
                            close_status='closed' if r.status_code in (200,204) else 'close_failed'
                        except httpx.HTTPError:close_status='close_failed'
                if close_status!='closed':raise WorkflowError('zetaris_close_failed')
        except httpx.TimeoutException:raise WorkflowError('zetaris_timeout') from None
        except httpx.HTTPError:raise WorkflowError('zetaris_network_error') from None
        except (ValueError,KeyError,TypeError):raise WorkflowError('zetaris_invalid_response') from None
        # Zetaris' query API serializes scalar values as strings. Convert only
        # declared integer columns; reject fractional, malformed and missing values.
        integer_columns=[k for k,v in ROW_MODELS[tool].model_fields.items() if v.annotation is int]
        normalized=[]
        for row in rows:
            if not isinstance(row,dict):raise WorkflowError('zetaris_invalid_response')
            converted=dict(row)
            for column in integer_columns:
                value=converted.get(column)
                if isinstance(value,str) and re.fullmatch(r'-?\d+',value):converted[column]=int(value)
                elif type(value) is not int:raise WorkflowError('source_schema_invalid',422)
            normalized.append(converted)
        return normalized,{'query':sql,'parameters':[],'read_only':True,'complete':True,'pages':pages,'page_limit':self.page_limit,'cursor':close_status,'platform_records':rows,
                     'integer_conversion':'Declared integer fields only; original scalar types are platform JSON strings or integers.',
                     'snapshot':'sequential source reads; not an atomic cross-source snapshot'}

    @staticmethod
    def check_status(response):
        if response.status_code in (402,429):raise WorkflowError('zetaris_quota_exceeded',429)
        if response.status_code in (401,403):raise WorkflowError('zetaris_auth_failed')
        if not response.is_success:raise WorkflowError('zetaris_http_error')

def read_evidence(source,plan,traces=None):
    rows={};traces=traces if traces is not None else []
    for call in plan.queries:
        start=time.monotonic()
        try:values,query=source.query(call.tool,call.sku)
        except WorkflowError as e:
            traces.append({'id':f'Q{len(traces)+1}','tool':call.tool,'source':source.name,'complete':False,'error':e.code,'retrieved_at':now()})
            raise
        if len(values)>500:raise WorkflowError('source_result_limit')
        trace={'id':f'Q{len(traces)+1}','tool':call.tool,'source':source.name,'retrieved_at':now(),
               'elapsed_ms':round((time.monotonic()-start)*1000,2),'row_count':len(values),'sha256':digest(values),
               **query,'rows':values}
        traces.append(trace);rows[call.tool]=values
    return rows,traces
