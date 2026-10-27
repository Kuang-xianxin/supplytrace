# Build disclosure

Submission owner: Xianxin Kuang. This is a solo submission developed with coding-agent assistance. The code, generated artifacts and dependency notices are available for review. No claim of unaided manual implementation or real customer deployment is made.

The original procurement fixture was created for this project on 22 October 2026. Work on the CSV workbench, persistence, bounded processing and delivery integration continued on 23–27 October. Historical failure records remain in the private development workspace. A source publication timestamp is not evidence of when every line was authored.

Pre-existing components:

- Meterless H-MEM reference engine and conformance suite, pinned to commit `1742dec56a66e58eef9dda2d2b865823758ca8cd`, Apache-2.0. The unmodified engine is a dependency. The Python/TypeScript bridge is project code.
- FastAPI, Pydantic, SQLite, DuckDB, PyArrow, OpenTelemetry, HTTPX, Uvicorn and tsx. Versions are pinned in the lock files.
- Qwen3.5-4B Q5_K_M, served by an existing local llama.cpp runtime. Weights and runtime binaries are not part of this repository. The application uses an OpenAI-compatible chat endpoint and records the configured/returned model identity.
- Four selected rows from UCI Online Retail, CC BY 4.0. Original dataset positions and attribution accompany the example.
- Zetaris' official local Docker bundle, images and JDBC driver. The private bundle and all credentials remain outside this repository and must be obtained through the sponsor's authorized channel.

The local model runs on an NVIDIA RTX 4060 Laptop GPU through Vulkan. This is GPU acceleration, not a claim of using Nemotron, NIM or CUDA. Cursor was not used for this delivery. Sponsor acceptance and the organizer's final judging remain separate from technical verification.
