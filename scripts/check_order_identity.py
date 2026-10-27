"""Run a bounded real-data identity experiment; never infer delivery promises.

Optional --persist records the actual local preflight outcome for the existing
history UI. It is explicitly distinct from /api/check and sponsor acceptance.
"""
import argparse,json,sys
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from supplytrace.adapters import digest
from supplytrace.config import ROOT
from supplytrace.models import WorkflowError
from supplytrace.quality import validate_identities
from supplytrace.storage import Store


def evaluate(sample):
    results=[]
    for case in sample['cases']:
        rows=[]
        for position,row in zip(case['source_row_numbers'],case['rows'],strict=True):
            if case['identity_strategy']=='invoice_product':identity=row['InvoiceNo']+'|'+row['StockCode']
            elif case['identity_strategy']=='source_location':identity='uci:'+sample['prefix_sha256'][:16]+':row:'+str(position)
            else:raise ValueError('Unsupported identity strategy')
            rows.append({'id':identity,**row})
        result={'operation':'identity_preflight_experiment','case':case['name'],
            'identity_strategy':case['identity_strategy'],'ai':{'status':'not_requested'},
            'official_platform_passed':False,'business_decision_allowed':False,
            'business_refusal':'Historical invoices have no current inventory, delivery due dates or supplier terms.',
            'evidence':[{'id':'Q1','tool':'orders','source':'uci-official-public-csv-prefix',
                'source_url':sample['source_url'],'retrieved_at':sample['retrieved_at'],
                'read_only':True,'complete':False,'scope':'selected authentic rows from bounded10000-row prefix, not complete order feed',
                'row_count':len(rows),'source_row_numbers':case['source_row_numbers'],
                'prefix_sha256':sample['prefix_sha256'],'sha256':digest(rows),'rows':rows,
                'mapping':'Candidate identity only; raw transaction fields retained. Not canonical ATP order rows.'}]}
        try:
            validate_identities('orders',rows)
            result['identity_status']='unique_within_this_snapshot_only'
        except WorkflowError as error:
            result.update(identity_status='rejected',error=error.code,data_quality=error.details)
        results.append(result)
    return results


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=ROOT/'data/real-samples/uci-order-identities.json')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--persist',action='store_true',help='Record first rejected real preflight in this project history (no model/platform calls)')
    args=parser.parse_args()
    sample=json.loads(args.input.read_text(encoding='utf8'))
    results=evaluate(sample)
    if args.persist:
        first=results[0]
        if first.get('identity_status')!='rejected':raise ValueError('Expected a refusal before persisting this experiment')
        store=Store(ROOT/'runtime')
        run_id=store.reserve({'operation':'identity_preflight_experiment','source_url':sample['source_url'],'identity_strategy':first['identity_strategy']},100)
        first['run_id']=run_id;store.finish(run_id,first,'failed')
    report={'at':datetime.now(timezone.utc).isoformat(),'kind':'real_public_data_local_identity_experiment',
        'fixture_sha256':digest(sample),'dataset_url':sample['metadata_url'],'results':results,
        'ai_called':False,'zetaris_called':False,'business_decisions_produced':0,'user_demand_validated':False}
    output=args.output or ROOT/'runtime'/('identity-real-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.json')
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'output':str(output),'cases':[{'name':r['case'],'identity_status':r['identity_status'],'error':r.get('error'),'run_id':r.get('run_id')} for r in results],'business_decisions_produced':0}))


if __name__=='__main__':main()
