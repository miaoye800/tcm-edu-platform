from __future__ import annotations

from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "deliverables" / "profile_audit_confirmation_2026-07-15.docx"


AUDIT_ROWS = [
    (
        "1",
        "已实现",
        "锚点已集中到区间常量表；规则兜底取区间中点，三师评审按区间内具体分打分，guardrail 按区间边界钳制。",
        "backend/app/profile/anchors.py:13,102; fusion.py:502-513; jury.py:16,272; guardrail.py:102; tests/test_scoring.py:400,448",
    ),
    (
        "2",
        "已实现",
        "同维度、tier=self_report、语义同质自述第 n 次出现按 0.5^(n-1) 衰减。",
        "backend/app/profile/fusion.py:101,516-533; backend/tests/test_scoring.py:384",
    ),
    (
        "3",
        "已实现",
        "对话加速系数 1.5 仅作用于 learning_goal / motivation；其余维度仍走全局 K=3.0。",
        "backend/app/profile/modes.py:133,138,954-955; backend/tests/test_scoring.py:388",
    ),
    (
        "4",
        "已实现",
        "首次从无分到有分时强制触发一次三师评审，绕过阈值与 24h 节流；无 key 时安全退化为规则融合区间中点。",
        "backend/app/profile/update.py:35,71,622; backend/tests/test_scoring.py:36,463-474",
    ),
    (
        "5",
        "已实现",
        "师父信任系数已乘进 self_report 实际融合权重，不只是落表；trust 下降导致自述影响减弱有单测。",
        "backend/app/profile/fusion.py:516-533; backend/app/profile/update.py:90-96; backend/tests/test_calibration.py:132-136",
    ),
    (
        "6",
        "已实现",
        "interaction 已细分 chat_message / resource_choice / skip_or_retry / dwell_time；旧调用方不带 subtype 时保持兼容。",
        "backend/app/profile/update.py:38,353,360,385,392-393",
    ),
    (
        "7",
        "已实现",
        "/goal/confirm 会写入 learning_goal self_report 证据卡并进入原融合链路；未确认 GoalAgent 时 planning_params 由七维现状推导。",
        "backend/app/api/profile.py:75-76,212-259; backend/app/profile/schema.py:150-190; backend/tests/test_goal_agent.py:114,124-148",
    ),
    (
        "8",
        "部分实现",
        "claim refuted 后可换卡，已揭示卡验证后不回退；但未见“小岐”反驳话术文案。",
        "frontend/src/components/profile/profileDialogueStreamState.ts:149-158; frontend/tests/profile-dialogue.component.spec.mjs:463-473",
    ),
    (
        "9",
        "部分实现 -> 本次补齐",
        "原先已触发 submitCalibration，但未处理结算返回。本次补上静默结算后的画像刷新与叙事刷新。",
        "frontend/src/views/QuizView.vue:46,51,60-82; frontend/src/services/profileApi.ts:32-43",
    ),
]


CHANGE_ROWS = [
    (
        "A",
        "Elo 快照去堆积",
        "融合 knowledge_base 时，仅保留 timestamp 最新的一张 Elo measured 快照参与数值融合，避免历史快照平均拖住新分数；新增连续作答跟随最新 rating 的单测。",
        "backend/app/profile/fusion.py:266,318-335; backend/tests/test_ability.py:78-92",
    ),
    (
        "B",
        "goal confirm 候选校验",
        "确认了 confirm 侧已通过 GoalConfirmRequest(candidate: GoalAgentResult) 做 Pydantic 嵌套校验；补了非法枚举 / 越界数值返回 422 的接口单测。",
        "backend/app/api/profile.py:75-76,212-220; backend/tests/test_goal_agent.py:124-148",
    ),
    (
        "C",
        "校准题提交断链修补",
        "前端在正常答题流后按 claim 分组静默提交 /api/profile/calibration/submit，verified / refuted 时刷新画像与叙事；同时移除 QuizView 中 topicInput.value 的“麻黄”硬编码回退，改为空值校验。",
        "frontend/src/views/QuizView.vue:51,60-82; frontend/src/services/profileApi.ts:32-43",
    ),
]


GAP_ROWS = [
    (
        "8",
        "refuted 后的小岐话术未实现",
        "目前只有换卡与已揭示锁定，没有“看来你的底子和你说的有些出入”这类差异提示。预计改动面：ProfileDialogue.vue 或对应文案映射文件 + 1 条组件断言。",
        "小到中，1 个前端组件 + 1 组文案分支 + 1 个测试。",
    ),
]


TEST_ROWS = [
    ("后端定向新增/相关测试", "通过", "pytest tests/test_ability.py tests/test_goal_agent.py -q -> 14 passed in 55.91s"),
    ("前端构建", "通过", "npm run build 通过"),
    ("前端测试", "通过", "npm test 通过"),
    ("后端全量测试", "未完成", "pytest -q 两次均超时；第二次约 10 分钟后超时，未取得全绿结论"),
]


def set_cell_shading(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)


def set_cell_width(cell, inches: float) -> None:
    width = Inches(inches)
    cell.width = width
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_w = tc_pr.first_child_found_in("w:tcW")
    if tc_w is None:
        tc_w = OxmlElement("w:tcW")
        tc_pr.append(tc_w)
    tc_w.set(qn("w:type"), "dxa")
    tc_w.set(qn("w:w"), str(int(width.inches * 1440)))


