# 指标采集器运行手册

采集器使用 Prometheus 3.14.0，按 15 秒间隔读取受保护的 API `/metrics`，将历史保存到独立 TSDB，并评估四条记录/告警规则。它没有数据库或 ERP 凭据，不写业务数据。当前提供可选 Compose 服务及已验证的本地 native 运行方式；目标 ECS 的容器、网络、容量和长期运行验收仍未完成。

## 配置与隔离

- `deploy/prometheus.yml`：单个 API 目标、文件凭据、禁止跟随重定向，以及样本数/响应体/标签长度上限。超过上限使该次采集失败，不接受截断数据。
- `deploy/warehouse-rules.yml`：按固定路由记录请求速率、完整 2xx 比例和完整响应 p95。比例计入 401/403 等拒绝，不能当作供应商或模型的业务成功率。
- `deploy/warehouse-rules.test.yml`：用 Promtool 验证两分钟故障触发、恢复解除、目标缺失、80% 成功、全部拒绝为 0 以及无请求时不返回比例。
- `scripts/local_monitoring.py`：从本地私有配置复制运维密钥，生成回环采集配置并保留原数据目录；不下载安装工具，也不管理其他服务进程。

Prometheus 是平台运维工具，不能给普通商户账号开放其全平台查询界面。Compose 服务只有内部 monitoring 网络，无宿主机端口；API 同时加入该网络。采集器不接数据库/出网网络，不挂载业务对象或整份 API 环境文件。管理写 API、HTTP 生命周期控制、remote-write 接收入口未启用，没有配置远程写入或 Alertmanager 通知。

## Compose 运行

先完成[云仓部署](../../cloud-warehouse/deploy/README.md)中的 API 与独立指标密钥配置。将同一原始密钥写入私有配置目录 `metrics-token`，属主 10001:10001、权限 0600；不要写 `Bearer ` 前缀。数据目录 `data/metrics` 的属主为 10001:10001、权限 0700。环境文件只需增加 `WAREHOUSE_PROMETHEUS_IMAGE`，发布标签应在目标验证后固定 digest。

从部署目录使用已有 `cw` 函数：

```bash
cw --profile observability config --quiet
cw --profile observability run --rm --no-deps --entrypoint /bin/promtool prometheus check config /etc/prometheus/prometheus.yml
cw --profile observability up -d prometheus
cw --profile observability exec prometheus promtool check ready --url=http://127.0.0.1:9090
```

检查目标 `tour-warehouse-api` 的 `up=1`、最近采集时间更新、规则健康为 ok；容器 running 或 ready 本身不能证明 API 密钥正确。查询通过受信任运维客户端在私有网络执行，不为此开放员工公网入口。外置 Prometheus 可复用相同配置，但每个单 Worker API 实例必须独立采集。

Compose 限制额外内存 512 MiB、Go 内存目标 384 MiB、查询并发 4、查询超时 15 秒和最大查询样本 500,000。原 1.6 GiB 共享节点不因此成为可用的完整部署目标，这些上限仍需目标容量验证。默认历史保留七天，并设置已落盘块的 2 GB 保留目标；当前内存块、WAL 和压缩过程仍需额外磁盘空间，该值不是文件系统硬配额。配置含义见[官方存储说明](https://prometheus.io/docs/prometheus/latest/storage/)。

## 本地运行

从[官方固定发布](https://github.com/prometheus/prometheus/releases/tag/v3.14.0)取得匹配操作系统/架构的二进制及 SHA-256 清单，核验后解压到 `.warehouse/tools/prometheus-3.14.0.<系统>-<架构>/`。本次 macOS arm64 文件的 SHA-256 为 `a9623f7f4fe65b1b171b423c1a72bbf23dfdf41a171dcb33e7dd302af80dc01c`，已同时与 GitHub 资产 digest 和官方清单核对；其他平台必须核对对应资产，不能复用此摘要。工具、密钥和数据均不提交到 Git。

本地 API 需已启用 `.warehouse/database.json` 中的 `metrics_token`。从仓库根目录运行：

```bash
.venv/bin/python cloud-warehouse/scripts/local_monitoring.py prepare
.venv/bin/python cloud-warehouse/scripts/local_monitoring.py serve
```

`prepare` 只更新自己生成的配置和密钥，不删除 `.warehouse/monitoring/data`。`serve` 先验证配置，再用固定版本工具启动；只监听 `127.0.0.1:9096`，只采集 `127.0.0.1:8005`。网页/API 查询地址仅供本机运维使用。该进程不是开机托管服务，退出任务或机器重启后的进程存活应重新核查。

本地密钥和配置权限为 0600，数据目录为 0700。进程环境只保留运行所需的少量系统变量和 Go 内存设置，数据库 URL、ERP 及模型配置不会传给采集器。轮换密钥后同时重启 API 与采集器，避免一端使用旧值。

## 故障与恢复

`WarehouseApiScrapeUnavailable` 在采集失败或目标消失持续两分钟后进入 firing，恢复后解除。先区分 API 进程停止、指标密钥错误、私有网络不可达和样本限额失败；不要把该告警当成业务库损坏或自动重试交易的依据。规则状态可在 `/api/v1/rules` 查询。当前没有通知接收方、Alertmanager 路由或外部投递，这个告警只在采集器内形成状态。

重启采集器前核实进程身份与 TSDB 路径，正常停止后再启动同一目录。不要同时启动两个进程读写同一个 TSDB，不删除 lock/WAL 文件绕过正在运行的进程。重启后用固定的历史查询时间对比原样本，再确认最新采集时间继续更新；只看到 ready 不算恢复验收。Prometheus WAL 的恢复不代替业务 PostgreSQL 的备份或 PITR。

## 验收证据和限制

本地实际观察到九个连续健康采集样本，跨度约 120 秒；正常停止并重启后，同一历史时间查询的样本完全一致，新的采集也恢复为 up。四条规则健康，管理写接口开关均为 false。私有工具验证及重启记录位于 `.warehouse/monitoring-tool-verification.json`、`.warehouse/monitoring/restart-audit.json`。

这证明本机采集与 TSDB 重启恢复，不证明七天保留、磁盘满处理、开机恢复、跨实例部署或 ECS 长期可用性。Linux 容器配置、规则 CI 的远端实际运行、业务指标面板、外部告警投递和指标库备份仍需完成。配置字段以[官方配置文档](https://prometheus.io/docs/prometheus/latest/configuration/configuration/)为准。
