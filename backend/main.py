"""
基于 Python 的路由模拟系统 - 后端主入口

使用 FastAPI 提供：
    - WebSocket /ws        实时双向通信（拓扑、路由表、日志、分组转发）
    - REST  /api/*         调试与报告用接口（拓扑、指标、命令）

输入校验说明：
    WebSocket 与 REST 共用 simulator.handle_command()，
    所有命令先经 models.Command / parse_command_params 校验，
    非法输入只返回错误响应，绝不断开客户端连接。
"""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

import settings
from network.simulator import Simulator

# ---------- 日志配置 ----------
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("router-simulator")
# 逐节点日志（LSA 洪泛、路由表更新）默认关闭，避免一次故障注入刷屏 50 行
logging.getLogger("router-simulator.ospf").setLevel(logging.DEBUG if settings.DEBUG_LOGS else logging.INFO)
logging.getLogger("router-simulator.rip").setLevel(logging.DEBUG if settings.DEBUG_LOGS else logging.INFO)

# ---------- 全局模拟器实例 ----------
simulator: Optional[Simulator] = None

SEND_TIMEOUT_SECONDS = 5.0  # 单个客户端发送超时，避免慢客户端拖住广播


class ConnectionManager:
    """管理所有活跃的 WebSocket 连接"""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info("New client connected: %s", websocket.client)

    def disconnect(self, websocket: WebSocket) -> None:
        self.active_connections.discard(websocket)
        logger.info("Client disconnected: %s", websocket.client)

    async def broadcast(self, message: Dict[str, Any]) -> None:
        """
        向所有连接的客户端广播消息。

        优化点：
            1. JSON 只序列化一次，而不是每个连接各序列化一次；
            2. 并发发送（asyncio.gather）而不是串行等待，客户端数量不影响推送耗时；
            3. 单连接超时/异常只剔除该连接，不影响其他客户端。
        """
        connections = list(self.active_connections)
        if not connections:
            return
        payload = json.dumps(message, ensure_ascii=False)
        results = await asyncio.gather(
            *(self._safe_send(conn, payload) for conn in connections),
            return_exceptions=True,
        )
        for conn, result in zip(connections, results):
            if isinstance(result, Exception):
                logger.warning("推送失败，剔除连接 %s: %s", conn.client, result)
                self.disconnect(conn)

    @staticmethod
    async def _safe_send(websocket: WebSocket, payload: str) -> None:
        await asyncio.wait_for(websocket.send_text(payload), timeout=SEND_TIMEOUT_SECONDS)


manager = ConnectionManager()


# ---------- 模拟器事件回调 ----------
async def on_topology_update(snapshot: dict):
    """拓扑更新回调，推送给前端"""
    await manager.broadcast({"type": "topology_update", "data": snapshot})


async def on_routing_table_update(update: dict):
    """路由表更新回调"""
    await manager.broadcast({"type": "routing_table_update", "data": update})


async def on_log_message(log_msg: dict):
    """日志消息回调"""
    await manager.broadcast({"type": "log", "data": log_msg})


async def on_packet_forwarded(packet_info: dict):
    """分组转发完成回调"""
    await manager.broadcast({"type": "packet_forwarded", "data": packet_info})


# ---------- FastAPI 生命周期 ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动/关闭时管理模拟器生命周期"""
    global simulator
    logger.info("Starting router simulator backend...")

    simulator = Simulator(
        num_nodes=settings.NUM_NODES,
        topology_file=settings.TOPOLOGY_FILE,
        seed=settings.SEED,
        callbacks={
            "topology_update": on_topology_update,
            "routing_table_update": on_routing_table_update,
            "log_message": on_log_message,
            "packet_forwarded": on_packet_forwarded,
        },
    )
    # 初始拓扑与路由表在事件循环就绪后推送（__init__ 只做纯计算）
    await simulator.start()
    logger.info("Simulator started: %d nodes / %d links",
                len(simulator.topology.nodes), len(simulator.topology.links))

    yield  # 应用运行期间

    logger.info("Shutting down simulator...")
    simulator = None


