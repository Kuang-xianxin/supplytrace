# Integration contracts

## Zetaris

The application implements the official paginated SQL query contract. On 27 October 2026, the running official local platform passed exact row comparison for all four sources, one-row pagination and cursor closure. Two real model-driven requests also passed with H-MEM persistence, recall and audit. See [platform evidence](evidence/zetaris-live-20261027.json) and [workflow evidence](evidence/live-workflow-20261027.json). This is technical acceptance on synthetic records, separate from organizer certification.

Official references inspected: [SQL command reference](https://github.com/zetaris/getting-started/blob/main/docs/guides/zetaris-lightning-sql-commands.md), the official local bundle's `verify.sh`, and [API authentication](https://kbase.zetaris.com/knowledge/accessing-zetaris-using-api).

| Operation | Contract |
|---|---|
| Local UI authentication | `POST /api/auth/login` with private username/password, use returned token and numeric `orgId` |
| Start | `POST {prefix}/query/sql/start`, JSON `{select, pageLimit}` |
| Page | `GET {prefix}/query/sql/page` with query token, page size and page number |
| Close | `DELETE {prefix}/query/sql/close/{queryToken}` to release the query resource |
| Authorization | Bearer token, numeric `X-Org-ID`, new `X-Request-ID` |

The direct API prefix is `/api/v1.0`. The official Docker UI proxy uses `/api/proxy` on `http://localhost:3000`. The adapter permits HTTPS endpoints or loopback HTTP only. No credentials appear in trace output. The returned org ID is used rather than assuming organization 1.

```dotenv
DATA_BACKEND=zetaris
ZETARIS_BASE_URL=http://localhost:3000
ZETARIS_API_PREFIX=/api/proxy
ZETARIS_CATALOG=supplytrace
ZETARIS_API_KEY=
ZETARIS_ORG_ID=
ZETARIS_USERNAME=<private local account>
ZETARIS_PASSWORD=<private local password>
DATASET_KIND=original_synthetic_demo
DATASET_AS_OF=2026-10-22
```

For a hosted tenant, use its official HTTPS API URL, `/api/v1.0`, issued API key and org ID. Platform authorization remains the account operator's responsibility. The demo application's four hard-coded SELECTs are not a substitute for server-side least privilege.

The four original files in `data/zetaris-import/` match `data/synthetic.json` exactly:

- `inventory`: id, sku, updated_at, on_hand, damaged, warehouse
- `orders`: id, sku, updated_at, quantity, due_date, status
- `shipments`: id, sku, updated_at, quantity, eta, status, supplier_id
- `supplier`: id, sku, updated_at, supplier_id, safety_stock, handling_days, buffer_days, lead_days

Register these as `supplytrace.inventory`, `.orders`, `.shipments` and `.supplier` using the official file-source setup. The source must be readable by the query engine. Dates are ISO calendar dates. The platform may serialize integers as strings. The adapter converts only declared integer fields, rejects fractional/malformed/missing values, and preserves the unmodified platform records in each trace.

The verified local setup mounts that directory read-only at `/home/zetaris/customer-data/shared/supplytrace` in the official driver and API containers. After inspecting `SHOW LIGHTNING DATABASES`, it creates the new database and each absent table with the official SQL Editor endpoint. Example, one statement per request:

```sql
CREATE LIGHTNING DATABASE supplytrace DESCRIBE BY "Original synthetic SupplyTrace fixtures"
CREATE LIGHTNING FILESTORE TABLE inventory FROM supplytrace FORMAT CSV OPTIONS (
  path "file:///home/zetaris/customer-data/shared/supplytrace/inventory.csv",
  header "true", delimiter ","
)
```

Repeat the table statement for `orders`, `shipments` and `supplier` with the matching filename, then inspect `SHOW LIGHTNING TABLES supplytrace`. The provisioning endpoint is `POST /api/proxy/sql-editor/sqls/run-query` with `{sql, limit: 1000}` and the same authentication headers. Existing registrations must be inspected rather than replaced. Container mounts and credentials belong in private local configuration.

Only trusted application code creates SQL. The model cannot choose SQL text, columns, endpoints or credentials. Pagination must match the query token, declared total count and page count. Results have a 500-row/5-page bound. Authorization, quota, timeout, incomplete results and cursor failures remain explicit. Zetaris mode never falls back to local rows.

```powershell
python scripts/verify_zetaris.py --offline --output-dir ./verification
python scripts/verify_zetaris.py --live --env-file .env --output-dir ./verification
```

The live verifier reads the actual sources with page size 1, checks exact original row values, verifies every cursor closure, and checks LAMP-01 capacity 55 on 28 October. A successful technical check is not organizer certification. Source queries are sequential, not an atomic cross-source snapshot.

## Meterless H-MEM

The pinned official H-MEM reference runs through `integrations/hmem.ts` in a real Node subprocess. Python serializes the bridge calls. `HMEM.add`, `HMEM.query` and `ledger.history` create, recall and audit local memories under the selected runtime directory. A process restart reloads the saved state.

Recall is SKU-scoped and capped at five. The application uses a 0.2 recall threshold for the narrow demo histories; the reference default is 0.35. Prior decisions remain historical context, not current inventory or authority for a new promise. The application exposes no dreaming, deletion or automatic promotion. A failed memory operation is visible in the result.

This is the official open reference engine, not a hosted Meterless API or billing product. Source commit and file hashes are in `vendor/meterless/manifest.json`; Apache-2.0 license and third-party notices remain intact.

## Model and GPU

The planner calls an OpenAI-compatible local chat endpoint with a constrained response schema. It may order four required source reads and supply a rationale. The validator rechecks allowed tools, requested SKU, coverage and model identity. Deterministic Python computes the final quantity/date verdict. Model failures remain failed runs.

The separate CSV assistant sees at most the current 30 original rows. Suggestions cite source rows and cannot edit data or save a human review. The server binds cached results to the input hash, prompt hash, model, language and pagination. A stale revision is rejected. One local suggestion call runs at a time, and failed attempts still count against quota.

Development inference uses Qwen3.5-4B Q5_K_M through llama.cpp. The actual NVIDIA RTX 4060 Laptop GPU executes the Vulkan backend. No Nemotron, NIM or CUDA use is claimed. Weights and inference binaries remain outside the repository.

## Data origin and local quota

`synthetic` describes the dataset, while `data_source` describes the transport. Original synthetic fixtures remain synthetic when queried through Zetaris. `DATASET_KIND=operator_supplied` is an explicit operator assertion for separately supplied records, not automatic verification of their origin.

The SQLite request budget is application-local. It reserves an attempt before model/source work and counts failures. It is not Meterless billing or a payment integration. No purchase or procurement action is exposed.
