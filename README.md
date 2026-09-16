# IPv4 分组转发与路由选择模拟系统

基于 **FastAPI + WebSocket + vis-network** 的 IPv4/IPv6 分组转发与路由选择模拟器：
支持 50+ 节点拓扑、OSPF（链路状态）与 RIP（距离向量）路由计算、逐跳分组的可视化转发、
节点/链路故障注入与路由重新收敛，并提供可复现的性能指标。

---

## 1. 需求对照

| 课程需求 | 实现位置 | 验证方式 |
| --- | --- | --- |
| 模拟 Internet 路由交换过程，实现一种常用路由算法 | `backend/network/routing/ospf.py`（LSA 生成 → 洪泛 → 每节点 Dijkstra）、`backend/network/routing/rip.py`（距离向量 / Bellman-Ford） | `tests/test_routing.py`（与**独立实现**的 Dijkstra/BFS 逐条对照 4900 条路由） |
| 演示节点故障时的路由选择 | `Simulator._inject_fault / _recover_fault`、前端"故障注入 / 故障恢复 / 故障前后路径对比 / 收敛耗时"面板 | `tests/test_topology_state.py`、`tests/test_fault_convergence.py` |
| B/S 架构前后端 | 后端 FastAPI（WebSocket `/ws` + REST `/api/*`），前端单页 `frontend/index.html`（vis-network 拓扑渲染、逐跳动画） | `tests/test_ws_integration.py`（真实 uvicorn + 真实 WebSocket 客户端） |
| 支持不少于 50 个网络节点 | `Simulator`（随机可复现拓扑：环形保连通 + 随机弦边），或 `RS_TOPOLOGY_FILE` 加载固定拓扑 | 默认即 50 节点 / 75 链路；`tests/test_fault_convergence.py` 另测 60/100/150 节点 |
| 单点故障后路由重计算 < 2 秒 | OSPF 全量重算（最小堆 Dijkstra + 前缀解析缓存） | **穷举** 50 个单点故障与 75 条链路故障，断言 `convergence_ms < 2000` |

### 实测数据（本机 Python 3.14，50 节点 / 75 链路）

| 场景 | 最慢一次 `convergence_ms` | 平均 |
| --- | --- | --- |
| 穷举 50 个单节点故障 | **18.1 ms** | 12.9 ms |
| 穷举 75 条单链路故障 | **17.6 ms** | 13.6 ms |

规模扩展（同机）：`N=60 → 18 ms`、`N=100 → 51 ms`、`N=150 → 121 ms`、`N=300 → 494 ms`。
即：50 节点规模下相对 2 秒指标约有 **100 倍余量**。

### IPv6 支持范围（附加实现，需求本身只要求 IPv4）

| 能力 | 后端 | 前端 |
| --- | --- | --- |
| 地址分配（节点回环 `/32`+`/128`、链路 `/30`+`/127`） | ✅ | — |
| 路由计算与最长前缀匹配（v4/v6 各一套路由） | ✅ | — |
| 逐跳转发（本机投递、直连投递、TTL 语义） | ✅ | ✅ 协议选择器 IPv4/IPv6 |
| 故障注入后的重路由 | ✅ | ✅ 路径面板显示协议族 |
| 路由表展示 | ✅ | ✅ 按 IPv4/IPv6 分别统计条数 |

**未实现（避免夸大）**：NDP/邻居发现、SLAAC 地址自动配置、ICMPv6 差错报文、
IPv6 扩展报头/分片/流量类别、IPv4↔IPv6 过渡机制（本项目是双栈并行，没有 NAT64/隧道）。
地址统一使用 `2001:db8::/32` 文档前缀。

---

## 2. 目录结构

