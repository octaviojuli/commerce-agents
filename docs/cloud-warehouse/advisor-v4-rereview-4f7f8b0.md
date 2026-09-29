# advisor-v4 复查：4f7f8b0

范围：`dbf5d22bbfb507cd88271047743d16487c92e851..4f7f8b04abff57d90d353b02f62f7acddc8f522b`，仅本轮两个文件的差异。

**结论：主要修复有效，上轮 P2 仍有一个较窄的来源判断边界，尚未完全关闭。未发现其他本轮问题。**

## [P2] 文案精确匹配仍会覆盖明确的供应商来源

位置：[selling.py:477](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/tour/advisor/selling.py:477)。

从“包含 ¥”收窄为 `BUDGET_LINE.fullmatch()` 后，普通另付说明已能保留，但兼容分支仍未检查来源。“每人约 ¥300”并非程序独占措辞，供应商原文也可能使用。

本次用虚构供应商文档经方案生成、分享、公开接口完整复现：

1. 文档注意事项的标题是“ACME 签证服务费（出发前另付）”，正文是“每人约 ¥300”。
2. 本单未核价；生成的私有方案 `price=None`，提醒的 `source="注意事项"`、`text="每人约 ¥300"`。
3. 公开接口的 `tell` 仍变为空字符串。它删掉的是供应商来源金额，而非本单报价。

这是上轮误删问题在相同措辞下的残留，不是新增加的一类缺陷。代码注释中“供应商不会使用这种文案”的假设没有数据契约保证。

建议做小范围修正：显式标记为“报价”的条目继续按报价有效期处理；历史兼容分支同时限定旧预算条目的来源和形态。旧版负向预算条目被标为“行程”，不应让该兼容判断覆盖明确标为“注意事项”等供应商来源的内容。保留已通过的旧方案过期价格清理用例。

## 已验证部分

| 检查 | 结果 |
| --- | --- |
| 提交内完整顾问测试 | **69 passed，0 skipped** |
| 上轮两个另付费用保留用例 | 已通过，包含在上述 69 项中 |
| 历史超预算文案清理 | 已通过，包含在上述 69 项中 |
| 新增来源冲突用例 | **1 failed**，对应上述唯一残留问题 |
| 本轮 Python 静态、格式及差异空白检查 | 通过 |

测试基于固定提交快照，使用虚构云仓文档和独立临时 PostgreSQL 数据库；数据库已清理。未修改业务源文件、合并或部署。

复查结束时，目标工作树另有 5 个未提交文件的变更；它们不属于指定提交范围，本次未审查或修改。

本轮没有前端或数据库结构变更，未重复上轮已执行的浏览器、前端构建和迁移检查。本报告仅评价本轮差异，不代表真实供应商、多回合真实模型或 ECS 验收通过。

材料：[提交内测试记录](/Users/k/projects/Tour_Agent/output/advisor-v4-rereview-4f7f8b0/existing/tests.log)、[新增用例](/Users/k/projects/Tour_Agent/output/advisor-v4-rereview-4f7f8b0/test_supplier_source_collision.py)、[失败记录](/Users/k/projects/Tour_Agent/output/advisor-v4-rereview-4f7f8b0/source-boundary/tests.log)。
