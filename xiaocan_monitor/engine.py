#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run 一轮监控的纯逻辑：判定「当前时段可点」并触发一次钉钉推送。

依赖全部注入（搜索函数、推送器、当前时间），便于无网络单元测试。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, List, Optional, Tuple

from .api import Card, Offer, rpc_error
from .store import Monitor, Store
from .timeutil import format_cn, minute_of_day


@dataclass
class Outcome:
    """单个监控在本轮的结果。"""

    monitor: Monitor
    status: str  # skipped | waiting | hit | error
    detail: str
    pushed: bool = False


SearchFn = Callable[[str, float, float, str, int], Tuple[List[Card], object]]


def _fen_to_yuan(value) -> str:
    if value is None:
        return "?"
    return f"{value / 100:.2f}元"


def _hhmm(minutes: Optional[int]) -> str:
    if minutes is None:
        return "??:??"
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def build_hit_text(
    monitor: Monitor, matches: List[Tuple[Card, Offer]], now: datetime
) -> str:
    """组装钉钉 markdown 文本（不使用 emoji）。"""
    lines = [
        "## 小蚕余量可点",
        f"门店: {monitor.store_name}  store_id={monitor.store_id}",
        f"中国时间: {format_cn(now)}",
    ]
    for card, offer in matches:
        window = f"{_hhmm(card.start_minute)}~{_hhmm(card.end_minute)}"
        left = "?" if offer.left is None else str(offer.left)
        lines.append(
            f"- {offer.platform_name} 时段{window}: "
            f"满 {_fen_to_yuan(offer.order_money)} 返现 {_fen_to_yuan(offer.rebate)} | 余 {left}"
        )
    return "\n".join(lines)


def _collect_matches(
    monitor: Monitor, cards: List[Card], now_minute: int
) -> List[Tuple[Card, Offer]]:
    matched_cards = [c for c in cards if str(c.store_id) == str(monitor.store_id)]
    if monitor.platform:
        matched_cards = [
            c
            for c in matched_cards
            if any(o.platform == monitor.platform for o in c.offers)
        ]
    result: List[Tuple[Card, Offer]] = []
    for card in matched_cards:
        for offer in card.orderable_offers(now_minute):
            if monitor.platform == 0 or offer.platform == monitor.platform:
                result.append((card, offer))
    return result


def run_cycle(
    *,
    store: Store,
    search_fn: SearchFn,
    notifier,
    now: datetime,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> List[Outcome]:
    """执行一轮监控。

    - 当天（中国时区）已推送过的监控直接跳过，不请求接口。
    - 发现「当前时段且有余量」：非 dry-run 时推送一次并写入当日标记。
    """
    monitors = [m for m in store.load_monitors() if m.enabled]
    state = store.load_state()
    daily = state.get("daily") if isinstance(state.get("daily"), dict) else {}
    today = now.strftime("%Y-%m-%d")
    now_minute = minute_of_day(now)

    outcomes: List[Outcome] = []
    for monitor in monitors:
        tag = f"[{monitor.store_name} store_id={monitor.store_id}]"

        if daily.get(monitor.id) == today:
            detail = "今日已完成，跳过"
            outcomes.append(Outcome(monitor, "skipped", detail))
            log(f"{tag} SKIP  {detail}")
            continue

        cards, result = search_fn(
            monitor.store_name, monitor.lat, monitor.lon, monitor.city, 15
        )
        err = rpc_error(result)
        if err:
            outcomes.append(Outcome(monitor, "error", err))
            log(f"{tag} FAIL  {err}")
            continue

        matches = _collect_matches(monitor, cards, now_minute)
        if not matches:
            detail = "当前时段无可点余量"
            outcomes.append(Outcome(monitor, "waiting", detail))
            log(f"{tag} WAIT  {detail}")
            continue

        text = build_hit_text(monitor, matches, now)
        if dry_run:
            detail = "发现可点（dry-run，未推送、未落盘）"
            outcomes.append(Outcome(monitor, "hit", detail, pushed=False))
            log(f"{tag} HIT   {detail}")
            log(text)
            continue

        if notifier is None or not notifier.configured():
            detail = "发现可点但未配置钉钉 webhook，未推送"
            outcomes.append(Outcome(monitor, "error", detail))
            log(f"{tag} FAIL  {detail}")
            continue

        ok, message = notifier.send_markdown(notifier.title, text)
        if ok:
            store.mark_done(monitor.id, today)
            outcomes.append(
                Outcome(monitor, "hit", "发现可点，已推送钉钉", pushed=True)
            )
            log(f"{tag} HIT   已推送钉钉")
        else:
            detail = f"发现可点但推送失败：{message}"
            outcomes.append(Outcome(monitor, "error", detail))
            log(f"{tag} FAIL  {detail}")

    return outcomes
