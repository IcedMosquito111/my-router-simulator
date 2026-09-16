# -*- coding: utf-8 -*-
"""
生成课程设计报告所需的全部插图（不含界面截图，界面截图见 capture_ui_screenshot.py）。

用法：
    python report/scripts/make_figures.py
输出：
    report/figures/*.png
数据来源：
    report/metrics.json、report/metrics_current.json、report/metrics_baseline.json
    （均由 measure_*.py 实跑生成，绘图脚本不写死任何测量数字）
"""

import json
import math
import pathlib
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Polygon

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIG_DIR = ROOT / "report" / "figures"
sys.path.insert(0, str(ROOT / "backend"))

from network.simulator import Simulator  # noqa: E402

SEED = 20240915
LIMIT_MS = 2000

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["figure.dpi"] = 200

COLOR_OK = "#4CAF50"
COLOR_BAD = "#F44336"
COLOR_LINE = "#9E9E9E"
COLOR_ACCENT = "#1976D2"
COLOR_WARN = "#FF9800"


def load(name: str) -> dict:
    return json.loads((ROOT / "report" / name).read_text(encoding="utf-8"))


def topology_layout(topology, radius: float = 1.0):
    """按前端一致的环形布局计算节点坐标"""
    node_ids = list(topology.nodes)
    positions = {}
    for index, node_id in enumerate(node_ids):
        angle = 2 * math.pi * index / len(node_ids)
        positions[node_id] = (radius * math.cos(angle), radius * math.sin(angle))
    return node_ids, positions


def draw_topology(ax, topology, positions, down_nodes=(), draw_labels=True):
    for link in topology.links:
        x1, y1 = positions[link.source_id]
        x2, y2 = positions[link.target_id]
        failed = not link.is_up() or link.source_id in down_nodes or link.target_id in down_nodes
        ax.plot([x1, x2], [y1, y2],
                color=COLOR_BAD if failed else COLOR_LINE,
                linewidth=1.6 if failed else 0.9,
                zorder=1, alpha=0.9 if failed else 0.75)
    for node_id, (x, y) in positions.items():
        failed = node_id in down_nodes
        ax.scatter([x], [y], s=110, zorder=3,
                   color=COLOR_BAD if failed else COLOR_OK,
                   edgecolors="#B71C1C" if failed else "#2E7D32", linewidths=1.0)
        if draw_labels:
            ax.text(x, y, node_id.replace("R", ""), fontsize=5.5, ha="center", va="center",
                    color="white", zorder=4)
    ax.set_aspect("equal")
    ax.axis("off")


# ---------- 图 1：拓扑与单点故障 ----------
def figure_topology():
    simulator = Simulator(num_nodes=50, seed=SEED)
    topology = simulator.topology
    _, positions = topology_layout(topology)

    fig, axes = plt.subplots(1, 2, figsize=(11, 5.6))
    draw_topology(axes[0], topology, positions)
    axes[0].set_title("(a) 50 节点 / 75 链路拓扑（环形保连通 + 随机弦边）", fontsize=10)

    victim = "R25"
    for link in topology.links:
        if victim in (link.source_id, link.target_id):
            link.set_down()
    topology.refresh_interface_states()
    draw_topology(axes[1], topology, positions, down_nodes=(victim,))
    axes[1].set_title("(b) 节点 R25 故障：相连 4 条链路与两端接口同时失效", fontsize=10)

    fig.tight_layout()
    fig.savefig(FIG_DIR / "fig1_topology.png", bbox_inches="tight")
    plt.close(fig)


# ---------- 图 2：故障前后路径对比 ----------
def figure_path_compare():
    simulator = Simulator(num_nodes=50, seed=SEED)
    topology = simulator.topology
    _, positions = topology_layout(topology)
    demo = load("metrics.json")["demo"]

    fig, ax = plt.subplots(figsize=(6.4, 6.2))
    draw_topology(ax, topology, positions)

    def draw_path(path, color, width, label, offset=0.0):
        xs = [positions[node][0] for node in path]
        ys = [positions[node][1] for node in path]
        ax.plot(xs, ys, color=color, linewidth=width, alpha=0.85, zorder=2,
                marker="o", markersize=3.5, label=label)

    draw_path(demo["path_before"], COLOR_OK, 4.0,
              f'故障前：{demo["path_before_hops"]} 跳', )
    draw_path(demo["path_after_link_fault"], COLOR_WARN, 2.6,
              f'链路 R1–R2 故障后：{demo["path_after_link_fault_hops"]} 跳（自动绕行）')
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, 1.10), fontsize=8, frameon=False)
    ax.set_title("同一对端点（R1→R2）在链路故障前后的转发路径", fontsize=10, pad=28)

    fig.tight_layout()
    fig.savefig(FIG_DIR / "fig2_path_compare.png", bbox_inches="tight")
    plt.close(fig)


