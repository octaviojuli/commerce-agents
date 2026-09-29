# advisor-v4 开发评估与修复反馈

评估对象：`489816e5bb7b8db3f8ba9ad167e6a9610d53e6a6..234000b0522ee2ec93290535d60ebd55c14bacf9`，即本次检查时的 `advisor-v4`。共 62 个文件，新增 12,379 行。以下位置均对应这个提交。

**结论：可以作为新版顾问端的开发基础，当前不建议合并 main 或部署 ECS。** 核心问题集中在报价与当前需求的绑定、异步结果的版本校验、成交记录和登录撤销。它们已通过可复现用例确认。

这是新增独立顾问服务 `examples/tour/advisor` 和独立前端 `advisor-web`，原顾问服务仍存在。合并代码本身不会完成旧数据迁移、入口切换或部署切换。

**验证结果**

| 检查 | 结果及边界 |
| --- | --- |
| 分支原有顾问测试 | 43 passed；独立 PostgreSQL 测试库，模拟云仓和脚本化模型 |
| 针对风险补充的测试 | 10 failed；断言代表期望行为，失败复现下述 9 类业务问题，其中换线、改时间是同一类的两个用例 |
| 新顾问 Python 静态检查 | Ruff 检查与格式检查通过，排除审查补充文件 |
| 仓库一致性检查 | `scripts/check.py` 通过；不代表新服务的实际部署通过 |
| 新前端生产编译、TypeScript | 通过；在提交快照中复用本机现有依赖，以 webpack 构建 |
| 干净依赖安装预检 | 失败；`npm ci --offline --ignore-scripts --dry-run` 报锁文件缺少新工作区 |
| 真实供应商 / 真实模型多回合 / ECS | 本次未运行，不构成验收通过 |

业务源文件未修改，也未合并或部署。测试使用提交导出的隔离快照和一次性数据库；记录见文末。

**需要修复的问题（P1 为合并前阻断项，P2 为上线前应完成项）**

### 1. [P1] 换线、改变出发时间后，旧报价仍能按当前报价发送

位置：[pricing.py:192](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/tour/advisor/pricing.py:192)、[closing.py:173](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/tour/advisor/closing.py:173)。

复现：为线路 A 生成正式报价 29,600 元，然后选择线路 B。换线操作清空团期，但报价有效性检查在“没有已选团期”时跳过匹配，也不检查线路，结果仍返回 `valid=true`。发送接口返回 **B 的线路名 + A 的出发日期与价格**。把出发窗口从 12 月改到次年 4 月，同样保留有效旧价。

应区分“历史/候选线路核价”与“本单可发送报价”：后者必须绑定本单、选定线路、团期、套餐、影响价格的需求快照以及确认状态。换线或改变相关条件后应同步作废旧确认和当前报价。不能因为团期被清空就允许旧报价重新通过；筛选无效记录时也不能只处理第一条。

复现用例：`test_changing_route_invalidates_old_quote_and_send`、`test_changing_window_invalidates_old_quote`。

### 2. [P1] 云仓已撤销登录，顾问端仍能读取私有客户数据

位置：[api.py:163](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/tour/advisor/api.py:163)、[api.py:195](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/tour/advisor/api.py:195)。

本地鉴权只看自己的 cookie 和有效期，云仓返回 401 也不作废本地会话。复现中，云仓接口已明确拒绝访问，随后读取本地需求单仍返回 200。现有登录有效期上限为 8 小时，这期间撤销动作不能及时覆盖客户、会话和材料数据。

应定义统一的登录撤销与角色变更同步机制，私有数据访问检查有效身份；至少收到明确的云仓会话失效后，立即撤销关联本地会话。网络故障与明确的鉴权拒绝应分开处理。

复现用例：`test_revoked_warehouse_session_cannot_keep_reading_private_deals`。

### 3. [P1] 报价只校验顾问归属，没有校验属于当前客户单

位置：[closing.py:529](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/tour/advisor/closing.py:529)，相同问题还在 `set_sales_total`、`send_quote`。

`store.one` 校验组织和用户，但不会校验路径中的 `deal_id`。复现：把同一顾问的客户甲报价 ID 提交到客户乙成交接口，返回 200，并按甲的报价给乙记应收、应付与定金。这里是**同一顾问名下客户串单**，并未证明跨顾问读取。

