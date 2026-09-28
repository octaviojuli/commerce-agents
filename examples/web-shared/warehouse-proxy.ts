const upstream =
  process.env.WAREHOUSE_API_INTERNAL_URL ?? "http://127.0.0.1:8005";
const MAX = 10 * 1024 * 1024;
export async function proxyWarehouse(request: Request, path: string[]) {
  if (
    path[0] !== "v1" ||
    path.some((part) => part === "." || part === ".." || part.includes("/"))
  )
    return new Response(null, { status: 404 });
  const headers = new Headers();
  for (const key of [
    "authorization",
    "x-organization-id",
    "content-type",
    "idempotency-key",
    "x-file-name",
  ]) {
    const value = request.headers.get(key);
    if (value) headers.set(key, value);
  }
  const documentUpload = path.length === 2 && path[1] === "documents";
  const advisorUpload = path.length === 5 && path[1] === "copilot" &&
    path[2] === "deals" && path[4] === "assets";
  // Multipart overhead is separate from the API's 20 MB file limit.
  const maxBytes = request.method === "POST" && (documentUpload || advisorUpload)
    ? 20_100_000 : MAX;
  let body: Uint8Array | undefined;
  if (request.body) {
    const reader = request.body.getReader(),
      chunks: Uint8Array[] = [];
    let size = 0;
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.length;
      if (size > maxBytes) {
        await reader.cancel();
        return Response.json(
          { message: `文件超过 ${maxBytes === MAX ? "10" : "20"} MB 限制` },
          { status: 413 },
        );
      }
      chunks.push(value);
    }
    body = new Uint8Array(size);
    let at = 0;
    for (const chunk of chunks) {
      body.set(chunk, at);
      at += chunk.length;
    }
  }
  try {
    const response = await fetch(
      `${upstream.replace(/\/$/, "")}/${path.map(encodeURIComponent).join("/")}${new URL(request.url).search}`,
      {
        method: request.method,
        headers,
        body: body as BodyInit | undefined,
        cache: "no-store",
        redirect: "error",
        signal: request.signal,
      },
    );
    const output = new Headers({
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
      "X-Accel-Buffering": "no",
    });
    for (const key of ["content-type", "content-disposition", "retry-after", "x-request-id"]) {
      const value = response.headers.get(key);
      if (value) output.set(key, value);
    }
    return new Response(response.body, {
      status: response.status,
      headers: output,
    });
  } catch {
    return Response.json(
      {
        message:
          "云仓服务暂时无法连接，请稍后重试；提交过的变更请先核对审批状态。",
      },
      { status: 502 },
    );
  }
}
