# 课程设计报告（生成物与脚本）

本目录是《计算机与网络课程设计》报告的全部材料，**所有数字都由脚本实跑生成**，不手写。

## 文件说明

| 文件 | 说明 |
| --- | --- |
| `课程设计报告-王文聪-1120241345.docx` | 最终报告（由模板 + 正文源生成，含封面、成果简表、9 章正文、22 张表、10 张插图） |
| `report_source.md` | 报告正文源（Markdown）。**要改内容改这里**，再重新运行生成脚本 |
| `figures/` | 全部插图（拓扑、故障前后路径、架构、转发流程、度量口径、收敛分布、规模对比、真实界面截图） |
| `metrics.json` | 主指标：穷举 125 个单点故障、规模扩展、端到端、演示场景、测试结果、代码规模 |
| `metrics_baseline.json` | 优化**前**版本的规模-耗时（用于对比） |
| `metrics_current.json` | 优化**后**版本的规模-耗时（与 baseline 同脚本、同种子） |
| `scripts/measure_metrics.py` | 采集主指标 → `metrics.json` |
| `scripts/measure_scaling_generic.py` | 通用规模测量，`--backend` 可指向任意代码版本（用于优化前后对比） |
| `scripts/make_figures.py` | 由指标 JSON 绘制全部图表 |
| `scripts/capture_ui_screenshot.py` | 无头 Edge + CDP 驱动页面自动执行演示流程并截图（保证面板里有真实数据） |
| `scripts/build_docx.py` | 读取模板与正文源，生成最终 docx |

## 重新生成

```powershell
# 依赖（绘图与 docx 生成属于开发依赖）
..\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

cd D:\Project\IPV4\router-simulator
.\.venv\Scripts\python.exe report\scripts\measure_metrics.py        # 1. 采指标（约 25 s）
.\.venv\Scripts\python.exe report\scripts\make_figures.py           # 2. 画图
.\.venv\Scripts\python.exe report\scripts\capture_ui_screenshot.py  # 3. 真实界面截图（需 Edge/Chrome）
.\.venv\Scripts\python.exe report\scripts\build_docx.py             # 4. 生成 docx
```

对比优化前后（需要旧版本代码，例如 git worktree 检出早期提交）：

```powershell
git worktree add --detach ..\_baseline db561e7
.\.venv\Scripts\python.exe report\scripts\measure_scaling_generic.py --backend ..\_baseline\backend --label baseline --out report\metrics_baseline.json
git worktree remove --force ..\_baseline
```

## 提交前需要注意

1. 打开 docx 后按 `F9` 更新目录域（模板的目录是 Word 域，不会自动刷新）；
2. 标题样式自带**自动编号**，因此正文里的标题文本不带序号（`build_docx.py` 会自动去掉 `6.2.1`、`（1）` 之类前缀，避免出现"1 1 研究背景和意义"）；
3. 报告中仍留有待填写项，请按实际情况补全：
   - 封面「学生专业」；
   - 成果简表「难度系数」「人时数」「特殊加分项」，以及「特殊加分项类型」的勾选；
4. 模板文件 `计算机与网络课程设计-课程设计报告-模板.docx` 位于仓库根目录，`build_docx.py` 依赖它。