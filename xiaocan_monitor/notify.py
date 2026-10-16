"""钉钉自定义机器人加签推送。

仅依赖标准库。任何返回值、异常与日志都不得泄露 secret。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request

try:
    from .proxy import Socks5Proxy
except ImportError:  # 允许按文件路径直接导入
    from proxy import Socks5Proxy


def sign_params(secret: str, timestamp_ms: int) -> dict[str, str]:
    """按钉钉加签算法返回 {"timestamp": str, "sign": str}。"""
    string_to_sign = f"{timestamp_ms}\n{secret}"
    hmac_code = hmac.new(
        secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    sign = base64.b64encode(hmac_code).decode("utf-8")
    return {"timestamp": str(timestamp_ms), "sign": sign}


def signed_url(webhook: str, secret: str, timestamp_ms: int) -> str:
    """在 webhook 后追加 timestamp 与 sign（URL 编码）。"""
    params = sign_params(secret, timestamp_ms)
    query = urllib.parse.urlencode(params)
    sep = "&" if "?" in webhook else "?"
    return f"{webhook}{sep}{query}"


def default_post_json(url: str, payload: dict, timeout: float) -> dict:
    """POST JSON 并解析响应，返回 dict。HTTP/URL/JSON 错误直接抛出。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body)


def make_post_json(proxy: "Socks5Proxy | None" = None):
    """构造一个 post_json；传入 ``proxy`` 时钉钉请求经 SOCKS5 代理转发。"""
    if proxy is None:
        return default_post_json

    opener = urllib.request.build_opener(proxy.https_handler())

    def post_json(url: str, payload: dict, timeout: float) -> dict:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
        return json.loads(body)

    return post_json


class DingTalkNotifier:
    """钉钉 markdown 推送（加签模式）。"""

    def __init__(
        self,
        webhook: str,
        secret: str,
        title: str = "小蚕余量监控",
        *,
        timeout: float = 8.0,
        post_json=None,
        now_ms=None,
        proxy: "Socks5Proxy | None" = None,
    ) -> None:
        self.webhook = webhook or ""
        self.secret = secret or ""
        self.title = title
        self.timeout = timeout
        self._post_json = post_json if post_json is not None else make_post_json(proxy)
        self._now_ms = now_ms

    def configured(self) -> bool:
        """webhook 非空即视为已配置。"""
        return bool(self.webhook)

    def _timestamp_ms(self) -> int:
        if self._now_ms is not None:
            return int(self._now_ms())
        return int(time.time() * 1000)

    def send_markdown(self, title: str, text: str) -> tuple[bool, str]:
        """发送 markdown 消息，返回 (ok, message)。"""
        if not self.configured():
            return (False, "未配置钉钉 webhook")
        try:
            url = signed_url(self.webhook, self.secret, self._timestamp_ms())
            payload = {
                "msgtype": "markdown",
                "markdown": {"title": title, "text": text},
            }
            resp = self._post_json(url, payload, self.timeout)
            if isinstance(resp, dict) and resp.get("errcode") == 0:
                return (True, "推送成功")
            errcode = resp.get("errcode") if isinstance(resp, dict) else None
            errmsg = resp.get("errmsg") if isinstance(resp, dict) else ""
            return (False, f"钉钉返回 errcode={errcode} errmsg={errmsg}")
        except Exception as exc:  # noqa: BLE001
            return (False, f"钉钉推送异常: {type(exc).__name__}")
