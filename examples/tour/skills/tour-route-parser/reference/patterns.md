# 行程附件的常见写法与处理方式

以下写法来自对多类线路附件的抽样，涵盖欧洲跟团、北欧、英国半自由行、海岛度假、邮轮、南美探险和单地接产品。示例均为虚构改写。

| 写法 | 例子 | 处理 |
|---|---|---|
| 【】标注景点 | 外观【某大教堂】（约30分钟） | 景点节点，`visit_mode=outside`，时长照写 |
| 景点写在散文里 | 早上乘园区巴士上山，进入古城遗址…… | 规则抽不到，由模型抽取，名称必须能在原文中找到 |
| 观光段 | 【某市 市区观光】（总观光时间不少于1小时）：【A】【B（外观）】 | `group` 节点，时长属于整段，A、B 为子节点 |
| 入内与门票 | 【某宫 含门票含人工讲解+优速通】 | `visit_mode=inside`，`ticket=included`，`includes` 列出讲解、优速通 |
| 外观 | 【某剧院（外观）】 | `outside`；门票状态保持未说明 |
| 途经、车游、远眺 | 车游某大道，途经【A】【B】；远眺【某岛】 | `drive_by`、`passing`、`distant_view` |
| 特别安排、升级 | 特别安排❀【某瀑布】 | `highlight=true`，仍是行程包含 |
| 赠送 | 赠送体验海边火车 | `inclusion=gift` |
| 条件替代 | 如遇预约已满，则改为游览【某山】 | `alternative` |
| 不保证 | 观鲸受自然因素影响，无法确保一定观测到 | `disclaimer`，reason 为 wildlife |
| 闭馆、营业日 | 某宫每周一闭馆；直升机周二至周四营业 | `disclaimer`，reason 为 closure 或 operating_days |
| 小费 | 船游结束需付船夫小费约 300 当地币 | `extra_cost_note` |
| 自费项目 | 晚上可自愿参加【某表演】（约60欧元/人） | `optional`，`price_text` 照写 |
| 自费套餐 | 推荐自费套餐：A+B+C+午餐 120 美金/人（10 人成团） | `package`，项目为子节点，`min_participants` |
| 推荐自行前往 | 全天自由活动。推荐：【某海滩】【某小镇】 | `recommend`，`inclusion=recommended_not_included` |
| 购物 | 【某皮具中心】（购物店，停留约60分钟）；某奥特莱斯（自由活动不少于2小时） | `shopping`，区分指定购物店、购物村、百货、市场；不写产品宣传 |
| 拍照点 | 打卡机位3---【某广场】某电影取景地（打卡时间约10-15分钟） | `photo_spot`，`spot_label` 保留机位编号，描述写拍摄提示 |
| 当天服务范围 | 注：当天不含车、司机、导游 | 当天的 `services` |
| 邮轮 | 邮轮预计到港 08:00，离港 19:00，须提前返船 | `day_kind=port_call`，`port_call` |
| 多天一条 | 第23–26天 南极半岛巡游（不登岛） | `day_end`，`day_kind=cruising_no_landing` |
| 交通 | 参考航班：XX123 01:45-07:15；A-300KM-B | `transport` 节点，航班号、时间、里程照写，参考航班标注 |
| 概览表加详细行程 | 先有 DAY-1…DAY-12 概览表，再有逐日详情 | 详情作为逐日行程，概览行作为补充依据 |
| PDF 天数标签在左栏中部 | "第 02 天"出现在当天内容中间 | PDF 天数边界复核 |
| 三餐写法 | 早餐:含 午餐:升级 晚餐:×；早X晚；餐:午、晚 | `meals`：included、self、not_applicable、unknown |
| 住宿写法 | 住宿：某地或周边；全程网评 5 钻；A / B / C | `stay`：names 分开，grade_text，or_similar |
