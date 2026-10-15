"""跨进程持久化的最小请求间隔限流。

保证任意两次「实际请求」之间间隔不小于阈值：状态「读取 → sleep → 写回」全程
持有一个跨进程互斥锁文件（``O_CREAT|O_EXCL`` 创建，带陈旧超时），因此即使
cron 重叠或多进程并发，也不会有两个请求同时读到旧时间戳而同时放行。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable, Optional


class _StateMutex:
    """跨进程互斥锁：以 O_CREAT|O_EXCL 独占创建锁文件；陈旧锁可被回收。"""

    def __init__(self, path: Path, stale_seconds: float = 60.0, poll: float = 0.05):
        self.path = Path(path)
        self.stale_seconds = float(stale_seconds)
        self.poll = float(poll)
        self._owned = False

    def __enter__(self) -> "_StateMutex":
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        deadline = time.time() + 30.0
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if self._is_stale():
                    self._remove()
                    continue
                time.sleep(self.poll)
                continue
            except OSError:
                if time.time() > deadline:
                    raise
                time.sleep(self.poll)
                continue
            try:
                os.write(fd, str(os.getpid()).encode("utf-8"))
            except OSError:
                os.close(fd)
                self._remove()
                raise
            os.close(fd)
            self._owned = True
            return self

    def _is_stale(self) -> bool:
        try:
            age = time.time() - os.path.getmtime(str(self.path))
        except OSError:
            return True
        return age > self.stale_seconds

    def _remove(self) -> None:
        try:
            os.remove(str(self.path))
        except OSError:
            pass

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._owned:
            self._remove()
            self._owned = False
        return None


class RateLimiter:
    """在每次请求前调用 ``wait()``，保证任意两次请求间隔不小于阈值。

    ``store`` 只需实现 ``load_state() -> dict`` 与 ``save_state(dict) -> None``。
    时钟与 ``sleep`` 可注入以便测试；默认使用墙钟 ``time.time`` 以跨进程生效。
    ``lock_path`` 为跨进程状态锁文件；缺省时由 ``store.state_path`` 推导，
    推导不出则仅做进程内限流。
    """

    def __init__(
        self,
        store,
        min_interval: float,
        *,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.time,
        lock_path: Optional[Path] = None,
    ) -> None:
        self._store = store
        self._min_interval = float(min_interval)
        self._sleep = sleep
        self._now = now
        if lock_path is not None:
            self._lock_path: Optional[Path] = Path(lock_path)
        else:
            state_path = getattr(store, "state_path", None)
            self._lock_path = (
                Path(state_path).with_name(Path(state_path).name + ".ratelimit.lock")
                if state_path
                else None
            )
        self._stale_seconds = max(60.0, self._min_interval * 5.0)

    def wait(self) -> None:
        if self._lock_path is None:
            self._wait_unlocked()
            return
        with _StateMutex(self._lock_path, stale_seconds=self._stale_seconds):
            self._wait_unlocked()

    def _wait_unlocked(self) -> None:
        state = self._store.load_state()
        if not isinstance(state, dict):
            state = {}

        last = state.get("last_request_epoch")
        if isinstance(last, (int, float)) and not isinstance(last, bool):
            remaining = self._min_interval - (self._now() - last)
            if remaining > 0:
                self._sleep(remaining)

        fresh = self._store.load_state()
        if not isinstance(fresh, dict):
            fresh = {}
        fresh["last_request_epoch"] = self._now()
        self._store.save_state(fresh)
