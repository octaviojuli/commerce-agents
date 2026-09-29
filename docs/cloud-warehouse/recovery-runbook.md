# 云仓备份与恢复

`cloud_warehouse.recovery` 服务于离线运维。备份包包括数据库导出、同一快照的表内容摘要、附件索引和全部已登记私有文件。恢复只接受预先创建的空数据库和全新附件目录，不覆盖现有业务库，不自动切换应用连接。

## 前提与范围

- 使用与应用匹配的代码和数据库迁移版本、PostgreSQL 16 兼容的 `pg_dump`、`pg_restore`。恢复后的管理员拥有表和安全定义者函数；业务和认证使用独立低权限角色。
- 管理员连接必须能完整读取所有组织。工具设置 `row_security=off`，权限不足时失败，不导出被 RLS 截断的数据。导出期间不要运行数据库迁移；备份已通过共享对象锁与附件清理互斥。
- 附件根目录及备份父目录为 0700，文件为 0600。备份存放在非公开目录，不能放进网页静态目录或提交到 Git。当前后端为本地私有卷。
- 包中不包含 ERP / 模型 `.env`、数据库角色密码、TLS 私钥、进程配置和应用镜像。这些由独立的受保护配置交付流程恢复；数据库只保存供应源凭据引用。
- 库存、审批、报价、会话、平台密码哈希和登录会话都属于数据库备份。包包含业务敏感数据；校验和保证内容一致，不能证明来源可信，只恢复受信任的自有备份。
- 当前命令是逻辑全量备份，不是持续 WAL 归档。定期任务入口和 systemd 模板已提供，但真实调度、异地加密副本、对象存储版本控制及保留期尚未配置，不能宣称达到规划的 RPO ≤ 15 分钟。

## 创建与校验备份

在受保护的运维环境注入 `WAREHOUSE_ADMIN_URL`，不要把连接密码写进命令行。以下使用示例路径；父目录须提前创建且权限正确，目标包名不得重复。

```bash
umask 077
mkdir -p /private-backups/warehouse
chmod 700 /private-backups/warehouse
warehouse backup /private-backups/warehouse/batch-001 --objects /private-objects/warehouse
warehouse verify-backup /private-backups/warehouse/batch-001
```

