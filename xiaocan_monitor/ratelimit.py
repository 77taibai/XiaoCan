"""跨进程持久化的最小请求间隔限流。"""

from __future__ import annotations

import time
from typing import Callable


class RateLimiter:
    """在每次请求前调用 ``wait()``，保证任意两次请求间隔不小于阈值。

    ``store`` 只需实现 ``load_state() -> dict`` 与 ``save_state(dict) -> None``。
    时钟与 ``sleep`` 可注入以便测试；默认使用墙钟 ``time.time`` 以跨进程生效。
    """

    def __init__(
        self,
        store,
        min_interval: float,
        *,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._min_interval = float(min_interval)
        self._sleep = sleep
        self._now = now

    def wait(self) -> None:
        state = self._store.load_state()
        if not isinstance(state, dict):
            state = {}

        last = state.get("last_request_epoch")
        if isinstance(last, (int, float)) and not isinstance(last, bool):
            remaining = self._min_interval - (self._now() - last)
            if remaining > 0:
                self._sleep(remaining)

        state["last_request_epoch"] = self._now()
        self._store.save_state(state)
