# -*- coding: utf-8 -*-
"""engine.run_cycle 的纯逻辑判定（注入假搜索/假推送，无网络）。"""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from xiaocan_monitor.api import Card, Offer
from xiaocan_monitor.engine import run_cycle
from xiaocan_monitor.rpc import RpcResult
from xiaocan_monitor.store import Monitor, Store
from xiaocan_monitor.timeutil import CN_TZ

STORE_ID = "1000001"
STORE_NAME = "示例门店A"
NOW = datetime(2026, 10, 15, 14, 0, tzinfo=CN_TZ)
TODAY = "2026-10-15"
PLATFORM_NAMES = {1: "美团", 2: "饿了么", 3: "京东"}


def _ok_result():
    return RpcResult(200, {"status": {"code": 0}}, None)


def _offer(platform=1, left=3):
    return Offer(
        platform=platform,
        platform_name=PLATFORM_NAMES[platform],
        order_money=2000,
        rebate=500,
        left=left,
    )


def _card(offers, start=600, end=1350):
    return Card(
        store_id=STORE_ID,
        store_name=STORE_NAME,
        promotion_id=1,
        distance=100,
        start_minute=start,
        end_minute=end,
        rebate_condition=99,
        offers=list(offers),
    )


class _FakeNotifier:
    def __init__(self, configured=True, ok=True, message="推送成功"):
        self.title = "小蚕余量监控"
        self._configured = configured
        self._ok = ok
        self._message = message
        self.calls = []

    def configured(self):
        return self._configured

    def send_markdown(self, title, text):
        self.calls.append((title, text))
        return (self._ok, self._message)


class EngineTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.store = Store(self.tmp / "monitors.json", self.tmp / "state.json")

    def tearDown(self):
        self._tmp.cleanup()

    def _add_monitor(self, platform=0):
        monitor = Monitor(
            id="a1b2c3d4",
            store_id=STORE_ID,
            store_name=STORE_NAME,
            platform=platform,
            lat=39.904200,
            lon=116.407400,
            city="110100",
        )
        self.store.save_monitors([monitor])
        return monitor

    def _search(self, cards, result, calls):
        def search_fn(keyword, lat, lon, city, number):
            calls.append(keyword)
            return (cards, result)

        return search_fn

    def run_with(self, cards, result=None, notifier=None, monitor_platform=0, **kwargs):
        monitor = self._add_monitor(platform=monitor_platform)
        calls = []
        search_fn = self._search(cards, result or _ok_result(), calls)
        notifier = notifier or _FakeNotifier()
        outcomes = run_cycle(
            store=self.store,
            search_fn=search_fn,
            notifier=notifier,
            now=NOW,
            log=lambda msg: None,
            **kwargs,
        )
        return monitor, notifier, calls, outcomes

    def test_hit_pushes_once_and_marks(self):
        monitor, notifier, calls, outcomes = self.run_with([_card([_offer(1, 3)])])

        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0].status, "hit")
        self.assertTrue(outcomes[0].pushed)
        self.assertEqual(len(notifier.calls), 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.store.load_state()["daily"][monitor.id], TODAY)

    def test_second_run_same_day_skips_without_search(self):
        monitor = self._add_monitor()
        cards = [_card([_offer(1, 3)])]
        calls = []
        search_fn = self._search(cards, _ok_result(), calls)

        first = run_cycle(
            store=self.store, search_fn=search_fn, notifier=_FakeNotifier(),
            now=NOW, log=lambda msg: None,
        )
        self.assertEqual(first[0].status, "hit")
        self.assertEqual(len(calls), 1)

        second_notifier = _FakeNotifier()
        second = run_cycle(
            store=self.store, search_fn=search_fn, notifier=second_notifier,
            now=NOW, log=lambda msg: None,
        )
        self.assertEqual(second[0].status, "skipped")
        self.assertEqual(len(calls), 1)
        self.assertEqual(second_notifier.calls, [])
        self.assertEqual(self.store.load_state()["daily"][monitor.id], TODAY)

    def test_left_zero_is_waiting(self):
        monitor, notifier, calls, outcomes = self.run_with([_card([_offer(1, 0)])])

        self.assertEqual(outcomes[0].status, "waiting")
        self.assertEqual(notifier.calls, [])
        self.assertEqual(self.store.load_state()["daily"], {})

    def test_outside_window_is_waiting(self):
        monitor, notifier, calls, outcomes = self.run_with(
            [_card([_offer(1, 3)], start=600, end=700)]
        )

        self.assertEqual(outcomes[0].status, "waiting")
        self.assertEqual(notifier.calls, [])
        self.assertEqual(self.store.load_state()["daily"], {})

    def test_dry_run_does_not_push_or_mark(self):
        monitor, notifier, calls, outcomes = self.run_with(
            [_card([_offer(1, 3)])], dry_run=True
        )

        self.assertEqual(outcomes[0].status, "hit")
        self.assertFalse(outcomes[0].pushed)
        self.assertEqual(notifier.calls, [])
        self.assertEqual(self.store.load_state()["daily"], {})

    def test_business_failure_is_error(self):
        bad = RpcResult(200, {"status": {"code": 1, "msg": "boom"}}, None)
        monitor, notifier, calls, outcomes = self.run_with([], result=bad)

        self.assertEqual(outcomes[0].status, "error")
        self.assertIn("业务失败", outcomes[0].detail)
        self.assertEqual(notifier.calls, [])
        self.assertEqual(self.store.load_state()["daily"], {})

    def test_transport_failure_is_error(self):
        bad = RpcResult(None, None, "网络异常: OSError: boom")
        monitor, notifier, calls, outcomes = self.run_with([], result=bad)

        self.assertEqual(outcomes[0].status, "error")
        self.assertIn("网络异常", outcomes[0].detail)

    def test_platform_filter_ignores_other_platform_only_offers(self):
        monitor, notifier, calls, outcomes = self.run_with(
            [_card([_offer(2, 5)])], monitor_platform=1
        )

        self.assertEqual(outcomes[0].status, "waiting")
        self.assertEqual(notifier.calls, [])

    def test_platform_filter_selects_matching_offer(self):
        monitor, notifier, calls, outcomes = self.run_with(
            [_card([_offer(1, 5), _offer(2, 5)])], monitor_platform=1
        )

        self.assertEqual(outcomes[0].status, "hit")
        self.assertEqual(len(notifier.calls), 1)
        text = notifier.calls[0][1]
        self.assertIn("美团", text)
        self.assertNotIn("饿了么", text)

    def test_missing_notifier_config_is_error(self):
        monitor, notifier, calls, outcomes = self.run_with(
            [_card([_offer(1, 3)])], notifier=_FakeNotifier(configured=False)
        )

        self.assertEqual(outcomes[0].status, "error")
        self.assertEqual(notifier.calls, [])
        self.assertEqual(self.store.load_state()["daily"], {})


if __name__ == "__main__":
    unittest.main()