导出通过 [`pg_export_snapshot`](https://www.postgresql.org/docs/16/functions-admin.html#FUNCTIONS-SNAPSHOT-SYNCHRONIZATION) 与 [`pg_dump --snapshot`](https://www.postgresql.org/docs/16/app-pgdump.html) 共享快照。附件按该快照中 `document_asset` 的组织、UUID、字节数和 SHA-256 复制；文件不可改写，因此不需要停止常规读取和写入。未提交上传及孤立文件不进入备份。

工具先写独立 `.partial-*` 目录，全部完成后发布为目标目录；失败清理本次临时目录。已有成功备份不会覆盖。`manifest.json` 记录快照时间、迁移版本、所有现有应用表的逐行内容摘要和附件索引；未来新增表会自动纳入，缺少主键则拒绝备份。`verify-backup` 复核清单格式、数据库文件哈希及全部附件的长度、哈希和私有权限。它不能替代实际恢复演练。

## 定期运行与状态

`backup-cycle` 是一次执行并退出的运维入口，由外部调度器调用。它不读取应用配置文件，管理员 URL 仍仅从 `WAREHOUSE_ADMIN_URL` 环境变量获取。以下数值仅展示参数用法，不能当作已经批准的部署预算：

```bash
warehouse backup-cycle --root /var/lib/tour-warehouse-backups \
  --objects /opt/tour-warehouse/data/objects --target-database warehouse \
  --max-bytes 8589934592 --reserve-bytes 4294967296
warehouse backup-status --root /var/lib/tour-warehouse-backups --max-age-seconds 900
```

根目录须预先创建、0700、初始为空，且与对象目录互不包含。首次执行将根目录绑定数据库地址、数据库名称和对象目录，之后拒绝换源复用；不自动接管现有备份目录。固定 `job.lock` 用操作系统锁防重：有活动持锁者时返回 `already_running`，不会覆盖其状态或启动第二份备份。`job.json` 和 `state.json` 为 0600，状态原子发布并同步到磁盘。日志不包含数据库 URL、密码、业务正文或底层异常文本。

执行前根据数据库占用和登记附件大小估算新增空间，检查根目录已用量与可用磁盘余量。`max-bytes` 和 `reserve-bytes` 是前置保护，不是文件系统硬配额；并发增长、压缩效果和导出开销可能不同。空间不足返回 `SPACE_GUARD` 和失败退出码，不删除旧备份来腾空间。失败备份不会替换上次成功快照；已生成但校验失败的包保留供运维核查。强制终止可能留下 `.partial-*`，后续执行把它计入占用，不自动删除。

每次成功要经过原 `backup` 和完整 `verify-backup` 校验，保存最后成功的**数据库快照时间**、包名和大小。`backup-status` 不需要数据库凭据，会探测真实文件锁；锁已释放而状态仍是 `running` 时报告 `interrupted`。按快照时间计算年龄，未来时间、缺失包、过期快照均不算新鲜；失败或中断即使还有新鲜旧备份也返回非零退出码。状态查询只检查包存在性，不重复全量哈希核验，明确返回 `bundle_reverified: false` 和 `restore_verified: false`。应另外运行 `verify-backup` 和隔离恢复演练，不能把状态为成功当成灾难恢复验收。

`deploy/systemd/` 的模板采用 15 分钟日历周期、持久调度与单次服务。复用 [systemd timer](https://github.com/systemd/systemd/blob/main/man/systemd.timer.xml) 的现成机制：服务正在执行时不会重复启动，停机错过的周期可在恢复后触发一次；进程锁还覆盖手工执行。模板不自动安装或启用。使用前确认独立管理 Python 环境包含匹配版本代码，`pg_dump` 可用，将私有环境文件放在 `/etc/tour-warehouse-backup.env`，并核对 URL 与显式目标库、目录权限、空间预算及只读/可写路径。修改模板目录时同时更新 `ReadWritePaths`。

完成一次真实单次备份及隔离恢复核验后，运维才执行 `systemd-analyze verify`、复制单元至系统目录、`systemctl daemon-reload` 和 `systemctl enable --now tour-warehouse-backup.timer`。检查 `systemctl list-timers`、服务退出状态及 `backup-status`；本入口不发送外部通知。启用后的频率、执行耗时、失败重试间隔、独立副本和恢复演练共同决定 RPO/RTO，15 分钟定时器本身不能证明 RPO ≤ 15 分钟。

本入口不自动清理备份，也没有 OSS/S3 上传或加密能力。保存位置与保留规则尚未确定时，仅保留模板及隔离测试结果，不启用真实定时任务。原有迁移前备份不属于该任务根目录，不会被覆盖或删除。

## 恢复到隔离目标

1. 从受信任的基础设施流程创建新空库及其管理员。预建独立业务、认证角色，两者不得是超级用户、拥有 BYPASSRLS、创建数据库或创建角色。不要把应用、Worker 或旧数据库连接池指向恢复库。
2. 设置专用 `WAREHOUSE_RESTORE_ADMIN_URL`。工具要求连接中的库名与 `--target-database` 完全相同。选择新的私有附件目录，并确保磁盘能容纳恢复副本。
3. 执行恢复。示例中的角色须与实际预建角色一致。

```bash
warehouse restore /private-backups/warehouse/batch-001 \
  --target-database warehouse_restore_candidate \
  --objects /private-restores/candidate-objects \
  --runtime-role warehouse_runtime \
  --auth-role warehouse_auth

warehouse verify-restore /private-backups/warehouse/batch-001 \
  --objects /private-restores/candidate-objects
```

工具先检查备份及空库，撤销目标库对 PUBLIC、业务和认证角色的 CONNECT 权限，再使用 [`pg_restore --single-transaction --exit-on-error`](https://www.postgresql.org/docs/16/app-pgrestore.html) 导入。不使用 `--clean` 或 `--create`，不会删除旧库。恢复后的表和函数由目标管理员拥有；源库角色 ACL 不照搬，由当前 `grant_runtime`、`grant_auth` 重新授予最小权限。

验证包括：迁移版本、每张表的行数及内容摘要、附件引用与文件哈希、库存总量/已售/停售/版本按不可改写流水重算，以及组织字段表的强制 RLS。审批内容和报价、会话快照参与逐表摘要核对。业务角色 CONNECT 保持关闭，报告中的 `activation_required: true` 表示尚未启用。报告写到恢复附件父目录的私有 JSON 文件。

若数据库导入失败，单事务回滚；若后续附件复制或验证失败，保留新目标作诊断，仍不可启用。不要向非空目标重复运行恢复，不要根据“库已经存在”推断恢复成功；修复原因后使用另一个新目标。工具不自动删除恢复库，也不自动重试。

## 启用前的业务核对

恢复报告证明快照内容复原，不代表整个业务已经恢复。先保存验证报告，再进行以下恢复后的状态处理；处理会改变表内容，之后不能再要求与原备份逐表摘要相同。

1. 保持 API 和所有 Worker 停止。核对灾难发生前后新增的线下销售、库存调整及供应商变更，记录备份时间之后可能丢失的业务；不可用旧快照抹除已确认销售。
2. 撤销恢复库中的旧登录会话：`UPDATE auth_session SET revoked_at=now() WHERE revoked_at IS NULL;`。重新核对停用员工、组织成员及供采授权，避免恢复已撤销的访问。只在已明确确认的恢复库中执行。
3. 在恢复库暂停全部同步调度并递增配置版本，保留历史尝试次数；不要同时启动旧、新两套 Worker。过期租约由 Worker 的既有恢复机制处理。文档和附件 Worker 同样保持停止，先检查排队与失败任务。
4. 检查过期报价、待审批提议、会话租约及附件人工发布状态。重新询价和生成过期提议，不延长旧报价或审批有效期。真实文档不能因为恢复成功而自动批准。
5. 经运维核对后，显式授予目标库对业务、认证角色的 CONNECT；应用仍使用受限账号，先做健康、登录、组织隔离、附件鉴权及库存查询验证，再按采购组织灰度切换。重新登录，不复用恢复前的浏览器令牌。
6. 逐来源恢复 Worker，核对最新上游目录和库存，再开放相应采购组织。回退应用路由时保留新产生的库存与审计流水；不得直接覆盖回旧数据库。

外部配置还原、重新登录、业务核对和灰度切换都计入实际 RTO。单机导入耗时不等于生产恢复时间。

## 定期入口验收

新增备份任务、恢复和对象清理专项 22 项通过；最终完整数据库与连接器回归 503 项通过、零跳过，仓库 Ruff、格式与一致性检查通过。专项覆盖真实备份与校验、重复执行互斥、空间拒绝、上次成功记录保留、校验失败不推广新包、错误信息脱敏、错误根目录/数据库拒绝、快照过期与文件移走，以及备份与附件清理双向互斥。

[ECS 隔离验收](backup-job-acceptance.json)使用独立虚构 PostgreSQL 容器、44 张表和空附件目录，验证真实 CLI 完成备份、无数据库凭据读取状态、空间不足及非零退出码。强制杀死自有测试进程后，状态从运行正确变为中断。systemd 模板通过 `systemd-analyze verify`；数据库、容器、卷和临时文件均已清理。本次 Linux 样本没有附件，附件内容和库存恢复能力仍以非空 ACME 恢复测试为证据，不能用本次空目录证明它们。

最初 Linux CLI 检查发现独立管理环境没有 Web 依赖，已将供应商冷却模块改为仅在相关业务命令中加载，保留同步/Worker/分组命令所需能力；最终源码摘要与本地一致。当前仅暂存于独立候选目录，没有替换线上 API 或安装真实备份 service/timer。存储策略确定后仍须完成真实单次备份、隔离恢复和实际调度验证。

## 已完成的演练与剩余工作

隔离 ACME 数据库测试覆盖 Excel 库存期初、销售、冲正、审批、协议价、报价、会话、登录会话和私有文档。恢复后可用量仍为 17，组织隔离生效；备份期间新增记录不混入旧快照。缺少附件在数据库导入前即失败，表内容变动和库存流水不一致均被拒绝，失败临时包不残留。

本地真实数据演练已恢复 34 张表、7,414 行、242 个私有对象（934,294,807 字节）。逐表及文件校验通过，受限同步身份可读 287 条线路，未设置组织上下文时可读线路为 0。此真实库尚无 Excel 库存池，余额重算能力由上述非空 ACME 库验证，不能把真实库的零行检查当作库存业务验收。

本次本机全量备份约 2.63 秒，独立恢复及校验约 4.93 秒。正式 CLI 再次校验通过后，本轮创建的恢复库和附件副本已清理，完整备份与私有报告保留。演练没有切换现有 API，也没有启动恢复库 Worker。ECS 上的容量、异地副本、加密、定时备份、WAL/PITR、告警及完整服务切换演练仍待完成。
