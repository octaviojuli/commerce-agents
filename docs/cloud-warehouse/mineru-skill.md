# 原生线路解析与共用 Skill

本页保留原链接，当前流程采用 Word 原生结构和 PDF 文字层；不再启动外部文件抽取。

`warehouse_documents` 在受限子进程解析，`route_fields` 增加保留原句的新字段，`route_consistency` 产生逐项待审问题。模型抽取可在原生结果之后作为带源行引用的候选，只补充通过代码核验的缺失值；未配置或失败均保留原生结果。模型改写保留事实、条件和数字与对象的关系，人工审批才发布。

图片较多、文字稀少的 PDF 页保留物理页码并要求人工核对。没有文字层的附件需要供应商提供 DOCX 或带文字层的 PDF。Markdown 是阅读形式，不作为准确性判据。

历史兼容：旧 MinerU/hybrid 解析类型、原始抽取证据和只读接口继续保留。`0037_native_documents` 将尚在运行的旧任务明确标记为已停用；供应商可显式重新解析，产生新一代原生结果，不覆盖旧解析、人工稿或发布版本。

参见 [ADR-010](adr-010-route-doc-v3.md)、[共用 Skill](../../examples/tour/skills/route-document-cleaning/SKILL.md) 和 `scripts/eval_route_parser.py` 的字段精确率、召回率、缺失及错误报告。
