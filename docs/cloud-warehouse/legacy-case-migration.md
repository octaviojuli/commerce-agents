# 旧顾问案例迁移

`tour.api.warehouse_legacy` 从原 SQLite 的一致性只读快照导出一个案例；`cloud_warehouse.legacy_cases` 保存不可覆盖的历史档案。保留会话文本、方案各版本、实际父版本、差异、参考价格及旧分享令牌哈希与版本的对应关系。每个旧部署使用稳定、独立的 `source_namespace`。

导出不包含旧登录状态、已见商品集合、工具调用、待执行事件或明文分享令牌。它不会创建可执行模型会话、有效报价、订单或公开分享。旧线路和团期编号保留明确的连接上下文，需要当前资料时通过旧编号解析接口查验。文件原文、顾问记忆、继续编辑旧方案及旧公网链接切换仍待后续交付。

## 导出与导入

先核对旧部署、会话、顾问、目标采购组织、已开户的平台顾问与供应连接，不根据姓名或同名产品猜测归属。归属矛盾、版本缺失、版本列与载荷不一致时整个导出失败。正式切换前冻结旧写入、备份 SQLite 及附件；启用 WAL 时使用数据库备份接口，不能只复制主文件。逻辑摘要用于检测变化，不能替代受控源备份或证明来源真实性。

以下变量必须来自已核对的归属映射；`WAREHOUSE_ADMIN_URL` 由私有环境提供。没有真实顾问和映射时保留待迁移状态。

```bash
umask 077
mkdir -p /private-migration/warehouse
chmod 700 /private-migration/warehouse
PYTHONPATH=examples .venv/bin/python -m tour.api.warehouse_legacy \
  /private-migration/legacy/sessions.sqlite /private-migration/warehouse/case.json \
  --session "$LEGACY_SESSION_ID" --legacy-user "$LEGACY_USER_ID" \
  --source-namespace "$LEGACY_DEPLOYMENT_NAMESPACE" \
  --organization "$BUYER_ORGANIZATION_ID" --user "$ADVISOR_USER_ID" \
  --connection "$SOURCE_CONNECTION_ID"

warehouse import-legacy-case /private-migration/warehouse/case.json \
  --target-database "$WAREHOUSE_DATABASE_NAME" --note "已按交接映射核对案例归属"

warehouse import-legacy-case /private-migration/warehouse/case.json \
  --target-database "$WAREHOUSE_DATABASE_NAME" --note "已按交接映射核对案例归属" --apply
```

导出文件只能新建，目标目录私有。档案含客户文本及历史同业价格，不能进入 Git、公共附件或日志。单案例上限 2 MB、5,000 条原始消息、200 个方案、每方案 1,000 个版本；超过即失败，不截断。

导入默认预检，`--apply` 才写入；校验目标库名、离线管理员权限、目标采购角色和当前来源授权。按“旧部署 + 旧会话”在事务内串行去重，同内容同归属重试返回同一档案，变化则拒绝覆盖。已归档案例不做增量合并，也不通过更换命名空间规避冲突。档案记录数据库维护角色、归属说明和时间，不伪造顾问审批。

## 读取与验收

- `GET /v1/advisor/legacy-cases?limit=25&before=UUID`：当前顾问的历史档案分页。
- `GET /v1/advisor/legacy-cases/{id}`：完整案例，返回 `historical_only=true`、`requires_revalidation=true`、`resumable=false`、`public_shares_enabled=false`。

两个接口要求平台登录及采购组织选择，禁止缓存。RLS 检查用户、组织、采购角色、来源启用状态和导入时的供采授权版本。同组织其他顾问或管理员不能代读；撤销或变更授权、停用用户或组织后不可读取，新版本授权也不自动恢复旧档案访问。运行时只有读取权限，没有导入 API 或模型工具。

云仓顾问页面的会话栏提供“历史档案”：分页选择案例后查看原对话、各方案版本、父版本差异和内部历史参考价。未记录的币种及团队总额不补算；历史内容不自动加入当前助手会话。页面重新聚焦及每 30 秒复查，权限失效或读取失败后清除内容，切换组织和退出后取消请求并清空档案。“核对当前线路”复查档案授权后，使用档案绑定的来源连接解析旧编号，再读取当前云仓产品；它不沿用旧价格或库存。

逐案例核对摘要、文本、各版内容和父版本、分享哈希对应关系，确认没有创建活动会话、报价或库存流水。测试使用原 `SqliteSessionStore` 生成虚构案例，覆盖 v3 基于 v1 的分支、来源隔离、并发幂等、跨用户拒绝及授权撤销；不能替代真实归属和旧分享链接切换签认。
