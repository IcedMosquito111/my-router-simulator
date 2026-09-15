"""
路由器节点类：模拟网络中的一个路由器。
每个节点维护自己的接口、邻居关系、链路状态数据库（LSDB）和路由表。
支持 IPv4 和 IPv6 路由计算与分组转发。

状态一致性约定（重要）：
    1. 节点状态（self.status）只由故障注入/恢复显式设置。
    2. 接口状态是"派生量"，由 Topology.refresh_interface_states() 依据
       「链路状态 + 两端节点状态」统一刷新，节点自身不直接改接口状态。
    3. 路由表中 next_hop 的语义：
           "local"  本机地址（回环地址、本接口地址），表示本地投递
           "direct" 直连网段，下一跳为该出接口的对端节点
           其他      下一跳邻居的接口 IP
"""

from typing import List, Dict, Optional, Tuple, Any, Callable, Union
import heapq
import ipaddress

from .link import Link  # 仅用于类型提示，实际节点不直接持有链路对象

# 前缀类型（用于最长前缀匹配缓存）
Prefix = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]
# 回环地址解析器：输入节点 ID，返回 (ipv4, ipv6)
LoopbackResolver = Callable[[str], Tuple[Optional[str], Optional[str]]]


# 前缀解析缓存：同一条目在一次全网路由同步中会被解析 N 次（N=节点数），
# 缓存后把 O(N^2) 次字符串解析降为哈希查找，这是 50 节点能在毫秒级收敛的关键。
_PREFIX_CACHE: Dict[str, Tuple[str, Prefix]] = {}


def parse_prefix(address: str, force_host: bool = False) -> Tuple[str, Prefix]:
    """
    解析地址为 (规范化前缀字符串, 前缀对象)，带全局缓存。

    参数:
        address:    形如 "10.0.0.1/30" 或 "10.255.0.1" 的地址
        force_host: True 时强制为 /32（IPv4）或 /128（IPv6）主机路由
    """
    key = f"{address}|{int(force_host)}"
    cached = _PREFIX_CACHE.get(key)
    if cached is not None:
        return cached

    ip_part, mask = (address.split("/", 1) + [None])[:2] if "/" in address else (address, None)
    if ":" in ip_part:
        prefixlen = 128 if force_host else (int(mask) if mask is not None else 128)
        network: Prefix = ipaddress.IPv6Network(f"{ip_part}/{prefixlen}", strict=False)
    else:
        prefixlen = 32 if force_host else (int(mask) if mask is not None else 32)
        network = ipaddress.IPv4Network(f"{ip_part}/{prefixlen}", strict=False)

    result = (str(network), network)
    _PREFIX_CACHE[key] = result
    return result


def to_prefix(address: str, force_host: bool = False) -> str:
    """将地址规范化为前缀字符串（用于路由表 key），如 "10.0.0.0/30"、"10.255.0.1/32" """
    return parse_prefix(address, force_host)[0]


def ip_address(value: str) -> Union[ipaddress.IPv4Address, ipaddress.IPv6Address, None]:
    """解析 IP 地址字符串，非法时返回 None（不抛异常，便于路由查找快速失败）"""
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


