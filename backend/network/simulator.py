"""
模拟器核心：管理拓扑、路由协议、分组转发和故障注入。
通过回调函数与 WebSocket 层交互，实现实时更新。

状态一致性约定（重要）：
    接口状态是派生量，唯一真相是「链路状态 + 两端节点状态」。
    因此任何故障注入/恢复之后都必须调用 Topology.refresh_interface_states()，
    分组转发时也必须校验出接口与链路是否可用，
    否则会出现"报文穿越已断链路却报告送达"的仿真失真。

生命周期约定：
    __init__ 只做纯计算（不创建异步任务），事件循环就绪后由 start() 推送初始状态。
"""

import asyncio
import json
import logging
import random
import time
from collections import deque
from typing import Dict, Any, List, Optional, Callable, Awaitable, Tuple
from datetime import datetime

from models import (
    Command,
    SendPacketParams,
    ValidationError,
    UnknownActionError,
    format_validation_error,
    parse_command_params,
)

from .topology import Topology
from .node import Node
from .link import Link
from .packet import Packet
from .routing.ospf import OSPF

logger = logging.getLogger("router-simulator.simulator")

DEFAULT_TTL = 64
METRIC_HISTORY_SIZE = 50  # 保留最近多少次收敛耗时，用于报告与监控


class Simulator:
    """
    路由模拟器主类，封装了整个模拟环境。
    """

    def __init__(
        self,
        num_nodes: int = 50,
        topology_file: Optional[str] = None,
        callbacks: Optional[Dict[str, Callable[[dict], Awaitable[None]]]] = None,
        seed: Optional[int] = None,
    ):
        """
        初始化模拟器。

        参数:
            num_nodes: 随机生成拓扑时的节点数量，默认 50
            topology_file: 拓扑配置文件路径（JSON），提供时忽略 num_nodes
            callbacks: 回调函数字典，包含以下键：
                - "topology_update": 拓扑更新回调，参数为拓扑快照字典
                - "routing_table_update": 路由表更新回调，参数为 RoutingTableUpdate 字典
                - "log_message": 日志消息回调，参数为日志字典
                - "packet_forwarded": 分组转发完成回调，参数为 PacketInfo 字典
            seed: 随机拓扑的随机种子；固定种子可让拓扑与实验可复现
        """
        self.callbacks = callbacks or {}
        self.topology = Topology()
        self.seed = seed

        # 当前路由协议实例（OSPF；协议名写入路由表便于前端区分来源）
        self.ospf: Optional[OSPF] = None
        self.protocol_name = "OSPF"

        # 接口名到对端节点 ID 的映射，用于分组转发时快速查找下一跳节点
        self.interface_to_neighbor: Dict[str, str] = {}

        # 故障注入/恢复是读-改-写操作，用锁串行化，避免多客户端并发注入时状态交错
        self._state_lock = asyncio.Lock()

        # 初始路由表更新（start() 时推送，避免在 __init__ 里创建异步任务）
        self._initial_updates: List[Dict[str, Any]] = []
        self._started = False

        # 运行指标：用于验证"故障后重计算时间不超过 2 秒"这一指标
        self.metrics: Dict[str, Any] = {
            "started_at": None,
            "topology_source": "file" if topology_file else "random",
            "node_count": 0,
            "link_count": 0,
            "faults_injected": 0,
            "faults_recovered": 0,
            "route_recalculations": 0,
            "packets_sent": 0,
            "packets_delivered": 0,
            "packets_dropped": 0,
            "last_convergence_ms": None,
            "last_total_ms": None,
            "max_convergence_ms": 0.0,
            "convergence_history_ms": deque(maxlen=METRIC_HISTORY_SIZE),
        }

        # 生成或加载拓扑
        if topology_file:
            self._load_topology_from_file(topology_file)
        else:
            self._generate_random_topology(num_nodes)

        # 依据链路与节点状态初始化接口状态
        self.topology.refresh_interface_states()

        # 初始化路由协议并计算初始路由（纯计算，不涉及事件循环）
        self.ospf = OSPF(self.topology)
        self._initial_route_sync()

        self.metrics["node_count"] = len(self.topology.nodes)
        self.metrics["link_count"] = len(self.topology.links)

        logger.info(
            "模拟器初始化完成，节点数: %d, 链路数: %d, 随机种子: %s",
            len(self.topology.nodes), len(self.topology.links), seed,
        )

    # ---------- 生命周期 ----------
    async def start(self) -> None:
        """
        在事件循环就绪后启动模拟器：推送初始拓扑快照与所有节点的路由表。
        必须由 FastAPI lifespan 或测试代码显式 await。
        """
        if self._started:
            return
        self._started = True
        self.metrics["started_at"] = datetime.now().isoformat()

        await self._push_callback("topology_update", self.topology.get_topology_snapshot())
        for update in self._initial_updates:
            await self._push_callback("routing_table_update", update)

        logger.info("模拟器已启动，已推送 %d 个节点的初始路由表", len(self._initial_updates))

    # ---------- 拓扑生成与加载 ----------
    def _generate_random_topology(self, num_nodes: int) -> None:
        """
        随机生成一个连通拓扑（可复现：指定 seed 后每次生成的拓扑一致）。
        步骤：
        1. 创建 num_nodes 个节点，分配回环地址。
        2. 首先构建一个环形连接保证连通性。
        3. 随机添加额外链路（控制平均度数）。
        """
        rng = random.Random(self.seed)

        if num_nodes < 2:
            raise ValueError("节点数至少为 2")

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
            self.topology.connect_nodes(node1, node2, cost=rng.randint(1, 10), bandwidth=1000, delay=rng.randint(1, 5))

        # 随机添加额外链路，平均每个节点再增加约 1 条边
        extra_edges = num_nodes // 2
        attempts = 0
        while extra_edges > 0 and attempts < num_nodes * 2:
            attempts += 1
            a, b = rng.sample(node_ids, 2)
            if self.topology.get_link_between(a, b) is None:
                self.topology.connect_nodes(a, b, cost=rng.randint(1, 10), bandwidth=1000, delay=rng.randint(1, 5))
                extra_edges -= 1

    def _load_topology_from_file(self, filepath: str) -> None:
        """
        从 JSON 文件加载拓扑，便于用固定拓扑（例如真实的 50 节点拓扑）复现实验。

        文件格式：
        {
          "nodes": [{"id": "R1", "name": "R1", "loopback_ipv4": "10.255.0.1/32",
                     "loopback_ipv6": "2001:db8:ffff::1/128"}],
          "links": [{"source": "R1", "target": "R2", "cost": 4, "bandwidth": 1000, "delay": 2}]
        }
        """
        with open(filepath, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        nodes = data.get("nodes") or []
        links = data.get("links") or []
        if not nodes:
            raise ValueError(f"拓扑文件 {filepath} 中没有节点定义")

        for spec in nodes:
            node = Node(
                node_id=str(spec["id"]),
                name=spec.get("name"),
                loopback_ipv4=spec.get("loopback_ipv4"),
                loopback_ipv6=spec.get("loopback_ipv6"),
            )
            self.topology.add_node(node)

        for spec in links:
            self.topology.connect_nodes(
                str(spec["source"]),
                str(spec["target"]),
                cost=int(spec.get("cost", 1)),
                bandwidth=int(spec.get("bandwidth", 1000)),
                delay=int(spec.get("delay", 1)),
            )

        logger.info("已从 %s 加载拓扑：%d 节点 / %d 链路", filepath, len(nodes), len(links))

    def export_topology_config(self) -> Dict[str, Any]:
        """导出当前拓扑配置（与 _load_topology_from_file 的格式一致，可用于固化实验拓扑）"""
        return {
            "nodes": [
                {
                    "id": node.id,
                    "name": node.name,
                    "loopback_ipv4": node.loopback_ipv4,
                    "loopback_ipv6": node.loopback_ipv6,
                }
                for node in self.topology.nodes.values()
            ],
            "links": [
                {
                    "source": link.source_id,
                    "target": link.target_id,
                    "cost": link.cost,
                    "bandwidth": link.bandwidth,
                    "delay": link.delay,
                }
                for link in self.topology.links
            ],
        }

    def _initial_route_sync(self) -> None:
        """初始路由同步：构建接口映射、运行路由协议计算，缓存结果待 start() 推送"""
        self._build_interface_mapping()
        if self.ospf:
            self._initial_updates = self.ospf.sync_all()

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
            "action": "inject_fault" | "recover_fault" | "send_packet" | "get_topology" | "get_routing_table",
            "params": {...}
        }
        返回命令执行结果（字典），也会通过回调推送更新。

        任何非法输入都会转成 {"success": False, ...} 的错误响应，
        绝不向上抛出异常（否则 WebSocket 连接会被直接断开）。
        """
        try:
            cmd = Command.model_validate(command)
        except ValidationError as exc:
            return {"success": False, "message": f"命令格式非法：{format_validation_error(exc)}"}

        action = cmd.action
        try:
            params = parse_command_params(action, cmd.params)
        except UnknownActionError as exc:
            return {"success": False, "message": str(exc)}
        except ValidationError as exc:
            return {"success": False, "message": f"参数校验失败：{format_validation_error(exc)}"}

        try:
            if action == "get_topology":
                return {
                    "success": True,
                    "message": "拓扑获取成功",
                    "data": self.topology.get_topology_snapshot(),
                }
            if action == "get_routing_table":
                node = self.topology.get_node(params.node_id)
                if node and node.is_up():
                    return {
                        "success": True,
                        "message": f"节点 {params.node_id} 路由表获取成功",
                        "data": {"node_id": params.node_id, "routes": node.get_routing_table()},
                    }
                return {"success": False, "message": f"节点 {params.node_id} 不存在或已故障"}
            if action == "inject_fault":
                return await self._inject_fault(params)
            if action == "recover_fault":
                return await self._recover_fault(params)
            if action == "send_packet":
                return await self._send_packet(params)
            return {"success": False, "message": f"未实现的命令: {action}"}
        except Exception as exc:  # 兜底：内部异常也不能打断客户端连接
            logger.exception("命令 %s 处理异常", action)
            return {"success": False, "message": f"服务端处理异常: {type(exc).__name__}: {exc}"}

    # ---------- 故障注入与恢复 ----------
    async def _inject_fault(self, params) -> Dict[str, Any]:
        """注入故障入口：加锁串行执行"""
        async with self._state_lock:
            return await self._inject_fault_impl(params)

    async def _inject_fault_impl(self, params) -> Dict[str, Any]:
        """注入故障：节点 down 或链路 down"""
        if params.type == "node":
            node_id = params.id
            node = self.topology.get_node(node_id)
            if not node:
                return {"success": False, "message": f"节点 {node_id} 不存在"}
            if not node.is_up():
                return {"success": False, "message": f"节点 {node_id} 已经处于故障状态"}

            node.set_status("down")
            # 接口状态由「链路 + 两端节点状态」派生：故障节点相连的接口/链路必须一起置为不可用
            self.topology.refresh_interface_states()
            await self._log("warning", f"节点 {node_id} 发生故障")
            await self._push_callback("topology_update", self.topology.get_topology_snapshot())
            timing = await self._recalculate_routes()
            self.metrics["faults_injected"] += 1
            return {
                "success": True,
                "message": f"节点 {node_id} 故障注入成功",
                "data": {
                    "operation": "fault",
                    "fault_type": "node",
                    "fault_target": node_id,
                    "node_id": node_id,
                    **timing,
                },
            }

        # 链路故障
        src, dst = params.source, params.target
        link = self.topology.get_link_between(src, dst)
        if not link:
            return {"success": False, "message": f"链路 {src}-{dst} 不存在"}
        if not link.is_up():
            return {"success": False, "message": f"链路 {src}-{dst} 已经处于故障状态"}

        link.set_down()
        self.topology.refresh_interface_states()
        await self._log("warning", f"链路 {src}-{dst} 发生故障")
        await self._push_callback("topology_update", self.topology.get_topology_snapshot())
        timing = await self._recalculate_routes()
        self.metrics["faults_injected"] += 1
        return {
            "success": True,
            "message": f"链路 {src}-{dst} 故障注入成功",
            "data": {
                "operation": "fault",
                "fault_type": "link",
                "fault_target": f"{src}-{dst}",
                "source": src,
                "target": dst,
                **timing,
            },
        }

    async def _recover_fault(self, params) -> Dict[str, Any]:
        """恢复故障入口：加锁串行执行"""
        async with self._state_lock:
            return await self._recover_fault_impl(params)

    async def _recover_fault_impl(self, params) -> Dict[str, Any]:
        """恢复故障：节点或链路恢复 up"""
        if params.type == "node":
            node_id = params.id
            node = self.topology.get_node(node_id)
            if not node:
                return {"success": False, "message": f"节点 {node_id} 不存在"}
            if node.is_up():
                return {"success": False, "message": f"节点 {node_id} 已经处于正常状态"}

            node.set_status("up")
            # 注意：不要无条件把该节点的所有接口置 up——链路本身可能仍是 down 的，
            # 恢复动作只是让节点重新参与计算，接口可用性由拓扑统一派生。
            self.topology.refresh_interface_states()
            await self._log("info", f"节点 {node_id} 恢复")
            await self._push_callback("topology_update", self.topology.get_topology_snapshot())
            timing = await self._recalculate_routes()
            self.metrics["faults_recovered"] += 1
            return {
                "success": True,
                "message": f"节点 {node_id} 恢复成功",
                "data": {"node_id": node_id, **timing},
            }

        src, dst = params.source, params.target
        link = self.topology.get_link_between(src, dst)
        if not link:
            return {"success": False, "message": f"链路 {src}-{dst} 不存在"}
        if link.is_up():
            return {"success": False, "message": f"链路 {src}-{dst} 已经处于正常状态"}

        link.set_up()
        self.topology.refresh_interface_states()
        await self._log("info", f"链路 {src}-{dst} 恢复")
        await self._push_callback("topology_update", self.topology.get_topology_snapshot())
        timing = await self._recalculate_routes()
        self.metrics["faults_recovered"] += 1
        return {
            "success": True,
            "message": f"链路 {src}-{dst} 恢复成功",
            "data": {"source": src, "target": dst, **timing},
        }

    async def _recalculate_routes(self) -> Dict[str, Any]:
        """
        重新计算全网路由并推送所有节点的路由表更新，同时记录耗时。

        返回:
            {"convergence_ms": 路由计算耗时, "total_ms": 含推送的总耗时, "node_count": 参与计算的节点数}
        说明:
            convergence_ms 只统计"路由重新计算"本身（算法耗时），这是需求中
            "重新计算时间不超过 2 秒"的度量口径；total_ms 额外包含向所有客户端
            推送 N 张路由表的耗时，便于评估端到端体验。
        """
        if not self.ospf:
            return {"convergence_ms": 0.0, "total_ms": 0.0, "node_count": 0}

        start_time = time.perf_counter()
        routing_updates = self.ospf.handle_topology_change()
        convergence_ms = (time.perf_counter() - start_time) * 1000

        for update in routing_updates:
            await self._push_callback("routing_table_update", update)

        total_ms = (time.perf_counter() - start_time) * 1000
        self._record_convergence(convergence_ms, total_ms, len(routing_updates))

        await self._log(
            "info",
            f"路由重计算完成：{len(routing_updates)} 个节点，计算 {convergence_ms:.2f} ms"
            f"（含推送 {total_ms:.2f} ms）",
        )
        return {
            "convergence_ms": round(convergence_ms, 2),
            "total_ms": round(total_ms, 2),
            "node_count": len(routing_updates),
        }

    def _record_convergence(self, convergence_ms: float, total_ms: float, node_count: int) -> None:
        """记录收敛耗时指标"""
        self.metrics["route_recalculations"] += 1
        self.metrics["last_convergence_ms"] = round(convergence_ms, 2)
        self.metrics["last_total_ms"] = round(total_ms, 2)
        self.metrics["last_node_count"] = node_count
        self.metrics["max_convergence_ms"] = max(self.metrics["max_convergence_ms"], round(convergence_ms, 2))
        self.metrics["convergence_history_ms"].append(round(convergence_ms, 2))

    def get_metrics(self) -> Dict[str, Any]:
        """导出运行指标（供 /api/metrics 使用）"""
        data = dict(self.metrics)
        history = list(data.pop("convergence_history_ms", []))
        data["convergence_samples"] = len(history)
        data["avg_convergence_ms"] = round(sum(history) / len(history), 2) if history else None
        data["recent_convergence_ms"] = history[-10:]
        return data

    # ---------- 分组转发 ----------
    async def _send_packet(self, params: SendPacketParams) -> Dict[str, Any]:
        """
        模拟发送一个 IP 分组（逐跳转发）。

        转发规则（贴近真实路由器行为）：
            - 目的地址属于本机（回环或自身接口地址）-> 本地投递，直接送达；
            - 路由 next_hop = "direct"（直连网段）-> 从出接口直接发给对端节点；
            - 其他 -> 按下一跳地址转发，并校验下一位在出接口网段内；
            - 每一步都校验出接口与链路可用性，链路故障则明确丢弃（不会"穿过"死链路）。
        """
        src_node_id, dst_ip = params.src_node, params.dst_ip

        src_node = self.topology.get_node(src_node_id)
        if not src_node:
            return {"success": False, "message": f"源节点 {src_node_id} 不存在"}
        if not src_node.is_up():
            return {"success": False, "message": f"源节点 {src_node_id} 已故障"}

        protocol = params.protocol or ("IPv6" if ":" in dst_ip else "IPv4")
        src_ip = src_node.loopback_ipv6 if protocol == "IPv6" else src_node.loopback_ipv4
        if not src_ip:
            return {"success": False, "message": f"源节点 {src_node_id} 缺少 {protocol} 回环地址"}
        src_ip = src_ip.split("/")[0]

        packet = Packet(src_ip=src_ip, dst_ip=dst_ip, protocol=protocol, ttl=params.ttl)
        packet.record_hop(src_node.id)
        self.metrics["packets_sent"] += 1
        await self._log("info", f"开始转发分组: {src_ip} -> {dst_ip} ({protocol}, ttl={params.ttl})")

        current_node = src_node
        is_origin = True  # 源节点扮演"主机"，发出去的报文不递减 TTL
        drop_reason: Optional[str] = None
        max_hops = len(self.topology.nodes) + 5  # 防御性上限：TTL 之外再兜一层

        for _ in range(max_hops):
            # 1) 目的地址属于当前节点 -> 本地投递（含源节点 ping 自己的场景）
            if current_node.owns_ip(packet.dst_ip):
                packet.mark_delivered()
                break

            # 2) TTL 处理：报文到达"中转路由器"时递减，减到 0 即丢弃
            #    （源节点是发送方、目的节点是接收方，二者都不递减）
            if not is_origin:
                packet.decrement_ttl()
                if packet.ttl <= 0:
                    drop_reason = f"TTL 超时，分组在节点 {current_node.id} 被丢弃"
                    break

            # 3) 查表（最长前缀匹配）
            route = current_node.lookup_route(packet.dst_ip)
            if route is None:
                drop_reason = f"节点 {current_node.id} 无到达 {dst_ip} 的路由"
                break

            next_hop = route.get("next_hop")
            out_interface_name = route.get("interface")

            if next_hop == "local":
                # 路由表认为目的地址在本机，但 owns_ip 未命中：属于路由表异常，保守丢弃
                drop_reason = f"节点 {current_node.id} 路由表标记为本地投递，但地址 {dst_ip} 不属于本机"
                break

            # 4) 解析出接口与下一跳节点
            out_interface = current_node.get_interface_by_name(out_interface_name)
            if out_interface is None:
                drop_reason = f"节点 {current_node.id} 路由表指向不存在的出接口 {out_interface_name}"
                break
            if not out_interface.is_up():
                drop_reason = (
                    f"节点 {current_node.id} 出接口 {out_interface.name} 不可用（链路或对端节点故障）"
                )
                break

            peer_id = out_interface.neighbor_id
            if not peer_id:
                drop_reason = f"节点 {current_node.id} 出接口 {out_interface.name} 没有对端节点"
                break

            # 5) 校验下一跳地址确实位于该出接口网段内（模拟 ARP/直连可达性检查）
            hop_address = dst_ip if next_hop in (None, "direct") else next_hop
            if not out_interface.contains(hop_address):
                drop_reason = (
                    f"节点 {current_node.id} 的下一跳 {hop_address} 不在出接口 {out_interface.name} 网段内"
                )
                break

            # 6) 校验对端节点与链路的可用性（关键：避免报文穿越已断链路）
            peer_node = self.topology.get_node(peer_id)
            if peer_node is None:
                drop_reason = f"下一跳节点 {peer_id} 不存在"
                break
            if not peer_node.is_up():
                drop_reason = f"下一跳节点 {peer_id} 已故障"
                break
            link = self.topology.get_link_between(current_node.id, peer_id)
            if link is None or not link.is_up():
                drop_reason = f"节点 {current_node.id} → {peer_id} 链路故障，无法转发"
                break

            # 7) 模拟链路传输时延
            if link.delay:
                await asyncio.sleep(link.delay / 1000.0)  # 毫秒转秒

            logger.debug("转发: %s -> %s (ttl=%d)", current_node.id, peer_id, packet.ttl)
            current_node = peer_node
            is_origin = False
            packet.record_hop(current_node.id)
        else:
            drop_reason = "超过最大跳数，疑似存在路由环路"

        # 状态收尾与结果推送
        if packet.status == "in_transit":
            packet.mark_dropped()
        if packet.status == "delivered":
            self.metrics["packets_delivered"] += 1
            hops = len(packet.path) - 1  # 跳数 = 链路数 = 节点数 - 1
            await self._log("info", f"分组到达目的节点 {current_node.id}，共 {hops} 跳（途经 {len(packet.path)} 个节点）")
        else:
            self.metrics["packets_dropped"] += 1
            packet.drop_reason = drop_reason or "未知原因"
            level = "error" if drop_reason and "无到达" in drop_reason else "warning"
            await self._log(level, f"分组被丢弃：{packet.drop_reason}")

        packet_info = packet.to_dict()
        await self._push_callback("packet_forwarded", packet_info)

        if packet.status == "delivered":
            message = f"分组转发完成，状态: delivered（{len(packet.path) - 1} 跳）"
        else:
            message = f"分组转发完成，状态: dropped（{packet.drop_reason}）"
        return {
            "success": packet.status == "delivered",
            "message": message,
            "data": packet_info,
        }

    # ---------- 获取拓扑快照 ----------
    def get_topology_snapshot(self) -> Dict[str, Any]:
        """返回当前拓扑快照（供 REST API 使用）"""
        return self.topology.get_topology_snapshot()
