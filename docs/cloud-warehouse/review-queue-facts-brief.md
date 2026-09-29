# Brief：供应商逐项确认队列与天数/口岸核定的后续集成

## 已完成（提交 `cf7fe1a`，分支 `tour-review-queue-facts`）

1. **逐项确认队列**（仅结构化行程 `route-kit/1`）：`merchant-web/lib/route-review.ts` 把云仓的待确认事项转成问题卡片，`components/route-kit-review.tsx` 渲染，`components/route-kit-editor.tsx` 提供 审核 / 阅读 / 高级编辑 三个入口。供应商管理员点“确认无误并发布”一次完成保存、提议、批准、应用；产品编辑只提交审批。用法与约束见 `examples/tour/merchant-web/README.md`。
2. **解析器**：`examples/tour/skills/tour-route-parser/src/route_kit/extract.py` 给节点级问题附带 `subject`（节点名称），因为问题路径里的序号指向模型输出的原始列表，不是保留下来的列表。
3. **天数与出发口岸核定**：`cloud-warehouse/cloud_warehouse/product_facts.py` 与迁移 `0045_product_facts`。上游值不回写；云仓在旁边存核定值，只在上游仍是核定时的值时生效。说明见 `cloud-warehouse/README.md` 的“天数与出发口岸的云仓核定”。

## 尚未验证，请先做

- **真实云仓端到端**：以上前端流程只在模拟接口的页面里点过，后端由数据库测试覆盖。需要一条带疑点的真实草稿，在真实商户端走完：审核、核定、发布、线路列表标注、“上游数据待更正”面板。最好用标题与上游 `days` 不一致的线路（例如标题 7 天、上游 6 天）。
- **审批中心**：`components/approvals.tsx` 的 `RouteContentApproval` 仍按旧结构读取修订。确认结构化行程在审批中心里的展示，并补上 `fact_decisions` 的显示（接口 `GET /v1/route-content-revisions/{id}` 已返回 `fact_decisions` 与 `listing`）。
- **迁移 0045**：视图 `product_listing` 通过 `pg_get_viewdef` 追加列。请在带真实数据的库副本上试跑，并核对已有授权仍有效。
- **读取点是否遗漏**：已把顾问、目录、检索、商户列表等改读 `effective_days`、`effective_gateway`。请再全库检索 `p.days`、`p.gateway`，确认没有面向客户的读取仍用上游原值。`documents.py` 和 `_source_hash` 有意保留原值，不要改。

## Codex 核查后的处理

- **顾问线路详情读原值**（`advisor.py` 的详情查询）：已改读有效天数、口岸。
- **关联规则纠正**：线路、团期、附件在上游已按线路 ID 确定关联。内容校对及天数、口岸核定只改内容与展示，不按日期跨度、文中适用日期或城市排除团期，不要求重新选择。此前“只放行被覆盖跨度”的设计与验收标准作废。
- **待整理，由接手方处理**：提交不能独立运行（缺已发布基线里的启动入口和共享前端模块）；迁移 `0045_product_facts` 需要接到已发布的 `0045–0047` 之后重新编号；`short_name` 缺失需要查因；审批中心展示核定前后值与依据。

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


## Codex 集成验收补充（2026-09-29）

集成分支为 `codex/sync-ecs-review`。以已部署的 `advisor-v4` / `5efcd6c` 为基线，先由 `5cd5bb6` 收录与 ECS 文件校验和一致的多供应商 goods-stock 接入，再移入本 Brief 对应功能，保留已发布顾问修复与商户工作模式提示。未直接覆盖原目录中的未提交文件。

- 迁移改为 `0048_product_facts`，接 `0047_supplier_name_scope`；保留已有 `0045_advisor_v3`、`0046_supplier_short_name`、`0047_supplier_name_scope`。旧分支的 short_name 失败由缺失这段迁移基线造成，完整基线上已通过供应商名称与 goods-stock 测试。
- 审批中心可展示 `route-kit/1` 全部行程，核定字段显示上游原值、发布后值、当前上游值和依据；产品编辑仅提交，管理员明确审批应用。
- 补齐旧商户助手目录、旧顾问方向卡和核实单的有效天数/口岸读取。保留来源快照和附件绑定原值。
- 内容校对与团期关联已解耦：原有按天数、适用日期、口岸筛选历史发布版本的规则移除，同线路团期读取当前已发布行程。
- 修复核定草稿刷新后丢失、发布后仍显示待核定，以及相同问题编号在重新检查后仍显示旧等待卡的问题。新增数据库与前端状态转换回归。
- 用户授权的 ECS 数据副本已在本机私有目录完成 `0047→0048`：57 张原有表、2,534,736 条记录数量不变，7 类关键业务表原列校验和及 127 条权限策略不变。
- 隔离副本真实商户页面已走通：FJLS 天数核定、保存刷新、产品编辑提交、管理员审批、发布后列表、客户预览与上游待更正面板。独立顾问搜索及详情为 7 天，上游原值仍 6 天，供应商名称正常，7 个团期可读取发布行程。
- 原 WXEY 混合 8/10 天团期“核定为 9 天后仍应拦截”的验收结论作废。正确要求是内容复核完成后正常发布，全部原有关联保持不变；不得要求调整团期或以内容天数重新匹配。
- 按纠正后的规则复测隔离副本：WXEY 在真实商户页面完成产品编辑提交、管理员审批及发布，9 天行程可被全部 7 个在售团期读取（包含 8 天及 10 天跨度）。40 条团期记录（含既有停用记录）、附件、原始解析、产品源值、报价和库存校验和均未变化。
- 本轮相关回归 1,030 项通过、零跳过，商户审核卡片 3 项通过，商户及顾问前端构建通过。内容校对不要求先选团期，也不因文字中的适用日期或口岸切换历史行程版本。

全部业务写入仅在隔离副本完成；原始附件对象未整库复制，附件下载/原图逐一核对不在本次验收范围。线上仍需供应商实际核对并发布；测试核定与测试账号不回灌 ECS。生产切换需保留 goods-stock 与 domestic 的现有连接表和部署覆盖配置，停止所有写入服务后迁移、刷新授权，再统一启动新版本，不能只更新网页。
