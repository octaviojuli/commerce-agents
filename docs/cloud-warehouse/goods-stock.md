# 团期列表测试源

`goods_stock.py` 读取 POST `/goods_stock/index`，参数放在 Query String；每页 100 条，
校验总数、唯一 ID、页长、日期、投放平台和首尾扫描锚点。上游没有快照游标，
这些检查不能证明分页期间完全无并发变化。出现不一致保留旧目录并报告失败。

`goods_stock_sync.py` 将一个查询平台下的团期按上游 `company_id` 拆分到独立供应商，
产品和团期使用连接范围内的稳定 ID。首次开通须由离线操作员明确执行；自动同步遇到
未开通的新供应商停止并报告，不能自动扩大发布范围。同步身份只有 sync_worker，
不创建人类登录、分销授权或订单权限。平台销售模式按现有权限开放目录。

库存 `stock` 按确认的余位使用，不减 reserve_stock 或 used_stock，不创建本地库存池。
价格统一 CNY，成人/儿童销售价和结算价分开保存。报价通过当前顾问权限读取五分钟内的
来源快照，供应商、连接、查询平台和窗口均须匹配；过期或撤回后拒绝核价。列表价格
缺少完整收费条件，`fees_complete=false`，零价按未知处理，不能生成完整成交报价。
报价读取不会重新扫描上游。实际刷新时间沿用扫描开始时间，不在入库时延长。

附件保留产品与团期各自的原始相对路径；未配置并验证资源域名前，不猜测地址、不自动
下载或发布。操作员联系资料和财务流水字段不进入目录快照。现有来源、行程审核、
订单禁用、顾问权限以及历史报价不被本接入改变。不同来源之间不自动合并同名供应商。

## 配置

私有 JSON 配置包含 `feed_id`（独立 UUID）、`base_url`（域名或含 `/api` 的基地址）、
`query_company_id`、`start_date`、`end_date`、`env_prefix`。窗口最多 366 天；初始测试
应使用明确的小窗口。`provision` 写入固定 `approved_suppliers` 和注册表增量文件。
保留 feed_id；更换会建立不同来源。

小规模测试目录通过可选 `selection` 固定供应商、产品和团期 ID：每项包含
`supplier_company_id`、`goods_id`、`departure_ids`，总计最多 100 个产品、400 个团期。
空列表和重复 ID 是配置错误，不解释为全量同步。同步仍扫描完整日期窗口，然后仅保留
所选 ID；新增产品或团期不会自动加入。所选团期消失时按旧数据过期处理，所选团期
换了供应商或产品则停止同步，不能静默重新归属。可选取跨月且覆盖不同日期的小样本，
避免只用单个节假日窗口导致很快无法测试；样本窗口到期前应由操作员重新选取未来团期。

```bash
python cloud-warehouse/scripts/goods_stock_sync.py inspect --config /private/feed.json --state /private/state
# WAREHOUSE_ADMIN_URL 仅离线开通进程使用。
python cloud-warehouse/scripts/goods_stock_sync.py provision --config /private/feed.json --state /private/state
# WAREHOUSE_DATABASE_URL 必须是受限 runtime 角色。
python cloud-warehouse/scripts/goods_stock_sync.py sync --config /private/feed.json --state /private/state
python cloud-warehouse/scripts/goods_stock_sync.py worker --config /private/feed.json --state /private/state --interval 180
```

worker 使用文件锁防止同目录并发扫描，保存重试截止时间并尊重 Retry-After；不可重试
错误或五次连续来源错误停止。进程管理器不应自动重置失败次数。没有成功同步时，
库存和参考价在五分钟后自然过期。一次平台扫描后逐供应商原子发布，跨供应商不共享
单个事务；部分发布失败必须报告，不能声称整个平台已同步。搜索投影随供应商目录
事务更新。未返回的旧团期保留历史，但余位立即过期，不作为当前可售承诺。

将生成的 `registry-additions.json` 合并到已有连接注册表，保留原有条目。
新增条目指定 `adapter=goods_stock`、`supplier_company_id` 和 `env_prefix`。
API 环境提供该前缀的 `BASE_URL`、`QUERY_COMPANY_ID`、`START_DATE`、`END_DATE`；
无需上游用户名、密码或 token。更新须同时保留旧 API 镜像和原配置以便回退。
关闭测试源应停用新增连接和本同步进程，不删除顾问会话或历史数据。
