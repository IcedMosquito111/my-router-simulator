"""
链路类：表示两个网络节点之间的一条连接。
每条链路是双向的，连接两个节点（通过节点 ID 引用）。
"""

from typing import Optional, Dict, Any


class Link:
    """
    网络链路，连接两个节点。链路是双向的，但代价可能非对称（本项目中简化为对称代价）。

    属性:
        source_id (str):         源节点 ID
        target_id (str):         目标节点 ID
        cost (int):              链路开销（用于路由算法，如 OSPF 的 metric）
        bandwidth (int):         链路带宽（Mbps）
        delay (int):             链路传输延迟（毫秒）
        status (str):            链路状态: "up" 或 "down"
        source_interface (str):  源节点侧接口名称（可选，如 "eth0"）
        target_interface (str):  目标节点侧接口名称（可选，如 "eth0"）
    """

    def __init__(
        self,
        source_id: str,
        target_id: str,
        cost: int = 1,
        bandwidth: int = 100,
        delay: int = 1,
        status: str = "up",
        source_interface: Optional[str] = None,
        target_interface: Optional[str] = None,
    ):
        """
        初始化一条链路。

        参数:
            source_id: 源节点 ID
            target_id: 目标节点 ID
            cost:      链路开销，默认为 1
            bandwidth: 带宽（Mbps），默认为 100
            delay:     延迟（毫秒），默认为 1
            status:    初始状态，默认为 "up"
            source_interface: 源节点接口名称，可选
            target_interface: 目标节点接口名称，可选
        """
        self.source_id = source_id
        self.target_id = target_id
        self.cost = cost
        self.bandwidth = bandwidth
        self.delay = delay
        self.status = status  # "up" or "down"
        self.source_interface = source_interface
        self.target_interface = target_interface

    # ---------- 状态管理 ----------
    def set_up(self) -> None:
        """将链路状态设置为 up（正常）"""
        self.status = "up"

    def set_down(self) -> None:
        """将链路状态设置为 down（故障）"""
        self.status = "down"

    def is_up(self) -> bool:
        """判断链路是否处于正常状态"""
        return self.status == "up"

    # ---------- 邻居查询 ----------
    def get_other_end(self, node_id: str) -> Optional[str]:
        """
        获取链路的另一端节点 ID。

        参数:
            node_id: 当前已知的一端节点 ID

        返回:
            另一端节点 ID，如果 node_id 不是该链路的端点则返回 None
        """
        if node_id == self.source_id:
            return self.target_id
        elif node_id == self.target_id:
            return self.source_id
        else:
            return None

    def connects(self, node_id1: str, node_id2: str) -> bool:
        """
        判断链路是否直接连接指定的两个节点（不考虑方向）。

        参数:
            node_id1: 节点1 ID
            node_id2: 节点2 ID

        返回:
            True 如果链路连接这两个节点，否则 False
        """
        return (self.source_id == node_id1 and self.target_id == node_id2) or \
               (self.source_id == node_id2 and self.target_id == node_id1)

    # ---------- 信息导出 ----------
    def to_dict(self) -> Dict[str, Any]:
        """
        将链路信息转换为字典，便于 JSON 序列化（与 models.LinkInfo 兼容）。

        返回:
            包含链路所有属性的字典
        """
        return {
            "source": self.source_id,
            "target": self.target_id,
            "cost": self.cost,
            "bandwidth": self.bandwidth,
            "delay": self.delay,
            "status": self.status,
        }

    def __repr__(self) -> str:
        """字符串表示，便于调试"""
        return f"Link({self.source_id} <-> {self.target_id}, cost={self.cost}, status={self.status})"