# -*- coding: utf-8 -*-
"""api 归一化、时段判断、搜索封装与错误信息（无网络）。"""

import unittest

from xiaocan_monitor.api import (
    Card,
    METHOD_SEARCH,
    Offer,
    SERVER_SEARCH,
    build_search_body,
    expand_promotion,
    rpc_error,
    search,
)
from xiaocan_monitor.rpc import RpcResult

CITY = "110100"


def _promotion(**over):
    item = {
        "promotion_id": 111,
        "distance": 850,
        "start_time_hour": 10,
        "start_time_minute": 0,
        "end_time_hour": 22,
        "end_time_minute": 30,
        "rebate_condition": 99,
        "store": {"store_id": 1000001, "name": "示例门店A"},
    }
    item.update(over)
    return item


def _card(start=None, end=None, offers=None, store_id="1"):
    return Card(
        store_id=store_id,
        store_name="店",
        promotion_id=1,
        distance=100,
        start_minute=start,
        end_minute=end,
        rebate_condition=None,
        offers=list(offers or []),
    )


def _offer(platform=1, left=3, order_money=2000, rebate=500):
    names = {1: "美团", 2: "饿了么", 3: "京东"}
    return Offer(
        platform=platform,
        platform_name=names[platform],
        order_money=order_money,
        rebate=rebate,
        left=left,
    )


class ExpandPromotionTest(unittest.TestCase):
    def test_status_gating_and_fields(self):
        item = _promotion(
            meituan_status=1,
            eleme_status=0,
            tp_promotion={
                "tp_status": 1,
                "tp_order_money": 3000,
                "tp_user_rebate": 800,
                "tp_left_number": 2,
            },
            meituan_order_money=2000,
            meituan_user_rebate=500,
            meituan_left_number=3,
            eleme_order_money=1,
            eleme_user_rebate=1,
            eleme_left_number=9,
        )
        card = expand_promotion(item)
        self.assertEqual([o.platform for o in card.offers], [1, 3])

        meituan = card.offers[0]
        self.assertEqual(meituan.platform_name, "美团")
        self.assertEqual(meituan.order_money, 2000)
        self.assertEqual(meituan.rebate, 500)
        self.assertEqual(meituan.left, 3)

        tp = card.offers[1]
        self.assertEqual(tp.platform, 3)
        self.assertEqual(tp.platform_name, "京东")
        self.assertEqual(tp.left, 2)

    def test_missing_status_creates_no_offer(self):
        card = expand_promotion(_promotion())
        self.assertEqual(card.offers, [])

    def test_store_id_stringified_and_window_mapped(self):
        card = expand_promotion(_promotion())
        self.assertEqual(card.store_id, "1000001")
        self.assertIsInstance(card.store_id, str)
        self.assertEqual(card.store_name, "示例门店A")
        self.assertEqual(card.start_minute, 600)
        self.assertEqual(card.end_minute, 1350)
        self.assertEqual(card.distance, 850)

    def test_missing_store(self):
        item = _promotion()
        del item["store"]
        card = expand_promotion(item)
        self.assertIsNone(card.store_id)
        self.assertEqual(card.store_name, "(店名缺失)")

    def test_missing_time_components(self):
        item = _promotion()
        del item["start_time_hour"]
        card = expand_promotion(item)
        self.assertIsNone(card.start_minute)
        self.assertEqual(card.end_minute, 1350)


class WindowContainsTest(unittest.TestCase):
    def test_normal_window(self):
        card = _card(start=600, end=1350)
        self.assertTrue(card.window_contains(600))
        self.assertTrue(card.window_contains(1000))
        self.assertTrue(card.window_contains(1349))
        self.assertFalse(card.window_contains(599))
        self.assertFalse(card.window_contains(1350))

    def test_cross_midnight_window(self):
        card = _card(start=1320, end=120)
        self.assertTrue(card.window_contains(1320))
        self.assertTrue(card.window_contains(1439))
        self.assertTrue(card.window_contains(0))
        self.assertTrue(card.window_contains(119))
        self.assertFalse(card.window_contains(120))
        self.assertFalse(card.window_contains(1000))

    def test_equal_window_is_all_day(self):
        card = _card(start=600, end=600)
        self.assertTrue(card.window_contains(0))
        self.assertTrue(card.window_contains(600))
        self.assertTrue(card.window_contains(1439))

    def test_missing_edge_is_all_day(self):
        self.assertTrue(_card(start=None, end=1350).window_contains(0))
        self.assertTrue(_card(start=600, end=None).window_contains(1439))
        self.assertTrue(_card(start=None, end=None).window_contains(720))


