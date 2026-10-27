const $ = (s) => document.querySelector(s);
const main = $("#main");
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
let lang = localStorage.getItem("st-language") || "zh";
import { words } from "./translations.js";
const t = (k) => words[k]?.[lang === "zh" ? 0 : 1] || k;
const date = (x) => new Date(x).toLocaleString(lang === "zh" ? "zh-CN" : "en-GB", { dateStyle: "medium", timeStyle: "short" });
const icon = `<svg viewBox="0 0 48 48" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M13 5h16l8 8v30H13zM29 5v10h8M19 23h12M19 30h12M19 37h8"/></svg>`;
let current = null, groups = null, detail = null, selected = /* @__PURE__ */ new Set(), timer = null, renderId = 0, historyOffset = 0, groupOffset = 0, detailOffset = 0, issueOffset = 0;
let filters = { kind: "", status: "", q: "" }, historyQuery = "", uploadQueue = [], uploadBusy = false, saving = false, showBulk = false, issuesOpen = false;
function badge(k) {
  return `<span class="badge ${["quantity_difference", "pending", "follow_up", "interrupted"].includes(k) ? "warning" : k === "failed" ? "error" : ["parsing", "draft", "multiple_rows"].includes(k) ? "neutral" : ""}">${esc(t(k))}</span>`;
}
function toast(k) {
  $("#toast").textContent = t(k);
  setTimeout(() => $("#toast").textContent = "", 3500);
}
function translateShell() {
  document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
  document.querySelectorAll("[data-i]").forEach((n) => n.textContent = t(n.dataset.i));
  $("#language").textContent = lang === "zh" ? "English" : "中文";
}
async function api(path, options = {}) {
  try {
    const res = await fetch("/api/workbench" + path, { ...options, headers: { ...options.body ? { "Content-Type": "application/json" } : {}, ...options.headers } });
    const data = await res.json();
    if (!res.ok) {
      const e = new Error(data.error || "unknownError");
      e.code = data.error || "unknownError";
      throw e;
    }
    return data;
  } catch (e) {
    if (!e.code) e.code = "network";
    throw e;
  }
}
const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body) });
async function downloadFile(link) {
  if (link.getAttribute("aria-disabled") === "true") return;
  link.setAttribute("aria-disabled", "true");
  const originalText = link.textContent;
  link.textContent = t('preparingDownload');
  try {
    const kind = new URL(link.href).pathname.endsWith('/original') ? 'original' : 'export';
    const prepared = await post(`/imports/${current.id}/download`, {kind});
    const anchor = document.createElement("a");
    anchor.href = prepared.url;
    anchor.download = prepared.url.split('/').at(-1);
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
  } catch (error) {
    failure({code: error.code || "network"});
  } finally {
    link.textContent = originalText;
    link.removeAttribute("aria-disabled");
  }
}
function failure(e, target = main) {
  let box = target.querySelector(".error-box");
  if (!box) {
    box = document.createElement("div");
    box.className = "error-box";
    box.tabIndex = -1;
    box.setAttribute("role", "alert");
    target.prepend(box);
  }
  box.innerHTML = `<p>${esc(t(e.code || e.message || "unknownError"))}</p><button data-reload>${t("retry")}</button>`;
  box.querySelector("button").onclick = () => route();
  box.focus();
}
function pager(offset, total, callback) {
  const node = document.createElement("div");
  node.className = "pagination";
  node.innerHTML = `<button ${offset === 0 ? "disabled" : ""} data-prev>${t("previous")}</button><span>${Math.min(offset + 1, total)}–${Math.min(offset + 30, total)} / ${total}</span><button ${offset + 30 >= total ? "disabled" : ""} data-next>${t("next")}</button>`;
  node.querySelector("[data-prev]").onclick = () => callback(Math.max(0, offset - 30));
  node.querySelector("[data-next]").onclick = () => callback(offset + 30);
  return node;
}
function navActive(kind) {
  document.querySelectorAll(".sidebar nav a").forEach((a) => a.classList.toggle("active", a.hash === "#" + kind));
  $("#breadcrumb").textContent = t(kind === "new" ? "newImport" : "batches");
}
function heading(title, subtitle, actions = "") {
  return `<div class="page-heading"><div><h1>${esc(title)}</h1>${subtitle ? `<p class="muted">${esc(subtitle)}</p>` : ""}</div>${actions ? `<div class="actions">${actions}</div>` : ""}</div>`;
}
function draftKey() {
  return `st-review-${current?.id}-${showBulk ? "bulk" : detail?.group_id}`;
}
function readDraft() {
  try {
    const draft = JSON.parse(localStorage.getItem(draftKey()) || "{}");
    return draft.dirty || draft.note ? draft : {};
  } catch {
    return {};
  }
}
function persistDraft() {
  const f = $("#review-form");
  if (!f || f.dataset.saved === "true" || f.dataset.dirty !== 'true') return;
  const value = { reviewer: f.reviewer.value, note: f.note.value, disposition: f.disposition.value, dirty: true };
  const old = readDraft();
  value.request_id = old.reviewer === value.reviewer && old.note === value.note && old.disposition === value.disposition && old.request_id ? old.request_id : crypto.randomUUID();
  try {
    localStorage.setItem(draftKey(), JSON.stringify(value));
    localStorage.setItem("st-reviewer", value.reviewer);
  } catch {
  }
}
$("#language").onclick = () => {
  persistDraft();
  lang = lang === "zh" ? "en" : "zh";
  localStorage.setItem("st-language", lang);
  translateShell();
  route();
};
window.addEventListener("hashchange", () => {
  persistDraft();
  selected.clear();
  showBulk = false;
  issuesOpen = false;
  detailOffset = 0;
  route();
});
window.addEventListener("beforeunload", persistDraft);
async function route() {
  clearTimeout(timer);
  translateShell();
  const rid = ++renderId;
  const parts = (location.hash || "#imports").slice(1).split("/");
  if (parts[0] === "new") {
    navActive("new");
    return renderUpload();
  }
  navActive("imports");
  try {
    if (parts[0] === "batch" && parts[1]) {
      const record = await api("/imports/" + encodeURIComponent(parts[1]));
      if (rid !== renderId) return;
      if (current?.id !== record.id) {
        filters = { kind: "", status: "", q: "" };
        groupOffset = 0;
        detailOffset = 0;
        detail = null;
        selected.clear();
      }
      current = record;
      $("#breadcrumb").textContent = t("batches") + " / " + record.filename;
      await renderBatch(parts[2] ? Number(parts[2]) : null, rid);
    } else {
      await renderHistory(rid);
    }
  } catch (e) {
    if (rid === renderId) {
      main.innerHTML = heading(t("batches"), "");
      failure(e);
    }
  }
}
async function renderHistory(rid) {
  current = null;
  detail = null;
  main.innerHTML = heading(t("batches"), t("batchSubtitle"), `<a href="#new" class="button primary">${t("uploadCSV")}</a>`) + `<form id="history-search" class="toolbar"><label>${t("searchFiles")}<input name="query" value="${esc(historyQuery)}" maxlength="200"></label><button>${t("search")}</button></form><div id="ledger"><div class="loading-line"></div><div class="loading-line"></div></div>`;
  $("#history-search").onsubmit = (e) => {
    e.preventDefault();
    historyQuery = e.target.query.value;
    historyOffset = 0;
    route();
  };
  const data = await api("/imports?q=" + encodeURIComponent(historyQuery) + "&offset=" + historyOffset);
  if (rid !== renderId) return;
  const node = $("#ledger");
  if (!data.items.length) {
    node.innerHTML = `<div class="empty">${icon}<h2>${t(historyQuery ? "noResults" : "noBatches")}</h2><p>${t("noBatchesText")}</p>${historyQuery ? `<button id="clear-search">${t("clearSearch")}</button>` : `<a class="button primary" href="#new">${t("uploadCSV")}</a>`}</div>`;
    if ($("#clear-search")) $("#clear-search").onclick = () => {
      historyQuery = "";
      route();
    };
    return;
  }
  node.innerHTML = `<div class="table-wrap"><table class="batch-ledger"><thead><tr><th>${t("file")}</th><th>${t("state")}</th><th>${t("created")}</th><th>${t("rows")}</th></tr></thead><tbody>${data.items.map((x) => `<tr><td><a href="#batch/${x.id}"><strong>${esc(x.filename)}</strong></a><span class="hint">${t(x.origin === "upload" ? "upload" : "example")}</span></td><td>${badge(x.state)}${x.group_count ? `<span class="hint">${x.group_count} ${t("groupCount")}</span>` : ""}</td><td>${date(x.created_at)}</td><td class="num">${x.row_count.toLocaleString()}</td></tr>`).join("")}</tbody></table></div>`;
  node.append(pager(historyOffset, data.total, (o) => {
    historyOffset = o;
    route();
  }));
  if (data.items.some((x) => ["parsing", "analyzing"].includes(x.state))) timer = setTimeout(route, 2e3);
}
function renderUpload() {
  main.innerHTML = heading(t("uploadTitle"), t("uploadText")) + `<div class="import-grid"><section class="panel"><form id="upload-form"><div class="drop-zone">${icon}<label for="csv-files">${t("chooseFile")}</label><input type="file" id="csv-files" accept=".csv,text/csv" multiple ${uploadBusy ? "disabled" : ""}><p class="hint">${t("fileLimit")}</p></div><div class="two"><label>${t("encoding")}<select name="encoding"><option value="utf-8-sig">UTF-8</option><option value="gb18030">GB18030 / GBK</option></select></label><label>${t("delimiter")}<select name="delimiter"><option value=",">${t("comma")}</option><option value=";">${t("semicolon")}</option><option value="tab">${t("tab")}</option></select></label></div><button class="primary" ${uploadBusy ? "disabled" : ""}>${t(uploadQueue.some((x) => x.error) ? "retryUpload" : "startUpload")}</button></form><div id="upload-queue" aria-live="polite"></div><button id="example" class="link-button">${t("chooseExample")}</button></section><aside class="panel import-guide"><h2>${t("guideTitle")}</h2>${[1, 2, 3].map((n) => `<div class="import-step"><span class="step-number">${n}</span><div><strong>${t("step" + n)}</strong><p>${t("step" + n + "Text")}</p></div></div>`).join("")}</aside></div>`;
  $("#csv-files").onchange = (e) => {
    uploadQueue = [...e.target.files].map((f) => ({ file: f, id: crypto.randomUUID(), progress: 0, encoding: $("#upload-form").encoding.value, delimiter: $("#upload-form").delimiter.value }));
    renderQueue();
  };
  $("#upload-form").onsubmit = async (e) => {
    e.preventDefault();
    if (uploadBusy) return;
    if (!uploadQueue.length) {
      failure({ code: "selectFiles" }, $("#upload-form"));
      return;
    }
    uploadBusy = true;
    const enc = e.target.encoding.value, del = e.target.delimiter.value === "tab" ? "	" : e.target.delimiter.value;
    e.target.querySelector("button").disabled = true;
    e.target.querySelectorAll("input,select").forEach(n => n.disabled = true);
    for (const x of uploadQueue) {
      if (x.result) continue;
      x.error = null;
      x.encoding = enc;
      x.delimiter = del;
      if (x.file.size > 67108864 || !x.file.size) {
        x.error = "file_size";
        renderQueue();
        continue;
      }
      try {
        x.result = await uploadFile(x);
      } catch (err) {
        x.error = err.code || "uploadFailed";
      }
      renderQueue();
    }
    uploadBusy = false;
    renderQueue();
  };
  $("#example").onclick = async (e) => {
    e.target.disabled = true;
    try {
      const r = await post("/example", {});
      location.hash = "#batch/" + r.id;
    } catch (err) {
      failure(err);
      e.target.disabled = false;
    }
  };
  renderQueue();
}
function renderQueue() {
  const n = $("#upload-queue");
  if (!n) return;
  n.innerHTML = uploadQueue.map((x) => `<div class="queue-item"><strong>${esc(x.file.name)}</strong>${x.result ? `<span class="hint">${t("uploaded")}</span><a href="#batch/${x.result.id}">${t("open")}</a>` : x.error ? `<div class="error-box">${t(x.error)}</div>` : `<progress max="100" value="${x.progress}" aria-label="${t("uploading")}"></progress><span class="hint">${x.progress}%</span>`}</div>`).join("");
  const submit = $("#upload-form > button");
  $("#upload-form")?.querySelectorAll("input,select").forEach(n => n.disabled = uploadBusy);
  if (submit && uploadQueue.length && uploadQueue.every(x => x.result)) submit.disabled = true;
  else if (submit && !uploadBusy) submit.disabled = false;
}
function uploadFile(x) {
  return new Promise((resolve, reject) => {
    const req = new XMLHttpRequest();
    req.open("POST", `/api/workbench/imports?filename=${encodeURIComponent(x.file.name)}&encoding=${encodeURIComponent(x.encoding)}&delimiter=${encodeURIComponent(x.delimiter)}`);
    req.setRequestHeader("Idempotency-Key", x.id);
    req.setRequestHeader("Content-Type", "text/csv");
    req.timeout = 12e4;
    req.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        x.progress = Math.round(e.loaded / e.total * 100);
        renderQueue();
      }
    };
    req.onload = () => {
      let data;
      try {
        data = JSON.parse(req.responseText);
      } catch {
        data = { error: "uploadFailed" };
      }
      if (req.status >= 200 && req.status < 300) resolve(data);
      else reject({ code: data.error || "uploadFailed" });
    };
    req.onerror = () => reject({ code: "network" });
    req.ontimeout = () => reject({ code: "network" });
    req.send(x.file);
  });
}
function previewTable(headers, rows) {
  return `<div class="table-wrap"><table><thead><tr><th>${t("csvLine")}</th>${headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr><td class="num">${r.line_start}${r.line_end !== r.line_start ? "–" + r.line_end : ""}</td>${r.cells.map((v) => `<td>${esc(v)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}
async function renderBatch(gid, rid) {
  const r = current;
  main.innerHTML = heading(r.filename, `${t(r.origin === "upload" ? "upload" : "example")} · ${date(r.created_at)}`, `<a class="button quiet" href="#imports">${t("back")}</a><a class="button" href="/api/workbench/imports/${r.id}/original">${t("original")}</a>${r.state === "ready" ? `<a class="button primary" href="/api/workbench/imports/${r.id}/export" title="${t("exportHint")}">${t("export")}</a>` : ""}`);
  main.querySelectorAll('a[href^="/api/workbench/"]').forEach(link => {
    link.onclick = event => { event.preventDefault(); downloadFile(link); };
  });
  if (["parsing", "analyzing"].includes(r.state)) {
    main.insertAdjacentHTML("beforeend", `<section class="busy" aria-live="polite"><h2>${t(r.state)}</h2><p>${t(r.state === "parsing" ? "reading" : "processing")}</p><progress ${r.state === "analyzing" ? `value="${r.progress}" max="${r.row_count || 1}"` : ""} aria-label="${t(r.state)}"></progress><button id="cancel-processing">${t("stopProcessing")}</button>${r.state === "analyzing" ? `<div>${r.progress.toLocaleString()} / ${r.row_count.toLocaleString()} ${t("rows")}</div>` : ""}</section>`);
    $("#cancel-processing").onclick = async (e) => {
      clearTimeout(timer);
      e.target.disabled = true;
      try { await post(`/imports/${r.id}/cancel`, {}); route(); }
      catch (error) { failure(error); e.target.disabled = false; }
    };
    timer = setTimeout(route, 1e3);
    return;
  }
  if (r.state === "interrupted" || r.state === "failed" || r.state === "cancelled") {
    main.insertAdjacentHTML("beforeend", `<section class="error-box" role="alert"><h2>${t(r.state)}</h2><p>${t(r.state === "cancelled" ? "cancelled" : r.error === "interrupted" ? "interruptedText" : r.error || "unknownError")}${r.error_line ? " · " + t("csvLine") + " " + r.error_line : ""}</p><div class="actions">${r.state === "interrupted" || r.state === "cancelled" || ["analysis_failed", "task_timeout"].includes(r.error) ? `<button id="resume">${t("resume")}</button>` : ""}<a class="button" href="#new">${t("chooseAgain")}</a></div></section>`);
    if ($("#resume")) $("#resume").onclick = async (e) => {
      e.target.disabled = true;
      try {
        if (r.mapping) await post(`/imports/${r.id}/analyze`, { ...r.mapping, expected_revision: r.revision });
        else if (r.headers.length) {
          renderMapping();
          return;
        } else await post(`/imports/${r.id}/retry`, {});
        route();
      } catch (err) {
        failure(err);
      }
    };
    return;
  }
  if (r.state === "draft") {
    renderMapping();
    return;
  }
  main.insertAdjacentHTML("beforeend", `<section class="summary"><div><strong>${r.row_count.toLocaleString()}</strong>${t("rowCount")}</div><div><strong>${r.group_count}</strong>${t("groupCount")}</div><div><strong>${r.reviewed_count}</strong>${t("reviewedCount")}</div><div class="status-copy">${r.group_count === r.reviewed_count ? t("allReviewed") : r.group_count - r.reviewed_count + " " + t("unreviewed")}</div></section>${r.error_count ? `<div class="notice">${r.error_count} ${t("batchIssues")} <button id="show-issues" class="link-button">${t("issues")}</button></div>` : ""}<div id="issue-region"></div><form id="group-filter" class="toolbar"><label>${t("keySearch")}<input name="q" value="${esc(filters.q)}" maxlength="200"></label><label class="compact">${t("kind")}<select name="kind">${["", "quantity_difference", "identical_content", "multiple_rows"].map((k) => `<option value="${k}" ${filters.kind === k ? "selected" : ""}>${t(k || "all")}</option>`).join("")}</select></label><label class="compact">${t("reviewStatus")}<select name="status">${["", "pending", "retain_all", "follow_up"].map((k) => `<option value="${k}" ${filters.status === k ? "selected" : ""}>${t(k || "all")}</option>`).join("")}</select></label><button>${t("applyFilters")}</button></form><div class="work-grid"><section><div id="bulk-bar"></div><div id="groups"></div></section><section id="detail"></section></div><details><summary>${t("details")}</summary><dl class="metadata"><dt>${t("source")}</dt><dd>${t(r.origin === "upload" ? "upload" : "example")}</dd><dt>${t("keys")}</dt><dd>${r.mapping.keys.map(esc).join(" + ")}</dd><dt>${t("quantity")}</dt><dd>${esc(r.mapping.quantity)}</dd><dt>${t("checksum")}</dt><dd>${r.sha256}</dd><dt>${t("version")}</dt><dd>${r.revision}</dd></dl><button id="copy-batch">${t("newFields")}</button></details>`);
  $("#copy-batch").onclick = async (e) => {
    e.target.disabled = true;
    try { const copy = await post(`/imports/${r.id}/copy`, {request_id:crypto.randomUUID()}); location.hash = "#batch/"+copy.id; }
    catch(error) { failure(error); e.target.disabled=false; }
  };
  $("#group-filter").onsubmit = (e) => {
    e.preventDefault();
    persistDraft();
    filters = { q: e.target.q.value, kind: e.target.kind.value, status: e.target.status.value };
    groupOffset = 0;
    selected.clear();
    showBulk = false;
    route();
  };
  if ($("#show-issues")) $("#show-issues").onclick = () => {
    issuesOpen = !issuesOpen;
    renderIssues();
  };
  if (issuesOpen) await renderIssues();
  groups = await api(`/imports/${r.id}/groups?` + new URLSearchParams({ ...filters, offset: groupOffset }));
  if (rid !== renderId) return;
  renderGroups();
  if (showBulk) {
    detail = null;
    renderReview(null);
    return;
  }
  if (groups.items.length && !groups.items.some((x) => x.group_id === gid)) gid = groups.items[0].group_id;
  if (!groups.items.length) gid = null;
  if (gid) {
    detail = await api(`/imports/${r.id}/groups/${gid}?offset=${detailOffset}`);
    if (rid !== renderId) return;
    renderDetail();
  } else $("#detail").innerHTML = `<div class="empty">${icon}<h2>${t(r.group_count ? "pickGroup" : "noGroups")}</h2><p>${t(r.group_count ? "pickGroupText" : "noGroupsText")}</p></div>`;
}
function renderMapping() {
  const r = current;
  let saved = {};
  try {
    saved = JSON.parse(localStorage.getItem("st-mapping-" + r.id) || "{}");
  } catch {
  }
  main.insertAdjacentHTML("beforeend", `<section class="panel"><h2>${t("mapTitle")}</h2><div class="mapping-grid"><form id="mapping-form"><fieldset><legend>${t("keys")}</legend><p class="hint">${t("keysHelp")}</p><div class="key-options">${r.headers.map((h, i) => `<label class="check-label"><input type="checkbox" name="keys" value="${esc(h)}" ${(saved.keys || []).includes(h) ? "checked" : ""}><span>${esc(h)}</span></label>`).join("")}</div></fieldset><label>${t("quantity")}<select name="quantity" required><option value="">${t("selectColumn")}</option>${r.headers.map((h) => `<option ${saved.quantity === h ? "selected" : ""}>${esc(h)}</option>`).join("")}</select></label><button class="primary" type="submit">${t("beginReview")}</button></form><div><h3>${t("preview")} · ${r.row_count.toLocaleString()} ${t("rows")}</h3>${previewTable(r.headers, r.preview)}</div></div></section>`);
  const f = $("#mapping-form");
  const mapping = () => ({ keys: [...f.querySelectorAll("[name=keys]:checked")].map((n) => n.value), quantity: f.quantity.value });
  f.onchange = () => localStorage.setItem("st-mapping-" + r.id, JSON.stringify(mapping()));
  f.onsubmit = async (e) => {
    e.preventDefault();
    const body = mapping();
    if (!body.keys.length || !body.quantity) {
      failure({ code: "mappingRequired" }, f);
      return;
    }
    f.querySelector("button[type=submit]").disabled = true;
    try {
      await post(`/imports/${r.id}/analyze`, { ...body, expected_revision: r.revision });
      route();
    } catch (err) {
      failure(err, f);
      f.querySelector("button[type=submit]").disabled = false;
    }
  };
}
function renderGroups() {
  const node = $("#groups");
  if (!groups.items.length) {
    node.innerHTML = `<div class="panel"><p>${t("noFiltered")}</p><button id="reset-filters">${t("resetFilters")}</button></div>`;
    $("#reset-filters").onclick = () => {
      filters = { q: "", kind: "", status: "" };
      groupOffset = 0;
      route();
    };
    renderBulkBar();
    return;
  }
  node.innerHTML = `<div class="group-list">${groups.items.map((g) => `<div class="group-row ${detail?.group_id === g.group_id ? "selected" : ""}" data-gid="${g.group_id}"><label class="group-check"><input type="checkbox" aria-label="${t("selectGroup")} ${g.group_id}" value="${g.group_id}" ${selected.has(g.group_id) ? "checked" : ""}></label><button class="group-open" data-gid="${g.group_id}"><span class="group-key">${g.key_value.map(esc).join(" / ")}</span><span class="group-meta">${badge(g.kind)}<span class="hint">${g.row_count} ${t("rowUnit")}</span></span><span class="hint">${t(g.disposition)}</span></button></div>`).join("")}</div>`;
  node.querySelectorAll("input").forEach((c) => c.onchange = () => {
    if (c.checked && selected.size >= 100) { c.checked = false; toast('selectionLimit'); return; }
    c.checked ? selected.add(Number(c.value)) : selected.delete(Number(c.value));
    renderBulkBar();
  });
  node.querySelectorAll(".group-open").forEach((b) => b.onclick = () => {
    persistDraft();
    detailOffset = 0;
    showBulk = false;
    location.hash = `#batch/${current.id}/${b.dataset.gid}`;
    if (detail?.group_id === Number(b.dataset.gid)) route();
  });
  node.append(pager(groupOffset, groups.total, (o) => {
    groupOffset = o;
    route();
  }));
  renderBulkBar();
}
function renderBulkBar() {
  const el = $("#bulk-bar");
  el.innerHTML = `<div class="actions"><button id="select-page" class="quiet">${t("selectAll")}</button>${selected.size ? `<button id="clear-selected" class="quiet">${t("clearSelection")}</button>` : ""}</div>${selected.size ? `<div class="bulk-bar"><span>${t("selected")} ${selected.size} ${t("group")}</span><button id="bulk-review">${t("bulkReview")}</button></div>` : ""}`;
  $("#select-page").onclick = () => {
    if (new Set([...selected, ...groups.items.map(x => x.group_id)]).size > 100) { toast('selectionLimit'); return; }
    groups.items.forEach((x) => selected.add(x.group_id));
    renderGroups();
  };
  $("#select-page").disabled = !groups.items.length;
  if ($("#clear-selected")) $("#clear-selected").onclick = () => {
    selected.clear();
    showBulk = false;
    renderGroups();
    if (detail) renderDetail();
  };
  if ($("#bulk-review")) $("#bulk-review").onclick = () => {
    persistDraft();
    showBulk = true;
    renderReview(null);
    $("#detail").scrollIntoView({ block: "start" });
  };
}
function originalCell(value) {
  const text = String(value ?? "");
  return text.length <= 100 ? esc(text) : `<details class="cell-detail"><summary>${esc(text.slice(0, 55))}… <span>${t("expandValue")}</span></summary><div>${esc(text)}</div></details>`;
}
function renderDetail() {
  showBulk = false;
  const d = detail;
  document.querySelectorAll(".group-row").forEach((n) => n.classList.toggle("selected", Number(n.dataset.gid) === d.group_id));
  $("#detail").innerHTML = `<div class="review-panel"><div class="review-head"><h2 title="${esc(d.key_value.join(" / "))}">${d.key_value.map(esc).join(" / ")}</h2>${badge(d.kind)}</div><h3>${t("compare")} · ${d.total} ${t("rowUnit")}</h3><div class="table-wrap"><table class="compare"><thead><tr><th>${t("field")}</th>${d.rows.map((r) => `<th>${t("csvLine")} ${r.line_start}${r.line_end !== r.line_start ? "–" + r.line_end : ""}</th>`).join("")}</tr></thead><tbody>${d.headers.map((h,i)=>({h,i})).sort((a,b)=>Number(b.h===current.mapping.quantity)-Number(a.h===current.mapping.quantity)).map(({h,i}) => `<tr class="${new Set(d.rows.map((r) => r.cells[i])).size > 1 ? "different" : ""} ${h === current.mapping.quantity ? "quantity" : ""}"><th scope="row">${esc(h)}${h === current.mapping.quantity && !/^(quantity|qty|数量)$/i.test(h) ? `<span class="hint">${t("quantity")}</span>` : ""}</th>${d.rows.map((r) => `<td class="num">${originalCell(r.cells[i])}</td>`).join("")}</tr>`).join("")}</tbody></table></div><div id="row-pager"></div><div id="review-slot"></div></div>`;
  if (d.total > 30) $("#row-pager").append(pager(detailOffset, d.total, async (o) => {
    persistDraft();
    const batch = current.id, rid = renderId;
    detailOffset = o;
    try {
      const value = await api(`/imports/${batch}/groups/${d.group_id}?offset=${o}`);
      if (rid !== renderId || current.id !== batch || detail?.group_id !== d.group_id || detailOffset !== o) return;
      detail = value;
      renderDetail();
    } catch (e) { if (rid === renderId) failure(e, $('#detail')); }
  }));
  renderReview(d);
  const assistance=document.createElement('section');
  assistance.className='investigation';
  assistance.innerHTML=`<h3>${t('investigationTitle')}</h3><p class="hint">${t('investigationHint')}</p><button type="button" class="quiet" data-investigate>${t('investigate')}</button><div data-investigation-result role="status" aria-live="polite"></div>`;
  $('#detail .review-panel').insertBefore(assistance,$('#review-slot'));
  const button=assistance.querySelector('[data-investigate]'), output=assistance.querySelector('[data-investigation-result]');
  button.onclick=async()=>{
    const batch=current.id,gid=d.group_id,offset=detailOffset,rid=renderId;
    button.disabled=true;button.textContent=t('investigating');output.textContent='';
    try{
      const result=await post(`/imports/${batch}/groups/${gid}/investigate`,{locale:lang,offset,expected_revision:current.revision});
      if(rid!==renderId||current?.id!==batch||detail?.group_id!==gid||detailOffset!==offset)return;
      if(result.status!=='generated_unverified')throw Object.assign(new Error(result.error),{code:result.error});
      output.innerHTML=`<p class="hint">${t('investigationScope')} ${result.shown_rows} / ${result.group_rows} ${t('rows')} · ${esc(result.model)}</p><ul>${result.output.observations.map(o=>`<li><strong>${esc(o.field)}</strong> · ${t('sourceRow')} ${o.row_numbers.map(esc).join(', ')}<p>${esc(o.text)}</p></li>`).join('')}</ul><h4>${t('investigationQuestions')}</h4><ol>${result.output.questions.map(q=>`<li>${esc(q)}</li>`).join('')}</ol><button type="button" data-save-investigation>${t('investigationDownload')}</button>`;
      output.querySelector('[data-save-investigation]').onclick=()=>{
        const blob=new Blob([JSON.stringify(result,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');
        a.href=url;a.download=`supplytrace-investigation-${batch}-${gid}.json`;a.click();URL.revokeObjectURL(url);
      };
    }catch(e){if(assistance.isConnected)output.textContent=t(e.code||'network');}
    finally{button.disabled=false;button.textContent=t('investigate');}
  };
}
function renderReview(d) {
  const bulk = showBulk, ids = bulk ? [...selected] : [d.group_id];
  let target = $("#review-slot");
  if (bulk) {
    $("#detail").innerHTML = '<div class="review-panel" id="review-slot"></div>';
    target = $("#review-slot");
  }
  const draft = readDraft();
  const prior = bulk ? current.events.filter((e) => e.group_ids.some((id) => ids.includes(id))) : (d.history?.items || []).slice().reverse();
  const latest = bulk ? prior.at(-1) : d.review;
  const disposition = draft.disposition || latest?.disposition || "retain_all";
  target.innerHTML = `<form id="review-form" class="review-form"><h3>${bulk ? t("bulkReview") + " · " + ids.length + " " + t("group") : t("recordTitle")}</h3>${bulk ? `<p class="hint">${t("bulkHint")}</p>` : ""}<div class="two"><label>${t("decision")}<select name="disposition">${["retain_all", "follow_up", "pending"].map((x) => `<option value="${x}" ${disposition === x ? "selected" : ""}>${t(x)}</option>`).join("")}</select></label><label>${t("reviewer")}<input name="reviewer" required maxlength="80" value="${esc(draft.reviewer || localStorage.getItem("st-reviewer") || "")}" placeholder="${t("reviewerPlaceholder")}"></label></div><label>${t("reason")}<textarea name="note" required maxlength="1500" placeholder="${t("reasonPlaceholder")}">${esc(draft.note || "")}</textarea></label><p class="saved-note">${t("draftSaved")}</p><div class="actions"><button class="primary" type="submit" ${saving ? "disabled" : ""}>${t(saving ? "saving" : "save")}</button>${!bulk ? `<button type="button" id="save-next" ${saving ? "disabled" : ""}>${t("saveNext")}</button>` : `<button type="button" id="cancel-bulk">${t("cancel")}</button>`}</div></form><details ${prior.length ? "open" : ""}><summary>${t("reviewHistory")} · ${prior.length}</summary>${prior.length ? prior.slice().reverse().map((e) => `<article class="history-event"><strong>${esc(e.reviewer)}</strong> · ${t(e.disposition)}<p>${esc(e.note)}</p><small>${date(e.created_at)} · ${t("version")} ${e.revision}</small></article>`).join("") : `<p class="hint">${t("noHistory")}</p>`}</details>`;
  const form = $("#review-form");
  if (!bulk) {
    const history = target.querySelector('details');
    const renderHistoryPage = (data, offset) => {
      history.innerHTML = `<summary>${t('reviewHistory')} · ${data.total}</summary><div>${data.items.map(e => `<article class="history-event"><strong>${esc(e.reviewer)}</strong> · ${t(e.disposition)}<p>${esc(e.note)}</p><small>${date(e.created_at)} · ${t('version')} ${e.revision}</small></article>`).join('') || `<p class="hint">${t('noHistory')}</p>`}</div>`;
      if (data.total > 30) history.append(pager(offset, data.total, async o => {
        const batch = current.id, gid = d.group_id, rid = renderId;
        try {
          const page = await api(`/imports/${batch}/groups/${gid}/events?offset=${o}`);
          if (rid === renderId && current.id === batch && detail?.group_id === gid) renderHistoryPage(page, o);
        } catch (e) { failure(e, history); }
      }));
    };
    renderHistoryPage(d.history || {total:0,items:[]}, 0);
  }
  form.dataset.dirty = draft.request_id ? 'true' : 'false';
  form.oninput = form.onchange = () => { form.dataset.dirty = 'true'; persistDraft(); };
  form.onsubmit = (e) => {
    e.preventDefault();
    saveReview(ids, false);
  };
  if ($("#save-next")) $("#save-next").onclick = () => {
    if (form.reportValidity()) saveReview(ids, true);
  };
  if ($("#cancel-bulk")) $("#cancel-bulk").onclick = () => {
    persistDraft();
    showBulk = false;
    if (detail) renderDetail();
    else route();
  };
}
async function saveReview(ids, next) {
  if (saving) return;
  const form = $("#review-form");
  if (!form.reviewer.value.trim() || !form.note.value.trim()) {
    failure({ code: "reviewRequired" }, form);
    return;
  }
  persistDraft();
  const storageKey = draftKey();
  saving = true;
  form.querySelectorAll("button").forEach((b) => b.disabled = true);
  try {
    await post(`/imports/${current.id}/review`, { group_ids: ids, reviewer: form.reviewer.value, note: form.note.value, disposition: form.disposition.value, expected_revision: current.revision, request_id: readDraft().request_id || crypto.randomUUID() });
    form.dataset.saved = "true";
    localStorage.removeItem(storageKey);
    selected.clear();
    showBulk = false;
    toast("saved");
    if (next) {
      const at = groups.items.findIndex((x) => x.group_id === ids[0]);
      const target = groups.items[at + 1];
      if (target) {
        location.hash = `#batch/${current.id}/${target.group_id}`;
      } else {
        groupOffset = at === groups.items.length - 1 && groupOffset + 30 < groups.total ? groupOffset + 30 : groupOffset;
        location.hash = "#batch/" + current.id;
      }
    }
    await route();
  } catch (e) {
    failure(e, form);
  } finally {
    saving = false;
    $("#review-form")?.querySelectorAll("button").forEach((b) => b.disabled = false);
    const saveButton = $("#review-form button[type=submit]");
    if (saveButton) saveButton.textContent = t("save");
  }
}
async function renderIssues() {
  const n = $("#issue-region");
  if (!issuesOpen) {
    n.innerHTML = "";
    return;
  }
  try {
    const data = await api(`/imports/${current.id}/issues?offset=${issueOffset}`);
    n.innerHTML = `<section class="panel"><h2>${t("issuesTitle")}</h2><p class="hint">${t("issuesHelp")}</p><div class="table-wrap"><table class="issues-table"><thead><tr><th>${t("csvLine")}</th><th>${t("field")}</th><th>${t("problem")}</th><th>${t("allValues")}</th></tr></thead><tbody>${data.items.map((x) => `<tr><td>${x.line_start}</td><td>${esc(x.field)}</td><td>${t(x.code)}</td><td>${x.cells.map(esc).join(" | ")}</td></tr>`).join("")}</tbody></table></div></section>`;
    n.firstChild.append(pager(issueOffset, data.total, (o) => {
      issueOffset = o;
      renderIssues();
    }));
  } catch (e) {
    failure(e, n);
  }
}
route();
