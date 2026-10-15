#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小蚕余量监控 CLI：search / add / remove / run。"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from . import __version__
from .api import PLATFORM_NAMES, rpc_error
from .api import search as api_search
from .engine import run_cycle
from .lock import LockBusy, RunLock
from .notify import DingTalkNotifier
from .ratelimit import RateLimiter
from .rpc import RpcClient
from .settings import Settings, load_settings
from .store import Monitor, Store, StoreError
from .timeutil import cn_now, cn_today

PLATFORM_CHOICES = {0: "全部", 1: "美团", 2: "饿了么", 3: "京东"}

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_LOCKED = 3


def _platform_label(value: int) -> str:
    return PLATFORM_CHOICES.get(value, str(value))


def _ask(label: str, default: Optional[str] = None, required: bool = False) -> Optional[str]:
    """交互式提问；返回 None 表示用户取消（EOF/空且必填）。"""
    suffix = f" [{default}]" if default not in (None, "") else ""
    while True:
        try:
            raw = input(f"{label}{suffix}: ").strip()
        except EOFError:
            print("\n输入结束，已取消。")
            return None
        if raw:
            return raw
        if default not in (None, ""):
            return default
        if not required:
            return ""
        print("该项不能为空，请重新输入。")


def _ask_platform() -> Optional[int]:
    while True:
        raw = _ask("平台 [0=全部/1=美团/2=饿了么/3=京东]", default="0")
        if raw is None:
            return None
        if raw in ("0", "1", "2", "3"):
            return int(raw)
        print("请输入 0/1/2/3。")


def _ask_float(label: str, default: float) -> Optional[float]:
    while True:
        raw = _ask(label, default=str(default))
        if raw is None:
            return None
        try:
            return float(raw)
        except ValueError:
            print("请输入数字。")


def _ask_confirm(label: str) -> bool:
    raw = _ask(label, default="N")
    if raw is None:
        return False
    return raw.strip().lower() in ("y", "yes", "是")


def _make_client(settings: Settings, store: Store, verbose: bool = False) -> RpcClient:
    limiter = RateLimiter(store, settings.min_request_interval)
    logger = print if verbose else (lambda *a, **k: None)
    return RpcClient(
        timeout=settings.request_timeout,
        retries=settings.retries,
        limiter=limiter,
        logger=logger,
    )


# --------------------------------------------------------------- 子命令 --

def cmd_search(settings: Settings, args) -> int:
    store = Store(settings.config_path, settings.state_path)
    client = _make_client(settings, store)
    cards, result = api_search(
        client, args.keyword, args.lat, args.lon, args.city, args.number
    )
    err = rpc_error(result)
    if err:
        print(f"搜索失败：{err}")
        return EXIT_FAIL

    print(f"关键词 {args.keyword!r} 命中 {len(cards)} 条（lat={args.lat} lon={args.lon} "
          f"x-City={args.city}）")
    if not cards:
        print("（无结果：该定位下此关键词无满减活动；注意搜索接口仅支持第一页）")
        return EXIT_OK

    for idx, card in enumerate(cards, 1):
        window = ""
        if card.start_minute is not None and card.end_minute is not None:
            window = f"{card.start_minute // 60:02d}:{card.start_minute % 60:02d}~" \
                     f"{card.end_minute // 60:02d}:{card.end_minute % 60:02d}"
        dist = "?" if card.distance is None else (
            f"{card.distance / 1000:.2f}km" if card.distance >= 1000 else f"{card.distance}m"
        )
        print(f"[{idx:02d}] {card.store_name}")
        print(f"     store_id={card.store_id}  距离={dist}  时段={window or '?'}")
        if not card.offers:
            print("     (无有效平台活动)")
        for offer in card.offers:
            left = "?" if offer.left is None else str(offer.left)
            print(f"     {offer.platform_name}: 余 {left}")
    print("\n提示: 用上面某个 store_id 执行 `add` 添加监控。")
    return EXIT_OK


def cmd_add(settings: Settings, args) -> int:
    store = Store(settings.config_path, settings.state_path)
    print("添加监控（交互式）")
    store_name = _ask("店名（用于搜索，建议完整店名）", required=True)
    if store_name is None:
        return EXIT_USAGE
    store_id = _ask("store_id（从 search 结果复制）", required=True)
    if store_id is None:
        return EXIT_USAGE
    platform = _ask_platform()
    if platform is None:
        return EXIT_USAGE
    lat = _ask_float("纬度", settings.default_lat)
    if lat is None:
        return EXIT_USAGE
    lon = _ask_float("经度", settings.default_lon)
    if lon is None:
        return EXIT_USAGE
    city = _ask("城市编码 x-City", default=settings.default_city, required=True)
    if city is None:
        return EXIT_USAGE
    note = _ask("备注（可空）", default="")
    if note is None:
        return EXIT_USAGE

    monitors = store.load_monitors()
    for existing in monitors:
        if str(existing.store_id) == str(store_id) and existing.platform == platform:
            print(f"提示：已存在相同 store_id 且平台的监控（id={existing.id}）。")

    if not _ask_confirm("确认添加?"):
        print("已取消。")
        return EXIT_OK

    monitor = Monitor(
        id=Store.new_id(),
        store_id=str(store_id),
        store_name=store_name,
        platform=platform,
        lat=lat,
        lon=lon,
        city=str(city),
        note=note,
        enabled=True,
        created_at=cn_now().isoformat(timespec="seconds"),
    )
    monitors.append(monitor)
    store.save_monitors(monitors)
    print(f"已添加监控 id={monitor.id}  共 {len(monitors)} 条。")
    return EXIT_OK


