#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SOCKS5 代理支持（RFC 1928 / RFC 1929）。

仅用标准库实现：
- ``parse_proxy`` 解析 ``socks5://user:pass@host:port`` 形式的代理地址；
- ``Socks5Proxy.connect`` 完成握手并返回一条到目标主机的已连接 socket；
- ``Socks5Proxy.https_connection`` 返回一个经代理建连的 ``HTTPSConnection``，
  供 ``http.client``（网关 RPC）使用；
- ``Socks5Proxy.https_handler`` 返回一个 ``urllib.request`` 处理器，
  供 ``urllib``（钉钉推送）使用。

约定：
- 方案 ``socks5h`` 表示由代理远端解析域名（ATYP=域名），``socks5`` 表示本地解析
  后再把 IP 发给代理；域名已是 IP 字面量时直接发 IP。
- 任何网络/协议错误都抛 ``ProxyError``（``OSError`` 子类），以便上层统一按网络异常处理。
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import struct
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional, Tuple

SOCKS_VERSION = 5
AUTH_VERSION = 1

METHOD_NO_AUTH = 0x00
METHOD_USERNAME_PASSWORD = 0x02
METHOD_NO_ACCEPTABLE = 0xFF

CMD_CONNECT = 0x01
ATYP_IPV4 = 0x01
ATYP_DOMAIN = 0x03
ATYP_IPV6 = 0x04

REP_SUCCESS = 0x00

REP_MESSAGES = {
    0x01: "通用 SOCKS 服务器故障",
    0x02: "连接不被允许",
    0x03: "网络不可达",
    0x04: "主机不可达",
    0x05: "连接被拒绝",
    0x06: "TTL 超时",
    0x07: "不支持的命令",
    0x08: "不支持的地址类型",
}

DEFAULT_PROXY_PORT = 1080
DEFAULT_TIMEOUT = 8.0


class ProxyError(OSError):
    """代理握手或隧道建立失败。"""


def _resolve_local(host: str, port: int, timeout: Optional[float]) -> Tuple[int, bytes, int]:
    """本地解析域名，返回 (ATYP, 打包后的地址, 端口)。"""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ProxyError(f"无法解析目标主机 {host}: {exc}") from exc
    family, _, _, _, sockaddr = infos[0]
    ip = sockaddr[0]
    if family == socket.AF_INET6:
        return ATYP_IPV6, socket.inet_pton(socket.AF_INET6, ip), port
    return ATYP_IPV4, socket.inet_pton(socket.AF_INET, ip), port


def _encode_destination(
    host: str, port: int, remote_dns: bool, timeout: Optional[float]
) -> Tuple[int, bytes, int]:
    """把目标地址编码为 SOCKS5 的 ATYP + 地址字节 + 端口。"""
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None

    if literal is not None:
        if literal.version == 6:
            return ATYP_IPV6, literal.packed, port
        return ATYP_IPV4, literal.packed, port

    if remote_dns:
        encoded = host.encode("idna") if not host.isascii() else host.encode("ascii")
        if len(encoded) > 255:
            raise ProxyError(f"目标域名过长，无法经 SOCKS5 发送: {host}")
        return ATYP_DOMAIN, bytes([len(encoded)]) + encoded, port

    return _resolve_local(host, port, timeout)


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    """读取恰好 ``count`` 字节，连接提前关闭时抛错。"""
    chunks = bytearray()
    while len(chunks) < count:
        chunk = sock.recv(count - len(chunks))
        if not chunk:
            raise ProxyError("SOCKS5 代理在握手中途关闭连接")
        chunks.extend(chunk)
    return bytes(chunks)


