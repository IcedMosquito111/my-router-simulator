"""
分组（Packet）类：模拟 IP 分组在网络中的转发。
支持 IPv4 和 IPv6 两种协议版本。
"""

from typing import List, Optional, Dict, Any
import ipaddress
import uuid


class Packet:
    """
    IP 分组对象，包含源/目的地址、TTL、协议版本、负载和转发路径等信息。
    在转发过程中，TTL 逐跳递减，路径被记录。
    """

    def __init__(
        self,
        src_ip: str,
        dst_ip: str,
        protocol: Optional[str] = None,
        ttl: int = 64,
        payload: Any = None,
        packet_id: Optional[str] = None,
    ):
        """
        初始化一个分组。

        参数:
            src_ip:   源 IP 地址（IPv4 或 IPv6 字符串，不含掩码）
            dst_ip:   目的 IP 地址
            protocol: 协议版本，可选 "IPv4" 或 "IPv6"；如果不指定，则根据 IP 地址自动判断
            ttl:      初始 TTL 值，默认为 64
            payload:  负载内容，任意类型，默认为空
            packet_id: 分组唯一标识，若不提供则自动生成 UUID
        """
        # 自动判断协议版本
        if protocol is None:
            protocol = self._infer_protocol(src_ip)
            if protocol is None:
                protocol = self._infer_protocol(dst_ip)
            if protocol is None:
                raise ValueError("无法判断 IP 版本，请提供有效的 IPv4 或 IPv6 地址")

        self.src_ip = src_ip
        self.dst_ip = dst_ip
        self.protocol = protocol
        self.ttl = ttl
        self.payload = payload if payload is not None else {}
        self.packet_id = packet_id or str(uuid.uuid4())

        # 转发路径：记录经过的节点 ID 序列（按顺序）
        self.path: List[str] = []

        # 最终状态：in_transit（转发中）、delivered（已送达）、dropped（被丢弃）
        self.status = "in_transit"

    @staticmethod
    def _infer_protocol(ip: str) -> Optional[str]:
        """根据 IP 地址字符串推断协议版本"""
        try:
            ipaddress.IPv4Address(ip)
            return "IPv4"
        except ipaddress.AddressValueError:
            pass
        try:
            ipaddress.IPv6Address(ip)
            return "IPv6"
        except ipaddress.AddressValueError:
            pass
        return None

    # ---------- TTL 操作 ----------
    def decrement_ttl(self) -> int:
        """
        减少 TTL 值（每经过一跳调用一次）。
        返回新的 TTL 值。
        """
        self.ttl -= 1
        return self.ttl

    def is_expired(self) -> bool:
        """判断 TTL 是否已经耗尽（<=0）"""
        return self.ttl <= 0

    # ---------- 路径记录 ----------
    def record_hop(self, node_id: str) -> None:
        """
        记录分组经过的节点。
        通常由路由器在转发时调用，将当前节点 ID 添加到路径末尾。
        """
        if not self.path or self.path[-1] != node_id:
            self.path.append(node_id)

    # ---------- 状态设置 ----------
    def mark_delivered(self) -> None:
        """标记分组已成功到达目的节点"""
        self.status = "delivered"

    def mark_dropped(self) -> None:
        """标记分组已被丢弃（如 TTL 耗尽或路由不可达）"""
        self.status = "dropped"

    # ---------- 信息导出 ----------
    def to_dict(self) -> Dict[str, Any]:
        """
        将分组信息转换为字典，与 models.PacketInfo 兼容。
        返回:
            包含分组所有关键信息的字典
        """
        return {
            "packet_id": self.packet_id,
            "src": self.src_ip,
            "dst": self.dst_ip,
            "protocol": self.protocol,
            "ttl": self.ttl,
            "path": self.path,
            "status": self.status,
            "payload": self.payload,
        }

    def __repr__(self) -> str:
        return f"Packet({self.src_ip} -> {self.dst_ip}, {self.protocol}, ttl={self.ttl}, status={self.status})"