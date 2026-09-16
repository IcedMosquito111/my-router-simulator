import ipaddress
from typing import Any, Dict, List, Optional, Literal, Annotated
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator, model_validator
from datetime import datetime


# ---------- 节点相关 ----------
class NodeInfo(BaseModel):
    """网络节点（路由器）信息"""
    id: str = Field(..., description="节点唯一标识，如 'R1'")
    name: str = Field(..., description="节点显示名称")
    loopback_ipv4: Optional[str] = Field(default=None, description="回环 IPv4 地址（含掩码），如 '10.255.0.1/32'")
    loopback_ipv6: Optional[str] = Field(default=None, description="回环 IPv6 地址（含掩码），如 '2001:db8:ffff::1/128'")
    ipv4_interfaces: List[str] = Field(default_factory=list, description="IPv4 接口地址列表，如 ['192.168.1.1/24']")
    ipv6_interfaces: List[str] = Field(default_factory=list, description="IPv6 接口地址列表，如 ['2001:db8::1/64']")
    status: Literal["up", "down"] = Field(default="up", description="节点状态：up=正常，down=故障")

    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "id": "R1",
                "name": "Router 1",
                "ipv4_interfaces": ["10.0.0.1/24", "10.0.1.1/24"],
                "ipv6_interfaces": ["2001:db8:1::1/64"],
                "status": "up"
            }
        }
    )


class LinkInfo(BaseModel):
    """网络链路（连接两个节点的边）信息"""
    source: str = Field(..., description="链路源节点 ID")
    target: str = Field(..., description="链路目标节点 ID")
    cost: int = Field(default=1, ge=1, description="链路开销，用于路由计算")
    bandwidth: int = Field(default=100, gt=0, description="链路带宽（Mbps）")
    delay: int = Field(default=1, ge=0, description="链路传输延迟（毫秒）")
    status: Literal["up", "down"] = Field(default="up", description="链路有效状态（链路 up 且两端节点都 up）")
    admin_status: Optional[Literal["up", "down"]] = Field(
        default=None, description="链路自身状态（故障注入/配置的结果，不考虑节点状态）"
    )

    model_config = ConfigDict(
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
    )


class TopologySnapshot(BaseModel):
    """完整拓扑快照，用于发送给前端展示"""
    nodes: List[NodeInfo] = Field(default_factory=list, description="所有节点信息")
    links: List[LinkInfo] = Field(default_factory=list, description="所有链路信息")

    model_config = ConfigDict(
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
    )


# ---------- 路由表相关 ----------
class RouteEntry(BaseModel):
    """单条路由表项"""
    destination: str = Field(..., description="目的网络地址，如 '192.168.1.0/24' 或 '2001:db8::/64'")
    next_hop: str = Field(..., description="下一跳地址，如 '10.0.0.2' 或 'fe80::2'")
    metric: int = Field(..., description="路由度量值（成本）")
    interface: str = Field(..., description="出接口名称，如 'eth0'")
    protocol: Literal["OSPF", "RIP", "static", "connected", "local"] = Field(default="OSPF", description="路由来源协议")

    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "destination": "192.168.1.0/24",
                "next_hop": "10.0.0.2",
                "metric": 20,
                "interface": "eth0",
                "protocol": "OSPF"
            }
        }
    )


class RoutingTableUpdate(BaseModel):
    """某个节点的完整路由表（用于推送更新）"""
    node_id: str = Field(..., description="节点 ID")
    routes: List[RouteEntry] = Field(default_factory=list, description="该节点的所有路由条目")

    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "node_id": "R1",
                "routes": [
                    {"destination": "192.168.1.0/24", "next_hop": "10.0.0.2", "metric": 20, "interface": "eth0", "protocol": "OSPF"},
                    {"destination": "10.0.0.0/24", "next_hop": "direct", "metric": 0, "interface": "eth0", "protocol": "connected"}
                ]
            }
        }
    )


# ---------- 日志相关 ----------
class LogMessage(BaseModel):
    """日志消息，用于前端日志窗口显示"""
    level: Literal["info", "warning", "error", "debug"] = Field(default="info", description="日志级别")
    message: str = Field(..., description="日志内容")
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat(), description="日志产生时间（ISO 格式）")

    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "level": "info",
                "message": "节点 R1 发生故障",
                "timestamp": "2025-01-01T12:00:00"
            }
        }
    )


# ---------- 分组转发相关 ----------
class PacketInfo(BaseModel):
    """模拟转发的分组信息"""
    src: str = Field(..., description="源 IP 地址（IPv4 或 IPv6）")
    dst: str = Field(..., description="目的 IP 地址（IPv4 或 IPv6）")
    protocol: Literal["IPv4", "IPv6"] = Field(..., description="IP 协议版本")
    path: List[str] = Field(default_factory=list, description="分组经过的节点 ID 序列，如 ['R1', 'R2', 'R3']")
    ttl: int = Field(default=64, ge=0, description="生存时间（跳数）")
    status: Literal["delivered", "dropped", "in_transit"] = Field(default="in_transit", description="分组最终状态：delivered=已送达，dropped=被丢弃")
    drop_reason: Optional[str] = Field(default=None, description="分组被丢弃的原因（status=dropped 时有效）")

    model_config = ConfigDict(
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
    )


