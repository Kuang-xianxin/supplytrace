# Zetaris synthetic import / 合成数据导入包

The four CSV files are deterministic exports of `../synthetic.json`, created exclusively for SupplyTrace. They contain no real supplier, customer or employer data. Import them into four authorized Zetaris data sources/views and map the exposed names to `supplytrace.inventory`, `supplytrace.orders`, `supplytrace.shipments`, `supplytrace.supplier`.

这些文件只准备了原始合成数据，没有上传到 Zetaris。导入时需将数量/天数字段设为整数、日期设为 ISO 日期；API 返回的数据需满足 `supplytrace/models.py`。实际导入、凭据与跨源查询仍待验收。

2026-10-23 follow-up: `python scripts/verify_zetaris.py --offline` validated all 14 rows against the original JSON, strict row models and unique IDs, and recorded hashes. This is offline validation, not a platform upload. See `docs/ZETARIS-ACCEPTANCE.md` for exact import types and a real four-source acceptance with pageLimit=1. The new `--live` command has only reached a missing-credentials preflight; no tenant request was sent.
