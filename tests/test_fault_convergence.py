# -*- coding: utf-8 -*-
"""
性能指标测试：验证需求"发生单个节点或链路故障后，路由重新计算时间不超过 2 秒"。

度量口径（与后端返回字段一致）：
    convergence_ms = 路由重新计算本身的耗时（纯算法，不含网络推送）
    total_ms       = 计算 + 向所有客户端推送全部路由表的总耗时

覆盖方式：**穷举所有单点故障**（50 个节点 + 全部链路），而不是抽样，
这样"最坏情况也在 2 秒内"才是可证的事实。
"""

import pytest

from conftest import TEST_NODE_COUNT, TEST_SEED, run
from helpers import assert_no_blackhole, assert_routing_complete
from network.simulator import Simulator

REQUIREMENT_LIMIT_MS = 2000


def _fault_and_recover(simulator, params):
    injected = run(simulator.handle_command({"action": "inject_fault", "params": params}))
    assert injected["success"] is True, injected["message"]
    recovered = run(simulator.handle_command({"action": "recover_fault", "params": params}))
    assert recovered["success"] is True, recovered["message"]
    return injected["data"]


def _report(samples, label):
    worst = max(samples, key=lambda item: item["convergence_ms"])
    worst_total = max(samples, key=lambda item: item["total_ms"])
    print(
        f"\n[{label}] 样本={len(samples)} "
        f"最慢重计算={worst['convergence_ms']}ms（目标 {worst.get('fault_target')}）"
        f" 最慢端到端={worst_total['total_ms']}ms "
        f"平均重计算={sum(s['convergence_ms'] for s in samples) / len(samples):.1f}ms"
    )
    assert worst["convergence_ms"] < REQUIREMENT_LIMIT_MS, (
        f"{label}: 最慢一次路由重计算 {worst['convergence_ms']}ms 超过 {REQUIREMENT_LIMIT_MS}ms"
    )
    assert worst_total["total_ms"] < REQUIREMENT_LIMIT_MS, (
        f"{label}: 含推送的端到端耗时 {worst_total['total_ms']}ms 超过 {REQUIREMENT_LIMIT_MS}ms"
    )


def test_every_single_node_fault_recomputes_within_2s(simulator):
    """穷举 50 个节点的单点故障：每一次重计算都必须 < 2 秒，且故障后路由仍然完备"""
    samples = []
    for node_id in sorted(simulator.topology.nodes):
        data = _fault_and_recover(simulator, {"type": "node", "id": node_id})
        samples.append(data)

    _report(samples, f"{TEST_NODE_COUNT} 节点全量单点故障")
    assert_no_blackhole(simulator.topology)
    # 所有故障都已恢复，因此每个节点应能到达其余全部节点
    assert assert_routing_complete(simulator.topology) == TEST_NODE_COUNT * (TEST_NODE_COUNT - 1)


def test_every_single_link_fault_recomputes_within_2s(simulator):
    """穷举全部链路的单点故障"""
    links = [(link.source_id, link.target_id) for link in simulator.topology.links]
    assert len(links) >= 50, "测试前提：拓扑应有足够多的链路"

    samples = []
    for source, target in links:
        data = _fault_and_recover(simulator, {"type": "link", "source": source, "target": target})
        samples.append(data)

    _report(samples, f"{len(links)} 条链路全量单点故障")
    assert_no_blackhole(simulator.topology)
    assert assert_routing_complete(simulator.topology) == TEST_NODE_COUNT * (TEST_NODE_COUNT - 1)


def test_metrics_reflect_requirement(simulator):
    injected = run(simulator.handle_command({"action": "inject_fault", "params": {"type": "node", "id": "R25"}}))
    recovered = run(simulator.handle_command({"action": "recover_fault", "params": {"type": "node", "id": "R25"}}))
    data, metrics = injected["data"], simulator.get_metrics()

    # last_* 记录的是最近一次（这里是恢复）的重计算结果
    assert recovered["data"]["convergence_ms"] == metrics["last_convergence_ms"]
    assert metrics["route_recalculations"] == 2  # 故障 + 恢复
    assert metrics["faults_injected"] == 1
    assert metrics["faults_recovered"] == 1
    assert metrics["max_convergence_ms"] < REQUIREMENT_LIMIT_MS
    assert metrics["avg_convergence_ms"] is not None
    assert metrics["convergence_samples"] == 2
    assert metrics["node_count"] == TEST_NODE_COUNT
    assert metrics["link_count"] == len(simulator.topology.links)
    assert metrics["topology_source"] == "random"


def test_topology_generation_is_reproducible():
    """固定随机种子后，拓扑与路由必须完全可复现（报告与答辩需要）"""
    first = Simulator(num_nodes=30, seed=1234)
    second = Simulator(num_nodes=30, seed=1234)

    assert [(l.source_id, l.target_id, l.cost) for l in first.topology.links] == \
           [(l.source_id, l.target_id, l.cost) for l in second.topology.links]
    assert first.topology.get_node("R7").get_routing_table() == second.topology.get_node("R7").get_routing_table()

    other = Simulator(num_nodes=30, seed=4321)
    assert [(l.source_id, l.target_id, l.cost) for l in other.topology.links] != \
           [(l.source_id, l.target_id, l.cost) for l in first.topology.links]


@pytest.mark.parametrize("node_count", [60, 100, 150])
def test_scales_beyond_requirement(node_count):
    """超出 50 节点的规模仍有充足的 2 秒余量（说明实现不是"刚好卡线"）"""
    simulator = Simulator(num_nodes=node_count, seed=TEST_SEED)
    data = _fault_and_recover(simulator, {"type": "node", "id": "R5"})
    print(f"\n[扩展性] N={node_count} 重计算={data['convergence_ms']}ms")
    assert data["convergence_ms"] < REQUIREMENT_LIMIT_MS