```
router-simulator/
├── backend/
│   ├── main.py                     # FastAPI 入口：WebSocket /ws、REST /api/*、前端静态托管
│   ├── settings.py                 # 运行配置（全部走环境变量）
│   ├── models.py                   # 数据模型 + 入参校验（pydantic）
│   ├── utils.py                    # IP 生成/校验等辅助函数
│   └── network/
│       ├── simulator.py            # 模拟器核心：拓扑、故障注入、逐跳转发、指标
│       ├── topology.py             # 拓扑与"链路/接口状态"的单一真相
│       ├── node.py                 # 路由器节点：接口、LSDB、路由表、Dijkstra、最长前缀匹配
│       ├── link.py                 # 链路
│       ├── packet.py               # 分组（TTL、路径、丢弃原因）
│       └── routing/{ospf.py,rip.py}# 路由协议
├── frontend/index.html             # 单页前端（vis-network 拓扑 + 逐跳动画 + 面板）
├── tests/                          # pytest 测试（73 项）
├── report/                         # 课程设计报告：正文源、图表、指标数据与生成脚本
│   ├── report_source.md            # 报告正文源（改这里后重新生成 docx）
│   ├── 课程设计报告-*.docx          # 按学校模板生成的报告
│   ├── metrics*.json               # 实跑指标（含优化前后对比）
│   ├── figures/*.png               # 报告插图（含真实界面截图）
│   └── scripts/                    # 指标采集 / 绘图 / 截图 / 生成 docx
├── requirements.txt / requirements-dev.txt
└── pytest.ini
```

数据流：`前端命令 →(WebSocket)→ main.execute_command → models 校验 → Simulator →
拓扑/路由变化 → 回调广播（topology_update / routing_table_update / log / packet_forwarded）→ 前端渲染`。

---

## 3. 快速开始

```powershell
# 1) 依赖
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt      # 运行依赖
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt  # 开发/测试依赖

# 2) 启动（后端同时托管前端，浏览器打开 http://localhost:8000/ 即可）
cd backend
..\.venv\Scripts\python.exe main.py
```

也可以用 `uvicorn main:app --port 8000`（`cd backend` 后执行），或单独用浏览器打开 `frontend/index.html`
（此时前端会回退到 `ws://localhost:8000/ws` 连接后端）。

### 前端操作（课堂演示用）

1. 「选择源节点」→ 点一个节点；「选择目的节点」→ 再点一个节点；
2. 选择「协议」为 IPv4 或 IPv6（默认 IPv4），点「发送分组」即可看到逐跳动画；
3. 也可在「目的地址」输入框直接填任意地址（如 `2001:db8:ffff::29`、`10.255.0.50`，
   或故意填一个不可达地址观察丢包原因）——填 IPv6 地址时会自动切换协议族；
4. 点任意节点可查看其路由表（表头含 IPv4/IPv6 条数统计）；
5. 「故障注入 / 故障恢复」+ 点击节点或链路，即可观察故障前/后路径与收敛耗时。

### 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `RS_NUM_NODES` | `50` | 随机拓扑节点数（≥2） |
| `RS_TOPOLOGY_FILE` | 空 | 拓扑 JSON 路径（提供后忽略 `RS_NUM_NODES`） |
| `RS_SEED` | 空 | 随机种子，固定后拓扑与实验可复现 |
| `RS_HOST` / `RS_PORT` | `0.0.0.0` / `8000` | 监听地址与端口 |
| `RS_CORS_ORIGINS` | `*` | 跨域白名单（逗号分隔）；通配时自动关闭 `allow_credentials` |
| `RS_LOG_LEVEL` | `INFO` | 日志级别 |
| `RS_DEBUG_LOGS` | `0` | 置 1 打开逐节点调试日志（LSA 洪泛、路由表更新） |
| `RS_RELOAD` | `0` | 置 1 开启热重载（仅开发） |

示例：`$env:RS_SEED=20240915; ..\.venv\Scripts\python.exe main.py`（复现同一拓扑与实验）。

---

## 4. 接口协议

### WebSocket `/ws`

客户端订阅的消息类型：