所有包含 `deal_id` 和对象 ID 的写操作，都应校验对象属于该单；成交登记还要校验报价种类、有效性和本单确认状态，避免仅凭报价 ID 就记账。

复现用例：`test_sale_rejects_another_deals_quote`。

### 4. [P1] 重复提交线下成交会重复记账

位置：[closing.py:529](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/tour/advisor/closing.py:529)。

已对需求单加锁，但没有检查是否已经登记成交，也没有操作幂等标识。相同请求提交两次，应收从 **29,600 变成 59,200 元**，已收定金从 **10,000 变成 20,000 元**，应付、利润和后续任务也重复累加。刷新、重试或重复点击即可触发，不需要并发请求。

应在事务内保护成交状态，使用业务操作幂等键或唯一约束，使重试返回已有登记；后续变更走明确的调整流程。收款登记也需要同类重试保护。

复现用例：`test_repeated_sale_does_not_double_money`。

### 5. [P1] 模型迟到的结果会覆盖顾问已经保存的修改

位置：[turns.py:225](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/tour/advisor/turns.py:225)。

“直接填充还是生成变更提议”按调用模型前的需求判定，写回时虽然重读并加锁，却直接执行旧的填充决定。复现：模型处理“从上海出发”期间，顾问手动保存“杭州”；模型返回后，杭州被无提示覆盖成上海，来源也变为客人原话。

应在锁内基于最新字段重新判定变更，或校验调用时的需求版本与字段原值；并发新增信息不能覆盖已保存的顾问输入，应转为待采纳提议或明确冲突。

复现用例：`test_late_model_fill_preserves_manual_edit`。

### 6. [P1] 旧方案内容会被标成最新需求版本

位置：[selling.py:288](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/tour/advisor/selling.py:288)。

方案内容先按旧需求生成，写入前重读需求单后直接使用最新 `need_version`，没有检查生成期间的修改。复现：生成期间人数从 2 人改成 3 人，结果方案正文仍是“2 大”，却与当前需求一样标记为版本 5，状态为可分享的草稿。

应保存生成所依据的版本，提交前确认未变化；冲突时重建或拒绝提交。不能把旧内容重新标成当前版本，避免绕过已有方案作废机制。

复现用例：`test_plan_cannot_stamp_old_content_with_new_need_version`。

### 7. [P1] 同团期有多个套餐时，后台擅自选择第一个核价

位置：[closing.py:232](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/tour/advisor/closing.py:232)。

未传 `offer_id` 时，代码从在售套餐中直接取第一项。复现两个在售套餐 `o1/o2`，未作选择就向云仓提交 `o1` 并返回有效报价。顾问没有机会确认报价对应哪种套餐，列表排序变化还可能改变结果。

应只在恰好一个可选套餐时自动选择；多个套餐必须先展示差异并由顾问明确选择，云仓继续承担套餐与团期的关系验证。

复现用例：`test_multiple_offers_require_explicit_choice`。

### 8. [P1] 工作区锁文件未更新，现有构建链路也会受影响

位置：[examples/package.json:8](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/package.json:8)。

新增 `*/advisor-web` 工作区，却没有更新 `examples/package-lock.json`。干净安装预检明确失败：`Missing: acme-tour-advisor-web@0.1.0 from lock file`。现有商户端与顾问端 Dockerfile 都在这个工作区执行 `npm ci`，因此影响的不只是新页面。

应同步提交锁文件，并从干净依赖环境验证新旧前端构建。复用本机 `node_modules` 的编译通过不能替代这项检查。

证据：[npm-ci.log](/Users/k/projects/Tour_Agent/output/advisor-v4-review/npm-ci.log)。

### 9. [P2] 客户分享页持续展示已经过期的价格

位置：[selling.py:443](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/tour/advisor/selling.py:443)、[客户页:88](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/tour/advisor-web/app/p/[token]/page.tsx:88)。

分享方案复制价格后，公开接口不再检查价格期限，前端也没有使用返回的 `valid_until`。把读取时间推进到报价过期后，仍返回并显示原人均价、全家总价与“就选这个”。页面虽有“以最终确认为准”，但没有提示该价格已经失效。

应在服务端按过期状态返回明确的待核价信息，前端停止把旧价作为当前价格展示；历史参考价若保留，应附核价时间和失效标签。分享链接的有效期、撤销及供应商授权变化，也需要完整的生命周期设计。

