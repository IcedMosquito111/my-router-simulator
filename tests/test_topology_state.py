# -*- coding: utf-8 -*-
"""
拓扑状态一致性测试。

这里覆盖的核心缺陷（修复前实测复现）：
    1. 节点故障后，与之相连的链路在快照里仍显示为 "up"（前端会出现红节点+正常链路的矛盾画面）；
    2. 链路故障 + 节点故障/恢复后，节点接管了已断链路，路由重新指向死链路，
       报文"穿过"已断链路仍然报告 delivered。
"""

from conftest import run
from helpers import assert_no_blackhole, build_graph, reachable_nodes


def _attached_links(topology, node_id):
    return [link for link in topology.links if node_id in (link.source_id, link.target_id)]


def _interfaces_to(topology, node_id, neighbor_id):
    return [intf for intf in topology.get_node(node_id).interfaces if intf.neighbor_id == neighbor_id]


def test_topology_snapshot_exposes_loopback_addresses(simulator):
    """前端需要按协议族取目的地址，因此快照必须带出回环 IPv4/IPv6"""
    snapshot = simulator.get_topology_snapshot()
    assert len(snapshot["nodes"]) == 50
    for node in snapshot["nodes"]:
        assert node["loopback_ipv4"], f"{node['id']} 缺少回环 IPv4"
        assert node["loopback_ipv6"], f"{node['id']} 缺少回环 IPv6"
        assert node["loopback_ipv4"].endswith("/32")
        assert node["loopback_ipv6"].endswith("/128")
        assert any(":" in address for address in node["ipv6_interfaces"])


def test_node_fault_marks_attached_links_and_interfaces_down(simulator):
    victim = "R25"
    attached = _attached_links(simulator.topology, victim)
    assert attached, "测试前提：R25 应有相连链路"

    result = run(simulator.handle_command({"action": "inject_fault", "params": {"type": "node", "id": victim}}))
    assert result["success"] is True

    snapshot = {
        frozenset((item["source"], item["target"])): item
        for item in simulator.get_topology_snapshot()["links"]
    }
    assert simulator.topology.get_node(victim).routing_table == {}, "故障节点不应保留路由表"

    for link in attached:
        # 节点故障不改变链路自身的（配置/注入）状态，但链路的"有效状态"必须变为 down：
        # 对外快照里 status 表示有效状态，admin_status 保留链路自身状态，
        # 这样前端不会出现"节点红色故障、链路却显示正常"的矛盾画面。
        assert link.status == "up", "节点故障不应修改链路自身的状态标记"
        assert simulator.topology.is_link_operational(link) is False
        item = snapshot[frozenset((link.source_id, link.target_id))]
        assert item["status"] == "down", "快照中的链路有效状态应为 down"
        assert item["admin_status"] == "up"
        peer_id = link.get_other_end(victim)
        peer = simulator.topology.get_node(peer_id)
        assert victim not in peer.get_neighbors(), f"{peer_id} 不应再把 {victim} 当作可用邻居"
        for intf in _interfaces_to(simulator.topology, peer_id, victim):
            assert intf.status == "down"

    assert_no_blackhole(simulator.topology)


def test_node_fault_drops_routes_towards_failed_node(simulator):
    victim = "R25"
    run(simulator.handle_command({"action": "inject_fault", "params": {"type": "node", "id": victim}}))

    graph = build_graph(simulator.topology)
    for node in simulator.topology.nodes.values():
        if not node.is_up():
            continue
        destinations = {route["destination"] for route in node.get_routing_table()}
        assert "10.255.0.25/32" not in destinations, f"{node.id} 仍保留了到故障节点 {victim} 的路由"
        assert reachable_nodes(graph, node.id) <= {n.id for n in simulator.topology.nodes.values()}


