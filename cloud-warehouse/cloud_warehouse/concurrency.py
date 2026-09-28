"""Run complete synchronous business operations outside the request event loop."""

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import ParamSpec, TypeVar

from starlette.concurrency import run_in_threadpool

P = ParamSpec("P")
T = TypeVar("T")


def in_worker_thread(operation: Callable[P, T]) -> Callable[P, Awaitable[T]]:
    """Adapt sync methods to async role protocols; never pass an open transaction out."""

    @wraps(operation)
    async def run(*args: P.args, **kwargs: P.kwargs) -> T:
        return await run_in_threadpool(operation, *args, **kwargs)

    return run
