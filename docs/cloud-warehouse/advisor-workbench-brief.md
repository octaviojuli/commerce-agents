# 云仓顾问工作台重构 Brief

交给 Codex 执行。遵守仓库根目录 `AGENTS.md`：Python 3.11+、ruff、pytest，前端用 Next.js + TypeScript；测试样本只用虚构的 ACME 资料；改动提示词或工具描述后要重新生成 `system.md` 并通过 `scripts/check.py`；新增模块同步更新 `AGENTS.md` 和对应 README。

交互以原型为准：[`advisor-workbench-prototype.html`](advisor-workbench-prototype.html)，在浏览器里直接打开。右上角"设计说明"列出了 8 条设计决定。原型里的数据、金额和线路名都是演示用的。

## 1. 背景

云仓顾问端是 `TOUR_BACKEND_MODE=warehouse` 这个分支，由以下文件组成：

- 前端：`examples/tour/storefront-web/components/warehouse/*`、`lib/warehouse.ts`
- Agent 装配：`examples/tour/api/warehouse_agent.py`、`warehouse_chat.py`
- 后端：`cloud-warehouse/cloud_warehouse/advisor.py`、`conversations.py`、`api.py`

测试中发现的问题：

1. **布局复杂。** 默认进入的是线路目录，并列有四个视图；询价要在独立表单里重新填人数和房型；分页通过往聊天里发"游标 xxx"实现。
2. **没有会话状态。** 会话状态只有 `seen_products`。客人的目的地、时间、人数只存在对话文字里，`enable_memory=False`。
3. **没有流程主线。** 缺少"需求解析 → 判定完整 → 召回 → 团期 → 报价"这条线。`warehouse_agent.py` 的 `domain_search_notes` 用了 30 多条"不得……"规则代替状态和关卡。
4. **检索太弱。** 天数只能精确匹配，不能按人数判断余位，没有匹配理由，也不排序。

原版 Tour 顾问（legacy 分支）的做法可以参考，但不照搬：

- `api/tour_backend.py` 的 `SearchContext`、`Overview`
- `api/focus.py`、`api/next_steps.py`

这些状态都存在进程内存里，重启就丢，而且人数默认 2 人。本次要做的，是把这些思路放到云仓里持久化。

## 2. 范围

**要做：**
- 持久化的需求单；
- 服务端的完整度判定和阶段；
- 带匹配理由的召回；
- 按人数判断团期是否有位；
- 从需求单取参数的报价；
- 重新布局的前端；
- 以"发给客人"和线下占位作为流程终点。

**不做：**
- 在线占位、下单、支付。一期边界不变：`inventory_pool.held` 保持为 0，交易接口继续返回 `TRANSACTIONS_DISABLED`。
- 商户端。
- legacy 模式。它不受影响。
- 顾问个人习惯记忆。以后再议。

## 3. 核心设计

### 3.1 需求单 TripBrief

新增模块 `cloud-warehouse/cloud_warehouse/trip_brief.py`。

**每个字段都带来源信息：**

```python
class Source(StrEnum): said, inferred, advisor   # 客人说 / 推断·待确认 / 顾问改
class Field_[T]: value: T | None; source: Source | None; evidence: str = ""; hint: str = ""
```

**字段清单：**

| 字段 | 类型 | 说明 |
|---|---|---|
| `destinations` | `list[str]` | 国家、城市或区域 |
| `window` | `{start: date, end: date}` | 出行时间窗口。"春节前后"这类说法换算后记为 `inferred`，`hint` 写明换算依据 |
| `days` | `{min: int, max: int}` | "12 天左右"记为 11–13 |
| `depart_city` | `str` | |
| `party_total` | `int` | 客人只说了总人数时只填这一项 |
| `adults`、`children`、`seniors` | `int` | 客人没有说明时保持 `None`。**不能用 `party_total` 推出来，也不能默认** |
| `child_ages` | `list[int]` | |
| `rooms` | `{doubles, twins, singles: int, child_bed: bool \| None, raw: str}` | |
| `preferences` | `list[{key, label}]` | 取值限定在一个受控集合里，首批：`no_shopping`、`no_self_pay`、`slow_pace`、`family` |
| `budget` | `{max_per_person: Decimal, currency}` | 可选 |

**会话选择：** `route_id`、`departure_id`、`offer_id`、`quote_id`、`quote_brief_version`、`share_token`，加上一个线下状态字段（取值 `none`、`customer_confirmed`、`offline_hold_recorded`），以及 `offline_hold_note`（纯文本备注）。

**持久化：**
- 新迁移 `0038_trip_brief`，建表 `conversation_brief`：
  - 列：`conversation_id` 为主键，外键指向 `agent_conversation`；另有 `organization_id`、`user_id`、`body jsonb`、`version`、`updated_at`。
  - 行级安全策略与 `agent_conversation` 相同。只有会话本人可以读写。
