# -*- coding: utf-8 -*-
"""
路由计算正确性测试：与"独立实现的最短路算法"逐条对照。

关键点：这里不复用被测的 Dijkstra，而是在测试里重新实现堆版 Dijkstra，
这样才能真正发现"路由算错"而不是"自己验自己"。
"""

import ipaddress
import json

from conftest import run
from helpers import (
    assert_no_blackhole,
    assert_no_route_to_unreachable,
    assert_routing_complete,
    build_graph,
    dijkstra,
    loopback_index,
    shortest_hops,
)


def _assert_routes_are_optimal(topology) -> int:
    """所有 up 节点的每条路由，度量值必须等于独立 Dijkstra 的最短路代价"""
    graph = build_graph(topology)
    index = loopback_index(topology)
    checked = 0
    for node in topology.nodes.values():
        if not node.is_up():
            continue
        dist, _ = dijkstra(graph, node.id)
        for route in node.get_routing_table():
            destination = route["destination"]
            # 本机路由（local）、直连网段（connected）与展示型路由不作为"协议学到的远端路由"比对
            if route["protocol"] in ("local", "connected") or route["next_hop"] == "local":
                continue
            if "/" not in destination:
                continue
            target_id = index.get(destination.split("/")[0])
            assert target_id is not None, f"{node.id} 的路由目的 {destination} 不属于任何节点回环地址"
            expected = dist.get(target_id)
            if expected == float("inf"):
                continue  # 不可达目的地在 assert_no_route_to_unreachable 中单独校验
            assert route["metric"] == expected, (
                f"{node.id} -> {target_id} 路由度量 {route['metric']} != 独立最短路 {expected}"
            )
            checked += 1
    return checked


def test_initial_routes_match_independent_dijkstra(simulator):
    checked = _assert_routes_are_optimal(simulator.topology)
    assert checked > 2000, f"比对的路由条目过少（{checked}），测试可能失效"
    # 每个节点对同时存在 IPv4 与 IPv6 两条路由，因此比对条数应为可达节点对数的 2 倍
    assert assert_routing_complete(simulator.topology) * 2 == checked
    assert_no_blackhole(simulator.topology)
    assert_no_route_to_unreachable(simulator.topology)


def test_routes_after_node_fault_match_independent_dijkstra(simulator):
    result = run(simulator.handle_command({"action": "inject_fault", "params": {"type": "node", "id": "R17"}}))
    assert result["success"] is True
    assert _assert_routes_are_optimal(simulator.topology) > 2000
    assert_no_blackhole(simulator.topology)
    assert_no_route_to_unreachable(simulator.topology)


def test_routes_after_link_fault_match_independent_dijkstra(simulator):
    result = run(simulator.handle_command({
        "action": "inject_fault", "params": {"type": "link", "source": "R17", "target": "R18"},
    }))
    assert result["success"] is True
    assert _assert_routes_are_optimal(simulator.topology) > 2000
    assert_no_blackhole(simulator.topology)
    assert_no_route_to_unreachable(simulator.topology)


def test_routes_after_recovery_match_independent_dijkstra(simulator):
    run(simulator.handle_command({"action": "inject_fault", "params": {"type": "node", "id": "R17"}}))
    run(simulator.handle_command({"action": "inject_fault", "params": {"type": "link", "source": "R3", "target": "R4"}}))
    run(simulator.handle_command({"action": "recover_fault", "params": {"type": "node", "id": "R17"}}))
    run(simulator.handle_command({"action": "recover_fault", "params": {"type": "link", "source": "R3", "target": "R4"}}))
    assert _assert_routes_are_optimal(simulator.topology) > 2000
    assert_no_blackhole(simulator.topology)


def test_local_and_connected_routes_present(simulator):
    """每个节点都必须有：本机地址（local）+ 直连网段（direct）路由"""
    for node in simulator.topology.nodes.values():
        table = {route["destination"]: route for route in node.get_routing_table()}
        assert node.loopback_ipv4 in table, f"{node.id} 缺少自身回环地址的本地路由"
        assert table[node.loopback_ipv4]["next_hop"] == "local"
        for intf in node.interfaces:
            host_prefix = intf.ipv4.split("/")[0] + "/32"
            assert host_prefix in table, f"{node.id} 缺少接口地址 {intf.ipv4} 的主机路由 {host_prefix}"
            assert table[host_prefix]["next_hop"] == "local"
            subnet = str(ipaddress.ip_network(intf.ipv4, strict=False))
            assert subnet in table, f"{node.id} 缺少直连网段 {subnet} 的路由"
            assert table[subnet]["next_hop"] == "direct"


def test_routing_table_is_json_serializable(simulator):
    """路由表内部带前缀缓存，但对外的路由条目必须是纯 JSON（否则推送会失败）"""
    for node in simulator.topology.nodes.values():
        payload = json.dumps(node.get_routing_table())
        assert "RouteEntry" not in payload
        assert "IPv4Network" not in payload


def test_rip_matches_independent_bfs_hops(simulator):
    """RIP（距离向量，跳数度量）必须与独立 BFS 的最少跳数一致"""
    from network.routing.rip import RIP

    rip = RIP(simulator.topology)
    rip.sync_all()
    graph = build_graph(simulator.topology)
    index = loopback_index(simulator.topology)

    checked = 0
    for node in simulator.topology.nodes.values():
        for route in node.get_routing_table():
            if route["protocol"] != "RIP":
                continue
            target_id = index.get(route["destination"].split("/")[0])
            assert target_id is not None
            hops = shortest_hops(graph, node.id, target_id)
            assert hops is not None and hops <= 15, f"RIP 不应存在超过 15 跳的路由: {route}"
            assert route["metric"] == hops, (
                f"RIP 在 {node.id} -> {target_id} 的跳数 {route['metric']} != 独立 BFS {hops}"
            )
            checked += 1
    assert checked > 2000, f"比对的 RIP 路由条目过少（{checked}）"
    assert_no_blackhole(simulator.topology)
