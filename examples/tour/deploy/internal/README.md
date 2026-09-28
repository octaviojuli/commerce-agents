# Tour ECS 内部测试

员工直接访问的部署入口为 **https://8.130.118.212**，见 [`../public/README.md`](../public/README.md)。本目录保留仅经 SSH 隧道访问的部署变体。

本配置运行真实 ERP 模式的 `examples/tour`。前端、API 和 Caddy 使用主机网络，全部绑定 `127.0.0.1`；公网不开放应用端口。浏览器通过 SSH 隧道访问同一来源，聊天流由 Caddy 即时转发。

## 目录与端口

| 内容 | ECS 路径或地址 |
| --- | --- |
| 发布目录 | `/opt/tour-agent/releases/20260913-internal-01` |
| 运行配置 | `/opt/tour-agent/config/runtime.env`，目录 `700`、文件 `600` |
| 持久化数据 | `/opt/tour-agent/state` |
| 访问入口 | `127.0.0.1:18080` |
| 前端 | `127.0.0.1:18084` |
| API | `127.0.0.1:18004` |

发布包包含当前工作区的 tour 与所需共享代码，以及线路解析文档。历史会话、顾问记忆、本地 `.env` 和本地依赖均不进入 API 镜像。配置在服务器单独提供；缺少配置文件时 Compose 拒绝启动。

## 访问

在已有 ECS SSH 密钥的 Mac 上运行本目录的 `open-tunnel.sh`，随后打开 <http://localhost:18080>。该命令保持前台运行，按 Ctrl-C 关闭隧道；ECS 服务继续运行。每个顾问使用自己的 ERP 账号登录。客户分享页同样需要隧道，不能直接发给公网客户。

## 构建和运行

构建上下文是发布目录，包含 `src/` 源码及 `web/` 前端产物。前端在隔离副本构建：Next 配置使用 `output: "standalone"` 和 `outputFileTracingRoot: path.join(__dirname, "../../")`，构建命令为 `NEXT_PUBLIC_API_URL=http://localhost:18080 npm run build --workspace acme-tour-storefront-web -- --webpack`。将 `.next/standalone/` 复制为 `web/`，并补入 `tour/storefront-web/.next/static/` 和 `public/`。前端构建产物已在 Linux Node 容器中核验依赖加载。

在服务器发布目录运行：

```bash
docker compose build api
docker compose up -d
docker compose ps
curl -fsS http://localhost:18080/api/health
```

Compose 配置自动重启及日志轮转，并限制各容器的内存和 CPU。API 健康检查仅证明服务启动与目录加载，不能替代模型对话和 ERP 下单验收。备份应覆盖 `state/` 和受限的运行配置；备份 SQLite 时应使用 SQLite backup API 获得一致快照。

初始配置沿用本地 ERP 客户编码与模型设置。没有设置 `TOUR_ERP_STORE_NAME`，占位下单由后端拒绝。部署验收不创建 ERP 订单。

## 停用与回退

在此发布目录执行 `docker compose down` 仅停用这套内测服务，保留 `/opt/tour-agent/state`。后续升级保留上一发布目录和镜像；回退前确认数据格式兼容，再使用上一版本 Compose 启动。不要删除持久化目录。
