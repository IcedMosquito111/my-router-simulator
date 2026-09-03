"""
简化的 RIP（Routing Information Protocol）路由协议实现。
基于距离向量算法，使用跳数作为度量（最大 15 跳）。
通过迭代交换距离向量，最终收敛到最短路径。
与 OSPF 模块保持相似接口，便于模拟器切换。
"""

import ipaddress
import logging
from typing import Dict, Any, List, Optional, Tuple

from ..node import Node
from ..topology import Topology

logger = logging.getLogger("router-simulator.rip")

MAX_HOPS = 15  # RIP 最大跳数
INF = 16       # 表示不可达


class RIP:
    """
    RIP 协议模拟器。
    维护每个节点的距离向量表，并通过迭代更新直至收敛。
    """

    def __init__(self, topology: Topology):
        """
        初始化 RIP 实例。

        参数:
            topology: 网络拓扑对象
        """
        self.topology = topology
        # 距离向量表：{node_id: {dest_id: (metric, next_hop_id)}}
        self.distance_vectors: Dict[str, Dict[str, Tuple[int, Optional[str]]]] = {}
        # 设置节点获取回环地址的回调（与 OSPF 一致）
        Node.set_loopback_callback(self._get_loopback)

    def _get_loopback(self, node_id: str) -> Tuple[Optional[str], Optional[str]]:
        """回调：返回节点的回环 IPv4 和 IPv6 地址"""
        node = self.topology.get_node(node_id)
        if node:
            return node.loopback_ipv4, node.loopback_ipv6
        return None, None

    def _initialize_distance_vectors(self) -> None:
        """
        初始化所有节点的距离向量表。
        每个节点只知道自己（距离 0）和直连的 up 邻居（距离 1）。
        """
        self.distance_vectors.clear()
        # 获取所有 up 状态的节点
        up_nodes = {nid: node for nid, node in self.topology.nodes.items() if node.is_up()}
        if not up_nodes:
            return

        # 为每个节点创建初始距离向量
        for nid in up_nodes:
            self.distance_vectors[nid] = {nid: (0, None)}  # 到自己的距离 0

        # 根据 up 状态的链路添加直连邻居
        for link in self.topology.links:
            if link.is_up():
                src, dst = link.source_id, link.target_id
                # 只在双方节点都是 up 时才添加
                if src in up_nodes and dst in up_nodes:
                    # 链路代价统一视为 1（RIP 跳数）
                    self.distance_vectors[src][dst] = (1, dst)
                    self.distance_vectors[dst][src] = (1, src)

    def _bellman_ford_update(self) -> bool:
        """
        对所有节点执行一轮距离向量更新。
        每个节点向邻居发送自己的距离向量，邻居根据收到信息更新自己的表。
        返回是否有任何更新发生。
        """
        changed = False
        up_nodes = [nid for nid in self.distance_vectors.keys()]
        # 遍历每个节点
        for nid in up_nodes:
            node_dv = self.distance_vectors[nid]
            # 遍历该节点的邻居（在拓扑中找直连且 up 的节点）
            neighbors = self.topology.get_neighbors(nid)
            for neighbor_id in neighbors:
                # 检查邻居是否存在且 up
                if neighbor_id not in self.distance_vectors:
                    continue
                neighbor_dv = self.distance_vectors[neighbor_id]
                # 遍历邻居的距离向量表
                for dest, (metric, next_hop) in neighbor_dv.items():
                    # 跳过邻居本身不可达的情况
                    if metric >= INF:
                        continue
                    new_metric = metric + 1  # 经过邻居多一跳
                    if new_metric > MAX_HOPS:
                        new_metric = INF
                    # 检查当前节点表中是否有该目的
                    if dest not in node_dv or new_metric < node_dv[dest][0]:
                        node_dv[dest] = (new_metric, neighbor_id)
                        changed = True
                    elif node_dv[dest][1] == neighbor_id and new_metric != node_dv[dest][0]:
                        # 如果路径经过同一邻居但代价变化，更新（处理链路故障）
                        node_dv[dest] = (new_metric, neighbor_id)
                        changed = True
        return changed

    def sync_all(self) -> List[Dict[str, Any]]:
        """
        执行完整的 RIP 路由计算。
        初始化距离向量，然后迭代更新直到收敛（或达到最大迭代次数）。
        最后将计算结果写入每个节点的 routing_table。

        返回:
            所有节点的路由表更新列表（与 OSPF 相同格式）
        """
        self._initialize_distance_vectors()
        # 迭代更新，最多节点数轮（确保收敛）
        max_iterations = len(self.distance_vectors) + 5
        for iteration in range(max_iterations):
            if not self._bellman_ford_update():
                logger.info(f"RIP 收敛完成，迭代次数: {iteration + 1}")
                break
        else:
            logger.warning("RIP 未完全收敛，达到最大迭代次数")

        # 根据距离向量更新每个节点的路由表
        routing_updates = []
        for nid, dv in self.distance_vectors.items():
            node = self.topology.get_node(nid)
            if not node or not node.is_up():
                continue
            # 清空旧路由表
            node.routing_table.clear()
            # 添加直连路由（与 OSPF 类似）
            for intf in node.interfaces:
                if intf.is_up():
                    if intf.ipv4:
                        ip, mask = intf.ipv4.split("/")
                        network = ipaddress.IPv4Network(f"{ip}/{mask}", strict=False)
                        node.routing_table[str(network)] = {
                            "destination": str(network),
                            "next_hop": "direct",
                            "metric": 0,
                            "interface": intf.name,
                            "protocol": "connected",
                        }
                    if intf.ipv6:
                        ip6, mask6 = intf.ipv6.split("/")
                        network6 = ipaddress.IPv6Network(f"{ip6}/{mask6}", strict=False)
                        node.routing_table[str(network6)] = {
                            "destination": str(network6),
                            "next_hop": "direct",
                            "metric": 0,
                            "interface": intf.name,
                            "protocol": "connected",
                        }
            # 添加通过 RIP 学到的远端路由
            for dest_id, (metric, next_hop_id) in dv.items():
                if dest_id == nid or metric >= INF:
                    continue
                # 获取目的节点的回环地址
                dest_ipv4, dest_ipv6 = self._get_loopback(dest_id)
                if dest_ipv4:
                    dest_prefix = dest_ipv4  # 修改点：直接使用，不再拼接 /32
                    intf = node.get_interface_to_neighbor(next_hop_id) if next_hop_id else None
                    next_hop_ip = intf.neighbor_ipv4 if intf else None
                    if next_hop_ip:
                        node.routing_table[dest_prefix] = {
                            "destination": dest_prefix,
                            "next_hop": next_hop_ip,
                            "metric": metric,
                            "interface": intf.name,
                            "protocol": "RIP",
                        }
                if dest_ipv6:
                    dest_prefix6 = dest_ipv6  # 修改点：直接使用，不再拼接 /128
                    intf = node.get_interface_to_neighbor(next_hop_id) if next_hop_id else None
                    next_hop_ip6 = intf.neighbor_ipv6 if intf else None
                    if next_hop_ip6:
                        node.routing_table[dest_prefix6] = {
                            "destination": dest_prefix6,
                            "next_hop": next_hop_ip6,
                            "metric": metric,
                            "interface": intf.name,
                            "protocol": "RIP",
                        }
            routing_updates.append({
                "node_id": nid,
                "routes": node.get_routing_table(),
            })
        return routing_updates

    def handle_topology_change(self) -> List[Dict[str, Any]]:
        """处理拓扑变化后重新计算全网路由"""
        logger.info("RIP 检测到拓扑变化，重新计算路由...")
        return self.sync_all()