#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小蚕网关 RPC 客户端：签名、请求头、压缩解压、退避重试。

完全对齐仓库根目录测试脚本 xiaocan_search_test.py / xiaocan_store_track_test.py。
仅用标准库。
"""

from __future__ import annotations

import gzip
import hashlib
import http.client
import json
import socket
import time
import uuid
import zlib
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Tuple

GATEWAY_HOST = "gw.xiaocantech.com"
GATEWAY_PATH = "/rpc"

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


@dataclass
class RpcResult:
    """一次 RPC 的结果。"""

    http_status: Optional[int]
    payload: Optional[dict]
    error: Optional[str]

    @property
    def ok(self) -> bool:
        """传输层成功且拿到了 2xx 的 JSON 响应体。"""
        return self.error is None and isinstance(self.payload, dict)


def md5_hex(text: str) -> str:
    """返回 UTF-8 文本的 MD5 十六进制串。"""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def gen_nami() -> str:
    """X-Nami 设备指纹：uuid4 去掉横线后取前 4 位 + "0" + 再取 15 位，共 20 字符。"""
    hex32 = uuid.uuid4().hex
    return hex32[:4] + "0" + hex32[4:19]


def build_headers(
    servername: str,
    methodname: str,
    city: str,
    nami: str,
    *,
    now_ms: Optional[int] = None,
) -> dict:
    """固定头 + 动态头；签名与 servername.methodname 绑定，换接口必须重算。"""
    garen = str(int(now_ms if now_ms is not None else time.time() * 1000))
    x = md5_hex(f"{servername}.{methodname}".lower())
    ashe = md5_hex(f"{x}{garen}{nami}")
    headers = dict(BASE_HEADERS)
    headers.update(
        {
            "servername": servername,
            "methodname": methodname,
            "X-Garen": garen,
            "X-Nami": nami,
            "X-Ashe": ashe,
            "x-City": str(city),
        }
    )
    return headers


def post_once(headers: dict, data: bytes, timeout: float) -> Tuple[int, str, bytes]:
    """发送一次 POST，返回 (http_status, content_encoding 小写, 原始字节)。"""
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
    """按 Content-Encoding 解压，deflate 兼容 raw-deflate。"""
    if content_encoding == "gzip":
        return gzip.decompress(raw)
    if content_encoding == "deflate":
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw


class RpcClient:
    """带限流、签名与退避重试的 RPC 客户端。"""

    def __init__(
        self,
        *,
        timeout: float,
        retries: int,
        limiter: Any = None,
        nami: Optional[str] = None,
        logger: Callable[[str], None] = print,
        http_post: Callable[[dict, bytes, float], Tuple[int, str, bytes]] = post_once,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.limiter = limiter
        self.nami = nami or gen_nami()
        self.logger = logger
        self.http_post = http_post
        self.sleep = sleep

    def call(
        self, servername: str, methodname: str, body: dict, city: str
    ) -> RpcResult:
        """执行一次 RPC 调用（含退避重试）。"""
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        attempts = self.retries + 1
        last_err = "未知失败"

        for attempt in range(1, attempts + 1):
            if self.limiter is not None:
                self.limiter.wait()

            headers = build_headers(servername, methodname, city, self.nami)
            try:
                status, enc, raw = self.http_post(headers, data, self.timeout)
            except (OSError, socket.timeout, http.client.HTTPException) as exc:
                last_err = f"网络异常: {type(exc).__name__}: {exc}"
                if attempt < attempts:
                    wait = 2 * attempt
                    self.logger(f"  [重试 {attempt}/{attempts - 1}] {last_err}，{wait}s 后重试")
                    self.sleep(wait)
                    continue
                return RpcResult(None, None, last_err)

            if 200 <= status < 300:
                text = _decompress(raw, enc).decode("utf-8", "replace")
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    snippet = text[:300].replace("\n", " ")
                    return RpcResult(
                        status,
                        None,
                        f"响应不是 JSON（疑似 WAF 拦截页/网关错误）: {snippet}",
                    )
                return RpcResult(status, payload, None)

            last_err = f"HTTP {status} 非 2xx"
            if status in (401, 403, 405, 429):
                return RpcResult(
                    status, None, last_err + "（可能被 WAF 限流，请勿继续高频重试）"
                )
            if attempt < attempts:
                wait = 2 * attempt
                self.logger(f"  [重试 {attempt}/{attempts - 1}] {last_err}，{wait}s 后重试")
                self.sleep(wait)

        return RpcResult(None, None, last_err)
