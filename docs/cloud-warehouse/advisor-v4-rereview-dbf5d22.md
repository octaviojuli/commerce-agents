# advisor-v4 复查报告：dbf5d22

本轮范围：`5e14a708ed9ffa3c978770a8b71c8d9871bfac89..dbf5d22bbfb507cd88271047743d16487c92e851`。

整体范围：`489816e5bb7b8db3f8ba9ad167e6a9610d53e6a6..dbf5d22bbfb507cd88271047743d16487c92e851`。

**结论：上轮 3 项问题已修复，本轮发现 1 项新增 P2 回归；未发现新增 P0/P1。建议补好这处后进入真实联调验收。** 本轮涉及 4 个文件，评估使用固定提交快照，没有修改业务代码、合并或部署。

## 需要补充的修复

### [P2] 过期价格过滤会误删供应商原文的另付费用和付款条件

位置：[selling.py:472](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/tour/advisor/selling.py:472)，对应 `priced()` 及其下方的提醒过滤。

新兼容逻辑把所有含 `¥` 的文字都视为报价衍生内容。只要方案尚未核价，或者报价已经过期，相关提醒就会整条删除。供应商原文的另付费用、付款时间等条件同样可能包含这个符号，不能依此判断它们来自已失效报价。

使用虚构供应商文档复现：在“注意事项”加入“ACME 签证服务费须另付 ¥300/人，出发前支付”，经实际方案生成、分享、公开接口读取后：

- 私有方案正确保留原文，来源为“注意事项”。
- **尚未报价**时，客户页 `tell` 已变为空字符串，`includes` 也没有该条件。
- **报价过期**时，同样整条隐藏，另付义务和付款时间一起丢失。

同一组测试在 `5e14a70` **2 项通过**，在 `dbf5d22` **2 项失败**，确认是本轮引入，不是旧问题延续。两次失败属于同一个缺陷。

建议：依据来源或事实引用区分“报价衍生金额”和“供应商原文条款”；存量方案的兼容处理只匹配确属旧预算比较的记录，不能把出现货币符号作为统一删除条件。保留目前已正确修复的过期预算提示清理。

证据：[边界用例](/Users/k/projects/Tour_Agent/output/advisor-v4-rereview-dbf5d22/test_public_price_boundaries.py)、[本轮失败记录](/Users/k/projects/Tour_Agent/output/advisor-v4-rereview-dbf5d22/price-boundaries/tests.log)、[上轮版本对照记录](/Users/k/projects/Tour_Agent/output/advisor-v4-rereview-dbf5d22/baseline-price-boundaries/tests.log)。

## 上轮问题的关闭情况

| 上轮问题 | 本轮复查结果 |
| --- | --- |
| 换线当轮回复继续使用旧价 | 选线已提前到事实与价格收集之前，并重读本单及上下文；原复现用例通过 |
| 超预算提醒保留过期价格 | 新方案来源已统一为“报价”；原复现用例通过，另补的旧方案错误来源兼容用例也通过；但过滤过宽引入上述 P2 |
| 失效正式报价没有重新核价入口 | 浏览器实点通过：有效确认单显示“重新核价”，点击后恢复“发正式报价”；确认单失效时显示“去确认单”，可实际跳转 |

前端验证运行的是固定提交的生产构建，连接本机虚构接口。它验证了页面分支、点击和请求，并不代表真实供应商报价或真实客户确认。见[浏览器复查记录](/Users/k/projects/Tour_Agent/output/advisor-v4-rereview-dbf5d22/web-observations.md)。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| 提交内完整顾问测试 | **66 passed，0 skipped**，包含此前复现用例 |
| 新增价格边界用例 | **1 passed，2 failed**；通过项验证旧方案超预算文字被撤下，失败项均对应上述 P2 |
| 相同费用条款用例在上轮提交对照 | **2 passed** |
| 无数据库环境的提交内顾问测试 | **40 passed，26 skipped**，无错误 |
| 新前端生产编译及 TypeScript | 通过；复用本机已有依赖，未重新进行干净安装 |
| 浏览器页面检查 | 重新核价、返回确认单两个场景通过，接口为虚构 fixture |
| Python 静态与格式检查、仓库一致性检查 | 通过 |

数据库测试使用独立临时数据库并完成清理；本轮创建的浏览器页和两个本机测试服务均已关闭。业务分支在复查结束时仍为 `dbf5d22`，工作树无修改。

## 整体交付判断

整体仍是独立顾问服务与前端，通过云仓顾问接口读取供应商数据；此前的报价归属、重复成交、迟到需求填充、确认单作废等回归用例在当前提交继续通过。本轮没有数据库结构或部署改动。

**本次未执行真实商户端、国旅环球、多场景多回合真实模型及 ECS 验收。** 整体范围内先前记录的身份稳定标识、上游报价复核与混合占床、目录分页、旧顾问数据切换及新服务部署接线仍需落实。这些不计作本轮新增缺陷，但当前本地测试不能替代用户要求的最终验收。

## 其他材料

- [提交内测试记录](/Users/k/projects/Tour_Agent/output/advisor-v4-rereview-dbf5d22/existing/tests.log)
- [普通测试环境记录](/Users/k/projects/Tour_Agent/output/advisor-v4-rereview-dbf5d22/unit-ci-without-db.log)
- [前端构建记录](/Users/k/projects/Tour_Agent/output/advisor-v4-rereview-dbf5d22/web-build.log)
- [浏览器触发的重新核价请求](/Users/k/projects/Tour_Agent/output/advisor-v4-rereview-dbf5d22/web-fixture-request.json)
- [上轮报告](/Users/k/projects/Tour_Agent/docs/cloud-warehouse/advisor-v4-rereview-5e14a70.md)
