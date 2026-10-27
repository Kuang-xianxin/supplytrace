# Authentic invoice identity sample / 真实发票行身份样例

Source: Chen, D. (2015), **Online Retail**, UCI Machine Learning Repository. DOI https://doi.org/10.24432/C5BW33 . Metadata https://archive.ics.uci.edu/dataset/352/online+retail . CSV URL returned by UCI's own metadata API: https://archive.ics.uci.edu/static/public/352/data.csv . License: **CC BY 4.0** (metadata verified 2026-10-23).

`uci-order-identities.json` keeps original string values from selected rows in a bounded prefix of the official CSV. It includes retrieval time, prefix SHA-256, exact CSV row numbers and transformation details. Customer identifiers are the dataset's public numeric IDs, not real names. No private business data was added.

发票536381、商品71270在CSV第115和127行的数量分别为1和3。“发票号+商品号”不能直接当作唯一明细ID。这不说明原数据错误；同一商品可能合法分多条明细。程序既不选较大/较新的一行，也不自动合并或删除。

Source locations identify rows **within this exact snapshot**, not globally stable business IDs. Identical strings alone also do not prove a row is accidental duplication. The dataset has invoice timestamps, not promised delivery dates, current stock or supplier ETA. It cannot establish a real delivery promise.

```powershell
.\.venv\Scripts\python.exe scripts/check_order_identity.py
# Optional: persist the actual refusal for the local history UI; no AI or Zetaris call
.\.venv\Scripts\python.exe scripts/check_order_identity.py --persist
```

The workbook download failed and its range retry was unsupported. The successful experiment uses the officially provided CSV prefix. CSV row references must not be described as verified workbook positions. Failures and acquisition evidence are retained in this round's records.
