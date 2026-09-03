import random
import ipaddress
from datetime import datetime
from typing import Optional, Set, List


# ---------- IP 地址生成 ----------
def generate_ipv4(subnet: str = "10.0.0.0", mask: int = 24, used_ips: Optional[Set[str]] = None) -> str:
    """
    在指定的 IPv4 子网中生成一个未使用的地址。

    参数:
        subnet: 子网地址，如 "10.0.0.0"
        mask:   子网掩码长度，如 24 表示 255.255.255.0
        used_ips: 已使用的 IP 地址集合（避免重复）

    返回:
        一个 IPv4 地址字符串（不带子网掩码），如 "10.0.0.5"
    """
    if used_ips is None:
        used_ips = set()

    # 创建网络对象，strict=False 允许 host bits 非零
    network = ipaddress.IPv4Network(f"{subnet}/{mask}", strict=False)

    # 获取所有可用主机地址（排除网络地址和广播地址）
    hosts = list(network.hosts())
    if not hosts:
        raise ValueError("子网中没有可用主机地址")

    # 随机打乱，然后选择第一个未被使用的
    random.shuffle(hosts)
    for host in hosts:
        ip_str = str(host)
        if ip_str not in used_ips:
            used_ips.add(ip_str)
            return ip_str

    # 如果所有地址都被占用，则抛出异常
    raise RuntimeError("子网中没有更多可用地址")


def generate_ipv6(prefix: str = "2001:db8::", prefix_len: int = 64, used_ips: Optional[Set[str]] = None) -> str:
    """
    在指定的 IPv6 前缀中生成一个未使用的地址。

    参数:
        prefix:     IPv6 前缀，如 "2001:db8::"
        prefix_len: 前缀长度，如 64
        used_ips:   已使用的 IP 地址集合（避免重复）

    返回:
        一个 IPv6 地址字符串（不带前缀长度），如 "2001:db8::1"
    """
    if used_ips is None:
        used_ips = set()

    # 创建 IPv6 网络对象
    network = ipaddress.IPv6Network(f"{prefix}/{prefix_len}")

    # 获取所有主机地址，但 IPv6 地址空间巨大，我们不能枚举所有主机
    # 因此随机生成一个接口标识部分（后 128-prefix_len 位）
    # 为简单起见，我们只生成一个随机整数作为接口 ID
    interface_id = random.randint(1, 2**(128 - prefix_len) - 2)
    ip_int = int(network.network_address) + interface_id
    ip_str = str(ipaddress.IPv6Address(ip_int))

    # 如果已存在，则递归尝试（最多尝试 100 次）
    attempts = 0
    while ip_str in used_ips and attempts < 100:
        interface_id = random.randint(1, 2**(128 - prefix_len) - 2)
        ip_int = int(network.network_address) + interface_id
        ip_str = str(ipaddress.IPv6Address(ip_int))
        attempts += 1

    if attempts >= 100:
        raise RuntimeError("无法在 IPv6 前缀中找到未使用的地址")

    used_ips.add(ip_str)
    return ip_str


# ---------- IP 地址校验 ----------
def is_valid_ipv4(ip: str) -> bool:
    """检查字符串是否为合法的 IPv4 地址（不含子网掩码）"""
    try:
        ipaddress.IPv4Address(ip)
        return True
    except ipaddress.AddressValueError:
        return False


def is_valid_ipv6(ip: str) -> bool:
    """检查字符串是否为合法的 IPv6 地址（不含前缀长度）"""
    try:
        ipaddress.IPv6Address(ip)
        return True
    except ipaddress.AddressValueError:
        return False


# ---------- 时间与日志辅助 ----------
def format_timestamp() -> str:
    """
    返回当前时间的 ISO 格式字符串，用于日志消息。

    返回:
        如 "2025-03-21T14:30:45.123456"
    """
    return datetime.now().isoformat()


def log_message(level: str, message: str) -> dict:
    """
    构建一条标准格式的日志消息字典，方便后续通过 WebSocket 推送。

    参数:
        level:   日志级别，如 "info", "warning", "error", "debug"
        message: 日志内容

    返回:
        包含级别、消息和时间戳的字典
    """
    return {
        "level": level,
        "message": message,
        "timestamp": format_timestamp()
    }


# ---------- 其他可能用到的辅助函数 ----------
def split_ip_subnet(ip_with_mask: str) -> tuple:
    """
    将 "IP/掩码" 形式的字符串拆分为 (ip, mask) 元组。

    参数:
        ip_with_mask: 如 "192.168.1.1/24" 或 "2001:db8::1/64"

    返回:
        (ip_str, mask_int) 元组
    """
    if "/" in ip_with_mask:
        ip_part, mask_part = ip_with_mask.split("/", 1)
        return ip_part, int(mask_part)
    else:
        return ip_with_mask, None


def is_same_subnet(ip1: str, ip2: str, mask: int) -> bool:
    """
    判断两个 IP 地址是否在同一个子网内。

    参数:
        ip1, ip2: IP 地址字符串（不带掩码）
        mask:     子网掩码长度

    返回:
        True 如果在同一子网，否则 False
    """
    # 自动识别 IPv4 或 IPv6
    if ":" in ip1:
        net1 = ipaddress.IPv6Network(f"{ip1}/{mask}", strict=False)
        net2 = ipaddress.IPv6Network(f"{ip2}/{mask}", strict=False)
    else:
        net1 = ipaddress.IPv4Network(f"{ip1}/{mask}", strict=False)
        net2 = ipaddress.IPv4Network(f"{ip2}/{mask}", strict=False)
    return net1.network_address == net2.network_address