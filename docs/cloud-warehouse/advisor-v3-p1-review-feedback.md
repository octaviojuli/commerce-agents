# advisor-v3-p0 最新修复反馈

**结论：继续修复，暂不建议合并或发布。** 本轮确认 4 项 P1、1 项 P2。此前 3 类问题仍可复现；新增的比较文案截句逻辑引入 1 个事实完整性问题；重新找线的修复还漏了问句场景。

## 审查范围与验证

- 分支：`advisor-v3-p0`。
- 固定提交：`489816e5bb7b8db3f8ba9ad167e6a9610d53e6a6`。
- 增量基线：`6cb49bb72b91bbf37697334a7e8a0ba8f30895c7`；本次增量 18 个文件，431 行增加、186 行删除。
- 审查与测试使用固定提交导出的副本，未修改 Claude 分支的业务代码；结束时分支仍为上述提交，工作目录干净。
- 独立运行 96 项定向测试：**89 通过、7 失败、0 跳过**。其中仓内原有用例 87/87 通过；上次审查的 6 项边界用例为 1 通过、5 失败；本次新增的 3 项用例为 1 通过、2 失败。
- Ruff、格式检查、`scripts/check.py`、前端 TypeScript 检查通过。
- 数据库测试使用本轮创建的临时数据库和受限角色，执行后已清理；不使用业务表。会话调度回归使用确定性的模型返回，未把它称作真实模型验收。

## R1 · P1 · 比较卡截句丢掉适用条件（本次引入）

位置：[copilot_plans.py:44](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v3-p0/cloud-warehouse/cloud_warehouse/copilot_plans.py:44)，实际输出在 `compare()` 的比较维度中。

**复现：** 已复核事实为“餐食：仅限6岁以下儿童。早餐免费。其他年龄按成人标准收费。”，客人关心早餐。调用真实 `compare()` 后，餐食维度变成“餐食：早餐免费。”，同时仍返回 `known=true` 和原事实引用。

**原因与影响：** `clip()` 只保留命中主题词的句子，删除了不含“餐”字的年龄条件和收费例外。引用 ID 仍指向完整事实，会让删掉限制后的说法看起来有依据。该比较结果还会用于方案展示和导出。

**对照证据：** 同一个数据库回归在上一提交 `6cb49bb` 通过，在本次提交失败，确认由本轮截句逻辑引入。

**修复要求：** 摘录应保留同一事实单元的条件、例外、收费与适用人群；不能只按关键词筛句。不能安全拆分时保留完整事实。验收必须覆盖跨句年龄条件、日期条件和否定例外。

## R2 · P1 · 草稿仍能反转费用和限制含义（上次问题未修）

位置：[copilot_reply.py:138](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v3-p0/cloud-warehouse/cloud_warehouse/copilot_reply.py:138)，以及该文件第 13 行的 `FACTUAL`。

**仍可复现的三个输入：**

| 已有事实 | 被放行的客户草稿 |
| --- | --- |
| 取消须支付1000元手续费 | 取消无须支付1000元手续费 |
| 午餐和晚餐自理 | 午餐和晚餐不需要自理 |
| 儿童餐需要提前两天登记 | 儿童餐需要提前登记 |

第一例还被自动附上原事实引用。“无须”包含“须”，使当前限制词检查误判；后两例没有触发完整的事实校验。降低删句率不能以允许含义反转为代价。

**修复要求：** 同一事实内校验否定方向、费用承担、数量单位、期限和适用条件；中文数字也应纳入。无法证明改写等价时退回保留条件的原文或待核实提示。上述三个用例必须阻止错误草稿输出。

## R3 · P1 · 需求变化后旧报价仍进入模型依据（上次问题未修）

位置：[warehouse_copilot.py:322](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v3-p0/examples/tour/api/warehouse_copilot.py:322)。

**复现：** 当前需求为 3 位成人、报价字段版本 3，保存的旧报价仍属于版本 2，展示期限尚未到期。`offer_facts()` 仍生成旧金额的 `price` 事实供草稿使用。

**原因与影响：** 这里只检查 `complete`、市场总价和时间，没有核对当前需求与报价的依赖版本。页面提示报价失效，不能阻止另一条模型依据路径继续引用旧金额。

