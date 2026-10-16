# -*- coding: utf-8 -*-
"""SOCKS5 代理：地址解析、握手与代理感知连接（用本地假代理，无外网）。"""

import socket
import struct
import threading
import unittest

from xiaocan_monitor import proxy as proxy_mod
from xiaocan_monitor.proxy import Socks5Proxy, ProxyError, parse_proxy


class ParseProxyTest(unittest.TestCase):
    def test_empty_returns_none(self):
        self.assertIsNone(parse_proxy(None))
        self.assertIsNone(parse_proxy(""))
        self.assertIsNone(parse_proxy("   "))

    def test_scheme_and_defaults(self):
        p = parse_proxy("socks5://127.0.0.1:1080")
        self.assertEqual(p.host, "127.0.0.1")
        self.assertEqual(p.port, 1080)
        self.assertIsNone(p.username)
        self.assertIsNone(p.password)
        self.assertFalse(p.remote_dns)
        self.assertFalse(p.requires_auth)

    def test_socks5h_uses_remote_dns(self):
        self.assertTrue(parse_proxy("socks5h://10.0.0.1:1080").remote_dns)
        self.assertTrue(parse_proxy("socks://10.0.0.1").remote_dns)

    def test_host_only_uses_default_port(self):
        p = parse_proxy("socks5://proxy.example")
        self.assertEqual(p.host, "proxy.example")
        self.assertEqual(p.port, proxy_mod.DEFAULT_PROXY_PORT)

    def test_userinfo(self):
        p = parse_proxy("socks5://alice:s3cr3t@proxy.example:1080")
        self.assertEqual(p.username, "alice")
        self.assertEqual(p.password, "s3cr3t")
        self.assertTrue(p.requires_auth)

    def test_userinfo_percent_decoded(self):
        p = parse_proxy("socks5://a%40b:p%3Aw@proxy.example:1080")
        self.assertEqual(p.username, "a@b")
        self.assertEqual(p.password, "p:w")

    def test_username_without_password(self):
        p = parse_proxy("socks5://onlyuser@proxy.example:1080")
        self.assertEqual(p.username, "onlyuser")
        self.assertEqual(p.password, "")

    def test_ipv6_bracket(self):
        p = parse_proxy("socks5://[::1]:1080")
        self.assertEqual(p.host, "::1")
        self.assertEqual(p.port, 1080)

    def test_no_scheme_host_port(self):
        p = parse_proxy("127.0.0.1:1081")
        self.assertEqual(p.host, "127.0.0.1")
        self.assertEqual(p.port, 1081)

    def test_unsupported_scheme(self):
        with self.assertRaises(ProxyError):
            parse_proxy("http://127.0.0.1:8080")

    def test_bad_port(self):
        with self.assertRaises(ProxyError):
            parse_proxy("socks5://127.0.0.1:notaport")
        with self.assertRaises(ProxyError):
            parse_proxy("socks5://127.0.0.1:70000")

    def test_missing_host(self):
        with self.assertRaises(ProxyError):
            parse_proxy("socks5://:1080")


class EncodeDestinationTest(unittest.TestCase):
    def test_ipv4_literal(self):
        atyp, addr, port = proxy_mod._encode_destination(
            "127.0.0.1", 443, True, None
        )
        self.assertEqual(atyp, proxy_mod.ATYP_IPV4)
        self.assertEqual(addr, socket.inet_pton(socket.AF_INET, "127.0.0.1"))
        self.assertEqual(port, 443)

    def test_ipv6_literal(self):
        atyp, addr, port = proxy_mod._encode_destination("::1", 80, True, None)
        self.assertEqual(atyp, proxy_mod.ATYP_IPV6)
        self.assertEqual(addr, socket.inet_pton(socket.AF_INET6, "::1"))

    def test_domain_remote_dns(self):
        atyp, addr, port = proxy_mod._encode_destination(
            "gw.xiaocantech.com", 443, True, None
        )
        self.assertEqual(atyp, proxy_mod.ATYP_DOMAIN)
        self.assertEqual(addr[0], len("gw.xiaocantech.com"))
        self.assertEqual(addr[1:].decode("ascii"), "gw.xiaocantech.com")
        self.assertEqual(port, 443)


