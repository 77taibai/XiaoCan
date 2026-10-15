#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小蚕 HTTP 接口测试脚本 —— 满减活动列表（文档 4.1）· 对齐 Java 参考实现版

接口:  POST https://gw.xiaocantech.com/rpc
       servername = SilkwormRec
       methodname = RecService.GetStorePromotionList

与旧版的关键差异（对齐 src/ 里 Java 项目 XiaochanHttp.parsePromotion +
XiaoChanServiceImpl.getList/hasNext 的行为，解决"比 APP 结果少"的问题）:
    1. 不做 store_id 去重：同一门店可挂多个 promotion_id，去重会丢数据。
    2. 行模型 = 「门店 × 有效平台」一张卡，与 APP 列表行数一致
       （美团/饿了么/京东各算一行；三个平台都无效的行默认丢弃，同 Java）。
    3. 自动翻页到底：默认最多 100 页（同 Java），累计卡片数达到 --max-size
       （默认 500，同 StoreConstant.MAX_SIZE）即停；
       终止条件同 Java hasNext()：
         - 本页展开行数 < number        => 数据到底
         - 本页过半行距离 > --max-distance(默认3500m，同 MAX_DISTANCE) => 太远，停
    4. 翻页间隔 --delay 秒防 WAF。

签名规则不变: X-Ashe = MD5(MD5(lower(servername.methodname)) + 毫秒时间戳 + X-Nami)

用法示例:
    python xiaocan_list_test.py                     # 自动翻到底（同 APP）
    python xiaocan_list_test.py --pages 3           # 限制最多 3 页
    python xiaocan_list_test.py --store-platform 1  # 只看美团
    python xiaocan_list_test.py --show-invalid      # 无有效平台的原始行也展示
    python xiaocan_list_test.py --json --pages 1    # 看原始响应
