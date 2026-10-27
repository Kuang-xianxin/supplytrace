"""Durable, bounded CSV review. Original input and review events are immutable."""
import csv
import io
import json
import re
import uuid
from typing import Literal

from fastapi import APIRouter, Request, Query
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from pydantic import Field, model_validator

from .models import Strict, WorkflowError
from .workspace import Workbench
from .csv_engine import MAX_BYTES, MAX_ROWS


class Mapping(Strict):
    keys: list[str] = Field(min_length=1, max_length=8)
    quantity: str = Field(min_length=1, max_length=200)
    expected_revision: int = Field(ge=0)

class CopyImport(Strict):
    request_id: str = Field(pattern=r'^[a-zA-Z0-9-]{8,80}$')


class Download(Strict):
    kind: Literal['original','export']


class Investigation(Strict):
    locale: Literal['zh', 'en'] = 'zh'
    offset: int = Field(default=0, ge=0, le=500000)
    expected_revision: int = Field(ge=0)


class Review(Strict):
    group_ids: list[int] = Field(min_length=1, max_length=100)
    disposition: str = Field(pattern=r'^(retain_all|follow_up|pending)$')
    reviewer: str = Field(min_length=1, max_length=80)
    note: str = Field(min_length=1, max_length=1500)
    expected_revision: int = Field(ge=0)
    request_id: str = Field(pattern=r'^[a-zA-Z0-9-]{8,80}$')

    @model_validator(mode='after')
    def text_required(self):
        if not self.note.strip() or not self.reviewer.strip():
            raise ValueError('nonblank_text_required')
        if len(set(self.group_ids)) != len(self.group_ids):
            raise ValueError('duplicate_group_selection')
        return self


def parse_csv(raw, encoding, delimiter):
    """Legacy small-input helper; service uses workspace.parse's streaming reader."""
    from .csv_engine import records
    iterator = records(io.BytesIO(raw), encoding, delimiter, max_rows=50000)
    headers = next(iterator)
    return headers, list(iterator)


def router(workbench, assistant=None):
    api = APIRouter(prefix='/api/workbench')

    @api.post('/example')
    def example():
        from .config import ROOT
        sample = json.loads((ROOT/'data/real-samples/uci-order-identities.json').read_text(encoding='utf-8'))
        data = io.StringIO(newline='')
        headers = list(sample['cases'][0]['rows'][0])
        writer = csv.writer(data);writer.writerow(headers+['UCI_source_CSV_line'])
        for case in sample['cases'][:2]:
            for source_line, row in zip(case['source_row_numbers'], case['rows']):
                writer.writerow([row[h] for h in headers]+[source_line])
        return workbench.create(data.getvalue().encode('utf-8'), 'UCI-Online-Retail-example.csv',
                               'utf-8-sig', ',', str(uuid.uuid4()), 'uci_example')

    @api.get('/imports')
    def history(q: str = Query('', max_length=200), offset: int = Query(0, ge=0, le=500000)):
        return workbench.list(q, offset)

    @api.post('/imports')
    async def upload(request: Request, filename: str = Query('upload.csv', max_length=250), encoding: str = 'utf-8-sig', delimiter: str = ','):
        if not workbench.upload_slots.acquire(blocking=False):
            raise WorkflowError('queue_full', 429)
        try:
            path = workbench.new_upload()
            size = 0
            with path.open('xb') as target:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise WorkflowError('file_size', 413)
                    await run_in_threadpool(target.write, chunk)
                await run_in_threadpool(target.flush)
            return await run_in_threadpool(workbench.create_file, path, filename, encoding, delimiter, request.headers.get('idempotency-key', ''))
        finally:
            workbench.upload_slots.release()

    @api.get('/imports/{ident}')
    def get(ident: str):
        return workbench.get(ident)

    @api.post('/imports/{ident}/copy')
    def recheck(ident: str, body: CopyImport):
        return workbench.copy(ident, body.request_id)

    @api.post('/imports/{ident}/analyze')
    def analyze(ident: str, body: Mapping):
        return workbench.analyze(ident, body)

    @api.post('/imports/{ident}/retry')
    def retry(ident: str):
        return workbench.retry_parse(ident)

    @api.post('/imports/{ident}/cancel')
    def cancel(ident: str):
        return workbench.cancel(ident)

    @api.get('/imports/{ident}/groups')
    def groups(ident: str, kind: str = '', status: str = '', q: str = Query('', max_length=200), offset: int = Query(0, ge=0, le=500000)):
        return workbench.groups(ident, kind, status, q, offset)

    @api.get('/imports/{ident}/groups/{gid}')
    def detail(ident: str, gid: int, offset: int = Query(0, ge=0, le=500000)):
        return workbench.detail(ident, gid, offset)

    @api.post('/imports/{ident}/groups/{gid}/investigate')
    def investigate(ident: str, gid: int, body: Investigation):
        if assistant is None:
            raise WorkflowError('model_not_configured', 503)
        return assistant.investigate(ident, gid, body)

    @api.get('/imports/{ident}/issues')
    def issues(ident: str, offset: int = Query(0, ge=0, le=500000)):
        return workbench.issues(ident, offset)

    @api.post('/imports/{ident}/review')
    def review(ident: str, body: Review):
        return workbench.review(ident, body)

    @api.get('/imports/{ident}/groups/{gid}/events')
    def events(ident: str, gid: int, offset: int = Query(0, ge=0, le=20000)):
        return workbench.events(ident, gid, offset)

    @api.get('/imports/{ident}/original')
    def original(ident: str):
        return FileResponse(workbench.original_file(ident),media_type='application/octet-stream',filename=f'original-{ident}.csv')

    @api.post('/imports/{ident}/download')
    def prepare_download(ident: str, body: Download):
        path = workbench.original_file(ident) if body.kind=='original' else workbench.export_file(ident)
        return {'url':f'/api/workbench/downloads/{path.name}', 'bytes':path.stat().st_size}

    @api.get('/downloads/{filename}')
    def download_artifact(filename: str):
        match = re.fullmatch(r'(?:export-)?([0-9a-f-]{36})(?:-\d+-\d+\.zip|\.original\.csv)',filename)
        if not match:
            raise WorkflowError('import_not_found',404)
        with workbench.store.connect() as db:
            workbench.item(db,match[1])
        path = workbench.artifacts/filename
        if not path.is_file():
            raise WorkflowError('import_not_found',404)
        return FileResponse(path,media_type='application/octet-stream',filename=filename)

    @api.get('/imports/{ident}/export')
    def export(ident: str):
        return FileResponse(workbench.export_file(ident), media_type='application/zip', filename=f'supplytrace-{ident}.zip')

    return api
