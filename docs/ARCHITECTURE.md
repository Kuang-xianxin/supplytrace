# SupplyTrace architecture, version 0.3

## Delivery integration, 2026-10-27

The same local service now exposes `/promises` alongside the CSV workspace. A model proposes an order for four approved source reads. The adapter executes only validated, parameter-bounded queries against inventory, orders, shipments and supplier data; deterministic verification computes the decision. A real Meterless H-MEM reference process persists decisions, recalls related history and exposes its ledger. Sequential source reads have individual timestamps and do not constitute an atomic cross-system snapshot. CSV imports do not modify these procurement tables.

The CSV assistant receives at most the current 30-row page and suggests neutral questions. A generation/revision/digest check rejects stale requests, one-call admission bounds model concurrency, failed attempts consume the daily quota, and response evidence is retained. The model cannot write review dispositions or change originals. Data origin and transport are separate: original synthetic fixtures stay marked synthetic even when the actual query runs on Zetaris.

SupplyTrace is a local procurement CSV review application. This architecture supports an explicit one-process workspace and concurrent requests; it does not provide public accounts, organization authorization or multihost collaboration. SQLite is an intentional transaction store, while DuckDB executes the analytical work. Replacing SQLite with PostgreSQL alone would not fix whole-file Python lists, enormous duplicate-key arrays, unbounded downloads or lost job state.

## Main data flow

1. **HTTP admission** (`boundary.py`): 16 active requests cover body receive through response drain. Uploads have2 slots, per-file64MiB, body idle15s, total upload budget120s. Non-upload bodies are10,000bytes even without Content-Length. Cross-site mutation origins are refused; trusted hosts and CSP remain enabled.
2. **Original preservation** (`workbench.py` / `workspace.create_file`): network chunks go to a unique disk staging file. A transaction checks request-key/parameters and workspace quota, inserts an incremental SQLite BLOB in immutable import_sources, and records a durable queued job. Mutable imports metadata stays separate from the large BLOB; updating progress must not rewrite a huge source record. Schema4 preserves legacy imports.raw in place and falls back to it for old batches. Raw bytes enter the canonical database before parsing; failed staging/CSV evidence is retained.
3. **Streaming parse** (`csv_engine.records` / `workspace.parse`): a64KiB buffer decodes UTF-8/GB18030 incrementally; csv.reader retains physical start/end lines including quoted multiline cells. Fields/records/row count are bounded. At most512rows and approximately1MiB serialized data form a batch; each record has its own upper bound. Batches commit progress and original rows. Restart re-reads source from byte0 and idempotently retains prior rows; this is checkpointed replay, not a resumable network upload or byte-offset parser.
4. **Arrow and DuckDB** (`Analysis.append/group`): bounded batches become Arrow tables and enter a job-specific disk DuckDB database. Original cells remain strings. Decimal is converted to an exact canonical comparison token without float rounding or enormous exponent expansion. Grouping uses count/min/max, never list aggregation or a Python dictionary containing all members. DuckDB has1thread,128MB buffer budget and1GB spill limit; this is not a128MB process-RSS guarantee. External access and auto-download paths are not used.
5. **Publishing analysis** (`workspace.process`): indexed relational membership holds one row per group member. Ordered row hashes create a streaming group digest. Intermediate generations are not readable as ready results; the last transaction publishes ready/counts. Old generations remain retained. Cancellation and deadlines interrupt the engine; generation predicates prevent stale workers changing later jobs.
6. **Read/review** (`groups/detail/events/review`): SQL applies filters and30-item limits before Python serialization. Current review and reviewed counts are transaction projections of immutable append-only events; history is paginated. CAS revision checking and parameter-bound idempotency share the write transaction. A128-entry/4MiB singleflight cache has jittered15s TTL and a3s waiter budget; keys include authoritative generation/revision. Errors and nonexistent IDs are not cached. No decision writes depend on cached data.
7. **Export** (`workspace_export.py`): one SQLite read snapshot streams CSV/JSON/member records and original BLOB into a disk ZIP; the browser receives a FileResponse, not a JS Blob. Formula-looking derived cells are escaped; original/evidence cells are unchanged. Original, group evidence and cached ZIP hashes are verified. The original event log remains the source of truth; projections are rebuildable. A successful export never implies the original business lines were invalid or approved.
8. **Tracing** (`tracing.py`): OpenTelemetry SDK propagates persisted traceparent through import/parse/analyze/aggregate/export. Exporter emits structural timing/status only, excludes cells/filenames/exception messages and stops after16MiB rather than deleting logs. There is no external telemetry endpoint. Traces are useful for diagnosis, not a business audit ledger.

