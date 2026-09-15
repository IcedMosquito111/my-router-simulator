# -*- coding: utf-8 -*-
"""
端到端集成测试：真实 uvicorn 服务 + 真实 WebSocket 客户端。

验证修复前实测存在的问题：
    1. 发送非法命令会让客户端连接被直接断开（ConnectionClosedError）；
    2. 故障注入后是否推送全部节点的路由表、收敛耗时的可观测性（/api/metrics）。
"""

import asyncio
import json
import os
import socket
import sys
import threading
import time
import urllib.request

import pytest

pytestmark = pytest.mark.slow

STARTUP_TIMEOUT_SECONDS = 30.0
RECV_TIMEOUT_SECONDS = 20.0


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _get_json(port: int, path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as response:
        return json.loads(response.read())


def _wait_for_health(port: int, deadline: float):
    while time.time() < deadline:
        try:
            return _get_json(port, "/health")
        except Exception:
            time.sleep(0.2)
    raise AssertionError("后端未能在超时时间内启动")


@pytest.fixture
def backend():
    """在后台线程启动真实 uvicorn 服务（50 节点、固定随机种子）"""
    port = _free_port()
    os.environ.update({
        "RS_NUM_NODES": "50",
        "RS_SEED": "20240915",
        "RS_LOG_LEVEL": "WARNING",
    })
    for module_name in ("main", "settings"):
        sys.modules.pop(module_name, None)

    import uvicorn

    import main as backend_main

    server = uvicorn.Server(uvicorn.Config(
        backend_main.app, host="127.0.0.1", port=port, log_level="warning",
    ))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        health = _wait_for_health(port, time.time() + STARTUP_TIMEOUT_SECONDS)
        assert health["status"] == "ok"
        assert health["nodes"] == 50
        yield port
    finally:
        server.should_exit = True
        thread.join(timeout=15)


async def _recv_until_command_response(ws, action: str):
    """持续接收，直到拿到该 action 的 command_response；返回 (响应, 其它消息计数)"""
    counts = {}
    while True:
        message = json.loads(await asyncio.wait_for(ws.recv(), timeout=RECV_TIMEOUT_SECONDS))
        counts[message["type"]] = counts.get(message["type"], 0) + 1
        if message["type"] == "command_response" and message.get("action") == action:
            return message, counts


async def _scenario(port: int):
    import websockets

    uri = f"ws://127.0.0.1:{port}/ws"
    async with websockets.connect(uri, max_size=None) as ws:
        # 1) 连接后应收到拓扑快照
        snapshot = json.loads(await asyncio.wait_for(ws.recv(), timeout=RECV_TIMEOUT_SECONDS))
        assert snapshot["type"] == "topology_snapshot"
        assert len(snapshot["data"]["nodes"]) == 50
        assert len(snapshot["data"]["links"]) == 75

        # 2) 非法命令：必须返回错误响应，且连接不能断开
        invalid_commands = [
            {"action": "send_packet", "params": {"src_node": "R1", "dst_ip": 123}},
            {"action": "send_packet", "params": "oops"},
            {"action": "inject_fault", "params": {"type": "link", "source": "R1"}},
            {"action": "unknown_action", "params": {}},
        ]
        for command in invalid_commands:
            await ws.send(json.dumps(command))
            response, _ = await _recv_until_command_response(ws, command["action"])
            assert response["data"]["success"] is False, f"非法命令未被拒绝: {command}"
            assert response["data"]["message"]

        # 3) 连接仍然可用
        await ws.send(json.dumps({"action": "get_topology", "params": {}}))
        response, _ = await _recv_until_command_response(ws, "get_topology")
        assert response["data"]["success"] is True
        assert len(response["data"]["data"]["nodes"]) == 50

        # 4) 正常转发一次分组
        await ws.send(json.dumps({
            "action": "send_packet", "params": {"src_node": "R1", "dst_ip": "10.255.0.2"},
        }))
        response, counts = await _recv_until_command_response(ws, "send_packet")
        assert response["data"]["success"] is True
        assert counts.get("packet_forwarded") == 1

        # 5) 故障注入：应推送剩余 49 个节点的路由表，并报告收敛耗时
        await ws.send(json.dumps({"action": "inject_fault", "params": {"type": "node", "id": "R25"}}))
        response, counts = await _recv_until_command_response(ws, "inject_fault")
        assert response["data"]["success"] is True
        assert counts.get("routing_table_update") == 49, f"路由表推送数量异常: {counts}"
        assert counts.get("topology_update") == 1
        data = response["data"]["data"]
        assert data["convergence_ms"] < 2000
        assert data["total_ms"] < 2000
        assert data["node_count"] == 49


def test_end_to_end_websocket_scenarios(backend):
    asyncio.run(_scenario(backend))

    metrics = _get_json(backend, "/api/metrics")
    assert metrics["max_convergence_ms"] < 2000
    assert metrics["requirement"]["satisfied"] is True
    assert metrics["faults_injected"] == 1
    assert metrics["packets_delivered"] == 1
    assert metrics["node_count"] == 50

    exported = _get_json(backend, "/api/topology/export")
    assert len(exported["nodes"]) == 50
    assert len(exported["links"]) == 75
    assert {"id", "loopback_ipv4"} <= set(exported["nodes"][0])


def test_topology_config_round_trip(tmp_path, backend):
    """导出的拓扑配置能重新加载（固化实验拓扑用，RS_TOPOLOGY_FILE 复现同一实验）"""
    exported = _get_json(backend, "/api/topology/export")
    config_path = tmp_path / "topology.json"
    config_path.write_text(json.dumps(exported, ensure_ascii=False), encoding="utf-8")

    sys.path.insert(0, str((__import__("pathlib").Path(__file__).resolve().parents[1] / "backend")))
    from network.simulator import Simulator

    reloaded = Simulator(topology_file=str(config_path))
    assert len(reloaded.topology.nodes) == len(exported["nodes"])
    assert len(reloaded.topology.links) == len(exported["links"])
    original_routes = {link.source_id: link.cost for link in reloaded.topology.links}
    assert original_routes, "加载后的拓扑应包含链路代价"
