# ADR-007：数据库连接公平分配

## 问题

目录 SQL 优化后，100 并发混合调用仍有库存与报价尾部延迟超标。分段诊断观测库存调用 p95 约 646 ms，其中连接获取约 635 ms、SQL 往返约 14 ms；这些分位数来自各自样本分布，不能直接相加。原 QueuePool 的连接归还通知与新到申请竞争，等待者可能被后来的调用反复超过。单纯扩大池到 16 个连接未解决目标。

## 决策

复用 [Psycopg 官方连接池](https://www.psycopg.org/psycopg3/docs/advanced/pool.html#connections-life-cycle)，归还连接时直接交给第一个等待者。按 [SQLAlchemy 官方集成方式](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#using-psycopg-connection-pooling) 使用 `NullPool`、Psycopg `getconn()` 与 `close_returns=True`，不叠加第二层连接池，也不重写自定义锁或数据库驱动。

`database_pool.py` 管理资源生命周期。首次借用才创建池，最多 8 个连接，最多 256 个等待申请，获取超时 30 秒；使用官方健康检查，异常连接丢弃并补充。超过排队上限或超时通过 SQLAlchemy 的数据库异常边界返回，由现有 API 脱敏为 503。身份和组织仍由 `persistence.transaction` 设置为事务本地值，提交与回滚后清除。

连接参数由 SQLAlchemy dialect 解析后作为参数传给驱动。不能将其 URL 字符串直接改写为 libpq URI：两者对查询参数空格的编码不同，会损坏 `options` 中的超时配置。凭据不进入命令行或报告。

Engine 释放时关闭对应池与后台线程；在用连接可以结束当前工作，归还到已关闭池时实际关闭。后续复用 Engine 会建立新的池。各 Worker 必须在本进程创建 Engine，不继承已打开的池；不是跨进程全局连接上限。原来的 RLS、成员权限、只读同步及审批事务不变。

## 验证和限制

独立数据库验证先等待者优先、队列满/超时后的恢复、提交与回滚后的组织清理、自动提交隔离级别恢复、Engine 释放与复用、闲置连接被终止后的重建，以及驱动超时参数真实生效。完整业务套件另验证恢复、授权撤销、报价有效期和库存幂等。

相同最大 8 连接的初步对照中，400 次混合调用库存 p95 约 211 ms、报价约 409 ms，均无错误。最终与延长负载证据在 [容量报告](capacity-report.md)。公平排队减少长时间等待，不保证所有请求都比之前更快；中位延迟可能上升。此决策不证明 HTTP/模型/上游调用延迟、生产资源容量或长期可用性，也不替代异步入口的事件循环响应性验证。
