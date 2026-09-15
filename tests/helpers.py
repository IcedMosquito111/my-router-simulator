# -*- coding: utf-8 -*-
"""
测试辅助：与实现完全独立的参考算法与不变量检查。

这里刻意"另写一遍 Dijkstra / BFS"，避免用被测代码验证被测代码。
"""

import heapq
from typing import Dict, Optional, Set


def build_graph(topology) -> Dict[str, Dict[str, int]]:
    """从拓扑构建邻接表（只含当前可承载流量的链路），{node_id: {neighbor_id: cost}}"""
    graph: Dict[str, Dict[str, int]] = {node_id: {} for node_id in topology.nodes}
    for link in topology.links:
        if not topology.is_link_operational(link):
            continue
        for a, b in ((link.source_id, link.target_id), (link.target_id, link.source_id)):
            if a not in graph:
                continue
            existing = graph[a].get(b)
            graph[a][b] = link.cost if existing is None else min(existing, link.cost)
    return graph


def dijkstra(graph: Dict[str, Dict[str, int]], source: str):
    """标准 Dijkstra（堆实现），返回 (dist, prev)"""
    dist: Dict[str, float] = {node_id: float("inf") for node_id in graph}
    prev: Dict[str, Optional[str]] = {node_id: None for node_id in graph}
    if source not in graph:
        return dist, prev
    dist[source] = 0
    heap = [(0, source)]
    while heap:
        current_dist, current = heapq.heappop(heap)
        if current_dist > dist[current]:
            continue
        for neighbor, cost in graph[current].items():
            new_dist = current_dist + cost
            if new_dist < dist[neighbor]:
                dist[neighbor] = new_dist
                prev[neighbor] = current
                heapq.heappush(heap, (new_dist, neighbor))
    return dist, prev


def shortest_cost(graph: Dict[str, Dict[str, int]], source: str, target: str) -> Optional[int]:
    """独立计算最短路代价，不可达返回 None"""
    dist, _ = dijkstra(graph, source)
    value = dist.get(target, float("inf"))
    return None if value == float("inf") else int(value)


def shortest_hops(graph: Dict[str, Dict[str, int]], source: str, target: str) -> Optional[int]:
    """独立计算最少跳数（BFS，忽略代价）"""
    if source == target:
        return 0
    seen = {source}
    queue = [(source, 0)]
    while queue:
        node, depth = queue.pop(0)
        for neighbor in graph.get(node, {}):
            if neighbor == target:
                return depth + 1
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append((neighbor, depth + 1))
    return None


def reachable_nodes(graph: Dict[str, Dict[str, int]], source: str) -> Set[str]:
    """独立计算从 source 出发可达的节点集合"""
    dist, _ = dijkstra(graph, source)
    return {node_id for node_id, value in dist.items() if value != float("inf")}


def assert_no_blackhole(topology) -> None:
    """
    不变量：任何 up 节点的每一条路由，其出接口/链路/对端节点都必须可用。

    这正是"报文可能被转发进已断链路"这类仿真失真的通用检查。
    """
    for node in topology.nodes.values():
        if not node.is_up():
            continue
        for route in node.get_routing_table():
            if route["next_hop"] == "local":
                continue
            interface = node.get_interface_by_name(route["interface"])
            assert interface is not None, f"{node.id} 的路由 {route} 指向不存在的接口"
            assert interface.is_up(), f"{node.id} 的路由 {route} 使用了 down 的接口"
            peer_id = interface.neighbor_id
            peer = topology.get_node(peer_id)
            assert peer is not None and peer.is_up(), f"{node.id} 的路由 {route} 的下一跳节点不可用"
            link = topology.get_link_between(node.id, peer_id)
            assert link is not None and link.is_up(), f"{node.id} 的路由 {route} 的链路已断"


def assert_no_route_to_unreachable(topology) -> None:
    """不变量：不可达的目的地不应出现在路由表中（防止遗留过期路由）"""
    graph = build_graph(topology)
    for node_id, node in topology.nodes.items():
        if not node.is_up():
            continue
        reachable = reachable_nodes(graph, node_id)
        for route in node.get_routing_table():
            if route["next_hop"] == "local" or "/" not in route["destination"]:
                continue
            interface = node.get_interface_by_name(route["interface"])
            assert interface is not None
            assert interface.neighbor_id in reachable, (
                f"{node.id} 存在指向不可达下一跳 {interface.neighbor_id} 的路由 {route}"
            )


def assert_routing_complete(topology) -> int:
    """不变量：每个 up 节点都能到达所有其它可达 up 节点的回环地址，返回比较次数"""
    graph = build_graph(topology)
    up_nodes = [node for node in topology.nodes.values() if node.is_up()]
    checked = 0
    for node in up_nodes:
        reachable = reachable_nodes(graph, node.id)
        table = {route["destination"] for route in node.get_routing_table()}
        for other in up_nodes:
            if other.id == node.id or other.id not in reachable:
                continue
            assert other.loopback_ipv4 in table, f"{node.id} 缺少到 {other.id} 的路由"
            checked += 1
    return checked

def loopback_index(topology) -> Dict[str, str]:
    """回环地址（不含掩码）-> 节点 ID 的索引，用于把路由表目的前缀映射回节点"""
    index: Dict[str, str] = {}
    for node_id, node in topology.nodes.items():
        for address in (node.loopback_ipv4, node.loopback_ipv6):
            if address:
                index[address.split("/")[0]] = node_id
    return index


def path_link_cost(topology, path) -> int:
    """计算一条路径的总链路代价；路径中存在不可用链路时抛 AssertionError"""
    total = 0
    for source, target in zip(path, path[1:]):
        link = topology.get_link_between(source, target)
        assert link is not None, f"路径 {source}->{target} 不存在链路"
        assert link.is_up(), f"路径 {source}->{target} 的链路已故障"
        assert topology.is_link_operational(link), f"路径 {source}->{target} 的链路不可用"
        total += link.cost
    return total