"""

import argparse
import gzip
import hashlib
import http.client
import json
import socket
import sys
import time
import uuid
import zlib

# 兜底：个别门店名含特殊符号，窄编码控制台不让 print 崩溃。
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass

# ------------------------------------------------------------ 常量与默认值 --

GATEWAY_HOST = "gw.xiaocantech.com"
GATEWAY_PATH = "/rpc"

SERVERNAME = "SilkwormRec"
METHODNAME = "RecService.GetStorePromotionList"

DEFAULT_LAT = 39.904200        # 纬度（北纬 示例）
DEFAULT_LON = 116.407400       # 经度（东经 示例）
DEFAULT_CITY = "110100"

# 对齐 StoreConstant.java
MAX_SIZE = 500                 # 门店卡最大数量
MAX_DISTANCE = 3500            # 距离阈值（米），过半即停
MAX_PAGES = 100                # Java 侧 for (i < 100) 的硬上限

REBATE_COND = {99: "无需评价", 2: "图文评价"}
PLATFORM_NAME = {0: "全部", 1: "美团", 2: "饿了么", 3: "京东"}

# 文档 2.1 固定头（同 Java getHeaders；Accept-Encoding 去 br，本机无法解码，签名与此头无关）
BASE_HEADERS = {
    "Content-Type": "application/json",
    "appid": "20",
    "X-Platform": "mini",
    "version": "3.15.9.10",
    "X-Version": "3.15.9.10",
    "X-Model": "microsoft microsoft",
    "x-Annie": "XC",
    "xweb_xhr": "1",
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Sec-Fetch-Site": "cross-site",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 "
        "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
        "MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) "
        "UnifiedPCWindowsWechat(0xf254173b) XWEB/19027"
    ),
    "Referer": "https://servicewechat.com/wx52ae84595214/965/page-frame.html",
}

# ---------------------------------------------------------------- 签名部分 --

def md5_hex(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def gen_nami() -> str:
    """同 Java getNami(): UUID 去横线 -> 前4位 + "0" + 再15位，共 20 字符。"""
    hex32 = uuid.uuid4().hex
    return hex32[:4] + "0" + hex32[4:19]


def build_headers(city: str, nami: str) -> dict:
    garen = str(int(time.time() * 1000))
    x = md5_hex(f"{SERVERNAME}.{METHODNAME}".lower())
    ashe = md5_hex(f"{x}{garen}{nami}")
    headers = dict(BASE_HEADERS)
    headers.update({
        "servername": SERVERNAME,
        "methodname": METHODNAME,
        "X-Garen": garen,
        "X-Nami": nami,
        "X-Ashe": ashe,
        "x-City": str(city),
    })
    return headers


def build_body(args, offset: int) -> dict:
    """文档 4.1 请求体（字段与 Java getBody 完全一致）。"""
    return {
        "latitude": args.lat,
        "longitude": args.lon,
        "city_code": int(args.city),
        "offset": offset,
        "number": args.number,
        "promotion_sort": args.sort,
        "store_type": args.store_type,
        "store_category": args.store_category,
        "promotion_filter": args.promotion_filter,
        "promotion_category": args.promotion_category,
        "store_platform": args.store_platform,
        "silk_id": 0,
        "app_id": 20,
    }

# ---------------------------------------------------------------- 网络请求 --

def _post_once(headers: dict, data: bytes, timeout: float):
    conn = http.client.HTTPSConnection(GATEWAY_HOST, timeout=timeout)
    try:
        conn.request("POST", GATEWAY_PATH, body=data, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        enc = (resp.getheader("Content-Encoding") or "").lower()
        return resp.status, enc, raw
    finally:
        conn.close()


def _decompress(raw: bytes, content_encoding: str) -> bytes:
    if content_encoding == "gzip":
        return gzip.decompress(raw)
    if content_encoding == "deflate":
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw


def fetch_page(args, nami: str, offset: int):
    body = build_body(args, offset)
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    attempts = args.retries + 1
    last_err = "未知失败"

    for attempt in range(1, attempts + 1):
        headers = build_headers(args.city, nami)
        if args.show_request:
            print("  --- 请求预览 ---")
            for k, v in headers.items():
                print(f"  {k}: {v}")
            print(f"  body: {data.decode('utf-8')}")
        try:
            status, enc, raw = _post_once(headers, data, args.timeout)
        except (OSError, socket.timeout, http.client.HTTPException) as exc:
            last_err = f"网络异常: {type(exc).__name__}: {exc}"
            if attempt < attempts:
                wait = 2 * attempt
                print(f"  [重试 {attempt}/{attempts - 1}] {last_err}，{wait}s 后重试")
                time.sleep(wait)
                continue
            return None, None, last_err

        if 200 <= status < 300:
            text = _decompress(raw, enc).decode("utf-8", "replace")
            try:
                return status, json.loads(text), None
            except json.JSONDecodeError:
                snippet = text[:300].replace("\n", " ")
                return status, None, f"响应不是 JSON（疑似 WAF 拦截页/网关错误）: {snippet}"

        last_err = f"HTTP {status} 非 2xx"
        if status in (401, 403, 405, 429):
            return status, None, last_err + "（可能被 WAF 限流，请勿继续高频重试）"
        if attempt < attempts:
            wait = 2 * attempt
            print(f"  [重试 {attempt}/{attempts - 1}] {last_err}，{wait}s 后重试")
            time.sleep(wait)

    return None, None, last_err

# ------------------------------------------------- 展开：一行原始数据->平台卡 --

def _fen_to_yuan(value) -> str:
    return "?" if value is None else f"{value / 100:.2f}元"


def _fmt_distance(meters) -> str:
    if meters is None:
        return "?"
    return f"{meters / 1000:.2f}km" if meters >= 1000 else f"{int(meters)}m"


def _hm(hour, minute) -> str:
    return f"{int(hour or 0):02d}:{int(minute or 0):02d}"


def expand_item(item: dict):
    """同 Java parsePromotion()：把一条 promotion_list 拆成 0~3 张「门店×平台」卡。
    仅 status==1 的平台生成卡片；全部无效时按 show_invalid 需求返回一张占位卡或 0 张。"""
    store = item.get("store") or {}
    base = {
        "name": store.get("name") or "(店名缺失)",
        "store_id": store.get("store_id"),
        "promotion_id": item.get("promotion_id"),
        "distance": item.get("distance"),
        "time_range": (_hm(item.get("start_time_hour"), item.get("start_time_minute"))
                       + "~" + _hm(item.get("end_time_hour"), item.get("end_time_minute"))),
        "cond": REBATE_COND.get(item.get("rebate_condition"), "其他"),
    }
    cards = []
    for platform_type, prefix, label in ((1, "meituan", "美团"), (2, "eleme", "饿了么")):
        if item.get(prefix + "_status") == 1:
            cards.append({**base, "platform": label, "type": platform_type,
                          "order_money": item.get(prefix + "_order_money"),
                          "rebate": item.get(prefix + "_user_rebate"),
                          "left": item.get(prefix + "_left_number")})
    tp = item.get("tp_promotion") or {}
    if tp.get("tp_status") == 1:
        cards.append({**base, "platform": "京东", "type": 3,
                      "order_money": tp.get("tp_order_money"),
                      "rebate": tp.get("tp_user_rebate"),
                      "left": tp.get("tp_left_number")})
    if not cards:
        cards.append({**base, "platform": "无有效平台", "type": 0,
                      "order_money": None, "rebate": None, "left": None})
    return cards


def render_card(idx: int, card: dict) -> str:
    head = (f"[{idx:03d}] {card['platform']}  {card['name']}"
            f"  距离={_fmt_distance(card['distance'])}"
            f" | store_id={card['store_id']}"
            f" | promo_id={card['promotion_id']}")
    detail = (f"      时段 {card['time_range']}   评价: {card['cond']}"
              f"   满 {_fen_to_yuan(card['order_money'])}"
              f" 返现 {_fen_to_yuan(card['rebate'])} | 余 {card['left'] if card['left'] is not None else '?'}")
    return head + "\n" + detail

# ---------------------------------------------------------------- 翻页主逻辑 --

def has_next(page_cards, number: int, max_distance: int):
    """完全对齐 Java XiaoChanServiceImpl.hasNext():
    以「展开后的卡片数」判断（注意不是原始条数）；过半超过 max_distance 即停。"""
    size = len(page_cards)
    if size < number:
        return False, "本页卡片数 < number，数据到底"
    over = sum(1 for c in page_cards
               if c["distance"] is not None and c["distance"] > max_distance)
    if over > size // 2:
        return False, f"本页过半卡片距离 > {max_distance}m（{over}/{size}），已出圈"
    return True, ""


def collect(args):
    nami = gen_nami()
    all_cards = []
    raw_total = 0
    pages_fetched = 0
    stop_reason = "达到页数上限 --pages"
    max_pages = args.pages if args.pages > 0 else MAX_PAGES

    for page in range(max_pages):
        offset = page * args.number
        print(f"\n>>> 第 {page + 1} 页  offset={offset} number={args.number}")
        status, payload, err = fetch_page(args, nami, offset)
        if err:
            print(f"    FAIL  {err}")
            return all_cards, False, err, pages_fetched
        pages_fetched += 1

        biz = payload.get("status") or {}
        if biz.get("code") != 0:
            msg = f"业务失败 code={biz.get('code')} msg={biz.get('msg', '')}"
            print(f"    FAIL  HTTP {status}  {msg}")
            return all_cards, False, msg, pages_fetched

        if args.raw_json:
            print("    原始响应:")
            print(json.dumps(payload, ensure_ascii=False, indent=2))

        promotions = payload.get("promotion_list")
        if not isinstance(promotions, list):
            promotions = []
            print(f"    WARN  缺少 promotion_list，顶层键: {list(payload.keys())}")
        raw_total += len(promotions)

        page_cards = []
        for item in promotions:
            cards = expand_item(item)
            if args.show_invalid:
                page_cards.extend(cards)
            else:
                page_cards.extend(c for c in cards if c["type"] != 0)   # 同 Java：丢弃无有效平台行

        for card in page_cards:
            all_cards.append(card)
        print(f"    PASS  HTTP {status}  原始 {len(promotions)} 条 -> 有效卡片 {len(page_cards)} 张"
              f"（累计 {len(all_cards)}）")
        start_idx = len(all_cards) - len(page_cards)
        for i, card in enumerate(page_cards, start_idx + 1):
            print("  " + render_card(i, card))

        if len(all_cards) >= args.max_size:
            stop_reason = f"累计卡片数达到 --max-size={args.max_size}"
            all_cards = all_cards[:args.max_size]
            break

        more, why = has_next(page_cards, args.number, args.max_distance)
        if not more:
            stop_reason = why
            break

        time.sleep(args.delay)   # 控制频率，防 WAF

    return all_cards, True, stop_reason, pages_fetched


def summarize(cards):
    by_platform = {}
    for c in cards:
        by_platform[c["platform"]] = by_platform.get(c["platform"], 0) + 1
    parts = " / ".join(f"{k} {v}" for k, v in sorted(by_platform.items(),
                                                     key=lambda kv: -kv[1]))
    print(f"    卡片分布 -> {parts if parts else '(空)'}")

# -------------------------------------------------------------------- 主流程 --

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="小蚕 4.1 满减活动列表接口测试脚本（对齐 Java 参考实现）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--lat", type=float, default=DEFAULT_LAT, help="纬度")
    parser.add_argument("--lon", type=float, default=DEFAULT_LON, help="经度")
    parser.add_argument("--city", default=DEFAULT_CITY, help="城市编码（body city_code 与 x-City 头）")
    parser.add_argument("--number", type=int, default=30, help="每页条数")
    parser.add_argument("--pages", type=int, default=0, help="最大页数，0=按 max-size/距离/到底 自动判停（上限100，同Java）")
    parser.add_argument("--max-size", dest="max_size", type=int, default=MAX_SIZE, help="累计卡片上限（同 StoreConstant.MAX_SIZE）")
    parser.add_argument("--max-distance", dest="max_distance", type=int, default=MAX_DISTANCE,
                        help="距离阈值米（同 MAX_DISTANCE）：本页过半卡片超出即判停")
    parser.add_argument("--sort", type=int, default=3, help="promotion_sort 排序方式")
    parser.add_argument("--store-type", type=int, default=0, dest="store_type", help="store_type，0=全部")
    parser.add_argument("--store-category", type=int, default=0, dest="store_category", help="品类，0=全部")
    parser.add_argument("--promotion-filter", type=int, default=0, dest="promotion_filter", help="活动过滤，0=无")
    parser.add_argument("--promotion-category", type=int, default=0, dest="promotion_category", help="活动分类，0=全部")
    parser.add_argument("--store-platform", type=int, default=0, dest="store_platform", choices=[0, 1, 2, 3],
                        help="store_platform：0=全部 1=美团 2=饿了么 3=京东")
    parser.add_argument("--timeout", type=float, default=8.0, help="请求超时秒")
    parser.add_argument("--retries", type=int, default=1, help="网络失败/非2xx 重试次数（退避）")
    parser.add_argument("--delay", type=float, default=3.0, help="翻页间隔秒（防风控）")
    parser.add_argument("--show-invalid", action="store_true", help="展示三个平台都无效的原始条目（默认像 Java 一样丢弃）")
    parser.add_argument("--json", dest="raw_json", action="store_true", help="打印原始 JSON 响应")
    parser.add_argument("--show-request", action="store_true", help="发送前打印请求头与请求体")
    args = parser.parse_args(argv)

    print("=" * 64)
    print("小蚕 HTTP 接口测试 —— 4.1 满减活动列表（APP 对齐版）")
    print(f"  网关     : https://{GATEWAY_HOST}{GATEWAY_PATH}")
    print(f"  RPC 方法 : {SERVERNAME} / {METHODNAME}")
    print(f"  定位     : lat={args.lat}, lon={args.lon}, city_code={args.city}")
    print(f"  停止条件 : max-size={args.max_size} 卡, 距离阈值={args.max_distance}m, "
          f"pages={args.pages or f'自动(≤{MAX_PAGES})'}, delay={args.delay}s")
    print(f"  过滤     : sort={args.sort} platform={PLATFORM_NAME.get(args.store_platform, args.store_platform)}")
    print("=" * 64)

    t0 = time.perf_counter()
    cards, ok, stop_reason, pages = collect(args)
    elapsed = time.perf_counter() - t0

    print("\n" + "=" * 64)
    print(f"测试汇总: {'PASS' if ok else 'FAIL'}  翻页 {pages} 页  耗时 {elapsed:.1f}s")
    print(f"  有效平台卡片共 {len(cards)} 张，停止原因: {stop_reason}")
    summarize(cards)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
