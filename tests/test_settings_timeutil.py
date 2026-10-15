# -*- coding: utf-8 -*-
"""配置优先级、.env 解析与中国时区工具（无网络，不依赖主机时区）。"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from xiaocan_monitor import timeutil
from xiaocan_monitor.settings import load_settings, parse_env_file


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.empty = self.tmp / "empty.env"
        self.empty.write_text("", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_defaults(self):
        settings = load_settings(env={"XIAOCAN_ENV_FILE": str(self.empty)})
        self.assertEqual(settings.dingtalk_webhook, "")
        self.assertEqual(settings.dingtalk_secret, "")
        self.assertEqual(settings.dingtalk_title, "小蚕余量监控")
        self.assertEqual(settings.min_request_interval, 3.0)
        self.assertEqual(settings.request_timeout, 8.0)
        self.assertEqual(settings.retries, 1)
        self.assertAlmostEqual(settings.default_lat, 39.904200)
        self.assertAlmostEqual(settings.default_lon, 116.407400)
        self.assertEqual(settings.default_city, "110100")

    def test_process_env_over_file_over_defaults(self):
        env_file = self.tmp / "custom.env"
        env_file.write_text(
            "XIAOCAN_DINGTALK_TITLE=来自文件\n"
            "XIAOCAN_REQUEST_TIMEOUT=99\n"
            "XIAOCAN_DINGTALK_WEBHOOK=https://file.example/hook\n",
            encoding="utf-8",
        )
        settings = load_settings(
            env={
                "XIAOCAN_ENV_FILE": str(env_file),
                "XIAOCAN_DINGTALK_TITLE": "来自环境",
            }
        )
        self.assertEqual(settings.dingtalk_title, "来自环境")
        self.assertEqual(settings.request_timeout, 99.0)
        self.assertEqual(settings.dingtalk_webhook, "https://file.example/hook")
        self.assertEqual(settings.retries, 1)

    def test_env_file_env_var_points_elsewhere(self):
        other = self.tmp / "other.env"
        other.write_text("XIAOCAN_DEFAULT_CITY=999999\n", encoding="utf-8")
        settings = load_settings(env={"XIAOCAN_ENV_FILE": str(other)})
        self.assertEqual(settings.default_city, "999999")
        self.assertEqual(settings.env_path, other.resolve())

    def test_malformed_numeric_falls_back(self):
        settings = load_settings(
            env={
                "XIAOCAN_ENV_FILE": str(self.empty),
                "XIAOCAN_MIN_REQUEST_INTERVAL": "abc",
                "XIAOCAN_REQUEST_TIMEOUT": "",
                "XIAOCAN_RETRIES": "many",
                "XIAOCAN_DEFAULT_LAT": "north",
            }
        )
        self.assertEqual(settings.min_request_interval, 3.0)
        self.assertEqual(settings.request_timeout, 8.0)
        self.assertEqual(settings.retries, 1)
        self.assertAlmostEqual(settings.default_lat, 39.904200)

    def test_paths_and_lock_derivation(self):
        config = self.tmp / "cfg" / "monitors.json"
        state = self.tmp / "st" / "state.json"
        settings = load_settings(
            env={
                "XIAOCAN_ENV_FILE": str(self.empty),
                "XIAOCAN_CONFIG_PATH": str(config),
                "XIAOCAN_STATE_PATH": str(state),
            }
        )
        self.assertEqual(settings.config_path, config.resolve())
        self.assertEqual(settings.state_path, state.resolve())
        self.assertEqual(
            settings.lock_path, (config.parent / "xiaocan.lock").resolve()
        )


class ParseEnvFileTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_parsing_rules(self):
        path = self.tmp / "x.env"
        path.write_text(
            "# a comment\n"
            "\n"
            "NOEQUALS\n"
            "KEY1=value1\n"
            'KEY2 = "quoted"\n'
            "KEY3='single'\n"
            "KEY4=  spaced value  \n"
            "#KEY5=ignored\n"
            "=nokey\n"
            "  KEY6  =  v6  \n",
            encoding="utf-8",
        )
        self.assertEqual(
            parse_env_file(path),
            {
                "KEY1": "value1",
                "KEY2": "quoted",
                "KEY3": "single",
                "KEY4": "spaced value",
                "KEY6": "v6",
            },
        )

    def test_missing_file_returns_empty(self):
        self.assertEqual(parse_env_file(self.tmp / "nope.env"), {})


class TimeUtilTest(unittest.TestCase):
    def test_cn_now_utcoffset(self):
        self.assertEqual(timeutil.cn_now().utcoffset(), timedelta(hours=8))

    def test_minute_of_day(self):
        self.assertEqual(timeutil.minute_of_day(datetime(2026, 10, 15, 0, 0)), 0)
        self.assertEqual(timeutil.minute_of_day(datetime(2026, 10, 15, 14, 23)), 863)
        self.assertEqual(timeutil.minute_of_day(datetime(2026, 10, 15, 23, 59)), 1439)

    def test_format_cn_naive(self):
        self.assertEqual(
            timeutil.format_cn(datetime(2026, 10, 15, 14, 23)), "2026-10-15 14:23"
        )
        self.assertEqual(
            timeutil.format_cn(datetime(2026, 1, 2, 3, 4)), "2026-01-02 03:04"
        )

    def test_format_cn_converts_tz(self):
        utc = datetime(2026, 10, 15, 6, 23, tzinfo=timezone.utc)
        self.assertEqual(timeutil.format_cn(utc), "2026-10-15 14:23")

    def test_cn_today_format(self):
        today = timeutil.cn_today()
        self.assertRegex(today, r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(today, timeutil.cn_now().strftime("%Y-%m-%d"))


if __name__ == "__main__":
    unittest.main()
