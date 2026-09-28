# Tour 云仓后端

顾问报价单与价格核实采用独立时效：`WAREHOUSE_ADVISOR_QUOTE_HOURS` 默认 24 小时，控制分享、确认与线下成交登记；五分钟价格新鲜度只描述即时核价和库存可信度。`copilot_dependencies.py` 按需求值、所选对象和报价条件判定失效，按钮操作不增加需求版本。

多供应商、多采购组织的旅游产品与库存服务。PostgreSQL 保存组织授权、产品、团期、来源快照、审批和库存流水；FastAPI 提供带组织上下文的接口。顾问占位、下单、支付接口关闭。

平台销售模式将公共货架与个人业务记录分离：获准登录的顾问可查询所有有效供应商的上线产品，不要求旅行社归属或逐个分销授权。所有顾问使用统一同行结算价，客户映射、等级和协议价不参与平台模式报价。API 来源须配置统一价格身份，Excel 来源采用标准价表；缺失时返回待核实。`platform_sales.py` 提供离线开通独立顾问及模式切换；`travel_search.py` 支持目的地分词和常用简称。技术工作空间只承担记录归属，报价与幂等请求按操作人隔离。见[平台销售模式](../docs/cloud-warehouse/platform-sales.md)。

当前可运行目录同步、平台登录、组织隔离、Excel 校验与审批发布、供应商库存登记、采购客户映射、协议价和报价快照。`WarehouseMerchantBackend` 和 `WarehouseAdvisorBackend` 复用原仓库两种角色接口，聊天使用原运行时并保存至 PostgreSQL。业务工作台已接通主要运营流程，原 Tour 顾问前端已有可选择的云仓模式；历史迁移与生产切换尚未完成，见 [实施状态](../docs/cloud-warehouse/implementation-status.md)。真实 B2B 数据保存在忽略提交的本地 `.warehouse` 环境，与演示测试数据分离。

B2B 返回 `routeId=0` 时，团期进入来源异常隔离。已发布过的同一团期也立即对采购方隐藏，关联方案、旧报价与客户分享沿用实时权限检查；供应商仍可读取异常记录和原始快照。隔离不删除历史、不补造线路、不修改本地停售设置。只有后续成功同步恢复有效线路关联，才清除隔离，继续使用原团期及方案编号；旧报价仍需重新查询。`0027_departure_quarantine` 会根据最近成功来源快照修复升级前遗留的异常记录。

`0029_quarantine_scope` 保留异常团期隔离，同时恢复每条查询一次计算有效采购授权的读取策略，并应用到 Offer 和库存的嵌套读取。它只替换读策略，不修改数据；新语句仍重新检查成员、组织、来源和分销授权，不使用跨请求权限缓存。

`0030_pricing_scope` 将同样的语句级组织校验应用到来源和分销授权读取，价表读取与调用者权限的实时授权函数复用采购授权集合。协议价、等级价的适用对象与有效授权检查不变，不修改写权限或业务记录。

`0031_authorization_plans` 让既有组织、角色、采购连接与有效授权函数复用 PL/pgSQL 执行计划，保留原函数权限和事务内身份。执行计划复用不缓存查询结果；成员、来源、供采关系和有效期仍按每条语句的快照检查。

供应方通过 `GET /v1/source-issues/{issue_id}` 核对异常原因与最近成功批次，接口只返回定位计划所需的团号、日期、线路编号和批次信息，不返回完整来源正文、价格或附件链接。最新批次未返回该团期时保持“未观察到”，新的失败或执行中批次单独呈现；历史异常不会因上游修复而被删除。商户数据同步页提供对应详情及操作指引。

## 本地运行

生产 API 与目录 Worker 共用 Tour `warehouse_connector.connector_registry` 的连接 UUID/凭据前缀绑定。`warehouse sync` 与 `warehouse worker` 支持 `--connections` 或 `WAREHOUSE_CONNECTORS_CONFIG`，单源 Worker 仅加载所选连接的凭据；绑定无效时拒绝启动，不回退到通用账号。没有配置连接表的本地单源命令保留原用法。配置迁移与权限见[部署说明](deploy/README.md)。

本地 `scripts/local_warehouse.py` 的 API、同步及 Worker 命令共用 `.env` 与进程环境的读取方式，进程环境优先。设置 `WAREHOUSE_CONNECTORS_CONFIG` 时使用该显式路径；否则，存在 `.warehouse/connections.json` 时读取本地连接表，仅选择 `b2b-context.json` 指定的连接。连接表可沿用 `TOUR_ERP` 前缀而不复制密码，文件应为 0600。已存在但无效的连接表不会降级到旧方式；两种连接表都未配置时才保留原单源配置。更新文件后需在同步空闲时重启 API 与目录 Worker，并核对新成功批次；常驻进程不会热加载凭据。

仓库虚拟环境安装根目录 `requirements.txt`。本机需 PostgreSQL 16+ 的 `initdb`、`pg_ctl`；辅助脚本创建独立实例，不改动已有 PostgreSQL 服务。

```bash
.venv/bin/python cloud-warehouse/scripts/local_postgres.py
.venv/bin/python cloud-warehouse/scripts/local_warehouse.py migrate
.venv/bin/python cloud-warehouse/scripts/local_warehouse.py test
.venv/bin/python cloud-warehouse/scripts/local_warehouse.py serve
```

实例仅监听 `127.0.0.1:55432`。`.warehouse/database.json` 保存模式 0600 的随机数据库凭据。`warehouse` 为应用库，`warehouse_test` 为隔离测试库。迁移、认证、业务分别使用不同数据库角色；业务账号不是表所有者，也没有超级用户或 BYPASSRLS 权限。测试不自动访问真实 B2B。

## 二期订单契约预留

`order_contracts.py` 定义冻结的数据契约，不注册路由、模型工具、Worker 或数据库迁移，不执行占位、下单、支付及状态转换。内容涵盖服务端解析的组织范围与库存权威、报价关联、名额规则版本、本地幂等身份、占位及订单快照、上游候补/未知结果、回查证据和已有销售流水关联。候补不作为已确认有位；上游写入结果未知时先回查，零匹配也不自动重发；已有销售流水关联订单的额外已售量只能为零。

决策、替代方案及二期实施前提见 [ADR-004](../docs/cloud-warehouse/adr-004-order-boundary.md)，字段见 [契约包](../docs/cloud-warehouse/order-contracts.json)。JSON Schema 描述字段和基本约束，跨字段规则仍由 Python 模型校验；模型校验不替代真实成员权限、数据库外键、供应商签认或事务一致性。

```bash
.venv/bin/python cloud-warehouse/scripts/export_order_contracts.py         # 更新审阅文件
.venv/bin/python cloud-warehouse/scripts/export_order_contracts.py --check # 检查代码与文件一致
.venv/bin/pytest cloud-warehouse/tests/test_order_contracts.py -q
```

契约文件一致性与领域规则纳入普通测试，HTTP 关闭、库存不变和零上游调用纳入独立数据库 CI。一期继续强制 `held=0`，兼容交易接口返回 `TRANSACTIONS_DISABLED`；供应商声明交易能力也不构成开启交易的授权。

## 商户业务页面读取

`route_facts.py` 将规则可识别的原文对象与数值、费用和限制条件对应核对，补充既有语义核对；支持有限同义表达并保留证据。`daily-editor-2` 使用独立候选版本，不覆盖旧候选或人工稿。商户 `route-fact-review.tsx` 展示未采用改写的具体差异，可定位原稿日期。验收场景位于 `tests/test_route_facts.py`；规则不能判断全部自然语言事实，发布仍需人工审批。

`merchant_business.py` 提供组织内的线路与团期专用列表、筛选、分页和详情；参考价、库存观察、销售限制及文档版本分别保留来源含义。商户界面复用原审批、行程复核、报价方案和销售规则服务。

`merchant_source_reads.py` 通过绑定的连接器提供团期库存与市场价即时观察，以及供应商管理员的已有订单实时只读查询。运维配置的 `order_read_departments` 与上游当前可用部门取交集；调用前后复核权限和连接配置。订单读取遍历完整分页，达到上限、变化或总预算超时均报错；不存储订单观察历史、不执行交易、不扣库存，不反算缺失单价或推断结算方式。

`catalog.enforce_selection` 在同步事务内执行运维配置的 `catalog_selection` 来源 ID 清单。范围外记录下架并递增版本，现有记录、原始观察和历史关联保留。商户业务页面和助手目录使用相同清单，采购读取沿用发布状态和实时 RLS；所选记录不会因同步自动恢复被人工关闭的状态。

## 持续集成