**修复要求：** 使用统一报价有效性判断，不在草稿入口另设一套简化规则；核对人数、房型、选定团期和来源变化。无效时只提示重新核价。已有有效顾问销售报价时，应使用对应销售价，不能用市场参考价替代。

## R4 · P1 · 只填一个孩子的占床仍被当作全体已确认（上次问题未修）

位置：[trip_brief.py:77](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v3-p0/cloud-warehouse/cloud_warehouse/trip_brief.py:77)、[quotes.py:55](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v3-p0/cloud-warehouse/cloud_warehouse/quotes.py:55)。

**复现：** 两名儿童分别为 5 岁、8 岁，只记录 `[{age:8, bed:true}]`。`Rooms` 从这一条记录推导出 `child_bed=true`；计价发现逐人记录数量不足时，又退回这个统一值。需求完整性检查没有要求补充另一个孩子的占床。

**修复要求：** 有逐人记录时，必须完整匹配儿童人数和对应旅客；不允许把部分记录推广成全体统一值。缺一人、重复对应或与年龄名单不一致时保留待确认，不得形成完整报价。原有“一占床、一不占床”的正常用例仍应通过。

## R5 · P2 · 问句形式的重新找线仍被转成查资料（本次修复未覆盖）

位置：[warehouse_copilot.py:613](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v3-p0/examples/tour/api/warehouse_copilot.py:613)，以及第 625–632 行。

**复现：** 已选线路和团期时，模型明确返回允许的 `next_action=search_routes`。输入“按原需求重新找线”可以搜索；输入“可以按原需求重新找线吗？”却只返回 `copilot_facts` 和 `copilot_reply`，没有线路结果。

**原因：** 第 613 行识别了重新搜索，但随后因问句且已有当前线路，又被通用的线路问答分支覆盖成 `answer_question`。

**修复要求：** 明确的重新搜索请求应优先于“询问当前线路事实”的分支。用真实会话调度路径同时覆盖命令句、礼貌问句和“不需要重新找线”的否定句，不能只测试允许动作列表里有 `search_routes`。

## 可以保留的改善

- 已选线路/团期后允许重新搜索，普通命令句路径已通过本轮数据库回归。
- 新增方向标签、空内容过滤、陈述句式追问已知需求的检查，相关仓内用例通过。
- 界面代码已区分顾问按钮操作，更新阶段提示，整理需求标签，并过滤已成交单的报价到期待办；静态检查通过。本轮没有重复手机真机验收。

## 合并及真实数据验收要求

先修复上述 5 项，并把复现用例加入正式回归。随后将匹配版本的前后端一起验证，继续完成国旅环球多场景、多回合验收。

[上一轮国旅环球接入报告](/Users/k/projects/Tour_Agent/output/advisor-supplier-test/report.md)中的前后端版本兼容、行程审核与发布、费用确认及天数口径问题，不能由本次单元测试通过替代。本轮没有重新部署或重跑 ECS 全流程，不把旧联调结果当作当前线上状态确认。

本轮只完成审查与报告，没有合并、推送或部署。

## 可复用证据

- [本次新增业务回归用例](/Users/k/projects/Tour_Agent/output/advisor-v3-p1-review/test_advisor_review_489816e.py)：放入待测副本 `cloud-warehouse/tests/` 后运行，依赖该目录的隔离数据库夹具。
- [上次问题的复测用例](/Users/k/projects/Tour_Agent/output/advisor-v3-p1-review/test_previous_review_regressions.py)。
- [完整测试输出](/Users/k/projects/Tour_Agent/output/advisor-v3-p1-review/tests.log)与 [JUnit 结果](/Users/k/projects/Tour_Agent/output/advisor-v3-p1-review/results.xml)。
- [上一提交的比较卡对照结果](/Users/k/projects/Tour_Agent/output/advisor-v3-p1-review/baseline/tests.log)。
- [本轮隔离测试运行脚本](/Users/k/projects/Tour_Agent/output/advisor-v3-p1-review/run_review.py)：默认使用本轮固定提交副本，可通过 `ADVISOR_REVIEW_SOURCE`、`ADVISOR_REVIEW_OUTPUT` 指定副本与报告目录。