## Recovery, deadlines and capacity

`work_jobs` commits with the triggering business transition. At most6 actual unfinished futures are admitted, including cancelled tasks still waiting in ThreadPoolExecutor. A supervisor polls queued work and catches transient database failures; executor submission failure returns503 while the durable task remains recoverable. Unexpected execution retries are limited to3; business failures require explicit retry. An OS file lease prevents two running services for one workspace and is automatically released after process death.

CSV jobs have a180s budget. SQLite writes wait at most1.5s for locks; group SQL has a2s progress deadline. Export assembly has one slot and a180s budget; response writes have a30s drain timeout. SQLite cursors, BLOBs, Arrow registration, DuckDB connections and ZIP streams have explicit ownership. No database write transaction waits for a model or remote provider.

Storage boundaries:1000imports,1GiB canonical originals,20,000review events per import. Workfiles have a2GiB admission threshold and a256MiB free-space reserve; in-flight concurrent writes can temporarily exceed the admission threshold. No automatic bulk cleanup is implemented. Old analysis files, failed partial exports and staging files are retained; operators must review a bounded cleanup plan and obtain deletion approval. Public workspaces and general SQL submission are not supported.

## Dependencies and selection

Python3.12, FastAPI0.115.12/Pydantic2.14.1, DuckDB1.5.6, PyArrow26.0.0, OpenTelemetry1.45.1; psutil7.2.2 is used for actual process measurements. Browser code stays semantic HTML/CSS/ES modules: routing/render generations and transactional server state are sufficient for this workbench, so an unrelated React migration would not improve the specified failure boundaries.

Official references read during this change: DuckDB's Arrow guide and workload tuning guide, Arrow installation documentation, and OpenTelemetry instrumentation documentation. Stable DuckDB URLs currently redirect to2.0; this implementation pins1.5.6 and verifies the used API by execution. Sources and actual installed licenses are in THIRD_PARTY_NOTICES.md. No downloaded model or GPU daemon was started by this project.

## 学习与面试

- 为什么分批读取不等于流式？如果先`decode`整份CSV、`fetchall`或在group里累积所有行，后续每次取512行仍已占用了全文件内存。本实现从网络/BLOB/解码器开始有界，成员存在关系表，导出也直接写文件。
- 为什么用了DuckDB还保留SQLite？SQLite负责小事务、版本冲突和事件；DuckDB负责列式批处理和磁盘聚合。两者没有跨库事务，因此DuckDB结果先写未发布generation，最后在SQLite发布ready；失败可重算，原件不丢。
- 如何防缓存击穿与脏读？同键只有一个loader，其余等待有上限。写意见改变revision，旧缓存自然失效；真实计数和版本仍从事务库读取。TTL只控制内存复用，不决定业务正确性。
- 为什么取消不能马上释放队列名额？已提交future可能仍在执行器队列内。只有任务真正退出才释放信号量，否则反复取消/重试会撑爆隐藏队列。
- 怎样解释“内存泄漏已解决”？只能给定输入、预热、测量窗口和RSS/句柄趋势；C++原生分配不在tracemalloc里，缓存高水位也不是泄漏。报告记录整个服务进程树，而不只测纯Python函数。
- 如何保证交接包可追溯？一致读快照、原件SHA256、物理行号、逐组有序行哈希、独立意见历史和只读原文。哈希证明本地一致性，不能证明来源可信或记录者身份真实。
