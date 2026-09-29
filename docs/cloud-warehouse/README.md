# 旅游云仓规划

业务范围：多供应商、多采购旅行社；一期产品、报价与库存查询；Excel 供应商由云仓管理库存及扣减；订单交易留待二期。

- [完整后端开发规划](backend-plan.md)：源码复用、架构、身份、数据模型、API、库存、审批、迁移、验收和实施阶段。
- [浏览版](backend-plan.html)：完整规划的排版阅读版本，支持目录与打印。
- [开发任务清单](development-backlog.csv)：30 项任务，包含角色、依赖和验收条件。
- [订单预留 ADR-004](adr-004-order-boundary.md)：一期仅预留契约，明确库存权威、超时回查、候补及销售流水关联边界。
- [订单预留契约包](order-contracts.json)：由 Python 模型导出的字段定义，不代表交易功能已启用。
- [展示补充 ADR-005](adr-005-product-display.md)：上游事实与云仓展示文案分离，独立版本、来源标记及权限视图。
- [商户工作台业务结构方案](merchant-business-model.md)：已确认三类业务结构及独立详情页方向，包含字段、状态、返回体验和只读接入设计；未核实口径单独列出。
- [商户工作台页面草图](merchant-workbench-preview.html)：可点击的三个业务列表与独立详情页，支持分页、独立地址和列表位置恢复，使用虚构示例，不连接真实业务。

开发代码位于 [cloud-warehouse](../../cloud-warehouse/README.md)。当前进度和未完成项见 [实施状态](implementation-status.md)；规划文档不能作为功能已完成或线上已切换的证明。

规划浏览版的源码引用路径、目录锚点、两张内嵌架构图和任务依赖引用已检查；未进行浏览器视觉验收。

- [ADR-006：可重建检索与 Outbox 消费](adr-006-search-outbox.md)

- [运行监测与异常处理手册](operations-runbook.md)