def set_table_layout_fixed(table) -> None:
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    table.autofit = False
    tbl_pr = table._tbl.tblPr
    tbl_layout = tbl_pr.first_child_found_in("w:tblLayout")
    if tbl_layout is None:
        tbl_layout = OxmlElement("w:tblLayout")
        tbl_pr.append(tbl_layout)
    tbl_layout.set(qn("w:type"), "fixed")


def apply_font(run, *, size: int, bold: bool = False, color: str = "000000") -> None:
    run.font.name = "Calibri"
    run._element.rPr.rFonts.set(qn("w:ascii"), "Calibri")
    run._element.rPr.rFonts.set(qn("w:hAnsi"), "Calibri")
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = RGBColor.from_string(color)


def style_paragraph(paragraph, *, after: int = 6, before: int = 0, line: float = 1.1, align=WD_ALIGN_PARAGRAPH.LEFT) -> None:
    fmt = paragraph.paragraph_format
    fmt.space_after = Pt(after)
    fmt.space_before = Pt(before)
    fmt.line_spacing = line
    paragraph.alignment = align


def add_title(doc: Document) -> None:
    p = doc.add_paragraph()
    style_paragraph(p, after=4, line=1.0)
    run = p.add_run("画像模块五会话改造核对与局部修补确认记录")
    apply_font(run, size=22, bold=True, color="2E74B5")

    meta = doc.add_paragraph()
    style_paragraph(meta, after=10, line=1.0)
    run = meta.add_run(f"仓库: qihuang-zhixue    日期: {date(2026, 7, 15).isoformat()}    范围: 只覆盖本轮核对与修补确认内容")
    apply_font(run, size=10, color="555555")


def add_intro(doc: Document) -> None:
    p = doc.add_paragraph()
    style_paragraph(p, after=8)
    run = p.add_run(
        "本记录区分两类信息：一类是已在代码与测试中确认存在的实现状态；另一类是本轮实际补做的修补项。文内所有结论均附文件与关键行号，未将工作区中其他既有脏改动计入本次说明。"
    )
    apply_font(run, size=11)


def add_heading(doc: Document, text: str) -> None:
    p = doc.add_paragraph()
    style_paragraph(p, before=12, after=6, line=1.0)
    run = p.add_run(text)
    apply_font(run, size=15, bold=True, color="2E74B5")


def add_table(doc: Document, headers: list[str], rows: list[tuple[str, ...]], widths: list[float]) -> None:
    table = doc.add_table(rows=1, cols=len(headers))
    set_table_layout_fixed(table)
    table.style = "Table Grid"
    header = table.rows[0]
    for idx, text in enumerate(headers):
        cell = header.cells[idx]
        set_cell_width(cell, widths[idx])
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        set_cell_shading(cell, "F2F4F7")
        p = cell.paragraphs[0]
        style_paragraph(p, after=2, line=1.0)
        run = p.add_run(text)
        apply_font(run, size=10, bold=True)

    for row_values in rows:
        row = table.add_row()
        for idx, text in enumerate(row_values):
            cell = row.cells[idx]
            set_cell_width(cell, widths[idx])
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            p = cell.paragraphs[0]
            style_paragraph(p, after=2, line=1.08)
            run = p.add_run(text)
            apply_font(run, size=9)


def build_document() -> Path:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    section = doc.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.top_margin = Inches(1)
    section.bottom_margin = Inches(1)
    section.left_margin = Inches(1)
    section.right_margin = Inches(1)
    section.header_distance = Inches(0.492)
    section.footer_distance = Inches(0.492)

    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal._element.rPr.rFonts.set(qn("w:ascii"), "Calibri")
    normal._element.rPr.rFonts.set(qn("w:hAnsi"), "Calibri")
    normal.font.size = Pt(11)

    add_title(doc)
    add_intro(doc)
    add_heading(doc, "一、核对结论")
    add_table(
        doc,
        ["项", "结论", "确认内容", "关键证据"],
        AUDIT_ROWS,
        [0.35, 0.75, 2.85, 2.55],
    )

    doc.add_section(WD_SECTION.NEW_PAGE)
    add_heading(doc, "二、已确认改动")
    add_table(
        doc,
        ["项", "改动主题", "实际改动", "关键文件"],
        CHANGE_ROWS,
        [0.35, 1.15, 3.25, 1.75],
    )

    add_heading(doc, "三、其余未实现缺口")
    add_table(
        doc,
        ["项", "缺口", "预计改动面", "工作量"],
        GAP_ROWS,
        [0.35, 1.4, 3.6, 1.15],
    )

    add_heading(doc, "四、测试结果摘要")
    add_table(
        doc,
        ["类别", "状态", "说明"],
        TEST_ROWS,
        [1.8, 0.8, 3.9],
    )

    note = doc.add_paragraph()
    style_paragraph(note, before=8, after=0)
    run = note.add_run("备注：第 8 项仅确认“换卡”和“已揭示不回退”，未将缺失的话术补做；后续若继续实现，应以文案分支方式局部补入，不需要重写现有命格卡流转。")
    apply_font(run, size=10, color="555555")

    doc.save(OUT)
    return OUT


if __name__ == "__main__":
    path = build_document()
    print(path)
