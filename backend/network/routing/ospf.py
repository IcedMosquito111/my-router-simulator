"""
简化的 OSPF 路由协议实现。
负责：
1. 为所有正常节点生成 LSA（链路状态通告）
2. 通过洪泛将 LSA 传播到全网
3. 每个节点基于 LSDB 运行 Dijkstra 算法计算最短路径，生成路由表
4. 当拓扑发生变化时，重新执行上述过程
"""

from typing import Dict, Any, List, Optional, Tuple
import logging

from ..node import Node
from ..topology import Topology

logger = logging.getLogger("router-simulator.ospf")


class OSPF:
    """
    OSPF 协议模拟器。
    它不处理报文传输延迟，而是采用同步方式一次性完成所有节点的 LSDB 同步和路由计算。
    对于 50 个节点的规模，计算时间在毫秒级，完全满足收敛时间要求。
    """

    def __init__(self, topology: Topology):
        """
        初始化 OSPF 协议实例。

        参数:
            topology: 网络拓扑对象，包含所有节点和链路
        """
        self.topology = topology
        # 设置节点获取回环地址的回调，使 Dijkstra 能生成到达其他节点回环地址的路由
        Node.set_loopback_callback(self._get_loopback)

    def _get_loopback(self, node_id: str) -> Tuple[Optional[str], Optional[str]]:
        """
        回调函数：根据节点 ID 返回其回环 IPv4 和 IPv6 地址。
        如果节点没有配置回环地址，则返回 (None, None)。
        """
        node = self.topology.get_node(node_id)
        if node:
            return node.loopback_ipv4, node.loopback_ipv6
        return None, None

    def sync_all(self) -> List[Dict[str, Any]]:
        """
        执行一次完整的 LSA 生成、洪泛和路由计算。
        所有处于 up 状态的节点都会参与。故障节点及其接口状态不影响其他节点的 LSA 生成。

        返回:
            所有节点的路由表更新信息列表，每个元素包含节点 ID 和路由表（符合 RoutingTableUpdate 模型）
        """
        # 只处理状态为 up 的节点
        up_nodes = {node_id: node for node_id, node in self.topology.nodes.items() if node.is_up()}

        if not up_nodes:
            logger.warning("没有处于 up 状态的节点，无法进行路由计算")
            return []

        # 清空所有 up 节点的 LSDB，重新开始构建（也可以保留旧数据并更新，但清空更简单）
        for node in up_nodes.values():
            node.lsdb.clear()

        # 第一步：每个 up 节点生成自己的 LSA（自动更新自己的 LSDB）
        for node_id, node in up_nodes.items():
            node.generate_lsa()
            logger.debug(f"节点 {node_id} 生成 LSA: {node.lsdb[node_id]}")

        # 第二步：洪泛 LSA。此处简化为全局广播，即每个节点的 LSA 被复制到所有其他 up 节点的 LSDB 中。
        # 注意：因为所有节点都生成了新的 LSA（序列号递增），直接复制不会引起冲突。
        # 为了模拟可靠洪泛，我们遍历所有节点对，使用 update_lsdb 确保最新。
        for src_id, src_node in up_nodes.items():
            lsa = src_node.lsdb[src_id]  # 自己的 LSA
            for dst_id, dst_node in up_nodes.items():
                if src_id == dst_id:
                    continue
                dst_node.update_lsdb(lsa)
                logger.debug(f"洪泛: {src_id} -> {dst_id}: {lsa}")

        # 第三步：每个 up 节点运行 Dijkstra 算法，生成路由表
        routing_updates = []
        for node_id, node in up_nodes.items():
            node.run_dijkstra()
            routing_updates.append({
                "node_id": node_id,
                "routes": node.get_routing_table(),
            })
            logger.info(f"节点 {node_id} 路由表更新完成，共 {len(node.routing_table)} 条路由")

        return routing_updates

    def handle_topology_change(self) -> List[Dict[str, Any]]:
        """
        处理拓扑变化（节点故障、链路故障或恢复）后重新计算全网路由。
        直接调用 sync_all()。
        """
        logger.info("检测到拓扑变化，重新计算全网路由...")
        return self.sync_all()

    def get_routing_table(self, node_id: str) -> Optional[List[Dict[str, Any]]]:
        """
        获取指定节点的当前路由表。
        如果节点不存在或 down，返回 None。
        """
        node = self.topology.get_node(node_id)
        if node and node.is_up():
            return node.get_routing_table()
        return None