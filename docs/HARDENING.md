# Reliability verification · 0.3

This file describes the implemented mechanisms and explicit delivery boundary. Original execution artifacts are retained in the development workspace; the dated public summary is in `VERIFICATION-20261027.md`. Older results remain historical evidence. Local correctness and contest/public readiness are separate.

## Reproduction

Use Python3.12 and requirements-lock.txt. All app/test subprocesses use scripts/safe_run.py's allowlisted environment. No inherited provider key or project dotenv enters them. Unit tests can run directly; stress/memory/fault batches and model batches require the coordinator's global lock:

```powershell
.venv/Scripts/python.exe scripts/safe_run.py .venv/Scripts/python.exe -m pytest tests/test_hardening.py -q -p no:cacheprovider
```

Use quoted paths when they contain spaces. The development-scale benchmark runner and its machine-specific launch wrapper remain in the private execution archive; its measurements are reported with their limitations in the dated verification summary. A launch receipt is not a passing result. Tests retain their UUID data directories under `verification/` and disable pytest's cache provider; no bulk cleanup is performed.

## Robustness matrix

| Trigger | Implemented protection and user behavior | Main path / reproduction evidence | Boundary |
|---|---|---|---|
|1. Burst concurrent reads/uploads/downloads|16 complete ASGI lifecycles,2uploads,1export builder; excess503/429+Retry-After|boundary.LocalBoundary; admission body/drain tests; real HTTP1/10/30 benchmark|No queue fairness or production-capacity claim|
|2. Two reviewers save one version|BEGIN IMMEDIATE, compare revision, atomic event/projection/count update; second409|workspace.review; existing concurrent-review and bulk atomic tests|Reviewer is a local self-declared label|
|3. Retry after response loss|Request key binds source hash/encoding/delimiter/filename; review key binds exact review body; changed payload409|create_file/review; existing idempotency tests|Uploads retry the full file, not network-resume|
|4. Cancel/retry queue flood|Six actual unfinished future permits; durable queue count; cancellation does not release a live future early|_job/_submit/cancel; real executor-queue regression|One analytical worker,180s deadline|
|5. Process death mid import|work_jobs checkpoint, OS process lease, replay and generation fencing; result remains unavailable until ready|start/_supervise/parse/process; durable recovery unit and forced process death benchmark|Parser replay starts at byte0; no duplicate stored rows|
|6. SQLite busy/connection loss|1.5s busy timeout, explicit closing connection,503; transient supervisor failure remains recoverable|storage.connect; busy timeout and supervisor injection|Not a remote-DB pool or deadlock simulator|
|7. Slow SQL/pagination|SQL filters/30-row pages, member/status/history indices,2s SQLite progress deadline; bounded cache for expensive scans|groups/detail/events; populated ANALYZE/EXPLAIN and timed slow join|Substring search remains bounded O(number of groups); OFFSET deep paging still costs a scan|
|8. Large CSV/huge duplicate key|Incremental64KiB decode,512row/1MiB Arrow batches, disk DuckDB min/max grouping, relational members|csv_engine/workspace;500k-row and250k-same-key fixtures|64MiB,500krows,100columns,32K field/256K record chars|
|9. Repeat operations grow RSS/handles|Explicit connection/engine/registration/stream ownership; measure whole server process tree|real HTTP lifecycle warmup+18cycles; native RSS/handles|Only the reported window, not proof of no leak forever|
|10. Same-key cache herd/expiry/miss|Singleflight,3s waiter deadline,128entries/4MiB, jittered TTL, authoritative version keys, no cached errors|cache.py; concurrent waiter/failure/capacity tests|No distributed cache; decisions never use cache|
|11. Model timeout/5xx/bad JSON|Explicit loopback endpoint,16KiB context,90s budget,64KiB output bound, strict structure/reference validation|assistant.suggest; MockTransport faults and fresh shared-model holdouts|Model stays outside live workbench until usefulness/semantics established|
|12. Retry storm/duplicate effects|No automatic model retry; max3 unexpected worker attempts; idempotent source rows and generation publication|_submit/_supervise, review CAS|User retries remain explicit and bounded|
|13. Partial/unknown result|parsing/draft/analyzing/ready/failed/cancelled/interrupted are distinct; unpublished generations not reviewable|workspace state checks; crash/cancel tests|A saved local note does not establish source-owner approval|
|14. Invalid text/formulas/injection|Strict CSV encoding/header/record rules, physical line errors, parameterized SQL, escaped HTML, formula-safe derived CSV|records/export; invalid files/formula/SQL/XSS tests; rendered long cells|No ZIP extraction or arbitrary SQL input, so ZipSlip/SQLGlot not applicable|
|15. Cross-origin/session/secret boundaries|TrustedHost, cross-site mutation refusal, CSP, allowlisted subprocess environment, secret repr suppression|boundary/app/config/safe_run; existing Origin/repr tests|No public authentication or multitenancy; loopback only|
|16. Full disk/write failure/backup|Free-space and workfile admission; failed partial retained; checksum before atomic export publish; online SQLite backup to new path|workspace_export/database; injected disk-full/hash failures; clean package restore|No power-loss hardware lab; in-flight writes can exceed admission threshold|
|17. Unbounded logs/history/cache|16MiB traces,512 metric window,20k events/import,1000imports/1GiB originals,2GiB/4096 workfile admission|tracing/Metrics/workspace; cap tests|No automatic deletion; operator review is required when storage fills|
|18. UI repeat/refresh/offline/late responses|Disabled busy controls, idempotent saves, dirty-only drafts, render/page guards, paged history and native download preparation|workbench.js; Egolite Space30 fresh scripts and screenshots|QA observations are local; no user adoption claim|
|19. Corrupted source/evidence/artifact|Original hash, ordered row hashes, group count, ZIP checksum; refuse export and preserve failed evidence|workspace_export; blob/row/ZIP tamper tests|Hashes are not signatures or proof of upstream authenticity|
|20. Install/restart/readiness|Pinned wheels, clean Python environment, private allowlist package, schema migrations, worker-ready check and process lease|clean-install.json, migration-live.json and deployment smoke|Linux/container/public deployments unverified|

