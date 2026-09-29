"""Isolated HTTP capacity server; credentials arrive only through the parent's pipe."""

import json
import os
import socket
import sys
import time

import uvicorn
from sqlalchemy.engine import make_url

from cloud_warehouse.api import create_app
from cloud_warehouse.database_pool import pooled_engine


def timed_application(app, server_index=0):
    """Fixture-only timing, from ASGI receipt to response headers, not full wire latency."""

    async def timed_app(scope, receive, send):
        if scope["type"] != "http":
            return await app(scope, receive, send)
        started = time.perf_counter()

        async def timed_send(message):
            if message["type"] == "http.response.start":
                elapsed = (time.perf_counter() - started) * 1000
                message = {
                    **message,
                    "headers": [
                        *message.get("headers", []),
                        (b"x-capacity-server-ms", f"{elapsed:.3f}".encode()),
                        (b"x-capacity-server-index", str(server_index).encode()),
                    ],
                }
            await send(message)

        await app(scope, receive, timed_send)

    return timed_app


def fixture_urls(config):
    if set(config) != {"runtime", "authentication"}:
        raise ValueError("Invalid HTTP fixture configuration")
    urls = {key: make_url(value) for key, value in config.items()}
    for url in urls.values():
        if (
            url.drivername != "postgresql+psycopg"
            or url.host not in {"127.0.0.1", "::1", "localhost"}
            or not url.database
            or not url.database.startswith("warehouse_ci_")
            or not url.database.endswith("_test")
        ):
            raise ValueError("HTTP capacity requires disposable loopback databases")
    if urls["runtime"].database != urls["authentication"].database:
        raise ValueError("HTTP capacity roles must share one fixture database")
    return urls


def process_pool_size(processes):
    if type(processes) is not int or processes not in (1, 2, 4, 8):
        raise ValueError("Capacity processes must divide the fixed pool budget of eight")
    return 8 // processes


class CapacityServer(uvicorn.Server):
    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        if self.started:
            print("CAPACITY_READY", flush=True)


def main():
    # A descriptor inherited from the parent is already bound to loopback. No public
    # bind option or application environment/configuration is accepted by this helper.
    with socket.socket(fileno=int(sys.argv[1])) as listener:
        if listener.getsockname()[0] != "127.0.0.1":
            raise ValueError("HTTP capacity listener must use loopback")
        processes, index = int(sys.argv[2]), int(sys.argv[3])
        pool_size = process_pool_size(processes)
        if not 0 <= index < processes:
            raise ValueError("Invalid capacity server index")
        urls = fixture_urls(json.loads(sys.stdin.read(32768)))
        runtime = pooled_engine(urls["runtime"], max_size=pool_size)
        authentication = pooled_engine(urls["authentication"], max_size=pool_size)
        try:
            app = timed_application(create_app(runtime, authentication), index)
            CapacityServer(
                uvicorn.Config(
                    app, access_log=False, log_level="critical", timeout_graceful_shutdown=5
                )
            ).run(sockets=[listener])
        finally:
            runtime.dispose()
            authentication.dispose()


if __name__ == "__main__":
    os.umask(0o077)
    try:
        main()
    except Exception:
        # SQL/URL details never leave this child, even if initialization fails.
        raise SystemExit("HTTP_CAPACITY_SERVER_FAILED") from None
