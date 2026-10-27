// Thin bridge to the unmodified, pinned Apache-2.0 Meterless reference.
import { HMEM } from '../vendor/meterless/engines/hmem/reference/src/index.ts';
import fs from 'node:fs';
const input=JSON.parse(fs.readFileSync(0,'utf8'));
if(!['remember','recall','audit'].includes(input.op)) throw new Error('operation_not_allowed');
const hmem=new HMEM({persistDir:input.persistDir,actor:'supplytrace-verifier'});
let result:unknown;
if(input.op==='remember'){
  const existing=hmem.store.all().find(m=>m.source===input.source);
  result=existing ?? hmem.add({content:input.content,type:'factual',layer:'working',confidence:0.8,
    source:input.source,chatId:input.sku,tags:['project:supplytrace','sku:'+input.sku,input.synthetic?'synthetic':'zetaris'],
    provenance:{origin:'plan_completion',learnedAt:Date.now(),label:'Deterministic procurement verification; historical, not current inventory',refs:input.refs}});
}else if(input.op==='recall'){
  result=hmem.query(input.query,{chatId:input.sku,topN:5,threshold:0.2});
}else result=hmem.ledger.history(input.id);
process.stdout.write(JSON.stringify(result));
