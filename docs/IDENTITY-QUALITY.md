# Identity quality and refusal / 身份冲突核查

The current scope is a source-quality experiment. It does not establish product demand, AI superiority, a real Zetaris connection or a delivery promise on real business data.

原问题不是“重复ID之前会被放行”：旧核验器已经拒绝，但所有重复都只报`duplicate_evidence`，页面失败分支没有展示原始证据。现在区分相同内容重复和不同内容冲突，并保留可核查的两侧行。

Flow: raw read-only source rows and trace → existing schema validation → `quality.validate_identities` → either unchanged date arithmetic, or422 refusal with `data_quality` → SQLite failed-run persistence → bilingual explanation and JSON export. No row is removed, replaced or automatically trusted.

`source_identity_conflict` means one declared ID has different content. It can be a bad join/key, two versions or legitimate lines under an unsuitable identity; the program cannot decide which. `duplicate_evidence` means the same ID and content reappeared, which can also reflect replayed pages. Neither condition permits silent deduplication. Distinct line IDs are not merged just because SKU matches.

每条诊断提供来源tool、记录ID、原始响应内从1开始的行位置、变化字段、两行SHA-256。哈希只标识内容，不证明内容真实或来源权威。原始行与查询保留在evidence中；若真实实验有CSV物理行号，单独显示，不能混成平台分页位置。模型输出、HTTP认证错误等不作为数据质量诊断透出。

Real sample: UCI Online Retail, official CSV first10,000 data rows. Invoice536381/product71270 has Quantity1 at CSV115 and Quantity3 at CSV127. These may be legitimate invoice lines. The candidate composite key is refused; snapshot+physical-row identity passes identity checking only. All three sample cases disallow business promises because inventory, delivery due dates and supplier terms are absent. No due date is inferred from InvoiceDate.

Reproduce from project root:

```powershell
.\.venv\Scripts\python.exe scripts/check_order_identity.py
.\.venv\Scripts\python.exe -m pytest tests/test_identity_quality.py tests/test_real_identity_sample.py -q -p no:cacheprovider
node --check web/app.js
```

`--persist` on the experiment command optionally records the actual local preflight as a failed run in the existing UI. It consumes one local request unit and is explicitly labeled`identity_preflight_experiment`; it does not claim `/api/check` or official-platform execution. Run620625f4-d6f4-4afa-a072-973a788b7eca is the actual2026-10-23 experiment.

技术栈：Python/Pydantic校验结构；字典按ID线性扫描，SHA-256标识原始内容；FastAPI传递受控诊断；SQLite记录失败；原生JavaScript用文本转义显示差异和导出。复杂度约O(n×每行字段数)，此应用原有每源500行限制；未新增数据库、模型、外部依赖或自动修复。

取舍：不自动选择最新或数量最大的记录，可能使本可人工快速判断的数据也被拒绝，但避免把身份映射猜测当作确定业务事实。只报告当前首先失败的数据源，未新增全量跨源冲突发现。UCI例子验证导入键的歧义，不验证真实Zetaris数据冲突或跨系统权威排序。

学习/面试：

- 为什么发票号+SKU不一定唯一？一张发票可能有同商品多条合法明细，需要原系统明细ID或明确聚合规则。
- 两行内容相同为什么不能直接删除？它们可能代表两次真实动作；内容相同不等于业务身份相同。
- 行哈希解决什么？定位和复现“当时看到了什么”，不能证明内容可信。
- 为什么失败也保存？对账人员需要依据定位源记录；只报错没有上下文无法闭环。
- 为什么不用LLM决定谁对？源权威、版本和业务状态是缺失事实，语言流畅不能补齐。AI解释建议是否有价值需另做对照实验。

Actual scoped results and preserved failed attempts: this round's result.md/json, runtime/identity-final.xml and runtime/real-identity-final.xml. The former mock HTTP test is explicitly a client-contract test, never a live Zetaris result. Source/license: [real sample](../data/real-samples/README.md).
