# -*- coding: utf-8 -*-
"""
分组转发语义测试。

覆盖修复前实测存在的问题（P0）：
    1. ping 自己的回环地址被丢弃；
    2. ping 直连邻居的接口地址被当作"下一跳为直连"异常丢弃；
    3. 报文"穿过"已断链路却报告 delivered。
并校验：路径连续、代价最优、TTL 语义、丢弃原因可解释。
"""

import random

from conftest import run
from helpers import build_graph, loopback_index, path_link_cost, shortest_cost


def _send(simulator, src, dst_ip, ttl=64, protocol=None):
    params = {"src_node": src, "dst_ip": dst_ip, "ttl": ttl}
    if protocol:
        params["protocol"] = protocol
    return run(simulator.handle_command({"action": "send_packet", "params": params}))


def test_packet_to_own_loopback_is_delivered_locally(simulator):
    """源节点 ping 自己的回环地址：属于本地投递，不应查表/不应被丢弃"""
    node = simulator.topology.get_node("R1")
    result = _send(simulator, "R1", node.loopback_ipv4.split("/")[0])
    assert result["success"] is True, result["message"]
    assert result["data"]["path"] == ["R1"]
    assert result["data"]["status"] == "delivered"


def test_packet_to_own_interface_address_is_delivered_locally(simulator):
    node = simulator.topology.get_node("R1")
    own_address = node.interfaces[0].ipv4.split("/")[0]
    result = _send(simulator, "R1", own_address)
    assert result["success"] is True, result["message"]
    assert result["data"]["path"] == ["R1"]


def test_packet_to_direct_neighbor_interface_address(simulator):
    """目的地址是直连邻居的接口地址：next_hop=direct，应从该接口直接发出"""
    node = simulator.topology.get_node("R1")
    interface = node.interfaces[0]
    peer_id = interface.neighbor_id

    result = _send(simulator, "R1", interface.neighbor_ipv4)
    assert result["success"] is True, result["message"]
    assert result["data"]["path"] == ["R1", peer_id]


def test_random_pairs_follow_optimal_paths(simulator):
    """随机节点对：必须送达、路径连续、且路径总代价等于独立 Dijkstra 的最短路代价"""
    topology = simulator.topology
    graph = build_graph(topology)
    index = loopback_index(topology)
    rng = random.Random(7)
    node_ids = sorted(topology.nodes)

    checked = 0
    for _ in range(20):
        src, dst = rng.sample(node_ids, 2)
        dst_ip = topology.get_node(dst).loopback_ipv4.split("/")[0]
        result = _send(simulator, src, dst_ip)

        assert result["success"] is True, f"{src} -> {dst} 转发失败: {result['message']}"
        path = result["data"]["path"]
        assert path[0] == src and path[-1] == dst
        assert len(path) == len(set(path)), f"路径出现环路: {path}"

        expected_cost = shortest_cost(graph, src, dst)
        assert path_link_cost(topology, path) == expected_cost, f"{src} -> {dst} 路径非最优: {path}"

        hops = len(path) - 1
        # TTL 只在中转路由器处递减：路径 h 跳消耗 h-1 个 TTL
        assert result["data"]["ttl"] == 64 - (hops - 1)
        # 响应里的跳数必须是链路数（节点数 - 1），不能把节点数当跳数
        assert f"{hops} 跳" in result["message"], result["message"]
        checked += 1

    assert checked == 20
    assert index  # 索引非空，保证上面的映射有效


def test_ttl_semantics(simulator):
    """TTL 语义：恰好够用则送达，少 1 则在中途被丢弃（TTL 超时）"""
    topology = simulator.topology
    rng = random.Random(11)
    node_ids = sorted(topology.nodes)

    for _ in range(50):
        src, dst = rng.sample(node_ids, 2)
        dst_ip = topology.get_node(dst).loopback_ipv4.split("/")[0]
        baseline = _send(simulator, src, dst_ip)
        hops = len(baseline["data"]["path"]) - 1
        if hops >= 3:
            break
    assert hops >= 3, "测试前提：需要一条至少 3 跳的路径"

    assert _send(simulator, src, dst_ip, ttl=hops)["success"] is True

    short = _send(simulator, src, dst_ip, ttl=hops - 1)
    assert short["success"] is False
    assert short["data"]["status"] == "dropped"
    assert "TTL" in short["data"]["drop_reason"]


def test_packet_to_failed_node_is_dropped_with_reason(simulator):
    run(simulator.handle_command({"action": "inject_fault", "params": {"type": "node", "id": "R30"}}))
    result = _send(simulator, "R1", "10.255.0.30")
    assert result["success"] is False
    assert result["data"]["status"] == "dropped"
    assert result["data"]["drop_reason"], "丢弃原因必须非空，便于前端解释"
    assert "无到达" in result["data"]["drop_reason"]


def test_ipv6_forwarding(simulator):
    topology = simulator.topology
    source = "R2"
    target = "R41"
    dst_ip = topology.get_node(target).loopback_ipv6.split("/")[0]

    result = _send(simulator, source, dst_ip, protocol="IPv6")
    assert result["success"] is True, result["message"]
    assert result["data"]["protocol"] == "IPv6"
    assert result["data"]["path"][0] == source and result["data"]["path"][-1] == target

    graph = build_graph(topology)
    assert path_link_cost(topology, result["data"]["path"]) == shortest_cost(graph, source, target)


def test_dropped_packet_keeps_partial_path(simulator):
    """丢包时也要保留已走过的路径，前端才能展示"走到哪里失败" """
    run(simulator.handle_command({"action": "inject_fault", "params": {"type": "node", "id": "R30"}}))
    result = _send(simulator, "R1", "10.255.0.30")
    assert result["data"]["path"][0] == "R1"
    assert result["data"]["status"] == "dropped"
