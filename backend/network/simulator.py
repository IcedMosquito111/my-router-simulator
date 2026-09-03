"""
模拟器核心：管理拓扑、路由协议、分组转发和故障注入。
通过回调函数与 WebSocket 层交互，实现实时更新。
"""

import asyncio
import logging
import random
import time
from typing import Dict, Any, List, Optional, Callable, Awaitable
from datetime import datetime

from .topology import Topology
from .node import Node
from .link import Link
from .packet import Packet
from .routing.ospf import OSPF

logger = logging.getLogger("router-simulator.simulator")


class Simulator:
    """
    路由模拟器主类，封装了整个模拟环境。
    """

    def __init__(
        self,
        num_nodes: int = 50,
        topology_file: Optional[str] = None,
        callbacks: Optional[Dict[str, Callable[[dict], Awaitable[None]]]] = None,
    ):
        """
        初始化模拟器。

        参数:
            num_nodes: 随机生成拓扑时的节点数量，默认 50
            topology_file: 拓扑配置文件路径（可选，暂未实现）
            callbacks: 回调函数字典，包含以下键：
                - "topology_update": 拓扑更新回调，参数为拓扑快照字典
                - "routing_table_update": 路由表更新回调，参数为 RoutingTableUpdate 字典
                - "log_message": 日志消息回调，参数为日志字典
                - "packet_forwarded": 分组转发完成回调，参数为 PacketInfo 字典
        """
        self.callbacks = callbacks or {}
        self.topology = Topology()
        self.ospf: Optional[OSPF] = None

        # 接口名到对端节点 ID 的映射，用于分组转发时快速查找下一跳节点
        self.interface_to_neighbor: Dict[str, str] = {}

        # 如果提供了拓扑文件，则加载，否则随机生成
        if topology_file:
            self._load_topology_from_file(topology_file)
        else:
            self._generate_random_topology(num_nodes)

        # 初始化 OSPF 协议并计算初始路由
        self.ospf = OSPF(self.topology)
        self._initial_route_sync()

        logger.info(f"模拟器初始化完成，节点数: {len(self.topology.nodes)}, 链路数: {len(self.topology.links)}")

    # ---------- 拓扑生成 ----------
    def _generate_random_topology(self, num_nodes: int) -> None:
        """
        随机生成一个连通拓扑。
        步骤：
        1. 创建 num_nodes 个节点，分配回环地址。
        2. 首先构建一个环形连接保证连通性。
        3. 随机添加额外链路（控制平均度数）。
        """
        # 创建节点
        for i in range(1, num_nodes + 1):
            node_id = f"R{i}"
            # 分配回环地址，IPv4 使用 10.255.0.0/16 网段，IPv6 使用 2001:db8:ffff::/64 前缀
            loopback_ipv4 = f"10.255.{i // 256}.{i % 256}/32"
            loopback_ipv6 = f"2001:db8:ffff::{i:x}/128"
            node = Node(node_id=node_id, loopback_ipv4=loopback_ipv4, loopback_ipv6=loopback_ipv6)
            self.topology.add_node(node)

        node_ids = list(self.topology.nodes.keys())

        # 构建环形，确保连通性
        for i in range(num_nodes):
            node1 = node_ids[i]
            node2 = node_ids[(i + 1) % num_nodes]
            self.topology.connect_nodes(node1, node2, cost=random.randint(1, 10), bandwidth=1000, delay=random.randint(1, 5))
            logger.debug(f"连接 {node1} - {node2}")

        # 随机添加额外链路，平均每个节点再增加约 1 条边
        extra_edges = num_nodes // 2
        attempts = 0
        while extra_edges > 0 and attempts < num_nodes * 2:
            attempts += 1
            a, b = random.sample(node_ids, 2)
            if self.topology.get_link_between(a, b) is None:
                self.topology.connect_nodes(a, b, cost=random.randint(1, 10), bandwidth=1000, delay=random.randint(1, 5))
                extra_edges -= 1
                logger.debug(f"随机连接 {a} - {b}")

    def _load_topology_from_file(self, filepath: str) -> None:
        """从 JSON 文件加载拓扑（预留功能）"""
        raise NotImplementedError("拓扑文件加载功能尚未实现，请使用随机生成")

    def _initial_route_sync(self) -> None:
        """初始路由同步：构建接口映射，运行 OSPF 计算，推送初始路由表"""
        self._build_interface_mapping()
        if self.ospf:
            routing_updates = self.ospf.sync_all()
            # 推送所有节点的路由表
            for update in routing_updates:
                asyncio.create_task(self._push_callback("routing_table_update", update))

    def _build_interface_mapping(self) -> None:
        """根据拓扑链路构建接口名到对端节点 ID 的映射"""
        self.interface_to_neighbor.clear()
        for link in self.topology.links:
            if link.source_interface:
                self.interface_to_neighbor[link.source_interface] = link.target_id
            if link.target_interface:
                self.interface_to_neighbor[link.target_interface] = link.source_id

    # ---------- 回调推送 ----------
    async def _push_callback(self, callback_name: str, data: Any) -> None:
        """异步调用指定的回调函数，并捕获异常"""
        callback = self.callbacks.get(callback_name)
        if callback:
            try:
                await callback(data)
            except Exception as e:
                logger.error(f"回调 {callback_name} 执行失败: {e}")

    async def _log(self, level: str, message: str) -> None:
        """记录日志并通过回调推送"""
        log_data = {
            "level": level,
            "message": message,
            "timestamp": datetime.now().isoformat(),
        }
        await self._push_callback("log_message", log_data)

    # ---------- 命令处理 ----------
    async def handle_command(self, command: Dict[str, Any]) -> Dict[str, Any]:
        """
        处理前端命令。命令格式：
        {
            "action": "inject_fault" | "recover_fault" | "send_packet" | "get_topology" | "get_routing_table" | ...,
            "params": {...}
        }
        返回命令执行结果（字典），也会通过回调推送更新。
        """
        action = command.get("action")
        params = command.get("params", {})

        if action == "get_topology":
            return {
                "success": True,
                "message": "拓扑获取成功",
                "data": self.topology.get_topology_snapshot(),
            }
        elif action == "get_routing_table":
            node_id = params.get("node_id")
            node = self.topology.get_node(node_id)
            if node and node.is_up():
                return {
                    "success": True,
                    "message": f"节点 {node_id} 路由表获取成功",
                    "data": {
                        "node_id": node_id,
                        "routes": node.get_routing_table(),
                    },
                }
            else:
                return {"success": False, "message": f"节点 {node_id} 不存在或已故障"}
        elif action == "inject_fault":
            return await self._inject_fault(params)
        elif action == "recover_fault":
            return await self._recover_fault(params)
        elif action == "send_packet":
            return await self._send_packet(params)
        else:
            return {"success": False, "message": f"未知命令: {action}"}

    # ---------- 故障注入与恢复 ----------
    async def _inject_fault(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """注入故障：节点 down 或链路 down"""
        fault_type = params.get("type")
        if fault_type == "node":
            node_id = params.get("id")
            node = self.topology.get_node(node_id)
            if not node:
                return {"success": False, "message": f"节点 {node_id} 不存在"}
            if not node.is_up():
                return {"success": False, "message": f"节点 {node_id} 已经处于故障状态"}
            # 设置节点故障，其所有接口状态自动 down
            node.set_status("down")
            await self._log("warning", f"节点 {node_id} 发生故障")
            await self._push_callback("topology_update", self.topology.get_topology_snapshot())
            # 重新计算路由
            await self._recalculate_routes()
            return {"success": True, "message": f"节点 {node_id} 故障注入成功", "data": {"node_id": node_id}}
        elif fault_type == "link":
            src = params.get("source")
            dst = params.get("target")
            link = self.topology.get_link_between(src, dst)
            if not link:
                return {"success": False, "message": f"链路 {src}-{dst} 不存在"}
            if not link.is_up():
                return {"success": False, "message": f"链路 {src}-{dst} 已经处于故障状态"}
            # 设置链路 down，并同步更新两端节点的接口状态
            link.set_down()
            # 找到两端节点，将对应接口状态设为 down
            src_node = self.topology.get_node(src)
            dst_node = self.topology.get_node(dst)
            if src_node:
                for intf in src_node.interfaces:
                    if intf.neighbor_id == dst:
                        intf.status = "down"
            if dst_node:
                for intf in dst_node.interfaces:
                    if intf.neighbor_id == src:
                        intf.status = "down"
            await self._log("warning", f"链路 {src}-{dst} 发生故障")
            await self._push_callback("topology_update", self.topology.get_topology_snapshot())
            await self._recalculate_routes()
            return {"success": True, "message": f"链路 {src}-{dst} 故障注入成功", "data": {"source": src, "target": dst}}
        else:
            return {"success": False, "message": f"不支持的故障类型: {fault_type}"}

    async def _recover_fault(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """恢复故障：节点或链路恢复 up"""
        fault_type = params.get("type")
        if fault_type == "node":
            node_id = params.get("id")
            node = self.topology.get_node(node_id)
            if not node:
                return {"success": False, "message": f"节点 {node_id} 不存在"}
            if node.is_up():
                return {"success": False, "message": f"节点 {node_id} 已经处于正常状态"}
            node.set_status("up")
            await self._log("info", f"节点 {node_id} 恢复")
            await self._push_callback("topology_update", self.topology.get_topology_snapshot())
            await self._recalculate_routes()
            return {"success": True, "message": f"节点 {node_id} 恢复成功", "data": {"node_id": node_id}}
        elif fault_type == "link":
            src = params.get("source")
            dst = params.get("target")
            link = self.topology.get_link_between(src, dst)
            if not link:
                return {"success": False, "message": f"链路 {src}-{dst} 不存在"}
            if link.is_up():
                return {"success": False, "message": f"链路 {src}-{dst} 已经处于正常状态"}
            link.set_up()
            # 恢复两端节点的接口状态
            src_node = self.topology.get_node(src)
            dst_node = self.topology.get_node(dst)
            if src_node:
                for intf in src_node.interfaces:
                    if intf.neighbor_id == dst:
                        intf.status = "up"
            if dst_node:
                for intf in dst_node.interfaces:
                    if intf.neighbor_id == src:
                        intf.status = "up"
            await self._log("info", f"链路 {src}-{dst} 恢复")
            await self._push_callback("topology_update", self.topology.get_topology_snapshot())
            await self._recalculate_routes()
            return {"success": True, "message": f"链路 {src}-{dst} 恢复成功", "data": {"source": src, "target": dst}}
        else:
            return {"success": False, "message": f"不支持的故障类型: {fault_type}"}

    async def _recalculate_routes(self) -> None:
        """重新计算全网路由，并推送所有节点的路由表更新，同时记录耗时"""
        if not self.ospf:
            return
        start_time = time.time()
        routing_updates = self.ospf.handle_topology_change()
        elapsed_ms = (time.time() - start_time) * 1000  # 毫秒
        await self._log("info", f"路由重计算完成，耗时 {elapsed_ms:.2f} 毫秒")
        for update in routing_updates:
            await self._push_callback("routing_table_update", update)

    # ---------- 分组转发 ----------
    async def _send_packet(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        模拟发送一个 IP 分组。
        params 需包含:
            - src_node: 源节点 ID
            - dst_ip: 目的 IP 地址（IPv4 或 IPv6）
        可选:
            - protocol: "IPv4" 或 "IPv6"，若不指定则根据 dst_ip 自动判断
        """
        src_node_id = params.get("src_node")
        dst_ip = params.get("dst_ip")
        if not src_node_id or not dst_ip:
            return {"success": False, "message": "参数缺失，需要 src_node 和 dst_ip"}

        src_node = self.topology.get_node(src_node_id)
        if not src_node or not src_node.is_up():
            return {"success": False, "message": f"源节点 {src_node_id} 不存在或已故障"}

        # 确定源 IP：优先使用源节点的回环地址（与协议版本匹配）
        protocol = params.get("protocol")
        if not protocol:
            protocol = "IPv6" if ":" in dst_ip else "IPv4"
        if protocol == "IPv4":
            src_ip = src_node.loopback_ipv4.split("/")[0] if src_node.loopback_ipv4 else None
        else:
            src_ip = src_node.loopback_ipv6.split("/")[0] if src_node.loopback_ipv6 else None
        if not src_ip:
            return {"success": False, "message": f"源节点 {src_node_id} 缺少对应的回环地址"}

        # 创建分组
        packet = Packet(src_ip=src_ip, dst_ip=dst_ip, protocol=protocol, ttl=64)
        await self._log("info", f"开始转发分组: {src_ip} -> {dst_ip} ({protocol})")

        # 执行转发过程
        current_node = src_node
        packet.record_hop(current_node.id)

        max_hops = 100  # 防止环路
        while packet.status == "in_transit" and packet.ttl > 0 and max_hops > 0:
            max_hops -= 1

            # 查找路由
            route = current_node.lookup_route(packet.dst_ip)
            if not route:
                packet.mark_dropped()
                await self._log("error", f"节点 {current_node.id} 无到达 {dst_ip} 的路由，分组被丢弃")
                break

            # 获取下一跳 IP 和出接口
            next_hop_ip = route.get("next_hop")
            out_interface = route.get("interface")
            if not next_hop_ip or next_hop_ip == "direct" or not out_interface:
                packet.mark_dropped()
                await self._log("error", f"节点 {current_node.id} 路由异常（下一跳为直连），分组被丢弃")
                break

            # 查找下一跳节点 ID
            next_hop_node_id = self.interface_to_neighbor.get(out_interface)
            if not next_hop_node_id:
                packet.mark_dropped()
                await self._log("error", f"无法根据接口 {out_interface} 确定下一跳节点，分组被丢弃")
                break

            next_hop_node = self.topology.get_node(next_hop_node_id)
            if not next_hop_node or not next_hop_node.is_up():
                packet.mark_dropped()
                await self._log("error", f"下一跳节点 {next_hop_node_id} 不存在或故障，分组被丢弃")
                break

            # 模拟传输延迟
            link = self.topology.get_link_between(current_node.id, next_hop_node_id)
            if link:
                await asyncio.sleep(link.delay / 1000.0)  # 延迟毫秒转秒

            # 转发分组
            packet.decrement_ttl()
            current_node = next_hop_node
            packet.record_hop(current_node.id)

            # 检查是否到达目的节点（目的 IP 等于当前节点的回环地址）
            if packet.dst_ip == (current_node.loopback_ipv4.split("/")[0] if current_node.loopback_ipv4 else None) or \
               packet.dst_ip == (current_node.loopback_ipv6.split("/")[0] if current_node.loopback_ipv6 else None):
                packet.mark_delivered()
                await self._log("info", f"分组成功到达目的节点 {current_node.id}")
                break

            if packet.is_expired():
                packet.mark_dropped()
                await self._log("warning", f"分组 TTL 耗尽，在节点 {current_node.id} 被丢弃")
                break

        # 推送转发结果
        packet_info = packet.to_dict()
        await self._push_callback("packet_forwarded", packet_info)

        return {
            "success": packet.status == "delivered",
            "message": f"分组转发完成，状态: {packet.status}",
            "data": packet_info,
        }

    # ---------- 获取拓扑快照 ----------
    def get_topology_snapshot(self) -> Dict[str, Any]:
        """返回当前拓扑快照（供 REST API 使用）"""
        return self.topology.get_topology_snapshot()