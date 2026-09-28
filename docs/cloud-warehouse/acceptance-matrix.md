# 一期业务验收矩阵

本矩阵对应 [后端开发规划](backend-plan.md) 第 15 节的 12 项验收，不替代供采双方签认或 ECS 上线验收。技术测试使用隔离 PostgreSQL 和虚构 ACME 数据；真实 B2B 证据包括本地只读同步及[新 ECS 部署验收](ecs-acceptance.md)。数据库为 `0032_source_cooldowns`，当前代码包含 Excel 容量入口、外部报价总预算和持久共享冷却、目录及报价查询优化、固定连接预算的多进程容量入口和团期响应兼容性测试；加入定期备份任务保护、同采购方两供应商并行故障与真实 Worker 进程恢复测试后，最新完整隔离回归为 506 项通过、零跳过；ECS 小规模 HTTP 四项目标通过，100 并发长负载时延仍未达标，详见[容量记录](capacity-report.md)，不能把功能测试通过当作容量验收。

## 场景与证据边界

| 编号 | 规划要求 | 可检查的证据 | 尚未证明的部分 |
|---|---|---|---|
| 1 | 两供应商外部编号相同仍隔离目录、附件、团期和报价 | [部署测试](../../cloud-warehouse/tests/test_deployment.py) 的 `test_same_external_36_isolated_across_catalog_documents_and_quotes` 在一次测试中同步两家外部线路/团期编号 36，经文件解析审批发布、供采客户映射批准及采购接受后分别报价；各自金额/库存不同，跨组织目录、团期、方案、附件、文档版本、报价读取及伪造报价请求均拒绝，拒绝请求不调用另一供应源 | 虚构 ACME 完整服务链已验证，不能替代两个真实供应商的业务试用，也不覆盖全部浏览器、导出或 AI 参数组合 |
| 2 | 同一产品按采购方授权显示不同同业价，所有访问入口不可越权 | [报价测试](../../cloud-warehouse/tests/test_quotes.py) 同一产品两采购方分别得到 200 和 160，私价记录按组织过滤；[顾问测试](../../cloud-warehouse/tests/test_advisor.py) 验证分页和工具来源约束；[会话测试](../../cloud-warehouse/tests/test_conversations.py) 验证组织、用户和授权撤销 | 真实供采价格条款、客户映射和完整浏览器/导出/缓存攻击矩阵尚未签认；当前不存在可跨用户复用的长期模型记忆 |
| 3 | 同步账号更宽的权限不自动扩大采购范围 | [同步测试](../../cloud-warehouse/tests/test_sync.py)、[供应源测试](../../cloud-warehouse/tests/test_sources.py)、[邀请测试](../../cloud-warehouse/tests/test_invitations.py) 验证默认不自动授权、双方确认及撤销；B2B 来源与采购身份分离 | 需业务方核对真实 ERP 授权范围能否由当前连接级分销授权完整表达；现有测试不能证明真实上游的产品级销售权限已全部映射 |
| 4 | 库存剩 1 位时两人登记，仅一次成功 | [库存测试](../../cloud-warehouse/tests/test_inventory.py) `test_last_place_concurrency_and_ledger_reconstruction`：并发应用一个成功、一个冲突，余额为 0，只有一笔销售扣减 | 技术条件已验证；目标部署环境与真实运营仍需试用 |
| 5 | 销售及冲正重试不重复扣增 | [库存测试](../../cloud-warehouse/tests/test_inventory.py)：同变更重放返回原结果；新变更复用销售业务号被拒绝；同销售再次冲正被拒绝，流水数量保持 | 应区分同一变更重放与用新请求号重复提交同一业务；后者明确冲突，不静默生成新销售 |
| 6 | Excel 预览后销售不能被旧预览覆盖 | [导入测试](../../cloud-warehouse/tests/test_imports.py) `test_preview_cannot_overwrite_new_sale`：旧预览应用冲突，余位仍为 6，重新预览后正确更新至 8 | 实际供应商的期初余额、已售口径和责任人尚未签认 |
| 7 | 上游库存失败/过期不伪装实时，供应商间故障隔离 | [顾问测试](../../cloud-warehouse/tests/test_advisor.py) 对过期库存返回未知；[连接器测试](../../examples/tour/api/tests/test_warehouse_connector.py) 覆盖来源错误和等待期限；[同步测试](../../cloud-warehouse/tests/test_sync.py) 验证半次失败不覆盖目录 | 共享冷却跨进程测试及[同采购方两供应商并行故障验收](supplier-failure-acceptance.json)通过：A 挂起、超时或限流不阻止 B；失败报价无价格、无实时余位，重放不重复请求。虚构浏览器验证了超时提示、同请求号重试及切换后丢弃 A 的迟到结果。真实双供应商、公网故障注入和主动请求配额尚未验收；网络中断提示仍直接显示英文浏览器错误，不把旧目录解释为实时余位 |
| 8 | 顾问/API/Agent/兼容路径均禁止 ERP 交易 | [API 测试](../../cloud-warehouse/tests/test_api.py) 订单/占位/支付返回拒绝；[顾问测试](../../cloud-warehouse/tests/test_advisor.py) 后端交易方法拒绝且工具集移除交易；[旧案例测试](../../examples/tour/api/tests/test_warehouse_legacy.py) 不重放旧工具调用 | 云仓模式不能自动关闭仍在运行的旧 Tour 服务；ECS 旧交易入口尚未切换停用，外部沙箱订单数量前后核验未完成 |
| 9 | AI 只提议，批准绑定版本及内容，重放不重复 | [商户测试](../../cloud-warehouse/tests/test_merchant.py) 提议不改内容、未经批准应用拒绝、重建后端后审批仍有效且重放不新增版本；[库存测试](../../cloud-warehouse/tests/test_inventory.py) 验证内容篡改及审批过期 | 已有虚构 ACME 真实模型联调证据见[实施状态](implementation-status.md)；真实供应商批准流程仍需运营验收 |
| 10 | Worker 中断恢复，重复/乱序不使旧值覆盖新值 | [作业测试](../../cloud-warehouse/tests/test_jobs.py) 旧租约拒绝发布、新租约接管、失败次数跨重启保留；[Outbox 测试](../../cloud-warehouse/tests/test_outbox.py) 去重及旧投影不覆盖新版本；[真实进程恢复验收](worker-recovery-acceptance.json)在本地及 ECS 当前镜像中分别于提交前、提交后执行 SIGKILL；默认 60 秒租约自然到期后接管，未提交数据完整回滚，新版本只发布一次，审计/Outbox 原子且采购可读 | 当前 B2B 为轮询，未实现/验收供应商 Webhook；Outbox 不能冒充 Webhook 验证。ECS 验收使用隔离虚构数据库和子进程；主机重启、Docker 自动恢复、数据库重启和网络中断尚未验收 |
| 11 | 多方案共享池只扣实际名额，未知价格费用明确 | [方案测试](../../cloud-warehouse/tests/test_offers.py)：两 Offer 同一库存池，一次售出 2 位后两者余位均由 10 变 8；[报价测试](../../cloud-warehouse/tests/test_quotes.py) 成人儿童所需名额与未知价/费用计算 | 技术模型已验证；真实上游的儿童占位规则、费用和退改条款仍须供应商核对 |
| 12 | 恢复后账本、会话、分享和客户方案归属/版本正确 | [恢复测试](../../cloud-warehouse/tests/test_recovery.py) 使用 `pg_dump`/`pg_restore` 和私有对象恢复，验证余额、持久会话、报价、分享撤销、历史方案分支版本和组织拒读 | 历史案例保持只读，旧公开分享未自动启用；真实旧案例尚未迁移。定期备份入口与 Linux 隔离检查通过，但真实调度未启用；异地备份、生产 RPO/RTO、PITR 与实际流量恢复未验收 |

## 当前交付条件

新 ECS 已完成内部测试部署，首个管理员已开户。Linux 镜像、HTTPS、登录隔离、真实 B2B 新批次、私有附件和模型连接的验证范围见[部署验收记录](ecs-acceptance.md)。原先缺少部署资源与登录账号的阻塞已解除。

完整业务验收仍需要真实供采双方确认客户映射及价格权限、真实 Excel 库存交接资料与审批责任人。旧 ERP 交易入口停用、历史案例迁移、异地备份和生产恢复目标也未因此自动完成。云仓内部测试部署通过不等于一期全部业务验收通过。
