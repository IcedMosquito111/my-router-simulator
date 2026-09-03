"""
拓扑管理类：维护网络中所有节点和链路的集合，并提供查询和修改操作。
支持自动为链路两端分配接口 IP 地址，简化网络构建过程。
"""

import ipaddress
from typing import Dict, List, Optional, Tuple, Any

from .node import Node, Interface
from .link import Link


class Topology:
    """
    网络拓扑，包含节点字典和链路列表。
    节点通过 node_id 唯一标识，链路连接两个节点。
    """

    def __init__(self):
        self.nodes: Dict[str, Node] = {}
        self.links: List[Link] = []
        # 用于自动分配 IP 的计数器
        self._ip_counter = 0       # IPv4 子网索引
        self._ipv6_counter = 0     # IPv6 子网索引

    # ---------- 节点管理 ----------
    def add_node(self, node: Node) -> None:
        """添加一个节点到拓扑中，如果节点 ID 已存在则抛出异常"""
        if node.id in self.nodes:
            raise ValueError(f"节点 {node.id} 已存在")
        self.nodes[node.id] = node

    def remove_node(self, node_id: str) -> None:
        """移除一个节点，同时删除所有与之相连的链路"""
        if node_id not in self.nodes:
            return
        # 删除相关链路
        links_to_remove = [link for link in self.links if link.source_id == node_id or link.target_id == node_id]
        for link in links_to_remove:
            self.links.remove(link)
            # 从对端节点的接口中移除
            other_id = link.source_id if link.target_id == node_id else link.target_id
            other_node = self.nodes.get(other_id)
            if other_node:
                other_node.interfaces = [intf for intf in other_node.interfaces if intf.neighbor_id != node_id]
        # 删除节点
        del self.nodes[node_id]

    def get_node(self, node_id: str) -> Optional[Node]:
        """根据 ID 获取节点对象"""
        return self.nodes.get(node_id)

    # ---------- 链路管理 ----------
    def add_link(self, link: Link) -> None:
        """直接添加一个链路对象（不进行接口配置），需确保节点存在"""
        if link.source_id not in self.nodes or link.target_id not in self.nodes:
            raise ValueError("链路两端的节点必须已存在于拓扑中")
        # 避免重复链路
        for existing in self.links:
            if existing.connects(link.source_id, link.target_id):
                raise ValueError(f"节点 {link.source_id} 和 {link.target_id} 之间已存在链路")
        self.links.append(link)

    def remove_link(self, source_id: str, target_id: str) -> None:
        """移除连接两个节点的链路（如果存在）"""
        for link in self.links:
            if link.connects(source_id, target_id):
                self.links.remove(link)
                # 从两端节点的接口中移除相关接口
                source_node = self.nodes.get(source_id)
                target_node = self.nodes.get(target_id)
                if source_node:
                    source_node.interfaces = [intf for intf in source_node.interfaces if intf.neighbor_id != target_id]
                if target_node:
                    target_node.interfaces = [intf for intf in target_node.interfaces if intf.neighbor_id != source_id]
                return

    def get_link_between(self, node1_id: str, node2_id: str) -> Optional[Link]:
        """返回连接两个节点的链路对象，如果不存在返回 None"""
        for link in self.links:
            if link.connects(node1_id, node2_id):
                return link
        return None

    def get_all_links(self) -> List[Link]:
        """返回所有链路对象"""
        return self.links

    # ---------- 便捷连接方法（自动配置接口和 IP） ----------
    def connect_nodes(
        self,
        node1_id: str,
        node2_id: str,
        cost: int = 1,
        bandwidth: int = 100,
        delay: int = 1,
    ) -> Link:
        """
        在两个节点之间建立一条链路，并自动为两端创建接口、分配 IP 地址。

        参数:
            node1_id, node2_id: 节点 ID
            cost: 链路代价
            bandwidth: 带宽（Mbps）
            delay: 延迟（ms）

        返回:
            创建的 Link 对象
        """
        if node1_id not in self.nodes or node2_id not in self.nodes:
            raise ValueError("节点不存在")
        if self.get_link_between(node1_id, node2_id):
            raise ValueError(f"节点 {node1_id} 和 {node2_id} 之间已存在链路")

        # 生成一对 IP 地址（IPv4 和 IPv6）
        ipv4_1, ipv4_2 = self._allocate_ipv4_pair()
        ipv6_1, ipv6_2 = self._allocate_ipv6_pair()

        # 创建接口
        intf1 = Interface(
            name=f"{node1_id}-eth{len(self.nodes[node1_id].interfaces)}",
            ipv4=ipv4_1,
            ipv6=ipv6_1,
            neighbor_id=node2_id,
            neighbor_ipv4=ipv4_2.split('/')[0],
            neighbor_ipv6=ipv6_2.split('/')[0],
            cost=cost,
        )
        intf2 = Interface(
            name=f"{node2_id}-eth{len(self.nodes[node2_id].interfaces)}",
            ipv4=ipv4_2,
            ipv6=ipv6_2,
            neighbor_id=node1_id,
            neighbor_ipv4=ipv4_1.split('/')[0],
            neighbor_ipv6=ipv6_1.split('/')[0],
            cost=cost,
        )

        # 将接口添加到节点
        self.nodes[node1_id].add_interface(intf1)
        self.nodes[node2_id].add_interface(intf2)

        # 创建链路对象
        link = Link(
            source_id=node1_id,
            target_id=node2_id,
            cost=cost,
            bandwidth=bandwidth,
            delay=delay,
            status="up",
            source_interface=intf1.name,
            target_interface=intf2.name,
        )
        self.links.append(link)
        return link

    def disconnect_nodes(self, node1_id: str, node2_id: str) -> None:
        """断开两个节点之间的链路，并移除相关接口"""
        self.remove_link(node1_id, node2_id)

    # ---------- IP 地址分配辅助 ----------
    def _allocate_ipv4_pair(self) -> Tuple[str, str]:
        """
        分配一对 IPv4 地址。
        使用 10.0.0.0/16 网段，每个子网 /30，提供两个可用主机地址。
        子网起始地址 = 10.0.0.0 + _ip_counter * 4。
        """
        base_ip = ipaddress.IPv4Address('10.0.0.0')
        subnet_start = int(base_ip) + self._ip_counter * 4
        network = ipaddress.IPv4Network((subnet_start, 30), strict=False)
        hosts = list(network.hosts())
        if len(hosts) < 2:
            raise RuntimeError("IPv4 地址空间不足")
        self._ip_counter += 1
        ip1 = f"{hosts[0]}/30"
        ip2 = f"{hosts[1]}/30"
        return ip1, ip2

    def _allocate_ipv6_pair(self) -> Tuple[str, str]:
        """
        分配一对 IPv6 地址。
        使用 2001:db8:100::/48 网段，每个子网 /127，提供两个可用地址。
        子网起始地址 = 2001:db8:100:: + _ipv6_counter * 4。
        """
        base_ip = ipaddress.IPv6Address('2001:db8:100::')
        subnet_start = int(base_ip) + self._ipv6_counter * 4
        network = ipaddress.IPv6Network((subnet_start, 127), strict=False)
        hosts = list(network.hosts())
        if len(hosts) < 2:
            raise RuntimeError("IPv6 地址空间不足")
        self._ipv6_counter += 1
        ip1 = f"{hosts[0]}/127"
        ip2 = f"{hosts[1]}/127"
        return ip1, ip2

    # ---------- 信息导出 ----------
    def get_topology_snapshot(self) -> Dict[str, Any]:
        """
        导出当前拓扑的快照，符合 TopologySnapshot 模型的结构。
        返回:
            包含 nodes 和 links 列表的字典
        """
        nodes_data = []
        for node in self.nodes.values():
            node_info = node.get_node_info()
            nodes_data.append(node_info)

        links_data = [link.to_dict() for link in self.links]
        return {
            "nodes": nodes_data,
            "links": links_data,
        }

    def get_neighbors(self, node_id: str) -> List[str]:
        """返回指定节点的所有邻居节点 ID 列表（基于当前拓扑中的链路）"""
        neighbors = []
        node = self.nodes.get(node_id)
        if not node:
            return neighbors
        for link in self.links:
            if link.source_id == node_id and link.status == "up":
                neighbors.append(link.target_id)
            elif link.target_id == node_id and link.status == "up":
                neighbors.append(link.source_id)
        # 去重（理论上不会重复）
        return list(set(neighbors))

    def __repr__(self) -> str:
        return f"Topology(nodes={len(self.nodes)}, links={len(self.links)})"