class OrderableOffersTest(unittest.TestCase):
    def test_excludes_zero_and_none_left(self):
        card = _card(
            start=600,
            end=1350,
            offers=[_offer(left=0), _offer(left=None), _offer(left=3)],
        )
        self.assertEqual([o.left for o in card.orderable_offers(700)], [3])

    def test_out_of_window_returns_empty(self):
        card = _card(start=600, end=1350, offers=[_offer(left=3)])
        self.assertEqual(card.orderable_offers(200), [])


class BuildSearchBodyTest(unittest.TestCase):
    def test_documented_body(self):
        self.assertEqual(
            build_search_body("示例门店B", 39.904200, 116.407400, 15),
            {
                "keyword": "示例门店B",
                "latitude": 39.904200,
                "longitude": 116.407400,
                "offset": 0,
                "number": 15,
                "promotion_sort": 1,
                "store_type": 99,
                "store_platform": 0,
                "promotion_category": 0,
                "silk_id": 0,
                "app_id": 20,
            },
        )


class _FakeClient:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def call(self, servername, methodname, body, city):
        self.calls.append((servername, methodname, body, city))
        return self.result


class SearchTest(unittest.TestCase):
    def test_returns_cards_on_success(self):
        payload = {
            "status": {"code": 0},
            "promotion_list": [_promotion(meituan_status=1, meituan_left_number=2)],
        }
        client = _FakeClient(RpcResult(200, payload, None))
        cards, result = search(client, "示例门店A", 39.9, 116.4, CITY, 15)

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].store_id, "1000001")
        self.assertEqual(client.calls[0][0], SERVER_SEARCH)
        self.assertEqual(client.calls[0][1], METHOD_SEARCH)
        self.assertEqual(client.calls[0][3], CITY)
        self.assertEqual(client.calls[0][2]["keyword"], "示例门店A")

    def test_transport_error_returns_empty(self):
        client = _FakeClient(RpcResult(None, None, "网络异常: OSError: boom"))
        cards, result = search(client, "示例门店A", 39.9, 116.4, CITY, 15)
        self.assertEqual(cards, [])
        self.assertIsNotNone(result.error)

    def test_business_failure_returns_empty(self):
        client = _FakeClient(RpcResult(200, {"status": {"code": 1, "msg": "x"}}, None))
        cards, result = search(client, "示例门店A", 39.9, 116.4, CITY, 15)
        self.assertEqual(cards, [])

    def test_missing_promotion_list_is_empty(self):
        client = _FakeClient(RpcResult(200, {"status": {"code": 0}}, None))
        cards, result = search(client, "示例门店A", 39.9, 116.4, CITY, 15)
        self.assertEqual(cards, [])


class RpcErrorTest(unittest.TestCase):
    def test_none_result(self):
        self.assertEqual(rpc_error(None), "RPC 调用未返回结果")

    def test_error_passthrough(self):
        self.assertEqual(rpc_error(RpcResult(None, None, "网络异常")), "网络异常")

    def test_no_http_status(self):
        self.assertEqual(rpc_error(RpcResult(None, None, None)), "网络请求失败")

    def test_non_dict_payload(self):
        self.assertEqual(rpc_error(RpcResult(200, None, None)), "响应不是 JSON")
        self.assertEqual(rpc_error(RpcResult(200, ["x"], None)), "响应不是 JSON")

    def test_business_code_zero(self):
        self.assertIsNone(rpc_error(RpcResult(200, {"status": {"code": 0}}, None)))

    def test_business_code_nonzero(self):
        err = rpc_error(RpcResult(200, {"status": {"code": 7, "msg": "bad"}}, None))
        self.assertIn("业务失败", err)
        self.assertIn("7", err)
        self.assertIn("bad", err)


if __name__ == "__main__":
    unittest.main()
