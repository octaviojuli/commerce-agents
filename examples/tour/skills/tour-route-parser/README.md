# tour-route-parser

旅游线路附件解析器：PDF / DOCX → 带原文依据的线路内容（JSON）→ 手机和电脑自适应的详情页。使用方法见 [SKILL.md](SKILL.md)。

## 目录

| 路径 | 内容 |
|---|---|
| `src/route_kit/reader.py` | DOCX、PDF 读取，页眉页脚清理，图片页识别，乱码页和表格错乱页的重读 |
| `src/route_kit/daylabel.py` | 天标签识别（"第三天""DAY-3""D3"），读取和切分共用 |
| `src/route_kit/vision.py` | 图片转写：PDF 图片页、扫描 PDF、DOCX 图片，封面图 |
| `src/route_kit/segment.py` | 证据单元切分，天数识别，概览表与详细行程区分 |
| `src/route_kit/prompts.py` | 模型指令：逐日、封面条款、PDF 天数边界、最后一天结尾、图片转写 |
| `src/route_kit/llm.py` | Anthropic 兼容接口的类型化调用，本地结果缓存 |
| `src/route_kit/extract.py` | 模型抽取、代码校验、覆盖统计、组装 |
| `src/route_kit/confidence.py` | 待审分和"先看"原因，只用解析结果自带的质量记录 |
| `src/route_kit/models.py` | 数据契约 `RouteContent`（`schema: route-kit/1`） |
| `src/route_kit/render.py` | 详情页和索引页渲染 |
| `src/route_kit/templates/route.html` | 详情页模板：数据内嵌，不加载任何外部资源（随包安装） |
| `pyproject.toml` | 安装为 `route-kit` 包：`pip install ".[vision]"` |
| `schema/route-content.schema.json` | 由 `models.py` 导出的 JSON Schema |
| `scripts/run.py` | 单个文件或批量运行 |
| `scripts/export_schema.py` | 重新导出 JSON Schema |
| `reference/` | 原型页面、字段说明、样本模式 |
| `tests/` | 虚构样本的单元测试，不调用模型 |

## 数据契约要点

- **证据单元**：`units[]` 是切分后的原文，编号从 1 开始。所有字段的 `cite` 都引用这些编号。`origin` 为 `image` 的单元是从图片转写的。
- **线路级字段**：`title`、`subtitle`、`cover_facts`、`selling_points`（封面卖点短标签）、`highlights`、`prices`（附件写明金额的价格信息，按团费、另付费用、儿童价、单房差分类；页面标注"非实时报价"）、`departure_dates_text`、`inclusions`、`exclusions`、`shopping`、`optional_items`、`policies`、`notices`。
- **餐数与购物**：`meal_counts` 是费用包含里写明的全程餐数（如"4 早 7 正餐"），逐日行程与它不一致时，审核视图提示；`shopping_status` 为 `none` 时，`shopping_quote` 是原文里表示"没有"的那句话，必须出现在引用的原文里。
- **逐日字段**：`days[]` 包含 `title`、`summary`、`day_kind`、`travel_text`、`services`、`port_call`、`items`、`meals`、`stay`、`notes`、`status`、`issues`。
- **节点** `items[]`：
  - `type`：集合、交通、景点、观光段、活动、自由活动、购物、自费、套餐、推荐、拍照点（`spot_label` 保留原文机位编号）、提醒；
  - 属性：`visit_mode`、`ticket`、`inclusion`、`duration_text`、`includes`、`highlight`、`transport`、`shopping_kind`；
  - 子节点：`children`，只有观光段和套餐才有；
  - 附带说明：`alternative`、`disclaimer`、`extra_cost_note`。
- **质量** `quality`：
  - 天数：附件天数与登记天数；
  - 覆盖：`direct_units`（被字段引用）、`inferred_units`（续句、短标签）、`auto_attached`（原样挂入的高风险原文，对应条目标 `auto`），以及仍未被整理的原文 `unmapped`（其中 `risky` 标记涉及费用、购物、条件的）；
  - 读取时删除的行：`removed`；图片转写的单元数：`image_units`；
  - 来源 `source`：`pictures`（每张图片的类型、转写行数、错误）、`cover_image`（封面图文件名）、`scanned`（整份 PDF 没有文字层）；
  - 问题项：`issues`；
  - 待审分 `confidence`（0–100）和 `review_reasons`（先看的原因）：只用于分流，能挑出解析失败的线路，不能给可用的线路排序，不代表准确率，也不对客展示。

原则：原文没写的字段留空或为 `unknown`。页面上，"原文未说明"只在审核视图里显示。

## 问题码

