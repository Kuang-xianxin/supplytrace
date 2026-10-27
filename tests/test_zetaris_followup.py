import json
import shutil
from pathlib import Path

import httpx
import pytest

from test_workflow import runtime
from scripts.verify_zetaris import audit_imports
from supplytrace.adapters import ZetarisSource
from supplytrace.config import Settings


def test_zetaris_small_pages_keep_limit_and_close(runtime):
    calls = []

    def handler(req):
        calls.append(req.method)
        if req.method == 'DELETE':
            return httpx.Response(200)
        if req.method == 'POST':
            assert json.loads(req.content)['pageLimit'] == 1
            page = 1
        else:
            assert req.url.params['pageLimit'] == '1'
            page = int(req.url.params['pageNumber'])
        return httpx.Response(200, json={'queryToken': 'explicit-test-fixture', 'pageNumber': page,
                                        'totalPages': 3, 'totalCount': 3, 'records': [{'id': f'row-{page}'}]})

    source = ZetarisSource(Settings(runtime=runtime, zetaris_org='42', zetaris_key='fixture'),
                           httpx.MockTransport(handler), page_limit=1)
    rows, info = source.query('orders', 'LAMP-01')
    assert [r['id'] for r in rows] == ['row-1', 'row-2', 'row-3']
    assert calls == ['POST', 'GET', 'GET', 'DELETE']
    assert info['page_limit'] == 1 and info['cursor'] == 'closed'


@pytest.mark.parametrize('limit', [0, 101, True, 1.5])
def test_zetaris_rejects_unsafe_page_size(limit):
    with pytest.raises(ValueError, match='page_limit'):
        ZetarisSource(Settings(), page_limit=limit)


def test_zetaris_import_audit_detects_modified_csv(runtime):
    source = Path(__file__).resolve().parents[1] / 'data'
    target = runtime / 'data'
    (target / 'zetaris-import').mkdir(parents=True)
    shutil.copy2(source / 'synthetic.json', target / 'synthetic.json')
    for csv in (source / 'zetaris-import').glob('*.csv'):
        shutil.copy2(csv, target / 'zetaris-import' / csv.name)
    _, manifest = audit_imports(target)
    assert sum(item['rows'] for item in manifest) == 14
    inventory = target / 'zetaris-import' / 'inventory.csv'
    original = inventory.read_text(encoding='utf8')
    inventory.write_text(original.replace('INV-101', 'INV-CHANGED'), encoding='utf8')
    with pytest.raises(ValueError, match='does not match'):
        audit_imports(target)
