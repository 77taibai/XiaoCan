#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``python -m xiaocan_monitor`` 入口。"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
