# 云仓部署

本目录提供 API、顾问网页、供应商工作台、四个 Worker 和可选 PostgreSQL 的独立部署配置。员工通过公网 HTTPS 网关访问两个网页；数据库与 API 不开放公网端口。网页的回环端口供同机网关使用，不要求员工建立 SSH 隧道。

Linux 镜像构建、隔离容器验收及新 ECS 内部测试部署已通过，[部署验收记录](../../docs/cloud-warehouse/ecs-acceptance.md)列明实际验证范围与剩余业务条件。现有 Tour 的部署目录与本目录是两个服务栈；启动云仓不会迁移旧会话，也不会停用旧 ERP 交易接口。切换前需完成下文验收。

## 服务与容量

| 服务 | 作用 | 配置 | 内存上限 |
|---|---|---|---|
| api | 登录、目录、报价、审批、聊天与私有附件 | api.env、connections.json | 768 MiB |
| advisor | 原 Tour 顾问框架的云仓模式 | Compose 内的代理地址 | 384 MiB |
| merchant | 供应商业务工作台 | Compose 内的代理地址 | 384 MiB |
| catalog-worker | 单个 B2B 连接的完整分页同步 | catalog.env、connections.json | 384 MiB |
| outbox-worker | 检索更新、事件回执与重试，仅数据库网络 | outbox.env | 256 MiB |
| attachment-worker | 来源附件受控下载 | attachment.env | 512 MiB |
| document-worker | 私有附件解析与重试 | document.env | 1536 MiB |
| database | 可选的独立 PostgreSQL 16 | postgres-password | 1 GiB |
| prometheus | 可选的 HTTP 指标采集、历史与规则评估 | prometheus.yml、warehouse-rules.yml、metrics-token | 512 MiB |
| admin | 按需迁移与授权，完成即退出 | admin.env | 512 MiB |

上限不代表实测常驻内存。部署目标按规划从独立 4 核 8 GiB 评估，保留系统、网关、文件缓存和解析峰值空间；实际并发仍需压测。不要在已有约 1.6 GiB 内存的共享节点上同时构建与启动整套服务。数据库可替换为专用 PostgreSQL；此时不启用 `bundled-db`，将所有数据库 URL 指向专用实例并配置 TLS 和网络访问控制。镜像在独立构建机完成。

`WAREHOUSE_DATABASE_CPUS` 控制可选数据库容器的 CPU 上限，默认 1 核。它是部署配额，不是数据库连接数或并发承诺；调整时同时核对 API、Worker 与宿主机余量，并重新测量容量。隔离测试中的 CPU 限流数据与不同配额结果见[容量记录](../../docs/cloud-warehouse/capacity-report.md)。

默认启动 API 和两个网页。`workers` profile 才启动后台处理；`ops` 仅用于显式运维命令。当前 Worker 组绑定一个供应源，增加供应商时复制独立的 Worker 配置与身份，不混用凭据。

`observability` profile 单独启用 Prometheus；额外预算内存和持久磁盘，不能把它计入原 API 的上限。它只连接内部 monitoring 网络，无宿主机端口、数据库凭据或原始业务附件。ECS 启动及采集验收见部署记录；本地 native 采集和恢复证据见[采集器手册](../../docs/cloud-warehouse/monitoring-collector.md)。

## 构建

在已具备 Docker 的 Linux 构建机，从仓库根目录执行。下列 `RELEASE` 是本次版本标签；按目标 ECS CPU 架构构建。构建上下文排除本地数据库、`.env`、私钥和 Next 缓存，禁止通过 build args 传入业务凭据。

```bash
docker build -f cloud-warehouse/deploy/Dockerfile.api -t local/warehouse-api:RELEASE .
docker build -f cloud-warehouse/deploy/Dockerfile.web --build-arg TOUR_WEB_APP=storefront-web -t local/warehouse-advisor:RELEASE .
docker build -f cloud-warehouse/deploy/Dockerfile.web --build-arg TOUR_WEB_APP=merchant-web -t local/warehouse-merchant:RELEASE .
```

网页使用 Next standalone，镜像包含独立服务器、追踪依赖和静态资源。API 镜像包含原 Tour 解析模块和 Poppler；不包含原 ERP 服务启动入口。运行镜像通过版本标签或 registry digest 分发，基础 Python、Node、PostgreSQL 镜像也应在完成兼容性测试后固定 digest。不要把本地 macOS 的 Node 依赖直接复制到 Linux 作为镜像构建替代。

