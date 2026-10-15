#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小蚕 4.2 搜索封装与 Card/Offer 归一化、时段判断。

对齐仓库根目录测试脚本 xiaocan_store_track_test.py 的 do_search / expand_item。
仅用标准库。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

try:
    from .rpc import RpcClient, RpcResult
except ImportError:  # 允许按文件路径直接导入（包 __init__ 尚未就绪时）
    from rpc import RpcClient, RpcResult

SERVER_SEARCH = "SilkwormRec"
METHOD_SEARCH = "RecService.SearchStorePromotionList"

PLATFORM_NAMES = {1: "美团", 2: "饿了么", 3: "京东"}


@dataclass
class Offer:
    """单个平台的活动。"""

    platform: int
    platform_name: str
    order_money: Optional[int]
    rebate: Optional[int]
    left: Optional[int]


@dataclass
class Card:
    """一条 promotion_list 元素归一化结果。"""

    store_id: Optional[str]
    store_name: str
    promotion_id: Optional[int]
    distance: Optional[int]
    start_minute: Optional[int]
    end_minute: Optional[int]
    rebate_condition: Optional[int]
    offers: List[Offer] = field(default_factory=list)

    def window_contains(self, minute_of_day: int) -> bool:
        """判断当日分钟数是否落在活动时段内；缺失或相等视为全天可点。"""
        if self.start_minute is None or self.end_minute is None:
            return True
        if self.start_minute == self.end_minute:
            return True
        if self.start_minute < self.end_minute:
            return self.start_minute <= minute_of_day < self.end_minute
        return minute_of_day >= self.start_minute or minute_of_day < self.end_minute

    def orderable_offers(self, minute_of_day: int) -> List[Offer]:
        """时段内且有余量（left > 0）的平台活动。"""
        if not self.window_contains(minute_of_day):
            return []
        return [o for o in self.offers if o.left is not None and o.left > 0]


def _minute_of_day(item: dict, prefix: str) -> Optional[int]:
    hour = item.get(f"{prefix}_time_hour")
    if hour is None:
        return None
    minute = item.get(f"{prefix}_time_minute")
    return int(hour) * 60 + int(minute or 0)


def expand_promotion(item: dict) -> Card:
    """把一条 promotion 展开成 Card（含受 *_status 约束的各平台 Offer）。"""
    store = item.get("store") or {}
    raw_store_id = store.get("store_id")
    offers: List[Offer] = []

    if item.get("meituan_status") == 1:
        offers.append(
            Offer(
                platform=1,
                platform_name=PLATFORM_NAMES[1],
                order_money=item.get("meituan_order_money"),
                rebate=item.get("meituan_user_rebate"),
                left=item.get("meituan_left_number"),
            )
        )
    if item.get("eleme_status") == 1:
        offers.append(
            Offer(
                platform=2,
                platform_name=PLATFORM_NAMES[2],
                order_money=item.get("eleme_order_money"),
                rebate=item.get("eleme_user_rebate"),
                left=item.get("eleme_left_number"),
            )
        )
    tp = item.get("tp_promotion") or {}
    if tp.get("tp_status") == 1:
        offers.append(
            Offer(
                platform=3,
                platform_name=PLATFORM_NAMES[3],
                order_money=tp.get("tp_order_money"),
                rebate=tp.get("tp_user_rebate"),
                left=tp.get("tp_left_number"),
            )
        )

    return Card(
        store_id=str(raw_store_id) if raw_store_id is not None else None,
        store_name=store.get("name") or "(店名缺失)",
        promotion_id=item.get("promotion_id"),
        distance=item.get("distance"),
        start_minute=_minute_of_day(item, "start"),
        end_minute=_minute_of_day(item, "end"),
        rebate_condition=item.get("rebate_condition"),
        offers=offers,
    )


def build_search_body(keyword: str, lat: float, lon: float, number: int) -> dict:
    """4.2 搜索请求体（同测试脚本 do_search）。"""
    return {
        "keyword": keyword,
        "latitude": lat,
        "longitude": lon,
        "offset": 0,
        "number": number,
        "promotion_sort": 1,
        "store_type": 99,
        "store_platform": 0,
        "promotion_category": 0,
        "silk_id": 0,
        "app_id": 20,
    }


def search(
    client: RpcClient,
    keyword: str,
    lat: float,
    lon: float,
    city: str,
    number: int = 15,
) -> Tuple[List[Card], RpcResult]:
    """调用 4.2 搜索并展开卡片；传输/业务失败时返回空列表与原始结果。"""
    body = build_search_body(keyword, lat, lon, number)
    result = client.call(SERVER_SEARCH, METHOD_SEARCH, body, city)

    if rpc_error(result) is not None:
        return [], result

    payload = result.payload or {}
    promotions = payload.get("promotion_list")
    if not isinstance(promotions, list):
        promotions = []
    cards = [expand_promotion(it) for it in promotions if isinstance(it, dict)]
    return cards, result


def rpc_error(result: RpcResult) -> Optional[str]:
    """传输成功且 status.code==0 时返回 None，否则返回可读中文错误。"""
    if result is None:
        return "RPC 调用未返回结果"
    if result.error is not None:
        return result.error
    if result.http_status is None:
        return "网络请求失败"
    payload = result.payload
    if not isinstance(payload, dict):
        return "响应不是 JSON"
    status = payload.get("status") or {}
    code = status.get("code")
    if code != 0:
        return f"业务失败 status.code={code} msg={status.get('msg', '')}"
    return None
