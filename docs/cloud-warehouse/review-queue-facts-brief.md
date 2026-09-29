# Brief：供应商逐项确认队列与天数/口岸核定的后续集成

## 已完成（提交 `cf7fe1a`，分支 `tour-review-queue-facts`）

1. **逐项确认队列**（仅结构化行程 `route-kit/1`）：`merchant-web/lib/route-review.ts` 把云仓的待确认事项转成问题卡片，`components/route-kit-review.tsx` 渲染，`components/route-kit-editor.tsx` 提供 审核 / 阅读 / 高级编辑 三个入口。供应商管理员点“确认无误并发布”一次完成保存、提议、批准、应用；产品编辑只提交审批。用法与约束见 `examples/tour/merchant-web/README.md`。
2. **解析器**：`examples/tour/skills/tour-route-parser/src/route_kit/extract.py` 给节点级问题附带 `subject`（节点名称），因为问题路径里的序号指向模型输出的原始列表，不是保留下来的列表。
3. **天数与出发口岸核定**：`cloud-warehouse/cloud_warehouse/product_facts.py` 与迁移 `0045_product_facts`。上游值不回写；云仓在旁边存核定值，只在上游仍是核定时的值时生效。说明见 `cloud-warehouse/README.md` 的“天数与出发口岸的云仓核定”。

## 尚未验证，请先做

- **真实云仓端到端**：以上前端流程只在模拟接口的页面里点过，后端由数据库测试覆盖。需要一条带疑点的真实草稿，在真实商户端走完：审核、核定、发布、线路列表标注、“上游数据待更正”面板。最好用标题与上游 `days` 不一致的线路（例如标题 7 天、上游 6 天）。
- **审批中心**：`components/approvals.tsx` 的 `RouteContentApproval` 仍按旧结构读取修订。确认结构化行程在审批中心里的展示，并补上 `fact_decisions` 的显示（接口 `GET /v1/route-content-revisions/{id}` 已返回 `fact_decisions` 与 `listing`）。
- **迁移 0045**：视图 `product_listing` 通过 `pg_get_viewdef` 追加列。请在带真实数据的库副本上试跑，并核对已有授权仍有效。
- **读取点是否遗漏**：已把顾问、目录、检索、商户列表、团期匹配等改读 `effective_days`、`effective_gateway`。请再全库检索 `p.days`、`p.gateway`，确认没有面向客户的读取仍用上游原值。`documents.py` 和 `_source_hash` 有意保留原值，不要改。

## 已知限制

- **旧稿没有 `subject`**：已入库的旧解析稿涉及具体景点的卡片会显示“未能自动定位”，只能保持现状或转高级编辑。重新解析后才有。
- **旧结构线路**（`RouteDoc`）不走队列，也不支持核定；`ContentForm` 保持原样。
- **口岸冲突可能偏多**：行程里的“出发地”与上游“口岸”含义可能本来不同。规则在 `product_facts.same_city`，看真实数据后再决定是否放宽。
- **核定失效后的重新提问**发生在下次审核；已发布内容不会自动下架。是否需要主动提醒，待定。
- **上游待更正清单**目前只有页面和“复制成表格”，没有导出到 ERP 的通道。

## 本机已知的无关失败

`cloud-warehouse/tests/test_goods_stock.py`（库中缺 `organization.short_name`，撤掉这次改动后同样失败）；`test_backup_jobs`、`test_capacity*`、`test_ci_runner` 在本机环境下失败，未逐个查因。

## 提交范围说明

该提交同时把此前从未纳入版本管理的 `cloud-warehouse/`、`examples/tour/merchant-web/`、`examples/tour/skills/`、`docs/cloud-warehouse/` 整体加入。原分支 `tour-aside-questions` 上其他未提交的改动没有包含。分支未推送，也没有开 PR。

## 验证命令

```bash
ruff check . && ruff format --check . && pytest && python scripts/check.py
```

云仓数据库测试需要 `WAREHOUSE_TEST_ADMIN_URL`、`WAREHOUSE_TEST_DATABASE_URL`，库名以 `_test` 结尾（见 `cloud-warehouse/README.md`）。
