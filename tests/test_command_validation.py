# -*- coding: utf-8 -*-
"""
输入校验测试。

修复前实测：`{"dst_ip": 123}` 会抛 TypeError、`{"params": "oops"}` 会抛 AttributeError，
两者都被 main.py 的兜底 except 捕获后**直接断开客户端 WebSocket 连接**。
本文件保证：任何非法输入都只返回 {"success": false}，不抛异常、不断连。
"""

import pytest

from conftest import run
from models import Command, ValidationError, parse_command_params
from network.simulator import Simulator

BAD_PAYLOADS = [
    {"action": "send_packet", "params": {"src_node": "R1", "dst_ip": 123}},          # 数字 IP
    {"action": "send_packet", "params": {"src_node": "R1", "dst_ip": "300.1.2.3"}},  # 非法 IP
    {"action": "send_packet", "params": {"src_node": "R1", "dst_ip": "10.255.0.2", "ttl": 0}},
    {"action": "send_packet", "params": {"src_node": "R1", "dst_ip": "10.255.0.2", "ttl": 9999}},
    {"action": "send_packet", "params": {"src_node": "R1", "dst_ip": "10.255.0.2", "protocol": "IPv6"}},
    {"action": "send_packet", "params": "oops"},                                     # params 非对象
    {"action": "send_packet", "params": None},
    {"action": "send_packet"},                                                       # 缺 params
    {"action": "send_packet", "params": {"src_node": "R1"}},                         # 缺 dst_ip
    {"action": "send_packet", "params": []},
    {"action": "inject_fault", "params": {}},                                        # 缺 type
    {"action": "inject_fault", "params": {"type": "node"}},                           # 缺 id
    {"action": "inject_fault", "params": {"type": "link", "source": "R1"}},           # 缺 target
    {"action": "inject_fault", "params": {"type": "link", "source": "R1", "target": "R1"}},
    {"action": "inject_fault", "params": {"type": "node", "id": "X" * 100000}},       # 超长 id
    {"action": "inject_fault", "params": {"type": "node", "id": 12345}},
    {"action": "recover_fault", "params": {"type": "link"}},
    {"action": "get_routing_table", "params": {}},                                    # 缺 node_id
    {"action": "get_routing_table", "params": {"node_id": None}},
    {"action": "rm -rf", "params": {}},                                               # 未知命令
    {"action": "", "params": {}},
    {"action": "x" * 1000, "params": {}},
]

NON_DICT_PAYLOADS = [None, [], "get_topology", 42, True, [{"action": "get_topology"}]]


@pytest.mark.parametrize("payload", BAD_PAYLOADS, ids=lambda item: str(item)[:60])
def test_bad_payloads_return_error_without_raising(simulator, payload):
    result = run(simulator.handle_command(payload))

    assert isinstance(result, dict)
    assert result["success"] is False
    assert result["message"], "错误响应必须给出可读原因"


@pytest.mark.parametrize("payload", NON_DICT_PAYLOADS, ids=lambda item: str(item)[:40])
def test_non_dict_payloads_are_rejected(simulator, payload):
    result = run(simulator.handle_command(payload))
    assert result["success"] is False


def test_bad_payloads_do_not_mutate_topology(simulator):
    """非法命令不能有副作用：拓扑与路由表必须保持原样"""
    before = simulator.get_topology_snapshot()
    for payload in BAD_PAYLOADS:
        run(simulator.handle_command(payload))
    assert simulator.get_topology_snapshot() == before
    assert simulator.get_metrics()["faults_injected"] == 0
    assert simulator.get_metrics()["packets_sent"] == 0


def test_unknown_node_and_link_report_readable_errors(simulator):
    assert "不存在" in run(simulator.handle_command(
        {"action": "inject_fault", "params": {"type": "node", "id": "R999"}}))["message"]
    assert "不存在" in run(simulator.handle_command(
        {"action": "inject_fault", "params": {"type": "link", "source": "R1", "target": "R999"}}))["message"]
    assert "不存在" in run(simulator.handle_command(
        {"action": "get_routing_table", "params": {"node_id": "R999"}}))["message"]


def test_valid_commands_still_work(simulator):
    topology = run(simulator.handle_command({"action": "get_topology", "params": {}}))
    assert topology["success"] is True and len(topology["data"]["nodes"]) == 50

    table = run(simulator.handle_command({"action": "get_routing_table", "params": {"node_id": "R1"}}))
    assert table["success"] is True and table["data"]["routes"]

    packet = run(simulator.handle_command(
        {"action": "send_packet", "params": {"src_node": "R1", "dst_ip": "10.255.0.2"}}))
    assert packet["success"] is True

    fault = run(simulator.handle_command({"action": "inject_fault", "params": {"type": "node", "id": "R5"}}))
    assert fault["success"] is True and fault["data"]["convergence_ms"] < 2000

    assert run(simulator.handle_command({"action": "recover_fault", "params": {"type": "node", "id": "R5"}}))["success"]

    # 参数被忽略的多余字段不应报错（例如前端带了无关字段）
    extra = run(simulator.handle_command(
        {"action": "get_topology", "params": {"ignored": "x"}}))
    assert isinstance(extra, dict)


def test_duplicate_fault_is_reported_not_silently_ignored(simulator):
    assert run(simulator.handle_command({"action": "inject_fault", "params": {"type": "node", "id": "R6"}}))["success"]
    again = run(simulator.handle_command({"action": "inject_fault", "params": {"type": "node", "id": "R6"}}))
    assert again["success"] is False and "已经" in again["message"]


def test_models_level_validation():
    """models 层：Command 与参数模型的边界行为"""
    with pytest.raises(ValidationError):
        Command.model_validate({"action": "get_topology", "params": "oops"})
    with pytest.raises(ValidationError):
        Command.model_validate({"action": "", "params": {}})

    command = Command.model_validate({"action": "get_topology"})
    assert command.params == {}

    with pytest.raises(ValueError):
        parse_command_params("unknown_action", {})


def test_simulator_can_be_constructed_outside_event_loop():
    """回归：__init__ 里不能再创建 asyncio 任务（否则同步构造会抛 RuntimeError）"""
    simulator = Simulator(num_nodes=5, seed=1)
    assert len(simulator.topology.nodes) == 5
    assert simulator._initial_updates  # 初始路由表已计算好，等待 start() 推送
