"""
运行配置：全部通过环境变量注入，避免把节点数、端口、跨域白名单硬编码在代码里。

常用环境变量：
    RS_NUM_NODES     随机拓扑的节点数（默认 50）
    RS_TOPOLOGY_FILE 拓扑 JSON 文件路径（提供后忽略 RS_NUM_NODES）
    RS_SEED          随机种子（固定后拓扑与实验可复现）
    RS_HOST/RS_PORT  监听地址与端口（默认 0.0.0.0:8000）
    RS_CORS_ORIGINS  允许的跨域来源，逗号分隔（默认 *）
    RS_LOG_LEVEL     日志级别（默认 INFO）
    RS_DEBUG_LOGS    置 1 时打开逐节点调试日志（默认关闭）
    RS_RELOAD        置 1 时开启 uvicorn 热重载（仅开发用）
"""

import os
from typing import List, Optional


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"环境变量 {name} 必须是整数，当前为 {raw!r}") from exc


def _get_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _get_list(name: str, default: List[str]) -> List[str]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


NUM_NODES: int = _get_int("RS_NUM_NODES", 50)
TOPOLOGY_FILE: Optional[str] = os.getenv("RS_TOPOLOGY_FILE") or None
SEED: Optional[int] = _get_int("RS_SEED", None) if os.getenv("RS_SEED") else None
HOST: str = os.getenv("RS_HOST", "0.0.0.0")
PORT: int = _get_int("RS_PORT", 8000)
CORS_ORIGINS: List[str] = _get_list("RS_CORS_ORIGINS", ["*"])
LOG_LEVEL: str = os.getenv("RS_LOG_LEVEL", "INFO").upper()
DEBUG_LOGS: bool = _get_bool("RS_DEBUG_LOGS", False)
RELOAD: bool = _get_bool("RS_RELOAD", False)

if NUM_NODES < 2:
    raise ValueError(f"RS_NUM_NODES 至少为 2，当前为 {NUM_NODES}")
