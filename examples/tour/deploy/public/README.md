# ECS 员工访问入口

顾问直接打开 **https://8.130.118.212**，使用自己的 ERP 账号登录。浏览器不需要 SSH 隧道。原来 `http://8.130.118.212` 和 `http://8.130.118.212:8080` 的业务保持原路由，因此应分享带 `https://` 的完整地址。

## 运行结构

| 内容 | 位置 |
| --- | --- |
| 当前发布 | `/opt/tour-agent/releases/20260914-public-01` |
| 公网 HTTPS 入口 | 原有 `deploy-caddy-1`，宿主机 443 端口 |
| 入口配置 | `/opt/ai-agent-h5/deploy/Caddyfile.ip`；本目录 `Caddyfile.edge` 保存部署快照 |
| 应用代理 | `172.18.0.1:18081`，仅在现有 Docker 网桥地址监听 |
| 前端 | `127.0.0.1:18084` |
| API | `127.0.0.1:18004` |
| 配置 | `/opt/tour-agent/config/runtime.env`，root 所有、权限 600 |
| 会话、记忆、线路文档 | `/opt/tour-agent/state` |

Compose 项目名保留为 `tour-internal`，容器和持久化路径沿用同一套。API 镜像 `local/tour-api:20260914-public-01` 继承 `20260913-public-02`，只更新 `agent_config.py` 及对应测试。共享 Python 包、依赖、前端和代理配置不变；前端与代理仍使用 `/opt/tour-agent/releases/20260913-public-02` 中的文件，前端 API 地址仍为 `https://8.130.118.212`。

本版基于 `6a7a705` 及本地部署修正，更新回答边界：按线路文档回答并标明依据，未记载的信息交由计调确认，背景介绍与旅行社承诺分开，对易变化和高风险的信息不凭模型记忆给出结论。发布目录的 `release-manifest.json` 记录源码校验值及沿用的前端构建编号。构建上下文不包含 `.env` 或本地 `.state`。后续共享包、依赖或其他 API 文件变化时，应相应扩大镜像更新范围。

员工 API（含线路目录）要求有效会话及尚未过期的 ERP 登录。匿名、伪造或已登出的会话返回 401；登录、健康检查、登录状态及带独立访问令牌的客户分享接口保留各自访问规则。服务未配置 `TOUR_ERP_STORE_NAME`，因此占位下单仍被后端拒绝。

## HTTPS 与续期

入口使用 Let's Encrypt 签发的 IP 证书，覆盖 `8.130.118.212`。`default_sni` 使未发送域名 SNI 的 IP 访问客户端也能选择该证书。证书验证路径使用原 80 端口的 `/.well-known/acme-challenge/`，其余 HTTP 路由保持不变。

证书保存在原 Caddy 数据卷的 `tour-letsencrypt/` 下，验证文件在独立的 `tour-acme/` 下；私钥不进入代码库。`tour-certificate-renew.timer` 每天两次检查续期，带随机延迟，`renew-certificate.sh` 在成功检查后强制热加载 Caddy 中的证书。

```bash
systemctl status tour-certificate-renew.timer
systemctl show tour-certificate-renew.service -p Result -p ExecMainStatus
journalctl -u tour-certificate-renew.service
```

IP 证书采用短有效期配置，必须保持续期任务和 80 端口验证路径可用。说明：[Let's Encrypt IP 证书](https://letsencrypt.org/2026/03/11/shorter-certs-certbot)。

## 验收

- 585 项 tour 后端测试通过、1 项跳过；代码风格、仓库一致性检查、前端沿用此前通过生产构建与 TypeScript 检查的同一产物。
- 从用户电脑通过公网 HTTPS 完成 ERP 登录、目录读取和模型对话；目录加载 84 条线路，模型返回“连接正常”，并通过附件缺失信息的回答规则验收；两轮流式事件均完整结束，总耗时约 7.9 秒。
- 未登录及登出后的目录访问均返回 401，模拟会话入口返回 403。测试会话已登出，未创建 ERP 订单。
- 浏览器直接打开公网 HTTPS 登录页成功，证书校验保持开启。
- 证书续期演练成功，系统续期定时器已启用。
- 原 80/8080 业务入口返回 HTTP 200，原业务容器保持运行。

当前接口验收数据见 `acceptance.json`。证书有效期和服务状态应以实时检查为准。

## 更新和回退

在当前发布目录运行 `docker compose up -d --no-build --no-deps --wait --wait-timeout 180 api`。新前端产物应在本地或构建机完成，再上传到新的发布目录；API 镜像与配置分开管理。升级后验证公网 HTTPS，而非仅检查回环健康接口。当前版本上线前的会话数据库通过 SQLite backup 保存于 `/opt/tour-agent/backups/before-20260914-public-01`，同目录保留员工记忆和上一版 Compose 配置。

回退应用时，在 `/opt/tour-agent/releases/20260913-public-02` 运行 `docker compose up -d --no-build --no-deps --wait --wait-timeout 180 api`。该版本保留员工接口登录校验，使用现有持久化数据和公网入口。

入口修改前的配置保存在 `/opt/tour-agent/public/Caddyfile.before-public`；开启 HTTPS 前的配置保存在 `Caddyfile.before-https`。恢复网关配置后必须执行 `caddy validate` 再热加载。不要在保留公网转发的情况下回退到缺少员工接口登录校验的旧 API。持久化目录不随版本回退删除。
