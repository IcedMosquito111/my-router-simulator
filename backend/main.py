"""
基于 Python 的路由模拟系统 - 后端主入口
使用 FastAPI 提供 WebSocket 服务，与前端实时通信
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Dict, Set, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from network.simulator import Simulator

# ---------- 日志配置 ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("router-simulator")

# ---------- 全局模拟器实例 ----------
simulator = None

# ---------- WebSocket 连接管理 ----------
class ConnectionManager:
    """管理所有活跃的 WebSocket 连接"""
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info(f"New client connected: {websocket.client}")

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)
        logger.info(f"Client disconnected: {websocket.client}")

    async def broadcast(self, message: dict):
        """向所有连接的客户端广播消息"""
        dead_connections = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                dead_connections.append(connection)
        for conn in dead_connections:
            self.disconnect(conn)

manager = ConnectionManager()

# ---------- 模拟器事件回调 ----------
async def on_topology_update(snapshot: dict):
    """拓扑更新回调，推送给前端"""
    await manager.broadcast({
        "type": "topology_update",
        "data": snapshot
    })

async def on_routing_table_update(update: dict):
    """路由表更新回调"""
    await manager.broadcast({
        "type": "routing_table_update",
        "data": update
    })

async def on_log_message(log_msg: dict):
    """日志消息回调"""
    await manager.broadcast({
        "type": "log",
        "data": log_msg
    })

async def on_packet_forwarded(packet_info: dict):
    """分组转发完成回调"""
    await manager.broadcast({
        "type": "packet_forwarded",
        "data": packet_info
    })

# ---------- FastAPI 生命周期 ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动/关闭时管理模拟器生命周期"""
    global simulator
    logger.info("Starting router simulator backend...")

    # 初始化模拟器，传入回调函数
    simulator = Simulator(
        num_nodes=50,   # 节点数量
        callbacks={
            "topology_update": on_topology_update,
            "routing_table_update": on_routing_table_update,
            "log_message": on_log_message,
            "packet_forwarded": on_packet_forwarded,
        }
    )
    logger.info("Simulator initialized.")

    yield  # 应用运行期间

    # 关闭时清理
    logger.info("Shutting down simulator...")
    # 这里可以添加清理逻辑

# 创建 FastAPI 应用
app = FastAPI(
    title="路由模拟系统后端",
    description="基于 Python 的路由模拟系统 API",
    version="0.1.0",
    lifespan=lifespan
)

# 配置 CORS（允许前端跨域访问）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应限制为具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- WebSocket 端点 ----------
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """处理 WebSocket 连接"""
    await manager.connect(websocket)
    try:
        # 连接后首先发送当前拓扑快照
        if simulator:
            snapshot = simulator.get_topology_snapshot()
            await websocket.send_json({
                "type": "topology_snapshot",
                "data": snapshot
            })

        # 持续接收前端命令
        while True:
            data = await websocket.receive_json()
            logger.debug(f"Received command: {data}")

            # 调用模拟器处理命令
            if simulator:
                response = await simulator.handle_command(data)
                if response:
                    await websocket.send_json({
                        "type": "command_response",
                        "data": response
                    })
            else:
                await websocket.send_json({
                    "type": "error",
                    "data": {"message": "Simulator not initialized"}
                })
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)

# ---------- 健康检查端点 ----------
@app.get("/health")
async def health_check():
    return {"status": "ok", "simulator": "running" if simulator else "not_initialized"}

# ---------- 可选的 REST API（供调试或非 WebSocket 客户端使用） ----------
@app.get("/api/topology")
async def get_topology():
    """获取当前拓扑快照（REST 方式）"""
    if simulator:
        return simulator.get_topology_snapshot()
    return {"error": "Simulator not available"}

@app.post("/api/command")
async def execute_command(command: dict):
    """通过 REST 执行命令（备用接口）"""
    if simulator:
        result = await simulator.handle_command(command)
        return result
    return {"error": "Simulator not available"}

# 如果直接运行此文件，启动 uvicorn
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )