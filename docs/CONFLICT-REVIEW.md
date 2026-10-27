# 冲突逐条处理与可追溯导出 / Conflict review and traceable export

本轮服务的目标人群是假设中的订单 CSV 核对人员：发现同一候选键出现多行后，回到来源核查，保留尚未确认的情况，再交给下一位处理人员。没有访谈、实际使用量或节省工时证据。

这次补齐的价值是**不必为了导出而先把冲突“解决”**。每对记录可以尚未处理，也可以追加“仍未决”或“保留两行、身份待确认”的意见。导出始终带原始结果和全部处理历史，未处理的冲突仍在其中；记录意见不让失败核验变成成功。

## 本地操作

1. 打开本地8212，点“最近核验”中的真实 UCI 预检记录。演示 Run `a42d3636-a5a4-4b37-a68d-0e61be583d76` 包含两对真实公开交易行。
2. 第一对是 CSV 115/127：发票536381、商品71270，数量1与3。它们可能是合法的两条明细；候选键不够唯一，不意味着原数据有错。
3. 第二对是 CSV 496/519：发票536409、商品21866，内容相同。相同内容仍不足以证明应删除其中一条。
4. 每对填写记录者自述标识、处理状态和理由，点击“追加处理记录”。这是本地备注，不是认证身份或来源负责人的签字。演示记录明确写 Agent demo。
5. 点击“导出原始证据与处理历史”。未填写的冲突也能导出；未保存的表单输入不会导出。切换中英文保留原始证据、已保存意见及当前未保存输入；浏览器整页刷新前必须保存输入。
6. 重新打开记录能看到已保存的追加历史。改正意见会添加新记录，旧意见仍存在。两个页面同时操作，过期页面的保存会收到409；刷新历史并重新核对后再保存。

## 导出的内容及边界

`original_run` 包含原请求、原失败状态和完整结果。两侧原行、响应位置、CSV实际行号、两行SHA-256、来源地址、时间及前10000行快照哈希保留。CSV行号含表头，未验证为原工作簿行号。`review` 包含全部冲突、递增版本和追加事件；每个事件关联冲突索引、诊断、证据指纹、时间、记录者自述及理由。

SHA-256用于核对本次保存的副本，不能证明来源可信。SQLite记录通过应用接口仅追加；数据库文件可被本机管理员修改，不能声称防篡改账本。程序没有账户认证，多人协作只做版本冲突检查，没有身份权限系统。

`business_decision_allowed=false`、原失败状态、`auto_resolved=false`保持不变。保留两行不等于确认两笔合法业务；仍需取得来源明细ID/修正后的证据，再另行核验。没有自动去重、选赢家、欺诈标签、库存写回或采购操作。

## 实现与学习

- FastAPI + Pydantic限定可写字段和两种状态，拒绝空白理由、伪造业务结论和未知字段。
- SQLite的独立`conflict_reviews`表追加事件，不更新`runs.result`。保存使用`BEGIN IMMEDIATE`串行校验“我看到的版本”和当前版本，解决检查后再写之间的竞争。
- 整个原始结果的SHA-256绑定证据版本，防止处理意见附到不同证据。版本过期返回409，用户需要重新核对，不悄悄覆盖。
- 导出在一次SQLite读事务中取得原结果和历史，避免前后两次读到不同的版本。原生JavaScript表单保存未提交输入；翻译只作用于界面标签。
- 不增加依赖、后台进程或模型调用。原有身份校验、供货计算、模型提示和评分没有改变。

面试可讲：发票号+SKU为什么不一定是行主键？业务身份与内容哈希有什么区别？为什么人工意见必须与证据分开？为什么“数据库有事务”还需要版本检查？失败状态为何仍应支持导出？回答时用115/127这对真实行说明，不能说是线上客户数据或生产收益。

## English walkthrough

The intended user is a person reconciling order CSVs. Demand remains a hypothesis: no interviews, adoption or time-saving measurements are claimed. The added value is to record an unresolved case and hand it over with its original evidence, without pretending that the source identity has been fixed.

Open the real UCI run in Recent checks. CSV rows115/127 share invoice536381 and stock71270 but contain quantities1 and3; these may be valid separate invoice lines. Rows496/519 have identical content, which still does not authorize deduplication. For each pair, enter a self-declared recorder label, choose Unresolved or Retain both, and append the rationale. Agent demo notes are workflow demonstrations, not source-owner confirmation.

Export works even when a pair has no review event. It preserves original rows, hashes, positions and refusal together with all saved annotations. Unsaved input is excluded. Language switching preserves source text and drafts; a full page reload requires saving first. A conflicting save returns409, requiring a refreshed inspection. Corrections append events instead of replacing earlier ones.

Implementation: strict FastAPI/Pydantic input; a separate SQLite event table; an immediate write transaction with expected revision and evidence hash; a consistent read transaction for export; plain JavaScript bilingual forms. The review is local, self-declared and not a tamper-proof or authenticated audit system. No model inference, source-row mutation or commitment verdict is produced by this flow.

Local workflow validation, model semantics, sponsor acceptance and contest submission remain distinct. Zetaris real integration is incomplete (registry401 is a known prior blocker; no Docker retry this round). Meterless has prior local H-MEM reference-engine evidence, but this annotation workflow is SQLite-backed and is not a new Meterless integration or official acceptance. Cursor is optional according to the latest saved official-rule evidence. LinkedIn onboarding and final submission remain incomplete. No public release or final submission was performed.
