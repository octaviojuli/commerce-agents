# 接口请求追踪

`http_observation.py` 对每次 HTTP 请求生成新的 UUID，在响应头 `X-Request-ID`、请求状态及一条 JSON 完成日志中使用同一编号。顾问和商户同源代理保留该响应头。客户端传来的编号不复用，防止冲突及日志内容注入；该编号不是登录身份、业务幂等键或审批凭证。代理尚未连到 API 时返回的 502 没有 API 请求编号。

采用 Starlette 的纯 ASGI 中间件方式，直接观察 `send` / `receive`，不读取请求或响应正文、不缓冲聊天流，也不使用会改变流式任务边界的 `BaseHTTPMiddleware`。实现参考 [Starlette 官方中间件说明](https://starlette.dev/middleware/#pure-asgi-middleware) 和 [ASGI Correlation ID](https://github.com/snok/asgi-correlation-id) 的请求编号模式；本模块额外限制日志字段和异常内容，不引入新的追踪依赖。

## 日志字段

| 字段 | 含义与边界 |
|---|---|
| `event` / `schema_version` | 固定事件 `warehouse.http.completed`，当前字段版本 1 |
| `timestamp` / `process_id` | UTC 完成时刻及 API 进程编号 |
| `request_id` | 此次 API 请求的服务端编号 |
| `method` / `route` | 标准 HTTP 方法与注册路由模板；未知方法记为 OTHER，未匹配路径记为 `<unmatched>` |
| `organization_id` | 通过平台身份和组织成员检查后的组织 UUID；匿名或组织鉴权失败时为空，不采信原始请求头 |
| `status` | 已成功发送的响应状态；尚未发送时为空 |
| `outcome` | `complete`、`incomplete`、`cancelled`、`disconnected` 或 `error` |
| `response_complete` | 是否已成功发送最后一段响应正文 |
| `headers_ms` | 从进入 API 中间件至响应头发送完毕的毫秒数 |
| `duration_ms` | API 内的完整请求耗时，包含响应流发送及背压，可能包含响应后的后台任务 |
| `response_bytes` | 已发送正文分段的字节数；不是文件名或正文摘要 |

`outcome=complete` 仅代表 HTTP 传输结束，仍须结合状态码判断拒绝或失败。聊天已返回 200 但流中断时不会记作完成；模型在正常 SSE 中返回业务错误也不能仅靠 HTTP 日志判断成功。报价接口的 2xx 可能包含缺项报价，不等于完整报价已确认。客户端主动取消、服务端任务取消及连接异常分别记录可观察到的状态，不能从单条记录推定网络根因。

日志不包含原始 URL、查询参数、路径实值、IP、User-Agent、请求头、凭据、聊天、金额或异常文本。组织 UUID 仍属内部运行信息，日志仅向运维开放，不作为商户可跨组织查询的接口。

## 启动与排障

两个正式 API 工厂和本地 `local_warehouse.py serve` 配置 `warehouse.http` 日志到标准输出；仅配置本模块，不修改其他包的日志级别。部署镜像及本地入口关闭 Uvicorn 原始访问日志。手动启动时同样添加 `--no-access-log`，HTTPS 网关也应使用脱敏日志格式，避免另一个入口重新记录完整查询参数。

顾问报告失败时，从浏览器请求的响应头取得 `X-Request-ID`，在受保护的日志中按该编号查找。同步的运行编号、审批变更编号和报价编号仍由各自业务记录追踪；本模块尚未把这些编号贯通到所有下游调用，不应宣称完整分布式追踪。后台 Worker 不经过 HTTP 中间件。

未处理异常在尚未发送响应头时返回脱敏的 500 及请求编号，提示先核对原操作结果；已开始的流只能中止，不能再写第二个错误响应。原异常文本不会传到日志或 ASGI 服务端，流中止仅重新抛出固定技术错误。日志输出失败不改变业务响应；该日志不是库存、审批的持久审计，进程崩溃、输出丢失或尚未结束的请求可能没有完成记录。

Compose 已限制容器日志轮转。[接口指标](http-metrics.md)提供受保护的 Prometheus 采集端点；[采集器](monitoring-collector.md)已在本地验证历史落盘和重启恢复，并提供可选部署配置。集中日志采集、目标环境持续指标、外部告警投递和备份监测仍未验收；商户运行监测继续如实列出这些边界。不要把一次日志输出或指标拉取验证当作生产可用性验收。

## 验证

`test_http_observation.py` 覆盖敏感路径/参数/请求头排除、服务端编号、并发隔离、未处理异常、真正的流式响应与取消、不缓存、日志出口失败和实际数据库组织鉴权。`examples/web-shared/warehouse-proxy.test.mjs` 验证正常/拒绝/失败响应的编号传递、流式分段保留及连接失败无虚构编号；运行命令为 `node --experimental-strip-types --test examples/web-shared/warehouse-proxy.test.mjs`，Node 22 CI 同样执行。
