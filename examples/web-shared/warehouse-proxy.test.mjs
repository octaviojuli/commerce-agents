import assert from "node:assert/strict";
import test from "node:test";
import { proxyWarehouse } from "./warehouse-proxy.ts";

test("operations metrics are not exposed through the employee web proxy", async (t) => {
  const upstream = t.mock.method(globalThis, "fetch", async () => {
    throw new Error("metrics proxy must not contact the upstream");
  });
  const response = await proxyWarehouse(new Request("http://acme.example/warehouse-api/metrics"), ["metrics"]);
  assert.equal(response.status, 404);
  assert.equal(upstream.mock.callCount(), 0);
});

test("API request ID survives both normal and failed responses without forwarding caller IDs", async (t) => {
  for (const status of [200, 403, 500]) {
    const requestId = "fb6bdc0a-42c3-4429-b3bd-7613b69d4f3d";
    const body = JSON.stringify({ code: "ACME_TEST" });
    const mocked = t.mock.method(globalThis, "fetch", async (_url, init) => {
      assert.equal(init.headers.get("x-request-id"), null);
      assert.equal(init.headers.get("authorization"), "Bearer ACME-token");
      return new Response(body, { status, headers: { "X-Request-ID": requestId } });
    });
    const response = await proxyWarehouse(new Request("http://acme.example/warehouse-api/v1/products", {
      headers: { "X-Request-ID": "ACME-untrusted", Authorization: "Bearer ACME-token" },
    }), ["v1", "products"]);
    assert.equal(response.status, status);
    assert.equal(response.headers.get("x-request-id"), requestId);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.equal(await response.text(), body);
    mocked.mock.restore();
  }
});

test("stream response keeps request ID and forwards chunks before completion", async (t) => {
  let stream;
  const upstream = new ReadableStream({ start(controller) { stream = controller; } });
  t.mock.method(globalThis, "fetch", async () => new Response(upstream, {
    headers: { "X-Request-ID": "stream-id", "Content-Type": "text/event-stream" },
  }));
  const response = await proxyWarehouse(new Request("http://acme.example/warehouse-api/v1/chat"), ["v1", "chat"]);
  assert.equal(response.headers.get("x-request-id"), "stream-id");
  assert.equal(response.headers.get("x-accel-buffering"), "no");
  const reader = response.body.getReader();
  stream.enqueue(new TextEncoder().encode("data: ACME\n\n"));
  assert.equal(new TextDecoder().decode((await reader.read()).value), "data: ACME\n\n");
  stream.close();
  assert.equal((await reader.read()).done, true);
});

test("proxy connection failure does not invent an API request ID", async (t) => {
  t.mock.method(globalThis, "fetch", async () => { throw new Error("ACME-private-upstream"); });
  const response = await proxyWarehouse(new Request("http://acme.example/warehouse-api/v1/products"), ["v1", "products"]);
  assert.equal(response.status, 502);
  assert.equal(response.headers.get("x-request-id"), null);
  assert.ok(!(await response.text()).includes("ACME-private-upstream"));
});