| 类型 | 说明 |
| --- | --- |
| `topology_snapshot` / `topology_update` | 拓扑快照（连接时）与拓扑变化 |
| `routing_table_update` | 单个节点的路由表（一次故障注入会推送 N-1～N 条） |
| `log` | 运行日志（`level`: info/warning/error/debug） |
| `packet_forwarded` | 分组转发结果（`path`、`ttl`、`status`、`drop_reason`） |
| `command_response` | 命令响应（`action` + `data.success/message/data`） |
| `error` | 协议层错误（例如消息不是合法 JSON） |

客户端发送的命令（`{"action": ..., "params": {...}}`）：

| action | 参数 | 说明 |
| --- | --- | --- |
| `get_topology` | 无 | 获取拓扑快照 |
| `get_routing_table` | `node_id` | 获取某节点路由表 |
| `send_packet` | `src_node`、`dst_ip`、`protocol?`、`ttl?`(1~255，默认 64) | 逐跳转发一个分组 |
| `inject_fault` | `type`=`node`/`link`；节点用 `id`，链路用 `source`+`target` | 注入故障并重新收敛 |
| `recover_fault` | 同上 | 恢复故障并重新收敛 |

**所有入参都经 pydantic 校验**：非法 IP、缺失参数、超长 ID、未知命令等一律返回
`{"success": false, "message": "参数校验失败：..."}`，**不会断开客户端连接**。

### REST

| 路径 | 说明 |
| --- | --- |
| `GET /health` | 健康检查（节点/链路数） |
| `GET /api/metrics` | 运行指标：最近/最大/平均收敛耗时、故障次数、分组收发统计，并直接给出 `requirement.satisfied` |
| `GET /api/topology` | 拓扑快照 |
| `GET /api/topology/export` | 导出拓扑配置 JSON（配合 `RS_TOPOLOGY_FILE` 固化实验拓扑） |
| `POST /api/command` | REST 方式执行命令（与 WebSocket 同一套校验） |

---

## 5. 关键设计说明

**1）接口/链路状态的单一真相**
所有接口状态由 `Topology.refresh_interface_states()` 依据「链路状态 + 两端节点状态」派生：
`接口 up ⇔ 链路 up 且两端节点 up`。任何故障注入/恢复之后都会刷新，
因此不会出现"链路已断但接口仍 up"，也不会有路由指向死链路。
对外快照中 `link.status` 表示**有效状态**（能否承载流量），`link.admin_status` 保留链路自身状态。

**2）转发语义**

- 目的地址属于本机（回环地址 / 自身接口地址）→ **本地投递**（源节点 ping 自己不会丢包）；
- 路由 `next_hop="direct"`（直连网段）→ 从出接口**直接发给对端**（ping 直连邻居的接口地址不再丢包）；
- 其他 → 按下一跳地址转发，并校验下一跳确实位于出接口网段内；
- 每一步都校验**出接口、对端节点、链路**的可用性，链路故障时明确丢弃并给出原因；
- TTL 只在中转路由器处递减（源主机与目的节点不递减）：路径 h 跳消耗 h-1，最小可用初始 TTL 为 h。

**3）路由计算**
`Node.run_dijkstra()` 使用最小堆实现的 Dijkstra（`heapq`），目的前缀取目的节点回环地址（`/32`、`/128`）；
前缀解析结果做全局缓存（同一条目一次同步中会被解析 N 次），
`lookup_route()` 基于预编译前缀对象做最长前缀匹配，不再每次重新解析字符串。

**4）可观测性**
`Simulator.metrics` 记录样本化的收敛耗时（最近 50 次）、故障次数、分组收发统计，
`convergence_ms` = 纯路由重计算耗时（指标口径），`total_ms` = 计算 + 向所有客户端推送全部路由表的耗时。

**5）容错**
命令在 `handle_command` 内被兜底 `try/except` 包裹，任何内部异常都会转成错误响应；
故障注入/恢复用 `asyncio.Lock` 串行化，多客户端并发注入不会交错。

---

## 6. 测试

```powershell
.\.venv\Scripts\python.exe -m pytest              # 全部 73 项，约 11 秒
.\.venv\Scripts\python.exe -m pytest -m "not slow" # 跳过需要启动真实服务的集成测试
```

