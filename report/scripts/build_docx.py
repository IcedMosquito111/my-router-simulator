# -*- coding: utf-8 -*-
"""
按课程模板生成课程设计报告 DOCX。

输入：
    计算机与网络课程设计-课程设计报告-模板.docx   （学校模板，只用它的封面表、成果简表与样式）
    report/report_source.md                        （报告正文源，含 [[FIG:文件名|图注]] 图片占位）
    report/figures/*.png                           （插图）
输出：
    report/课程设计报告-王文聪-1120241345.docx

用法：
    python report/scripts/build_docx.py

注意：
    模板的 Heading 1/2/3 样式自带自动编号（numPr），因此写入标题时**不能**再带序号，
    否则会出现“1 1 研究背景和意义”这类重复编号。
"""

import pathlib
import re
import sys

import shutil

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Emu, Inches, Pt

ROOT = pathlib.Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "计算机与网络课程设计-课程设计报告-模板.docx"
SOURCE = ROOT / "report" / "report_source.md"
FIG_DIR = ROOT / "report" / "figures"
OUTPUT = ROOT / "report" / "课程设计报告.docx"   # 与手动保存的文件名保持一致；覆盖前会自动备份 .bak

BODY_FONT_SIZE = Pt(11)      # 正文小四略小，保证表格与图注不过度占版面
TABLE_FONT_SIZE = Pt(9)
CAPTION_FONT_SIZE = Pt(9)
HEADING_NUMBER_PREFIX = re.compile(r"^\d+(\.\d+)*\s*")      # 去掉 “6.2.1 ” 这类前缀
HEADING_PAREN_PREFIX = re.compile(r"^[（(]\d+[）)]\s*")   # 去掉 “（1）” 这类前缀