@dataclass(frozen=True)
class Socks5Proxy:
    """一条 SOCKS5 代理配置。"""

    host: str
    port: int = DEFAULT_PROXY_PORT
    username: Optional[str] = None
    password: Optional[str] = None
    remote_dns: bool = True

    @property
    def requires_auth(self) -> bool:
        return bool(self.username or self.password)

    # ------------------------------------------------------------ 握手 --

    def _select_method(self, sock: socket.socket) -> None:
        if self.requires_auth:
            methods = bytes([METHOD_USERNAME_PASSWORD])
        else:
            methods = bytes([METHOD_NO_AUTH])
        sock.sendall(bytes([SOCKS_VERSION, len(methods)]) + methods)
        reply = _recv_exact(sock, 2)
        if reply[0] != SOCKS_VERSION:
            raise ProxyError(f"SOCKS5 代理版本不受支持: {reply[0]}")
        chosen = reply[1]
        if chosen == METHOD_NO_ACCEPTABLE:
            raise ProxyError("SOCKS5 代理拒绝所有认证方式")
        if chosen == METHOD_USERNAME_PASSWORD:
            self._authenticate(sock)
        elif chosen != METHOD_NO_AUTH:
            raise ProxyError(f"SOCKS5 代理返回未知认证方式: {chosen}")

    def _authenticate(self, sock: socket.socket) -> None:
        username = (self.username or "").encode("utf-8")
        password = (self.password or "").encode("utf-8")
        if len(username) > 255 or len(password) > 255:
            raise ProxyError("SOCKS5 用户名或密码过长（上限 255 字节）")
        request = (
            bytes([AUTH_VERSION, len(username)])
            + username
            + bytes([len(password)])
            + password
        )
        sock.sendall(request)
        reply = _recv_exact(sock, 2)
        if reply[0] != AUTH_VERSION:
            raise ProxyError(f"SOCKS5 认证响应版本异常: {reply[0]}")
        if reply[1] != 0x00:
            raise ProxyError("SOCKS5 用户名/密码认证失败")

    def _send_connect(self, sock: socket.socket, host: str, port: int, timeout) -> None:
        atyp, addr, port = _encode_destination(host, port, self.remote_dns, timeout)
        request = bytes([SOCKS_VERSION, CMD_CONNECT, 0x00, atyp]) + addr + struct.pack("!H", port)
        sock.sendall(request)

        header = _recv_exact(sock, 4)
        if header[0] != SOCKS_VERSION:
            raise ProxyError(f"SOCKS5 代理版本不受支持: {header[0]}")
        if header[1] != REP_SUCCESS:
            message = REP_MESSAGES.get(header[1], f"未知错误码 {header[1]}")
            raise ProxyError(f"SOCKS5 连接失败: {message}")

        reply_atyp = header[3]
        if reply_atyp == ATYP_IPV4:
            _recv_exact(sock, 4)
        elif reply_atyp == ATYP_IPV6:
            _recv_exact(sock, 16)
        elif reply_atyp == ATYP_DOMAIN:
            length = _recv_exact(sock, 1)[0]
            _recv_exact(sock, length)
        else:
            raise ProxyError(f"SOCKS5 代理返回未知地址类型: {reply_atyp}")
        _recv_exact(sock, 2)  # BND.PORT

    def connect(
        self,
        dest_host: str,
        dest_port: int,
        timeout: Optional[float] = None,
        source_address: Optional[Tuple[str, int]] = None,
    ) -> socket.socket:
        """经代理连到 ``dest_host:dest_port``，返回已建立的 socket。"""
        if timeout is None:
            timeout = DEFAULT_TIMEOUT
        try:
            sock = socket.create_connection(
                (self.host, self.port), timeout=timeout, source_address=source_address
            )
        except OSError as exc:
            raise ProxyError(
                f"无法连接 SOCKS5 代理 {self.host}:{self.port}: {exc}"
            ) from exc
        try:
            sock.settimeout(timeout)
            self._select_method(sock)
            self._send_connect(sock, dest_host, dest_port, timeout)
        except BaseException:
            try:
                sock.close()
            except OSError:
                pass
            raise
        return sock

    # ------------------------------------------------- 连接/处理器工厂 --

    def https_connection(
        self,
        host: str,
        port: Optional[int] = None,
        timeout: Optional[float] = None,
        context=None,
    ) -> http.client.HTTPSConnection:
        """返回一个经本代理建连的 ``HTTPSConnection``。"""
        proxy = self

        class _Socks5HTTPSConnection(http.client.HTTPSConnection):
            def connect(self) -> None:  # pragma: no cover - 真实网络路径
                self.sock = proxy.connect(self.host, self.port, self.timeout)
                tunnel = getattr(self, "_tunnel_host", None)
                server_hostname = tunnel or self.host
                self.sock = self._context.wrap_socket(
                    self.sock, server_hostname=server_hostname
                )

        if port is None:
            return _Socks5HTTPSConnection(host, timeout=timeout, context=context)
        return _Socks5HTTPSConnection(host, port, timeout=timeout, context=context)

    def https_handler(self, context=None):
        """返回一个把 HTTPS 请求经本代理转发的 ``urllib.request`` 处理器。

        ``context`` 为可选的 ``ssl.SSLContext``；缺省时由 ``urllib`` 使用默认上下文。
        """
        proxy = self

        class _Socks5HTTPSConnection(http.client.HTTPSConnection):
            def connect(self) -> None:  # pragma: no cover - 真实网络路径
                self.sock = proxy.connect(self.host, self.port, self.timeout)
                tunnel = getattr(self, "_tunnel_host", None)
                server_hostname = tunnel or self.host
                self.sock = self._context.wrap_socket(
                    self.sock, server_hostname=server_hostname
                )

        class _Socks5HTTPSHandler(urllib.request.HTTPSHandler):
            def https_open(self, req):
                return self.do_open(
                    _Socks5HTTPSConnection, req, context=self._context
                )

        if context is None:
            return _Socks5HTTPSHandler()
        return _Socks5HTTPSHandler(context=context)


