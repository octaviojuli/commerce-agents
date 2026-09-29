# 本地附件孤立文件清理

`warehouse collect-objects` 处理文件写入成功、数据库事务随后回滚留下的对象，以及中断写入留下的临时文件。它不清理业务历史记录、来源快照、备份或供应商原文件，也不提供 S3/OSS 生命周期管理。

## 使用前提

- 运维环境明确提供目标库的 `WAREHOUSE_ADMIN_URL`，不能使用 API 的受限数据库角色。命令不读取 `.env`，不接受命令行密码。
- 每个数据库必须使用独立的本地对象根目录，不得与测试库、恢复库或其他服务共享。恢复作业保持停止；已完成备份使用独立对象副本。
- 所有上传 API、附件 Worker 必须加载含共享事务锁的新版 `documents.save_asset`；旧进程必须先停止。首次升级建议在维护窗口完成。迁移版本 `0024` 本身不能证明进程已加载此代码。
- 先完成并核验成套备份，保留检查日志。文件删除不会随数据库事务回滚。

## 检查和执行

路径仅作运维配置示例，先替换为对应数据库的私有目录。日志必须位于对象目录之外，文件名必须尚不存在。

```bash
warehouse collect-objects --target-database warehouse \
  --objects /srv/warehouse/objects --journal /srv/warehouse/operations/objects-check.jsonl

warehouse collect-objects --target-database warehouse \
  --objects /srv/warehouse/objects --journal /srv/warehouse/operations/objects-apply.jsonl \
  --retention-days 7 --apply
```

默认保留期 7 天，最短 1 天。使用文件修改时间和元数据变更时间中的较新值，近期拷贝、修改或新增硬链接都会重新进入保留窗口。执行时重新扫描，不直接信任上一次检查清单。

返回计数包括 `referenced`（保留的数据库引用对象）、`recent`（近期未引用文件）、`unrecognized`（跳过的名称、链接或非普通文件）、`candidates`、`bytes`、`missing`、`deleted` 和 `removed_bytes`。检查不读取文件内容或校验内容哈希；附件完整性仍由下载校验、备份及恢复验证负责。

全部 `document_asset` 记录都是保留依据，包括历史产品版本、解析失败和未发布文档。执行要求至少有一个已引用对象，且所有引用都能找到普通文件；空库或缺失文件仅允许检查，防止挂错对象目录后直接删除。无引用的全空环境须单独走受控下线流程。

## 并发和中断

写入在落盘前取得共享 PostgreSQL 事务 advisory lock，并持有到元数据提交或回滚。清理使用同一数据库内的独占锁，先取锁再读取引用；取不到锁立即退出，不与正在写入的文件竞争。清理期间新写入等候事务锁，繁忙系统应选择维护窗口，避免上传等待超时。

命令只遍历标准组织 UUID 目录和标准对象 UUID、临时文件名；文件及组织目录均不跟随符号链接。删除前复核设备、inode、大小、修改和元数据时间，任何变化都停止执行。该机制不防御拥有服务账号文件权限的恶意并发进程；运维必须保证根目录独占和恢复停止。

日志以 0600 排他创建，目录项和逐条日志均刷新至磁盘。每次删除之前先写 `candidate`，删除并刷新目录后再写 `deleted`；完整结束才有 `complete`。中途异常可能已删除前面的候选文件，不能把异常当作整批回滚。只有 `candidate` 而没有 `deleted` 的最后一项需要重新扫描确认，不能根据日志推断已删除或仍存在。修复原因后使用新日志路径重试。

当前实现为显式运维命令，没有自动定时删除。生产排程、异地备份及 S3/OSS 清理须另行验收。
