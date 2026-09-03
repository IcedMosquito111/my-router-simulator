"""
路由器节点类：模拟网络中的一个路由器。
每个节点维护自己的接口、邻居关系、链路状态数据库（LSDB）和路由表。
支持 IPv4 和 IPv6 路由计算与分组转发。
"""

from typing import List, Dict, Optional, Tuple, Any
import ipaddress
from .link import Link  # 仅用于类型提示，实际节点不直接持有链路对象


class Interface:
    """
    节点的一个网络接口，连接到一个邻居路由器。
    接口包含本端和对端的 IP 地址、链路代价等信息。
    """

    def __init__(
        self,
        name: str,
        ipv4: Optional[str] = None,          # 本端 IPv4 地址，格式 "192.168.1.1/24"
        ipv6: Optional[str] = None,          # 本端 IPv6 地址，格式 "2001:db8::1/64"
        neighbor_id: Optional[str] = None,   # 对端节点 ID
        neighbor_ipv4: Optional[str] = None, # 对端接口 IPv4 地址（不含掩码）
        neighbor_ipv6: Optional[str] = None, # 对端接口 IPv6 地址（不含掩码）
        cost: int = 1,                       # 链路代价
        status: str = "up"                   # 接口状态："up" 或 "down"
    ):
        self.name = name
        self.ipv4 = ipv4
        self.ipv6 = ipv6
        self.neighbor_id = neighbor_id
        self.neighbor_ipv4 = neighbor_ipv4
        self.neighbor_ipv6 = neighbor_ipv6
        self.cost = cost
        self.status = status

    def is_up(self) -> bool:
        return self.status == "up"

    def get_prefix(self, version: str = "ipv4") -> Optional[Tuple[str, int]]:
        """提取本端地址的网络前缀，返回 (网络地址, 前缀长度) 或 None"""
        addr = self.ipv4 if version == "ipv4" else self.ipv6
        if not addr:
            return None
        ip, mask = addr.split("/") if "/" in addr else (addr, "32" if version == "ipv4" else "128")
        return ip, int(mask)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "ipv4": self.ipv4,
            "ipv6": self.ipv6,
            "neighbor_id": self.neighbor_id,
            "neighbor_ipv4": self.neighbor_ipv4,
            "neighbor_ipv6": self.neighbor_ipv6,
            "cost": self.cost,
            "status": self.status,
        }


