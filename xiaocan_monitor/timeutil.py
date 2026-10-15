"""中国时区工具：Asia/Shanghai，缺失 tzdata 时回退固定 UTC+8。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo

CN_TZ_NAME = "Asia/Shanghai"

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # Python 3.8 无 zoneinfo
    ZoneInfo = None  # type: ignore[assignment]
    ZoneInfoNotFoundError = None  # type: ignore[assignment]


def _resolve_cn_tz() -> tzinfo:
    if ZoneInfo is not None:
        try:
            return ZoneInfo(CN_TZ_NAME)
        except (ZoneInfoNotFoundError, KeyError, ValueError, OSError):
            pass
    return timezone(timedelta(hours=8), "UTC+8")


CN_TZ: tzinfo = _resolve_cn_tz()


def cn_now() -> datetime:
    """当前中国时区时间（不受服务器本地时区影响）。"""
    return datetime.now(timezone.utc).astimezone(CN_TZ)


def cn_today() -> str:
    """中国时区的今天，格式 ``YYYY-MM-DD``。"""
    return cn_now().strftime("%Y-%m-%d")


def minute_of_day(dt: datetime) -> int:
    """返回该时刻在当天内的分钟数（0~1439）。"""
    return dt.hour * 60 + dt.minute


def format_cn(dt: datetime) -> str:
    """格式化为 ``YYYY-MM-DD HH:MM``；带时区的输入先转中国时区。"""
    if dt.tzinfo is not None:
        dt = dt.astimezone(CN_TZ)
    return dt.strftime("%Y-%m-%d %H:%M")
