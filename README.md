# SupplyTrace

**Traceable purchasing decisions, from original records to a dated explanation.**

SupplyTrace helps a purchasing or operations colleague inspect source records and answer a delivery-date question. It preserves the original data, shows the calculation behind a promise, and keeps earlier decisions available for comparison.

This Open Agent Hackathon submission targets **Track 02: The Agent That Can Explain Why**. See [build disclosure](docs/BUILD-DISCLOSURE.md) for coding-agent assistance and pre-existing components.

## Two workspaces

- **CSV review:** import a file, choose a candidate business key, compare all original rows and physical CSV positions, request neutral model questions, record a human disposition, and export the original plus review history. Differences do not automatically mean errors. Original rows remain unchanged.
- **Delivery verification:** a bounded model plan orders four approved reads from inventory, orders, shipments and supplier terms. Zetaris supplies the records. Deterministic code protects safety stock and existing commitments, computes usable shipment dates, and produces a cited timeline. Meterless H-MEM remembers the result and exposes prior decisions and its ledger.

These workspaces use separate configured inputs. Importing a CSV does not automatically change the platform's procurement tables. Human review notes never authorize a purchase.

## Run locally

Python 3.12 and Node.js are required for the full demo. Windows is the tested host. Keep one application process per workspace.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
npm ci
Copy-Item .env.example .env
.\.venv\Scripts\python.exe scripts/run_demo.py --runtime ./runtime-demo
```

Open `http://127.0.0.1:8212`. The default `.env` uses the original synthetic procurement fixture. CSV review and rules mode work without a model service. H-MEM runs locally through the bundled, pinned reference engine. The launcher reads only this project's `.env` and ignores inherited provider credentials.

For model assistance, set `OPENAI_BASE_URL`, `OPENAI_MODEL` and the private endpoint key in `.env`. The verified development endpoint served Qwen3.5-4B Q5_K_M through llama.cpp on an NVIDIA RTX 4060 Laptop GPU using Vulkan. Model weights are not distributed here. A generic compatible endpoint may need to support the requested response schema. A model failure remains visible and does not silently become a successful rules run.

For Zetaris, obtain the official local bundle or an authorized tenant, register the four sample files, then follow [integration setup](docs/INTEGRATIONS.md). Keep sponsor credentials and licensed images outside the repository. Local synthetic mode is explicitly labeled and is not platform integration.

## Reproduce the main scenario

1. Open **Delivery verification** and use LAMP-01, quantity 30, as-of 2026-10-22.
2. Request 2026-10-26: the fixture supports only 15 units. The 60-unit inbound shipment is usable on the 27th after handling and buffer, so it cannot support the earlier promise.
3. Change the requested date to 2026-10-28: capacity becomes 55 units. Inspect the four source queries, the time line and the previous H-MEM decision.
4. Export the evidence JSON. In the CSV workspace, the optional four-row UCI example demonstrates source comparisons and model questions on a separate, clearly attributed public dataset.

The procurement records are **original synthetic data**, including when queried through Zetaris. They are not live customer inventory. The UCI example is real public historical data, not a customer deployment.

## Verification and boundaries

```powershell
.\.venv\Scripts\python.exe scripts/safe_run.py .\.venv\Scripts\python.exe -m pytest tests/test_workflow.py tests/test_dataset_provenance.py tests/test_review_assistance.py tests/test_workbench.py -q -p no:cacheprovider
npm run test:hmem
node --check web/workbench.js
node --check web/app.js
```

The 27 October retained HTTP run imported, analyzed and exported 500,000 generated rows in 25.5, 92.1 and 55.2 seconds respectively. The exported original matched the input SHA-256. A separate 250,000-row single-key case completed. A targeted follow-up checked 20 interrupted downloads and recovered a killed 150,000-row parse. See [verification record](docs/VERIFICATION-20261027.md) for scope and retained failures.

Input limits are 64 MiB and 500,000 rows. This is one local workspace with bounded concurrency, disk-backed processing and retained failure evidence. It is not a public multi-user deployment. Sequential source reads are not an atomic cross-source snapshot. Hashes establish record consistency, not source truth. Reviewers identify themselves; no identity verification is claimed.

On 27 October, the running official local Zetaris platform passed exact four-source row comparison, one-row pagination and cursor closure. Two complete requests used the real local model, Zetaris reads and H-MEM persistence/recall/audit, producing capacities 15 and 55 for the two dates. Reviewed records are in [live platform evidence](docs/evidence/zetaris-live-20261027.json) and [complete workflow evidence](docs/evidence/live-workflow-20261027.json). Contest submission and sponsor acceptance require official receipts and are not established by these technical checks or repository publication.

## Source map

| Component | Purpose |
|---|---|
| `supplytrace/workspace.py`, `csv_engine.py`, `workspace_export.py` | Bounded ingest, analysis, review transactions and complete exports |
| `supplytrace/review_assistance.py`, `assistant.py` | Row-grounded suggestions with quota, concurrency and provenance checks |
| `supplytrace/planner.py`, `adapters.py`, `verifier.py` | Approved query plans, source retrieval and deterministic date/quantity checks |
| `integrations/hmem.ts`, `supplytrace/memory.py` | Real Meterless reference execution and persisted memory/audit |
| `web/` | Chinese/English local interfaces |
| `docs/ARCHITECTURE.md`, `docs/BUILD-DISCLOSURE.md` | Design, constraints and dependency disclosure |

Application code is MIT licensed. The Meterless reference retains Apache-2.0. The UCI example retains CC BY 4.0 attribution. See [third-party notices](THIRD_PARTY_NOTICES.md).

## 中文说明

SupplyTrace 面向采购、业务人员的两类核对任务：先看清原始明细，再核对一个数量和日期是否有证据支持。

CSV 工作台保留原文件、原始行和实际行号。模型只根据当前展示的行提出核实问题，处理意见仍由人填写。交期核验读取库存、已有订单、在途和供应商规则，由程序计算安全库存、可用入库时间及可承诺数量，再用 Meterless 保存决策出处。

演示中的采购数据是原创合成场景；即使接入 Zetaris，也不会变成真实客户数据。当前没有客户访谈、节省时间或上线收益的实测结论。下一步计划用获得许可的真实记录，比较人工核对的准确性与耗时。
