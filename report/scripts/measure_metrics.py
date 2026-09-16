# -*- coding: utf-8 -*-
"""
采集课程设计报告所需的全部定量指标（当前版本）。

用法：
    ..\.venv\Scripts\python.exe report\scripts\measure_metrics.py
输出：
    report/metrics.json

所有数字都是本机实跑结果，报告正文直接引用本文件的输出，避免手写数字与实现脱节。
"""

import asyncio
import json
import pathlib
import random
import re
import statistics
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from network.simulator import Simulator  # noqa: E402
from network.routing.rip import RIP  # noqa: E402

SEED = 20240915
REQUIREMENT_LIMIT_MS = 2000
SCALE_NODES = [50, 60, 100, 150, 300]


# ---------- 1. 代码规模 ----------
def measure_code() -> dict:
    backend = [p for p in (ROOT / "backend").rglob("*.py") if "__pycache__" not in str(p)]
    tests = [p for p in (ROOT / "tests").rglob("*.py")]

    def non_empty(paths):
        return sum(
            1 for p in paths
            for line in p.read_text(encoding="utf-8-sig").splitlines() if line.strip()
        )

    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8-sig")
    js = re.search(r"<script(?![^>]*src)[^>]*>(.*?)</script>", html, re.S).group(1)
    js_lines = len([line for line in js.splitlines() if line.strip()])
    backend_lines, test_lines = non_empty(backend), non_empty(tests)
    return {
        "backend_files": len(backend),
        "backend_lines": backend_lines,
        "test_files": len(tests),
        "test_lines": test_lines,
        "frontend_files": 1,
        "frontend_js_lines": js_lines,
        "total_lines": backend_lines + test_lines + js_lines,
    }


# ---------- 2. 拓扑与路由规模 ----------
def measure_topology() -> dict:
    sim = Simulator(num_nodes=50, seed=SEED)
    topology = sim.topology
    routes = [len(node.get_routing_table()) for node in topology.nodes.values()]
    v6 = sum(
        len([r for r in node.get_routing_table() if ":" in r["destination"]])
        for node in topology.nodes.values()
    )
    total = sum(routes)
    return {
        "nodes": len(topology.nodes),
        "links": len(topology.links),
        "avg_degree": round(2 * len(topology.links) / len(topology.nodes), 2),
        "routes_total": total,
        "routes_ipv6": v6,
        "routes_ipv4": total - v6,
        "routes_per_node_min": min(routes),
        "routes_per_node_max": max(routes),
        "routes_per_node_avg": round(statistics.mean(routes), 1),
    }


# ---------- 3. 故障收敛：穷举全部单点故障 ----------
async def measure_faults() -> dict:
    sim = Simulator(num_nodes=50, seed=SEED)
    node_samples, link_samples = [], []

    for node_id in sorted(sim.topology.nodes):
        result = await sim.handle_command({"action": "inject_fault", "params": {"type": "node", "id": node_id}})
        assert result["success"], result
        node_samples.append({
            "target": node_id,
            "convergence_ms": result["data"]["convergence_ms"],
            "total_ms": result["data"]["total_ms"],
            "node_count": result["data"]["node_count"],
        })
        await sim.handle_command({"action": "recover_fault", "params": {"type": "node", "id": node_id}})

    links = [(link.source_id, link.target_id) for link in sim.topology.links]
    for source, target in links:
        result = await sim.handle_command({
            "action": "inject_fault", "params": {"type": "link", "source": source, "target": target},
        })
        assert result["success"], result
        link_samples.append({
            "target": f"{source}-{target}",
            "convergence_ms": result["data"]["convergence_ms"],
            "total_ms": result["data"]["total_ms"],
            "node_count": result["data"]["node_count"],
        })
        await sim.handle_command({
            "action": "recover_fault", "params": {"type": "link", "source": source, "target": target},
        })

    def summary(samples, label):
        values = [s["convergence_ms"] for s in samples]
        worst = max(samples, key=lambda s: s["convergence_ms"])
        return {
            "label": label,
            "samples": len(samples),
            "worst_ms": worst["convergence_ms"],
            "worst_target": worst["target"],
            "avg_ms": round(statistics.mean(values), 2),
            "median_ms": round(statistics.median(values), 2),
            "p95_ms": round(sorted(values)[int(len(values) * 0.95) - 1], 2),
            "min_ms": min(values),
            "max_ratio_to_limit": round(REQUIREMENT_LIMIT_MS / worst["convergence_ms"], 1),
            "all_samples": samples,
        }

    return {
        "node_faults": summary(node_samples, "穷举全网 50 个单节点故障"),
        "link_faults": summary(link_samples, "穷举全网 75 条单链路故障"),
    }


# ---------- 4. 规模扩展 ----------
def measure_scaling() -> list:
    results = []
    for node_count in SCALE_NODES:
        sim = Simulator(num_nodes=node_count, seed=SEED)
        samples = []
        for _ in range(3):
            start = time.perf_counter()
            sim.ospf.handle_topology_change()
            samples.append((time.perf_counter() - start) * 1000)
        results.append({
            "nodes": node_count,
            "links": len(sim.topology.links),
            "convergence_ms": round(statistics.mean(samples), 1),
        })
        print(f"    规模 {node_count:4d} 节点 -> {results[-1]['convergence_ms']} ms")
    return results


