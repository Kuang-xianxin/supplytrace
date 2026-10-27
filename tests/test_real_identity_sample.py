import json
from copy import deepcopy
import pytest
from supplytrace.config import ROOT
from supplytrace.models import WorkflowError
from supplytrace.quality import validate_identities
from scripts.check_order_identity import evaluate


def test_authentic_invoice_rows_refuse_ambiguous_key_without_business_promise():
    sample=json.loads((ROOT/'data/real-samples/uci-order-identities.json').read_text(encoding='utf8'))
    original=deepcopy(sample)
    conflict,duplicate,unique=evaluate(sample)
    assert conflict['error']=='source_identity_conflict'
    assert conflict['data_quality']['conflicts'][0]['changed_fields']==['Quantity']
    assert conflict['evidence'][0]['source_row_numbers']==[115,127]
    assert duplicate['error']=='duplicate_evidence'
    assert unique['identity_status']=='unique_within_this_snapshot_only'
    assert all(r['business_decision_allowed'] is False and 'decision' not in r and r['ai']['status']=='not_requested' for r in (conflict,duplicate,unique))
    assert sample==original


@pytest.mark.parametrize('rows',[[{'quantity':3}],[{'id':7}],[{'id':''}],None])
def test_missing_or_invalid_identity_rejected(rows):
    with pytest.raises(WorkflowError,match='source_schema_invalid'):validate_identities('orders',rows)