复现用例：`test_public_plan_stops_displaying_expired_prices`。

### 10. [P2] 修改销售总价后，对客明细与总额不一致

位置：[closing.py:512](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/tour/advisor/closing.py:512)、[pricing.py:245](/Users/k/projects/Tour_Agent/.claude/worktrees/advisor-v4/examples/tour/advisor/pricing.py:245)。

把两人销售总价改为 32,000 元后，利润按新总额计算，但发送文案仍为 **“成人 14,800 × 2；团费合计 32,000”**；明细合计实际是 29,600 元。分享页的人均价同样继续按门市价计算。

销售价应与对客明细使用同一口径：支持销售单价分摊，或明确列出销售调整项。若只管理总销售价，发送文案应避免同时展示不匹配的门市价明细。利润继续使用销售价减结算价即可。

复现用例：`test_changed_sales_price_keeps_customer_breakdown_consistent`。

**架构与既定范围的匹配情况**

- 顾问客户、需求、会话、方案、报价和线下台账保存在独立 PostgreSQL `advisor` schema；云仓产品、行程、团期和基础报价通过 HTTP 读取，符合独立管理方向。顾问按组织与用户隔离，现有跨顾问需求单读取用例通过。
- 证件扫描件和识别结果使用 AES-GCM 保存，护照识别在本地执行，返回号码做脱敏。这部分比单纯保存上传文件更完整。
- 需求按字段保存来源、变更先提议、儿童按人保存年龄与占床，方向合理。否定词、数量、年龄限制等旧问题已加入测试；异步版本保护仍需补齐。
- 销售价、结算价、利润已经分开，利润公式符合既定口径。通用 Office ID 仍需由云仓统一结算身份配置保证，新服务没有另建顾问分级价体系。
- 当前成交是本地线下登记，没有向供应商占位、下单或付款，符合一期边界；即便是线下台账，也必须保证归属、金额和幂等正确。
- 本分支没有接入 Jev；模型层仍使用 Anthropic 客户端的结构化工具输出。上面的确定性状态问题应先由程序规则解决。

**商户端衔接及 ECS 上线仍缺的工作**

1. 身份契约：`Warehouse.login` 通过“云仓地址 + 邮箱”生成本地用户 ID，并选择第一个顾问组织。上线前应使用云仓稳定身份，明确多组织选择、邮箱变化和会话撤销规则，避免旧客户资料归属漂移。
2. 报价契约：统一套餐选择与报价有效性复核。当前 `quote_read` 定义后没有调用；混合占床在顾问端拼接两次报价，应补充真实供应商验证，覆盖年龄价格、附加费、币种和两次快照是否一致，不能只靠单一线性价格的模拟测试。
3. 搜索完整性：线路与团期仅取固定首批结果，未消费后续游标。供应商和团期增加后，需要覆盖分页后的匹配项，并把首批数量与总数区分清楚。
4. 数据切换：旧顾问数据未在本次 diff 中迁移。需要确定旧客户、会话、方案、报价及材料如何保留和读取，再准备可回退的入口切换。
5. 部署接线：现有 `Dockerfile.web` 只允许 `storefront-web/merchant-web`，Compose 仍指向原服务，没有新的 8006 服务、数据库权限、证件持久卷和密钥接线。应补齐新服务镜像、迁移与备份恢复、HTTPS 代理和健康检查；材料密钥应固定管理，不应按 README 示例在每次启动时重新生成。

建议先修复 P1 与 P2，再按原验收标准执行真实商户端多场景、多回合测试：完整需求、缺失年龄/占床、先询问再选线、换线/改期、多套餐、改销售价、价格过期、登录撤销、断网重试和重复成交。国旅环球应纳入真实供应商回归。完成数据切换与部署接线后，再单独验收 ECS。

**可复查材料**

- [原有测试结果](/Users/k/projects/Tour_Agent/output/advisor-v4-review/tests.log)
- [补充复现测试](/Users/k/projects/Tour_Agent/output/advisor-v4-review/test_review_regressions.py)
- [补充复现结果](/Users/k/projects/Tour_Agent/output/advisor-v4-review/regressions/tests.log)
- [前端编译记录](/Users/k/projects/Tour_Agent/output/advisor-v4-review/web-build.log)
- [依赖安装失败记录](/Users/k/projects/Tour_Agent/output/advisor-v4-review/npm-ci.log)