def cmd_remove(settings: Settings, args) -> int:
    store = Store(settings.config_path, settings.state_path)
    monitors = store.load_monitors()
    if not monitors:
        print("当前没有监控。")
        return EXIT_OK

    state = store.load_state()
    daily = state.get("daily") if isinstance(state.get("daily"), dict) else {}
    today = cn_today()

    print("现有监控:")
    for idx, monitor in enumerate(monitors, 1):
        if not monitor.enabled:
            day_status = "已禁用"
        elif daily.get(monitor.id) == today:
            day_status = "今日已完成"
        else:
            day_status = "待监控"
        print(f"[{idx}] {monitor.store_name}  store_id={monitor.store_id}  "
              f"platform={_platform_label(monitor.platform)}  今日:{day_status}")

    raw = _ask("选择要删除的编号（输入 q 取消）")
    if raw is None or raw.strip().lower() == "q":
        print("已取消。")
        return EXIT_OK
    try:
        index = int(raw)
    except ValueError:
        print("无效编号。")
        return EXIT_USAGE
    if index < 1 or index > len(monitors):
        print("编号超出范围。")
        return EXIT_USAGE

    target = monitors[index - 1]
    print(f"将要删除: [{index}] {target.store_name}  store_id={target.store_id}  "
          f"platform={_platform_label(target.platform)}")
    if not _ask_confirm("确认删除?"):
        print("已取消。")
        return EXIT_OK

    monitors.pop(index - 1)
    store.save_monitors(monitors)
    print(f"已删除，剩余 {len(monitors)} 条。")
    return EXIT_OK


def cmd_run(settings: Settings, args) -> int:
    store = Store(settings.config_path, settings.state_path)
    notifier = DingTalkNotifier(
        settings.dingtalk_webhook,
        settings.dingtalk_secret,
        settings.dingtalk_title,
        timeout=settings.request_timeout,
    )
    if not args.dry_run and not notifier.configured():
        print("未配置 XIAOCAN_DINGTALK_WEBHOOK，无法推送；如需仅查看结果请加 --dry-run。")
        return EXIT_USAGE

    client = _make_client(settings, store, verbose=args.verbose)

    def search_fn(keyword, lat, lon, city, number):
        return api_search(client, keyword, lat, lon, city, number)

    try:
        with RunLock(settings.lock_path):
            now = cn_now()
            print(f"开始监控一轮（中国时间 {now.strftime('%Y-%m-%d %H:%M')}"
                  f"{'，dry-run' if args.dry_run else ''}）")
            outcomes = run_cycle(
                store=store,
                search_fn=search_fn,
                notifier=notifier,
                now=now,
                dry_run=args.dry_run,
            )
    except LockBusy as exc:
        print(f"跳过：{exc}")
        return EXIT_LOCKED

    hits = sum(1 for o in outcomes if o.status == "hit")
    waits = sum(1 for o in outcomes if o.status == "waiting")
    skips = sum(1 for o in outcomes if o.status == "skipped")
    errors = [o for o in outcomes if o.status == "error"]
    print(f"\n汇总: 共 {len(outcomes)} 个监控  "
          f"命中 {hits} / 待监控 {waits} / 今日已完成 {skips} / 失败 {len(errors)}")
    for outcome in errors:
        print(f"  - {outcome.monitor.store_name}: {outcome.detail}")
    return EXIT_FAIL if errors else EXIT_OK


# ----------------------------------------------------------------- main --

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xiaocan_monitor",
        description="小蚕余量监控 CLI（cron 定时刷新 + 钉钉推送）",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_search = sub.add_parser("search", help="按关键词搜索门店，列出 store_id")
    p_search.add_argument("keyword", help="搜索关键词，例如 示例门店B")
    p_search.add_argument("--lat", type=float, default=None, help="纬度（默认取 .env）")
    p_search.add_argument("--lon", type=float, default=None, help="经度（默认取 .env）")
    p_search.add_argument("--city", default=None, help="城市编码 x-City（默认取 .env）")
    p_search.add_argument("--number", type=int, default=15, help="每页条数（搜索建议 15）")
    p_search.set_defaults(func=cmd_search)

    p_add = sub.add_parser("add", help="交互式添加监控")
    p_add.set_defaults(func=cmd_add)

    p_remove = sub.add_parser("remove", help="交互式删除监控")
    p_remove.set_defaults(func=cmd_remove)

    p_run = sub.add_parser("run", help="执行一轮监控（cron 入口）")
    p_run.add_argument("--dry-run", action="store_true", help="只判定并打印，不推送、不落盘")
    p_run.add_argument("--verbose", action="store_true", help="打印更多细节")
    p_run.set_defaults(func=cmd_run)

    return parser


def main(argv=None) -> int:
    settings = load_settings()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "search":
        if args.lat is None:
            args.lat = settings.default_lat
        if args.lon is None:
            args.lon = settings.default_lon
        if args.city is None:
            args.city = settings.default_city

    try:
        return args.func(settings, args)
    except StoreError as exc:
        print(f"配置/状态文件错误：{exc}")
        return EXIT_USAGE
    except KeyboardInterrupt:
        print("\n已中断。")
        return 130


if __name__ == "__main__":
    sys.exit(main())