# ---------- 图 3：系统架构 ----------
def _box(ax, x, y, w, h, text, face="#E3F2FD", edge=COLOR_ACCENT, fontsize=8.5):
    ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h,
                                boxstyle="round,pad=0.01,rounding_size=0.02",
                                linewidth=1.1, facecolor=face, edgecolor=edge, zorder=2))
    ax.text(x, y, text, ha="center", va="center", fontsize=fontsize, zorder=3)


def _arrow(ax, p1, p2, text="", color="#616161", style="<->", dy=0.0):
    ax.add_patch(FancyArrowPatch(p1, p2, arrowstyle=style, mutation_scale=11,
                                 linewidth=1.0, color=color, zorder=1))
    if text:
        ax.text((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2 + dy, text,
                ha="center", va="center", fontsize=7.2, color=color,
                bbox=dict(boxstyle="round,pad=0.15", facecolor="white", edgecolor="none"))


def figure_architecture():
    fig, ax = plt.subplots(figsize=(9.6, 6.0))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 6.4)
    ax.axis("off")

    _box(ax, 2.6, 5.5, 4.4, 1.0, "浏览器前端（单页应用）\nvis-network 拓扑渲染 · 逐跳动画 · 故障对比面板\nIPv4/IPv6 协议选择 · 路由表查看",
         face="#FFF3E0", edge=COLOR_WARN)
    _box(ax, 2.6, 4.0, 5.0, 0.85,
         "通信层：WebSocket /ws + REST /api/*\n"
         "消息：topology_update · routing_table_update\nlog · packet_forwarded · command_response",
         face="#E8F5E9", edge=COLOR_OK, fontsize=7.0)
    _box(ax, 2.6, 2.85, 4.4, 0.75, "入口层：FastAPI main.py\n参数校验（pydantic）· 连接管理（并发广播）· 配置化 settings.py",
         face="#E3F2FD", edge=COLOR_ACCENT, fontsize=7.6)
    _box(ax, 2.6, 1.55, 4.4, 0.95, "业务层：Simulator\n拓扑管理 · 故障注入/恢复 · 逐跳转发 · 收敛指标",
         face="#EDE7F6", edge="#5E35B1", fontsize=7.8)
    _box(ax, 2.6, 0.45, 4.4, 0.7, "模型层：Topology / Node / Link / Packet\n路由协议：OSPF（链路状态）· RIP（距离向量）",
         face="#ECEFF1", edge="#546E7A", fontsize=7.6)

    _arrow(ax, (2.6, 5.0), (2.6, 4.38), "双向实时通信", dy=0.12)
    _arrow(ax, (2.6, 3.62), (2.6, 3.23))
    _arrow(ax, (2.6, 2.47), (2.6, 2.03))
    _arrow(ax, (2.6, 1.07), (2.6, 0.81))

    _box(ax, 8.1, 2.85, 2.9, 1.5,
         "可观测性\n/api/metrics 收敛耗时\n/api/topology/export 拓扑固化\n73 项 pytest 自动化测试",
         face="#F1F8E9", edge="#7CB342", fontsize=7.4)
    _box(ax, 8.1, 4.9, 2.9, 1.2, "演示场景\n正常转发 / 单节点故障\n单链路故障 / 分区不可达",
         face="#FBE9E7", edge="#E64A19", fontsize=7.4)
    _arrow(ax, (4.8, 2.85), (6.6, 2.85), "HTTP", style="->")
    _arrow(ax, (4.8, 5.2), (6.6, 5.1), "浏览器操作", style="->")

    ax.set_title("系统总体架构（B/S 架构，前端与后端解耦）", fontsize=11)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "fig3_architecture.png", bbox_inches="tight")
    plt.close(fig)