class _FakeSocks5Server:
    """最小 SOCKS5 服务端：完成握手后把流量回显给客户端。"""

    def __init__(self, *, require_auth=False, username="u", password="p"):
        self.require_auth = require_auth
        self.username = username
        self.password = password
        self.received = {}
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.host, self.port = self._sock.getsockname()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _recv_exact(self, conn, n):
        buf = b""
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("client closed")
            buf += chunk
        return buf

    def _serve(self):
        conn, _ = self._sock.accept()
        try:
            head = self._recv_exact(conn, 2)
            n_methods = head[1]
            methods = self._recv_exact(conn, n_methods)
            self.received["methods"] = list(methods)
            if self.require_auth:
                if proxy_mod.METHOD_USERNAME_PASSWORD not in methods:
                    conn.sendall(bytes([5, proxy_mod.METHOD_NO_ACCEPTABLE]))
                    return
                conn.sendall(bytes([5, proxy_mod.METHOD_USERNAME_PASSWORD]))
                self._server_auth(conn)
            else:
                conn.sendall(bytes([5, proxy_mod.METHOD_NO_AUTH]))

            req = self._recv_exact(conn, 4)
            atyp = req[3]
            self.received["atyp"] = atyp
            if atyp == proxy_mod.ATYP_IPV4:
                self.received["dest"] = socket.inet_ntoa(self._recv_exact(conn, 4))
            elif atyp == proxy_mod.ATYP_DOMAIN:
                length = self._recv_exact(conn, 1)[0]
                self.received["dest"] = self._recv_exact(conn, length).decode()
            elif atyp == proxy_mod.ATYP_IPV6:
                self.received["dest"] = socket.inet_ntop(
                    socket.AF_INET6, self._recv_exact(conn, 16)
                )
            self.received["port"] = struct.unpack("!H", self._recv_exact(conn, 2))[0]

            conn.sendall(
                bytes([5, 0, 0, proxy_mod.ATYP_IPV4])
                + socket.inet_pton(socket.AF_INET, "127.0.0.1")
                + struct.pack("!H", 0)
            )
            # 回显
            while True:
                data = conn.recv(4096)
                if not data:
                    break
                conn.sendall(data)
        except Exception:
            pass
        finally:
            conn.close()
            self._sock.close()

    def _server_auth(self, conn):
        head = self._recv_exact(conn, 2)
        uname = self._recv_exact(conn, head[1]).decode()
        plen = self._recv_exact(conn, 1)[0]
        passwd = self._recv_exact(conn, plen).decode()
        self.received["user"] = uname
        self.received["pass"] = passwd
        if uname == self.username and passwd == self.password:
            conn.sendall(bytes([1, 0]))
        else:
            conn.sendall(bytes([1, 1]))

    def close(self):
        self._thread.join(timeout=5)


