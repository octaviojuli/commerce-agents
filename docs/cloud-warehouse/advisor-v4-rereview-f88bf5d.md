# advisor-v4 修复复查：f88bf5d

目标提交：`f88bf5d8fb054b8da3b02c206f146ae8206cbb0d`。整体范围为 `489816e..f88bf5d`，重点复查 `234000b..f88bf5d` 的 17 个文件、723 行新增和 84 行删除。

**结论：上轮原始复现场景全部通过，修复有效；完整操作流程仍有 4 项 P1、2 项 P2，建议修完后再合并。** 本轮没有修改业务源码、合并或部署。

**已经验证的进展**

| 项目 | 本轮结果 |
| --- | --- |
| 分支顾问测试，包括上轮 10 个复现场景 | 54 passed，零跳过；模拟云仓、脚本化模型、一次性 PostgreSQL |
| 补充完整流程测试 | 6 failed，对应下述 5 类业务问题；多套餐有两个复现用例 |
| 数据库 v1 → v2 升级 | 1 passed；虚构旧会话、需求单、成交记录保留，重复迁移可执行 |
| 锁文件与干净安装预检 | 通过 `npm ci --offline --ignore-scripts --dry-run` |
| 新前端生产编译与 TypeScript | 通过；在固定提交快照中复用本机依赖，以 webpack 构建 |
| 新顾问服务 Ruff / 格式 | 通过 |
| 普通 CI 的无测试数据库环境 | 新增 `test_review.py` 得到 1 failed、10 errors，原因均为缺少环境变量 |

直接按钮换线、远期改期、明确的云仓 401 后撤销本地会话、同顾问客户串单、重复成交、迟到模型覆盖手工修改、方案版本错配、销售总价明细不一致，这些上轮测试都已通过。多套餐与过期价格的原始接口用例也通过，但继续操作后仍有遗漏，详见下面。

## 1. [P1] 对话换线没有接入按钮换线的失效处理

范围：上轮换线问题的未覆盖入口，存在于整体范围；本次只修了按钮路径。

位置：[turns.py:381](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/tour/advisor/turns.py:381)、[closing.py:185](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/tour/advisor/closing.py:185)。

按钮调用 `choose_route`，会作废旧报价、确认单并清空团期；`Turns.run` 根据对话选择线路时仍直接更新 `deal.route`。两条路径没有共享同一套业务处理。

复现：线路 A 已选团期且已确认，候选列表存在 B；输入“就选城市巡游那条”，结构化选择正确指向 B。保存后线路变成 B，团期仍为 A 的团期，确认单仍为 `confirmed`。随后正式报价接口返回 **201**，发送接口生成 **B 的线路名 + A 的团期与 29,600 元报价**。这里重新调用了云仓核价，但使用的是旧团期。

修复要求：模型选择与按钮选择必须走同一处理入口，并在事务内校验当前状态。正式报价还应校验线路与团期的真实关联，避免顾问层直接用当前线路覆盖报价来源标识。

复现：`test_conversation_route_change_uses_same_invalidation_as_button`。

## 2. [P1] 多套餐仍会被默认套餐绕过，所选套餐也未完整保留

范围：本次套餐选择修复不完整。

位置：[团期页面:34](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/tour/advisor-web/app/deals/[id]/dates/page.tsx:34)、[团期页面:51](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/tour/advisor-web/app/deals/[id]/dates/page.tsx:51)、[closing.py:219](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/tour/advisor/closing.py:219)。

后台新增“未传套餐且有多个套餐时返回 409”是有效的。但前端 `offerOf` 在没有人为选择时直接使用 `d.offer_id`。云仓团期列表中的这个字段来自 `code='base'` 的基础套餐；它不代表只有一个套餐，也不代表顾问已经选择。

按页面实际构造请求的方式复现：基础与升级两个套餐同时在售，顾问尚未选择，页面直接携带基础套餐 ID 核价，套餐列表接口根本没有被调用，选择弹窗不会出现。另外，明确核过升级套餐 `o2` 后重读团期，返回的仍是基础套餐 `o1`；`picked` 只在当前页面内存中，不能在刷新后恢复。

修复要求：将目录默认值与顾问明确选择分开；多套餐先完成选择，再核价。已选套餐应随本单/候选团期持久保存，并让团期返回、展示价格、确认单与正式报价使用同一选择。增加“有 base 且同时有升级套餐”和“刷新后继续确认”的场景。

云仓契约证据：[advisor.py:448](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/cloud-warehouse/cloud_warehouse/advisor.py:448)。本轮使用与此响应结构一致的模拟数据，没有宣称真实供应商页面验收通过。

复现：`test_catalog_base_offer_does_not_silently_choose_for_advisor`、`test_selected_nonbase_offer_survives_dates_reload`。

## 3. [P1] 作废确认单能被迟到回复重新确认

范围：本次新增换套餐作废逻辑，缺少后续入口的保护。

位置：[closing.py:325](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/tour/advisor/closing.py:325)、[closing.py:459](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/tour/advisor/closing.py:459)。