class Node:
    """
    路由器节点，拥有多个接口、LSDB 和路由表。
    """

    def __init__(self, node_id: str, name: Optional[str] = None, loopback_ipv4: Optional[str] = None, loopback_ipv6: Optional[str] = None):
        """
        初始化节点。

        参数:
            node_id: 节点唯一标识，如 "R1"
            name:    节点显示名称，默认与 node_id 相同
            loopback_ipv4: 回环 IPv4 地址（可选，如 "1.1.1.1/32"），用于标识节点
            loopback_ipv6: 回环 IPv6 地址（可选，如 "2001:db8::1/128"）
        """
        self.id = node_id
        self.name = name or node_id
        self.loopback_ipv4 = loopback_ipv4
        self.loopback_ipv6 = loopback_ipv6
        self.status = "up"  # "up" 或 "down"

        # 接口列表
        self.interfaces: List[Interface] = []

        # 链路状态数据库：key 为节点 ID（包括自身），value 为 LSA 字典
        # LSA 格式：{"node_id": str, "neighbors": [{"neighbor_id": str, "cost": int}, ...], "sequence": int}
        self.lsdb: Dict[str, Dict[str, Any]] = {}

        # 路由表：key 为目的前缀字符串（如 "1.1.1.1/32" 或 "2001:db8::1/128"），
        # value 为 RouteEntry 字典 {destination, next_hop, metric, interface, protocol}
        self.routing_table: Dict[str, Dict[str, Any]] = {}

        # 用于洪泛的序列号
        self._lsa_sequence = 0

    # ---------- 状态管理 ----------
    def set_status(self, status: str) -> None:
        """设置节点状态（up/down），同时影响所有接口状态"""
        self.status = status
        if status == "down":
            for intf in self.interfaces:
                intf.status = "down"
        else:
            # 恢复时，接口状态由邻居和链路决定，这里简单设为 up
            for intf in self.interfaces:
                intf.status = "up"

    def is_up(self) -> bool:
        return self.status == "up"

    # ---------- 接口管理 ----------
    def add_interface(self, intf: Interface) -> None:
        """添加一个接口"""
        self.interfaces.append(intf)

    def get_interface_to_neighbor(self, neighbor_id: str) -> Optional[Interface]:
        """查找连接到指定邻居的接口（假设最多一个）"""
        for intf in self.interfaces:
            if intf.neighbor_id == neighbor_id and intf.is_up():
                return intf
        return None

    def get_neighbors(self) -> List[str]:
        """返回当前所有直连且链路正常的邻居节点 ID 列表"""
        neighbors = []
        for intf in self.interfaces:
            if intf.neighbor_id and intf.is_up():
                neighbors.append(intf.neighbor_id)
        return neighbors

    def get_link_cost_to(self, neighbor_id: str) -> Optional[int]:
        """返回本节点到指定邻居的链路代价"""
        intf = self.get_interface_to_neighbor(neighbor_id)
        if intf:
            return intf.cost
        return None

    # ---------- LSDB 与 LSA 生成 ----------
    def generate_lsa(self) -> Dict[str, Any]:
        """
        生成本节点的链路状态通告（LSA）。
        包含所有 up 状态的邻居及链路代价。
        """
        self._lsa_sequence += 1
        neighbors = []
        for intf in self.interfaces:
            if intf.neighbor_id and intf.is_up():
                neighbors.append({
                    "neighbor_id": intf.neighbor_id,
                    "cost": intf.cost,
                })
        lsa = {
            "node_id": self.id,
            "neighbors": neighbors,
            "sequence": self._lsa_sequence,
        }
        # 更新自己的 LSDB
        self.lsdb[self.id] = lsa
        return lsa

    def update_lsdb(self, lsa: Dict[str, Any]) -> bool:
        """
        更新 LSDB。如果 LSA 比现有信息新（序列号更大），则更新并返回 True。
        否则返回 False。
        """
        node_id = lsa["node_id"]
        existing = self.lsdb.get(node_id)
        if existing is None or lsa["sequence"] > existing["sequence"]:
            self.lsdb[node_id] = lsa
            return True
        return False

    def remove_neighbor_from_lsa(self, neighbor_id: str) -> None:
        """
        从本地 LSDB 中移除与指定邻居相关的信息（当邻居故障时调用）。
        实际上应该由邻居节点发送新的 LSA 来更新，这里简化处理：
        直接从自己的 LSA 中删除该邻居（表示本地感知到链路断开）。
        """
        own_lsa = self.lsdb.get(self.id)
        if own_lsa:
            own_lsa["neighbors"] = [n for n in own_lsa["neighbors"] if n["neighbor_id"] != neighbor_id]
            own_lsa["sequence"] += 1
            self.lsdb[self.id] = own_lsa

    # ---------- 路由计算 ----------
    def run_dijkstra(self) -> None:
        """
        基于当前 LSDB 运行 Dijkstra 算法，计算到所有其他节点的最短路径。
        更新本节点的路由表。路由表目的为各节点的回环地址（如果存在），
        否则使用节点 ID 作为目的（但分组转发需要 IP，因此优先回环地址）。
        """
        if not self.is_up():
            self.routing_table.clear()
            return

        # 初始化距离和前驱
        dist: Dict[str, float] = {node_id: float('inf') for node_id in self.lsdb}
        prev: Dict[str, Optional[str]] = {node_id: None for node_id in self.lsdb}
        dist[self.id] = 0

        # 简单优先队列（使用列表，节点数量小，性能足够）
        unvisited = set(self.lsdb.keys())

        while unvisited:
            # 选择距离最小的未访问节点
            current = min(unvisited, key=lambda x: dist[x])
            unvisited.remove(current)

            if dist[current] == float('inf'):
                break

            # 遍历当前节点的邻居
            current_lsa = self.lsdb.get(current)
            if not current_lsa:
                continue
            for neighbor_info in current_lsa["neighbors"]:
                neighbor = neighbor_info["neighbor_id"]
                if neighbor not in unvisited:
                    continue
                cost = neighbor_info["cost"]
                new_dist = dist[current] + cost
                if new_dist < dist[neighbor]:
                    dist[neighbor] = new_dist
                    prev[neighbor] = current

        # 生成路由表
        self.routing_table.clear()
        for node_id, distance in dist.items():
            if node_id == self.id or distance == float('inf'):
                continue
            # 确定下一跳：从目的节点回溯到本节点的下一跳邻居
            next_hop_node = node_id
            while prev[next_hop_node] != self.id:
                next_hop_node = prev[next_hop_node]
                if next_hop_node is None:
                    break
            if next_hop_node is None:
                continue

            # 获取连接下一跳节点的接口和下一跳 IP
            intf = self.get_interface_to_neighbor(next_hop_node)
            if not intf:
                continue

            # 决定目的前缀：优先使用目的节点的回环地址
            if self._get_loopback_callback:
                dest_ipv4, dest_ipv6 = self._get_loopback_callback(node_id)
                if dest_ipv4:
                    dest_prefix = dest_ipv4  # 修改点：直接使用，不再拼接 /32
                    self._add_route(dest_prefix, intf.neighbor_ipv4 or intf.neighbor_ipv6, distance, intf.name, "OSPF")
                if dest_ipv6:
                    dest_prefix6 = dest_ipv6  # 修改点：直接使用，不再拼接 /128
                    self._add_route(dest_prefix6, intf.neighbor_ipv6 or intf.neighbor_ipv4, distance, intf.name, "OSPF")
            else:
                # 如果没有回调，则使用节点 ID 作为目的（仅用于展示）
                self._add_route(node_id, intf.neighbor_ipv4 or intf.neighbor_ipv6, distance, intf.name, "OSPF")

        # 添加直连路由（可选）：本节点接口的子网
        for intf in self.interfaces:
            if intf.is_up():
                if intf.ipv4:
                    ip, mask = intf.ipv4.split("/")
                    network = ipaddress.IPv4Network(f"{ip}/{mask}", strict=False)
                    self._add_route(str(network), None, 0, intf.name, "connected")
                if intf.ipv6:
                    ip6, mask6 = intf.ipv6.split("/")
                    network6 = ipaddress.IPv6Network(f"{ip6}/{mask6}", strict=False)
                    self._add_route(str(network6), None, 0, intf.name, "connected")

    def _add_route(self, destination: str, next_hop: Optional[str], metric: int, interface: str, protocol: str) -> None:
        """内部方法：添加或更新路由表项"""
        self.routing_table[destination] = {
            "destination": destination,
            "next_hop": next_hop or "direct",
            "metric": metric,
            "interface": interface,
            "protocol": protocol,
        }

    # 用于获取其他节点回环地址的回调（由模拟器注入）
    _get_loopback_callback = None

    @classmethod
    def set_loopback_callback(cls, callback) -> None:
        """设置获取节点回环地址的回调函数，参数为节点 ID，返回 (ipv4, ipv6) 元组"""
        cls._get_loopback_callback = callback

    # ---------- 路由查找 ----------
    def lookup_route(self, destination_ip: str) -> Optional[Dict[str, Any]]:
        """
        最长前缀匹配查找路由。
        参数 destination_ip 为 IPv4 或 IPv6 地址字符串（不带掩码）。
        返回路由表项字典，如果没有匹配返回 None。
        """
        best_match = None
        best_prefix_len = -1
        for dest_prefix, route in self.routing_table.items():
            if "/" not in dest_prefix:
                continue  # 忽略非前缀条目（如节点 ID）
            try:
                if ":" in destination_ip:
                    # IPv6
                    network = ipaddress.IPv6Network(dest_prefix, strict=False)
                    addr = ipaddress.IPv6Address(destination_ip)
                else:
                    # IPv4
                    network = ipaddress.IPv4Network(dest_prefix, strict=False)
                    addr = ipaddress.IPv4Address(destination_ip)
                if addr in network and network.prefixlen > best_prefix_len:
                    best_match = route
                    best_prefix_len = network.prefixlen
            except ValueError:
                continue
        return best_match

    # ---------- 信息导出 ----------
    def get_routing_table(self) -> List[Dict[str, Any]]:
        """返回路由表列表，按目的前缀排序"""
        return [route for _, route in sorted(self.routing_table.items())]

    def get_node_info(self) -> Dict[str, Any]:
        """返回节点基本信息（用于前端展示）"""
        return {
            "id": self.id,
            "name": self.name,
            "ipv4_interfaces": [intf.ipv4 for intf in self.interfaces if intf.ipv4],
            "ipv6_interfaces": [intf.ipv6 for intf in self.interfaces if intf.ipv6],
            "status": self.status,
        }

    def __repr__(self) -> str:
        return f"Node({self.id}, status={self.status}, neighbors={self.get_neighbors()})"