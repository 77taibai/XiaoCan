#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小蚕 HTTP 接口测试脚本 —— 单店优惠追踪（组合测试 4.2 搜索 + 4.6 详情）

背景（读源码得出的结论）:
    - store_id 是门店稳定主键（文档: promotion_id 每日变, 门店级持久化用 store_id;
      Java 项目收藏/推送历史/库存历史全部以 uniqId=store_id 为业务键）。
    - 但服务端【没有任何按 store_id 查询的接口】。Java 作者的做法
      (FavoriteStoreServiceImpl.queryStoreByFavorite L150-184):
          拿店名当关键词调 4.2 搜索 -> 本地按 store_id(+平台类型)过滤 -> 得到该店今日优惠
    本脚本复刻这条链路，并追加 4.6 详情（搜索命中的 promotion_id 当天有效, 立即再查一次）。

链路:  4.2 RecService.SearchStorePromotionList   (SilkwormRec)
       4.6 SilkwormService.GetStorePromotionDetail (Silkworm)

用法示例:
    python xiaocan_store_track_test.py 示例门店A --store-id 1000001
    python xiaocan_store_track_test.py 示例饮品 --store-id 1000001 --platform 1 --detail
    python xiaocan_store_track_test.py 示例饮品                      # 不传 id：列出候选店及其 store_id
    python xiaocan_store_track_test.py 全店名 --store-id 123 --lat 39.9042 --lon 116.4074 --city 110100

退出码: 0=找到该店今日优惠; 1=接口正常但未匹配到(同 Java 收藏 exists=false 场景);
        2=接口/网络失败; 3=参数错误。
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

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass

# ------------------------------------------------------------ 常量与默认值 --

GATEWAY_HOST = "gw.xiaocantech.com"
GATEWAY_PATH = "/rpc"

SERVER_SEARCH = "SilkwormRec"
METHOD_SEARCH = "RecService.SearchStorePromotionList"
SERVER_DETAIL = "Silkworm"
METHOD_DETAIL = "SilkwormService.GetStorePromotionDetail"

DEFAULT_LAT = 39.904200
DEFAULT_LON = 116.407400
DEFAULT_CITY = "110100"

REBATE_COND = {99: "无需评价", 2: "图文评价"}
PLATFORM_LABEL = {0: "无有效平台", 1: "美团", 2: "饿了么", 3: "京东"}

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
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 "
        "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
        "MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) "
        "UnifiedPCWindowsWechat(0xf254173b) XWEB/19027"
    ),
    "Referer": "https://servicewechat.com/wx52ae84595214/965/page-frame.html",
}

# ---------------------------------------------------------------- 签名与请求 --