| 测试文件 | 覆盖内容 |
| --- | --- |
| `tests/helpers.py` | **独立实现**的 Dijkstra/BFS 与"无黑洞/无过期路由/路由完备性"不变量检查 |
| `tests/test_topology_state.py` | 节点/链路故障后的状态一致性、故障+恢复后不"复活"死链路、网络分区行为 |
| `tests/test_routing.py` | 4900 条路由与独立最短路逐条对照、本机/直连路由、JSON 可序列化、RIP 与 BFS 跳数一致 |
| `tests/test_forwarding.py` | 本机投递、直连投递、路径最优且无环、TTL 语义、丢弃原因、IPv6 转发 |
| `tests/test_fault_convergence.py` | **穷举** 50 个节点故障 + 75 条链路故障断言 < 2 秒；扩展性（60/100/150 节点）；拓扑可复现性 |
| `tests/test_command_validation.py` | 34 项非法输入（数字 IP、非对象 params、超长 ID、未知命令…）均返回错误且无副作用 |
| `tests/test_ws_integration.py` | 真实 uvicorn + WebSocket：非法命令不断连、IPv6 转发与协议校验、49 张路由表推送、`/api/metrics`、拓扑导入导出往返、托管前端可用性 |
| `tests/test_frontend_assets.py` | 前端 IPv6 入口（协议选择器、从节点数据解析地址、不再硬拼 IPv4）与内联 JS 语法检查（本机有 node 时执行） |

---

## 7. 修复与新增记录（含修复前实测证据）

| # | 问题（修复前实测） | 修复 |
| --- | --- | --- |
| 1 | 链路故障 + 节点故障/恢复后，节点接管已断链路，报文"穿过"死链路仍报 `delivered` | 接口状态改为派生量并统一刷新；转发每跳校验出接口/对端/链路（回归测试 `test_recovering_node_does_not_resurrect_failed_link`） |
| 2 | 节点故障后，相连链路在快照里仍为 `up`，前端出现"红节点 + 正常链路" | 快照输出链路**有效状态**，新增 `admin_status` 保留链路自身状态 |
| 3 | ping 自己的回环地址、ping 直连邻居接口地址都被丢弃 | 本机路由（`local`）+ 直连投递分支 |
| 4 | `{"dst_ip": 123}` 抛 `TypeError`、`params: "oops"` 抛 `AttributeError` → 客户端连接被断开 | 接入 pydantic 校验（`models.py` 此前从未被引用），非法输入只返回错误响应 |
| 5 | `Simulator.__init__` 里 `asyncio.create_task`，同步构造直接 `RuntimeError: no running event loop`，初始路由表推送与握手竞争 | `__init__` 只做纯计算，新增 `async start()`；lifespan 显式 `await` |
| 6 | 回环地址解析用 `Node` 类级全局回调，同进程多实例互相污染 | 改为实例级注入（`run_dijkstra(loopback_of=...)`） |
| 7 | 规模增大后重计算时间迅速劣化：400 节点 2321.6 ms（`min()` 线性选点 + 每次重新解析前缀），已突破 2 秒指标 | 最小堆 Dijkstra + 前缀解析缓存：400 节点 2321.6 ms→832.4 ms（2.79×），300 节点 1103.0 ms→446.0 ms（2.47×），50 节点 14.2 ms→11.1 ms（同一脚本、同一随机种子、各重复 3 次） |
| 8 | TTL 在源节点也被递减（路径 h 跳消耗 h 个 TTL），与真实路由器行为不符 | TTL 只在中转路由器递减 |
| 9 | 前端写死 `ws://localhost:8000/ws`、断线不重连、一次故障刷 49 条日志 | 地址自适应（含 `file://` 兜底）、指数退避重连、路由表更新日志聚合、展示 `drop_reason` |
| 10 | 无 README/依赖锁定/测试；`CORS: allow_origins=["*"] + allow_credentials=True` 矛盾 | 补齐文档与依赖、加入测试、按通配来源自动关闭凭证、配置全部环境变量化 |
| 11 | **新增：前端 IPv6 支持**。此前前端只能发 IPv4（`parseInt(nodeId)` 硬拼 `10.255.x.y`），IPv6 仅后端/API 可用 | 拓扑快照带出回环地址；前端加协议选择器（IPv4/IPv6）、地址改为从节点数据解析、支持手输任意目的地址；路由表按协议族统计；手输 IPv6 时前端自动纠正协议族，后端仍会拒绝版本不一致的请求 |

