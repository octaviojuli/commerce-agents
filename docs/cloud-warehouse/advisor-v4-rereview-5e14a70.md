# advisor-v4 复查报告：5e14a70

本轮修复范围：`f88bf5d8fb054b8da3b02c206f146ae8206cbb0d..5e14a708ed9ffa3c978770a8b71c8d9871bfac89`。

整体评估范围：`489816e5bb7b8db3f8ba9ad167e6a9610d53e6a6..5e14a708ed9ffa3c978770a8b71c8d9871bfac89`。

**结论：本轮修复有效，但尚未完全通过。仍有 1 项 P1、2 项 P2，建议修复后再合并、部署。** 上轮的原始复现用例全部通过；扩展到换线当轮回复、超预算文案和报价过期后的页面操作，仍有遗漏。

审查和测试均基于固定提交导出的临时快照。顾问业务源文件未修改，未合并或部署。下列代码位置对应 `5e14a70`，不代表后续分支版本。

## 剩余问题

### 1. [P1] 对话换线后，当轮回复仍能把旧线路价格报给客人

位置：[turns.py:385](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/tour/advisor/turns.py:385)，相关价格事实在 [turns.py:323](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/tour/advisor/turns.py:323) 提前生成。

本轮 `settle_route` 已正确清空旧团期、作废旧确认单和报价，但它执行时，本轮使用的 `facts`、`context` 已按旧线路准备好。后续生成对客回复、校验事实时继续使用这些旧数据。

复现：先为线路 A 核出全家 29,600 元，再在对话中选择“城市巡游”线路 B。数据库中旧报价确已变为 `void`，当前团期也已清空；脚本化模型按系统要求复述收到的价格后，最终回复仍为：

> 城市巡游，全家合计29600元。

这句话通过了事实校验，金额被记录为有 `price:total` 依据。测试没有让模型编造价格，而是复述服务端仍提供的旧事实。正式报价发送接口已能拦住旧价，但可复制到微信的会话草稿仍会串价。

建议：先完成本轮选线等状态变更，再生成问题回答和对客事实；换线后重建上下文，清除旧线路、旧团期和旧价格事实。校验应依据变更后的状态。

证据：`test_route_change_removes_old_price_from_same_turn_reply`，断言失败；见[补充复现结果](/Users/k/projects/Tour_Agent/output/advisor-v4-rereview-5e14a70/followup/tests.log)。这是上轮换线问题在对客回复路径上的残留。

### 2. [P2] 超预算提示的来源标记错误，过期金额仍出现在客户页

位置：[selling.py:470](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/tour/advisor/selling.py:470)，根因在 [selling.py:248](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/tour/advisor/selling.py:248)。

本轮会过滤来源为“报价”的推荐理由和提醒，但方案生成时只有预算内的正向理由使用这个来源；超预算的负向提醒使用 `_section(route, None)`，被标为“行程”，因此不会被过滤。

复现：人均预算 10,000 元、报价 14,800 元，生成双线路方案后将读取时间推进至报价过期。客户页总价已撤下，并显示价格过期，但 `tell` 仍返回：

> 每人约 ¥14,800，超 ¥4,800

建议：预算比较的正向、负向文案统一保存报价来源及引用；价格失效时同步撤下所有衍生金额。还需兼容已经保存的方案，单改新方案的来源标记不能修复存量分享。

证据：`test_expired_price_is_removed_from_over_budget_warning`，断言失败；见[补充复现结果](/Users/k/projects/Tour_Agent/output/advisor-v4-rereview-5e14a70/followup/tests.log)。上轮“预算内推荐理由”用例已通过，超预算分支仍未闭合。

### 3. [P2] 正式报价过期后，页面没有重新核价入口

位置：[quote/page.tsx:23](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/tour/advisor-web/app/deals/[id]/quote/page.tsx:23)、[quote/page.tsx:54](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/tour/advisor-web/app/deals/[id]/quote/page.tsx:54)。属于整体范围内尚未修复的页面流程问题，本轮没有改动此页。

报价过期时，接口返回 `kind=formal`、`status=active`、`valid=false`。页面按前两个条件选中旧报价，却只在 `!q` 时显示“核价并生成正式报价”按钮。因此重新进入该页后，顾问只能看到失效原因，无法正常生成新正式报价；同线路换团期或套餐后保留的旧正式报价也会进入这个分支。