# ---------- 命令与响应模型（WebSocket 交互） ----------
class Command(BaseModel):
    """前端发送给后端的命令结构"""
    action: str = Field(..., min_length=1, max_length=64,
                        description="命令类型，如 'inject_fault', 'send_packet', 'get_topology' 等")
    params: Dict[str, Any] = Field(default_factory=dict, description="命令参数（必须是 JSON 对象）")

    @field_validator("params", mode="before")
    @classmethod
    def _params_must_be_object(cls, value: Any) -> Any:
        """params 允许缺省或为 null，但显式传非对象（字符串/数字/数组）一律拒绝"""
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("params 必须是 JSON 对象")
        return value

    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "action": "inject_fault",
                "params": {"type": "node", "id": "R1"}
            }
        }
    )


class CommandResponse(BaseModel):
    """后端对命令的响应"""
    success: bool = Field(..., description="命令是否成功执行")
    message: str = Field(default="", description="响应消息（成功或错误说明）")
    data: Optional[dict] = Field(default=None, description="附加数据，如路径信息等")

    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "success": True,
                "message": "故障注入成功",
                "data": {"node_id": "R1", "status": "down"}
            }
        }
    )


# ---------- 命令参数校验模型 ----------
# 说明：这些模型是 WebSocket/REST 入口的第一道防线。
# 未校验的输入会以异常形式穿透到连接处理层，导致客户端连接被直接断开。

NodeIdStr = Annotated[str, StringConstraints(min_length=1, max_length=64, strip_whitespace=True)]
AddressStr = Annotated[str, StringConstraints(min_length=1, max_length=45, strip_whitespace=True)]


class UnknownActionError(ValueError):
    """命令类型不受支持"""

    def __init__(self, action: str, known: tuple):
        super().__init__(f"未知命令: {action}（支持: {', '.join(known)}）")
        self.action = action


def format_validation_error(exc: ValidationError) -> str:
    """把 pydantic 的校验错误转成一行中文提示，便于直接回给前端"""
    details = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ())) or "params"
        details.append(f"{location}: {error.get('msg', '非法取值')}")
    return "; ".join(details) if details else "参数非法"


class EmptyParams(BaseModel):
    """无需参数的命令"""
    model_config = {"extra": "forbid"}


class GetRoutingTableParams(BaseModel):
    """查询指定节点的路由表"""
    node_id: NodeIdStr = Field(..., description="节点 ID，如 'R1'")


class InjectFaultParams(BaseModel):
    """故障注入参数：节点故障需要 id，链路故障需要 source/target"""
    type: Literal["node", "link"] = Field(..., description="故障类型：node=节点，link=链路")
    id: Optional[NodeIdStr] = Field(default=None, description="节点 ID（type=node 时必填）")
    source: Optional[NodeIdStr] = Field(default=None, description="链路源节点（type=link 时必填）")
    target: Optional[NodeIdStr] = Field(default=None, description="链路目的节点（type=link 时必填）")

    @model_validator(mode="after")
    def _check_required_fields(self) -> "InjectFaultParams":
        if self.type == "node" and not self.id:
            raise ValueError("type=node 时必须提供 id")
        if self.type == "link":
            if not self.source or not self.target:
                raise ValueError("type=link 时必须提供 source 和 target")
            if self.source == self.target:
                raise ValueError("链路的 source 与 target 不能相同")
        return self


class RecoverFaultParams(InjectFaultParams):
    """故障恢复参数：与故障注入同构"""


class SendPacketParams(BaseModel):
    """发送分组的参数"""
    src_node: NodeIdStr = Field(..., description="源节点 ID")
    dst_ip: AddressStr = Field(..., description="目的 IP 地址（IPv4 或 IPv6，不带掩码）")
    protocol: Optional[Literal["IPv4", "IPv6"]] = Field(default=None, description="协议版本，缺省按目的地址推断")
    ttl: int = Field(default=64, ge=1, le=255, description="初始 TTL")

    @field_validator("dst_ip")
    @classmethod
    def _dst_must_be_ip(cls, value: str) -> str:
        try:
            ipaddress.ip_address(value)
        except ValueError as exc:
            raise ValueError(f"不是合法的 IP 地址: {value}") from exc
        return value

    @model_validator(mode="after")
    def _protocol_matches_address(self) -> "SendPacketParams":
        is_ipv6 = ":" in self.dst_ip
        if self.protocol == "IPv4" and is_ipv6:
            raise ValueError("protocol=IPv4 与 IPv6 目的地址不一致")
        if self.protocol == "IPv6" and not is_ipv6:
            raise ValueError("protocol=IPv6 与 IPv4 目的地址不一致")
        return self


# 命令 -> 参数模型 的映射表
ACTION_PARAMS: Dict[str, type] = {
    "get_topology": EmptyParams,
    "get_routing_table": GetRoutingTableParams,
    "inject_fault": InjectFaultParams,
    "recover_fault": RecoverFaultParams,
    "send_packet": SendPacketParams,
}
KNOWN_ACTIONS = tuple(ACTION_PARAMS)


def parse_command_params(action: str, params: Optional[Dict[str, Any]]):
    """
    按命令类型校验并解析参数。

    参数:
        action: 命令类型（必须在 KNOWN_ACTIONS 中）
        params: 原始参数字典（可为 None）

    返回:
        对应的 pydantic 模型实例

    异常:
        UnknownActionError: 命令类型不受支持
        ValidationError:    参数不合法
    """
    model = ACTION_PARAMS.get(action)
    if model is None:
        raise UnknownActionError(action, KNOWN_ACTIONS)
    return model.model_validate(params or {})
