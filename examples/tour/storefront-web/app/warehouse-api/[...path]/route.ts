import { proxyWarehouse } from "web-shared/warehouse-proxy";
export const dynamic = "force-dynamic";
async function proxy(
  request: Request,
  context: { params: Promise<{ path: string[] }> },
) {
  return proxyWarehouse(request, (await context.params).path);
}
export { proxy as GET, proxy as POST, proxy as PATCH };
