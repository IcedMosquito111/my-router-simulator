# 课程设计报告（生成物与脚本）

本目录是《计算机与网络课程设计》报告的全部材料，**所有数字都由脚本实跑生成**，不手写。

## 文件说明

| 文件 | 说明 |
| --- | --- |
| `课程设计报告.docx` | 最终报告（由模板 + 正文源生成，含封面、成果简表、9 章正文、22 张表、10 张插图） |
| `report_source.md` | 报告正文源（Markdown）。**要改内容改这里**，再重新运行生成脚本 |
| `课程学习体会-王文聪-1120241345.docx` | 课程学习体会（按学校体会模板生成：页眉标题 + 学号/姓名/签字/日期表 + 正文 9 段） |
| `reflection_source.md` | 学习体会正文源 |
| `答辩准备.md` | 答辩速记卡：关键数字、技术优点与证据、不足清单、预判问题与 30 秒回答、准备清单 |
| `figures/` | 全部插图（拓扑、故障前后路径、架构、转发流程、度量口径、收敛分布、规模对比、真实界面截图） |
| `metrics.json` | 主指标：穷举 125 个单点故障、规模扩展、端到端、演示场景、测试结果、代码规模 |
| `metrics_baseline.json` | 优化**前**版本的规模-耗时（用于对比） |
| `metrics_current.json` | 优化**后**版本的规模-耗时（与 baseline 同脚本、同种子） |
| `scripts/measure_metrics.py` | 采集主指标 → `metrics.json` |
| `scripts/measure_scaling_generic.py` | 通用规模测量，`--backend` 可指向任意代码版本（用于优化前后对比） |
| `scripts/make_figures.py` | 由指标 JSON 绘制全部图表 |
| `scripts/capture_ui_screenshot.py` | 无头 Edge + CDP 驱动页面自动执行演示流程并截图（保证面板里有真实数据） |
| `scripts/build_docx.py` | 读取模板与正文源生成报告 docx（自动处理标题序号与 `**加粗**`；覆盖前自动备份 `.bak`） |
| `scripts/build_reflection_docx.py` | 读取体会模板与体会正文源，生成学习体会 docx |

## 重新生成

```powershell
# 依赖（绘图与 docx 生成属于开发依赖）
..\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

cd D:\Project\IPV4\router-simulator
.\.venv\Scripts\python.exe report\scripts\measure_metrics.py        # 1. 采指标（约 25 s）
.\.venv\Scripts\python.exe report\scripts\make_figures.py           # 2. 画图
.\.venv\Scripts\python.exe report\scripts\capture_ui_screenshot.py  # 3. 真实界面截图（需 Edge/Chrome）
.\.venv\Scripts\python.exe report\scripts\build_docx.py             # 4. 生成报告 docx
.\\.venv\Scripts\python.exe report\scripts\build_reflection_docx.py   # 5. 生成学习体会 docx
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
3. 报告中仍需本人确认的项：
   - 成果简表「特殊加分项类型」的勾选与「特殊加分项」说明（其余项已填：专业=电子信息工程、
     难度系数=0.9、人时数=52 人时、自编代码行数=4877 行）；
4. 模板文件 `计算机与网络课程设计-课程设计报告-模板.docx` 位于仓库根目录，`build_docx.py` 依赖它。

注意：
- 报告 docx 会被生成脚本覆盖（覆盖前自动生成 `.docx.bak`）。**文字修改请改 `report_source.md`**，
  只在 Word 里改的内容在下次生成时会丢失；
- 多人协作时，正文源里的“学生姓名/学号”以“、”分隔填写两位同学，生成脚本会分别写入封面表两列。