换套餐会把原确认单设为 `void`，但 `record_confirmation` 不拒绝作废记录；`confirmation_view.current` 只比较需求版本和团期，没有套餐绑定。对话确认入口同样直接写确认状态。

复现：同一团期从 `o1` 改选 `o2`，旧确认单已正确作废；对旧确认单 ID 再提交原套餐的确认回复，接口仍返回 **200**，把旧单恢复为 `confirmed`；随后为新套餐生成正式报价返回 **201**。浏览器旧页面或迟到请求即可触发。

修复要求：作废确认单不得重新激活；确认单应绑定线路、团期、套餐及相关需求快照。按钮确认、模型确认、正式报价都应调用同一有效性检查，迟到回复返回明确冲突并要求重新确认。

复现：`test_void_confirmation_cannot_be_revived_after_offer_change`。

## 4. [P1] 新增回归测试会让普通 CI 报错

范围：本次新增问题。

位置：[test_review.py:28](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/tour/advisor/tests/test_review.py:28)、[test_review.py:297](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/tour/advisor/tests/test_review.py:297)。

新增测试直接读取 `os.environ["WAREHOUSE_TEST_ADMIN_URL"]`。仓库普通 Python CI 运行 `pytest -q`，没有提供这个变量；独立 PostgreSQL CI 则只运行云仓和两个旧 Tour 测试文件，没有包含新顾问测试目录。

本地按普通 CI 的无数据库配置运行该文件，结果为 **1 failed、10 errors**，均为 `KeyError: WAREHOUSE_TEST_ADMIN_URL`。这是本地复现结果，没有查询或声称远端 CI 已运行失败。

修复要求：普通无数据库测试应按仓库惯例有条件跳过数据库用例；同时把 `examples/tour/advisor/tests` 纳入拥有一次性数据库的 CI，并要求零跳过，不能仅靠跳过来获得绿灯。

证据：[无数据库 CI 复现记录](/Users/k/projects/Tour_Agent/output/advisor-v4-rereview-f88bf5d/unit-ci-without-db.log)。

## 5. [P2] 收款重试键相同、金额变化时静默返回成功

范围：本次新增幂等收款逻辑缺少请求内容校验。

位置：[closing.py:671](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/tour/advisor/closing.py:671)。

同一个重试键先登记 5,000 元，再提交 8,000 元，接口仍返回 **200**，账本保持 5,000 元，没有提示金额不一致。实际场景是第一次已落库但响应丢失，顾问在仍打开的弹窗内修改金额再保存；前端继续使用同一键，随后清空输入并关闭弹窗。

修复要求：同一键只允许相同操作内容的重试；绑定金额、币种、备注与本单信息，内容不同返回 409 并展示原登记，避免顾问误以为新金额已经保存。

复现：`test_receipt_key_reuse_with_changed_amount_is_rejected`。

## 6. [P2] 过期价从金额区撤下后，仍出现在推荐理由中

范围：本次分享页过期处理遗漏文本投影。

位置：[selling.py:463](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/tour/advisor/selling.py:463)。

`public_route` 已清空过期后的 `per_person/total` 并提示重新核价，但 `why` 仍拼接保存时的全部推荐理由。带预算比较的双线路方案在报价过期后，仍向客户返回 **“每人约 ¥14,800”** 作为适合理由，与下方“价格已过期”并存。原用例生成单线路方案，没有覆盖这个字段。

修复要求：过期处理覆盖整个客户投影，按理由来源剔除或明确标记基于失效报价的预算、人均价与差价表述。路线事实可以继续展示。

复现：`test_expired_price_is_also_removed_from_customer_reasons`。

**后续验收边界**

建议先统一换线、确认和套餐选择的业务入口，再完善收款重试、分享价格文本与 CI。补充用例通过后，继续原定的真实商户/国旅环球多场景、多回合验收。

本轮没有进行真实模型、真实供应商、多设备浏览器或 ECS 验收。旧记录升级测试验证了没有重复成交记录的 v1 数据；若旧库已经存在重复成交，应先核对并明确处理，再创建唯一索引，不能自动删除财务记录。此前列出的身份稳定性、历史数据切换、报价实时复核、分页完整性与 ECS 新服务部署接线仍未由本次 diff 完成，不在本轮重复列为新缺陷。

**复查材料**

- [54 项分支测试](/Users/k/projects/Tour_Agent/output/advisor-v4-rereview-f88bf5d/existing/tests.log)
- [6 项补充用例](/Users/k/projects/Tour_Agent/output/advisor-v4-rereview-f88bf5d/test_followup_contracts.py)
- [补充用例运行结果](/Users/k/projects/Tour_Agent/output/advisor-v4-rereview-f88bf5d/followup/tests.log)
- [旧数据升级验证](/Users/k/projects/Tour_Agent/output/advisor-v4-rereview-f88bf5d/migration/tests.log)
- [依赖安装预检](/Users/k/projects/Tour_Agent/output/advisor-v4-rereview-f88bf5d/npm-ci.log)
- [前端构建记录](/Users/k/projects/Tour_Agent/output/advisor-v4-rereview-f88bf5d/web-build.log)
