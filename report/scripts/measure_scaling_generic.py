# -*- coding: utf-8 -*-
"""
通用规模-收敛耗时测量脚本（用于对比优化前后的两个代码版本）。

用法：
    python measure_scaling_generic.py --backend <backend目录> --label baseline --out <输出json>

说明：
    优化前的版本 Simulator.__init__ 内部会调用 asyncio.create_task，
    因此必须在事件循环内构造；本脚本统一在 asyncio.run 中构造，两个版本都能跑。
    优化前版本用全局 random 生成拓扑，因此这里先 random.seed，保证两版拓扑一致。
"""

import argparse
import asyncio
import json
import pathlib
import random
import statistics
import sys
import time

NODE_COUNTS = [50, 60, 100, 150, 300, 400]
SEED = 20240915


def measure(backend_dir: str, label: str) -> dict:
    sys.path.insert(0, backend_dir)
    from network.simulator import Simulator

    results = []

    async def run() -> None:
        for node_count in NODE_COUNTS:
            random.seed(SEED)  # 优化前版本依赖全局随机数生成器
            try:
                simulator = Simulator(num_nodes=node_count, seed=SEED)  # 新版本接受 seed
            except TypeError:
                simulator = Simulator(num_nodes=node_count)  # 优化前版本无 seed 参数
            samples = []
            for _ in range(3):
                start = time.perf_counter()
                simulator.ospf.handle_topology_change()
                samples.append((time.perf_counter() - start) * 1000)
            results.append({
                "nodes": node_count,
                "links": len(simulator.topology.links),
                "convergence_ms": round(statistics.mean(samples), 1),
            })
            print(f"    {label}: {node_count:4d} 节点 / {results[-1]['links']:4d} 链路 -> {results[-1]['convergence_ms']} ms")

    asyncio.run(run())
    return {"label": label, "seed": SEED, "results": results}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    payload = measure(args.backend, args.label)
    pathlib.Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {args.out}")


if __name__ == "__main__":
    main()