class ConnectHandshakeTest(unittest.TestCase):
    def test_no_auth_tunnel_echo(self):
        server = _FakeSocks5Server()
        try:
            client = Socks5Proxy(host=server.host, port=server.port, remote_dns=True)
            sock = client.connect("gw.xiaocantech.com", 443, timeout=5)
            sock.sendall(b"ping")
            self.assertEqual(sock.recv(4), b"ping")
            sock.close()
        finally:
            server.close()
        self.assertEqual(server.received["dest"], "gw.xiaocantech.com")
        self.assertEqual(server.received["port"], 443)
        self.assertEqual(server.received["atyp"], proxy_mod.ATYP_DOMAIN)
        self.assertEqual(server.received["methods"], [proxy_mod.METHOD_NO_AUTH])

    def test_username_password_auth(self):
        server = _FakeSocks5Server(require_auth=True, username="u", password="p")
        try:
            client = Socks5Proxy(
                host=server.host,
                port=server.port,
                username="u",
                password="p",
                remote_dns=True,
            )
            sock = client.connect("example.com", 80, timeout=5)
            sock.sendall(b"x")
            self.assertEqual(sock.recv(1), b"x")
            sock.close()
        finally:
            server.close()
        self.assertEqual(server.received["user"], "u")
        self.assertEqual(server.received["pass"], "p")

    def test_auth_failure_raises(self):
        server = _FakeSocks5Server(require_auth=True, username="u", password="p")
        try:
            client = Socks5Proxy(
                host=server.host,
                port=server.port,
                username="u",
                password="wrong",
                remote_dns=True,
            )
            with self.assertRaises(ProxyError):
                client.connect("example.com", 80, timeout=5)
        finally:
            server.close()

    def test_connect_refused_proxy(self):
        # 找一个基本不会被监听的端口
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
        probe.close()

        client = Socks5Proxy(host="127.0.0.1", port=free_port)
        with self.assertRaises(ProxyError):
            client.connect("example.com", 443, timeout=2)


class _Recorder:
    """记录 post_once 收到的 proxy 参数。"""

    def __init__(self):
        self.calls = []

    def __call__(self, headers, data, timeout, proxy=None):
        self.calls.append(proxy)
        return (200, "", b'{"status": {"code": 0}}')


class RpcClientProxyWiringTest(unittest.TestCase):
    def test_client_passes_proxy_to_post_once(self):
        from xiaocan_monitor import rpc

        recorder = _Recorder()
        original = rpc.post_once
        rpc.post_once = recorder
        try:
            proxy = Socks5Proxy(host="127.0.0.1", port=1080)
            client = rpc.RpcClient(
                timeout=1.0,
                retries=0,
                nami="a1b20c3d4e5f6a7b8c9d",
                logger=lambda msg: None,
                proxy=proxy,
                sleep=lambda s: None,
            )
            result = client.call("SilkwormRec", "M", {}, "110100")
            self.assertTrue(result.ok)
            self.assertEqual(recorder.calls, [proxy])
        finally:
            rpc.post_once = original

    def test_client_without_proxy_calls_bare_post_once(self):
        from xiaocan_monitor import rpc

        calls = []

        def fake_post(headers, data, timeout):
            calls.append(True)
            return (200, "", b'{"status": {"code": 0}}')

        client = rpc.RpcClient(
            timeout=1.0,
            retries=0,
            nami="a1b20c3d4e5f6a7b8c9d",
            logger=lambda msg: None,
            http_post=fake_post,
            sleep=lambda s: None,
        )
        result = client.call("SilkwormRec", "M", {}, "110100")
        self.assertTrue(result.ok)
        self.assertEqual(len(calls), 1)


class NotifyProxyWiringTest(unittest.TestCase):
    def test_notifier_uses_proxy_handler(self):
        import urllib.request

        from xiaocan_monitor import notify

        captured = {}

        def fake_https_handler(self):
            captured["proxy"] = self
            return urllib.request.HTTPSHandler()

        original = Socks5Proxy.https_handler
        Socks5Proxy.https_handler = fake_https_handler
        try:
            proxy = Socks5Proxy(host="127.0.0.1", port=1080)
            notify.DingTalkNotifier(
                "https://oapi.dingtalk.com/robot/send?access_token=x",
                "SEC",
                "标题",
                proxy=proxy,
            )
            self.assertIs(captured.get("proxy"), proxy)
        finally:
            Socks5Proxy.https_handler = original

    def test_notifier_without_proxy_uses_default(self):
        from xiaocan_monitor import notify

        notifier = notify.DingTalkNotifier("hook", "SEC")
        self.assertIs(notifier._post_json, notify.default_post_json)


if __name__ == "__main__":
    unittest.main()