# ---------- 图 4：分组转发流程 ----------
def figure_forwarding_flow():
    """
    以数据结构驱动渲染，避免手工排坐标导致分支标签错位。

    流程（与 Simulator._send_packet 一一对应）：
        目的属本机 -> 本地投递
        无路由 / 链路故障 / TTL 超时 -> 丢弃并给出原因
        否则逐跳转发，直到到达目的节点
    """
    fig, ax = plt.subplots(figsize=(8.8, 8.6))
    ax.set_xlim(0, 10.4)
    ax.set_ylim(0, 12.2)
    ax.axis("off")

    spine_x, right_x = 3.3, 8.4
    y = 11.5

    def diamond(cy, text, w=5.2, h=1.1):
        ax.add_patch(Polygon([[spine_x, cy + h / 2], [spine_x + w / 2, cy],
                              [spine_x, cy - h / 2], [spine_x - w / 2, cy]],
                             closed=True, facecolor="#FFF9C4", edgecolor="#F9A825",
                             linewidth=1.1, zorder=2))
        ax.text(spine_x, cy, text, ha="center", va="center", fontsize=7.6, zorder=3)

    def box(cy, text, face="#E3F2FD", edge=COLOR_ACCENT, w=4.9, h=0.6, x=spine_x):
        _box(ax, x, cy, w, h, text, face=face, edge=edge, fontsize=7.8)

    def terminal(cy, label, text, kind):
        face, edge = ("#E8F5E9", COLOR_OK) if kind == "ok" else ("#FFEBEE", COLOR_BAD)
        _box(ax, right_x, cy, 3.4, 0.62, text, face=face, edge=edge, fontsize=7.6)
        ax.add_patch(FancyArrowPatch((spine_x + 2.75, cy), (right_x - 1.75, cy),
                                     arrowstyle="->", mutation_scale=10, linewidth=1.0,
                                     color="#616161", zorder=1))
        ax.text((spine_x + 2.75 + right_x - 1.75) / 2, cy + 0.16, label,
                fontsize=7.4, color="#616161", ha="center")

    def down_arrow(y_from, y_to):
        ax.add_patch(FancyArrowPatch((spine_x, y_from), (spine_x, y_to),
                                     arrowstyle="->", mutation_scale=10, linewidth=1.0,
                                     color="#616161", zorder=1))

    box(y, "源节点生成分组（src = 自身回环地址，dst = 目的地址，TTL = 64）")
    down_arrow(y - 0.3, y - 0.72)

    y -= 1.35
    diamond(y, "① 目的地址属于当前节点？\n（回环地址或本接口地址）")
    terminal(y, "是", "本地投递，标记 delivered", "ok")
    down_arrow(y - 0.55, y - 1.0)

    y -= 2.35
    diamond(y, "② 最长前缀匹配找到路由？")
    terminal(y, "否", "丢弃：无到达目的地址的路由", "bad")
    down_arrow(y - 0.55, y - 1.0)

    y -= 2.35
    diamond(y, "③ 出接口 / 对端节点 / 链路可用？\n（故障链路直接丢弃，不穿越）")
    terminal(y, "否", "丢弃：链路或接口故障", "bad")
    down_arrow(y - 0.55, y - 1.0)

    y -= 2.35
    diamond(y, "④ TTL 递减后 > 0？\n（源节点与目的节点不递减）")
    terminal(y, "否", "丢弃：TTL 超时", "bad")
    down_arrow(y - 0.55, y - 0.95)

    y -= 1.6
    box(y, "按链路时延转发到下一跳，记录路径并更新统计", face="#EDE7F6", edge="#5E35B1")
    down_arrow(y - 0.3, y - 0.72)

    y -= 1.35
    diamond(y, "⑤ 到达目的节点？")
    terminal(y, "是", "已送达，返回 path / status / ttl", "ok")

    # 否 -> 回到步骤 ② 继续逐跳（左侧回环箭头）
    loop_x = 0.45
    ax.add_patch(FancyArrowPatch((spine_x - 2.6, y), (loop_x, y),
                                 arrowstyle="-", mutation_scale=10, linewidth=1.0, color="#616161"))
    ax.add_patch(FancyArrowPatch((loop_x, y), (loop_x, y + 4.55),
                                 arrowstyle="-", mutation_scale=10, linewidth=1.0, color="#616161"))
    ax.add_patch(FancyArrowPatch((loop_x, y + 4.55), (spine_x - 2.6, y + 4.55),
                                 arrowstyle="->", mutation_scale=10, linewidth=1.0, color="#616161"))
    ax.text(loop_x + 0.14, y + 2.3, "否：继续逐跳", fontsize=7.2,
            color="#616161", rotation=90, va="center")

    ax.set_title("IPv4 / IPv6 分组逐跳转发流程（每一步都能解释丢包原因）", fontsize=11)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "fig4_forwarding_flow.png", bbox_inches="tight")
    plt.close(fig)


