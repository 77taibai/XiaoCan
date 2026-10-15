# -*- coding: utf-8 -*-
"""Store 原子读写与 RunLock 互斥行为（仅使用临时目录）。"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from xiaocan_monitor.lock import LockBusy, RunLock
from xiaocan_monitor.store import Monitor, Store, StoreError


class StoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.store = Store(self.tmp / "monitors.json", self.tmp / "state.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_files(self):
        self.assertEqual(self.store.load_monitors(), [])
        state = self.store.load_state()
        self.assertEqual(state["daily"], {})
        self.assertIsNone(state["last_request_epoch"])

    def test_monitors_round_trip(self):
        monitor = Monitor(
            id="a1b2c3d4",
            store_id="1000001",
            store_name="示例门店A",
            platform=1,
            lat=39.904200,
            lon=116.407400,
            city="110100",
            note="备注",
            enabled=True,
            created_at="2026-10-15T12:00:00+08:00",
        )
        self.store.save_monitors([monitor])
        loaded = self.store.load_monitors()

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].to_dict(), monitor.to_dict())
        self.assertIsInstance(loaded[0].store_id, str)

    def test_store_id_back_as_str_from_int(self):
        doc = {
            "version": 1,
            "monitors": [
                {
                    "id": "aabbccdd",
                    "store_id": 1000001,
                    "store_name": "店",
                    "platform": 0,
                }
            ],
        }
        (self.tmp / "monitors.json").write_text(
            json.dumps(doc, ensure_ascii=False), encoding="utf-8"
        )
        loaded = self.store.load_monitors()[0]
        self.assertEqual(loaded.store_id, "1000001")
        self.assertIsInstance(loaded.store_id, str)

    def test_mark_done_preserves_other_keys(self):
        self.store.save_state(
            {
                "version": 1,
                "last_request_epoch": 1760000000.123,
                "daily": {},
                "custom": {"k": 1},
            }
        )
        self.store.mark_done("a1b2c3d4", "2026-10-15")
        state = self.store.load_state()

        self.assertEqual(state["version"], 1)
        self.assertEqual(state["last_request_epoch"], 1760000000.123)
        self.assertEqual(state["daily"], {"a1b2c3d4": "2026-10-15"})
        self.assertEqual(state["custom"], {"k": 1})

    def test_mark_done_with_non_dict_daily(self):
        self.store.save_state({"version": 1, "daily": None})
        self.store.mark_done("id1", "2026-10-15")
        self.assertEqual(self.store.load_state()["daily"], {"id1": "2026-10-15"})

    def test_no_tmp_files_left(self):
        self.store.save_monitors([Monitor(id="aabbccdd", store_id="1", store_name="店")])
        self.store.save_state({"version": 1})
        self.assertEqual(list(self.tmp.glob("*.tmp")), [])

    def test_invalid_json_raises_for_both_loaders(self):
        (self.tmp / "monitors.json").write_text("{ not json", encoding="utf-8")
        with self.assertRaises(StoreError):
            self.store.load_monitors()

        (self.tmp / "state.json").write_text("[]]", encoding="utf-8")
        with self.assertRaises(StoreError):
            self.store.load_state()

    def test_new_id_shape(self):
        new_id = Store.new_id()
        self.assertEqual(len(new_id), 8)
        self.assertTrue(all(c in "0123456789abcdef" for c in new_id))


class RunLockTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "xiaocan.lock"

    def tearDown(self):
        self._tmp.cleanup()

    def test_acquire_busy_release_reacquire(self):
        with RunLock(self.path):
            self.assertTrue(self.path.exists())
            with self.assertRaises(LockBusy):
                with RunLock(self.path):
                    pass

        self.assertFalse(self.path.exists())
        with RunLock(self.path) as lock:
            self.assertTrue(lock._owned)

    def test_stale_seconds_zero_reclaims(self):
        self.path.write_text(str(os.getpid()), encoding="utf-8")
        with RunLock(self.path, stale_seconds=0) as lock:
            self.assertTrue(lock._owned)
            self.assertEqual(
                self.path.read_text(encoding="utf-8").strip(), str(os.getpid())
            )
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
