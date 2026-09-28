# 共用接口

- `tour.api.warehouse_documents`：受限子进程执行原生 DOCX/Poppler 解析；只返回固定失败阶段。
- `tour.api.route_fields`：具有原文依据的增量字段；`cloud_warehouse.route_consistency` 定位冲突。
- `cloud_warehouse.route_extraction`：可选的带行号模型字段候选。只补通过核验的缺失字段，规则有效值优先，冲突待审。
- `tour.api.route_document_workflow`：本地共用入口；`candidate.json` 是原始候选，`edited-candidate.json` 是改写候选，`validation.json` 和私有证据保存差异。
- `cloud_warehouse.route_editing`：逐日改写与校验；编辑续接结果见 `editing.private.json`，失败日保留原稿。

`POST /v1/documents/{asset_id}/reparse` 必须提供当前 `expected_parse_id` 与原因。它只产生新一代解析。`warehouse reparse-diff --organization UUID --user UUID --report /private/new-report.json` 默认只读；`--apply` 才排队重新解析。报告必须写入私有位置，每页最多 100 个文件，返回游标后按页继续。

商户通过 `/v1/merchant/routes/{id}/content` 读取和保存草稿，携带服务端的修订号与来源哈希。`/validate` 返回可定位问题；发布仍走 `/v1/route-content-proposals` 及人工审批。

多版本来源保留原文行号范围。选择版本、适用日期和城市后，顾问使用 `/v1/advisor/products/{id}/document?departure_id=WD-...` 获取适用已发布内容；没有匹配版本时不得借用另一团期的行程。

原始文件、解析证据、字段来源与复核身份只在供应商权限下读取。云仓和 Codex 都不把密钥、订单或客户资料放入模型输入。