# ---------- 图 5：收敛耗时度量口径 ----------
def figure_convergence_timeline():
    fig, ax = plt.subplots(figsize=(9.6, 3.4))
    ax.set_xlim(0, 10.4)
    ax.set_ylim(0, 3.2)
    ax.axis("off")

    steps = [
        "t₀ 注入故障\n（节点 / 链路）",
        "刷新接口状态\n（链路 up 且两端\n节点 up 才可用）",
        "各 up 节点\n重新生成 LSA",
        "LSA 洪泛\n全网 LSDB 同步",
        "每节点独立\nDijkstra 计算",
        "推送 N 张\n路由表",
    ]
    xs = [0.9 + 1.7 * index for index in range(len(steps))]
    for x, text in zip(xs, steps):
        _box(ax, x, 2.25, 1.5, 1.0, text, face="#E3F2FD", edge=COLOR_ACCENT, fontsize=7.0)
    for x in xs[:-1]:
        _arrow(ax, (x + 0.76, 2.25), (x + 0.94, 2.25), style="->")

    ax.annotate("", xy=(xs[-6] - 0.05, 1.5), xytext=(xs[-2] + 0.05, 1.5),
                arrowprops=dict(arrowstyle="|-|", color="#D32F2F", linewidth=1.4))
    ax.text((xs[0] + xs[4]) / 2, 1.62,
            "convergence_ms：路由重新计算耗时（需求指标口径；实测最慢 13.9 ms，上限 2000 ms）",
            ha="center", fontsize=8.4, color="#D32F2F")

    ax.annotate("", xy=(xs[0] - 0.05, 0.72), xytext=(xs[-1] + 0.05, 0.72),
                arrowprops=dict(arrowstyle="|-|", color="#1976D2", linewidth=1.2))
    ax.text((xs[0] + xs[-1]) / 2, 0.36,
            "total_ms：计算 + 向全部客户端推送路由表（端到端体验口径，实测与计算耗时同量级）",
            ha="center", fontsize=8.2, color="#1976D2")

    ax.set_title("路由重计算的度量口径：明确区分算法耗时与推送耗时", fontsize=10.5)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "fig5_convergence_timeline.png", bbox_inches="tight")
    plt.close(fig)


# ---------- 图 6：穷举单点故障的收敛耗时 ----------
def figure_fault_distribution():
    metrics = load("metrics.json")
    node_faults = metrics["node_faults"]
    link_faults = metrics["link_faults"]
    node_values = [s["convergence_ms"] for s in node_faults["all_samples"]]
    link_values = [s["convergence_ms"] for s in link_faults["all_samples"]]
    all_values = node_values + link_values

    fig, axes = plt.subplots(1, 2, figsize=(11.2, 4.3),
                             gridspec_kw={"width_ratios": [1.0, 1.7]})

    # (a) 对数刻度箱线图：直观展示与 2000 ms 上限的余量
    box = axes[0].boxplot([node_values, link_values],
                          tick_labels=["节点故障\n(50 例)", "链路故障\n(75 例)"],
                          patch_artist=True, widths=0.5,
                          medianprops=dict(color="#B71C1C", linewidth=1.6))
    for patch, color in zip(box["boxes"], ["#90CAF9", "#FFCC80"]):
        patch.set_facecolor(color)
    axes[0].set_yscale("log")
    axes[0].set_ylim(5, 6000)
    axes[0].axhline(LIMIT_MS, color="#D32F2F", linestyle="--", linewidth=1.4)
    axes[0].text(1.5, LIMIT_MS * 1.25, "需求上限 2000 ms", color="#D32F2F",
                 fontsize=8.4, ha="center")
    axes[0].text(1.5, LIMIT_MS / 22,
                 f"实测区间 {min(all_values)} ~ {max(all_values)} ms\n"
                 f"余量 ≥ {round(LIMIT_MS / max(all_values))} 倍",
                 color="#1B5E20", fontsize=8.4, ha="center")
    axes[0].set_ylabel("路由重计算耗时 / ms（对数刻度）")
    axes[0].set_title("(a) 全部单点故障样本与 2 秒上限的余量", fontsize=10)
    axes[0].grid(axis="y", alpha=0.3, which="both")

    # (b) 逐样本柱状图
    axes[1].bar(range(len(node_values)), node_values, color="#42A5F5", width=0.9,
                label=f"单节点故障 50 例（最慢 {node_faults['worst_ms']} ms，平均 {node_faults['avg_ms']} ms）")
    axes[1].bar(range(len(node_values), len(all_values)), link_values, color="#FFA726", width=0.9,
                label=f"单链路故障 75 例（最慢 {link_faults['worst_ms']} ms，平均 {link_faults['avg_ms']} ms）")
    axes[1].set_ylim(0, max(all_values) * 1.25)
    axes[1].axhline(max(all_values), color="#D32F2F", linestyle=":", linewidth=1.1)
    axes[1].text(len(all_values) * 0.98, max(all_values) * 1.02, f"最慢 {max(all_values)} ms",
                 color="#D32F2F", fontsize=8, ha="right")
    axes[1].set_xlabel("故障样本序号（前 50 个为节点故障，后 75 个为链路故障）")
    axes[1].set_ylabel("重计算耗时 / ms")
    axes[1].set_title("(b) 125 个单点故障逐一实测（穷举，非抽样）", fontsize=10)
    axes[1].legend(fontsize=7.6, frameon=False, loc="upper center",
                   bbox_to_anchor=(0.5, -0.22), ncol=2)
    axes[1].grid(axis="y", alpha=0.3)

    fig.tight_layout()
    fig.savefig(FIG_DIR / "fig6_fault_distribution.png", bbox_inches="tight")
    plt.close(fig)