### 独立镜像验收

`scripts/container_smoke.py` 使用 Python 标准库及 Docker CLI，在至少 4 GiB 内存的专用 Linux Docker daemon 上检查三个预构建的 `linux/amd64` 镜像。它创建随机命名、带专属标签的内部网络、虚构 PostgreSQL 库及对象/配置卷，不读取 `.warehouse` 或 `.env`，不开放宿主机端口，不注册真实供应商连接。固定 ACME 密码仅属于该一次性网络内的测试账号，不能用于部署配置。

```bash
python3 cloud-warehouse/scripts/container_smoke.py \
  --api-image local/warehouse-api:RELEASE \
  --advisor-image local/warehouse-advisor:RELEASE \
  --merchant-image local/warehouse-merchant:RELEASE \
  --report output/warehouse-container-smoke.json
```

检查涵盖真实镜像中的迁移、低权限数据库角色、平台登录、组织隔离、目录与异常读取、私有 DOCX 上传/鉴权下载、两个网页的 API 代理和请求编号，以及 API 重启后原登录令牌与附件仍可读取。API/网页采用只读根文件系统、非 root 用户和受限临时目录，另检查时区与 `pdftotext` 可用性。库存快照自然过期时必须返回未知可用量，验收不会延长有效期。

正常结束或失败后只清理本次标签匹配的容器、网络及卷，不删除镜像或构建缓存。清理失败会令报告失败并给出资源标签；daemon 不可达不能记作已经清理。报告不包含会话令牌、文档正文或数据库 URL。进程被强制终止时仍需由构建机生命周期或运维核对清理遗留资源。

CI 的 `warehouse-images` 任务在 Ubuntu 顺序构建 API、顾问端和商户端，然后执行上述验收。该流程配置已加入；是否完成 Linux 验收以实际任务结果为准。此脚本检查镜像运行，不代替生产 Compose、HTTPS 网关、真实来源 Worker、监测服务、备份及 ECS 切换验收。

## 私有配置