class Interface:
    """
    节点的一个网络接口，连接到一个邻居路由器。
    接口包含本端和对端的 IP 地址、链路代价等信息。

    注意：status 是派生量，由 Topology.refresh_interface_states() 统一维护，
    不要在手写业务代码里随意设置，否则会出现"链路已断但接口仍 up"的不一致状态。
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

    def local_address(self, version: str = "ipv4") -> Optional[str]:
        """返回本端地址（不含掩码），无配置时返回 None"""
        addr = self.ipv4 if version == "ipv4" else self.ipv6
        if not addr:
            return None
        return addr.split("/")[0]

    def neighbor_address(self, version: str = "ipv4") -> Optional[str]:
        """返回对端地址（不含掩码），无配置时返回 None"""
        return self.neighbor_ipv4 if version == "ipv4" else self.neighbor_ipv6

    def get_prefix(self, version: str = "ipv4") -> Optional[Tuple[str, int]]:
        """提取本端地址的网络前缀，返回 (网络地址, 前缀长度) 或 None"""
        addr = self.ipv4 if version == "ipv4" else self.ipv6
        if not addr:
            return None
        ip, mask = addr.split("/") if "/" in addr else (addr, "32" if version == "ipv4" else "128")
        return ip, int(mask)

    def contains(self, address: str) -> bool:
        """判断给定地址是否属于本接口所在网段（用于校验下一跳是否可达）"""
        addr = ip_address(address)
        if addr is None:
            return False
        for value in (self.ipv4, self.ipv6):
            if not value:
                continue
            network = ipaddress.ip_network(value, strict=False)
            if addr.version == network.version and addr in network:
                return True
        return False

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

        # 最长前缀匹配用的前缀缓存（避免每次查找都重新解析字符串）
        self._route_prefixes: Dict[str, Prefix] = {}

        # 本机地址集合缓存（owns_ip 在每次逐跳转发时都会被调用）
        self._owned_cache: Optional[set] = None

        # 用于洪泛的序列号
        self._lsa_sequence = 0

    # ---------- 状态管理 ----------
    def set_status(self, status: str) -> None:
        """
        设置节点状态（up/down）。

        只维护节点自身的状态；接口状态由 Topology.refresh_interface_states()
        依据链路状态派生，节点故障期间路由表被清空（避免使用过期路由转发）。
        """
        if status not in ("up", "down"):
            raise ValueError(f"非法的节点状态: {status}")
        self.status = status
        if status == "down":
            self.clear_routes()

    def is_up(self) -> bool:
        return self.status == "up"

    # ---------- 接口管理 ----------
    def add_interface(self, intf: Interface) -> None:
        """添加一个接口"""
        self.interfaces.append(intf)
        self._owned_cache = None  # 接口变化后本机地址集合失效

    def get_interface_by_name(self, name: Optional[str]) -> Optional[Interface]:
        """按名称查找接口"""
        if not name:
            return None
        for intf in self.interfaces:
            if intf.name == name:
                return intf
        return None

    def get_interface_to_neighbor(self, neighbor_id: str) -> Optional[Interface]:
        """查找连接到指定邻居且处于 up 状态的接口（假设最多一个）"""
        for intf in self.interfaces:
            if intf.neighbor_id == neighbor_id and intf.is_up():
                return intf
        return None

    def get_neighbors(self) -> List[str]:
        """返回当前所有直连且接口正常的邻居节点 ID 列表"""
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

    # ---------- 地址管理 ----------
    def loopback_addresses(self) -> List[str]:
        """返回本节点的回环地址列表（带掩码，如 ["10.255.0.1/32"]）"""
        return [addr for addr in (self.loopback_ipv4, self.loopback_ipv6) if addr]

    def interface_addresses(self) -> List[str]:
        """返回本节点所有接口的本端地址（带掩码）"""
        addresses = []
        for intf in self.interfaces:
            addresses.extend([addr for addr in (intf.ipv4, intf.ipv6) if addr])
        return addresses

    def owned_addresses(self) -> set:
        """本节点所有地址（回环 + 接口）的解析结果集合，带缓存（接口在拓扑构建后不再变化）"""
        if self._owned_cache is None:
            owned = set()
            for value in self.loopback_addresses() + self.interface_addresses():
                parsed = ip_address(value.split("/")[0])
                if parsed is not None:
                    owned.add(parsed)
            self._owned_cache = owned
        return self._owned_cache

    def owns_ip(self, address: str) -> bool:
        """
        判断某个 IP 地址是否属于本节点（回环地址或任一接口地址）。
        用于实现"本地投递"：目的地址是本机时不需要转发，直接送达。
        """
        target = ip_address(address)
        if target is None:
            return False
        return target in self.owned_addresses()

    # ---------- LSDB 与 LSA 生成 ----------
    def generate_lsa(self) -> Dict[str, Any]:
        """
        生成本节点的链路状态通告（LSA）。
        包含所有接口状态为 up 的邻居及链路代价。
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
        更新 LSDB。只有序列号更大的 LSA 才会覆盖旧记录。
        返回 True 表示 LSDB 发生了变化。
        """
        node_id = lsa["node_id"]
        existing = self.lsdb.get(node_id)
        if existing is None or lsa["sequence"] > existing["sequence"]:
            self.lsdb[node_id] = lsa
            return True
        return False

    def remove_neighbor_from_lsa(self, neighbor_id: str) -> None:
        """从自己的 LSA 中移除某个邻居（本地感知到链路断开时使用）"""
        own_lsa = self.lsdb.get(self.id)
        if own_lsa:
            own_lsa["neighbors"] = [n for n in own_lsa["neighbors"] if n["neighbor_id"] != neighbor_id]
            own_lsa["sequence"] += 1
            self.lsdb[self.id] = own_lsa

    # ---------- 路由表维护 ----------
    def clear_routes(self) -> None:
        """清空路由表及其前缀缓存"""
        self.routing_table.clear()
        self._route_prefixes.clear()

    def add_route(self, destination: str, next_hop: Optional[str], metric: int, interface: str, protocol: str) -> None:
        """
        添加或更新一条路由表项。

        参数:
            destination: 目的前缀（如 "10.255.0.2/32"），内部会做规范化
            next_hop:    下一跳地址，或 "direct"/"local"
            metric:      度量值
            interface:   出接口名称
            protocol:    路由来源（OSPF/RIP/connected/local）
        """
        try:
            prefix, network = parse_prefix(destination)
        except ValueError:
            return  # 非法前缀直接忽略，避免脏数据进入路由表
        self._route_prefixes[prefix] = network
        self.routing_table[prefix] = {
            "destination": prefix,
            "next_hop": next_hop or "direct",
            "metric": metric,
            "interface": interface,
            "protocol": protocol,
        }

    # 兼容旧调用名
    _add_route = add_route

    def install_local_and_connected_routes(self) -> None:
        """
        安装本机路由与直连路由：
            - 回环地址、各接口本端地址 -> next_hop="local"（本地投递）
            - 接口所在网段            -> next_hop="direct"（从该接口直接发出）
        必须在安装协议学到的远端路由之前调用。
        """
        # 本机地址（回环）
        for addr in self.loopback_addresses():
            self.add_route(to_prefix(addr, force_host=True), "local", 0, "lo", "local")
        for intf in self.interfaces:
            for value in (intf.ipv4, intf.ipv6):
                if not value:
                    continue
                # 本接口地址本身也是本机地址（例如 10.0.0.1/30 -> 10.0.0.1/32）
                self.add_route(to_prefix(value, force_host=True), "local", 0, intf.name, "local")

        # 直连网段（只在接口可用时安装，接口 down 的网段不应出现在路由表里）
        for intf in self.interfaces:
            if not intf.is_up():
                continue
            for value in (intf.ipv4, intf.ipv6):
                if not value:
                    continue
                self.add_route(to_prefix(value), "direct", 0, intf.name, "connected")

    # ---------- 路由计算 ----------
    def run_dijkstra(self, loopback_of: Optional[LoopbackResolver] = None, protocol: str = "OSPF") -> None:
        """
        基于当前 LSDB 运行 Dijkstra（最小堆实现），计算到所有其他节点的最短路径。

        参数:
            loopback_of: 回环地址解析器，输入目的节点 ID 返回 (ipv4, ipv6)；
                         由路由协议实例注入，避免使用进程级全局状态。
            protocol:    写入路由表时标记的来源协议名（OSPF/RIP）

        说明:
            目的前缀使用目的节点的回环地址（/32 或 /128）；
            本机地址与直连网段由 install_local_and_connected_routes() 负责。
        """
        if not self.is_up():
            self.clear_routes()
            return

        # 初始化距离和前驱
        dist: Dict[str, float] = {node_id: float('inf') for node_id in self.lsdb}
        prev: Dict[str, Optional[str]] = {}
        dist[self.id] = 0.0

        heap: List[Tuple[float, str]] = [(0.0, self.id)]
        visited = set()
        while heap:
            current_dist, current = heapq.heappop(heap)
            if current in visited:
                continue
            visited.add(current)
            if current_dist > dist.get(current, float('inf')):
                continue
            current_lsa = self.lsdb.get(current)
            if not current_lsa:
                continue
            for neighbor_info in current_lsa.get("neighbors", []):
                neighbor = neighbor_info.get("neighbor_id")
                if neighbor is None or neighbor not in dist:
                    continue  # LSDB 中不存在的邻居（例如已故障节点）直接忽略
                new_dist = current_dist + neighbor_info.get("cost", 1)
                if new_dist < dist[neighbor]:
                    dist[neighbor] = new_dist
                    prev[neighbor] = current
                    heapq.heappush(heap, (new_dist, neighbor))

        # 重建路由表：先清空，再装本机/直连路由，最后装协议学到的远端路由
        self.clear_routes()
        self.install_local_and_connected_routes()

        for node_id, distance in dist.items():
            if node_id == self.id or distance == float('inf'):
                continue

            # 回溯前驱链，找到本节点的下一跳邻居
            next_hop_node = node_id
            hops = 0
            while prev.get(next_hop_node) is not None and prev[next_hop_node] != self.id:
                next_hop_node = prev[next_hop_node]
                hops += 1
                if hops > len(dist):  # 防御性检查：前驱链异常时不进入死循环
                    next_hop_node = None
                    break
            if next_hop_node is None or prev.get(next_hop_node) != self.id:
                continue  # 无有效前驱，跳过

            intf = self.get_interface_to_neighbor(next_hop_node)
            if not intf:
                continue  # 出接口不可用（链路/邻居故障），不安装该路由

            # 目的前缀：优先使用目的节点的回环地址
            dest_v4 = dest_v6 = None
            if loopback_of is not None:
                dest_v4, dest_v6 = loopback_of(node_id)
            if dest_v4:
                self.add_route(to_prefix(dest_v4, force_host=True), intf.neighbor_ipv4 or intf.neighbor_ipv6,
                               int(distance), intf.name, protocol)
            if dest_v6:
                self.add_route(to_prefix(dest_v6, force_host=True), intf.neighbor_ipv6 or intf.neighbor_ipv4,
                               int(distance), intf.name, protocol)
            if not dest_v4 and not dest_v6:
                # 没有回环地址时退化为用节点 ID 标识（仅用于展示，不能用于转发）
                self.add_route(node_id, intf.neighbor_ipv4 or intf.neighbor_ipv6,
                               int(distance), intf.name, protocol)

    # ---------- 路由查找 ----------
    def lookup_route(self, destination_ip: str) -> Optional[Dict[str, Any]]:
        """
        最长前缀匹配查找路由。
        参数 destination_ip 为 IPv4 或 IPv6 地址字符串（不带掩码）。
        返回路由表项字典，如果没有匹配返回 None。
        """
        target = ip_address(destination_ip)
        if target is None:
            return None
        best_route = None
        best_prefix_len = -1
        for prefix, network in self._route_prefixes.items():
            if network.version != target.version:
                continue
            if target in network and network.prefixlen > best_prefix_len:
                best_route = self.routing_table.get(prefix)
                best_prefix_len = network.prefixlen
        return best_route

    # ---------- 信息导出 ----------
    def get_routing_table(self) -> List[Dict[str, Any]]:
        """返回路由表列表（只含对外字段），按目的前缀排序"""
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
