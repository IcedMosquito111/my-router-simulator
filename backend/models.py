from typing import List, Optional, Literal
from pydantic import BaseModel, Field
from datetime import datetime


# ---------- 节点相关 ----------
class NodeInfo(BaseModel):
    """网络节点（路由器）信息"""
    id: str = Field(..., description="节点唯一标识，如 'R1'")
    name: str = Field(..., description="节点显示名称")
    ipv4_interfaces: List[str] = Field(default_factory=list, description="IPv4 接口地址列表，如 ['192.168.1.1/24']")
    ipv6_interfaces: List[str] = Field(default_factory=list, description="IPv6 接口地址列表，如 ['2001:db8::1/64']")
    status: Literal["up", "down"] = Field(default="up", description="节点状态：up=正常，down=故障")

    class Config:
        json_schema_extra = {
            "example": {
                "id": "R1",
                "name": "Router 1",
                "ipv4_interfaces": ["10.0.0.1/24", "10.0.1.1/24"],
                "ipv6_interfaces": ["2001:db8:1::1/64"],
                "status": "up"
            }
        }


class LinkInfo(BaseModel):
    """网络链路（连接两个节点的边）信息"""
    source: str = Field(..., description="链路源节点 ID")
    target: str = Field(..., description="链路目标节点 ID")
    cost: int = Field(default=1, ge=1, description="链路开销，用于路由计算")
    bandwidth: int = Field(default=100, gt=0, description="链路带宽（Mbps）")
    delay: int = Field(default=1, ge=0, description="链路传输延迟（毫秒）")
    status: Literal["up", "down"] = Field(default="up", description="链路状态：up=正常，down=故障")

    class Config:
        json_schema_extra = {
            "example": {
                "source": "R1",
                "target": "R2",
                "cost": 10,
                "bandwidth": 1000,
                "delay": 5,
                "status": "up"
            }
        }


class TopologySnapshot(BaseModel):
    """完整拓扑快照，用于发送给前端展示"""
    nodes: List[NodeInfo] = Field(default_factory=list, description="所有节点信息")
    links: List[LinkInfo] = Field(default_factory=list, description="所有链路信息")

    class Config:
        json_schema_extra = {
            "example": {
                "nodes": [
                    {"id": "R1", "name": "Router 1", "ipv4_interfaces": ["10.0.0.1/24"], "ipv6_interfaces": ["2001:db8::1/64"], "status": "up"},
                    {"id": "R2", "name": "Router 2", "ipv4_interfaces": ["10.0.0.2/24"], "ipv6_interfaces": ["2001:db8::2/64"], "status": "up"}
                ],
                "links": [
                    {"source": "R1", "target": "R2", "cost": 10, "bandwidth": 1000, "delay": 5, "status": "up"}
                ]
            }
        }


# ---------- 路由表相关 ----------
class RouteEntry(BaseModel):
    """单条路由表项"""
    destination: str = Field(..., description="目的网络地址，如 '192.168.1.0/24' 或 '2001:db8::/64'")
    next_hop: str = Field(..., description="下一跳地址，如 '10.0.0.2' 或 'fe80::2'")
    metric: int = Field(..., description="路由度量值（成本）")
    interface: str = Field(..., description="出接口名称，如 'eth0'")
    protocol: Literal["OSPF", "static", "connected"] = Field(default="OSPF", description="路由来源协议")

    class Config:
        json_schema_extra = {
            "example": {
                "destination": "192.168.1.0/24",
                "next_hop": "10.0.0.2",
                "metric": 20,
                "interface": "eth0",
                "protocol": "OSPF"
            }
        }


class RoutingTableUpdate(BaseModel):
    """某个节点的完整路由表（用于推送更新）"""
    node_id: str = Field(..., description="节点 ID")
    routes: List[RouteEntry] = Field(default_factory=list, description="该节点的所有路由条目")

    class Config:
        json_schema_extra = {
            "example": {
                "node_id": "R1",
                "routes": [
                    {"destination": "192.168.1.0/24", "next_hop": "10.0.0.2", "metric": 20, "interface": "eth0", "protocol": "OSPF"},
                    {"destination": "10.0.0.0/24", "next_hop": "direct", "metric": 0, "interface": "eth0", "protocol": "connected"}
                ]
            }
        }


# ---------- 日志相关 ----------
class LogMessage(BaseModel):
    """日志消息，用于前端日志窗口显示"""
    level: Literal["info", "warning", "error", "debug"] = Field(default="info", description="日志级别")
    message: str = Field(..., description="日志内容")
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat(), description="日志产生时间（ISO 格式）")

    class Config:
        json_schema_extra = {
            "example": {
                "level": "info",
                "message": "节点 R1 发生故障",
                "timestamp": "2025-01-01T12:00:00"
            }
        }


# ---------- 分组转发相关 ----------
class PacketInfo(BaseModel):
    """模拟转发的分组信息"""
    src: str = Field(..., description="源 IP 地址（IPv4 或 IPv6）")
    dst: str = Field(..., description="目的 IP 地址（IPv4 或 IPv6）")
    protocol: Literal["IPv4", "IPv6"] = Field(..., description="IP 协议版本")
    path: List[str] = Field(default_factory=list, description="分组经过的节点 ID 序列，如 ['R1', 'R2', 'R3']")
    ttl: int = Field(default=64, ge=0, description="生存时间（跳数）")
    status: Literal["delivered", "dropped", "in_transit"] = Field(default="in_transit", description="分组最终状态：delivered=已送达，dropped=被丢弃")

    class Config:
        json_schema_extra = {
            "example": {
                "src": "192.168.1.10",
                "dst": "10.0.0.5",
                "protocol": "IPv4",
                "path": ["R1", "R2", "R4"],
                "ttl": 61,
                "status": "delivered"
            }
        }


# ---------- 命令与响应模型（WebSocket 交互） ----------
class Command(BaseModel):
    """前端发送给后端的命令结构"""
    action: str = Field(..., description="命令类型，如 'inject_fault', 'send_packet', 'get_topology' 等")
    params: dict = Field(default_factory=dict, description="命令参数")

    class Config:
        json_schema_extra = {
            "example": {
                "action": "inject_fault",
                "params": {"type": "node", "id": "R1"}
            }
        }


class CommandResponse(BaseModel):
    """后端对命令的响应"""
    success: bool = Field(..., description="命令是否成功执行")
    message: str = Field(default="", description="响应消息（成功或错误说明）")
    data: Optional[dict] = Field(default=None, description="附加数据，如路径信息等")

    class Config:
        json_schema_extra = {
            "example": {
                "success": True,
                "message": "故障注入成功",
                "data": {"node_id": "R1", "status": "down"}
            }
        }