本次通过 HTTP 验证了过期记录形态、旧报价发送被拒绝，以及正式报价接口仍可生成新记录；按钮缺失由页面条件和该前端唯一的正式报价调用入口确认。本项未做浏览器点击复现。

建议：保留旧报价用于查看，同时在报价失效时提供“重新核价”入口；需要重新确认的情形先引导回确认单。不要依赖顾问修改需求、换线或直接调用接口来恢复流程。

证据：[过期报价接口记录](/Users/k/projects/Tour_Agent/output/advisor-v4-rereview-5e14a70/expired-quote-http.json)、[接口与迁移测试](/Users/k/projects/Tour_Agent/output/advisor-v4-rereview-5e14a70/upgrade-and-refresh/tests.log)。

## 上轮问题的复查结果

| 上轮问题 | 本轮结果 |
| --- | --- |
| 对话换线未作废旧团期、确认、报价 | 数据库状态和正式报价接口已修复；当轮会话草稿仍有问题，见第 1 项 |
| 目录默认套餐被当成顾问选择；刷新丢失已核套餐 | 原两个复现用例均通过，不再自动采用目录默认套餐，能恢复已核套餐 |
| 已作废确认单可被迟到回复重新确认 | 原接口用例通过；新增对话路径用例也通过，返回失效状态 |
| 普通 CI 缺数据库变量报错；数据库 CI 未包含顾问测试 | 无数据库运行正常跳过；数据库入口已加入顾问目录，本地该目录 62 项执行、零跳过 |
| 同收款键换金额仍返回成功 | 原用例通过，修改金额的重试返回 409；相同请求保持幂等 |
| 客户页撤下结构化价格却保留价格理由 | 预算内理由已修复；超预算提醒仍残留，见第 2 项 |

本轮还增加了跨线路团期拒绝核价、重复成交记录阻止升级的测试，均通过。重复点击当前线路不会清空其团期，本次补充用例通过。

## 验证结果与边界

| 验证 | 结果 |
| --- | --- |
| 提交内完整顾问测试 | **62 passed，0 skipped**，包含此前两轮复现用例 |
| 新增边界用例 | **2 passed，2 failed**；失败对应上述第 1、2 项 |
| 历史数据库升级及过期报价接口验证 | **2 passed** |
| 无数据库环境的顾问测试 | **40 passed，22 skipped**，无错误；未据此宣称整仓 CI 通过 |
| Python 静态及格式检查 | 通过，27 个文件；不包含审查临时用例 |
| 新前端生产编译及 TypeScript | 通过；复用本机依赖，未重新做干净安装 |

升级验证覆盖原始 v1 数据经 v2 升至 v3、重复执行迁移、保留客户单/会话/金额记录。旧确认单保留历史内容，但因缺少线路与套餐绑定而变为 `void`；上线前应明确这个重新确认行为。

整体架构仍符合一期的独立顾问数据管理与轻量闭环方向，利润仍按销售价减结算价计算，没有启用供应商占位或下单。

**这次测试使用虚构数据、模拟云仓和脚本化模型，未完成真实商户端、国旅环球、多回合真实模型或 ECS 验收。** 整体范围里的身份稳定标识、上游报价有效性复核/混合占床、目录分页、旧顾问数据切换仍需落实。部署文件也仍使用原 `storefront-web` 和 8005 API，尚未接入新 `advisor-web` 与 8006 服务；不能把合并代码等同于完成部署。

建议本次先修上述 3 项，然后按既定要求进行真实商户端多场景、多回合验收，再处理 ECS 切换。

## 复查材料

- [提交内测试记录](/Users/k/projects/Tour_Agent/output/advisor-v4-rereview-5e14a70/existing/tests.log)
- [新增边界用例](/Users/k/projects/Tour_Agent/output/advisor-v4-rereview-5e14a70/test_final_contracts.py)
- [数据库升级及接口验证用例](/Users/k/projects/Tour_Agent/output/advisor-v4-rereview-5e14a70/test_upgrade_and_refresh.py)
- [无数据库运行记录](/Users/k/projects/Tour_Agent/output/advisor-v4-rereview-5e14a70/unit-ci-without-db.log)
- [前端编译记录](/Users/k/projects/Tour_Agent/output/advisor-v4-rereview-5e14a70/web-build.log)
- [上轮报告](/Users/k/projects/Tour_Agent/docs/cloud-warehouse/advisor-v4-rereview-f88bf5d.md)
