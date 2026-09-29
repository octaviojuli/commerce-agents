# API 指标采集

`http_metrics.py` 使用 [Prometheus 官方 Python 客户端](https://prometheus.github.io/client_python/instrumenting/) 的 Counter、Gauge 和 Histogram。每个 API 应用拥有独立 registry，不使用默认进程/GC 收集器。中间件从同一套 HTTP 完成观测更新指标，不读取业务内容或额外查询数据库。

## 启用与凭据

默认不注册 `/metrics`。API 私有环境设置 `WAREHOUSE_METRICS_TOKEN` 后启用；值必须是 32 个随机字节编码成的 64 位小写十六进制字符串。使用密钥生成工具写入受保护配置，不复用 ERP 密码、模型密钥或平台登录令牌。错误格式在应用创建时拒绝，避免误以为已启用保护。采集请求使用 `Authorization: Bearer <该密钥>`，凭据以恒定时间比较；匿名、错误密钥以及任何普通平台账号均返回 401。

指标不出现在业务 OpenAPI 文档，也不经两个员工网页的 `/warehouse-api/v1` 代理公开。它仍须运行在仅运维采集器可达的 API 网络内，不应把 API 端口开放公网。API 和采集器分别持有受保护密钥；不要把密钥写到 Prometheus 标签、URL、仓库或命令行。轮换时同步更新两端并重启 API，旧密钥立即失效。

本地专用 `local_warehouse.py serve` 也可从私有 `.warehouse/database.json` 的 `metrics_token` 字段读取；环境变量优先。该配置与数据库凭据一样保持 0600，不复制到网页或 Worker 配置。正式部署仍使用 API 独立环境文件。

## 部署和采集

采用每个 API 容器一个 Uvicorn Worker；镜像启动参数固定 `--workers 1`。每个实例分别配置采集目标，再由 Prometheus 聚合。禁止在同一个目标后轮流命中多个进程，否则读数会在不同计数器间跳变。设置多 Worker 的 `WEB_CONCURRENCY` 或 Prometheus 多进程目录时应用拒绝启动；手工启动也不能覆盖镜像参数添加多 Worker。正式多进程模式需要另行实现生命周期和共享目录，不能仅设置环境变量。相关限制见[官方多进程说明](https://prometheus.github.io/client_python/multiprocess/)。

以下片段供已有 Prometheus 的私有采集网络使用；仓库另提供可选 Compose 采集服务和本地 native 启动工具，见[采集器手册](monitoring-collector.md)。`api:8005` 必须解析到一个明确的单 Worker 实例；密钥文件仅对采集器账号可读，文件内容为原始密钥，不加 `Bearer `。

```yaml
scrape_configs:
  - job_name: tour-warehouse-api
    scrape_interval: 15s
    scrape_timeout: 5s
    metrics_path: /metrics
    authorization:
      type: Bearer
      credentials_file: /run/secrets/warehouse_metrics_token
    static_configs:
      - targets: [api:8005]
```

该 HTTP 示例只用于受控的内部网络；跨主机采集应使用已验证的 TLS、私网访问控制或本机代理，不在公网明文发送凭据。采集端持久卷、保留时间、存储容量、可用性和告警接收方仍需部署验收。API 重启会清空内存指标；历史由外部采集器保存，不能把当前计数器当成持久账本。

## 指标口径

| 指标 | 标签/口径 |
|---|---|
| `warehouse_http_requests_total` | 固定路由模板、有限 HTTP 方法、状态码和 `outcome` 的累计交换次数 |
| `warehouse_http_duration_seconds` | 按路由、方法、结果统计完整交换耗时的直方图；包含流式发送与背压 |
| `warehouse_http_headers_seconds` | 按路由、方法统计发送响应头耗时的直方图；没有发出响应头时不观测 |
| `warehouse_http_in_progress` | 此实例当前在途 HTTP 请求；含尚未结束的聊天流 |
| `warehouse_http_response_bytes_total` | 按路由、方法统计已发送正文分段字节数 |
| `warehouse_http_metrics_started_seconds` | 本 registry 创建时的 Unix 时间，帮助识别进程重启 |

`/metrics` 及其尾斜线重定向不计入指标，避免采集流量影响业务数据。健康检查仍计入，查询业务成功率时应按业务路由过滤。未匹配路径统一为 `<unmatched>`；新添加但不在应用初始化路由集合中的路径也不会形成任意标签。组织、用户、请求编号、参数、客户端地址和 Token 均不成为标签。直方图包括 0.3、0.5、0.8 秒等目标边界，分位数是桶内估算，不等于压测报告中的精确样本分位数。

结果沿用[HTTP 追踪](http-observation.md)：`complete` 只表示传输完整，401/403/409 仍是拒绝；流在 200 响应头后取消时记为 `cancelled`，不记作成功。SSE 内的业务错误和 2xx 缺项报价尚未有独立语义指标，不能据此宣称模型或完整报价成功率。输出也不能替代供应商同步、库存账本、审批和备份的业务监测。

例如按固定报价路由查询 HTTP 完整响应的 p95：

```promql
histogram_quantile(0.95,
  sum by (le) (rate(warehouse_http_duration_seconds_bucket{
    route="/v1/advisor/quotes",method="POST",outcome="complete"
  }[5m]))
)
```

这包含该路由的业务拒绝和错误响应耗时。统计 2xx 完整传输占比时，使用同一路由的 `warehouse_http_requests_total`，分子筛选 `status=~"2..",outcome="complete"`，分母为全部结果；无请求时显示无数据，不能填成 100%。仓库记录规则处理了没有任何 2xx 时应为 0、没有请求时应无数据的区别。延迟/比例、采集 `up` 状态和在途请求数应共同判断。采集失败已有两分钟内部告警，业务阈值、跨实例告警去重和通知投递尚未完成。

## 验证

测试覆盖默认关闭、独立运维凭据、实际平台账号拒绝、无敏感标签、未知路径合并、应用 registry 隔离、直方图阈值、并发计数、在途流取消及排除采集自身。网页代理测试确认不会转发指标请求。生产采集器连续拉取、历史保留及告警仍须在实际目标环境验证，单次 `/metrics` 返回 200 不等于监控系统已上线。
