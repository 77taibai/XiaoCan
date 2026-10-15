"""monitors.json / state.json 的读写（原子写）。仅使用标准库。"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path


class StoreError(Exception):
    """存储层可读错误，供 CLI 展示。"""


def _as_int(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_float(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_str(value: object, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


@dataclass
class Monitor:
    """一条监控配置。"""

    id: str
    store_id: str
    store_name: str
    platform: int = 0
    lat: float = 39.904200
    lon: float = 116.407400
    city: str = "110100"
    note: str = ""
    enabled: bool = True
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "store_id": self.store_id,
            "store_name": self.store_name,
            "platform": self.platform,
            "lat": self.lat,
            "lon": self.lon,
            "city": self.city,
            "note": self.note,
            "enabled": self.enabled,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Monitor":
        if not isinstance(d, dict):
            raise StoreError("监控条目必须是 JSON 对象")
        return cls(
            id=_as_str(d.get("id"), ""),
            store_id=_as_str(d.get("store_id"), ""),
            store_name=_as_str(d.get("store_name"), ""),
            platform=_as_int(d.get("platform", 0), 0),
            lat=_as_float(d.get("lat", 39.904200), 39.904200),
            lon=_as_float(d.get("lon", 116.407400), 116.407400),
            city=_as_str(d.get("city", "110100"), "110100"),
            note=_as_str(d.get("note"), ""),
            enabled=bool(d.get("enabled", True)),
            created_at=_as_str(d.get("created_at"), ""),
        )


def _default_state() -> dict:
    return {"version": 1, "last_request_epoch": None, "daily": {}}


class Store:
    """监控清单与运行状态的持久化。"""

    def __init__(self, config_path: Path, state_path: Path):
        self.config_path = Path(config_path)
        self.state_path = Path(state_path)

    # ---- 清单 ----

    def load_monitors(self) -> list[Monitor]:
        """读取监控清单；文件不存在返回空列表。"""
        doc = self._read_document(self.config_path)
        if doc is None:
            return []
        if not isinstance(doc, dict):
            raise StoreError("监控清单格式错误（应为 JSON 对象）：%s" % self.config_path)
        raw = doc.get("monitors", [])
        if raw is None:
            raw = []
        if not isinstance(raw, list):
            raise StoreError("监控清单 monitors 字段应为数组：%s" % self.config_path)
        monitors = []
        for item in raw:
            if not isinstance(item, dict):
                raise StoreError("监控清单中存在无效条目：%s" % self.config_path)
            monitors.append(Monitor.from_dict(item))
        return monitors

    def save_monitors(self, monitors: list[Monitor]) -> None:
        data = {"version": 1, "monitors": [m.to_dict() for m in monitors]}
        self._atomic_write(self.config_path, data)

    # ---- 状态 ----

    def load_state(self) -> dict:
        """读取运行状态；文件不存在返回默认状态。"""
        doc = self._read_document(self.state_path)
        if doc is None:
            return _default_state()
        if not isinstance(doc, dict):
            raise StoreError("状态文件格式错误（应为 JSON 对象）：%s" % self.state_path)
        return doc

    def save_state(self, state: dict) -> None:
        self._atomic_write(self.state_path, state)

    def mark_done(self, monitor_id: str, day: str) -> None:
        """标记某监控当天已完成：读-改-写，保留其它键。"""
        state = self.load_state()
        daily = state.get("daily")
        if not isinstance(daily, dict):
            daily = {}
        daily[monitor_id] = day
        state["daily"] = daily
        self.save_state(state)

    # ---- 工具 ----

    @staticmethod
    def new_id() -> str:
        """8 位十六进制随机串。"""
        return uuid.uuid4().hex[:8]

    def _read_document(self, path: Path) -> object:
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError) as exc:
            raise StoreError("无法读取文件 %s：%s" % (path, exc)) from exc
        try:
            return json.loads(text)
        except ValueError as exc:
            raise StoreError("文件内容不是合法 JSON：%s（%s）" % (path, exc)) from exc

    def _atomic_write(self, path: Path, data: object) -> None:
        parent = path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StoreError("无法创建目录 %s：%s" % (parent, exc)) from exc
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        tmp_path = None
        try:
            fd, tmp_name = tempfile.mkstemp(
                prefix=path.name + ".", suffix=".tmp", dir=str(parent)
            )
            tmp_path = Path(tmp_name)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(tmp_path), str(path))
            tmp_path = None
        except OSError as exc:
            raise StoreError("写入文件失败 %s：%s" % (path, exc)) from exc
        finally:
            if tmp_path is not None:
                try:
                    os.remove(str(tmp_path))
                except OSError:
                    pass
