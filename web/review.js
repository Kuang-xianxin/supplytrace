// Local review annotations are separate from immutable source/model evidence.
let reviewState=null;const reviewDrafts={};
Object.assign(words.zh,{reviewTitle:'逐条记录处理意见',reviewScope:'仅记录核查意见。保留两行不等于确认两笔合法业务，不解除原核验失败；仍需来源负责人确认。',unreviewed:'尚未记录意见',unresolved:'仍未决 · 等待来源确认',retain_both:'保留两行 · 身份仍待确认',reviewer:'记录者（自填标识，未认证身份）',reviewNote:'判断依据 / 下一步',saveReview:'追加处理记录',reloadReview:'刷新处理历史',reviewHistory:'追加的处理历史（原文）',reviewSaved:'已保存；原始证据与拒绝结论未改变。',review_stale_reload_required:'处理记录已变化，请刷新历史后重新核对再保存。',reviewUnavailable:'处理记录暂时不可用',reviewExport:'导出原始证据与处理历史',reviewUnsaved:'未保存的输入不进入导出。切换语言会保留输入；刷新页面前请保存。'});
Object.assign(words.en,{reviewTitle:'Record a review for each conflict',reviewScope:'Annotations only. Retaining both rows does not confirm two valid business lines or release the failed check. Source-owner confirmation is still needed.',unreviewed:'No review recorded',unresolved:'Unresolved · Await source owner',retain_both:'Retain both · Identity unconfirmed',reviewer:'Recorder (self-declared, not authenticated)',reviewNote:'Rationale / next step',saveReview:'Append review record',reloadReview:'Refresh review history',reviewHistory:'Append-only review history (original text)',reviewSaved:'Saved; original evidence and refusal are unchanged.',review_stale_reload_required:'Review history changed. Refresh, inspect the current state, then save again.',reviewUnavailable:'Review history unavailable',reviewExport:'Export evidence and review history',reviewUnsaved:'Unsaved input is not exported. Language switching preserves input; save before reloading the page.'});

async function renderReviewPanel(result,refresh=false){
    const panel=$('#review-panel');if(!panel)return;
    const run=result.run_id;
    if(refresh||reviewState?.run_id!==run){
        panel.textContent=t('loading');
        try{const state=await api(`/api/runs/${encodeURIComponent(run)}/review`);if(last?.run_id!==run)return;reviewState=state}
        catch(e){panel.textContent=t(e.error||'reviewUnavailable');return}
    }
    if(last?.run_id!==run)return;
    const state=reviewState;
    panel.innerHTML=`<h3>${esc(t('reviewTitle'))}</h3><p>${esc(t('reviewScope'))}</p><p class="hint">${esc(t('reviewUnsaved'))}</p><button id="reload-review" type="button">${esc(t('reloadReview'))}</button>${state.conflicts.map((c,i)=>{
        const latest=state.events.filter(e=>e.conflict_index===i).at(-1);
        const draft=reviewDrafts[`${run}:${i}`]||{disposition:latest?.disposition||'unresolved',reviewer:'',note:''};
        return `<form class="conflict-review" data-conflict="${i}"><h4>${i+1}. ${esc(c.ref)} · ${esc(t(latest?.disposition||'unreviewed'))}</h4><p>${esc(t('sourceRows'))}: ${esc(c.row_numbers.join(', '))}</p>${conflictValues(result,result.data_quality,c)}<label for="review-status-${i}">${esc(t('reviewTitle'))}</label><select id="review-status-${i}" name="disposition">${['unresolved','retain_both'].map(v=>`<option value="${v}" ${draft.disposition===v?'selected':''}>${esc(t(v))}</option>`).join('')}</select><label for="reviewer-${i}">${esc(t('reviewer'))}</label><input id="reviewer-${i}" name="reviewer" maxlength="80" required value="${esc(draft.reviewer)}"><label for="review-note-${i}">${esc(t('reviewNote'))}</label><textarea id="review-note-${i}" name="note" maxlength="1500" required rows="3">${esc(draft.note)}</textarea><button type="submit">${esc(t('saveReview'))}</button><p role="status" class="hint"></p></form>`;
    }).join('')}${raw(t('reviewHistory'),state.events)}`;
    $('#reload-review').onclick=()=>renderReviewPanel(result,true);
    panel.querySelectorAll('form').forEach(form=>{
        const index=Number(form.dataset.conflict),key=`${run}:${index}`;
        const remember=()=>reviewDrafts[key]=Object.fromEntries(new FormData(form));
        form.oninput=remember;form.onchange=remember;
        form.onsubmit=async event=>{
            event.preventDefault();const draft=remember();const button=form.querySelector('button');button.disabled=true;
            try{
                const saved=await api(`/api/runs/${encodeURIComponent(run)}/review`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...draft,conflict_index:index,expected_revision:state.revision,evidence_sha256:state.evidence_sha256})});
                delete reviewDrafts[key];if(last?.run_id!==run)return;reviewState=saved;
                await renderReviewPanel(result);$('#review-panel').querySelector(`[data-conflict="${index}"] [role="status"]`).textContent=t('reviewSaved');
            }catch(e){form.querySelector('[role="status"]').textContent=t(e.error||'reviewUnavailable');button.disabled=false}
        };
    });
}

function exportReview(result){
    const link=document.createElement('a');
    link.href=`/api/runs/${encodeURIComponent(result.run_id)}/review-export`;
    link.download='supplytrace-conflict-review.json';link.click();
}
