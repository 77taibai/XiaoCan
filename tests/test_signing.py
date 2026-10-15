# -*- coding: utf-8 -*-
"""rpc 签名头与 RpcClient.call 的退避/解压/限流行为（无网络）。"""

import gzip
import hashlib
import json
import unittest
import zlib

from xiaocan_monitor import rpc


SERVER = "SilkwormRec"
METHOD = "RecService.SearchStorePromotionList"
CITY = "110100"
NAMI = "a1b20c3d4e5f6a7b8c9d"


class BuildHeadersTest(unittest.TestCase):
    def test_signature_matches_independent_md5(self):
        now_ms = 1700000000123
        headers = rpc.build_headers(SERVER, METHOD, CITY, NAMI, now_ms=now_ms)

        x = hashlib.md5(f"{SERVER}.{METHOD}".lower().encode("utf-8")).hexdigest()
        expected_ashe = hashlib.md5(
            f"{x}{now_ms}{NAMI}".encode("utf-8")
        ).hexdigest()

        self.assertEqual(headers["X-Garen"], str(now_ms))
        self.assertEqual(headers["X-Nami"], NAMI)
        self.assertEqual(headers["X-Ashe"], expected_ashe)
        self.assertEqual(headers["servername"], SERVER)
        self.assertEqual(headers["methodname"], METHOD)
        self.assertEqual(headers["x-City"], CITY)

    def test_fixed_headers_present(self):
        headers = rpc.build_headers(SERVER, METHOD, CITY, NAMI, now_ms=1)
        for key in ("Content-Type", "appid", "X-Platform", "X-Model", "x-Annie", "Referer"):
            self.assertIn(key, headers)

    def test_base_headers_not_mutated(self):
        before = dict(rpc.BASE_HEADERS)
        rpc.build_headers(SERVER, METHOD, CITY, NAMI, now_ms=42)
        self.assertEqual(rpc.BASE_HEADERS, before)
        for key in ("servername", "methodname", "X-Garen", "X-Nami", "X-Ashe", "x-City"):
            self.assertNotIn(key, rpc.BASE_HEADERS)

    def test_gen_nami_shape(self):
        nami = rpc.gen_nami()
        self.assertEqual(len(nami), 20)
        self.assertEqual(nami[4], "0")
        self.assertTrue(all(c in "0123456789abcdef" for c in nami))

    def test_gen_nami_distinct(self):
        samples = {rpc.gen_nami() for _ in range(5)}
        self.assertEqual(len(samples), 5)


class _FakeLimiter:
    def __init__(self):
        self.calls = 0

    def wait(self):
        self.calls += 1


class _SeqHttpPost:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def __call__(self, headers, data, timeout):
        self.calls += 1
        item = self.responses[min(self.calls - 1, len(self.responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return item


def _client(http_post, retries, limiter=None):
    return rpc.RpcClient(
        timeout=1.0,
        retries=retries,
        limiter=limiter,
        nami=NAMI,
        logger=lambda msg: None,
        http_post=http_post,
        sleep=lambda seconds: None,
    )


class RpcClientCallTest(unittest.TestCase):
    def test_transport_exception_retries_retries_plus_one(self):
        http = _SeqHttpPost([OSError("boom")])
        limiter = _FakeLimiter()
        result = _client(http, retries=2, limiter=limiter).call(SERVER, METHOD, {}, CITY)

        self.assertEqual(http.calls, 3)
        self.assertEqual(limiter.calls, 3)
        self.assertIsNone(result.http_status)
        self.assertIsNone(result.payload)
        self.assertIn("网络异常", result.error)
        self.assertFalse(result.ok)

    def test_http_403_returns_immediately(self):
        http = _SeqHttpPost([(403, "", b"")])
        limiter = _FakeLimiter()
        result = _client(http, retries=3, limiter=limiter).call(SERVER, METHOD, {}, CITY)

        self.assertEqual(http.calls, 1)
        self.assertEqual(limiter.calls, 1)
        self.assertEqual(result.http_status, 403)
        self.assertIn("HTTP 403", result.error)
        self.assertIsNone(result.payload)

    def test_non_waf_http_error_retries(self):
        http = _SeqHttpPost([(500, "", b"")])
        result = _client(http, retries=1).call(SERVER, METHOD, {}, CITY)

        self.assertEqual(http.calls, 2)
        self.assertIn("HTTP 500", result.error)

    def test_gzip_response_decoded(self):
        payload = {"status": {"code": 0}, "promotion_list": []}
        raw = gzip.compress(json.dumps(payload).encode("utf-8"))
        http = _SeqHttpPost([(200, "gzip", raw)])
        result = _client(http, retries=0).call(SERVER, METHOD, {}, CITY)

        self.assertEqual(result.payload, payload)
        self.assertTrue(result.ok)

    def test_raw_deflate_response_decoded(self):
        payload = {"status": {"code": 0}, "promotion_list": [{"x": 1}]}
        compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
        data = json.dumps(payload).encode("utf-8")
        raw = compressor.compress(data) + compressor.flush()
        http = _SeqHttpPost([(200, "deflate", raw)])
        result = _client(http, retries=0).call(SERVER, METHOD, {}, CITY)

        self.assertEqual(result.payload, payload)
        self.assertTrue(result.ok)

    def test_non_json_2xx_returns_error(self):
        http = _SeqHttpPost([(200, "", b"<html>WAF</html>")])
        result = _client(http, retries=0).call(SERVER, METHOD, {}, CITY)

        self.assertEqual(result.http_status, 200)
        self.assertIsNone(result.payload)
        self.assertIn("不是 JSON", result.error)
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
