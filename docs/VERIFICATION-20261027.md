# Verification on 27 October 2026

All measurements below come from actual retained local executions. The host runs Windows with 16 GB RAM and other workloads. OS caches were not controlled. These results do not establish production capacity or customer benefit.

## CSV scale cases

| Generated input | Groups | Parse | Analysis | First complete export | Peak service-tree RSS |
|---|---:|---:|---:|---:|---:|
| 2,000 rows | 1,000 | 0.235 s | 0.859 s | 0.250 s | 112,390,144 bytes |
| 500,000 rows / 40,500,025 bytes | 250,000 | 25.500 s | 92.094 s | 55.156 s | 228,192,256 bytes |
| 250,000 rows sharing one key | 1 | 12.891 s | 32.078 s | 20.516 s | 170,254,336 bytes |

For each export, the runner verified the original SHA-256, the number of derived CSV rows and every group membership record. The 500,000-row first detail request took 16 ms. A separate first group-page request after process restart took 550.56 ms. These are different endpoints and cache states, not an interchangeable latency claim.

The same run exercised mixed group pages and substring searches for about ten seconds at each concurrency. At 1, 10 and 30 clients it observed 828/488/179 successful responses and 0/36/1,364 bounded rejections. No network timeouts or unexpected HTTP failures occurred within those load phases. This is deliberate overload protection, not a claim that all clients receive a successful result.

The full original run later stopped on an HTTP 503 during rapid download interruption, so it remains recorded as **incomplete**. A targeted continuation completed 20 disconnects, with service-tree handles moving from 409 to 417. A test-harness restart initially checked the port before all killed children had exited. The retained retry waited for the owned process tree and successfully recovered a 150,000-row import interrupted in the `parsing` state. It finished with zero unfinished jobs and zero worker errors. Earlier failed scale runs remain retained.

## Model assistance

The first new bilingual holdout showed leading questions and failed semantic review even though the output schema and row references were valid. That result was kept. After revising the task instruction, six fresh holdout calls produced valid references and neutral requests for original evidence in 1.80–3.34 seconds. Some observations still only repeat numeric values. This is a narrow evaluation, not a general accuracy metric or proof of business value.

The actual workbench call on the labeled UCI example returned source positions 115/127 and quantities 1/3, then requested the original invoices and event IDs. The human note stayed empty. Suggestions cannot change source rows or save a review.

## Regression scope

- 62 affected tests passed after connecting the workbench assistant and local Zetaris login/normalization adapter.
- 35 affected workflow/provenance tests passed after separating data origin from transport. These overlap with the preceding suite and must not be added together.
- JavaScript syntax checks passed for both interfaces.
- Meterless persistence/recall/audit tests execute the pinned official reference engine in a real local Node subprocess.

API mocks verify contracts and error handling. They are not live Zetaris platform acceptance. The separate live acceptance checks exact imported rows, one-row pagination, cursor closure and the expected deterministic verdict. Its result must be inspected before claiming that integration is verified.

Retained private evidence identifiers: scale `6a8bf174-119d-4c2a-9cfc-c193126ad756`, disconnect `070a7b4c`, recovery `2aaad570`, regression `171643-6771038d` and `175702-65333e39`. Source publication will contain only reviewed, credential-free evidence extracts.