需要 Docker Compose 2.30.0 或更高版本，使用 [`env_file.format: raw`](https://docs.docker.com/reference/compose-file/services/#format) 保留密码中的美元符号和引号。业务 env 文件每行 `KEY=实际值`，不加包裹引号，不写行尾注释，不写变量引用。URL 中的账号、密码须百分号编码。`stack.env` 只包含非敏感的 Compose 路径、版本和 UUID。

部署管理员将 `*.env.example` 复制为受保护配置目录中的同名 `.env`，填写实际值；`connections.example.json` 复制为 `connections.json`。示例中的 ACME 地址、替换标记和全零前缀 UUID 不能作为实际配置。配置文件不要放进镜像、Git 或日志。

| 文件 | 内容与权限 |
|---|---|
| stack.env | 镜像、绝对目录、内部端口、实际供应商/连接/Worker UUID；运维账号可读 |
| api.env | runtime/auth 两个数据库角色、连接前缀下的 B2B 凭据、可选模型配置；0600 |
| catalog.env | runtime 数据库角色与本连接前缀下的三项 B2B 配置（例如 `SUPPLIER_A_*`）；0600，不放入其他供应商凭据 |
| outbox.env | 仅 runtime 数据库角色；0600，无 ERP/模型配置 |
| attachment.env | runtime 数据库角色与已批准的精确附件主机；0600 |
| document.env | 仅 runtime 数据库角色；0600 |
| admin.env | 仅迁移账号；0600，常驻服务不挂载 |
| connections.json | 实际连接 UUID 对应 `SUPPLIER_A` 等环境前缀；10001:10001、0600 |
| postgres-password | 可选数据库的管理员密码，内容为一行原始密码；见下文 |
| metrics-token | 与 api.env 的 WAREHOUSE_METRICS_TOKEN 相同的原始密钥；10001:10001、0600，仅采集器挂载 |

配置目录由 root 持有、0700。Compose 读取 env 文件后传给对应容器；JSON 单文件只读挂载，必须允许容器 UID 10001 读取。`data/objects` 由 10001:10001 持有、0700；恢复对象时也保持同一属主，子目录 0700、文件 0600。API 与下载 Worker 可写，解析 Worker 只读。对象目录不配置静态网页服务。

使用 bundled PostgreSQL 时，`postgres-password` 仅通过 Compose secret 挂载进数据库容器。宿主机父目录保持 root 0700；该文件可设 0444 供镜像中的 postgres 用户读取，其他业务容器不挂载。密码须与 `admin.env` 的数据库密码一致。已有数据目录时修改文件不会自动轮换数据库密码。不要把此目录整体公开或递归放宽权限。

外部数据库模式仍需填写 `WAREHOUSE_POSTGRES_IMAGE`；Compose 会解析 profile 中的变量。API 与目录 Worker 只读挂载同一份 `connections.json`，以实际连接 UUID 选择 `SUPPLIER_A_*` 等前缀。API 需要所列连接的全部凭据；每个目录 Worker 只加载其 `--connection` 对应凭据，不需要其他供应商密码。缺少绑定、重复 UUID、缺少或空白凭据时拒绝启动，不回退到通用账号。账号值可来自已授权 `.env`，不要把整个原 `.env` 分发给所有进程。

旧部署升级时，将 `catalog.env` 中 `TOUR_ERP_*` 三项改为连接表指定的前缀，核对它们与 API 对应连接使用同一授权账号，再重建目录 Worker。只更新镜像而不更新旧配置会明确启动失败。离线 `warehouse sync` 与 `warehouse worker` 也支持 `--connections /private/connections.json` 或 `WAREHOUSE_CONNECTORS_CONFIG`；该模式禁止同时指定 `--factory`。未设置连接表时保留原单源环境/自定义工厂用法，生产 Compose 始终启用显式绑定。连接表只能由运维维护；此校验不能证明运维录入的账号确实属于对应供应商，接入仍需核对来源身份和样本。

## 数据与启动顺序

后续命令在部署目录执行，使用填写完成的 `stack.env`；为了省略重复参数，可在当前运维 shell 中定义函数：

```bash
cw() { docker compose --env-file /opt/tour-warehouse/config/stack.env -f compose.yaml "$@"; }
cw --profile bundled-db --profile workers --profile ops config --quiet
```

不要运行会打印全部配置的 `config` 或 `config --environment` 并分享输出，其中可能包含凭据。

**已有本地云仓迁移：** 使用[成套恢复手册](../../docs/cloud-warehouse/recovery-runbook.md)，将数据库及私有附件一起恢复到专用空目标。保留组织、连接、来源及产品 UUID；不要再次 `onboard`。恢复验证完成后，按手册处置旧会话、暂停任务并显式恢复业务数据库连接权限。API 镜像没有 `pg_dump`/`pg_restore`，备份恢复由装有 PostgreSQL 16 客户端及仓库依赖的受信任运维主机执行。禁止用复制 PG 数据目录代替逻辑恢复。

**全新空环境：** 可选数据库先启动并等待健康：

```bash
cw --profile bundled-db up -d --wait database
```

运维人员在目标库创建 `warehouse_runtime` 和 `warehouse_auth` 登录角色，明确 `NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS`，不授予迁移角色成员资格；通过交互式 `psql` 的 `\password` 或受信任密钥流程设置随机密码，不将密码写在命令行。运行账号不得成为业务表所有者。新库随后执行：

```bash
cw --profile ops run --rm admin warehouse migrate
cw --profile ops run --rm admin warehouse grant-runtime warehouse_runtime
cw --profile ops run --rm admin warehouse grant-auth warehouse_auth
```

新供应商及其明确授权的采购方由离线管理员执行 `warehouse onboard --supplier ... --buyer ... --worker-email ...`，保存返回 UUID 并填入配置；它创建一个无登录密码的同步身份，不代表真实员工已经开户。员工账号使用 `auth.create_user` 明确分配实际组织与角色。不能让同步身份批准真实采购映射、文档或库存变更。

**两种路径共同步骤：** 数据迁移和身份配置完成后先启 API 与网页：

```bash
cw up -d --wait api advisor merchant
curl --fail http://127.0.0.1:18085/
curl --fail http://127.0.0.1:18105/
```

两个网页同时连接内部 `frontend` 网络和普通 `gateway` 桥接网络；后者使 Docker 能创建回环端口映射。API、数据库和 Worker 不加入 `gateway`。只连接内部网络的网页容器可能健康但没有实际宿主机端口映射，需核对 `docker port` 和本机 HTTP 读取。

将同机 HTTPS 网关的顾问站点转发至 `127.0.0.1:18085`，供应商站点转发至 `127.0.0.1:18105`。若网关运行在容器内，不能使用该容器的回环地址；应通过单独 override 将其加入本服务栈 frontend 网络，再转发至 `advisor:3000` 与 `merchant:3000`。不要为此将数据库/API 暴露到公网。网关需支持 20 MB 文档请求、至少 240 秒聊天流、关闭 SSE 缓冲，并配置有效证书。公网域名或 IP 证书与实际地址在目标确认后配置，不能把上述内部端口当员工登录地址。

验证正确的源账号和身份后，可先运行单次任务；无到期任务时目录命令返回 null，并不代表完成了同步：

```bash
cw --profile workers run --rm catalog-worker warehouse worker --connection "$WAREHOUSE_CONNECTION_ID" --organization "$WAREHOUSE_SUPPLIER_ID" --user "$WAREHOUSE_WORKER_ID" --once
```

上面三个 shell 变量需由运维人员从 `stack.env` 对应 UUID 设置；Compose 插值不会自动设置当前 shell。核对实际批次成功、来源/发布/异常数量与库存时效后，才启动常驻 Worker：

```bash
cw --profile workers up -d catalog-worker outbox-worker attachment-worker document-worker
cw ps
```

检索 Worker 按组织运行，多个供应源属于同一组织时只需一个。查询 `GET /v1/search-worker` 或执行 `warehouse outbox-status --organization UUID --user UUID`，确认积压、失败、索引版本和心跳；异常恢复见 [ADR-006](../../docs/cloud-warehouse/adr-006-search-outbox.md)。迁移需要 PostgreSQL 的 `pg_trgm` 扩展可用。

Worker 不使用 HTTP 健康探针；容器 running 不等于同步正常。通过商户同步中心的“运行监测”汇总及 `GET /v1/operations` 检查异常；`warehouse operations --organization UUID --user UUID` 可用于运维读取。该命令不发送外部通知，不替代备份监控。通过商户同步中心检查最近心跳、成功时间、任务状态、重试和来源异常；文档查看下载与解析状态。现有数据库的调度配置优先于 Worker 启动参数，暂停任务不会因重启自动启用。

## 切换验收与回退

API 镜像关闭 Uvicorn 原始访问日志，由 `warehouse.http` 输出脱敏 JSON 完成记录；顾问和商户代理保留 API 响应的 `X-Request-ID`。按编号查阅受保护容器日志，字段及错误/流中断语义见[接口请求追踪](../../docs/cloud-warehouse/http-observation.md)。HTTPS 网关同样不要记录带敏感查询参数的完整 URL。现有日志轮转不等于集中采集、指标保留或外部告警；这些服务仍需目标环境配置与验证。

API 的可选 `WAREHOUSE_METRICS_TOKEN` 启用运维专用 `/metrics`，格式为随机生成的 64 位小写十六进制密钥，仅放入 `api.env` 与采集器密钥文件。镜像明确使用一个 Uvicorn Worker；扩容时每个 API 实例单独采集，不能用一个负载均衡地址交替读取不同进程的内存计数器。准备属主 10001:10001、权限 0700 的 `data/metrics` 后，可显式启用 `observability` profile；具体检查与启动命令见[采集器手册](../../docs/cloud-warehouse/monitoring-collector.md)。不开放新的宿主机 API/监测端口；外部告警及目标环境网络验收仍未完成。

切换需同时确认：两个公网 HTTPS 地址可登录；供应/采购组织隔离；目录与团期完整分页；真实客户映射由双方人类确认；只读报价的缺价/未知费用正确显示；附件上传、后台解析、审批与鉴权下载；服务重启后会话和任务继续；匿名与跨组织请求被拒绝；当前 B2B 新批次与异常记录可核对；未发生上游交易。容器内核验 UID、对象权限、日志无凭据及内存峰值。实际供应商操作和真实模型还需各自完成验收。

员工流量切换完成后，明确停用旧 ERP 交易入口，再进行一期验收。单独设置新网页的 warehouse 模式不会关闭仍运行的旧服务。不要保留一个可访问的旧下单入口，却宣称整个平台已切换为只读一期。

发布前保存旧镜像 digest、完整数据库/对象备份及网关配置。若新版本失败，停止新 Worker 认领任务，将网关回到经过确认的入口；存在数据库不兼容时恢复到另一套空库和私有对象目录，再验证后切换，不对原库执行破坏性降级。恢复旧 ERP 网页可能重新开放交易，须按已确认的业务范围处理。不要使用 `down -v`、删除数据目录或覆盖式恢复作为常规回退。


## 定期备份入口

`systemd/tour-warehouse-backup.service` 和同名 `.timer` 是可选运维模板，不随 Compose 启动。它们复用 `warehouse backup-cycle`，不让常驻 API 或 Worker 获得管理员连接。使用单独匹配版本的管理 Python 环境及 PostgreSQL 工具，并按 `backup.env.example` 准备私有配置。模板仅允许写 `/var/lib/tour-warehouse-backups`；改变目录时同时修改配置和 `ReadWritePaths`。

保存位置、空间预算与后续保留规则必须先明确，再复制模板并手动启用。当前没有自动删除、OSS 上传或外部通知；本机副本不能抵御整机故障。状态、失败出口及恢复验证边界见[恢复手册](../../docs/cloud-warehouse/recovery-runbook.md#定期运行与状态)。

## 线路内容版本与统一价升级

`0033_route_content` 必须在 API、目录、附件与解析 Worker 停止后迁移，再刷新 runtime 角色授权。保留旧原文件和发布版本；重新解析只生成候选稿，不自动代表人工审核。旧发布可读，新版内容工作台可继承同一原稿上的人工内容。

`0037_native_documents` 必须在 API、文档及内容写入服务停止后迁移并刷新 runtime 授权。移除旧 `TOUR_DOCUMENT_MODE` 和外部文件解析凭据；旧任务转为 `DOCUMENT_EXTRACTION_RETIRED`，重新解析需供应商显式发起。原文件、历史解析、人工稿和发布版本保留。新原生解析写 RouteDoc 3.0 后，回滚须使用兼容该结构的镜像或经验证的备份恢复。按 ADR-010 配置版本与日期；不要批量自动批准新版候选。

国旅环球等采用统一结算价的来源，由运维将 `capabilities.settlement_pricing` 设为 `{"mode":"uniform","version":1}`，在 API 私有配置中填写对应连接前缀的 `STANDARD_CUSTOMER_ID` 和 `STANDARD_PRICE_VERSION`。标准身份须经业务确认且只能用于上游读取；任何身份变更均提升策略版本。缺配置或版本不匹配明确报缺价，不退回采购客户专属身份。顾问报价仍检查实时分销授权，但不再要求逐采购组织核验价格客户。

## 逐日模型整理

`0035_route_candidates` 增加供应商私有的整理任务与候选表。停止 API 和目录、文档写入进程后迁移并刷新 runtime 授权，再启动新镜像。完整备份原数据库与私有对象；无需重写旧解析或旧发布版本。

复制 `content.env.example` 到私有配置目录，单独配置 runtime 数据库连接及已授权的模型地址、密钥、型号。不要复制 ERP、外部文件解析或平台管理员密钥。取得附件行程文本发送到该模型服务的授权后，启动 `content-editing` profile 的 `route-content-worker`。服务没有对象卷、HTTP 入口或发布权限；只处理在架的线路。每个日期先编辑，再独立核对，网络或核对失败时保留原内容供人工复核。

商户内容接口的 `editing` 显示处理进度与保留原文数量；详细报告只向供应商文档角色返回。完成的候选不可修改，人工采用仍通过既有草稿与审批流程。新增解析代次会产生新候选，已保存人工稿不会被替换。

可选源行模型抽取在文档 Worker 中配置 `TOUR_EXTRACTION_MODEL` 和已获授权的模型连接；留空则仅原生解析。单独复核模型与人工抽查比例见 `content.env.example`。调用的模型名称由操作员提供，不自动选择其他服务或账号。解析失败阶段在商户运营观察页按固定类别统计，不记录 stderr 正文、路径或凭据。


## 微信 H5 与小程序 web-view

顾问 H5 的微信真机验收需覆盖 iOS/Android 键盘、复制、切后台三十秒后的完整回答恢复。
公网 IP 仅用于当前内部测试，不满足小程序 web-view 的业务域名配置要求。
准备小程序业务域名时，需使用有效 HTTPS 证书、完成适用的 ICP 备案，按小程序后台要求放置校验文件并配置业务域名；真机 web-view 待域名准备后验收。
开发者工具关闭域名校验仅是演示，不能作为上线验收。
SSE 网关保持 `proxy_buffering off`，禁止缓存，转发 `X-Accel-Buffering: no`，读取超时覆盖最长四分钟模型轮次。不得以页面回到前台的瞬间显示成功代替持久化状态复查。
