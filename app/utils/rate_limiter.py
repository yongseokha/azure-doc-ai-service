import asyncio
import time
from collections import defaultdict, deque


class SlidingWindowRateLimiter:
    """키(key)별로 주어진 기간(period_seconds) 동안 max_calls번까지만 통과시킨다.

    한도를 넘으면 거부하지 않고, 가장 오래된 호출이 기간을 벗어날 때까지 대기(스로틀)한다.
    """

    def __init__(self, max_calls: int, period_seconds: float):
        self._max_calls = max_calls
        self._period = period_seconds
        self._timestamps: dict[str, deque[float]] = defaultdict(deque)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def acquire(self, key: str) -> None:
        async with self._locks[key]:
            while True:
                now = time.monotonic()
                timestamps = self._timestamps[key]
                while timestamps and now - timestamps[0] >= self._period:
                    timestamps.popleft()
                if len(timestamps) < self._max_calls:
                    timestamps.append(now)
                    return
                await asyncio.sleep(timestamps[0] + self._period - now)

    def forget(self, key: str) -> None:
        """key에 대한 호출 이력을 정리한다. job이 끝난 뒤 호출해서 메모리가 계속 쌓이지 않게 한다."""
        self._timestamps.pop(key, None)
        self._locks.pop(key, None)