- 用 `version` 做乐观锁：写入时带上 `expected_version`，不一致返回 409。

**完整度函数 `readiness(brief)`：** 返回两个级别，每个级别给出 `missing` 和 `inferred` 两份清单。

- **search 级别：** 需要 `destinations` 和 `window`。另外，有 `party_total` 才能判断团期有没有位。
- **quote 级别：** 在 search 级别的基础上，还需要：
  - `adults` 和 `children`；
  - `child_ages` 的个数和 `children` 一致；
  - `rooms` 中至少有一种房型数量大于 0；
  - 有儿童时，`child_bed` 不能是 `None`。
- `inferred` 状态的字段不阻止操作，但要在界面上和模型回复里提醒顾问确认。

**转换 `to_party(brief)`：** 把需求单转成 `quotes.Party`。字段不全时抛出 `BriefIncomplete`，并带上缺失字段的清单。**禁止依赖 `Party` 的默认值**（`adults` 默认为 1）。

### 3.2 阶段由服务端推导

阶段不单独存储，每次由需求单和会话选择推导出来：`need → select → departure → quote → shared`，另有一个线下状态作为补充。

每个阶段允许的下一步写在 `advisor_stages.py`，思路参考 `examples/tour/api/next_steps.py`。模型给出的建议按钮会被服务端过滤：当前阶段不允许的步骤、或引用了未展示过的编号，都会被丢掉。

### 3.3 注入模型

`WarehouseAdvisorBackend.get_account_context(session)` 已经存在（`advisor.py:440`），在它里面追加以下内容：

- 需求单摘要：字段值、来源、提示；
- `readiness` 的结果；
- 当前阶段和允许的下一步；
- 会话选择。

这里的 `session.session_id` 就是 `conversation_id`（`api.py:1131`）。这些内容会进入缓存断点之后的受限数据块。如果超过 `account_max_chars` 的上限（2000），在 `ShoppingAgentConfig` 里调大这个上限，不要截掉需求单。

### 3.4 工具

以下全部放在 `warehouse_agent.py` 的扩展里，由服务端校验参数。

1. **`update_trip_brief`**：模型把顾问这一句话解析成字段写入需求单。
   - 每个 `said` 字段都要带 `evidence`，并且必须是本轮用户消息原文的子串，由服务端检查。不是子串的字段拒绝写入。
   - `inferred` 字段必须带 `hint`，说明是怎么推出来的。
   - 模型不能把字段写成 `advisor` 来源；这个来源只留给面板上的人工修改。
2. **`search_routes`**：默认从需求单取条件。可以临时覆盖某个条件，但不会写回需求单。返回的是线路列表加每条的匹配理由（见 WP3）。
3. **`present_warehouse_departures`**：日期窗口和人数从需求单取，每个团期给出按人数判断的结果。
4. **`present_warehouse_quote`**：**删掉 `party` 参数**，人数和房型一律从 `to_party(brief)` 取。需求单不完整时拒绝执行，并返回缺失字段的清单，模型据此一次追问一件事。
   - 只有一个有效方案时自动选用，不再要求先调用方案工具。
5. **`share_quote`**：复用现有的 `quote_shares`。

同时精简 `domain_search_notes`：凡是已经由上述状态和关卡保证的规则一律删掉，目标是 10 条以内。PR 里列出删掉的每一条，以及它现在由哪段代码保证。

## 4. 工作包

按顺序交付，每个工作包单独提交，单独可验证。

### WP1 需求单和完整度

内容：`trip_brief.py`、迁移 `0038_trip_brief`、以下接口：

- `GET /v1/conversations/{id}/brief`
- `PATCH /v1/conversations/{id}/brief`：带 `expected_version`；由人工修改的字段，来源记为 `advisor`。

验收：
- 单元测试覆盖 `readiness` 和 `to_party` 的全部分支，包括"只给了总人数时不能推出成人数""儿童年龄个数不一致""有儿童但占床未定"。
- 数据库测试：另一位顾问、另一个组织、匿名请求都读不到需求单；版本冲突返回 409；API 进程重启后需求单还在。

### WP2 工具、阶段和注入

内容：3.2 到 3.4 节。

验收：用虚构 ACME 目录和脚本化的模型回复，跑通原型里的这段流程：

> "一家 4 口，春节前后，西葡，12 天左右，上海出发，不进购物店"
> → 需求单写入：成人和儿童构成为空；出行时间是 `inferred`
> → 检索照常进行
> → 询价被拒绝，并返回缺失字段
> → 补充"2 大 2 小，8 岁和 11 岁，两间双人房"
> → 询价成功，报价里的人数和房型与需求单一致

