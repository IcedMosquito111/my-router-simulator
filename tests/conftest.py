# -*- coding: utf-8 -*-
"""pytest 公共配置：把 backend 加入 sys.path，并提供 Simulator fixture"""

import asyncio
import pathlib
import sys

import pytest

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from network.simulator import Simulator  # noqa: E402

TEST_NODE_COUNT = 50
TEST_SEED = 20240915  # 固定种子：拓扑与实验结果可复现


def run(coro):
    """在同步测试中运行协程（避免额外依赖 pytest-asyncio）"""
    return asyncio.run(coro)


@pytest.fixture
def simulator():
    """50 节点、固定随机种子的模拟器（未启动，无 WebSocket 回调）"""
    return Simulator(num_nodes=TEST_NODE_COUNT, seed=TEST_SEED)