# ---------- 解析 Markdown 源 ----------
def parse_source(text: str):
    """把正文源解析为块序列：('h1'|'h2'|'h3'|'p'|'bullet'|'table'|'figure', payload)"""
    blocks = []
    lines = text.splitlines()
    index = 0
    cover, summary = {}, {}
    current_dict = None

    while index < len(lines):
        raw = lines[index]
        line = raw.strip()

        if line.startswith("# "):
            title = line[2:].strip()
            current_dict = {"封面信息": cover, "成果简表": summary}.get(title)
            blocks.append(("h1", title))
            index += 1
            continue
        if line.startswith("## "):
            blocks.append(("h2", line[3:].strip()))
            index += 1
            continue
        if line.startswith("### "):
            blocks.append(("h3", line[4:].strip()))
            index += 1
            continue
        if line.startswith("[[FIG:"):
            match = re.match(r"\[\[FIG:([^|\]]+)\|([^\]]+)\]\]", line)
            blocks.append(("figure", (match.group(1).strip(), match.group(2).strip())))
            index += 1
            continue
        if line.startswith("|"):
            rows = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                cells = [c.strip() for c in lines[index].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                    rows.append(cells)
                index += 1
            blocks.append(("table", rows))
            continue
        if line.startswith("- "):
            blocks.append(("bullet", line[2:].strip()))
            index += 1
            continue
        if re.match(r"^\d+\.\s", line):
            blocks.append(("bullet", line))
            index += 1
            continue
        if not line:
            index += 1
            continue
        if current_dict is not None and "：" in line:
            key, value = line.split("：", 1)
            current_dict[key.strip()] = value.strip()
            index += 1
            continue
        blocks.append(("p", line))
        index += 1

    return cover, summary, blocks


# ---------- 填充封面与成果简表 ----------
def fill_cover(document: Document, cover: dict) -> None:
    table = document.tables[0]
    rows = table.rows
    rows[0].cells[1].text = cover.get("课题名称", "")
    for row_index, key in ((1, "学生姓名"), (2, "学生学号"), (3, "学生专业")):
        cells = rows[row_index].cells
        # 支持多人课题：以“、”分隔的第 2 位同学写入第 3 列
        values = [item.strip() for item in cover.get(key, "").split("、")]
        cells[1].text = values[0] if values else ""
        cells[2].text = values[1] if len(values) > 1 else ""
    for cell in rows[0].cells:
        for paragraph in cell.paragraphs:
            for run in paragraph.runs:
                run.font.size = Pt(12)


def fill_summary(document: Document, summary: dict) -> None:
    table = document.tables[1]
    keys = ["课题名称", "难度系数", "人时数", "自编代码行数", "基线是否完成", "基线功能和性能",
            "提升是否完成", "提升功能和性能", "特殊加分项类型", "特殊加分项"]
    for offset, key in enumerate(keys, start=1):
        if offset >= len(table.rows):
            break
        cell = table.rows[offset].cells[2]
        cell.text = summary.get(key, "")
        for paragraph in cell.paragraphs:
            for run in paragraph.runs:
                run.font.size = Pt(9)


# ---------- 正文写入辅助 ----------
def add_body_paragraph(document: Document, text: str, indent: bool = True, bold: bool = False):
    """
    写入一段正文，并处理 Markdown 的 **加粗** 标记。

    注意：早期版本把 ** 原样写进 Word（用户在 Word 里才手动清掉），
    这里改为把 ** 之间的内容生成为真正的加粗 run。
    """
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.line_spacing = 1.4
    for index, chunk in enumerate(text.split("**")):
        if not chunk:
            continue
        run = paragraph.add_run(chunk)
        run.font.size = BODY_FONT_SIZE
        run.bold = bold or (index % 2 == 1)
    if indent:
        paragraph.paragraph_format.first_line_indent = Pt(22)
    paragraph.paragraph_format.space_after = Pt(4)
    return paragraph


def add_bullet(document: Document, text: str, numbered: bool = False):
    """列表项：编号列表保持原文（1. xxx），无序列表加“· ”前缀并悬挂缩进"""
    paragraph = document.add_paragraph()
    prefix = "" if numbered else "· "
    for index, chunk in enumerate(text.split("**")):
        if not chunk:
            continue
        run = paragraph.add_run((prefix if index == 0 else "") + chunk)
        run.font.size = BODY_FONT_SIZE
        run.bold = index % 2 == 1
    paragraph.paragraph_format.left_indent = Pt(20)
    paragraph.paragraph_format.space_after = Pt(2)
    return paragraph


def add_table(document: Document, rows):
    if not rows:
        return None
    table = document.add_table(rows=len(rows), cols=len(rows[0]))
    table.style = "Table Grid"
    for row_index, row in enumerate(rows):
        for col_index, value in enumerate(row):
            cell = table.cell(row_index, col_index)
            cell.text = value
            for paragraph in cell.paragraphs:
                paragraph.paragraph_format.space_after = Pt(0)
                for run in paragraph.runs:
                    run.font.size = TABLE_FONT_SIZE
                    run.bold = row_index == 0
    document.add_paragraph()
    return table


def add_figure(document: Document, filename: str, caption: str, max_width_inch: float):
    path = FIG_DIR / filename
    if not path.is_file():
        add_body_paragraph(document, f"[缺图: {filename}]", indent=False)
        return
    paragraph = document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.add_run().add_picture(str(path), width=Inches(max_width_inch))
    caption_paragraph = document.add_paragraph()
    caption_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = caption_paragraph.add_run(caption)
    run.font.size = CAPTION_FONT_SIZE
    caption_paragraph.paragraph_format.space_after = Pt(8)


def clear_template_body(document: Document, first_heading_text: str = "研究背景和意义") -> None:
    """移除模板中正文部分的占位段落（保留封面、成果简表与目录）"""
    paragraphs = document.paragraphs
    start = None
    for index, paragraph in enumerate(paragraphs):
        if paragraph.style.name == "Heading 1" and paragraph.text.strip() == first_heading_text:
            start = index
            break
    if start is None:
        raise RuntimeError("模板中未找到正文起始标题")
    for paragraph in paragraphs[start:]:
        paragraph._element.getparent().remove(paragraph._element)


def available_width_inch(document: Document) -> float:
    section = document.sections[0]
    # Length 相减得到普通 int（EMU），需要显式包回 Emu 才能拿到英寸
    width = int(section.page_width) - int(section.left_margin) - int(section.right_margin)
    return round(Emu(width).inches, 2)


def main() -> None:
    if not TEMPLATE.is_file():
        raise SystemExit(f"未找到模板：{TEMPLATE}")
    cover, summary, blocks = parse_source(SOURCE.read_text(encoding="utf-8-sig"))

    document = Document(str(TEMPLATE))
    fill_cover(document, cover)
    fill_summary(document, summary)
    clear_template_body(document)

    width = available_width_inch(document)
    figure_width = min(width - 0.1, 6.0)
    in_appendix = False

    for kind, payload in blocks:
        if kind == "h1":
            if payload in ("封面信息", "成果简表"):
                continue
            in_appendix = payload.endswith("附录")
            document.add_paragraph(HEADING_NUMBER_PREFIX.sub("", payload, count=1), style="Heading 1")
        elif kind == "h2":
            title = HEADING_NUMBER_PREFIX.sub("", payload, count=1)
            if in_appendix:
                add_body_paragraph(document, payload, indent=False, bold=True)
            else:
                document.add_paragraph(title, style="Heading 2")
        elif kind == "h3":
            title = HEADING_NUMBER_PREFIX.sub("", payload, count=1)
            title = HEADING_PAREN_PREFIX.sub("", title, count=1)
            document.add_paragraph(title, style="Heading 3")
        elif kind == "p":
            add_body_paragraph(document, payload)
        elif kind == "bullet":
            add_bullet(document, payload, numbered=bool(re.match(r"^\d+\.\s", payload)))
        elif kind == "table":
            add_table(document, payload)
        elif kind == "figure":
            filename, caption = payload
            add_figure(document, filename, caption, figure_width)

    if OUTPUT.exists():      # 覆盖前自动备份，避免手改内容丢失
        backup = OUTPUT.with_suffix(".docx.bak")
        shutil.copy2(OUTPUT, backup)
        print(f"已备份原文件：{backup}")
    document.save(str(OUTPUT))
    print(f"报告已生成：{OUTPUT}")
    print(f"  图片宽度：{figure_width} 英寸（版心宽度 {width} 英寸）")


if __name__ == "__main__":
    main()