# 创建 FastAPI 应用
app = FastAPI(
    title="路由模拟系统后端",
    description="基于 Python 的路由模拟系统 API（OSPF 路由计算 + 分组转发 + 故障注入）",
    version="0.3.0",
    lifespan=lifespan,
)

# 配置 CORS
# 注意：allow_origins=["*"] 与 allow_credentials=True 不能同时生效（浏览器会直接拒绝），
# 因此通配来源时关闭凭证；需要携带凭证时请通过 RS_CORS_ORIGINS 指定具体域名。
_allow_all_origins = settings.CORS_ORIGINS == ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=not _allow_all_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- 命令执行（WebSocket 与 REST 共用） ----------
async def execute_command(payload: Any) -> Dict[str, Any]:
    """
    统一命令入口：任何非法输入都返回结构化错误，不抛异常。
    校验逻辑在 models.Command / parse_command_params 中实现。
    """
    if simulator is None:
        return {"success": False, "message": "模拟器尚未初始化"}
    if not isinstance(payload, dict):
        return {"success": False, "message": "命令必须是 JSON 对象，例如 {\"action\": \"get_topology\"}"}
    return await simulator.handle_command(payload)


# ---------- WebSocket 端点 ----------
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """处理 WebSocket 连接"""
    await manager.connect(websocket)
    try:
        # 连接后首先发送当前拓扑快照
        if simulator:
            await websocket.send_json({
                "type": "topology_snapshot",
                "data": simulator.get_topology_snapshot(),
            })

        # 持续接收前端命令
        while True:
            raw_text = await websocket.receive_text()
            try:
                payload = json.loads(raw_text)
            except json.JSONDecodeError as exc:
                logger.warning("收到非法 JSON: %s", exc)
                await websocket.send_json({
                    "type": "error",
                    "data": {"message": f"消息不是合法 JSON: {exc}"},
                })
                continue

            action = payload.get("action") if isinstance(payload, dict) else None
            response = await execute_command(payload)
            await websocket.send_json({
                "type": "command_response",
                "action": action,
                "data": response,
            })
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as exc:  # 兜底：日志中保留完整堆栈，便于定位
        logger.exception("WebSocket 异常: %s", exc)
        manager.disconnect(websocket)


# ---------- 健康检查与指标 ----------
@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "simulator": "running" if simulator else "not_initialized",
        "nodes": len(simulator.topology.nodes) if simulator else 0,
        "links": len(simulator.topology.links) if simulator else 0,
    }


@app.get("/api/metrics")
async def get_metrics():
    """
    运行指标：最近/最大/平均路由重计算耗时、故障次数、分组收发统计。
    用于验证"故障后路由重新计算时间不超过 2 秒"这一指标（convergence_ms 口径 = 纯计算耗时）。
    """
    if simulator is None:
        return {"error": "Simulator not available"}
    metrics = simulator.get_metrics()
    metrics["requirement"] = {
        "max_convergence_ms": 2000,
        "satisfied": (metrics.get("max_convergence_ms") or 0) < 2000,
    }
    return metrics


# ---------- 可选的 REST API（供调试或非 WebSocket 客户端使用） ----------
@app.get("/api/topology")
async def get_topology():
    """获取当前拓扑快照（REST 方式）"""
    if simulator:
        return simulator.get_topology_snapshot()
    return {"error": "Simulator not available"}


@app.get("/api/topology/export")
async def export_topology():
    """导出拓扑配置文件（可用于固化实验拓扑，配合 RS_TOPOLOGY_FILE 复现）"""
    if simulator:
        return simulator.export_topology_config()
    return {"error": "Simulator not available"}


@app.post("/api/command")
async def execute_command_api(command: Dict[str, Any]):
    """通过 REST 执行命令（备用接口，与 WebSocket 走同一套校验）"""
    return await execute_command(command)


# ---------- 可选：直接托管前端页面（便于演示，避免 file:// 与跨域问题） ----------
_frontend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))
if os.path.isdir(_frontend_dir):
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
    logger.info("已挂载前端静态目录: %s", _frontend_dir)

# 如果直接运行此文件，启动 uvicorn
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.RELOAD,
    )
