# Brief：用 route-kit 技能包替换线路解析与线路详情

## 目标

云仓里线路附件的解析和线路详情展示，统一改用技能包 `examples/tour/skills/tour-route-parser/`（Python 包 `route_kit`，数据契约 `route-kit/1`）。现有的规则解析、字段抽取、逐日编辑候选，以及 RouteDoc 形态的逐日展示，全部退役。

本 Brief 取代 `route-parsing-optimization-brief.md` 第二阶段的 WP8–WP10。第一阶段已完成的 WP1–WP7 不回滚。

## 技能包现状（接入前提）

- **用法**：`pip install "examples/tour/skills/tour-route-parser[vision]"`，模板随包安装。流程见该目录的 `SKILL.md` 和 `README.md`。
- **流程**：`reader.read(path, pictures=True)` → `vision.transcribe(doc, model, pool)` → `segment.segment(doc)` → `extract.extract(doc, layout, model, meta, pool, sha256)` → `RouteContent`。
- **本地验证**：29 份 ECS 附件跑通。
  - 351 天全部整理成功；
  - 直接引用 86.8%；
  - 高风险原文未归入为 0。
- **模型与 JSON Schema**：模型用现有 DeepSeek 配置，走 Anthropic 兼容接口；图片转写需要模型支持图片输入，现有配置已验证可用。JSON Schema 在 `schema/route-content.schema.json`。

## 一、退役清单

| 模块 | 处理 |
|---|---|
| `examples/tour/api/route_parser.py`、`route_fields.py` | 删除 |
| `cloud-warehouse/cloud_warehouse/route_extraction.py` | 删除（0037 已停掉它的任务） |
| `route_editing.py`、`route_candidates.py`、`route_facts.py`、`examples/tour/api/route_editor_model.py`、`route_document_workflow.py` | 删除。route-kit 直接产出逐日节点，不再需要"daily-editor"候选 |
| `pdf_source.py`；`itinerary_source.py` 的 `parse_docx` | `tour_backend._read_itinerary` 改读 RouteContent 后删除；`fetch_attachment` 保留 |
| `route_doc.py`（RouteDoc 1.0–3.0） | 只读保留，用来显示已发布的旧内容，直到全部重新解析 |
| `Source.extraction_method` 的 `"mineru"` 取值、`docs/cloud-warehouse/mineru-skill.md` | 删除 |
| `merchant-web/components/route-itinerary.tsx`、`storefront-web/components/generative/RouteDaysCard.tsx` 的 RouteDoc 读法 | 换成新的线路详情组件（见五） |

删除前，先用新旧两套解析跑同一批 30 条线路，差异报告存档。

## 二、解析任务（worker）

`warehouse_documents` 的沙箱子进程没有凭据，限时 60 秒；route-kit 要调模型（1 + 天数 + 图片张数次）。按信任边界拆成两段：

1. **沙箱内（不可信文件）**：`reader.read(tmpfile, pictures=True)` 和 `segment.segment`。输出 `Document` 与 `Layout` 的 JSON（都是 dataclass，直接序列化）。解析 PDF/DOCX 的风险留在沙箱里。
2. **worker 父进程（有凭据）**：`vision.transcribe` → 重新 `segment` → `extract`。转写会插入新行，所以图片转写之后必须重新切分。
   - 模型缓存目录挂持久卷，重跑只为变化的部分付费；
   - 整条线路设总超时，例如 10 分钟；
   - 单天失败时技能包已降级为"仅原文"，不要整条重试。

**配置**：环境变量 `ROUTE_KIT_MODEL`（缺省读 `TOUR_MODEL`）、`ANTHROPIC_API_KEY`、`ANTHROPIC_BASE_URL`，写进 `document.env`。原来的 `TOUR_EXTRACTION_MODEL`、`TOUR_CONTENT_MODEL` 不再使用。

## 三、存储

- **`document_parse.body`** 存 `RouteContent` JSON（`by_alias=True`，顶层带 `"schema": "route-kit/1"`），`parser_version = "route-kit-1"`。
  - 体积在 150–600 KB 之间：`units` 内嵌全部原文，这是审核依据，不能删，低于 `MAX_CONTENT_BYTES`（2 MB）。
- **封面图** `cover.jpg` 按资产单独存（`document_asset` 旁的对象存储），JSON 里只存文件名 `source.cover_image`，不存 base64。
- **审核字段**（审核人、时间、处理结论）放 `document_publication` 或 `route_content_revision`，不写进 RouteContent。
- **旧数据**：RouteDoc 的发布保持可读。读取时按 `schema` 分派：有 `"schema": "route-kit/1"` 的走新路径，否则走 RouteDoc。

## 四、审核与发布流程

保留现有流程：解析 → 草稿修订 → 变更提议 → 审批 → 发布。需要改的地方：

