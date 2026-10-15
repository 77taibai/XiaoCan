#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小蚕 HTTP 接口测试脚本 —— 满减活动搜索（文档 4.2）

接口:  POST https://gw.xiaocantech.com/rpc
       servername = SilkwormRec
       methodname = RecService.SearchStorePromotionList
鉴权:  匿名（silk_id=0），签名头 X-Garen / X-Nami / X-Ashe 每次现算
依赖:  仅 Python 标准库（http.client / hashlib / json / uuid）

定位默认值（本次测试指定）:
    纬度(北纬): 39.904200
    经度(东经): 116.407400
    城市行政编码   -> 110100（示例省市示例区，通过请求头 x-City 传递；
                       搜索请求体没有 city_code 字段）

用法示例:
    python xiaocan_search_test.py 示例门店B
    python xiaocan_search_test.py 示例门店B 示例门店C "示例门店A"     # 多关键词依次搜索
    python xiaocan_search_test.py 示例门店B --lat 39.9042 --lon 116.4074 --city 110100
    python xiaocan_search_test.py                            # 不带参数则交互式输入关键词
    python xiaocan_search_test.py 示例门店B --show-request        # 打印完整请求头/请求体（调试签名）

退出码: 全部关键词业务 code==0 返回 0，否则返回 1（可直接用于自动化判断）。
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

# 兜底：个别门店名含特殊符号（间隔号/emoji），在 GBK 等编码控制台会让 print 崩溃，
# 这里把不可编码字符替换为占位符，保证测试不被中断。
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass

# ------------------------------------------------------------ 常量与默认值 --

GATEWAY_HOST = "gw.xiaocantech.com"
GATEWAY_PATH = "/rpc"

SERVERNAME = "SilkwormRec"
METHODNAME = "RecService.SearchStorePromotionList"

DEFAULT_LAT = 39.904200        # 纬度（北纬 示例）
DEFAULT_LON = 116.407400       # 经度（东经 示例）
DEFAULT_CITY = "110100"

REBATE_COND = {99: "无需评价", 2: "图文评价"}

# 文档 2.1 固定头。Accept-Encoding 去掉 br（本机无 brotli 库也无法解码），
# 签名只与方法名/时间戳/指纹有关，改此头不影响 X-Ashe。
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

# ---------------------------------------------------------------- 签名部分 --