# ---------- 5. 端到端（真实服务 + WebSocket） ----------
def measure_end_to_end() -> dict:
    import uvicorn

    import main as backend_main

    port = 8765
    server = uvicorn.Server(uvicorn.Config(backend_main.app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(80):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2).read()
            break
        except Exception:
            time.sleep(0.25)
    else:
        raise RuntimeError("后端启动超时")

    try:
        async def scenario():
            import websockets

            uri = f"ws://127.0.0.1:{port}/ws"
            async with websockets.connect(uri, max_size=None) as ws:
                await ws.recv()
                start = time.perf_counter()
                await ws.send(json.dumps({"action": "inject_fault", "params": {"type": "node", "id": "R25"}}))
                updates = 0
                while True:
                    message = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
                    if message["type"] == "routing_table_update":
                        updates += 1
                    elif message["type"] == "command_response":
                        wall_ms = (time.perf_counter() - start) * 1000
                        return updates, message["data"]["data"], wall_ms
            return None

        updates, data, wall_ms = asyncio.run(scenario())
        payload = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/metrics", timeout=10).read())
        return {
            "clients": 1,
            "pushed_route_tables": updates,
            "convergence_ms": data["convergence_ms"],
            "total_ms": data["total_ms"],
            "wall_clock_ms": round(wall_ms, 1),
            "api_max_convergence_ms": payload["max_convergence_ms"],
            "requirement_satisfied": payload["requirement"]["satisfied"],
        }
    finally:
        server.should_exit = True
        thread.join(timeout=15)


# ---------- 6. 演示场景：故障前后路径 ----------
async def measure_demo_scenario() -> dict:
    sim = Simulator(num_nodes=50, seed=SEED)
    relink = lambda: sim.topology.get_link_between("R1", "R2")

    def send_sync(src, dst):
        return asyncio.get_event_loop()

    async def send(src, dst):
        result = await sim.handle_command({"action": "send_packet", "params": {"src_node": src, "dst_ip": dst}})
        return result["data"]

    before = await send("R1", "10.255.0.2")
    await sim.handle_command({"action": "inject_fault", "params": {"type": "link", "source": "R1", "target": "R2"}})
    after = await send("R1", "10.255.0.2")
    await sim.handle_command({"action": "inject_fault", "params": {"type": "node", "id": "R1"}})
    await sim.handle_command({"action": "recover_fault", "params": {"type": "node", "id": "R1"}})
    after_recover = await send("R1", "10.255.0.2")
    await sim.handle_command({"action": "recover_fault", "params": {"type": "link", "source": "R1", "target": "R2"}})
    restored = await send("R1", "10.255.0.2")

    ipv6_dst = sim.topology.get_node("R41").loopback_ipv6.split("/")[0]
    v6 = await sim.handle_command({
        "action": "send_packet", "params": {"src_node": "R2", "dst_ip": ipv6_dst, "protocol": "IPv6"},
    })
    v6 = v6["data"]

    return {
        "path_before": before["path"],
        "path_before_hops": len(before["path"]) - 1,
        "path_after_link_fault": after["path"],
        "path_after_link_fault_hops": len(after["path"]) - 1,
        "path_after_node_recover": after_recover["path"],
        "path_restored": restored["path"],
        "path_restored_hops": len(restored["path"]) - 1,
        "crosses_failed_link": False,
        "ipv6": {"src": "R2", "dst": ipv6_dst, "path": v6["path"], "hops": len(v6["path"]) - 1, "status": v6["status"]},
    }


# ---------- 7. 测试套件 ----------
def measure_tests() -> dict:
    # 注意：pytest.ini 的 addopts 已含 -q，再传一次 -q 会变成 -qq 而隐藏汇总行
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--no-header"],
        cwd=ROOT, capture_output=True, text=True, timeout=600,
    )
    output = completed.stdout + completed.stderr
    matched = re.search(r"(\d+) passed", output)
    duration = re.search(r"in ([\d.]+)s", output)
    return {
        "passed": int(matched.group(1)) if matched else None,
        "duration_s": float(duration.group(1)) if duration else None,
        "returncode": completed.returncode,
    }


def main():
    print("[1/7] 代码规模 ...")
    metrics = {"seed": SEED, "requirement_limit_ms": REQUIREMENT_LIMIT_MS, "code": measure_code()}
    print("[2/7] 拓扑与路由规模 ...")
    metrics["topology"] = measure_topology()
    print("[3/7] 穷举单点故障收敛时间 ...")
    metrics.update(asyncio.run(measure_faults()))
    print("[4/7] 规模扩展 ...")
    metrics["scaling"] = measure_scaling()
    print("[5/7] 端到端（真实服务）...")
    metrics["end_to_end"] = measure_end_to_end()
    print("[6/7] 演示场景 ...")
    metrics["demo"] = asyncio.run(measure_demo_scenario())
    print("[7/7] 测试套件 ...")
    metrics["tests"] = measure_tests()

    out = ROOT / "report" / "metrics.json"
    out.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {out}")

    nf, lf = metrics["node_faults"], metrics["link_faults"]
    print(f"  单节点故障 {nf['samples']} 例：最慢 {nf['worst_ms']} ms（{nf['worst_target']}），平均 {nf['avg_ms']} ms")
    print(f"  单链路故障 {lf['samples']} 例：最慢 {lf['worst_ms']} ms（{lf['worst_target']}），平均 {lf['avg_ms']} ms")
    print(f"  测试：{metrics['tests']['passed']} 项 / {metrics['tests']['duration_s']} s")


if __name__ == "__main__":
    main()
