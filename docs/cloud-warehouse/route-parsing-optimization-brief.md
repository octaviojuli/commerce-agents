# 线路附件解析优化 Brief

改动提示词、工具描述或技能后要重新生成 `system.md` 并通过 `scripts/check.py`；新模块需要同步更新 `AGENTS.md` 和对应 README。

## 1. 背景

云仓线路附件（PDF/DOCX）的解析流程如下：

```
documents.py Worker
  → examples/tour/api/warehouse_documents.py      受限子进程
  → examples/tour/api/route_parser.py             正则规则解析，产出 RouteDoc
  → examples/tour/api/mineru_source.py            hybrid 模式下与 MinerU 结果二选一
  → cloud-warehouse/cloud_warehouse/route_content.py  候选内容与发布阻断项
  → cloud-warehouse/cloud_warehouse/route_editing.py  模型逐日改写与核对（可选）
  → 人工草稿 → 审批 → customer_projection 对客输出
```

字段契约定义在 `cloud-warehouse/cloud_warehouse/route_doc.py`；`examples/tour/data/route-schema.json` 是它生成的 JSON Schema。Codex 技能 `examples/tour/skills/route-document-cleaning/` 调用同一套程序。

以下原则保持不变：只采用有证据的字段，缺失留空并标记待确认，不补值；原始解析、机器候选、人工稿、已发布内容各自独立且不可覆盖；解析结果不回写 B2B，不修改价格和库存；机器结果不会自动发布。

## 2. 目标

1. 修正完整度评分的缺陷，使 `completeness` 和 `needs_review` 如实反映缺项。
2. 移除 MinerU，统一改用原生解析。
3. 建立字段级的准确性回归，并在解析器版本升级后能批量重跑、对比差异。
4. 补齐影响销售和合规的字段，加入字段间的一致性检查。
5. 降低接入新供应商版式的成本：增加一路带源行引用的模型抽取候选，并按天、按字段合并。
6. 加强改写校验。

不在本次范围内：订单与交易、价格计算、前端重新设计、从已发布内容导出 PDF/Word 行程单。

## 3. 工作包

按编号顺序交付，每个工作包单独提交，单独可验证。

### WP1 修正完整度评分

位置：`examples/tour/api/route_parser.py`，包括 `score()`（约第 1621 行）和 `parse_route()`（约第 1666 行）。

现有问题：

- 用餐检查只判断 `breakfast.included is not None`，午餐和晚餐缺失不扣分。
- "没有读到参考航班"对所有线路都扣 0.10，巴士、火车、邮轮线路会被固定扣分。
- 住宿检查只看 `overnight != "unknown"`，不检查是否读到了酒店名。
- PDF 用 pdftotext 的两种顺序各解析一遍，按 `completeness` 取高者。分段错误但字段多的结果可能胜出。

要求：

- 对 `overnight != "home"` 的每一天，三餐都要检查。
- 只有当文档里有航班迹象时才检查航班：封面写了航空公司、正文出现"航班"或"机场"，或某天的过夜类型是 `flight`。没有航班迹象时，这一项不参与评分，权重按比例分给其他项，总分上限仍为 1。
- 过夜类型为 `hotel` 的天，必须读到非空的 `hotel.name` 才算有住宿。
- PDF 选版时，先看天号是否连续且等于标称天数，再比较完整度。
- `PARSER_VERSION` 递增。

验收：`examples/tour/api/tests/test_route_parser.py` 增加以下虚构样本的用例：午餐或晚餐缺失、无航班的巴士线路、只识别出过夜类型但没有酒店名、两种 PDF 顺序中一种天号断裂。现有测试全部通过。

### WP2 移除 MinerU，统一原生解析

决策依据（本地实测）：`.warehouse/objects` 里共 92 份 PDF。75 份多页 PDF 全部有正常的文字层，最低约每页 1100 字；唯一没有文字层的是一份 2 页的测试文件。附件里还有 318 份 DOCX，本来就不走 MinerU。`docs/cloud-warehouse/mineru-skill.md` 记录的上线样本中，MinerU 唯一被采用的产出是一份 PDF 封面图片里的"全程四星酒店"和"四川航空"。它逐日解析的结果全部不如原生，其余情况下两者都不完整。它带来的成本包括：外部数据传输、密钥管理、最长 30 分钟的异步等待、`document_extraction_job` 的续接状态机，以及约 400 行适配代码。

要求：

- 删除 `examples/tour/api/mineru_client.py`、`mineru_source.py` 和 `tests/test_mineru.py`。
- `warehouse_documents.py`：去掉 `mode` 分支和 `MINERU_API_KEY` 的传递，只保留原生解析。
- `route_document_workflow.py`：去掉 `--mode` 和 `--key-file` 两个参数。
- `cloud_warehouse/documents.py`：去掉外部抽取的 pending/续接逻辑。不再新建外部抽取任务。升级时如果还有进行中的任务，转为明确的失败码（例如 `DOCUMENT_EXTRACTION_RETIRED`），并允许供应商重新解析。
- 历史数据保持可读：`Source.extraction_method` 的 `Literal` 保留 `"mineru"` 和 `"hybrid"`，旧解析照常通过校验；已保存的 extraction 证据和 `GET /documents/{id}/parses/{pid}/extraction` 保持只读可用。不删除 `0034_document_extraction` 的表和数据。
- 用原生检测替代封面图片识别：通过 Poppler（API 镜像里已有）识别"含有图片但文字很少"的页面，在 `quality.needs_review` 里写明，例如"第 N 页为图片，其中文字未读取，请核对封面的酒店标准和航空公司"，并在 `route_content.issues` 里给出可定位的问题项。没有文字层的 PDF 继续返回 `DOCUMENT_TEXT_MISSING`，提示供应商提供 DOCX 或带文字层的 PDF。
- 配置和文档：删除 `deploy/document.env.example` 里的 `TOUR_DOCUMENT_MODE` 和 `MINERU_API_KEY`；更新 `deploy/README.md`、`cloud-warehouse/README.md`、`examples/tour/README.md` 和 `AGENTS.md` 的相关条目；`docs/cloud-warehouse/mineru-skill.md` 改写为只描述原生解析流程，或者删除；更新 `scripts/ci_database.py` 中的引用。
- 技能：更新 `SKILL.md` 和 `references/warehouse.md`，去掉 hybrid/mineru 模式说明。

验收：全仓库 `grep -ri mineru` 只剩历史兼容相关的内容（`Literal` 取值、迁移文件、旧证据的读取接口）；数据库测试套件零跳过通过；容器验收脚本 `scripts/container_smoke.py` 通过。

### WP3 字段级回归与重跑对比

- 在 `examples/tour/api/tests/fixtures/route_eval/` 下建立虚构的标注样本，每个样本包含原文件和期望输出 `expected.json`。首批覆盖以下情形：跨页表格、天号缺失或重复、跨日航班、内观/外观、门票与讲解分开计费、自费与赠送、酒店"或同级"、包含和不含分两栏排版、概览表与逐日明细冲突、同一附件含多个版本（A/B 版或季节版）、无航班线路、封面为图片。
- 新增 `scripts/eval_route_parser.py`，按字段计算：天数和标题、三餐、住宿、航班、景点及其类型、包含/不含、自费、购物、政策。每类报告精确率和召回率，缺失和错误分开计数。不输出单一的"准确率"。
- 支持同时读取 `.warehouse` 下的私有标注样本（样本不提交），私有报告只写入 `.warehouse`。
- 新增运维命令 `warehouse reparse-diff --organization UUID --user UUID [--apply]`：用当前解析器在内存中重跑最新解析，按字段输出差异。默认只读；加 `--apply` 才走现有的 reparse 流程生成新一代解析。不影响人工稿和已发布版本。

验收：CI 里运行评测脚本，指标低于提交在仓库中的基线时失败。WP1 前后的指标对比写进 PR 描述。

### WP4 字段补齐与一致性检查

先写一份 ADR（`docs/cloud-warehouse/adr-010-route-doc-v3.md`），定稿后再实现。

`RouteDoc.schema_version` 增加 `"3.0"`。所有新字段都有默认值，旧文档照常通过校验。凡是结构化字段，同时保存原文句子作为证据；拿不准的值留 `None`，不猜测。

1. **行程版本与适用范围**
   - 新增 `applicability`：适用日期区间、出发城市、版本标签。
   - 解析器识别到多个版本时（例如"4–6 月版 / 7–8 月版"，或同一附件出现两套 D1 到 Dn），不自动拆分，而是报出 `VARIANTS_DETECTED` 阻断项，并标出每个版本对应的原文范围。
   - ADR 里说明版本如何按日期关联到 `departure`，并说明顾问读取行程时如何按所选团期取对应版本。
2. **成团**：最少成团人数、不成团时的处理方式、截止收客时间。
3. **费用结构化**：新增 `Money { amount: Decimal | None, currency, unit, raw }`，其中 `unit` 表示按人、按间或按晚。应用于司导服务费（小费）、单房差、自费项目价格。
4. **退改**：`cancellation_tiers: list[{days_before_min, days_before_max, penalty_percent | penalty_money, raw}]`。原来的 `policies.cancellation` 原文字段保留。
5. **人群与证件**：儿童年龄界限、占床与不占床；老人年龄及附加条件；孕妇限制；签证类型（申根、电子签、落地签、免签）、送签截止日期、护照有效期要求；保险是否包含及其说明。
6. **集合**：集合地点和时间、国内联运。
7. **逐日**：每天所在的国家和城市、用车类型；酒店房型和连住晚数；航班的当地起降时间和跨日偏移（保留 `times` 原文）。
8. **购物与自费**：购物店补充经营品类，并汇总购物店总数。依据是《旅游法》第三十五条对指定购物场所和另行付费项目的限制，这两类信息要能完整进入后续的合同附件。

一致性检查写在 `route_content.issues`，每一项都要能定位到具体的天或字段：

- 同一项目同时出现在"包含"和"不含"里；
- 封面的酒店标准与逐晚的酒店等级不一致；
- 概览表的三餐与逐日明细不一致；
- 过夜类型为 `flight` 的那天没有航班；
- 行程天数与所关联团期的 `return_date - depart_date + 1` 不一致；
- 过夜晚数与 `summary.nights` 不一致。

对客输出：逐项确定新字段是否进入 `customer_projection` 白名单，结论写进 ADR。内部结算信息一律不进入对客输出。

同步更新：重新生成 `route-schema.json`（`scripts/parse_attachments.py --schema`），更新 `tests/test_route_doc.py`，商户草稿编辑器要能查看和修改新字段。

### WP5 模型抽取候选，按天、按字段合并

- 新增一路模型结构化抽取，作为与规则解析并列的候选：
  - 输入是带行号的原文；
  - 输出与 RouteDoc 同形；
  - 每个非空字段必须附带 `source_lines`，由代码回查引用的行是否存在、引用的原文是否包含该值，不通过的字段丢弃；
  - 只发送附件文字，不发送 ERP 凭据或客户资料；
  - 模型配置沿用 `route_editor_model.py` 的方式，未配置时整路跳过，只用规则解析。
- 合并按天、按字段进行：规则解析结果有效时优先采用；某个字段规则缺失、而模型候选通过引用校验时，采用模型值，并在 `field_sources`（`document_parse` 表的现有列）里记录来源。两者对同一字段给出不同值时，保留规则值，并写入 `needs_review`。
- 两路结果的差异对比按字段逐项进行，不再只比较条目数。

验收：在 WP3 的评测集上，与纯规则解析相比，每个字段的召回率提高或持平，精确率不下降。模型抽取的每个字段都能追溯到源行。

### WP6 改写校验加强

位置：`cloud-warehouse/cloud_warehouse/route_editing.py` 和 `route_facts.py`。

- 数字比较从集合改为多重集合，出现次数也要一致。
- 限制词检查增加"新增"方向：原文没有的"含门票""赠送""入内"等出现在改写后的文字里时，拦截，错误码 `EDITOR_CONDITION_ADDED`。
- 把数字与所属对象的对应关系纳入 `route_facts.compare`，覆盖景点与时长、项目与金额、路段与里程。用例至少包括："A 外观不少于 1 小时、B 外观不少于 2 小时"改写后两个时长互换，必须被拦截。
- 复核模型可以单独配置（`TOUR_REVIEW_MODEL`），默认使用与改写不同的模型。未配置时沿用现有模型，同时在证据里记录"复核与改写使用同一模型"。
- 对状态为 `edited` 的天，按配置的比例抽样，标记为需要人工复核，商户审核界面要能显示这些标记。

### WP7 运维可观测性与技能文档

- `warehouse_documents.py` 不再把子进程的 stderr 直接丢弃，改为截取末尾一段，经脱敏后（不含正文、路径、密钥）归类为固定的失败阶段（打开、分段、逐日、条款、校验），写入解析失败记录，运营监测里可以查看。
- 精简 `SKILL.md`：代码已经强制执行的规则（步数、超时、续接）不再重复写，只保留需要人判断的部分：核对清单、清洗原则、交付时要说明的内容。

## 4. 通用验收

每个工作包都要满足：

```bash
ruff check . && ruff format --check . && pytest && python scripts/check.py
python cloud-warehouse/scripts/ci_database.py   # 需要专用测试集群
```

- 历史解析、人工稿、已发布内容的哈希和版本不发生变化。
- 采购方和匿名访问仍然拿不到解析证据。
- PR 描述中写明：改了哪些字段、评测指标前后对比、历史数据如何兼容、部署时需要修改哪些配置。

---

# 第二阶段：逐日行程节点化

## 5. 背景与现状

第一阶段 WP1–WP7 的代码已经存在于工作区：RouteDoc `3.0`、`adr-010-route-doc-v3.md`、`route_extraction.py`（带源行引用的模型抽取）、`eval_route_parser.py`、`EDITOR_CONDITION_ADDED`、`TOUR_REVIEW_MODEL`、解析失败阶段。开始第二阶段前，先确认这些都已提交并通过验收；如果没有，先补齐，不要重做。

第二阶段要做的，是把每天的行程从"整理后的段落"（`Day.blocks`）改成"按顺序排列的行程节点"（`Day.items`）：智能体从清洗后的原文里提炼出当天的景点、活动和项目，每个节点带上结构化属性，页面按节点展示。

交互以两份原型为准，都在浏览器里直接打开：

- **手机端（H5）**：[`route-itinerary-nodes-prototype.html`](route-itinerary-nodes-prototype.html)。一次看一天，另有两个"版式样例"（自由活动日、邮轮港口日）。
- **电脑端**：[`route-itinerary-nodes-desktop-prototype.html`](route-itinerary-nodes-desktop-prototype.html)。三栏布局：
  - 左栏是简要行程，同时用作目录，滚动时自动高亮当前这一天；
  - 中间连续展示全部天数，三餐和住宿放在每天右侧的窄栏里；
  - 右栏随视图变化：客户看时是全程的费用与安排提示，顾问看时是当天的核对项，原文对照时是当天的原文，和中间的节点左右联动高亮。
  - 宽度小于 900px 时，退化为手机端的单栏阅读。

两份原型都有"客户看 / 顾问看 / 原文对照"三种视图。数据结构相同，只是排布不同。各自的"设计说明"列出了设计要点。

**不在本阶段范围内**：平台景点库，即跨线路统一景点、景点介绍和图片，以后再做。节点里的描述只来自供应商原文。

## 6. 样本验证结论

在本地快照上统计了 230 条已解析线路、2,564 个逐日条目，并逐天细读了 15 条不同类型的线路：经典西欧团、北欧极光、冰岛自然、英国半自由行、斯里兰卡亲子、斯里兰卡加马尔代夫、毛里求斯活动型、土耳其希腊、美东、西葡摩、南美加南极邮轮、东南亚邮轮、地中海邮轮、单地接用车。

统计来自本地的旧快照，只用来判断各种模式有多常见。比例不代表准确率，也不代表线上全量。

| 模式 | 出现情况 | 节点模板怎么处理 |
|---|---|---|
| 【】标注景点 | 74% 的逐日条目有；约 26% 没有 | 没有标注的日子（散文写法、邮轮岸上游）规则抽不到景点，必须由模型抽取 |
| 观光段："XX 市区观光（总观光时间不少于 N 小时）"下面列多个景点 | 857 个条目，121 条线路 | **观光段节点**（`group`），时长属于整段，景点作为子节点 |
| 上午、下午、晚上 | 509 个条目，105 条线路 | 只在原文写明时，才给节点标注时段 |
| 具体钟点 | 244 个条目，109 条线路；多为航班和靠港时间 | 保留原文写法，不推算 |
| 条件替代或顺序调整："如遇……则改为" | 204 个条目，138 条线路 | 节点下的 `alternative` |
| 不保证："无法确保""视天气""费用不退" | 108 个条目，56 条线路 | 节点下的 `disclaimer`，和替代安排分开 |
| 营业日、闭馆 | 137 个条目，98 条线路 | `disclaimer`，原因为 `operating_days` 或 `closure` |
| 赠送、升级、"特别安排" | 536 个条目，173 条线路 | "赠送"对应 `inclusion=gift`；"特别安排""升级"表示包含的亮点，对应 `highlight=true`，不等于赠送 |
| 包含项：含门票、讲解、船票、摆渡船、优速通 | 常见 | 节点的 `includes` 列表 |
| 小费 | 133 个条目，56 条线路 | 节点下的 `extra_cost_note` |
| 推荐自行前往（不含） | 51 个条目，39 条线路；多出现在自由活动日 | `inclusion=recommended_not_included`，界面上必须和包含项明显区分 |
| 自费套餐：打包价、几人成团、需提前报名 | 35 个条目，28 条线路 | **套餐节点**（`package`），套餐内容作为子节点 |
| 购物：指定购物店、奥特莱斯、百货、市场 | 购物相关词出现在 177 条线路 | `shopping.kind` 区分这四类；只有"指定购物店"才和"不进购物店"冲突 |
| 中外文名："独立宫 \|\| Independence Hall" | 319 个条目，85 条线路 | `name` 和 `local_name` 分开 |
| 邮轮：到港、离港、返船时间、海上航行、巡游不登岛 | 21 条线路 | 当天类型 `day_kind`，加上 `port_call` 字段 |
| 当天服务范围："当天不含车、司机、导游""用车仅含接送" | 17 个条目，11 条线路 | 当天的 `services` 字段 |
| 候选酒店："A / B / C" | 18 条线路 | 酒店的 `names[]` 列表 |
| 一条记录覆盖多天："第 23–26 天 南极巡游" | 邮轮和长线常见 | 当天增加 `day_end` 字段 |
| 活动的计量："雪橇 400 米、体验 5–7 分钟""合影 1 张/人" | 活动类常见 | 放进 `includes`，不单独建模 |

另外发现 4 个问题，会直接影响节点抽取：

- **概览表和详细行程被重复计为两套天数。** 例如 13 天的线路解析出 26 个条目。
- **邮轮线路天数错乱。** 10 天的线路解析出 19 个条目。
- **有些日子的标题是空的。**
- **原文断行残留**，例如"老；城"，名称被切断。

节点抽取的前提，是天数完整、连续。概览表的行应当并入当天的标题信息，不能另算一天。

购物店段落里常有产品功效宣传，比如保健品、动物制品。这些内容不能进入节点描述。

## 7. 数据契约（RouteDoc 3.1）

在 `cloud-warehouse/cloud_warehouse/route_doc.py` 中新增以下结构，`schema_version` 增加 `"3.1"`。所有新字段都有默认值，`1.0`、`2.0`、`3.0` 的文档照常通过校验。修改前先写 `adr-011-itinerary-nodes.md`，定稿后再实现。

### 7.1 节点 `ItineraryNode`

| 字段 | 类型 | 说明 |
|---|---|---|
| `node_id` | `str` | 同一天内稳定，编辑后不变 |
| `type` | 见下 | `meet` 集合、`transport` 交通、`poi` 景点、`group` 观光段、`activity` 活动体验、`free` 自由活动、`shopping` 购物、`optional` 自费项目、`package` 自费套餐、`recommend` 推荐·自行前往、`notice` 提醒 |
| `name`、`local_name` | `str` | 名称照原文摘录，修复断行；外文名放 `local_name` |
| `part_of_day` | `morning` \| `noon` \| `afternoon` \| `evening` \| `night` \| `None` | 只在原文写明时填 |
| `clock_text` | `str` | 原文写的钟点，不推算 |
| `inclusion` | `included` \| `gift` \| `optional_paid` \| `package_item` \| `recommended_not_included` \| `unknown` | 是否包含在团费里 |
| `highlight` | `bool` | 原文写了"特别安排""升级" |
| `visit_mode` | `inside` \| `outside` \| `passing` \| `drive_by` \| `distant_view` \| `walk` \| `unknown` | 依次对应入内、外观、途经、车游、远眺、步行、未说明 |
| `duration` | `{text, qualifier: about\|min\|max\|range\|None, low_minutes, high_minutes}` | 原文怎么写就怎么存；对观光段节点，表示整段的时长 |
| `ticket` | `included` \| `excluded` \| `free_entry` \| `unknown` | 外观不等于不含门票 |
| `includes` | `list[{kind: guide\|audio_guide\|boat\|ferry\|fast_track\|transfer\|meal\|photo\|quantity\|other, text}]` | 门票以外的包含项 |
| `price` | `Money` + `min_participants` + `booking_note` | 只用于 `optional`、`package`、`recommend` |
| `transport` | `{mode, from_place, to_place, distance_km, duration_text, service_no, times_text, reference: bool, overnight: bool}` | 交通方式：大巴、航班、火车、夜火车、渡轮、邮轮、小船、快艇、水上飞机、园区摆渡、步行、未知 |
| `shopping` | `{kind: designated_store\|outlet_mall\|department_store\|market, categories[], stay_text}` | 购物类型 |
| `children` | `list[ItineraryNode]` | 只有 `group` 和 `package` 可以有子节点，最多一层 |
| `alternative` | `{condition, text, citations}` | 条件替代 |
| `disclaimer` | `{reason: weather\|wildlife\|operating_days\|closure\|booking\|other, text, citations}` | 不保证事项 |
| `extra_cost_note` | `{text, citations}` | 小费等额外费用 |
| `description` | `str` | 不超过 120 字，从供应商原文里摘出、改写成客观介绍，要引用原文；购物节点一律留空 |
| `citations` | `list[int]` | 原文行号，至少 1 个；沿用 `route_extraction` 的源行编号 |
| `review` | `list[str]` | 内部问题码，不进入对客输出 |

### 7.2 每天 `Day` 新增字段

| 字段 | 说明 |
|---|---|
| `items` | `list[ItineraryNode]`，按原文顺序排列 |
| `day_kind` | `regular` 常规、`transit` 交通日、`free` 自由活动日、`at_sea` 海上航行、`port_call` 港口停靠、`cruising_no_landing` 巡游不登岛、`resort` 度假村日 |
| `day_end` | 一条记录覆盖多天时，填结束天号。完整度检查要按覆盖范围计算天数连续 |
| `services` | `{coach, driver, guide, tour_leader: included\|excluded\|partial\|unknown, note}` |
| `port_call` | `{port, arrive_text, depart_text, all_aboard_text}` |
| `coverage` | `{units, mapped, unmapped: [{line, text, risky: bool}]}` |

酒店增加两个字段：`names: list[str]`（候选酒店），以及 `check_in_text`。三餐仍放在每天底部，不进时间线。

### 7.3 兼容

- `Day.sights` 保留，作为规则解析的输入和交叉核对。
- `Day.blocks` 不再为新文档生成，但旧文档照常可读。`customer_projection` 在 `items` 为空时，回退到读取 `blocks` 或 `text`。
- 简要行程和 `summary_basis_hash` 改为由 `items` 派生。

## 8. 工作包

按顺序交付，每个工作包单独提交，单独可验证。

### WP8 节点抽取与校验

**新增模块 `cloud-warehouse/cloud_warehouse/route_nodes.py`**，负责三件事：契约校验、原文覆盖检查、和规则结果交叉核对。

**抽取**
- 沿用 `route_extraction` 的做法：把当天原文逐行编号，连同规则解析出的 `sights` 和当天标题一起交给模型。模型返回节点列表，每个节点都带引用的行号。
- 逐日任务新增处理策略 `daily-nodes-1`，接入现有的 `route_candidates` 和 `route-content-worker`：每次处理一天，沿用租约、续接和不可覆盖的规则。
- 旧策略 `daily-editor-2` 的候选全部保留。

**代码校验**（任何一项不通过，当天保留原文，并写明原因）
1. 节点和子节点的名称，经过规范化（NFKC、去空白、去【】、合并断行残留）后，必须能在所引用的行里找到。
2. 时长、价格、距离、钟点、人数里的每个数字，都必须在所引用的行里出现。
3. 属性要有对应的原文词语作依据：
   - `inside` 需要"入内"或"参观……内部"；
   - `outside` 需要"外观"；
   - `recommended_not_included` 需要"推荐""可自行""建议前往"；
   - `gift` 需要"赠送"；
   - `ticket=included` 需要"含门票"或"含首道门票"。
   依据词的对照表写成代码常量，并附测试。
4. 节点的顺序，要和它们引用的行号顺序一致。子节点不受这一条约束。
5. **原文覆盖。** 当天的每一行，都要归入某个节点、当天字段（`services`、`port_call`）或底部的三餐、住宿。没有归入的行记入 `coverage.unmapped`。其中含有"购物、自费、不含、小费、如遇、取消、不退、自理、成团"等词的行，标记 `risky=true`，**阻止发布**。
6. **和规则结果交叉核对。** 规则解析出了、但节点里没有的景点，记为 `RULE_SIGHT_MISSING`，交给人工确认，不直接判定模型错。
7. 购物节点的 `description` 必须为空。

**语义复核**
- 用 `TOUR_REVIEW_MODEL` 独立复核一次，检查有没有遗漏的项目，`inclusion`、`visit_mode`、`ticket` 有没有判错。
- 复核有异议时，这一天保留原文，并写明原因。

**前提检查**
- 只处理天数完整、连续的文档。计算连续性时，要考虑 `day_end` 覆盖的多天。
- 同时修正解析器：概览表的行不能被当成新的一天，要并入当天标题信息。补充"概览加详细行程"和"邮轮线路"两类测试样本。

**Skill**：`route-document-cleaning` 增加 `--nodes` 模式，用同一个入口生成 `nodes-candidate.json` 和核对报告，不写云仓，不发布。

### WP9 展示与编辑

**商户工作台**
- 按天编辑节点列表：新增、删除、排序、移入或移出观光段、编辑各个字段。
- 旁边显示原文。点节点时高亮它引用的行；没有归入节点的行单独列出。
- 保存草稿、校验、审批都沿用现有流程。

**顾问端和客户页面**
- 手机端按 H5 原型实现，并满足顾问端修复清单的移动端要求；宽度 ≥1024px 时按电脑端原型的三栏布局实现。两种布局使用同一份节点数据和同一套组件，不写两套页面。
- 电脑端右栏"费用与安排提示"由全程节点自动汇总：自费、购物、小费、条件替代、不保证。不另外手写。
- 客户投影增加 `items`，但不包含 `citations`、`review`、`coverage`，以及"原文未说明"这类内部标记。
- 顾问视图额外显示"原文未说明"，以及与需求单的冲突。冲突规则：需求单有 `no_shopping` 时，`designated_store` 显示为冲突；`outlet_mall`、`department_store`、`market` 显示为提示。

**检索**
- `advisor_matching` 判断"不进购物店"时，优先读已发布文档的 `items` 里有没有 `designated_store`；没有 `items` 的旧文档沿用现有逻辑。
- 把已发布节点的 `name` 和 `local_name` 写进检索副本，顾问可以按景点名找线路。

**三端适配**

以下内容按现有代码核对过，实现时以此为准。

1. **共享组件**
   - 在 `examples/tour/` 下新建共享包 `tour/web-common`，放节点时间线、观光段、套餐、原文对照、简要行程派生这些组件和工具函数，并把它加进 `examples/package.json` 的 `workspaces`。
   - 不要放进 `examples/web-shared`：那里是所有行业示例共用的代码。
   - 组件只使用 CSS 变量。两个应用各自保留主色：商户端 `#bb663b`，顾问端 `#1f7a8c`。组件用到而应用里还没定义的变量（紫色、高亮黄等），由组件提供默认值。
   - 样式用组件自带的 CSS Module，不依赖某个应用的全局类名。
2. **商户端（`tour/merchant-web`）**：保留现有页面外框（天数导航、概览、审批、事实核对），只替换内容部分。
   - `route-itinerary.tsx`：每天的正文区（`itinerary-content-blocks`、`itinerary-stops`）换成节点时间线。没有 `items` 的旧文档，继续按现有方式显示。
   - `route-editor.tsx`：从按段落块编辑，改为按节点编辑。支持新增、删除、排序、移入或移出观光段，以及逐字段修改。旁边显示原文，点节点时高亮它引用的行。
   - `route-fact-review.tsx`：增加原文覆盖情况和未归入节点的原文行，高风险行放在最上面。
   - `RouteContentApproval`：审批差异改成节点级，能看出哪天、哪个节点、哪个字段改了。
