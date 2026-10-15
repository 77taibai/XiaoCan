"""run 的单机互斥锁（基于 O_CREAT|O_EXCL 的 PID 文件）。"""

from __future__ import annotations

import os
import time
from pathlib import Path


class LockBusy(Exception):
    """锁被其它存活进程持有。"""


class RunLock:
    """简单的单机文件锁，上下文管理器用法。"""

    def __init__(self, path: Path, stale_seconds: int = 900):
        self.path = Path(path)
        self.stale_seconds = stale_seconds
        self._owned = False

    def __enter__(self) -> "RunLock":
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        for attempt in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if attempt == 0 and self._is_stale():
                    self._remove_stale()
                    continue
                raise LockBusy("已有另一次 run 正在执行（锁文件：%s）" % self.path)
            except OSError as exc:
                raise LockBusy("无法获取锁文件 %s：%s" % (self.path, exc)) from exc
            try:
                os.write(fd, str(os.getpid()).encode("utf-8"))
            finally:
                os.close(fd)
            self._owned = True
            return self
        raise LockBusy("已有另一次 run 正在执行（锁文件：%s）" % self.path)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._owned:
            try:
                os.remove(str(self.path))
            except FileNotFoundError:
                pass
            except OSError:
                pass
            finally:
                self._owned = False
        return None

    def _read_pid(self):
        try:
            text = self.path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            return None
        if not text:
            return None
        token = text.split()[0]
        try:
            return int(token)
        except (TypeError, ValueError):
            return None

    def _is_stale(self) -> bool:
        try:
            age = time.time() - os.path.getmtime(str(self.path))
        except OSError:
            return True
        if self.stale_seconds is not None and age >= self.stale_seconds:
            return True
        pid = self._read_pid()
        if pid is None:
            return False
        if self._pid_alive(pid) is False:
            return True
        return False

    def _remove_stale(self) -> None:
        try:
            os.remove(str(self.path))
        except FileNotFoundError:
            pass
        except OSError:
            pass

    @staticmethod
    def _pid_alive(pid):
        """判断进程是否存活；未知或非 POSIX 平台返回 None。

        注意：Windows 上 ``os.kill(pid, 0)`` 行为异常（实测会破坏后续线程创建），
        因此只在 POSIX 上做存活探测；其它平台仅依赖锁文件 mtime 判陈旧。
        """
        if os.name != "posix":
            return None
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return None
        except Exception:
            return None
        return True