def parse_proxy(value: Optional[str]) -> Optional[Socks5Proxy]:
    """把配置字符串解析为 ``Socks5Proxy``；空值返回 ``None``。

    支持 ``socks5://`` / ``socks5h://`` / ``socks://``，也可省略方案直接写
    ``host:port``；用户名/密码写在 userinfo 中。
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None

    remote_dns = True
    if "://" in text:
        scheme, _, rest = text.partition("://")
        scheme = scheme.lower()
        if scheme not in ("socks5", "socks5h", "socks"):
            raise ProxyError(f"不支持的代理方案: {scheme}（仅支持 socks5/socks5h）")
        remote_dns = scheme != "socks5"
        text = rest

    if "@" in text:
        userinfo, _, text = text.rpartition("@")
        if ":" in userinfo:
            username, _, password = userinfo.partition(":")
        else:
            username, password = userinfo, ""
        username = urllib.parse.unquote(username)
        password = urllib.parse.unquote(password)
    else:
        username = password = None

    host, port = _split_host_port(text)
    return Socks5Proxy(
        host=host,
        port=port,
        username=username or None,
        password=password,
        remote_dns=remote_dns,
    )


def _split_host_port(text: str) -> Tuple[str, int]:
    """从 ``host`` / ``host:port`` / ``[ipv6]:port`` 中拆出主机与端口。"""
    text = text.strip().strip("/")
    if not text:
        raise ProxyError("代理地址缺少主机名")

    if text.startswith("["):
        end = text.find("]")
        if end == -1:
            raise ProxyError(f"代理地址 IPv6 括号不匹配: {text}")
        host = text[1:end]
        remainder = text[end + 1 :]
        if remainder.startswith(":"):
            return host, _parse_port(remainder[1:])
        if remainder:
            raise ProxyError(f"代理地址格式错误: {text}")
        return host, DEFAULT_PROXY_PORT

    if ":" in text:
        host, _, port_text = text.rpartition(":")
        if not host:
            raise ProxyError(f"代理地址缺少主机名: {text}")
        return host, _parse_port(port_text)

    return text, DEFAULT_PROXY_PORT


def _parse_port(text: str) -> int:
    try:
        port = int(text)
    except (TypeError, ValueError) as exc:
        raise ProxyError(f"代理端口不是数字: {text!r}") from exc
    if not 1 <= port <= 65535:
        raise ProxyError(f"代理端口超出范围: {port}")
    return port
