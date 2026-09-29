# advisor-v4 复查：72f1af2

本轮范围：`4f7f8b04abff57d90d353b02f62f7acddc8f522b..72f1af2014c7ce36c1d4bdc47f63c142f6143938`，单个提交，30 个文件。审查及测试基于固定提交快照，未修改业务代码、合并或部署。

**结论：暂不建议合并。新增供应商功能有 1 项 P1、2 项 P2 需要修复。** 本提交新增供应商简称、筛选、同类线路提示及顾问私人备注，范围已超出上一轮费用说明修复。

审查期间分支继续推进到 `61cd2c041284ede54026646c552194549e3348bc`。该后续提交仅修改费用说明判断及对应测试；已另行定向验证，上轮费用说明误删问题可以关闭。以下三项新增问题对应 `72f1af2`，相关代码在 `61cd2c0` 中未改动。

1. **[P1] 供应商名称权限遗漏独立顾问的平台销售模式。**

   位置：[0046_supplier_short_name.py:19](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/cloud-warehouse/migrations/versions/0046_supplier_short_name.py:19)。

   `warehouse_supplier_name` 只允许供应商自身或存在有效 `distribution_grant` 的采购组织读取名称。但项目采用的平台销售模式允许独立顾问读取供应商目录，不要求分销授权。结果是顾问可以搜索线路、查看详情和团期，三个入口的 `supplier_name` 却全部为空；前端会把不同供应商显示成“供应商未标注”。

   已在真实 PostgreSQL 权限路径复现：开启平台销售，创建无分销授权的独立顾问，设置供应商简称并发布虚构目录，目录、详情、团期都能读取，但三个名称均为 `""`。当前提交的名称测试只使用有分销授权的采购身份，未覆盖此模式。

   建议让名称权限复用当前目录授权逻辑，兼容平台销售和分销模式，并继续检查组织、连接及身份的实时有效性；不要为显示名称给独立顾问补建分销关系。

2. **[P2] 无匹配结果时，替代线路绕过了供应商筛选。**

   位置：[routes.py:133](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/tour/advisor/routes.py:133)。

   供应商条件只过滤 `fits`。如果过滤后为空且需求有出发城市，后续 `alternatives` 会重新从未受供应商约束的 `everything` 中取线路。返回值仍标记 `supplier_filter=["S-B"]`，结果却含 S-A 的线路。

   已通过顾问 HTTP 流程复现：先只看 S-B，再把出发城市改为上海，保持 S-B 筛选重新找线；结果同时返回 S-A 上海线路与 S-B 重庆线路。虽然标记为替代线路，程序放宽的已不只是出发城市，也悄悄取消了供应商限制。

   建议供应商条件同时约束正常候选、替代候选及放宽条件的数量；没有结果时明确说明，不自动取消已选供应商。

3. **[P2] 首次找线当轮，供应商名称过滤使用搜索前的上下文。**

   位置：[turns.py:792](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/tour/advisor/turns.py:792)。

   新增 `forbidden=supplier_names(context, deal)` 依赖本轮搜索前读取的 `context["visible"]`。`self.search()` 得到新供应商后，并未在写客户话术前把这些名称加入过滤集合。首次找线时，模型生成的供应商名称仍会进入可复制给客户的草稿。

   使用提交内同样的虚构模型输出做前后对照：已有搜索记录时，“ACME 城游”被删除；首次找线时，同一名称原样保留，且 `removed=[]`。这是过滤边界测试，不是声称真实模型每次都会生成该文案。

   建议在当轮生成客户话术前纳入本次搜索返回的供应商及同类线路供应商，统一检查最终对客文本；同时覆盖首次找线和重新找线场景。

已有费用说明问题：`72f1af2` 的 `public_route` 仍按文案匹配误删供应商“每人约 ¥300”，独立端到端用例仍失败。后续 `61cd2c0` 已把历史兼容限定为来源“行程”且符合旧版超预算格式，供应商费用保留、既有费用条件和历史过期预算清理共 **4 项定向测试通过**。本报告不再把这一项列为后续分支的待办，也不将这 4 项结果解释为对 `61cd2c0` 的完整复查。

验证结果：

| 检查 | 结果 |
| --- | --- |
| `72f1af2` 提交内顾问测试、云仓顾问、平台销售及目录权限测试 | **118 passed，0 skipped** |
| 独立补充用例 | **3 passed，4 failed**；失败分别对应上述三个新增问题及旧费用说明问题 |
| 补充用例通过部分 | 私人备注按顾问隔离、分销授权撤销后名称不可读、已有搜索上下文的供应商名过滤 |
| `61cd2c0` 旧费用说明问题定向检查 | **4 passed，23 deselected，0 skipped** |
| 前端生产构建与 TypeScript | 通过；复用本机现有依赖，不代表全新依赖安装或浏览器验收 |
| 本轮 16 个 Python 文件的 Ruff 检查及格式检查 | 通过 |
| 差异空白检查 | 通过 |

测试使用隔离临时 PostgreSQL、虚构云仓数据和脚本模型；临时数据库及角色已清理。云仓测试实际执行数据库迁移、受限角色与权限路径，但未连接真实供应商或真实模型，未做多场景多回合商户联调及 ECS 验收。

证据：[提交内测试日志](/Users/k/projects/Tour_Agent/output/advisor-v4-rereview-72f1af2/existing/tests.log)、[补充用例结果](/Users/k/projects/Tour_Agent/output/advisor-v4-rereview-72f1af2/probes/tests.log)、[顾问补充用例](/Users/k/projects/Tour_Agent/output/advisor-v4-rereview-72f1af2/probe-advisor.py)、[云仓补充用例](/Users/k/projects/Tour_Agent/output/advisor-v4-rereview-72f1af2/probe-warehouse.py)、[后续费用修复验证](/Users/k/projects/Tour_Agent/output/advisor-v4-rereview-72f1af2/follow-up-61cd2c0/tests.log)、[前端构建日志](/Users/k/projects/Tour_Agent/output/advisor-v4-rereview-72f1af2/web-build.log)、[验证摘要](/Users/k/projects/Tour_Agent/output/advisor-v4-rereview-72f1af2/review-summary.json)。