3. **顾问端（`tour/storefront-web` 云仓模式）**：目前没有行程详情界面，需要新建。
   - 线路卡片和团期卡片上加"逐日行程"入口，读取现有接口 `GET /v1/advisor/products/{id}/document`。从团期进入时，带上 `departure_id`。
   - 手机上打开为全屏页，按 H5 原型，默认用顾问视图，可以切到"客户看"预览。电脑上打开为右侧大抽屉，按电脑端原型。
   - 助手也要能读节点：`get_product_details` 给模型的已复核行程，改为节点摘要，包括名称、类型、游览方式、时长、是否包含、替代安排、不保证事项。这样助手能回答"进不进某个景点内部"这类问题。
4. **客户页面（`/quote` 报价分享页）**
   - 增加逐日行程，按"客户看"视图显示。
   - 分享记录要保存发送那一刻已发布内容的版本号，客户页面固定显示这个版本；之后重新发布，不影响已经发出去的链接。
   - 这需要调整 `quote_shares` 的客户投影，并补测试。
5. **还没发布内容时**
   - 顾问端和客户页面要明确显示"行程待复核"。顾问端另外提供"查看原始附件"入口，只给有权限的顾问。
   - 不显示空白，也不展示未发布的候选稿。
6. **上线顺序**
   - 先上线商户端的节点编辑和发布。运营逐条审核、发布展示范围内的线路后，再上线顾问端和客户页面。
   - 顾问端的改动，要等 [`advisor-workbench-fixes.md`](advisor-workbench-fixes.md) 的修复完成并提交之后再开始。两边都要改 `Workbench.tsx`，同时改容易冲突。
7. **ECS 现状**（2026-09-27 只读查询，迁移版本 `0038_trip_brief`）

   | 项目 | 数量 |
   |---|---|
   | 上架线路 | 30 |
   | 有附件、有解析稿 | 29 |
   | 天数完整连续 | 24 |
   | 有已完成的整理候选（`daily-editor-3`） | 29 |
   | 人工草稿 | 0 |
   | 已发布行程 | 0 |

   因此在 WP8 部署后，需要有一项明确的运营任务，按以下顺序处理：
   1. 对 24 条天数完整的线路生成节点候选，人工审核后发布；
   2. 天数有问题的 5 条和无附件的 1 条，单独核对原文，由人工补齐或说明原因；
   3. 在 `ecs-acceptance.md` 记录每条线路的发布状态。

   发布数达到约定目标之前，顾问端和客户页面的行程入口保持"行程待复核"状态。

### WP10 评测与验收

**评测样本**
- 在 `eval_route_parser.py` 的虚构样本里，为第 6 节表格中的每一种模式补至少 1 个样本，包括概览加详细行程、邮轮港口日、自由活动日加推荐、自费套餐、观光段、中外文名、断行残留。
- 每个样本附上期望的节点列表。

**指标**，分开报告，不合并成一个"准确率"：
- 节点召回率和精确率，按类型分别计算；
- `inclusion`、`visit_mode`、`ticket`、`duration` 各自的一致率；
- 原文覆盖率；
- 高风险未归入的行数，必须为 0 才能发布。

**私有真实样本**：在 `.warehouse` 下跑同样的评测，报告只写入 `.warehouse`，不提交真实内容。

**ECS 验收**，按顾问端修复清单 M12 的流程：
- 部署前备份，并在隔离空库演练恢复；
- 对展示范围内的全部线路生成节点候选；
- 人工抽查至少 30 天，覆盖第 6 节的全部模式，记录问题；
- 不自动发布任何内容；
- 验收记录写进 `ecs-acceptance.md`。

## 9. 第二阶段通用验收

沿用第 4 节的命令。另外要求：
- 旧文档（没有 `items`）在三个端都能正常显示。
- 历史解析、旧策略候选、人工稿、已发布内容的哈希和版本都不变。
- 采购方和匿名访问拿不到 `citations`、`coverage` 和核对报告。
