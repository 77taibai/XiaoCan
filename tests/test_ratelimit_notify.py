# -*- coding: utf-8 -*-
"""限流器（假时钟）与钉钉加签推送（假 post_json，无网络）。"""

import base64
import hashlib
import hmac
import unittest
import urllib.parse

from xiaocan_monitor import notify
from xiaocan_monitor.ratelimit import RateLimiter


class _FakeStore:
    def __init__(self, state=None):
        self.state = dict(state) if state else {}

    def load_state(self):
        return dict(self.state)

    def save_state(self, state):
        self.state = dict(state)


class _Clock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


class RateLimiterTest(unittest.TestCase):
    def test_waits_and_persists(self):
        store = _FakeStore({"last_request_epoch": 100.0})
        sleeps = []
        RateLimiter(store, 3.0, now=_Clock(101.5), sleep=sleeps.append).wait()

        self.assertEqual(sleeps, [1.5])
        self.assertEqual(store.state["last_request_epoch"], 101.5)

    def test_no_wait_when_interval_elapsed(self):
        store = _FakeStore({"last_request_epoch": 100.0})
        sleeps = []
        RateLimiter(store, 3.0, now=_Clock(104.0), sleep=sleeps.append).wait()

        self.assertEqual(sleeps, [])
        self.assertEqual(store.state["last_request_epoch"], 104.0)

    def test_no_previous_request(self):
        store = _FakeStore({})
        sleeps = []
        RateLimiter(store, 3.0, now=_Clock(50.0), sleep=sleeps.append).wait()

        self.assertEqual(sleeps, [])
        self.assertEqual(store.state["last_request_epoch"], 50.0)

    def test_non_numeric_last_ignored(self):
        store = _FakeStore({"last_request_epoch": "not a number"})
        sleeps = []
        RateLimiter(store, 3.0, now=_Clock(50.0), sleep=sleeps.append).wait()

        self.assertEqual(sleeps, [])
        self.assertEqual(store.state["last_request_epoch"], 50.0)

    def test_bool_last_ignored(self):
        store = _FakeStore({"last_request_epoch": True})
        sleeps = []
        RateLimiter(store, 3.0, now=_Clock(50.0), sleep=sleeps.append).wait()

        self.assertEqual(sleeps, [])


class NotifySignTest(unittest.TestCase):
    SECRET = "SECret123"
    TS = 1700000000000

    def _expected_sign(self, secret, ts):
        digest = hmac.new(
            secret.encode("utf-8"),
            f"{ts}\n{secret}".encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return base64.b64encode(digest).decode("utf-8")

    def test_sign_params_matches_independent_hmac(self):
        params = notify.sign_params(self.SECRET, self.TS)
        self.assertEqual(params["sign"], self._expected_sign(self.SECRET, self.TS))
        self.assertEqual(params["timestamp"], str(self.TS))

    def test_signed_url_without_query(self):
        webhook = "https://oapi.dingtalk.com/robot/send"
        url = notify.signed_url(webhook, self.SECRET, self.TS)
        self.assertEqual(url.count("?"), 1)
        base, _, query = url.partition("?")
        self.assertEqual(base, webhook)
        parsed = dict(urllib.parse.parse_qsl(query))
        self.assertEqual(parsed["timestamp"], str(self.TS))
        self.assertEqual(parsed["sign"], self._expected_sign(self.SECRET, self.TS))

    def test_signed_url_with_existing_query(self):
        webhook = "https://oapi.dingtalk.com/robot/send?access_token=abc"
        url = notify.signed_url(webhook, self.SECRET, self.TS)
        self.assertEqual(url.count("?"), 1)
        base, _, query = url.partition("?")
        self.assertEqual(base, "https://oapi.dingtalk.com/robot/send")
        parsed = dict(urllib.parse.parse_qsl(query))
        self.assertEqual(parsed["access_token"], "abc")
        self.assertEqual(parsed["timestamp"], str(self.TS))
        self.assertEqual(parsed["sign"], self._expected_sign(self.SECRET, self.TS))


class DingTalkNotifierTest(unittest.TestCase):
    SECRET = "SUPER_SECRET_VALUE"
    WEBHOOK = "https://oapi.dingtalk.com/robot/send?access_token=tok"

    def _notifier(self, webhook=None, response=None, exc=None):
        calls = []

        def post_json(url, payload, timeout):
            calls.append((url, payload))
            if exc is not None:
                raise exc
            return response

        notifier = notify.DingTalkNotifier(
            self.WEBHOOK if webhook is None else webhook,
            self.SECRET,
            "标题",
            post_json=post_json,
            now_ms=lambda: 1700000000000,
        )
        return notifier, calls

    def test_success(self):
        notifier, calls = self._notifier(response={"errcode": 0, "errmsg": "ok"})
        ok, _ = notifier.send_markdown("标题", "正文")

        self.assertTrue(ok)
        self.assertEqual(len(calls), 1)
        url, payload = calls[0]
        self.assertEqual(payload["msgtype"], "markdown")
        self.assertEqual(payload["markdown"], {"title": "标题", "text": "正文"})
        self.assertIn("timestamp=", url)
        self.assertNotIn(self.SECRET, url)

    def test_failure_errcode(self):
        notifier, _ = self._notifier(
            response={"errcode": 310000, "errmsg": "keywords not in content"}
        )
        ok, message = notifier.send_markdown("标题", "正文")

        self.assertFalse(ok)
        self.assertIn("310000", message)
        self.assertNotIn(self.SECRET, message)

    def test_unconfigured(self):
        notifier, calls = self._notifier(webhook="", response={"errcode": 0})
        ok, message = notifier.send_markdown("标题", "正文")

        self.assertFalse(ok)
        self.assertEqual(calls, [])
        self.assertNotIn(self.SECRET, message)

    def test_secret_not_leaked_on_exception(self):
        notifier, _ = self._notifier(exc=RuntimeError("boom " + self.SECRET))
        ok, message = notifier.send_markdown("标题", "正文")

        self.assertFalse(ok)
        self.assertNotIn(self.SECRET, message)


if __name__ == "__main__":
    unittest.main()