---

## 8. 报告与图表（`report/`）

课程设计报告由脚本按学校模板生成，全部数字来自实跑，不手写：

| 文件 | 说明 |
| --- | --- |
| `report/课程设计报告-王文聪-1120241345.docx` | 按模板生成的报告（封面、成果简表、9 个章节、22 张表、10 张插图） |
| `report/report_source.md` | 报告正文源（想改内容改这里，再重新生成 docx） |
| `report/figures/*.png` | 插图：拓扑、故障前后路径、架构、转发流程、度量口径、收敛分布、规模对比、真实界面截图 |
| `report/metrics.json` | 全部定量指标（穷举单点故障、规模扩展、端到端、测试结果） |
| `report/metrics_baseline.json` / `metrics_current.json` | 优化前 / 优化后的规模-耗时对比（同一脚本测量两个版本） |
| `report/scripts/measure_metrics.py` | 采集指标 → `metrics.json` |
| `report/scripts/measure_scaling_generic.py` | 通用规模测量（可用 `--backend` 指向任意版本，用于对比） |
| `report/scripts/make_figures.py` | 由指标数据绘制全部图表 |
| `report/scripts/capture_ui_screenshot.py` | 用无头 Edge + CDP 驱动页面自动演示并截图 |
| `report/scripts/build_docx.py` | 按模板填充生成报告 docx |

重新生成（需要 `pip install -r requirements-dev.txt`）：

```powershell
.\.venv\Scripts\python.exe report\scripts\measure_metrics.py       # 采指标
.\.venv\Scripts\python.exe report\scripts\make_figures.py          # 画图
.\.venv\Scripts\python.exe report\scripts\capture_ui_screenshot.py  # 界面截图
.\.venv\Scripts\python.exe report\scripts\build_docx.py            # 生成 docx
```

注意：报告模板的标题样式自带自动编号，因此正文标题**不带序号**；打开 docx 后按 `F9` 更新目录域。
报告中仍留有待填写项（封面"学生专业"、成果简表的"难度系数/人时数/特殊加分项"），请按实际情况补全。

## 9. 已知限制与后续计划

- **RIP 尚未接入前端**：`routing/rip.py` 已可用且有测试（与 BFS 跳数一致），但模拟器默认只用 OSPF；
  后续可做成 UI 可切换的 OSPF/RIP 对比（RIP 采用跳数度量，忽略链路 cost，未实现水平分割/路由毒化）。
- **LSA 洪泛为同步全网广播**：`OSPF.sync_all()` 一次性完成洪泛与计算（因此是"毫秒级收敛"而非真实协议时序），
  后续可加入逐跳洪泛动画与老化/序列号冲突场景。
- **逐跳转发使用真实 `asyncio.sleep(链路延迟)`**：长路径会阻塞该条命令 100ms 级；后续可改为虚拟时钟/倍速播放。
- **全局单例模拟器**：多客户端共享同一拓扑（存在互相影响），后续可按会话隔离或做只读/可写权限区分。
- **前端为单文件 32KB 内联 JS/CSS**：未做模块化与构建，`vis-network` 仍来自 CDN（离线演示需自行本地化）。
- **无认证与限流**：WebSocket/REST 未鉴权，故障注入未限流，仅适合教学演示环境。
- **IPv6 为双栈附加实现**：不含 NDP/SLAAC/ICMPv6/扩展报头与过渡机制，详见第 1 节"IPv6 支持范围"。