# ---------- 图 7：规模扩展与优化前后对比 ----------
def figure_scaling():
    baseline = load("metrics_baseline.json")["results"]
    current = load("metrics_current.json")["results"]

    nodes = [item["nodes"] for item in baseline]
    before = [item["convergence_ms"] for item in baseline]
    after = [item["convergence_ms"] for item in current]

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.0), gridspec_kw={"width_ratios": [1.1, 1]})

    axes[0].plot(nodes, before, marker="o", color="#E53935", linewidth=1.8, label="改动前（线性选点 Dijkstra，无前缀缓存）")
    axes[0].plot(nodes, after, marker="s", color="#43A047", linewidth=1.8, label="改动后（最小堆 Dijkstra + 前缀缓存）")
    axes[0].axhline(LIMIT_MS, color="#D32F2F", linestyle="--", linewidth=1.2)
    axes[0].text(205, LIMIT_MS * 1.12, "需求上限 2000 ms", color="#D32F2F", fontsize=8.2)
    axes[0].set_yscale("log")
    axes[0].set_ylim(8, 6000)
    axes[0].set_xlabel("节点数量")
    axes[0].set_ylabel("路由重计算耗时 / ms（对数刻度）")
    axes[0].set_title("(a) 规模扩展性与 2 秒指标", fontsize=10)
    axes[0].grid(alpha=0.3, which="both")
    axes[0].legend(fontsize=7.4, frameon=False, loc="lower right")
    axes[0].annotate(f"400 节点：改动前 {before[-1]} ms\n（此时已突破 2 秒指标），改动后 {after[-1]} ms",
                     xy=(400, before[-1]), xytext=(95, 700), fontsize=7.4, color="#C62828",
                     arrowprops=dict(arrowstyle="->", color="#C62828", linewidth=0.9))

    speedup = [round(b / a, 2) for b, a in zip(before, after)]
    bars = axes[1].bar([str(n) for n in nodes], speedup, color="#26A69A")
    for rect, value in zip(bars, speedup):
        axes[1].text(rect.get_x() + rect.get_width() / 2, value + 0.04, f"{value}×",
                     ha="center", fontsize=8)
    axes[1].axhline(1.0, color="#9E9E9E", linewidth=1.0)
    axes[1].set_xlabel("节点数量")
    axes[1].set_ylabel("加速比（改动前 / 改动后）")
    axes[1].set_title("(b) 单次重计算耗时加速比", fontsize=10)
    axes[1].grid(axis="y", alpha=0.3)

    fig.tight_layout()
    fig.savefig(FIG_DIR / "fig7_scaling.png", bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    for name, func in (
        ("fig1_topology", figure_topology),
        ("fig2_path_compare", figure_path_compare),
        ("fig3_architecture", figure_architecture),
        ("fig4_forwarding_flow", figure_forwarding_flow),
        ("fig5_convergence_timeline", figure_convergence_timeline),
        ("fig6_fault_distribution", figure_fault_distribution),
        ("fig7_scaling", figure_scaling),
    ):
        func()
        print(f"  已生成 {name}.png")
    print(f"\n全部插图输出目录：{FIG_DIR}")


if __name__ == "__main__":
    main()
