export type Organization = {
  id: string;
  name: string;
  kinds: string[];
  roles: string[];
};
export type WarehouseEvent = { type: string; data: Record<string, any> };

/** Strict SSE parsing with reader cleanup for warehouse conversation streams. */
export async function* warehouseEvents(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<WarehouseEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let boundary: RegExpExecArray | null;
      while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
        const frame = buffer.slice(0, boundary.index);
        buffer = buffer.slice(boundary.index + boundary[0].length);
        const lines = frame.split(/\r?\n/);
        const type = lines
          .find((line) => line.startsWith("event:"))
          ?.slice(6)
          .trim();
        const raw = lines
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart())
          .join("\n");
        if (type && raw) yield { type, data: JSON.parse(raw) };
      }
      if (done) break;
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}
export class WarehouseClient {
  constructor(
    public token: string,
    public organization: string = "",
  ) {}
  headers(extra: Record<string, string> = {}) {
    return {
      ...(this.token ? { Authorization: `Bearer ${this.token}` } : {}),
      ...(this.organization ? { "X-Organization-Id": this.organization } : {}),
      ...extra,
    };
  }
  async response(path: string, init: RequestInit = {}) {
    const response = await fetch(`/warehouse-api/v1${path}`, {
      ...init,
      headers: this.headers(init.headers as Record<string, string>),
      cache: "no-store",
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      if (response.status === 401)
        window.dispatchEvent(new Event("warehouse-expired"));
      throw new ApiError(
        typeof data.message === "string"
          ? data.message
          : typeof data.detail === "string"
            ? data.detail
            : "操作未完成，请刷新后核对状态。",
        response.status,
      );
    }
    return response;
  }
  async get<T>(path: string, signal?: AbortSignal): Promise<T> {
    return (await this.response(path, { signal })).json();
  }
  async post<T = any>(
    path: string,
    data?: unknown,
    extra: Record<string, string> = {},
  ): Promise<T> {
    const response = await this.response(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...extra },
      body: data === undefined ? undefined : JSON.stringify(data),
    });
    return response.status === 204 ? (undefined as T) : response.json();
  }
  async download(path: string, name: string) {
    const data = await (await this.response(path)).blob();
    const url = URL.createObjectURL(data);
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}
export function message(error: unknown) {
  return error instanceof Error ? error.message : "操作未完成";
}
