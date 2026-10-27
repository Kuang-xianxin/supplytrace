# Third-party notices

## Current delivery, 2026-10-27

This release reunites the CSV workbench with delivery verification. It includes the pinned Meterless H-MEM reference, conformance files and application bridge listed below. The 23 October statement about excluding vendor files refers only to that earlier workbench-only ZIP. The local Qwen endpoint is now connected to the visible row-review assistant as well as the bounded procurement planner; weights remain external. Official Zetaris container images and credentials are not redistributed. Their deployment and real-query acceptance are reported separately in docs/VERIFICATION-20261027.md.

SupplyTrace application code is MIT licensed. The H-MEM reference and conformance suite under `vendor/meterless/engines/hmem/` are copied **without modification** from the official Meterless repository, commit `1742dec56a66e58eef9dda2d2b865823758ca8cd`, under Apache-2.0. The license is retained at `vendor/meterless/engines/hmem/LICENSE`; per-file SHA-256 values and source metadata are in `vendor/meterless/manifest.json`.

Source: https://github.com/Meterless/Meterless/tree/1742dec56a66e58eef9dda2d2b865823758ca8cd/engines/hmem

Observed 2026-10-22: 251 stars; repository created 2026-07-13. First public release/open-source date was not verified. These are provenance facts, not a quality or compliance score. This is a declared pre-existing open-source dependency, not original SupplyTrace engine code. Application bridge `integrations/hmem.ts` is new code. No sponsor endorsement is implied.

FastAPI, SQLite, HTTPX, Uvicorn and tsx retain their respective licenses. Python dependencies are pinned in requirements / lock files; JavaScript dependencies are pinned in package-lock.json. Public documentation copies in `docs/sources` are verification material, not relicensed as SupplyTrace code.

The authentic rows under `data/real-samples/` are selected from Chen, D. (2015), **Online Retail**, UCI Machine Learning Repository, DOI https://doi.org/10.24432/C5BW33 , licensed **Creative Commons Attribution 4.0 International** according to https://archive.ics.uci.edu/dataset/352/online+retail (accessed2026-10-23). They are not MIT-licensed application code. The official CSV source, retrieval time, prefix hash, selection and added candidate identity are documented in the fixture and its README. Original selected field strings are unchanged. No source-data ownership or real operational user is claimed.
# Product rebuild, 2026-10-23

The CSV workbench adds no production dependency. Existing locked Python dependencies were downloaded into a fresh verification environment: FastAPI/Starlette/Uvicorn/Pydantic/HTTPX/AnyIO/python-dotenv/pytest and their locked transitive dependencies. Package metadata and installed license texts remain in that environment. Python uses its PSF license; the principal application libraries use MIT/BSD licenses, certifi uses MPL-2.0. The source package does not bundle their wheels or redistribute the verification environment.

Existing esbuild0.25.12 (MIT, already installed with tsx) formatted the authored ES module and CSS; no Node package installation or cleanup was run. ui-ux-pro-max and Impeccable were used from the coordinator's already installed fixed versions; launcher/hooks were not executed. No font, image, model or dataset download was added. The public UCI case remains CC BY4.0 and is labeled separately from user uploads; generated procurement/scale fixtures are original test data.

The new local-workbench ZIP excludes the preserved legacy vendor/integrations tree described above. It does not claim to deliver a newly functioning Meterless integration. Those historical license references describe the original project checkout, not additional contents of this ZIP.

The parent coordinator supplied a shared local Qwen3.5-4B Q5_K_M endpoint for 18 evaluation calls, reported as Apache-2.0 with model SHA-256 `8814232b85594dcd46c50e5b8b29324a7efe9e746edbe8a3d1df3d3fce7aad39`. This worker did not download, bundle or redistribute those weights. Provider identity/source/license metadata are preserved in the private evaluation record MODEL-ENDPOINT.json; this notice is not an independent model provenance audit.

## Architecture hardening, 2026-10-24

The earlier statement that the workbench added no dependency describes the previous build. Version0.3 actually downloads and uses these pinned packages in its main processing path:

| Package | Version | License | Actual use |
|---|---|---|---|
| duckdb | 1.5.6 | MIT | Disk-backed typed facts and bounded aggregate/member cursors |
| pyarrow | 26.0.0 | Apache-2.0 | Bounded columnar batch registration into DuckDB |
| opentelemetry-api / opentelemetry-sdk | 1.45.1 | Apache-2.0 | Import, parse, analyze, aggregate and export trace chain |
| opentelemetry-semantic-conventions | 0.66b1 | Apache-2.0 | Locked SDK dependency |
| psutil | 7.2.2 | BSD-3-Clause | Whole service process-tree RSS, handle and thread measurements |

Official package metadata and installed license texts were inspected; all versions are pinned in requirements-lock.txt. A new clean Python environment installed the lock from wheels and passed pip check. Wheels, environments, generated stress data, user databases and model weights are excluded from the source ZIP. No additional model or external dataset was downloaded by this worker. The six new bilingual holdout calls reuse the coordinator-provided local model.

Documentation used: https://duckdb.org/docs/stable/guides/python/sql_on_arrow.html and https://duckdb.org/docs/stable/guides/performance/how_to_tune_workloads.html ; https://arrow.apache.org/docs/python/install.html ; https://opentelemetry.io/docs/languages/python/instrumentation/ . These official pages were accessed during implementation. DuckDB's current stable documentation redirected to a newer documentation version; execution and package claims in this delivery refer to the installed1.5.6 version, not an untested upgrade. Browser observations are local Egolite checks, not official integration certification.

