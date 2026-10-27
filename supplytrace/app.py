import time
import sqlite3
from contextlib import asynccontextmanager
from fastapi import FastAPI,Request
from fastapi.responses import FileResponse,JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .config import ROOT,Settings
from .models import CheckRequest,ConflictReview,WorkflowError
from .storage import Store
from .planner import model_plan,rules_plan
from .adapters import LocalSource,ZetarisSource,read_evidence
from .verifier import verify
from .memory import MeterlessMemory
from .workbench import Workbench,router,MAX_BYTES
from .telemetry import Metrics
from .boundary import LocalBoundary
from .review_assistance import ReviewAssistant

def create_app(settings=None,model_transport=None,zetaris_transport=None):
    s=settings or Settings.from_env();store=Store(s.runtime);memory=MeterlessMemory(s.runtime)
    if s.backend not in ('synthetic','zetaris'):raise ValueError('Unknown DATA_BACKEND')
    if s.dataset_kind not in ('original_synthetic_demo','operator_supplied'):raise ValueError('Unknown DATASET_KIND')
    synthetic_data=s.backend=='synthetic' or s.dataset_kind=='original_synthetic_demo'
    source=LocalSource(store) if s.backend=='synthetic' else ZetarisSource(s,zetaris_transport)
    workbench=Workbench(store)
    @asynccontextmanager
    async def lifespan(application):
        workbench.start()
        try:
            yield
        finally:
            workbench.close()
    app=FastAPI(title='SupplyTrace',version='0.3.0',lifespan=lifespan)
    app.state.store=store;app.state.memory=memory;app.state.settings=s
    app.state.last_platform_read=None
    app.state.workbench=workbench
    metrics=Metrics()
    review_assistant=ReviewAssistant(workbench,s,model_transport)
    app.include_router(router(workbench,review_assistant))
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','testserver'])

    app.add_middleware(LocalBoundary,metrics=metrics)

    @app.exception_handler(WorkflowError)
    async def error_handler(request,exc):return JSONResponse({'error':exc.code},status_code=exc.status,headers={'Retry-After':'3'} if exc.status in (429,503) else {})

    @app.exception_handler(sqlite3.OperationalError)
    async def database_error(request,exc):
        full=getattr(exc,'sqlite_errorcode',None)==sqlite3.SQLITE_FULL
        return JSONResponse({'error':'disk_full' if full else 'storage_unavailable'},status_code=507 if full else 503,headers={'Retry-After':'3'})

    @app.exception_handler(OSError)
    async def filesystem_error(request,exc):
        full=exc.errno==28
        return JSONResponse({'error':'disk_full' if full else 'storage_unavailable'},status_code=507 if full else 503)

    @app.get('/')
    def index():return FileResponse(ROOT/'web/workbench.html')

    @app.get('/legacy')
    @app.get('/promises')
    def legacy():return FileResponse(ROOT/'web/index.html')

    @app.get('/health/live')
    def alive():return {'status':'alive'}

    @app.get('/api/workbench/metrics')
    def observation():return {**metrics.snapshot(),'cache':workbench.cache.snapshot(),'unfinished_jobs':len(workbench.futures),'worker_errors':workbench.worker_errors,'traces_dropped':workbench.tracing.exporter.dropped}

    @app.get('/health/ready')
    def ready():
        try:
            if workbench.supervisor is not None and not workbench.supervisor.is_alive():
                return JSONResponse({'status':'unavailable','dependency':'worker'},status_code=503)
            with store.connect() as db:
                db.execute('SELECT version FROM schema_migrations').fetchone()
            return {'status':'ready','scope':'local_single_user','schema_version':4}
        except Exception:
            return JSONResponse({'status':'unavailable'},status_code=503)

    @app.get('/static/workbench.css')
    def workbench_css():return FileResponse(ROOT/'web/workbench.css',media_type='text/css')

    @app.get('/static/workbench.js')
    def workbench_js():return FileResponse(ROOT/'web/workbench.js',media_type='application/javascript')

    @app.get('/static/translations.js')
    def translations_js():return FileResponse(ROOT/'web/translations.js',media_type='application/javascript')

    # Windows registry MIME mappings can report application/x-css, rejected by nosniff.
    @app.get('/static/style.css')
    def stylesheet():return FileResponse(ROOT/'web/style.css',media_type='text/css')

    @app.get('/static/app.js')
    def javascript():return FileResponse(ROOT/'web/app.js',media_type='application/javascript')

    @app.get('/api/status')
    def status():
        return {'project':'SupplyTrace','data_backend':source.name,'synthetic':synthetic_data,
            'dataset_kind':s.dataset_kind,'last_platform_read':app.state.last_platform_read,
            'model_configured':bool(s.model),'model':s.model or None,
            'meterless':'reference_engine_enabled' if s.meterless else 'disabled',
            'zetaris':'configured_not_yet_verified' if (s.zetaris_key and s.zetaris_org) or (s.zetaris_username and s.zetaris_password) else 'not_configured',
            'cursor':'not_used_by_this_codex_worker','nvidia':'not_verified',
            'daily_used':store.usage(),'daily_limit':s.daily_limit,'quota_clock':'UTC',
            'data_as_of':s.dataset_as_of or None}

    @app.get('/api/runs')
    def runs():return store.recent()

    @app.get('/api/runs/{run_id}')
    def run(run_id:str):return store.get(run_id)

    @app.get('/api/runs/{run_id}/review')
    def review(run_id:str):return store.review_state(run_id)

    @app.post('/api/runs/{run_id}/review')
    def annotate(run_id:str,body:ConflictReview):return store.add_review(run_id,body)

    @app.get('/api/runs/{run_id}/review-export')
    def review_export(run_id:str):
        return JSONResponse(store.review_export(run_id),headers={
            'Content-Disposition':'attachment; filename="supplytrace-conflict-review.json"'})

    @app.get('/api/memory/{sku}')
    def recall(sku:str):
        if not s.meterless:raise WorkflowError('meterless_disabled')
        return memory.call('recall',sku=sku,query=f'{sku} procurement promise capacity decision')

    @app.get('/api/memory-audit/{memory_id}')
    def audit(memory_id:str):return memory.call('audit',id=memory_id)

    @app.post('/api/check')
    def check(request:CheckRequest):
        run_id=store.reserve(request.model_dump(mode='json'),s.daily_limit);started=time.monotonic()
        traces=[];ai={'status':'not_requested'};memory_status={'status':'disabled'}
        try:
            if request.mode=='ai':plan,ai=model_plan(request,s,model_transport)
            else:plan=rules_plan(request)
            plan.authorize(request)
            rows,traces=read_evidence(source,plan,traces)
            if s.backend=='zetaris':app.state.last_platform_read=traces[-1]['retrieved_at']
            decision=verify(request,rows)
            if s.meterless:
                try:
                    previous=memory.call('recall',sku=request.sku,query=f'{request.sku} procurement promise capacity decision')
                    record=memory.call('remember',sku=request.sku,source=f'supplytrace:run:{run_id}',refs=decision['citations'],synthetic=synthetic_data,
                        content=f"{request.sku} procurement promise decision {decision['verdict']} quantity {request.quantity} deadline {request.promised_date} capacity {decision['capacity']}; snapshot {request.as_of}. Historical scenario only.")
                    memory_status={'status':'real_hmem_reference','id':record['id'],'source':record['source'],'prior_memories':previous,
                        'scope':'Historical context only; never reused as live stock or authority.'}
                except WorkflowError as e:memory_status={'status':'failed','error':e.code}
            result={'run_id':run_id,'request':request.model_dump(mode='json'),'decision':decision,'plan':plan.model_dump(),
                'ai':ai,'memory':memory_status,'evidence':traces,'data_source':source.name,'synthetic':synthetic_data,
                'dataset_kind':s.dataset_kind,'dataset_as_of':s.dataset_as_of or None,
                'usage':{'request_units':1,'query_units':len(traces),'model_usage':ai.get('usage'),'elapsed_ms':round((time.monotonic()-started)*1000,2)},
                'warning':'Local synthetic mode is not Zetaris integration or formal contest compliance.' if s.backend=='synthetic' else 'Live read-only adapter; source freshness and tenant schema must be verified.'}
            store.finish(run_id,result);return result
        except WorkflowError as e:
            if request.mode=='ai' and ai['status']=='not_requested':
                ai={'status':'failed','error':e.code,**(e.details or {})}
            result={'run_id':run_id,'error':e.code,'ai':ai,'evidence':traces,'usage':{'request_units':1,'elapsed_ms':round((time.monotonic()-started)*1000,2)}}
            if e.code in ('source_identity_conflict','duplicate_evidence') and e.details:
                result['data_quality']=e.details
            store.finish(run_id,result,'failed')
            return JSONResponse(result,status_code=e.status)
        except Exception:
            # Do not expose credential-bearing URLs, headers or SQL server error bodies.
            result={'run_id':run_id,'error':'internal_error','evidence':traces}
            store.finish(run_id,result,'failed');return JSONResponse(result,status_code=500)

    app.mount('/static',StaticFiles(directory=ROOT/'web'),name='static')
    return app

app=create_app()