| 代码 | 含义 |
|---|---|
| `NAME_NOT_IN_SOURCE` | 节点名称在原文中找不到，已删除 |
| `NO_EVIDENCE` | 字段没有有效引用，已删除 |
| `NUMBER_NOT_IN_SOURCE` | 字段中的数字不在引用原文里：节点字段清空，线路级条目（含价格）删除 |
| `ATTRIBUTE_UNSUPPORTED` | 游览方式、门票、包含状态或"特别安排"缺少原文依据，已改为未说明 |
| `TYPE_UNSUPPORTED` | "自费""购物"节点类型缺少原文依据，已改为一般节点 |
| `TYPE_CHECK` | 一般节点名称附近提到自费或另付，请核对类型 |
| `TYPE_FROM_SECTION` | 节点位于"自费推荐行程："这类标题下，已改为自费项目，请核对 |
| `TEXT_NOT_IN_SOURCE` | 条款、提示、备注、替代安排、标题、国家等文字在原文中找不到，已删除 |
| `NO_DAY_HEADERS` | 没有识别出天数标题，页面只显示原文 |
| `PDF_PAGE_REORDERED` | 表格被读乱的页（天标签不递增），已按版式重读 |
| `PDF_PAGE_UNORDERED` | 版式重读后顺序仍然错乱，这一页改用图片转写 |
| `DAYS_SPLIT_BY_MODEL` | 规则找不到天数标签（标签是图形、竖排，或只有日期行），由模型按内容分天 |
| `DAY_SPLIT_REJECTED` | 模型给出的分天结果不是完整的 1..N 递增序列，或与登记天数不符，未采用 |
| `DAY_SPLIT_UNCHECKED` | 分天调用失败，沿用规则结果 |
| `DAY_SEQUENCE_GAP` | 天数序号不连续 |
| `DAYS_DIFFER_FROM_LISTING` | 附件天数与业务系统登记天数不一致 |
| `IMAGE_PAGES_NOT_READ` | PDF 有图片页，其中文字未读取（未用 `--vision`、超过 30 张上限或渲染失败） |
| `PICTURES_NOT_READ` | DOCX 里可能有文字的图片未转写（未用 `--vision` 或超过上限） |
| `IMAGE_TEXT_TRANSCRIBED` | 已转写图片文字，发布前对照图片核对 |
| `PICTURE_NOT_READ` | 某张图片无法解码或转写调用失败 |
| `PDF_DAY_BOUNDARIES_ADJUSTED` | PDF 天数边界经内容复核后调整 |
| `PDF_DAY_BOUNDARIES_REJECTED` | 模型给出的天数边界不合格，沿用规则结果 |
| `PDF_DAY_BOUNDARIES_UNCHECKED` | 天数边界复核调用失败，沿用规则结果 |
| `LAST_DAY_TRIMMED` | 最后一天后面的条款已切出 |
| `PDF_TEXT_GARBLED`（质量提示） | 这些页的文字层乱码，已改用图片转写 |
| `LAST_DAY_UNCHECKED` | 最后一天结尾复核调用失败，未截断 |
| `ROUTE_MODEL_FAILED` | 封面条款调用失败，线路级字段为空 |
| `DAY_MODEL_FAILED` | 当天模型调用失败，页面只显示原文 |
| `DAY_EMPTY_RESULT` | 模型两次都返回空结果，页面只显示原文（空结果不缓存） |
| `NAME_APPROXIMATE` | 名称与原文写法接近但不完全一致，已保留并提示核对 |
| `AUTO_ATTACHED` | 涉及费用、条件或安全的原文没有被任何字段引用，已原样挂到当天提醒或温馨提示 |
| `RETRIED` | 当天首次结果问题较多，已带问题清单重试，保留问题较少的一次 |
| `RETRY_FAILED` | 重试调用失败，保留首次结果 |

## 读取失败

以下情况整条线路不生成详情页，索引页显示原因：

| 代码 | 含义 |
|---|---|
| `UNSUPPORTED_FORMAT` | 不是 PDF 或 DOCX |
| `FILE_UNREADABLE` | 文件无法读取 |
| `DOCX_UNREADABLE` | DOCX 已损坏 |
| `PDF_UNREADABLE` | `pdftotext` 读取失败 |
| `PDF_TOOLS_FAILED` | 缺少 Poppler，或 Poppler 超时 |
| `PDF_TEXT_GARBLED` | PDF 文字层乱码（字体映射错误）；不加 `--vision` 时拒绝读取，加 `--vision` 时这些页改为整页转写，并记入质量提示 |
| `PDF_TEXT_MISSING` | PDF 没有文字层（扫描件）；用 `--vision` 可以逐页转写 |
| `FAILED: <异常类型>` | 处理过程出错，旧的输出已删除 |

## 测试

```bash
python -m pytest tests -q
```

测试只用虚构样本，覆盖读取、图片占位与转写、切分、校验和渲染，不调用模型。