def md5_hex(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def gen_nami() -> str:
    hex32 = uuid.uuid4().hex
    return hex32[:4] + "0" + hex32[4:19]


def build_headers(servername: str, methodname: str, city: str, nami: str) -> dict:
    """签名与 servername.methodname 绑定：换接口必须重算（两个方法各算各的）。"""
    garen = str(int(time.time() * 1000))
    x = md5_hex(f"{servername}.{methodname}".lower())
    ashe = md5_hex(f"{x}{garen}{nami}")
    headers = dict(BASE_HEADERS)
    headers.update({
        "servername": servername,
        "methodname": methodname,
        "X-Garen": garen,
        "X-Nami": nami,
        "X-Ashe": ashe,
        "x-City": str(city),
    })
    return headers


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


def _decompress(raw: bytes, enc: str) -> bytes:
    if enc == "gzip":
        return gzip.decompress(raw)
    if enc == "deflate":
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw


def rpc_call(servername: str, methodname: str, body: dict, args, nami: str):
    """一次 RPC 调用（含退避重试），返回 (http_status, payload, err)。"""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    attempts = args.retries + 1
    last_err = "未知失败"
    for attempt in range(1, attempts + 1):
        headers = build_headers(servername, methodname, args.city, nami)
        if args.show_request:
            print(f"  --- 请求预览 {servername}.{methodname} ---")
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
                return status, None, f"响应非 JSON（疑似 WAF）: {text[:200]}"
        last_err = f"HTTP {status} 非 2xx"
        if status in (401, 403, 405, 429):
            return status, None, last_err + "（疑似 WAF 限流，停止重试）"
        if attempt < attempts:
            wait = 2 * attempt
            print(f"  [重试 {attempt}/{attempts - 1}] {last_err}，{wait}s 后重试")
            time.sleep(wait)
    return None, None, last_err

# ------------------------------------------------------------ 卡片展开/展示 --

def _fen(value):
    return "?" if value is None else f"{value / 100:.2f}元"


def _hm(hour, minute):
    return f"{int(hour or 0):02d}:{int(minute or 0):02d}"


def expand_item(item: dict):
    """一条 promotion（或 promotion_detail）拆成 1~3 张平台卡。"""
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
    for ptype, prefix, label in ((1, "meituan", "美团"), (2, "eleme", "饿了么")):
        if item.get(prefix + "_status") == 1:
            cards.append({**base, "type": ptype, "platform": label,
                          "order_money": item.get(prefix + "_order_money"),
                          "rebate": item.get(prefix + "_user_rebate"),
                          "left": item.get(prefix + "_left_number")})
    tp = item.get("tp_promotion") or {}
    if tp.get("tp_status") == 1:
        cards.append({**base, "type": 3, "platform": "京东",
                      "order_money": tp.get("tp_order_money"),
                      "rebate": tp.get("tp_user_rebate"),
                      "left": tp.get("tp_left_number")})
    if not cards:
        cards.append({**base, "type": 0, "platform": "无有效平台",
                      "order_money": None, "rebate": None, "left": None})
    return cards


def render_card(idx: int, card: dict) -> str:
    head = (f"  [{idx:02d}] {card['platform']:<5} {card['name']}"
            f"  store_id={card['store_id']} promo_id={card['promotion_id']}")
    detail = (f"      时段 {card['time_range']}  评价:{card['cond']}"
              f"  满 {_fen(card['order_money'])} 返现 {_fen(card['rebate'])} 余 {card['left']}")
    return head + "\n" + detail

# -------------------------------------------------------------------- 主流程 --

def do_search(keyword: str, args, nami: str):
    """4.2 搜索（同 Java searchList: offset=0, number=15, sort=1, store_type=99）。"""
    body = {
        "keyword": keyword,
        "latitude": args.lat,
        "longitude": args.lon,
        "offset": 0,
        "number": args.number,
        "promotion_sort": 1,
        "store_type": 99,
        "store_platform": 0,
        "promotion_category": 0,
        "silk_id": 0,
        "app_id": 20,
    }
    return rpc_call(SERVER_SEARCH, METHOD_SEARCH, body, args, nami)


def do_detail(promotion_id: int, args, nami: str):
    """4.6 详情，只收 promotion_id（当天有效）。"""
    body = {"promotion_id": int(promotion_id), "silk_id": 0, "app_id": 20}
    return rpc_call(SERVER_DETAIL, METHOD_DETAIL, body, args, nami)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="小蚕单店优惠追踪测试（4.2 搜索 -> store_id 过滤 -> 4.6 详情）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("keyword", nargs="?", help="搜索关键词（建议用完整店名，同 Java 收藏刷新）")
    parser.add_argument("--store-id", dest="store_id", help="目标门店 store_id（字符串即可，如 1000001）")
    parser.add_argument("--platform", type=int, choices=[1, 2, 3], default=None,
                        help="只看某平台：1美团 2饿了么 3京东（默认全部）")
    parser.add_argument("--detail", action="store_true", help="对命中的 promotion_id 追加 4.6 详情查询")
    parser.add_argument("--lat", type=float, default=DEFAULT_LAT)
    parser.add_argument("--lon", type=float, default=DEFAULT_LON)
    parser.add_argument("--city", default=DEFAULT_CITY, help="x-City 城市编码")
    parser.add_argument("--number", type=int, default=15, help="搜索每页条数（接口仅支持第一页）")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--delay", type=float, default=3.0, help="详情查询之间的间隔秒数")
    parser.add_argument("--json", dest="raw_json", action="store_true", help="打印原始 JSON")
    parser.add_argument("--show-request", action="store_true", help="打印请求头与请求体")
    args = parser.parse_args(argv)

    if not args.keyword:
        parser.print_usage()
        print("错误：必须提供关键词（建议用完整店名）。")
        return 3

    print("=" * 64)
    print("小蚕单店优惠追踪测试（store_id 过滤链路）")
    print(f"  关键词   : {args.keyword!r}")
    print(f"  目标 id  : store_id={args.store_id or '(未指定，仅列出候选)'}"
          f" platform={args.platform and PLATFORM_LABEL[args.platform] or '不限'}")
    print(f"  定位     : lat={args.lat}, lon={args.lon}, x-City={args.city}")
    print("=" * 64)

    nami = gen_nami()   # 会话内复用；两个接口签名各自现算

    # ---- 第一步：4.2 搜索 -------------------------------------------------
    print(f"\n>>> [1] 4.2 搜索 RecService.SearchStorePromotionList")
    status, payload, err = do_search(args.keyword, args, nami)
    if err:
        print(f"    FAIL  {err}")
        return 2
    biz = payload.get("status") or {}
    if biz.get("code") != 0:
        print(f"    FAIL  HTTP {status} code={biz.get('code')} msg={biz.get('msg','')}")
        return 2
    if args.raw_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    promotions = payload.get("promotion_list") or []
    cards = []
    for item in promotions:
        cards.extend(expand_item(item))
    print(f"    OK    原始 {len(promotions)} 条 -> 平台卡 {len(cards)} 张")

    # 不传 store_id：当"选店助手"，列出候选店
    if not args.store_id:
        for i, c in enumerate(cards, 1):
            print(render_card(i, c))
        print("\n    提示: 从上面挑一个 store_id，配合 --store-id 精确追踪该店。")
        return 0

    # ---- 第二步：本地按 store_id(+平台) 过滤（同 Java L176-178） ----------
    time.sleep(args.delay)
    print(f"\n>>> [2] 本地过滤 store_id == {args.store_id}"
          + (f" 且平台 == {PLATFORM_LABEL[args.platform]}" if args.platform else ""))
    matched = [c for c in cards
               if str(c["store_id"]) == str(args.store_id)
               and (args.platform is None or c["type"] == args.platform)]
    if not matched:
        print(f"    NOT FOUND  该店今日无活动（或搜索结果被前 {args.number} 条截断）。")
        print(f"    参考: Java 收藏刷新此时走兜底快照 exists=false。可换更精确的完整店名重试。")
        return 1
    for i, c in enumerate(matched, 1):
        print(render_card(i, c))
    stock = [c for c in matched if c["left"] is not None and c["left"] > 0]
    print(f"    命中 {len(matched)} 张卡，其中有余量可抢 {len(stock)} 张（同 StoreTask leftNumber>0）")

    # ---- 第三步（可选）：4.6 详情，promotion_id 当天有效 -------------------
    if args.detail:
        seen_pid = set()
        for c in matched:
            pid = c["promotion_id"]
            if pid is None or pid in seen_pid:
                continue
            seen_pid.add(pid)
            time.sleep(args.delay)
            print(f"\n>>> [3] 4.6 详情 GetStorePromotionDetail  promotion_id={pid}")
            st, pl, e = do_detail(pid, args, nami)
            if e:
                print(f"    FAIL  {e}")
                return 2
            b = pl.get("status") or {}
            if b.get("code") != 0:
                print(f"    FAIL  HTTP {st} code={b.get('code')} msg={b.get('msg','')}")
                return 2
            if args.raw_json:
                print(json.dumps(pl, ensure_ascii=False, indent=2))
            pd = pl.get("promotion_detail")
            if not isinstance(pd, dict):
                print(f"    WARN  缺少 promotion_detail，顶层键: {list(pl.keys())}")
                continue
            print(f"    OK    {pd.get('store', {}).get('name', '?')} 完整信息:")
            for card in expand_item(pd):
                print(render_card(1, card))

    print("\n结果: PASS —— 找到目标门店今日优惠。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
