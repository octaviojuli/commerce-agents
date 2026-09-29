# 顾问移动工作台验收

本轮依据 `advisor-workbench-fixes.md` 实施。H5 仍使用既有云仓业务服务，不开放上游订单、占位或支付。手机真机结果由项目负责人补充；浏览器截图不替代微信验收。

## 功能与证据

| 清单 | 实现及验证 |
|---|---|
| M1 | 小于 768px 为会话列表 / 单屏对话；底部需求单；中屏两栏、宽屏三栏。375px 顶部固定内容实测 92px，无横向溢出。按所有者搜索会话标题、显示阶段。 |
| M2 | 服务端保留字符串建议，校验可见编号与阶段；建议栏、主按钮不小于 44px；每张卡一个主操作。 |
| M3 | 日期窗口内优先，再按日期和 UUID 分页；游标绑定用户、组织、线路、人数与日期。30 个乱序团期测试确认前三条在窗口内，无重漏，换范围拒绝旧游标。窗口外显示真实总数并折叠。 |
| M4 | 控件字号至少 16px；视口 / visualViewport / 安全区；最多五行输入，移动端回车换行；步进器、年龄选择、日期、房型和偏好在弹层编辑。 |
| M5 | localStorage 失败退回内存；会话滑动七天且保留即时吊销。后台生成由有上限的服务端任务持有，恢复时最多轮询 90 秒；真实 ASGI 断连与进程关闭测试分别验证完成 / 中断。复制三级兜底已模拟到长按复制。 |
| M6 | 模型不能覆盖顾问字段，同批其他字段仍保存；冲突按钮按需求版本保护。已验证顾问主动采用建议。未保存线下备注在刷新时保留。 |
| M7 | 单列线路，首批三条，理由折叠，卡片内加载更多。真实模型验收移除了重复通用商品卡及独立搜索入口，避免模型另加硬条件。 |
| M8 | 检索与询价就绪度分别提示推断项；报价失效进入对话提示并提供重新询价。单方案自动选择后可再次询价。 |
| M9 | 必需目的地与举例分别保存；举例只影响排序与理由。模型工具只能使用保存的条件；出发窗口不等于旅行时长。 |
| M10 | 首页实际脚本 gzip 合计 244,315 字节（238.6 KiB），低于 250 KiB；次要页面按需加载，无网络字体。ECS 模拟慢网下登录输入框可输入为 1,859ms。 |
| M11 | 隔离分支 `codex/advisor-mobile` 保存提交。既有 WP1–WP5 原为未提交基线，统一保留为 `d8a11dd`，没有伪造历史拆分；本轮按主题独立提交。两个必要共享契约另以 `d5cc867` 提交，未纳入其他 Travel 改动。 |
| M12 | 见 [ECS 部署记录](ecs-acceptance.md)。异地恢复、迁移、Linux 镜像与真实业务验收分别记录。 |

性能测试为桌面 Chromium：150ms 延迟、1.6Mbps 下行、750Kbps 上行、CPU 四倍降速、禁用缓存；测量“登录输入框已可填写”，不是 Lighthouse TTI，也不代表手机蜂窝网络。

## 浏览器矩阵

以下均为 ACME 虚构业务的 Chromium 视口模拟。分享密钥已遮盖；客户页没有同行结算价、供应源身份或具体库存。

| 状态 | 375×667 | 390×844 | 360×780 |
|---|---|---|---|
| 需求 | [截图](advisor/mobile/need-375.png) | [截图](advisor/mobile/need-390.png) | [截图](advisor/mobile/need-360.png) |
| 线路 | [截图](advisor/mobile/routes-375.png) | [截图](advisor/mobile/routes-390.png) | [截图](advisor/mobile/routes-360.png) |
| 团期 | [截图](advisor/mobile/departures-375.png) | [截图](advisor/mobile/departures-390.png) | [截图](advisor/mobile/departures-360.png) |
| 报价 | [截图](advisor/mobile/quote-375.png) | [截图](advisor/mobile/quote-390.png) | [截图](advisor/mobile/quote-360.png) |
| 失效 | [截图](advisor/mobile/stale-375.png) | [截图](advisor/mobile/stale-390.png) | [截图](advisor/mobile/stale-360.png) |
| 分享 | [截图](advisor/mobile/share-375.png) | [截图](advisor/mobile/share-390.png) | [截图](advisor/mobile/share-360.png) |
| 线下记录 | [截图](advisor/mobile/offline-375.png) | [截图](advisor/mobile/offline-390.png) | [截图](advisor/mobile/offline-360.png) |

[深色模式](advisor/mobile/dark-375.png)、[缩小视口的键盘布局模拟](advisor/mobile/keyboard-simulation.png)、[长按复制兜底](advisor/mobile/copy-fallback.png)、[客户报价](advisor/mobile/customer-360.png)、[ECS 弱网首屏](advisor/mobile/slow4g-ecs.png)。键盘模拟没有操作真实软键盘。

## 尚待真机补充

- iOS Safari、iOS 微信、Android 微信：聚焦是否缩放、输入框是否被键盘遮挡、切后台 30 秒后恢复、复制并粘贴到微信。
- 微信 X5 / XWeb 流式显示；真实手机网络检索、团期、询价延迟。
- 小程序开发者工具未安装，未声称通过；真机 web-view 待业务域名、证书、备案和校验文件。当前 IP 入口供普通 H5 测试。

## 发布和回退

初始代码标签 `advisor-mobile-20260927-03`；真实模型修复标签 `advisor-mobile-20260927-04`。修复限制模型搜索为需求单中的条件，移除通用商品二次推荐；改写提示明确出发窗口含义。网页源与镜像不变。

跨 `0037` / `0038` 回退：停止 API 和所有写入 Worker，用 `/opt/tour-warehouse/backups/pre-0038-advisor-mobile` 恢复至新空库和新附件目录，执行 verify-restore，撤销旧会话、暂停恢复调度，按恢复手册重新配置私有连接并切回 API `20260926-13`、顾问 `20260926-01`、商户 `20260926-07`。保留现有库和对象目录供核对，不在原库反向迁移。仅撤回检索补丁可回到 API `20260927-03`，数据库仍为 `0038`。


最终回归：云仓数据库、Tour 接入和 Agent 共 662 passed，zero skips；ruff、格式、TypeScript、派生提示词及 Linux 隔离镜像验收通过。ECS 真实报价来源缺少费用范围与房型确认，三组均禁止分享；这一业务缺口和真机待办不计为通过。
