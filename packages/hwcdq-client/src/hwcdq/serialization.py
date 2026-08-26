"""Small priority-aware async serializer for application transactions."""

from __future__ import annotations

import asyncio
import heapq
import itertools
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator


@dataclass(order=True, slots=True)
class _Waiter:
    priority: int
    sequence: int
    future: asyncio.Future[None] = field(compare=False)


class PrioritySerializer:
    """Permit one owner; select the lowest-priority-number waiter next."""

    def __init__(self) -> None:
        self._active = False
        self._waiters: list[_Waiter] = []
        self._sequence = itertools.count()
        self._guard = asyncio.Lock()

    @property
    def busy(self) -> bool:
        return self._active or any(not item.future.done() for item in self._waiters)

    async def acquire(self, priority: int = 10) -> None:
        loop = asyncio.get_running_loop()
        waiter: _Waiter | None = None
        async with self._guard:
            if not self._active and not self._waiters:
                self._active = True
                return
            future: asyncio.Future[None] = loop.create_future()
            waiter = _Waiter(priority, next(self._sequence), future)
            heapq.heappush(self._waiters, waiter)
        try:
            await waiter.future
        except BaseException:
            # Lazy removal keeps cancellation O(1); release() skips completed
            # waiters before transferring ownership.
            if waiter.future.done() and not waiter.future.cancelled():
                # Ownership can be transferred immediately before the waiting
                # task is cancelled.  Hand it onward asynchronously so the
                # serializer cannot remain wedged without an owner.
                asyncio.get_running_loop().create_task(self.release())
            elif not waiter.future.done():
                waiter.future.cancel()
            raise

    async def release(self) -> None:
        async with self._guard:
            if not self._active:
                raise RuntimeError("serializer released without an owner")
            while self._waiters:
                waiter = heapq.heappop(self._waiters)
                if waiter.future.done():
                    continue
                waiter.future.set_result(None)
                return
            self._active = False

    @asynccontextmanager
    async def slot(self, priority: int = 10) -> AsyncIterator[None]:
        await self.acquire(priority)
        try:
            yield
        finally:
            await self.release()


__all__ = ["PrioritySerializer"]