def md5_hex(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def gen_nami() -> str:
    """X-Nami 设备指纹: 随机 UUID 去横线后取「前4位 + "0" + 再取15位」，共 20 字符。

    文档说明一次会话内可复用，本脚本每次运行生成一次，所有请求共用。
    """
    hex32 = uuid.uuid4().hex
    return hex32[:4] + "0" + hex32[4:19]


def build_headers(city: str, nami: str) -> dict:
    """固定头 + 动态头。X-Ashe = MD5( MD5(lower(servername.methodname)) + X-Garen + X-Nami )，
    时间戳与签名每次请求现算。"""
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


def build_body(keyword: str, lat: float, lon: float, number: int) -> dict:
    """文档 4.2 请求体。搜索场景固定 promotion_sort=1、store_type=99；
    仅支持第一页，offset 固定 0。"""
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
            return zlib.decompress(raw, -zlib.MAX_WBITS)   # raw deflate
    return raw


def send_search(keyword: str, args, nami: str):
    """执行一次搜索请求（网络失败/非2xx 按退避重试）。

    返回 (http_status, payload_dict_or_None, error_msg_or_None)
    """
    body = build_body(keyword, args.lat, args.lon, args.number)
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    attempts = args.retries + 1
    last_err = "未知失败"

    for attempt in range(1, attempts + 1):
        if args.show_request:
            print("  --- 请求预览 ---")
            for k, v in build_headers(args.city, nami).items():
                print(f"  {k}: {v}")
            print(f"  body: {data.decode('utf-8')}")

        headers = build_headers(args.city, nami)   # 每次尝试重算时间戳与签名
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
            # 大概率触发腾讯云 WAF 风控，继续重试只会加重封禁
            return status, None, last_err + "（可能被 WAF 限流，请勿继续高频重试）"
        if attempt < attempts:
            wait = 2 * attempt
            print(f"  [重试 {attempt}/{attempts - 1}] {last_err}，{wait}s 后重试")
            time.sleep(wait)

    return None, None, last_err

# ---------------------------------------------------------------- 结果展示 --

def _fen_to_yuan(value) -> str:
    if value is None:
        return "?"
    return f"{value / 100:.2f}元"


def _fmt_distance(meters) -> str:
    if meters is None:
        return ""
    if meters >= 1000:
        return f"{meters / 1000:.2f}km"
    return f"{int(meters)}m"


def _hm(hour, minute) -> str:
    return f"{int(hour or 0):02d}:{int(minute or 0):02d}"


def render_promotion(idx: int, item: dict) -> str:
    store = item.get("store") or {}
    name = store.get("name") or "(店名缺失)"

    head = f"[{idx:02d}] {name}"
    meta = []
    dist = _fmt_distance(item.get("distance"))
    if dist:
        meta.append(f"距离={dist}")
    if store.get("store_id") is not None:
        meta.append(f"store_id={store['store_id']}")
    if item.get("promotion_id") is not None:
        meta.append(f"promo_id={item['promotion_id']}")
    if meta:
        head += "  " + " | ".join(meta)

    lines = [head]

    info2 = []
    if item.get("start_time_hour") is not None or item.get("end_time_hour") is not None:
        info2.append("时段 " + _hm(item.get("start_time_hour"), item.get("start_time_minute"))
                     + "~" + _hm(item.get("end_time_hour"), item.get("end_time_minute")))
    cond = REBATE_COND.get(item.get("rebate_condition"))
    if cond:
        info2.append(f"评价: {cond}")
    if info2:
        lines.append("     " + "   ".join(info2))

    offers = []
    for label, prefix in (("美团", "meituan"), ("饿了么", "eleme")):
        if item.get(prefix + "_status") == 1:
            offers.append(
                f"{label}: 满 {_fen_to_yuan(item.get(prefix + '_order_money'))} "
                f"返现 {_fen_to_yuan(item.get(prefix + '_user_rebate'))} "
                f"| 余 {item.get(prefix + '_left_number', '?')}"
            )
    tp = item.get("tp_promotion") or {}
    if tp.get("tp_status") == 1:
        offers.append(
            f"京东: 满 {_fen_to_yuan(tp.get('tp_order_money'))} "
            f"返现 {_fen_to_yuan(tp.get('tp_user_rebate'))} "
            f"| 余 {tp.get('tp_left_number', '?')}"
        )
    if offers:
        lines.extend("     " + o for o in offers)
    else:
        lines.append("     (该行无有效平台活动)")
    return "\n".join(lines)

# -------------------------------------------------------------------- 主流程 --

def run_case(keyword: str, args, nami: str) -> dict:
    """测试单个关键词，返回 {'keyword','ok','detail'}"""
    print(f"\n>>> 关键词: {keyword!r}")
    t0 = time.perf_counter()
    status, payload, err = send_search(keyword, args, nami)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    if err:
        print(f"    FAIL  耗时 {elapsed_ms:.0f}ms  {err}")
        return {"keyword": keyword, "ok": False, "detail": err}

    biz_code = (payload.get("status") or {}).get("code")
    biz_msg = (payload.get("status") or {}).get("msg", "")

    if args.raw_json:
        print("    原始响应:")
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    if biz_code != 0:
        detail = f"业务失败 status.code={biz_code} msg={biz_msg}"
        print(f"    FAIL  HTTP {status}  耗时 {elapsed_ms:.0f}ms  {detail}")
        return {"keyword": keyword, "ok": False, "detail": detail}

    promotions = payload.get("promotion_list")
    if not isinstance(promotions, list):
        promotions = []
        print(f"    WARN  业务成功但响应缺少 promotion_list，实际顶层键: {list(payload.keys())}")

    print(f"    PASS  HTTP {status}  code=0 ({biz_msg})  耗时 {elapsed_ms:.0f}ms  命中 {len(promotions)} 条 (number={args.number})")
    for i, item in enumerate(promotions, 1):
        print("  " + render_promotion(i, item))
    if not promotions:
        print("    (搜索无结果：该定位下此关键词无满减活动；注意搜索接口仅支持第一页)")

    return {"keyword": keyword, "ok": True, "detail": f"code=0 返回 {len(promotions)} 条"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="小蚕 4.2 满减活动搜索接口测试脚本",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("keywords", nargs="*", help="搜索关键词，可传多个；不传则交互式输入")
    parser.add_argument("--lat", type=float, default=DEFAULT_LAT, help="纬度")
    parser.add_argument("--lon", type=float, default=DEFAULT_LON, help="经度")
    parser.add_argument("--city", default=DEFAULT_CITY, help="城市行政编码（x-City 请求头）")
    parser.add_argument("--number", type=int, default=15, help="每页条数（搜索建议 15）")
    parser.add_argument("--timeout", type=float, default=8.0, help="请求超时（秒，文档建议 3~10）")
    parser.add_argument("--retries", type=int, default=1, help="网络失败/非2xx 时的重试次数（退避）")
    parser.add_argument("--delay", type=float, default=3.0, help="多关键词之间的间隔秒数（防风控）")
    parser.add_argument("--json", dest="raw_json", action="store_true", help="打印原始 JSON 响应")
    parser.add_argument("--show-request", action="store_true", help="发送前打印请求头与请求体（调试签名）")
    args = parser.parse_args(argv)

    keywords = [k.strip() for k in args.keywords if k and k.strip()]
    if not keywords:
        try:
            kw = input("请输入搜索关键词: ").strip()
        except EOFError:
            kw = ""
        if kw:
            keywords = [kw]
        else:
            print("未提供关键词，退出。")
            return 2

    print("=" * 62)
    print("小蚕 HTTP 接口测试 —— 4.2 满减活动搜索")
    print(f"  网关        : https://{GATEWAY_HOST}{GATEWAY_PATH}")
    print(f"  RPC 方法    : {SERVERNAME} / {METHODNAME}")
    print(f"  定位        : lat={args.lat}, lon={args.lon}, x-City={args.city}")
    print(f"  请求体固定值: offset=0, number={args.number}, promotion_sort=1, store_type=99, silk_id=0, app_id=20")
    print("=" * 62)

    nami = gen_nami()   # 设备指纹会话内复用
    results = []
    for i, kw in enumerate(keywords):
        if i > 0:
            time.sleep(args.delay)   # 控制频率，避免触发 WAF
        results.append(run_case(kw, args, nami))

    print("\n" + "=" * 62)
    print("测试汇总")
    ok_cnt = sum(1 for r in results if r["ok"])
    for r in results:
        print(f"  [{'PASS' if r['ok'] else 'FAIL'}] {r['keyword']:<12} {r['detail']}")
    print(f"  成功 {ok_cnt} / {len(results)}")
    return 0 if ok_cnt == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
