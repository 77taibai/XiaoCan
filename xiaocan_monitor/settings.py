"""配置解析：进程环境 > .env 文件 > 内置默认值。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from .proxy import Socks5Proxy, parse_proxy

DEFAULT_DINGTALK_TITLE = "小蚕余量监控"
DEFAULT_MIN_REQUEST_INTERVAL = 3.0
DEFAULT_REQUEST_TIMEOUT = 8.0
DEFAULT_RETRIES = 1
DEFAULT_LAT = 39.904200
DEFAULT_LON = 116.407400
DEFAULT_CITY = "110100"

ENV_FILE_KEY = "XIAOCAN_ENV_FILE"


def project_root() -> Path:
    """返回包含 xiaocan_monitor 包的上一级目录（项目根）。"""
    return Path(__file__).resolve().parent.parent


def _strip_quotes(value: str) -> str:
    """去掉成对包裹的单引号或双引号。"""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def parse_env_file(path: Path) -> dict[str, str]:
    """解析简单 KEY=VALUE 的 .env 文件；文件不存在或缺行时返回空字典。"""
    result: dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return result

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        result[key] = _strip_quotes(value.strip())
    return result


def _to_float(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float(default)


def _to_int(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        try:
            return int(float(value))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return int(default)


def _abs(path: object) -> Path:
    return Path(str(path)).expanduser().resolve()


@dataclass(frozen=True)
class Settings:
    dingtalk_webhook: str
    dingtalk_secret: str
    dingtalk_title: str
    min_request_interval: float
    request_timeout: float
    retries: int
    default_lat: float
    default_lon: float
    default_city: str
    socks5_proxy: str
    proxy: Optional[Socks5Proxy]
    config_path: Path
    state_path: Path
    lock_path: Path
    env_path: Path


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """读取配置。``env`` 为进程环境来源，缺省时使用 ``os.environ``。"""
    process: Mapping[str, str] = os.environ if env is None else env

    env_file_value = process.get(ENV_FILE_KEY, "")
    if env_file_value and env_file_value.strip():
        env_path = _abs(env_file_value.strip())
    else:
        env_path = _abs(project_root() / ".env")

    file_values = parse_env_file(env_path)

    def lookup(key: str, default: str) -> str:
        if key in process:
            return process[key]
        if key in file_values:
            return file_values[key]
        return default

    config_value = lookup("XIAOCAN_CONFIG_PATH", "").strip()
    if config_value:
        config_path = _abs(config_value)
    else:
        config_path = _abs(project_root() / "data" / "monitors.json")

    state_value = lookup("XIAOCAN_STATE_PATH", "").strip()
    if state_value:
        state_path = _abs(state_value)
    else:
        state_path = _abs(project_root() / "data" / "state.json")

    proxy_value = lookup("XIAOCAN_SOCKS5_PROXY", "").strip()
    proxy = parse_proxy(proxy_value) if proxy_value else None

    return Settings(
        dingtalk_webhook=lookup("XIAOCAN_DINGTALK_WEBHOOK", ""),
        dingtalk_secret=lookup("XIAOCAN_DINGTALK_SECRET", ""),
        dingtalk_title=lookup("XIAOCAN_DINGTALK_TITLE", DEFAULT_DINGTALK_TITLE),
        min_request_interval=_to_float(
            lookup("XIAOCAN_MIN_REQUEST_INTERVAL", str(DEFAULT_MIN_REQUEST_INTERVAL)),
            DEFAULT_MIN_REQUEST_INTERVAL,
        ),
        request_timeout=_to_float(
            lookup("XIAOCAN_REQUEST_TIMEOUT", str(DEFAULT_REQUEST_TIMEOUT)),
            DEFAULT_REQUEST_TIMEOUT,
        ),
        retries=_to_int(
            lookup("XIAOCAN_RETRIES", str(DEFAULT_RETRIES)),
            DEFAULT_RETRIES,
        ),
        default_lat=_to_float(
            lookup("XIAOCAN_DEFAULT_LAT", str(DEFAULT_LAT)),
            DEFAULT_LAT,
        ),
        default_lon=_to_float(
            lookup("XIAOCAN_DEFAULT_LON", str(DEFAULT_LON)),
            DEFAULT_LON,
        ),
        default_city=lookup("XIAOCAN_DEFAULT_CITY", DEFAULT_CITY),
        socks5_proxy=proxy_value,
        proxy=proxy,
        config_path=config_path,
        state_path=state_path,
        lock_path=_abs(config_path.parent / "xiaocan.lock"),
        env_path=env_path,
    )