## Failure evidence and corrections

- Prior source evidence: `workbench-before.py.txt` contains full-file decode/list grouping/Python pagination/event replay and in-memory ZIP. New main routes no longer call those paths; the small parse_csv compatibility helper remains only for legacy unit callers.
- `streaming-first.xml`:14passed/3failed from Windows fsync on a read-only handle. Changed final artifact flush handle to r+b, then24 existing workbench cases passed in `streaming-regression.xml`.
- First real HTTP500k-row run `benchmark-f6e11ab7-3ef5-4669-8d73-af895dfa250a.json` failed on a503 while parsing340,992rows. Original source, DB and resource samples are retained. Schema4 separates immutable large source BLOBs from frequently updated metadata; journal_mode is set once at startup. This failure is not excluded or relabeled as a throughput success.
- Second large run `benchmark-bfade126-93f4-4616-9944-fe75b9e0ee3c.json` also returned503. Its retained DB shows all500,000 rows parsed to draft, but the last connection close performed a large WAL checkpoint while new polling connections opened. A lifecycle-owned connection now keeps WAL open without holding a transaction; shutdown closes it after workers. The re-run must pass before calling the issue resolved.
- Parent review found rejection/error responses bypassed the30s send deadline. Six deadline-path tests first failed with the pre-fix code (`send-deadline-before.xml`); all responses now use the same bounded sender. Ten related response/admission/supervisor tests pass in `send-deadline-after.xml`, with no repeated headers or leaked admission slot. The normal-response case also establishes an injectable short deadline for deterministic testing; its old default was already30s.
- `faults-first.xml`:19initial faults passed under the shared lock. Further model bounds, failed-supervisor cleanup and worker-submission tests were added later; final results are reported separately.
- Browser script03 waited for mapping before clicking the actual “Open batch” link; upload itself succeeded. Script04 inspected the current page and script05 followed the real button. This is a test navigation correction, not a product failure.
- Browser script08 encountered a noninteractive save toast intercepting a subsequent click. Added pointer-events:none, then script10 completed filter/selection.
- Script11 revealed untouched empty form drafts could mask a newer saved disposition. Persist only dirty forms; old empty incidental drafts are ignored. Script12 verifies current disposition, language draft preservation and save/reload.

## Results and release scope

The current work area is under verification. Final benchmark, model, package and browser receipts must be read before claiming any capacity or ready status. An overall partial result remains appropriate while required sponsor/platform access, business demand and AI advantage are unresolved; these are not replaced by passing generic tests.

中文：机制已进入真实主链，不能拿测试数量当完整生产能力。单机可复现范围、首次/重复/重启后的延迟、RSS测量窗口、成功/拒绝/超时分别记录。外部平台注册与赛事要求沿用交接边界，本轮未对外发消息、付费、上线或投稿。
