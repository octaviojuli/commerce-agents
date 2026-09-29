"""Bounded in-process turn ownership independent of an advisor's SSE connection."""

import asyncio
from contextlib import aclosing


class DeliveryPool:
    def __init__(self, limit=32):
        self.limit = limit
        self.reserved = 0
        self.tasks = set()

    def claim(self):
        if self.reserved >= self.limit:
            return False
        self.reserved += 1
        return True

    def release(self):
        self.reserved -= 1

    def start(self, source):
        queue = asyncio.Queue(maxsize=128)
        connected = True
        done = asyncio.Event()

        async def produce():
            nonlocal connected
            try:
                async with aclosing(source):
                    async for value in source:
                        if connected:
                            try:
                                queue.put_nowait(value)
                            except asyncio.QueueFull:
                                # Slow readers reconnect to the durable transcript. Never
                                # let them block the model's persistence or grow memory.
                                connected = False
            finally:
                done.set()
                self.release()

        task = asyncio.create_task(produce())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

        async def subscribe():
            nonlocal connected
            try:
                while connected:
                    if not queue.empty():
                        yield queue.get_nowait()
                    elif done.is_set():
                        break
                    else:
                        try:
                            yield await asyncio.wait_for(queue.get(), timeout=1)
                        except TimeoutError:
                            continue
            finally:
                connected = False

        return subscribe()

    async def close(self):
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