def test_recovering_node_does_not_resurrect_failed_link(simulator):
    """回归测试：链路故障 + 节点故障 + 节点恢复后，接口不能"复活"，路由必须绕开死链路"""
    topology = simulator.topology
    link = topology.get_link_between("R1", "R2")
    assert link is not None

    assert run(simulator.handle_command({"action": "inject_fault", "params": {"type": "link", "source": "R1", "target": "R2"}}))["success"]
    assert run(simulator.handle_command({"action": "inject_fault", "params": {"type": "node", "id": "R1"}}))["success"]
    assert run(simulator.handle_command({"action": "recover_fault", "params": {"type": "node", "id": "R1"}}))["success"]

    assert link.status == "down", "链路本身仍是故障状态，不应被节点恢复动作改回 up"
    dead_interfaces = {intf.name for intf in _interfaces_to(topology, "R1", "R2")}
    assert dead_interfaces
    for name in dead_interfaces:
        assert topology.get_node("R1").get_interface_by_name(name).status == "down"

    # R1 到 R2 的路由必须绕开那条死链路（或不可达），绝不能指向 R2 侧的死接口
    node_r2 = topology.get_node("R2")
    for route in topology.get_node("R1").get_routing_table():
        if route["destination"] == node_r2.loopback_ipv4:
            assert route["interface"] not in dead_interfaces, "路由重新指向了已断链路的出接口"

    assert_no_blackhole(topology)


def test_packet_does_not_use_failed_link_after_recovery(simulator):
    """报文不允许"穿过"已断链路：必须走替代路径，且路径中不能出现 R1->R2 这一跳"""
    simulator.topology.get_link_between("R1", "R2")
    run(simulator.handle_command({"action": "inject_fault", "params": {"type": "link", "source": "R1", "target": "R2"}}))
    run(simulator.handle_command({"action": "inject_fault", "params": {"type": "node", "id": "R1"}}))
    run(simulator.handle_command({"action": "recover_fault", "params": {"type": "node", "id": "R1"}}))

    result = run(simulator.handle_command({
        "action": "send_packet",
        "params": {"src_node": "R1", "dst_ip": "10.255.0.2"},
    }))
    path = result["data"]["path"]
    assert result["success"] is True, result["message"]
    for source, target in zip(path, path[1:]):
        link = simulator.topology.get_link_between(source, target)
        assert link is not None and link.is_up(), f"路径经过了不可用链路 {source}-{target}"


def test_link_fault_updates_both_ends_and_neighbors(simulator):
    topology = simulator.topology
    result = run(simulator.handle_command({
        "action": "inject_fault", "params": {"type": "link", "source": "R3", "target": "R4"},
    }))
    assert result["success"] is True

    failed_link = topology.get_link_between("R3", "R4")
    assert failed_link.status == "down", "链路故障注入后链路自身状态应为 down"
    assert topology.is_link_operational(failed_link) is False
    assert "R4" not in topology.get_node("R3").get_neighbors()
    assert "R3" not in topology.get_node("R4").get_neighbors()
    for name in ("R3", "R4"):
        peer = "R4" if name == "R3" else "R3"
        for intf in _interfaces_to(topology, name, peer):
            assert intf.status == "down"

    assert_no_blackhole(topology)


def test_partitioned_network_has_no_cross_routes_and_packets_drop(simulator):
    """把 R1 的所有链路都置为故障，制造网络分区：两侧不应有对方路由，分组应明确被丢弃"""
    topology = simulator.topology
    victims = [link.get_other_end("R1") for link in _attached_links(topology, "R1")]
    for neighbor in victims:
        result = run(simulator.handle_command({
            "action": "inject_fault", "params": {"type": "link", "source": "R1", "target": neighbor},
        }))
        assert result["success"] is True

    graph = build_graph(topology)
    assert "R2" not in reachable_nodes(graph, "R1"), "测试前提：R1 应已被隔离"

    result = run(simulator.handle_command({
        "action": "send_packet", "params": {"src_node": "R1", "dst_ip": "10.255.0.2"},
    }))
    assert result["success"] is False
    assert result["data"]["status"] == "dropped"
    assert "无到达" in result["data"]["drop_reason"]
    assert_no_blackhole(topology)


def test_full_recovery_restores_previous_routes(simulator):
    topology = simulator.topology
    baseline = {
        node.id: {route["destination"] for route in node.get_routing_table()}
        for node in topology.nodes.values()
    }

    run(simulator.handle_command({"action": "inject_fault", "params": {"type": "node", "id": "R10"}}))
    run(simulator.handle_command({"action": "inject_fault", "params": {"type": "link", "source": "R20", "target": "R21"}}))
    run(simulator.handle_command({"action": "recover_fault", "params": {"type": "node", "id": "R10"}}))
    run(simulator.handle_command({"action": "recover_fault", "params": {"type": "link", "source": "R20", "target": "R21"}}))

    for node in topology.nodes.values():
        assert node.is_up()
        assert {route["destination"] for route in node.get_routing_table()} == baseline[node.id]
        for intf in node.interfaces:
            assert intf.status == "up"
    for link in topology.links:
        assert link.status == "up"