另外要覆盖：
- `evidence` 不是原文子串时被拒绝；
- 当前阶段不允许的建议按钮被过滤；
- `domain_search_notes` 精简到 10 条以内。

### WP3 召回与匹配理由

改造 `advisor.py` 的 `catalog_page` 和 `departures_page`。

**线路层：**
- 天数按区间匹配。
- 判断日期窗口内是否有团期。
- 出发城市作为软条件：不符合也返回，但标出冲突。
- `no_shopping` 按已发布、已复核的 RouteDoc 判断，也就是 `shopping` 列表为空、行程里没有 `购物` 类型的景点。没有复核稿时结论为 `unknown`。
- 每条线路返回结构化的匹配理由：`[{criterion, verdict: ok|conflict|unknown, text}]`。
- 排序：完全匹配的在前；其余按冲突项数量从少到多；再按窗口内最早的团期。

**团期层：** 按 `party_total` 给出结论，取值为 `ok`、`short`、`unknown`、`out_of_window`。
- 库存过期时返回 `unknown`。
- 继续遵守现有规则：不向顾问展示具体余位。

所有查询继续受行级安全约束，SQL 片段固定、参数绑定的写法保持不变。

验收：
- 用虚构目录覆盖以下情形：天数区间、窗口外团期、北京出发线路标出冲突、有购物店的线路标出冲突、未复核线路结论为 `unknown`、余位不足人数时结论为 `short`。
- 查询计划测试（`capacity-query-plans` 的做法）确认没有新增全表扫描。

### WP4 前端重排

照原型重写 `components/warehouse/Workbench.tsx`：

- **三栏布局。** 左边是客人会话列表：一个客人一条会话，标题从需求单自动生成，例如"4 人 · 西葡 · 春节"。中间是对话。右边是需求单和当前选择。
- **顶部阶段条**，取值来自服务端推导的阶段。
- **需求单面板。**
  - 每个字段显示来源标签和"修改"按钮；修改走 `PATCH`。
  - 完整度分"可以检索"和"可以询价"两行显示。
  - 手机宽度下改成"对话 / 需求单"两个标签页。
- **卡片按钮直接调接口。** 看团期、询价、下一页、发给客人都直接调用接口。
  - 新增 `POST /v1/conversations/{id}/actions`：更新会话选择，并往对话记录里写一条紧凑的操作事件，比如"▸ 看团期：线路名"。模型下一轮通过注入的会话选择知道发生了什么。
  - 不再往聊天里发"游标 xxx"这类文本。
- **删掉独立的询价表单**（`Quote.tsx` 里的 `QuotePanel`），询价参数一律来自需求单。
  - 需求单里的人数或房型变了之后，已有报价显示"失效，需要重新询价"。判断方法：报价记录的 `quote_brief_version` 早于需求单中影响报价的字段最后一次修改的版本。
- **精简顶层视图。**
  - "线路目录"降为对话里的次要入口，比如一个"浏览目录"抽屉。
  - "报价分享"放进当前会话的选择面板。
  - "历史档案"放进会话列表的菜单。
  - 只有一个组织时，跳过选组织这一步。
- **保留现有能力：** 登录、会话续接、历史回放、`X-Request-ID`。

验收：在浏览器里按原型走一遍完整流程，桌面宽度和 375px 宽度各截一张图，附在 PR 里。`npm run build` 通过。

### WP5 流程终点

**发给客人：**
- 复用 `quote_shares`。
- 客人看到的页面不含同行结算价、余位数和供应商信息。这一点已有测试覆盖，本次保持。

**发送之后显示"线下占位"卡片：**
- 提供"复制给计调"的文本：线路、团号、出发日期、成人儿童构成、房型、报价有效期。
- 顾问可以标记"客人已确认"，也可以填写线下占位备注。两者都只写入需求单里的线下状态字段和 `offline_hold_note`。
- **不调用任何上游接口，不改变库存，不创建订单。**
- 会话列表里显示这个状态，比如"待线下占位"。

验收：有测试断言上述操作前后，`inventory_pool`、`inventory_movement` 以及上游调用次数都没有变化。

## 5. 通用验收

```bash
ruff check . && ruff format --check . && pytest && python scripts/check.py
python cloud-warehouse/scripts/ci_database.py      # 需要专用测试集群
cd examples && npm run build --workspace tour/storefront-web
```

- legacy 模式的测试全部通过，行为不变。
- 旧会话没有需求单时，按空需求单处理，可以继续使用，不报错。
- PR 描述写明：新增的表和接口、删掉的提示词规则及替代它的代码、截图、部署时需要执行的迁移。