`.github/workflows/ci.yml` 的 `warehouse-postgres` 任务为 Python 3.11 / 3.12 分别启动临时 PostgreSQL 16 服务，安装匹配的数据库客户端及 Poppler。流程使用 GitHub 的[独立服务容器](https://docs.github.com/en/actions/tutorials/use-containerized-services/create-postgresql-service-containers)，不使用真实 B2B、模型或部署凭据。普通 `pytest` 无测试数据库时仍会跳过集成用例，不能替代本任务。

`scripts/ci_database.py` 仅接受显式 `WAREHOUSE_CI_ADMIN_URL`，目标必须是回环地址的 PostgreSQL `postgres` 维护库；不会自动读取 `.warehouse` 配置。每次创建随机名称的 `_test` 数据库，以及不同的 runtime/auth 受限登录角色。迁移由管理员完成，业务与认证使用对应角色测试。执行云仓全套及 Tour 组合适配器用例后检查 JUnit 结果，空报告、失败或任何跳过均不通过。

```bash
# 在专用测试集群上提供 WAREHOUSE_CI_ADMIN_URL 后执行；不要填应用数据库 URL。
python cloud-warehouse/scripts/ci_database.py
```

脚本先要求 `pg_dump`、`pg_restore`、`pdftotext` 可用，子进程去除继承的 B2B 与模型配置。成功或测试失败时只清理本次创建的数据库和角色；进程被强制终止时，由 CI 作业结束销毁整个临时服务。本地运行需要独立测试集群及创建数据库/角色权限。GitHub 作业是否实际通过以 Actions 运行结果为准，本地脚本通过不等于远端环境验收。

`tests/test_worker_recovery.py` 在独立操作系统进程中执行目录 Worker，分别在事务提交前和提交后强制终止。测试保留默认 60 秒租约并等待自然到期，检查回滚、接管、防重复发布、审计/Outbox 和采购读取；因此完整数据库套件增加约一分钟。ECS 相同镜像的隔离结果见 [Worker 恢复验收](../docs/cloud-warehouse/worker-recovery-acceptance.json)，不能替代整机或网络故障演练。

镜像级验收由 `scripts/container_smoke.py` 和 CI 的 `warehouse-images` 负责，入口见[独立镜像验收](deploy/README.md#独立镜像验收)。它启动虚构数据库、API 和两个网页，验证组织权限、私有附件和重启后的会话保留；本地单元测试通过不代表 Linux 镜像已经运行，目标环境状态见实施状态文档。

## 容量验证

容量脚本 `scripts/capacity.py` 使用与 CI 相同的一次性数据库生命周期。显式设置 `WAREHOUSE_CAPACITY_ADMIN_URL` 为专用回环测试集群的 `postgres` 维护库，脚本不会自动读取应用配置或访问 B2B。`smoke` 验证调用链；`baseline` 创建 50 家虚构供应商、100 家采购组织、1 万条产品和 10 万条团期，以 100 个并发任务发起 400 次混合调用。每位采购方执行目录、团期、托管库存、托管报价各一次。报价使用独立幂等键并写入快照，检查库存余额和版本保持不变。失败和超时进入报告，不从延迟样本剔除；未达目标返回非零状态。

`--transport service` 为直接业务调用；`--transport http` 启动独立回环端口上的 Uvicorn，通过 TCP 请求相同业务。后者为测试身份预置随机会话摘要，每次 HTTP 请求仍执行实际认证和业务权限查询；不把密码登录计入负载。客户端线程与单个服务器事件循环处于同一 Python 进程，报告明确此拓扑，不能推断独立压测机、TLS 网关或 ECS 表现。基线开始前验证匿名 401 与伪造组织 403，正常结束或测量异常后关闭监听并清理测试资源。

HTTP 默认 `--http-server-mode thread --http-client-mode shared` 保留原测量方式；使用 `--http-server-mode process --http-client-mode per-thread` 可拆分服务器解释器与客户端连接池。`capacity_http.py` 子进程只接收父进程通过管道传入的测试数据库配置及已绑定回环地址的 socket，拒绝应用库、远程数据库或两角色指向不同库。每个负载线程持有一条 HTTP 连接，另有一条用于预检/预热，测量期间仍最多 100 个并发请求；客户端对象在请求计时前初始化，实际连接建立和响应读取仍计时。同一台机器仍共享 CPU、数据库与网络栈，不能替代目标服务器验收。

独立进程模式可用 `--http-server-processes 1|2|4|8` 对照多个 API 进程，默认仍为 1。所有进程共享父进程绑定的回环监听；业务与认证请求连接池各自合计上限始终为 8，按进程均分，不随进程数增加。夹具管理连接不属于请求池。父进程等待每个子进程确认启动后才测量，报告记录各操作由各进程处理的请求数；任何子进程退出都拒绝成功，部分启动失败和测量异常也清理全部子进程、监听与测试数据库。该选项只改变测试拓扑，不设置生产 Worker 数量。

```bash
python cloud-warehouse/scripts/capacity.py --profile smoke --output /tmp/warehouse-smoke.json
python cloud-warehouse/scripts/capacity.py --profile baseline --output /tmp/warehouse-baseline.json
python cloud-warehouse/scripts/capacity.py --profile extended --output /tmp/warehouse-extended.json
```

输出路径必须不存在，便于保留失败证据。测量包含受限身份的 RLS、连接池等待与报价快照写入，不含 HTTP、认证、模型或外部 API；不等同于 ECS 或持续负载验收。实测及剩余目标见 [容量验证](../docs/cloud-warehouse/capacity-report.md)。

`scripts/capacity_import.py --rows 5000 --output /tmp/warehouse-import.json` 复用同一显式测试维护库配置，生成虚构 Excel 并通过独立进程的回环 HTTP 验证上传、预览、夹具人工审批、发布与重放。每个库存池均与期初流水核对；草稿不发布，匿名和采购方不能上传。报告分别记录含解析和持久化的上传耗时、客户端单独解析耗时，以及预览和发布耗时。上传目标 2 秒，解析目标 60 秒；单次文件测量不是分位数，也不覆盖公网网关、真实审批或并发上传。正常及异常退出均清理临时 HTTP 服务、数据库与角色。

`extended` 保持基线数据规模与 100 并发，执行 20,000 次混合调用，每位采购组织执行四类调用各 50 次，创建 5,001 份独立报价（含预热）。它延长同负载验证，不增加授权范围或调用真实来源。

`database_pool.py` 采用 Psycopg 官方连接池及 SQLAlchemy `NullPool`，每个 Engine 最多 8 个连接、256 个排队申请、单次连接获取最多等待 30 秒。连接首次使用时建立，归还后优先交给等待者；取用前检查连接健康。`engine.dispose()` 关闭所属池，之后复用 Engine 会重新建立池；应用必须在各 Worker 进程内创建 Engine。连接数按进程和角色分别累计，不能把单个池上限当成整个服务的总上限。设计及验证见 [连接池决策](../docs/cloud-warehouse/adr-007-database-pool.md)。

## 备份与运行配置

`recovery.py` 与 `warehouse backup / verify-backup / restore / verify-restore` 提供数据库和私有附件成套备份、完整性检查及空库恢复。导出和核对清单使用同一 PostgreSQL 快照；恢复验证各表内容、附件、库存流水重算及强制 RLS。恢复不会切换 API 或启动 Worker，目标库保持业务连接关闭。操作步骤、角色重建、上线前会话处理及备份范围见[恢复手册](../docs/cloud-warehouse/recovery-runbook.md)。

`backup_jobs.py` 与 `warehouse backup-cycle / backup-status` 提供可调度的运维备份入口和持久状态。独立私有根目录绑定源数据库与对象目录；进程锁防止并发，显式空间预算先行检查，失败保留上次成功快照，不自动删除历史包。状态按快照时间判断新鲜度，并用实际文件锁识别运行或中断。备份和对象清理互斥，普通文档写入仍可继续。`deploy/systemd/` 提供未启用的调度模板；实际保存位置、保留期、异地副本和恢复目标须按恢复手册配置。

部署环境通过环境变量提供 `WAREHOUSE_ADMIN_URL`、`WAREHOUSE_DATABASE_URL`、`WAREHOUSE_AUTH_URL`。URL 使用 `postgresql+psycopg` 驱动。数据库角色由受信任的运维流程创建，再执行迁移及 `admin.grant_runtime` / `admin.grant_auth`；运行服务不得携带迁移账号。

`object_gc.py` 与 `warehouse collect-objects` 检查并清理写入失败留下的本地孤立文件。默认仅生成私有检查日志；显式 `--apply` 才删除超过保留期且没有任何 `document_asset` 记录的对象及临时文件。所有历史、失败解析、未发布的文档记录均保留。数据库不匹配、受限角色、引用对象缺失或其他写入正在进行时拒绝清理；符号链接、未知文件名及近期文件不删除。使用步骤及并发边界见 [对象清理手册](../docs/cloud-warehouse/object-cleanup-runbook.md)。

[独立部署包](deploy/README.md) 提供 API、两个 standalone 网页、目录/检索/附件/解析 Worker、可选 PostgreSQL、分进程配置模板及启动与回退步骤。生产 Tour 装配入口必须提供 `WAREHOUSE_OBJECT_ROOT`，与附件 Worker 共享私有目录；缺失该配置会在启动时失败。镜像构建与 ECS 切换仍需在选定目标完成验收。

```bash
warehouse migrate
warehouse grant-runtime warehouse_runtime
warehouse grant-auth warehouse_auth
uvicorn cloud_warehouse.api:application --factory --host 127.0.0.1 --port 8005 --no-access-log
```

人类账号由离线管理员调用 `auth.create_user` 创建，并明确指定组织和角色。登录不会自动加入组织，也不会复用 ERP 密码。浏览器集成需在 HTTPS 下使用登录返回的短期 Bearer token；切换组织时传入 `X-Organization-Id`。本地业务工作台入口为 `http://localhost:3104`，启动方式见 `examples/tour/merchant-web/README.md`。

`serve` 复用已初始化的本地 B2B 连接和授权 `.env`，监听 `127.0.0.1:8005`；不启动旧 ERP 应用。它不创建或模拟人类登录账号。配置模型凭据时启用聊天，否则聊天返回 503，目录和报价接口仍可用。本地端口不代表 ECS 已上线。

## HTTP 并发与中断

顾问产品与团期分页接口使用 `AdvisorProductPage` / `AdvisorDeparturePage` 响应模型，复用原 `Product` 契约。FastAPI 对已验证结果直接生成 JSON，避免逐字段递归转换后再编码。未知价格及库存仍为 `null`，空值、变体关系和分页游标保留；额外页面字段需明确加入契约，不能静默丢弃。性能结论以实际 HTTP 容量报告为准。

顾问目录与团期、基础团期查询只选用实际提供的筛选条件，SQL 片段固定、输入值保持绑定，便于复用不同查询形态的执行计划。无关键词时目录不读取检索副本；有关键词时继续验证副本版本并回查缺失或过期投影。来源内候选筛选先于全局分页，日期包含两端边界，所有读取保留实时 RLS 和既有排序。

HTTP 身份入口使用独立认证角色，在同一条实时查询中检查会话、用户、组织和成员状态。入口不为重复验证组织单独占用业务连接池；后续每段业务事务仍设置事务内组织上下文并重新检查成员及行级权限。入口认证后撤销成员，也不能继续读取业务数据。没有跨请求权限缓存。

`persistence.transaction` 通过 `0024_transaction_context` 的受限函数，一次调用顺序设置事务身份并校验当前成员。函数按调用者权限执行，身份保留到外层事务结束，提交或回滚后清除；业务 RLS 和动作角色检查继续执行。部署时先迁移并执行运行角色授权，再重启应用；参见 [ADR-008](../docs/cloud-warehouse/adr-008-transaction-context.md)。

`concurrency.py` 将顾问、商户异步协议中的同步业务操作交给 Starlette/AnyIO 工作线程。每段数据库事务在同一线程完整执行；报价和客户核验在上游异步请求前后分别执行事务，不跨网络等待持有连接。聊天的身份复核、会话保存和审批状态读取也离开事件循环；客户端断开后，会话中断记录与助手连接关闭在受保护的清理范围内完成。

请求取消不等于工作线程中的事务回滚。报价重试须复用原幂等键，审批变更须查询原变更状态，不能因为响应丢失而重新创建业务命令。`test_async_responsiveness.py` 使用真实 ASGI 请求及隔离数据库验证慢查询时健康检查可响应、业务 SQL 不在事件循环线程、上游/助手仍异步运行、聊天取消后立即重试，以及报价响应取消后的单份快照恢复。该验证不代替 TCP 或生产负载验收。

## 当前 B2B 同步

组合适配器位于 `examples/tour/api/warehouse_connector.py`，复用已有登录与 token 刷新。同步只允许线路、团期 GET 请求，不调用上游创建订单。

```bash
.venv/bin/python cloud-warehouse/scripts/local_warehouse.py onboard
.venv/bin/python cloud-warehouse/scripts/local_warehouse.py sync
.venv/bin/python cloud-warehouse/scripts/local_warehouse.py status
```

供应商简称由平台运维设置，最多 12 个字，留空则用全称：

```bash
.venv/bin/python -m cloud_warehouse.cli supplier-short-name --organization <供应组织 ID> --name <简称>
```

顾问接口在产品和团期的 `attributes.supplier_name` 返回简称。名称经 `warehouse_supplier_name` 读取，对供应商本身及有实时目录权限的顾问可见：平台销售模式支持无分销授权的独立顾问，分销模式仍检查有效授权。供应商、连接及顾问身份失效后，目录读取方也不能继续读名称；`organization` 表不对运行角色开放。

`0047_supplier_name_scope` 更新已部署的名称函数。已执行 `0046` 的环境也须迁移到最新版本；函数保留既有运行角色执行权限，按正常升级流程停止 API 和写入进程、迁移并刷新运行角色授权后再启动服务。

`onboard` 只为当前授权源建立一个供应组织和一个明确授权的内部采购测试组织；不代表其他采购方自动取得相同客户价或授权。`sync` 读取已授权的 `examples/tour/.env`。新供应商必须单独配置组织、连接、凭据绑定和分销授权。

读取全部分页并校验数量、重复编号和首尾锚点后，单事务发布。接口若返回 `pageNum`，首个响应、后续分页及复查首页均必须与请求页码一致；错页即失败，即使总数和内容哈希相同也不接受。接口没有上游一致性快照令牌，因此不能证明所有页来自同一上游时刻；本地发布是原子的。重复同步保持全局 UUID，失败保留已发布目录，缺失团期仅使其可用量过期。库存缺失或过期返回未知，不返回零。`routeId=0` 的团期保留来源快照并进入供应商复核清单，不虚构产品关联。

## 后台同步任务

`jobs.py` 以 PostgreSQL `sync_job` 保存每连接的周期、尝试次数、下次执行时间、错误码和最近成功批次。独立进程使用受限业务数据库角色及 `sync_worker` 身份；不得用这个身份批准价格、库存或客户映射。

```bash
# 一次认领到期任务，适合部署前核验；没有到期任务时返回 null。
.venv/bin/python cloud-warehouse/scripts/local_warehouse.py worker-once
# 持续处理当前本地授权源，停止进程即停止执行，不创建系统定时器。
.venv/bin/python cloud-warehouse/scripts/local_warehouse.py worker
# 修复失败原因后，明确重新启用达到重试上限的任务。
.venv/bin/python cloud-warehouse/scripts/local_warehouse.py retry-sync
```

新任务默认在成功完成后 180 秒再次读取，租约 60 秒、每 15 秒续期、单次扫描上限 900 秒。库存快照仍从抓取时刻起五分钟过期，不以发布时间重新计算。三分钟间隔为前后两次抓取和发布留出余量；扫描较慢、限流或失败时仍可能过期，周期同步不等于实时库存承诺。失败采用指数退避与抖动，默认连续 5 次失败后停止该任务；重启不会清零次数。同一连接仍受数据库会话锁保护，锁冲突延后执行而不消耗失败次数。认领使用 `SKIP LOCKED`；租约过期后可重新认领，旧持有者不能发布。任务成功、目录、审计与 Outbox 在同一事务提交；超时和取消清理运行记录。

通用部署使用 `warehouse worker --connection UUID --organization UUID --user UUID --interval 180`，加 `--once` 仅执行一次；环境提供 `WAREHOUSE_DATABASE_URL` 和该进程对应的供应源配置。一个进程绑定一个供应源，避免共享任意供应商凭据；多供应源由运维分别配置进程。启动参数只作为新任务默认值，已有任务沿用数据库配置；不同 Worker 身份不能接管任务。

运营监测按“五分钟有效期 − 配置间隔 − 两倍最近成功扫描耗时 − 五秒轮询余量”估算刷新余量，并返回最近扫描秒数、有效期和估算余量。余量非正时提示 `SYNC_FRESHNESS_GAP_RISK`，暂停或停用来源不重复报警。该估算不承诺下一次耗时；已有任务由供应商管理员结合上游配额调整，不由 Worker 重启自动覆盖，也不能通过延长快照 TTL 消除提示。

B2B 适配器将登录失败、权限不足、找不到接口/记录、请求拒绝、限流与临时不可用转换为固定错误码，不保存上游错误正文。登录、权限及确定性拒绝停止自动重试，需修复后人工重新排队；瞬时失败仍使用有限指数退避。`Retry-After` 支持秒数和 HTTP 日期，语义依据 [RFC 9110 §10.2.3](https://www.rfc-editor.org/rfc/rfc9110.html#name-retry-after)；429 至少冷却 60 秒，超过七天的有效等待提示停止自动重试并要求人工核对，不截短后提前执行。无效提示使用默认退避。

调度器将等待截止写入 `sync_job.provider_not_before`，计划时间取退避与上游冷却的较晚者。数据库触发器阻止提前开始及缩短尚有效的冷却，人工排队、失败重试、修改周期和进程重启仍遵守该时间；直接目录同步也检查已有冷却。成功后清除冷却，失败批次保留旧目录。API 的来源错误返回 `retryable`；含等待提示时返回 503 与 `Retry-After`，其余来源错误返回 502。此字段覆盖目录任务调度；跨进程、报价与客户核验共用的等待由下文 `source_limits.py` 负责。账户级主动并发限额与调用速率仍需依据供应商确认的配额接入。

`sync_management.py` 提供任务、批次及异常的完整游标分页。供应商管理员可在工作台修改间隔（60–86400 秒）、尝试上限（1–10 次）、暂停/启用、立即排队及失败重试；审计角色只能查询。`POST /v1/sync-jobs/{id}/control` 要求当前配置版本、操作类型及必填说明，每次成功操作递增版本并保存审计，重复提交旧版本返回 409。网页不会创建 Worker 或修改其身份、租约；立即同步仅代表入队，执行结果须查看任务状态和历史。

暂停后当前批次仍可完成，后续不再认领；执行期间只能调整开关，间隔与尝试上限需等批次完成。后台会清理暂停任务的过期租约，保留已消耗次数。工作台每 10 秒刷新任务并保留未提交表单，展示最近后台心跳；有历史心跳不保证进程此刻仍在线。Outbox 消费去重、监控告警、供应商账户级配额与生产进程托管仍待补齐。

## 历史顾问案例

`legacy_cases.py` 与离线 `warehouse import-legacy-case` 保存明确映射后的旧会话文本、不可覆盖的方案版本及旧分享哈希映射；`tour.api.warehouse_legacy` 只读导出原 SQLite。运行时按顾问、采购组织及导入时的授权版本读取，不恢复旧凭据或执行状态。命令、限制及待完成部分见[旧案例迁移手册](../docs/cloud-warehouse/legacy-case-migration.md)。

## Excel 与库存

[供应商团期导入模板](templates/供应商团期导入模板.xlsx) 含填写说明和虚构示例。首次交接明确总容量、已售、停售；已有团期更新时，期初列必须留空。供应商须及时把线下及其他渠道销售登记到云仓。

上传、校验、预览、审批、应用是不同步骤。文件及解析数据按组织保存在 PostgreSQL 私有表，单文件上限 10 MB、5000 行；对象存储迁移接口尚未实现。公式、宏、外链、重复业务键和不一致日期被拒绝。重新上传同文件不会再次扣减。

库存可用量为总量减已售与停售，一期占位量固定为零。销售、冲正、调整和导入使用同一持久化审批机制，审批绑定内容摘要与资源版本，五分钟内有效。应用时重新检查角色、内容和版本；余额、流水、审计、Outbox 同事务提交。总量不能减少到低于已售与停售，外部 B2B 库存不能被本地登记覆盖。

`GET /v1/inventory/{id}/movements?before=UUID&limit=50` 按时间、编号倒序返回 `items` 与 `next_cursor`，单页 1–100 条。游标必须属于当前供应组织的所选库存池，采购方不能查询供应商流水。后端关联完整账本返回 `reversed_by_id` 和 `reverses_business_key`，原记录与冲正记录分处不同页时仍能正确显示状态；界面状态不替代应用时的重复冲正检查。

## 报价与商户业务

`pricing.py` 保存按采购组织区分的协议价和客户映射。B2B 客户代码须在上游授权部门中唯一匹配，再经供应商审批、采购方接受，才能用于同业价查询。连接凭据由服务端注册表绑定；浏览器和模型不能提交上游客户 ID 或凭据。映射更换后采购方须重新接受。Excel 来源的完整协议价使用专用审批接口；商户助手只调整所选采购方已有成人同业价，遵守原商户调价幅度限制。

价格与映射预览绑定分销授权的编号和版本，应用时锁定并复查授权；撤销或变更授权后旧预览失效。升级前缺少授权快照的预览须重新生成。`price_books.py` 管理 Excel 来源的标准价、采购等级价、采购协议价和多有效期价表。报价按询价时刻选择一份完整价表，优先级为协议价、等级价、标准价；高层价表缺项不从低层拼凑。采购等级按供应连接分配，不跨供应商或连接共享；来源、分销授权、价表及等级变化会使旧报价失效。API 来源继续以上游报价为准，拒绝本地价表和等级覆盖。

`GET /v1/merchant/price-books?offer_id=UUID` 和 `GET /v1/merchant/buyer-grades?connection_id=UUID` 提供完整游标分页（默认 25、最多 100 条）；对应 `/proposals` 接口生成统一 `price_book` 审批。价表可由产品编辑提议，等级只能由供应商管理员提议，均由管理员核对后应用。不可改写的审批内容包含原值、完整新值、说明、供应源/方案/授权版本。应用重新锁定复核，价表、等级、审计和 Outbox 原子提交。已有价表的编号、层级和适用对象不可更换；停用保留历史。

有效期采用包含起点、不含终点的时间区间；界面明确使用北京时间。相同方案、同层级、同适用对象的启用价表不得重叠，数据库排斥约束防止并发绕过；相邻区间允许。报价新鲜度不超过当前价表截止和未来适用价表开始时刻。迁移 `0019_price_levels` 需要 PostgreSQL `btree_gist`，旧协议价记录保留原编号和内容；旧报价缺少价表编号与等级版本时须重新核价。响应增加 `price_layer`；兼容字段 `price_source=contract_price` 仍表示本地价表来源，不单独代表协议层级，旧错误码 `BUYER_CONTRACT_PRICE_MISSING` 表示没有有效适用价表。

`quotes.py` 使用 Decimal 计算市场价和同业价，保存数量、年龄、房型、费用条款、授权与产品版本及来源时间。缺价和未知费用使完整总价为空，不按零计算。报价不扣库存、不产生订单；过期快照保留历史价格但明确失效。客户展示使用字段白名单，包括嵌套人数与市场价明细，不含采购价、儿童年龄、库存或内部来源字段。

外部报价的连接上下文、登录/部门切换与价格读取共享 8 秒异步预算。超时保存 `SOURCE_PRICE_TIMEOUT` 的不完整查询快照，不采用迟到价格，也不生成预留；保存前仍复查当前授权和版本。同幂等键重放返回原快照，重新询价使用新键。调用方取消继续传播，不被转换为超时报价。预算不包含之前和之后的数据库处理，也不是整个 HTTP 请求的硬实时承诺；适配器须使用可取消的异步 IO 并在退出时关闭资源。

`source_limits.py` 与迁移 `0032_source_cooldowns` 将上游冷却状态保存在数据库。原同步任务的未到期等待与需人工复核状态迁入共享状态；升级时须刷新运行账号函数授权。顾问、供应商客户核验、目录 Worker、单次同步和本地审计读取通过同一组织/连接上下文。Tour 的每次 HTTP 尝试先检查状态，包括登录、部门切换及底层重试；普通 Tour ERP 客户端保持原行为，不导入云仓模块。

HTTP 429 或业务信封 429 至少冷却 60 秒，保留更长的 `Retry-After`；上游不可用响应携带有效 `Retry-After` 时同样共享等待，原故障分类保留。等待取更晚截止，成功、重启和人工重排任务不能清除它。超过七天的异常等待进入持久人工复核状态，不会七天后自动恢复；须由离线管理员联系供应商确认后修复。已观察到的冷却写入在调用方取消后仍等待完成，其数据库语句和锁等待有边界。数据库检查失败时当前请求不访问上游；这不保证数据库不可用期间未能持久化的响应可以跨主机恢复。

默认每个连接独立；同一上游账号的多个连接由离线管理员明确分组，不能靠采购方或模型输入推断。使用管理连接环境变量运行 `warehouse group-source-cooldowns --group UUID --connection UUID --connection UUID`；分组保留各连接及目标组最长冷却与复核状态，既有显式绑定不能改到另一组。运行与认证账号均不能读写分组表或直接清除冷却。分组只协调访问等待，不改变目录、客户或价格权限。

共享冷却不会主动取消已发出的请求，也未配置每秒次数、同时请求数或供应商账户配额；这些需依据供应商给定规则实现和验证。它不限制云仓之外的旧服务或其他客户端。该模块不存储 ERP 账号、密码、令牌或响应正文。

报价快照按全局报价编号，或当前采购组织内的幂等键精确读取；两种查询分别复用索引计划。组织来自事务身份，RLS 与保存前后版本复核保留，计划复用不缓存权限或价格。不同采购组织可以使用相同幂等键，不能读到对方的快照。

`quote_shares.py` 允许报价创建人在核对客户预览后生成市场报价分享。创建须为仍有效的报价，提交 UUID `request_id` 和 1–168 小时的查看期限（默认 72）；每份报价最多 10 个有效链接。并发同请求只生成一条记录，明文密钥仅首次返回；重试返回原记录和 `token=null`，遗失须撤销后重建。数据库只保存密钥摘要，撤销不能恢复，也不能延长期限。认证账号只能把密钥摘要解析为固定报价的所有者身份，业务账号随后重新检查成员、组织、分销授权和报价读取权限；公开请求不能自行指定组织或报价编号。

匿名读取使用固定 `POST /v1/public/quote` 路径和正文中的 `token`，不使用登录会话；无效、到期、撤销或已失去读取权限的链接统一返回 404，响应禁用缓存。链接查看期限与报价新鲜度独立：报价过期后仍可显示历史市场金额，但必须重新核价，不能据此确认库存。已打开网页在重新聚焦及每 30 秒复查；撤销阻止后续读取，不能收回已经保存的内容。

团期是否过期按供应源 `capabilities.business_timezone` 的 IANA 业务时区判断；未配置的国内来源默认 `Asia/Shanghai`，不依赖服务器或浏览器所在地。接入运维应在配置境外供应源时明确该字段，非法时区拒绝新报价，不回退成另一个日期。报价快照及客户字段白名单记录业务时区；时区改变使既有报价失效，未记录时区的旧快照须重新询价。报价有效期取价格、授权、五分钟新鲜度及团期当地日期结束时间的最早值，夏令时按日历计算。日期结束时间不是供应商报名截止时间。`sales.py` 统一目录、顾问、商户和报价中的本地停售、截止及过期判断；报价有效期还受云仓报名截止约束。顾问卡片标明团期业务时区，查询和有效期时间继续明确以北京时间展示。

API 报价服务使用 `examples/tour/api/warehouse_app.py` 装配。`WAREHOUSE_CONNECTORS_CONFIG` 指向受保护的 JSON 文件，内容为 `[{"connection_id":"UUID","env_prefix":"SUPPLIER_A"}]`；进程环境中的 `SUPPLIER_A_BASE_URL`、`SUPPLIER_A_MOBILE`、`SUPPLIER_A_PASSWORD` 绑定此连接。通用 `cloud_warehouse.api:application` 不自动注册任何供应商凭据。

```bash
uvicorn tour.api.warehouse_app:application --factory --app-dir examples --host 127.0.0.1 --port 8005 --no-access-log
```

`merchant.py` 实现原 `MerchantBackend`，主线路映射为商品族、团期映射为变体。后端实例绑定登录用户、供应组织和采购价格上下文；每次操作重查成员权限。`merchant_commands.py` 将原商户预览转换为持久化批量命令，共用 `changes.py` 的审批与原子应用。多个库存池任一版本变化时，整批拒绝；重试已应用变更不重复加库存。提议不等于审批，对话中的同意也不能替代带内容哈希的主机审批。

`catalog_edits.py` 提供 Excel 来源的线路名称、说明及整条线路上下架；数据库保留不可改写的产品版本。API 来源的事实不能被本地编辑覆盖。Excel 再导入可按已批准预览更新名称，保留手工说明和暂停状态。团期本地销售规则由 `sales.py` 维护，文档解析复核由 `documents.py` 维护。

`product_display.py` 提供 API 产品的独立展示名称和介绍，经产品编辑提议、供应商管理员审批后使用。`GET /v1/merchant/products/{product_id}/display` 返回源文字、补充文字及两个版本；`POST /v1/merchant/product-display/proposals` 接受 `target_id`、`expected_version`、`expected_display_version`、`name_override`、`description_override` 和说明。两项覆盖字段必须显式提供，`null` 恢复沿用上游，空介绍明确不展示介绍。审批同时绑定原值和源基准；源数据或展示版本变化均需重新提议。

统一只读视图 `product_listing` 沿用调用者行级权限，返回有效内容及逐字段来源。目录、团期、工作台与报价使用一致名称；顾问和客户页标明云仓展示补充。同步保留补充，清除后采用最新上游文字。展示版本独立递增，不使已发布行程失效；报价记录展示版本，修改后须重新核价。原商户助手的 API 文字修改复用同一处理器与审批中心。字段归属、恢复语义和扩展边界见 [ADR-005](../docs/cloud-warehouse/adr-005-product-display.md)。

未知价格、过期库存、未接入的销售额和订单数均返回 `null`。无价格币种依据时使用 `XXX` 标记未指定币种；界面不得把它当成人民币金额。库存登记人数不能当成订单数或销售收入。营销与订单方法明确返回能力未开放。

## 供应商工作台

`examples/tour/merchant-web/` 复用原商户 `PortalShell` 与 `AssistantRail`，提供平台登录、供采组织切换、目录游标分页、Excel 导入、库存登记、协议价维护、客户映射核验与双方确认、审批预览和同步记录。启动与独立浏览器验收见该目录 README。`management.py` 只返回当前组织有权访问的业务摘要；审批对象名称附加在返回结果中，不改变待批准的内容哈希。`distribution.py` 与工作台“分销授权”提供已有供采关系的启用、停用及有效期维护；`sources.py` 支持供应商管理员创建及维护 Excel 供应源；新采购关系已有邀请、采购申请及供应商批准接口，工作台已接通双方邀请流程，实际员工开户和系统接口接入仍需补齐。

## 顾问与持久化对话

`examples/tour/storefront-web/` 通过 `TOUR_BACKEND_MODE=warehouse` 接入本服务，保留原页面框架，新增平台登录、采购组织、目录与团期游标分页、直接询价和云仓聊天卡片。两个网页共用 `web-shared/warehouse-client.ts` 与 `warehouse-proxy.ts`。启动、独立浏览器验证和旧模式边界见该目录 README。默认模式仍为 `legacy`，设置云仓模式不会自动停用独立运行的旧 ERP API。

`advisor.py` 直接读取受采购授权约束的云仓目录，使用 `WP-UUID` 线路编号和 `WD-UUID` 团期编号。旧 `RT-数字` / `DP-数字` 不会自动认作某家供应商的记录。线路与团期采用游标分页；商品详情只呈现前 24 个团期，并明确提供下一页游标。价格在明确团期、报价方案、人数、儿童年龄和房型后查询，不把线路目录的未知价格当成零元。旧会话、方案和附件的明确迁移尚未完成。

旧目录引用可通过 `POST /v1/advisor/legacy-references/resolve` 显式解析。请求必须同时提供 `connection_id` 和 `reference`（规范的 `RT-36` 或 `DP-36`），使用当前平台身份及组织上下文。`legacy_references.py` 只查当前采购组织仍有授权的 B2B 来源和已发布记录，不按名字猜测来源，也不跨供应商搜索“第一个同编号”。返回 `warehouse_id`、所属 `product_id`、当前版本与观测时间，并始终标注 `requires_revalidation=true`。

解析只建立当前目录身份对应关系；旧价格、库存、文档版本、分享令牌、会话和用户权限均不继承，不调用上游 API、不创建报价或订单。旧编号不能直接传入普通顾问商品查询，也不会因解析成功自动进入模型的已见商品集合。历史方案必须先确认原连接与用户归属，再逐项映射并重新查询当前条件；迁移和灰度切换仍须单独验收。

### 同团期多报价方案

`offers.py` 维护 Excel 团期的方案编号、名称、服务说明与启停状态。`GET /v1/merchant/departures/{departure_id}/offers` 支持游标分页，默认 25、最多 100 条；`POST /v1/merchant/offers/proposals` 由产品编辑或供应商管理员提议，再由管理员核对原值、新值、团期和共享库存关系后审批应用。方案编号在同团期内唯一，停用后保留；已有方案不能更换编号、团期、供应商或库存池。审批复核方案、团期、产品和供应源版本，与审计、Outbox 原子提交。

所有方案绑定该团期已交接的同一个名额池。新增方案不增加库存，各方案余位不能相加；价格在“价表与等级”分别维护。API 来源拒绝本地新增或覆盖方案，价格与库存继续由上游负责。商户基础目录的成人价格和通用价格修改仍指基础方案，其他方案通过报价方案及价表页面管理。

`GET /v1/advisor/offers?departure_id=WD-UUID` 返回当前采购方可见的启用方案，支持游标分页；`POST /v1/advisor/quotes` 接受 `offer_id`。多个方案时必须明确选择，服务端拒绝缺省或其他团期的方案。单一方案保留缺省兼容。助手先读取方案编号再询价；长服务说明在模型结果中限长，完整文字仍保存在报价及页面。报价快照记录方案版本、名称、说明和库存池；停用或修改使已有报价不能继续作为有效依据，恢复启用也不会恢复旧报价的有效性。客户投影包含方案说明，不暴露内部库存池编号。

迁移 `0020_offer_management` 为已有托管方案补充库存池引用并递增方案版本，使升级前托管报价失效；API 方案版本与库存余额保持不变。升级前备份，回退使用已核验的备份。

`warehouse_agent.py` 为原顾问运行时注册团期与报价展示扩展，强制关闭购物车、订单、履约、跨会话记忆和网页搜索工具。展示扩展同时向模型返回经过数据围栏处理的团期编号、日期、分页游标及报价摘要；前端卡片本身不进入模型上下文。后端也拒绝直接调用占位和交易方法。`warehouse_chat.py` 组装两类运行时，并通过动态会话上下文提供中国业务时区的当前时间；商户应用前从数据库读取当前操作员的有效审批，数据库应用时再次核验。

`conversations.py` 按组织、用户和角色保存对话与工具依据。每轮请求使用 `Idempotency-Key`，数据库租约阻止多进程同时处理一个会话。完成后保存消息、状态和事件，再发送完成事件；中断不保存半轮模型上下文，已经创建的业务提议仍以审批中心为准。相同已完成请求只重放结果，不重复调用模型。商户网页在创建会话前可选择已授权采购方，服务端固定价格上下文；聊天消息不能改写采购方。供采授权变更、自然到期或采购组织停用时，旧上下文拒绝继续使用，需开启新会话。流式事件发送前重新检查登录与权限。

单轮处理上限 240 秒，租约 5 分钟；一轮保存的消息、状态和事件合计不超过 2 MB。会话列表与轮次历史已支持分页，长期归档与生产容量验收尚未完成。自动回归使用可控模型响应；真实模型另在 ACME 隔离库验证团期查询、双口径报价、商户待审批提议、进程重启续聊与幂等重放。该验收不代表真实 B2B 业务数据或生产网页已完成模型验收。

顾问网页提供会话历史选择、刷新恢复与更早轮次读取；旧方案分享页在云仓模式明确提示未迁移。顾问内部报价页含同业采购价；直接询价面板提供客户预览、明确创建与撤销，新 `/quote#密钥` 页面只读客户字段白名单。密钥由浏览器用 POST 正文提交，不进入页面请求路径，完整链接只在创建后的组件状态中保留，由顾问自行交付客户。报价快照保存线路、团期和来源名称，避免多线路同日出发时混淆；旧快照缺少这些名称时不补造历史信息。导航中的“报价分享”可在刷新或重新登录后管理当前采购组织下自己的历史链接；历史聊天卡片分享尚未接通。

### 隔离真实模型联调

```bash
.venv/bin/python cloud-warehouse/scripts/serve_web_fixture.py --seed-prices --live-model
```

此命令仅在 `warehouse_test` 创建虚构 ACME 组织、平台账号、Excel 团期及协议价，监听回环端口 8006；登录信息保存在私有 `.warehouse/e2e-access.json`。显式 `--live-model` 仅装入 `examples/tour/.env` 的模型连接配置，环境变量优先，不注册真实 ERP 连接；不能与 `--scripted-assistant` 同用。会向配置的模型服务发送虚构业务数据并消耗模型调用额度。

通过平台登录及 `/v1/conversations`、`/{id}/chat` 可验证顾问查询 ACME 山海环线 1 的团期并为 2 位成人、双人标准间询价：市场价 240 元、同业价 200 元、余位 17。商户增加 3 位库存应只生成 17 → 20 的待审批预览，未经审批库存仍为 17。完成请求使用相同消息和幂等键重放时，不应新增报价、提议或库存流水。测试结束后停止专用进程；此命令不启动生产同步 Worker。

## 接口

服务启动后的 `/docs` 和 `/openapi.json` 提供实际契约。

| 能力 | 路径 |
|---|---|
| 登录、注销、组织 | `/v1/auth/login`、`/v1/auth/logout`、`/v1/me/organizations` |
| 目录、团期 | `/v1/products`、`/v1/departures` |
| 同步、来源异常 | `/v1/sync-jobs?after=...`、`POST /v1/sync-jobs/{id}/control`、`/v1/sync-runs?connection_id=...&before=...`、`/v1/source-issues?run_id=...&after=...` |
| Excel 上传 | `POST /v1/imports?connection_id=...`，请求体为 XLSX 文件，文件名用 URL 编码的 `X-File-Name` |
| 预览、原文件 | `/v1/imports/{id}/preview`、`/v1/imports/{id}/file` |
| 库存提议、流水 | `/v1/inventory/proposals`、`/v1/inventory/{id}/movements` |
| 供应源管理 | `GET/POST /v1/merchant/sources`、`POST /v1/merchant/sources/{id}/control` |
| 分销授权 | `GET /v1/merchant/grants?after=...&limit=25`、`POST /v1/merchant/grants/{id}/control` |
| 分销邀请 | `GET/POST /v1/distribution-invitations`、`POST /v1/distribution-invitations/preview`、`POST /v1/distribution-invitations/claim`、`POST /v1/distribution-invitations/{id}/decision` |
| 导入与审批历史 | `/v1/merchant/imports?before=...&limit=25`、`/v1/changes?status=staged&before=...&limit=25` |
| 审批、应用、作废 | `/v1/changes/{id}/approve`、`apply`、`discard` |
| Offer、协议价 | `/v1/offers?departure_id=...`、`/v1/prices/proposals` |
| 客户映射 | `/v1/customer-bindings` 支持 `after`、`limit`、`connection_id`、`buyer_org_id`；另有 `/verify`、`/proposals`、`/{id}/accept` |
| 报价快照 | `POST /v1/quotes`（带 `Idempotency-Key`）、`/v1/quotes/{id}`、`{id}/customer-view` |
| 客户报价分享 | `GET/POST /v1/quotes/{id}/shares`、`GET /v1/quote-shares?before=...&limit=25&status=all`、`POST /v1/quote-shares/{id}/revoke`、匿名 `POST /v1/public/quote` |
| 商户查询 | `/v1/merchant/listings`、`listings/{id}`、`snapshot`、`changes` |
| 工作台查询 | `/v1/merchant/overview`、`connections`、`partners`、`catalog`、`inventory`、`imports`；目录与库存列表返回 `next_cursor` |
| 协议价维护查询 | `/v1/merchant/offers` 游标分页；`/v1/merchant/contract?offer_id=...&buyer_org_id=...` |
| Excel 模板 | `GET /v1/import-template`，需要供应组织角色 |
| 团期销售规则 | `GET /v1/merchant/departures/{id}/sales`、`POST /v1/merchant/departure-sales/preview` |
| 商户提议 | `/v1/merchant/content/proposals`、`prices/proposals`、`inventory/proposals` |
| 顾问目录与团期 | `/v1/advisor/products`、`products/{id}`、`departures?product_id=WP-...` |
| 顾问询价 | `POST /v1/advisor/quotes`，带 `Idempotency-Key` |
| 对话创建与读取 | `POST /v1/conversations`；`GET /v1/conversations?role=advisor&before=...&limit=25`；`GET /v1/conversations/{id}?before=...&limit=10` |
| 助手流式对话 | `POST /v1/conversations/{id}/chat`，带 `Idempotency-Key`，返回 SSE |

导入与审批历史返回 `{items,next_cursor}`，按创建时间及编号倒序，每页默认 25 条、最多 100 条。审批状态在数据库分页前筛选，较早待审批记录不会被较新已处理记录遮住；省略 `status` 查询全部状态。游标只能指向本组织记录，即使该记录随后被审批或作废，仍可继续翻页。上传成功及审批操作成功后工作台回到第一页。

分享历史 `GET /v1/quote-shares` 返回 `{items,next_cursor}`，按创建时间与编号倒序；`limit` 默认 25、最多 100，`before` 是前页末条记录编号，`quote_id` 可限定单份报价。`status` 为 `all`、`active`（未撤销且在查看期限内）、`revoked` 或 `expired`（已到期但未撤销）。筛选先于分页，状态改变后的原游标仍可定位；游标与列表同时受当前组织、当前用户及可选报价范围限制。元数据没有密钥、摘要和价格；失去报价权限时隐藏线路名称与出发日期，保留自己的分享记录供撤销。查看期限内未撤销不代表报价内容仍有权读取或价格仍然有效。工作台与询价面板共用 `ShareHistory.tsx` 的分页记录和明确撤销确认，撤销后回到筛选首页。

供应源创建只接受 `request_id`（UUID）、`name` 和 `note`，库存权威固定为云仓，凭据固定为空，不创建分销授权或 Worker。创建键按供应组织隔离，相同内容重试返回同一供应源；同键不同内容拒绝，创建后的重试不覆盖已修改名称。列表按编号分页，只有管理员和审计人员可读；Excel 维护要求当前 `version`、名称、启用状态和说明。身份及创建键不可更改；系统接口来源只能经接入运维维护。启停同步递增相关授权版本，旧报价、对话和价格预览不能在恢复后自动重新生效。供应源变化、审计与 Outbox 同事务提交。

分销授权列表只向供应商管理员与审计角色展示本供应组织的已有授权，按编号游标分页。控制接口仅供应商管理员可用，要求当前 `version`、明确 `active`、带时区的 `valid_from` / `expires_at`（`null` 表示长期）及非空 `note`；不接收组织归属、连接编号或凭据变更。行锁和版本检查拒绝覆盖及重复提交，授权、修改前后审计与 Outbox 事件同事务提交。停用立即影响后续目录、报价、文档和对话授权检查；恢复后旧报价仍因版本变化而过期。已下载文件无法远程收回。该控制不作为模型工具，工作台要求管理员核对对象、状态、有效期和说明后明确提交。

`invitations.py` 为已有平台账号建立新的供采关系。供应商管理员创建邀请时提交 UUID `request_id`、`connection_id`、带时区的 `valid_from` / `expires_at`、`invite_hours`（1–168，默认 72）和 `note`。返回的 `code` 仅首次展示，数据库只保存 SHA-256 摘要；相同请求重试返回原邀请但 `code=null`，遗失后须撤销并重新创建。人类管理员自行交付邀请码，服务不会发送邮件或消息。

采购管理员用 POST 正文中的 `code` 预览供应商、供应源和有效期，再调用 `claim` 提交加入申请。预览不占用邀请码；申请不创建授权，也不开放目录。邀请仅允许一个采购组织领取；供应商核对申请组织后，通过 `decision` 提交当前 `version`、`action=approve|revoke` 和说明。批准重新检查来源版本、双方组织、申请人管理员资格及有效期，原子生成授权、审计和 Outbox。已有关系（包括停用关系）拒绝重复开户，使用分销授权维护；批准后的邀请不能用撤销邀请来撤销授权。

邀请列表按编号分页（`after`、`limit`，默认 25、最多 100），只返回双方管理员及本组织审计人员可见记录；未领取邀请仅供应方可见。令牌摘要、请求摘要及供应商内部说明不返回列表。运行时账号不能直接修改邀请表，只能调用限定的数据库函数。邀请码不放入 URL，接口响应禁用缓存。工作台“供采邀请”按角色提供创建、预览、申请、批准和撤销；每步业务提交都要求明确核对。邀请码只保留在组件状态，刷新、切换组织及退出后清除；供应商查看申请时显示采购组织名称与编号。实际员工开户和供采签认仍需完成，接口不创建上游客户映射或调用上游写接口。

`/v1/merchant/changes` 与原 `MerchantBackend.get_pending_changes` 保留完整待处理列表契约，仅返回本组织 `merchant` 类提议，不设静默的 100 条截断；工作台跨业务历史使用上面的分页接口。助手一次读取整个待办集合，大队列的模型上下文容量尚需单独验收。

会话列表按创建时间倒序，游标为上一页最后的会话 UUID；恢复接口返回最近一页、页内按时间正序，`next_cursor` 用于读取更早轮次。会话列表每页最多 100 条，历史每页最多 25 轮且累计事件正文以 2 MB 为窗口上限（首轮始终完整返回）。原始模型消息和状态不返回浏览器；服务端继续对话仍使用完整已保存上下文。供采授权变化后列表隐藏标题并标记不可恢复，历史读取与继续对话均拒绝。

订单、占位、支付端点固定拒绝。没有提供任意 SQL 执行接口。业务、原文件、审计数据的读取均经过组织权限；缓存响应使用 `no-store`。

## 线路文档

`route_editor.py` 将日常整理保存为不可变的 `route_content_revision`，通过已有审批中心写入 `document_publication`。新发布内容使用独立内容版本；普通价格同步不会令它失效。附件更换、重新解析、并发草稿和发布版本变化仍需重新核对。`route_content.py` 把原文转换为带类型的逐日段落及每日简述，保留原始正文；客户预览只返回已发布内容的字段白名单。`route_tags.py` 提供已确认标签、候选和持久排除记录，已批准的可检索标签进入现有搜索投影。上述操作只写云仓。

商户 `GET /v1/merchant/routes/{id}/content` 读取草稿，`include_source=true` 按需读取原稿与新候选；同一路径 POST 保存带期望版本的草稿，`/validate` 校验，`POST /v1/route-content-proposals` 提交发布审批。`/customer-preview` 只读取发布版本。原文件通过现有私有文档接口访问，不向客户暴露原文地址、内部修订标识或采购价。

`assets.py` 保存不可覆盖的私有原文件；`documents.py` 管理追加解析、复核与发布。`route_doc.py` 的 RouteDoc 3.0 对旧结构兼容；`tour.api.warehouse_documents` 在无凭据的受限子进程里原生读取 DOCX/PDF，保存固定失败阶段。图片稀疏文字页带物理页码提示，缺文字层时要求提供可读文件。

部署设置 `WAREHOUSE_OBJECT_ROOT` 为服务账号独占的 0700 私有目录，API 和文档 Worker 使用同一持久卷。此实现为单机私有对象存储，不能暴露为静态目录；S3/OSS 后端、对象版本备份与孤立对象清理尚待集成。本地 `local_warehouse.py serve` 使用 `.warehouse/objects`。文件在数据库事务提交前完整写入并刷新；事务中断可能留下没有数据库引用的私有文件，不会发布半文件。

```bash
# 环境已提供 WAREHOUSE_DATABASE_URL、WAREHOUSE_OBJECT_ROOT；身份须为本供应商的 sync_worker。
PYTHONPATH=examples warehouse document-worker --organization <supplier_uuid> --user <worker_uuid> --once
# 移除 --once 后持续认领本组织的解析任务。
```

上传 `POST /v1/documents?product_id=<uuid>`，请求体为原文件，`X-File-Name` 使用 URL 编码。文件最多 20 MB，DOCX 校验压缩展开量、文件数量、宏和 XML 实体。上传返回任务编号；解析不在请求中执行。任务使用数据库租约、并发认领及最多三次崩溃接管；解析失败保留原文件，人工可通过 `POST /v1/documents/{id}/retry` 重试。解析子进程每步最多运行 60 秒，CPU 和输出文件另有限额；Linux 限制地址空间。PDF 需要 `pdftotext`。失败只传固定错误码，不传原文、令牌、签名链接或命令错误输出。文字不足不等于纯扫描件，也可能是封面、部分图片或非行程文件，不降低阈值猜测行程。

`0034_document_extraction` 保留历史 MinerU/hybrid 证据，只读接口仍限供应商文档角色。`0037_native_documents` 停用旧外部任务并撤销写授权，供应商重新解析产生新一代原生结果。历史解析、人工稿与发布哈希不变。

`route_consistency.py` 检查版本、三餐、餐住、费用及团期时长冲突；人工核对说明绑定内容哈希，修改后需重新核对。`route_applicability.py` 按团期日期和出发城市选择同一来源下的已审批版本，未选团期或没有匹配时不返回含糊的行程。`route_extraction.py` 将可选模型字段逐项回查源行，仅补有效缺失字段，并把 provenance 保存到原有 `document_parse.field_sources`。`TOUR_EXTRACTION_MODEL` 留空时不发模型请求。`TOUR_REVIEW_MODEL` 可单独指定复核模型；未配时复用改写模型并如实标注。人工抽查比例由 `TOUR_REVIEW_SAMPLE_PERCENT` 控制。

`reparse_diff.py` 提供 `warehouse reparse-diff --organization UUID --user UUID --report /private/new-report.json`，默认只在内存比较最新解析；显式 `--apply` 才排队新一代解析。报告不得放入公开目录。每页 100 项，使用返回的 `next_cursor` 继续。`scripts/eval_route_parser.py` 校验虚构字段基线，`--private .warehouse/... --report .warehouse/...` 保存真实样本私有报告。

供应商通过 `GET /v1/documents?product_id=...` 分页列出文档，通过 `GET /v1/documents/{id}` 读取解析结果和字段来源。复核内容使用 `POST /v1/document-proposals` 提议，再复用 `/v1/changes/{id}/approve` 与 `/apply`。源文件身份不可修改，逐日行程须连续完整并符合产品天数；审批人由平台身份确定，不能让解析器填写。发布与产品版本、审计、Outbox 同事务提交。文件解析不修改价格或库存。

`Source.page_locations` 是可选的原始 PDF 段落定位，保存封面、解析原稿各日与条款的物理页序；页码随标准化文本合并、拆分和重排，不通过相似文字猜测。定位随源文件指纹及解析版本入库，在复核中不可改写；人工改动仍记录 `human_review`，不继承原文证明。DOCX 和旧解析稿默认为空，旧发布版本不回填。商户文档页及审批页用受权限保护的原文件临时预览并跳页，权限复查失败即清除预览；浏览器不支持 PDF 显示时可下载核对。发布前仍对采购方不可见。

供应商管理员或产品编辑可通过 `POST /v1/documents/{asset_id}/reparse` 提交 `expected_parse_id` 和原因 `note`，使用当前解析规则重新读取同一私有原文件。迁移 `0028_document_reparses` 为原文件和解析稿增加次数编号，历史数据归为第 1 次；每个成功解析稿只追加、不覆盖。请求与文档发布在同一产品锁下序列化，同一旧稿并发请求最多排队一次。新一轮排队后旧稿不能再用于审批应用；新稿失败或尚未复核时，已有发布版本及其访问规则保持不变。历史外部任务停用后的显式重试开启新次数；原生解析失败重试和崩溃接管沿用原次数。过期租约不能完成新一轮任务。

`GET /v1/documents/{asset_id}/parses` 按次数倒序分页，`GET /v1/documents/{asset_id}/parses/{parse_id}` 返回指定只读解析稿；均仅向所属供应商的管理员、产品编辑或审计角色开放，不向采购方或模型提供未复核草稿。新一轮仍绑定原文件的产品快照；历史产品版本不能通过重新解析自动变成当前版本。商户文档页提供排队与历史原稿查看。升级时停止旧 API/文档 Worker，备份、迁移并刷新运行角色的列权限后，统一启动新版服务，避免旧 Worker 用第 1 次默认值处理新任务。

发布前采购方不能读取文件或解析稿。发布后可读取 `/v1/advisor/products/WP-<uuid>/document` 的当前复核版本、`/v1/published-documents/{id}` 的指定版本，以及鉴权代理 `/v1/documents/{asset_id}/file`。供应商内部复核说明不返回采购方。产品后续更新会让旧文档退出当前查询；指定旧版本保留不可改写内容并标记 `historical`。授权撤销或产品下架会撤销采购方访问。相同文件在其他产品、产品版本或供应商下均不继承复核。

顾问原 `get_product_details` 只附带当前已发布行程与文档版本，超过展示长度明确提示截短。商户工作台的“线路文档”接通文件历史分页、上传、解析状态与重试、完整行程编辑、字段修订对照和原文件下载。审批中心核对文件来源与产品版本；发布详情显示人工修订后的版本，解析原稿单独保留。旧附件迁移尚未接通。

### B2B 来源附件

`document_fetches.py` 在目录发布事务中登记 `routeAttachmentUrl` 对应的任务，绑定来源快照、线路与产品版本。网络下载在独立 Worker 中执行，不阻塞团期同步。每个产品版本一个任务，成功后安排 24 小时复查；同 URL 内容变化也能通过重新下载和 SHA-256 对比发现，不依赖供应商 ETag 正确变化。线路来源及文件内容均未变时校验并复用已保存对象，不重复解析；变更文件保持未发布，仍走人工复核审批。

迁移 `0025_attachment_rechecks` 新增不可改写的 `document_fetch_observation`，回填已成功拉取的来源记录；后续复查、失败或产品版本变化不抹去旧文件来源。人工发布提升产品版本后，下次目录同步或附件 Worker 初始化建立对应任务，并沿用同来源最近成功检查的时间预算，避免立即重复下载。当前版本任务返回最近成功检查、变化结果及下次检查时间；失败仍按三次上限退避，之后等待人工重试。复查不是自动发布，也不证明此前人工版本仍符合供应商最新条件。

应用迁移后先执行运行角色授权，再重启 API、目录与附件 Worker。已有成功任务首次检查安排在迁移后至少 24 小时，避免升级触发全量下载；实际执行需要附件 Worker 持续运行。原发布文件不因复查失败自动删除或撤销。超过 24 小时才发现的供应商变更仍有窗口，正式供应商试点需确认该周期及下载流量。未实现条件请求、Webhook 和 OCR。

```bash
# 服务环境提供 WAREHOUSE_DATABASE_URL、WAREHOUSE_OBJECT_ROOT 和精确的附件主机列表。
WAREHOUSE_ATTACHMENT_HOSTS=files.acme.example warehouse attachment-worker \
  --organization <supplier_uuid> --user <worker_uuid> --connection <connection_uuid> --once
```

移除 `--once` 后持续执行，每个任务间隔一秒；空队列等待五秒。每个 Worker 使用明确的供应商连接和 `sync_worker` 身份。未配置主机列表时直接拒绝启动，不能从来源 URL 自动扩展信任范围。API 和下载、解析 Worker 共用私有对象卷。

`remote_documents.py` 仅接受允许列表中的 HTTPS 主机和 443 端口，拒绝凭据 URL、内网/环回/链路本地地址及重定向；校验全部 DNS 地址后直接连接选定公网 IP，TLS 证书和 SNI 仍使用原主机名。请求不携带 ERP 令牌、Cookie 或环境代理。流式读取限制 20 MB，拒绝压缩传输，下载子进程总计最多 60 秒；DOCX/PDF 内容另经原文件校验。失败只保留错误码，不记录签名链接或响应正文。

下载任务使用两分钟租约，崩溃后可接管；瞬时失败按退避重试最多三次，格式或地址等确定性拒绝直接进入失败状态。`GET /v1/document-fetches?product_id=<uuid>` 返回当前产品版本任务，`POST /v1/document-fetches/{id}/retry` 供本供应商管理员或产品编辑重试失败任务。旧租约、产品版本变化或供应商连接失效时不能保存文件。已下载文件的详情包含来源快照编号、ETag 和读取时间，原 URL 留在受限的来源快照中。下载只创建待解析文件，Worker 无权批准发布。

本地命令使用 `.warehouse/attachment-policy.json` 的 `allowed_hosts` 精确列表，配置文件不入库：

```bash
.venv/bin/python cloud-warehouse/scripts/local_warehouse.py attachments --limit 10
.venv/bin/python cloud-warehouse/scripts/local_warehouse.py parse-documents --limit 10
.venv/bin/python cloud-warehouse/scripts/local_warehouse.py attachment-status
```

`--limit` 为单次最多处理数，默认 1、最大 1000；暂无可执行任务即退出。首个命令为已经同步的目录补齐队列，不重新请求目录 API。来源图片、Excel、旧版 DOC 和超限文件会明确失败；不会通过猜测生成行程。解析失败保留私有原文件，可人工重新上传可读版本。

## 验证

独立 PostgreSQL 测试覆盖跨租户隔离、撤销授权、分页截断、重复同步、异常团期保留、并发登记、审批过期、冲正、导入版本冲突与事务回滚。根目录普通 `pytest` 在未配置测试数据库 URL 时跳过数据库测试；必须额外运行上方 `local_warehouse.py test`，不能把跳过视为通过。CI 可设置 `WAREHOUSE_TEST_ADMIN_URL`、`WAREHOUSE_TEST_DATABASE_URL`、`WAREHOUSE_TEST_AUTH_URL`，数据库名须以 `_test` 结尾。

商户与报价用例另覆盖买方价格隔离、客户映射确认、查询期间撤销授权、未知指标展示、重建后端后的审批应用、多团期批量修改、不可改写的历史版本、Excel 下载审计以及 HTTP 登录到内容编辑的完整流程。

顾问与对话用例覆盖超过 20 页的持续读取、未知库存、禁用交易工具、报价不扣库存、原顾问运行时的工具与展示链路、并发租约、幂等重放、中断恢复、同组织不同用户隔离和授权撤销后的历史保护。

任务用例覆盖持久周期、有限重试、重启不重置、最终尝试崩溃、租约接管后旧任务拒绝发布、原子成功记录、心跳、超时取消和连接锁争抢；测试使用虚构来源，真实 B2B 需另行只读同步核对。

### 团期销售规则

供应商管理员或产品编辑提交 `target_id`、`expected_version`、`sales_paused`、必填但可为空的 `local_booking_deadline` 与 `reason`；时间须含时区。云仓保存原停售/截止值及来源版本、业务时区，由共享审批服务生成 `departure_sales` 提议。管理员批准和应用时锁定并复查版本，审计与 Outbox 同事务提交；过时或无变化提议拒绝。截止不得晚于出发业务日结束。

规则只改变云仓查询与报价资格，不改动源快照和账面余位，后续 B2B 同步保留这些本地设置。停售、过截止或过出发业务日拒绝新报价；更改规则使旧快照失效，解除限制后也不能恢复旧快照。`can_quote=true` 仅表示未被本地规则拒绝，供应商实际报名条件仍需确认；上游停售和报名截止字段映射尚未完成。商户工作台在团期详情设置规则，经审批中心完整前后对照后应用；顾问卡片显示销售状态、截止与账面余位。

Excel 模板版本 2 在原 10 列后增加“云仓停售”“云仓报名截止（北京时间）”“销售规则说明”，旧版 10 列文件仍可导入。停售填写“停售”或“解除停售”；截止填写完整北京时间或带时区 ISO 时间，Excel 日期时间单元格按北京时间解释。空白保留已有规则，新团期空白表示未设置；移除截止须填写“清空”，仅日期和无说明的修改拒绝。新建库存的期初不可售数量与团期整体停售分别管理。

导入预览附带 `sales_rules` 前后对照和 `source_version`，应用前重算并核对当前团期、库存及供应源版本；提前出发日期时也检查保留的截止。实际修改销售规则的提议人须为供应商管理员，或同时拥有库存管理和产品编辑角色。管理员批准后，产品、团期规则与库存命令在同一事务提交；任一库存调整失败则全部回滚。模板仅为输入，不包含公式或真实供应商数据。

## 检索与事件消费

顾问目录在同一 SQL 语句内，先从当前可见供应源各取一页候选，再按全局 UUID 排序截取页面。筛选条件与游标在各来源截取之前应用；所有表继续执行 RLS，分页不缓存授权，也不限制可继续读取的页数。供应商自己的已发布记录仍遵循原有读取规则。

`outbox.py` 将发布事件消费为 `search.py` 的组织隔离检索副本。副本携带来源/展示版本，落后或缺失时回查实时数据；采购权限、价格和库存始终实时查询。数据库副作用与消费者回执原子提交，重复事件不重复处理；失败退避最多 5 次，未知事件明确失败。`GET /v1/search-worker` 供供应商管理员/审计员查看积压、失败、心跳和版本覆盖。

```bash
warehouse outbox-worker --organization UUID --user WORKER_UUID --once
warehouse outbox-status --organization UUID --user WORKER_UUID
warehouse rebuild-search --organization UUID --user WORKER_UUID
warehouse retry-outbox --organization UUID --user WORKER_UUID --event EVENT_UUID
```

去掉 `--once` 常驻轮询。上述命令只使用 `WAREHOUSE_DATABASE_URL`；检索 Worker 不需要上游凭据。只读状态也允许同组织供应商管理员/审计员身份，其余命令要求 sync_worker。现有本地上下文可执行 `scripts/local_warehouse.py outbox --limit 20`、`outbox-status`、`rebuild-search`。消费者回执独立于 `outbox_event.processed_at`，恢复和性能边界见 [ADR-006](../docs/cloud-warehouse/adr-006-search-outbox.md)。

## 运行监测

`http_metrics.py` 使用 Prometheus 官方客户端输出每进程请求次数、完整耗时/响应头耗时直方图、在途请求数与响应字节计数。设置独立的 `WAREHOUSE_METRICS_TOKEN` 才启用 `/metrics`；普通平台身份不能读取，网页代理不转发该路径。指标仅含固定路由、方法、状态与传输结果，排除组织、用户及请求内容。采集配置、查询口径与单 Worker 部署边界见[接口指标](../docs/cloud-warehouse/http-metrics.md)。

部署目录的可选 `observability` profile 提供独立 Prometheus 采集与 TSDB；本地 `scripts/local_monitoring.py` 仅监听回环地址。规则记录按路由的请求速率、HTTP 完整 2xx 比例和 p95，采集失败连续两分钟形成内部告警，恢复后解除。没有请求时不生成虚假 100% 比例；规则不发送外部通知。密钥、历史存储与重启操作见[采集器运行手册](../docs/cloud-warehouse/monitoring-collector.md)。

`http_observation.py` 为 HTTP 响应生成 `X-Request-ID`，同一编号进入包含注册路由、已验证组织、状态及全程耗时的 JSON 日志。日志不读取正文或原始 URL，聊天流结束/中断/取消分别记录，两个网页代理保留响应编号。正式入口配置独立日志输出并关闭原始访问日志；异常不携带上游或数据库文本。字段、排查、测试及集中采集边界见[接口请求追踪](../docs/cloud-warehouse/http-observation.md)。

`operations.py` 与 `GET /v1/operations` 汇总当前供应商的同步成功率、最近成功时间、调度/租约/心跳、来源异常、未来团期库存过期数、余额与完整流水差异、当前文档失败及待复核、检索事件积压。仅供应商管理员、审计员及同步身份可读。商户数据同步页每 10 秒刷新，组织切换取消旧请求并清除旧组织展示。

已审批变更发生版本/业务冲突、库存不足、重复业务编号或数据库错误时，业务调用保留原异常，并另存 `change.apply_failed` 审计，字段仅为固定错误码与变更类型。不保存数据库异常原文、价格明细、账号或聊天内容。数据库断连等错误必须查询变更状态后再判断提交结果；不能凭错误响应假定未提交。

```bash
warehouse operations --organization UUID --user OPERATOR_UUID
.venv/bin/python cloud-warehouse/scripts/local_warehouse.py operations
```

接口观测不是自动修复或外部消息通知。备份、接口时延/报价成功率、通知投递尚未集中监测，响应和页面明确标为未监测。阈值、边界及排查步骤见 [运行监测手册](../docs/cloud-warehouse/operations-runbook.md)。

顾问目录、报价和 HTTP 团期响应只提供 `availability`（`available`、`unavailable`、`unknown`），不返回具体库存。精确库存保留在服务端核算和商户读取中。顾问会话授权摘要包含库存展示规则版本，旧规则下的消息、工具状态和重放不会重新进入顾问上下文；历史记录不删除。

`merchant_source_reads.observation` 可接受当前操作者、同一供应源且未过期的 `verification_id`，查询前后复核客户上下文。`merchant_business.route` 在无已发布行程时向具备文档权限的供应商返回 `draft_document`；只预览最新成功解析代际，并保留源线路版本，不修改审核或发布记录。

### 逐日内容整理

`route_editing.py` 定义统一标题、独立参考里程和有来源段落的改写校验；`route_candidates.py` 按已展示线路的当前附件解析生成持久候选。`route_content_candidate` 保存来源、模型、规则版本、逐日进度和核对结果。原始解析、人工修订与发布版本互相独立。模型仅改写简述和正文；餐食、酒店、航班及费用字段不由模型修改。缺天、重复天号不自动补齐，核对未通过的日期保留原文。

`warehouse route-content-worker --organization UUID --user UUID` 运行独立后台任务，需要已授权的模型配置。它逐日处理并续接过期租约，不提交审批或发布。商户原文对照可采用最新候选后人工保存、复核与审批。协议、限制与真实样例见[逐日整理验证](../docs/cloud-warehouse/daily-content-editing.md)。

## Advisor requirements

`trip_brief.py` persists each advisor conversation's field values and provenance. GET/PATCH `/v1/conversations/{id}/brief` restore and edit requirements; PATCH requires `expected_version` and records human edits as `advisor`. Migration `0038_trip_brief` adds the owner-only RLS table `conversation_brief`; refresh runtime grants after migration. Search and quote readiness are independent; an unknown adult/child split never defaults from total travelers. Inferred fields remain visibly pending confirmation and can be quoted.


`advisor_actions.py` powers POST `/v1/conversations/{id}/actions` (search_routes,
departures, offers, quote, share_quote, customer_confirmed, offline_hold_recorded).
`expected_version` rejects stale writes; `request_id` replays a completed action.
The conversation lease, owner permissions and current product visibility are rechecked.
Quote party and rooms come only from TripBrief. Multiple offers require explicit selection.
`advisor_matching.py` adds bounded per-source ranking, range filtering and reviewed shopping
reasons, then applies the same global ordering and opaque keyset cursor. Departure cards
return only capacity categories; exact inventory remains private.

`advisor_flow.py` requires a unique human choice from a completed card page before model
tools show departures or quote a departure. It reads the active server-owned turn and
owner-scoped history; model claims of user intent cannot authorize advancement. Ambiguous
references wait for a choice. Direct workbench buttons carry explicit user selections.
New search requirements clear old selections and invalidate the quote. Route details
return itinerary content; departures are read separately after route selection.
Clean suggestion rounds end the response without another model summary. Collapsed
out-of-window departures remain available in the UI but are excluded from model result notes.

`TripBrief.share_token` stores the share receipt ID, despite its legacy design name;
it never stores the bearer token. Raw share links appear only in the immediate response.
Offline follow-up stores a status and note only. See the
[implementation and acceptance record](../docs/cloud-warehouse/advisor-workbench-acceptance.md)
for prompt-rule mapping, local browser evidence and migration instructions.

`chat_delivery.py` owns up to 32 in-process advisor turns per API worker, with a bounded subscriber queue. Disconnected browsers recover persisted turns; process shutdown interrupts unfinished work. Session activity extends the live session by seven days without reviving revoked or expired tokens.

`destinations.py` defines a bounded, operator-owned country, city, region and group vocabulary. `travel_requirements.py` normalizes model destination patches against the active human message and preserves unknown party composition and inferred years. Explicit countries remain required; examples affect ranking and exclusions remove candidates. Advisor manual values retain precedence.

`destination_catalog.py` supplies source-labelled destination facts from names, allowlisted supplier fields, globally applicable reviewed publications and approved merchant tags. `0039_destination_projection` stores these rebuildable facts with source, display, content and dictionary versions on the existing search projection. Stale projections use the same live rules; product authorization remains live. Name matches are candidates, not confirmation of itinerary coverage or capacity. Date-bound ranked searches require a published departure in that window. Upgrade with API and writers stopped, migrate and refresh runtime grants; existing catalog selection, schedules and inventory are unchanged.

`route_kit_content` 和 `route_media` 处理 `route-kit/1` 的草稿、对客投影及私有封面。`route_search_facts` 在原搜索投影中保存已发布行程特征；`itinerary_reads` 为顾问提供按日、按条款分页读取。迁移 `0040_route_kit` 添加内容绑定复核及媒体存储，升级步骤见 [route-kit 接入](../docs/cloud-warehouse/route-kit-integration.md)。

## 开发测试自动发布

`route_test_publication` 只在明确设置 `WAREHOUSE_DEPLOYMENT_MODE=development-test`、启用开关并绑定组织、来源和操作员后生效。`warehouse route-test-publish` 消费新版解析稿；直接来源单元覆盖率达到85%，且通过天数、解析失败和高风险条款检查后，通过原变更记录发布。指标不把推断续句和原样挂入算作整理完成，不代表事实准确率。数据库保留 `test_auto`、评分和机器处理记录；页面明确标识未人工审核。解析后的人工修改继续走正常审批。配置见 `deploy/route-test.env.example`，服务在可选 `test-publication` profile 下。

## 顾问搭档与商户核实闭环

`copilot_records.py` 保存每位顾问独立的客户、旅客和跟单；`copilot_engine.py` 保存需求版本、采纳和确认。`copilot_inquiries.py` 只向对应商户开放明确提交的核实内容，商户回复经顾问采纳后才用于本单。`copilot_facts.py` 检查当前发布版本和事实编号；`copilot_sales.py` 绑定结算快照、销售总价及利润，保存线下成交和收退款台账。利润为销售总价减结算总价，不开启占位、下单或支付。

`copilot_api.py` 提供 `/v1/copilot` 接口。`warehouse_copilot.py` 使用并行的强类型需求提取和意图判断，再执行受限业务动作；模型不能直接覆盖已确认需求。供应商权限变化后停止历史会话，但顾问自己的客户与线下台账仍可管理。长会话的台账汇总独立查询，完整记录可分页读取。

`copilot_crypto.py` 使用 AES-GCM 加密证件原图和识别候选。部署配置独立的 32 字节 Base64 `WAREHOUSE_ADVISOR_MATERIAL_KEY`，不得提交仓库或放入浏览器；该密钥必须与数据库、对象备份分别保管，恢复时使用原密钥。`copilot_ocr.py` 只在本机调用受限的 Poppler/Tesseract，识别结果须人工核对，不自动改旅客信息。上传前须选定团期，原图保留至行程结束后 90 天；到期拒绝读取，由 `copilot_retention.purge` 离线管理员任务清理，元数据留作审计。历史备份的保留与销毁仍遵循备份运维策略。

数据库升级至 `0044_advisor_retention`，停止旧 API 和写入 Worker 后迁移并刷新运行角色授权。加密原图与供应商原件共享私有对象卷、备份锁和一致性检查，供应商文档不会被顾问资料清理任务删除。实施与验收见 [顾问搭档一期](../docs/cloud-warehouse/advisor-copilot-implementation.md)。
