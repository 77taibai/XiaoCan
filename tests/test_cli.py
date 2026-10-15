# -*- coding: utf-8 -*-
"""cli.main 的退出码与互斥锁（环境变量隔离，指向临时目录，无网络）。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from xiaocan_monitor import cli


class CliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.env_file = self.tmp / "empty.env"
        self.env_file.write_text("", encoding="utf-8")
        self.config_path = self.tmp / "monitors.json"
        self.state_path = self.tmp / "state.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _env(self, **over):
        base = {
            "XIAOCAN_ENV_FILE": str(self.env_file),
            "XIAOCAN_CONFIG_PATH": str(self.config_path),
            "XIAOCAN_STATE_PATH": str(self.state_path),
        }
        base.update(over)
        return base

    def test_version_exits_zero(self):
        with mock.patch.dict(os.environ, self._env(), clear=True):
            with self.assertRaises(SystemExit) as cm:
                cli.main(["--version"])
        self.assertEqual(cm.exception.code, 0)

    def test_run_without_webhook_returns_usage_error(self):
        with mock.patch.dict(os.environ, self._env(), clear=True):
            code = cli.main(["run"])

        self.assertEqual(code, 2)
        self.assertFalse((self.tmp / "xiaocan.lock").exists())

    def test_run_while_lock_held_returns_locked(self):
        lock_path = self.tmp / "xiaocan.lock"
        lock_path.write_text(str(os.getpid()), encoding="utf-8")
        env = self._env(
            XIAOCAN_DINGTALK_WEBHOOK="https://oapi.dingtalk.com/robot/send?access_token=fake"
        )
        with mock.patch.dict(os.environ, env, clear=True):
            code = cli.main(["run"])

        self.assertEqual(code, 3)


if __name__ == "__main__":
    unittest.main()