- `finish_parse`、`_review`、`route_content.issues`、`route_consistency`、`route_applicability` 都按 `schema` 分派校验。RouteContent 的问题清单直接用 `quality.issues`，不再重算。
- **发布前阻断条件**：
  - 有 `DAY_MODEL_FAILED` 或 `DAY_EMPTY_RESULT`；
  - 附件天数与登记天数不一致，且没有人工确认；
  - `unmapped` 里仍有 `risky` 条目。
- **需要人工确认、但不阻断**：标 `auto`（原样挂入）的条目、`origin: "image"` 的内容（图片转写）、`ATTRIBUTE_UNSUPPORTED`。
- `reparse_diff` 改为按"天 → 节点名称 → 属性"比较 RouteContent，不再比较 RouteDoc 的叶子。

## 五、页面

设计依据是 `src/route_kit/templates/route.html`（本机案例页可直接对照），以及 `reference/prototype-mobile.html`、`reference/prototype-desktop.html`。**不要**把这个 HTML 直接给客户或顾问看：它内嵌全部原文和问题项，只用于内部审核。

1. **共享组件**：在 `examples/web-shared` 新建 `RouteDetail`（React + TypeScript），按模板的结构实现：
   - 封面区：封面图、标题、天数/晚数/国家、卖点、吃住行、价格表；
   - 行程亮点、简要行程；
   - 逐日卡片：节点时间线、用餐和住宿、当日提示；
   - 费用包含与不含、购物与自费、单房差与退改、温馨提示；
   - 侧栏"费用与安排提示"。

   标点整理函数 `tidy` 从模板移植到 TypeScript，只在显示时用。
2. **商户端**：`route-editor.tsx` 用 `RouteDetail`，外加审核模式（问题清单、原文对照、"图片转写""原样挂入""模型概括"标记）。审核模式等同模板的"审核看"和"原文对照"。
3. **顾问端（H5 / 小程序优先）**：用客户投影数据（见六）渲染单栏版本。天数标签吸顶，一天一屏，宽度 375 px 下不能横向滚动。
4. **顾问 agent**：`advisor.py` 读线路时改用新的客户投影，不再截断 RouteDoc 的 8000 字符。

## 六、客户投影

新增 `customer_projection_v2(content)`，返回对客字段，去掉：

- `cite`、`review`、`issues`、`units`、`quality`、`auto`；
- `source` 里除 `file_name` 以外的字段。

在投影里过滤：

- 属性为 `unknown` 的不输出；
- `recommend` 节点标"推荐·不含"；
- 价格保留"非实时报价"说明，另付费用不和团费相加。

顾问 agent、H5、未来的客户分享页都只用这份投影。

## 七、检索与标签

`catalog.py`、`route_docs.shopping_stops` / `hotel_grade` / `meals_included`、`advisor_matching`（`no_shopping`）、`tag-rules.json` 目前读 RouteDoc。改为从 RouteContent 派生：

- 购物点：`shopping` 加上 `type == "shopping"` 的节点；
- 自费：`optional_items` 加上 `optional`、`package` 节点；
- 含餐数：`meals.*.status == "included"`；
- 酒店标准：`stay.grade_text` 和 `cover_facts`；
- 卖点：`selling_points`。

派生结果作为索引列存到 `supplier_product`，不在每次查询时解析 JSON。

## 八、部署

- `cloud-warehouse/deploy/Dockerfile.api` 和 `examples/tour/deploy/Dockerfile.api` 复制技能包目录，执行 `pip install "./tour-route-parser[vision]"`。
- 镜像里已有 `poppler-utils`，要确认包含 `pdftoppm`。
- 模型缓存目录挂卷。

## 九、实施顺序与验收

| 阶段 | 内容 | 验收 |
|---|---|---|
| P1 | 安装技能包；拆分 worker；按 `schema` 存储和读取；新旧解析并行 | ECS 上 30 条线路全部生成 RouteContent，指标不低于本地：整理成功率 100%，直接引用 ≥ 85%，高风险未归入为 0 |
| P2 | 商户端 `RouteDetail` 和审核模式；发布阻断规则 | 审核一条线路，从问题清单到发布走通；阻断条件逐条可复现 |
| P3 | 客户投影；顾问端 H5 单栏详情；顾问 agent 改读投影 | 375 px 下 30 条线路逐一截图，没有横向滚动、没有原文字段泄露（投影里搜不到 `cite`、`units`） |
| P4 | 检索和标签改读 RouteContent；删除退役模块；旧数据重新解析 | `pytest` 全绿；`rg route_parser` 等退役模块名无引用；旧发布内容可读或已重新发布 |

每个阶段结束时部署到 ECS，接入云仓数据验收。

## 不做

- 景点库。
- 占位。
- 让模型写入业务表：技能包只产出待审核内容，发布必须经过人工审批。
