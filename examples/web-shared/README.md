# Shared web components

The example applications import this local npm workspace for role-neutral UI,
streamed conversation rendering, page shells, and translated interface copy.
`index.ts` exposes the shared public components; each application supplies its domain
cards and business copy.

Tour's cloud modes also share two direct module imports:

- `warehouse-client.ts`: organization-scoped Bearer requests, login-expiry errors,
  downloads, and SSE decoding with stream-reader cleanup. It holds no ERP credentials.
- `warehouse-proxy.ts`: a server-only same-origin proxy to
  `WAREHOUSE_API_INTERNAL_URL` (default `http://127.0.0.1:8005`). It forwards explicit
  authorization and organization headers, limits request bodies to 10 MB, and disables
  response caching and upstream redirects. Import it only from server route handlers.
  The API-generated `X-Request-ID` response header is preserved for diagnostics; caller
  request IDs are not forwarded, and upstream connection failures do not invent one.
  Run its Node 22 contracts with `node --experimental-strip-types --test warehouse-proxy.test.mjs`.

The legacy `AgentApi` contract remains available for the original demos. Cloud advisor
applications adapt the conversation transport and disable legacy cart/order/memory
reads rather than treating an ERP session as a platform identity.

`route-detail.tsx` 渲染 `route-kit/1` 的对客行程或明确标注的商户审核稿。审核模式才读取引用、原文和问题记录。`route-cover.ts` 通过现有认证客户端读取封面，并在权限失效或卸载时撤销本地图片地址。`route-kit-schema.json` 由技能包模型导出，用于商户字段表单。
