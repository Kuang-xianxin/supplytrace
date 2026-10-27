from collections import defaultdict
from datetime import timedelta
from pydantic import ValidationError
from .models import ROW_MODELS,WorkflowError
from .quality import validate_identities

def verify(request,raw):
    data={};issues=[];excluded=[]
    try:
        for tool,cls in ROW_MODELS.items():
            data[tool]=[cls.model_validate(r) for r in raw[tool]]
            validate_identities(tool,raw[tool])
            for r in data[tool]:
                if r.sku!=request.sku:raise WorkflowError('source_scope_violation',422)
                age=(request.as_of-r.updated_at).days
                if age<0 or age>2:issues.append({'code':'stale_or_future_evidence','refs':[f'{tool}:{r.id}'],'age_days':age})
    except (ValidationError,KeyError,TypeError):raise WorkflowError('source_schema_invalid',422) from None
    if not data['inventory'] or len(data['supplier'])!=1:
        return {'verdict':'review','capacity':None,'shortfall':None,'issues':issues+[{'code':'missing_stock_or_unique_supplier_terms','refs':[]}],
                'excluded':[],'timeline':[],'citations':[],'earliest_date':None}
    terms=data['supplier'][0];safety=terms.safety_stock
    refs=lambda tool,rows:[f'{tool}:{r.id}' for r in rows]
    initial=sum(r.on_hand-r.damaged for r in data['inventory'])-safety
    events=defaultdict(lambda:{'inbound':0,'orders':0,'refs':[]})
    events[request.as_of];events[request.promised_date]
    all_refs=refs('inventory',data['inventory'])+refs('supplier',data['supplier'])
    for order in data['orders']:
        ref=f'orders:{order.id}'
        if order.status!='open':
            excluded.append({'ref':ref,'reason':'order_'+order.status});continue
        day=max(order.due_date,request.as_of)
        events[day]['orders']+=order.quantity;events[day]['refs'].append(ref);all_refs.append(ref)
    for shipment in data['shipments']:
        ref=f'shipments:{shipment.id}'
        if shipment.status!='confirmed':
            excluded.append({'ref':ref,'reason':'shipment_'+shipment.status});continue
        if shipment.supplier_id!=terms.supplier_id:
            issues.append({'code':'unknown_supplier_terms','refs':[ref]});continue
        if shipment.eta<request.as_of:
            issues.append({'code':'overdue_unreceived_shipment','refs':[ref]});continue
        available=shipment.eta+timedelta(days=terms.handling_days+terms.buffer_days)
        events[available]['inbound']+=shipment.quantity;events[available]['refs'].append(ref);all_refs.append(ref)
    balance=initial;timeline=[]
    for day,event in sorted(events.items()):
        balance+=event['inbound']-event['orders']
        timeline.append({'date':day.isoformat(),**event,'balance_before_new':balance,
            'balance_after_new':balance-(request.quantity if day>=request.promised_date else 0)})
        if balance<0:
            issues.append({'code':'existing_commitment_shortage','refs':event['refs'],'date':day.isoformat(),'shortfall':-balance})
    future=[r for r in timeline if r['date']>=request.promised_date.isoformat()]
    capacity=max(0,min(r['balance_before_new'] for r in future))
    verdict='review' if issues else ('can_promise' if request.quantity<=capacity else 'cannot_promise')
    earliest=None
    if not issues:
        for i,r in enumerate(timeline):
            if r['date']>=request.promised_date.isoformat() and min(x['balance_before_new'] for x in timeline[i:])>=request.quantity:
                earliest=r['date'];break
    return {'verdict':verdict,'capacity':capacity,'requested':request.quantity,'shortfall':max(0,request.quantity-capacity),
        'usable_stock':initial+safety,'safety_stock':safety,'issues':issues,'excluded':excluded,'timeline':timeline,
        'citations':all_refs,'earliest_date':earliest,'new_purchase_arrival_estimate':(request.as_of+timedelta(days=terms.lead_days+terms.handling_days+terms.buffer_days)).isoformat(),
        'method':'Minimum projected balance from promised date through all known future commitments; safety stock reserved. Existing shortages require review.',
        'scope':'Single warehouse pool; calendar days; all known open orders protected; confirmed inbound only; no purchase executed; future delivery is conditional on ETA accuracy.'}
