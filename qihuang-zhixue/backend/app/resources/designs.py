"""三个工坊各自的「设计」产物。

共同点只有一句：**设计必须能被渲染、能被校验、能被用户改**。除此之外三者差别
落在数据上，不是换个动词：

- 幻灯的 design 是版式与素材槽（``app/ppt/design.py``，唯一有素材的一类）；
- 导图的 design 是结构与视觉语法，它要影响的是**生成**而不是渲染——
  升级前 ``structure`` 被明确声明为视图属性，生成侧从不读它，
  所以「四面发散」的左右分布与内容无关；
- 文档的 design 是体裁、版式、引用体例与篇幅目标，替代两处关键词规则
  与写死的「总长 1200-2500 字」。

三者都写成 pydantic 模型 + 确定性提案，与幻灯同一套做法：可穷举的判断不用模型。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.docs.agent import classify_kind
from app.docs.spec import GENRE_BUDGET, GENRE_LABEL, DocGenre, doc_spec_of
from app.mindmap.appearance import normalize_structure, normalize_theme
from app.ppt.design import DeckDesign, propose_design

# ── 文档 ────────────────────────────────────────────────────────────────────

#: 体裁与字数区间的**唯一事实源**在 ``app.docs.spec``：那边读整句话定体裁，
#: 这边只负责把它落成一份可确认、可修改的设计单。
DocLayout = Literal["lecture", "report", "manual", "minutes"]
CitationStyle = Literal["footnote", "endnote", "none"]
ChartPolicy = Literal["table", "flow", "text"]

#: 体裁 → 版式。讲义偏讲、综述与对比偏报告、指南偏手册、纪要就是纪要。
_LAYOUT_BY_GENRE: dict[str, DocLayout] = {
    "explain": "lecture",
    "compare": "report",
    "review": "report",
    "guide": "manual",
    "plan": "manual",
    "case": "report",
    "qa": "lecture",
    "minutes": "minutes",
    "cheatsheet": "manual",
}

_GENRE_BY_KIND: dict[str, DocGenre] = {
    "讲解": "explain",
    "综述": "review",
    "对比分析": "compare",
    "操作指南": "guide",
    "学习计划": "plan",
    "复习计划": "plan",
    "方案": "plan",
    "清单": "guide",
    "答疑": "explain",
}


class DocDesign(BaseModel):
    model_config = ConfigDict(extra="ignore")

    genre: DocGenre = "explain"
    layout: DocLayout = "lecture"
    citation: CitationStyle = "footnote"
    charts: ChartPolicy = "table"
    #: 目标字数（中值）。上下限由体裁给，用户可以改中值。
    target_words: int = Field(default=2200, ge=400, le=12000)
    rationale: list[str] = Field(default_factory=list, max_length=6)
    assumptions: list[str] = Field(default_factory=list, max_length=6)

    @property
    def word_range(self) -> tuple[int, int]:
        low = int(self.target_words * 0.8)
        high = int(self.target_words * 1.2)
        return low, high


def propose_doc_design(topic: str, params: dict[str, Any], plan: Any) -> DocDesign:
    """体裁与篇幅来自方案里的施工图；方案没有（老任务）才退回关键词判断。

    升级前它是「``plan.organization`` 字符串查表 → 体裁」，而那串字本身是
    关键词规则拼出来的。现在的顺序是：施工图（模型读整句）> 关键词兜底。
    """

    kind = ""
    spec = doc_spec_of(plan if isinstance(plan, dict) else None)
    genre: DocGenre = "explain"
    if spec is not None:
        genre = spec.genre  # type: ignore[assignment]
        kind = spec.genre_label
    else:
        if isinstance(plan, dict):
            kind = str(plan.get("organization") or "")
        genre = _GENRE_BY_KIND.get(kind, "explain")  # type: ignore[assignment]
        if not kind:
            genre = _GENRE_BY_KIND.get(classify_kind(topic), "explain")  # type: ignore[assignment]

    low, high = GENRE_BUDGET.get(genre, (1500, 3000))
    explicit = _int_or_none(params.get("target_words"))
    target = explicit or (spec.target_words if spec is not None else 0) or (low + high) // 2
    layout: DocLayout = _LAYOUT_BY_GENRE.get(genre, "lecture")
    charts: ChartPolicy = "flow" if genre == "guide" else "text" if genre == "cheatsheet" else "table"
    chart_label = {"table": "允许表格", "flow": "允许流程图", "text": "仅文字"}[charts]
    rationale = [
        f"体裁按「{GENRE_LABEL.get(genre, genre)}」处理：它决定章节骨架与语言风格，不是换个标题。",
        f"篇幅按约 {target} 字（{low}–{high}），超过目标不截断，低于下限会在校验里提示。",
        "引用体例用脚注式：正文标 [n]，文末列来源表；没有可核出处的内容标为模型补充。",
        f"图表策略：{chart_label}；表格与流程都由结构数据生成，不引位图。",
    ]
    if spec is not None and spec.sections:
        rationale.insert(1, f"章节按施工图的 {len(spec.sections)} 节写，标题逐字照用。")
    return DocDesign(
        genre=genre,
        layout=layout,
        citation="footnote",
        charts=charts,
        target_words=min(max(target, 400), 12000),
        rationale=rationale,
        assumptions=[f"读者按「{params.get('audience') or (spec.audience if spec is not None else '') or '通用读者'}」理解。"],
    )


# ── 导图 ────────────────────────────────────────────────────────────────────


class MindmapDesign(BaseModel):
    model_config = ConfigDict(extra="ignore")

    structure: str = "logic-right"
    theme: str = "indigo"
    node_shape: Literal["tag", "text", "outline"] = "tag"
    edge: Literal["straight", "curve", "bracket"] = "curve"
    fold: Literal["auto", "depth3", "all"] = "auto"
    #: 这些不是渲染参数，是**生成参数**：分支预算与深度上限会进 ``shape`` 步。
    branch_budget: int = Field(default=6, ge=3, le=8)
    max_depth: int = Field(default=4, ge=2, le=5)
    rationale: list[str] = Field(default_factory=list, max_length=6)
    assumptions: list[str] = Field(default_factory=list, max_length=6)

    @property
    def min_branches(self) -> int:
        return STRUCTURE_BUDGET.get(self.structure, STRUCTURE_BUDGET["logic-right"])["min_branches"]


#: 结构 → 分支与深度预算。这张表是「结构参与生成」的唯一依据。
#:
#: 深度上限**必须**与前端 ``mindmapStructures.maxDepth`` 一致：后端按 5 层生成、
#: 画布按 3 层折起来，用户看到的就是「说了五层只有三层」。
STRUCTURE_BUDGET: dict[str, dict[str, int]] = {
    "logic-right": {"min_branches": 3, "max_branches": 8, "max_depth": 5},
    "mind-both": {"min_branches": 4, "max_branches": 8, "max_depth": 5},
    "org-down": {"min_branches": 3, "max_branches": 6, "max_depth": 4},
    "fishbone": {"min_branches": 3, "max_branches": 5, "max_depth": 3},
}


def propose_mindmap_design(topic: str, params: dict[str, Any], plan: Any = None) -> MindmapDesign:
    """导图设计单。层数与结构**优先取方案里那一份**：用户已经说了要几层，
    设计单不该拿提案时的原始入参再算一遍，否则面板上改的 5 层会被设计单盖回 3 层。
    """

    spec = (plan or {}).get("spec") if isinstance(plan, dict) else None
    spec = spec if isinstance(spec, dict) else {}
    structure = normalize_structure(str(spec.get("structure") or params.get("structure") or "logic-right"))
    theme = normalize_theme(str(params.get("theme") or "indigo"))
    budget = STRUCTURE_BUDGET[structure]
    depth = _int_or_none(spec.get("depth")) or _int_or_none(params.get("depth")) or 4
    branches = _int_or_none(spec.get("branch_count")) or _int_or_none(params.get("branches")) or min(budget["max_branches"], 6)
    units = _plan_unit_labels(plan)
    if units:
        branches = len(units)
    return MindmapDesign(
        structure=structure,
        theme=theme,
        node_shape="outline" if structure == "org-down" else "tag",
        edge="bracket" if structure == "fishbone" else "curve",
        fold="auto",
        branch_budget=min(max(branches, budget["min_branches"]), budget["max_branches"]),
        max_depth=min(depth, budget["max_depth"]),
        rationale=[
            f"结构取「{structure}」：它决定一级分支数与每支深度（{budget['min_branches']}–{budget['max_branches']} 支、最多 {budget['max_depth']} 层），不只是换个画法。",
            f"层数按 {min(depth, budget['max_depth'])} 层生成——这一版来自方案里的层数，不是默认值；要更深就改方案里的层数。",
            "配色只影响观感，不影响内容；换主题不会重排节点。",
        ],
        assumptions=[f"主题按「{topic[:20] or '未命名'}」理解，分支数取 {min(max(branches, budget['min_branches']), budget['max_branches'])}。"],
    )


# ── 分道 ────────────────────────────────────────────────────────────────────


def design_for(resource_type: str, topic: str, params: dict[str, Any], plan: Any) -> dict[str, Any]:
    """按资源类型给设计单。三个工坊在这里分道，之后才轮到 select/compose。"""

    if resource_type == "ppt":
        design: DeckDesign | DocDesign | MindmapDesign = propose_design(topic, params, plan)
    elif resource_type == "doc":
        design = propose_doc_design(topic, params, plan)
    elif resource_type == "quiz":
        return _quiz_design(params, plan)
    elif resource_type == "story":
        return _story_design(params, plan)
    else:
        # 导图的设计单要读方案：层数与结构以用户确认的那一版为准。
        design = propose_mindmap_design(topic, params, plan)
    return design.model_dump(mode="json")


def _plan_unit_labels(plan: Any) -> list[str]:
    units = (plan or {}).get("units") if isinstance(plan, dict) else None
    return [str(unit.get("label") or "") for unit in units or [] if isinstance(unit, dict)]


def _quiz_design(params: dict[str, Any], plan: Any) -> dict[str, Any]:
    """卷式设计单：题量、题型配比、难度档。全部可被用户改，改完才开炼。

    没有让模型来设计卷式：题量与配比是可数的东西，模型给的数通常落在
    「五道单选加两道多选」这类漂亮但不对齐考点的组合上，用户还得逐项改。
    """

    count = int(params.get("question_count") or 8)
    return {
        "kind": "quiz",
        "question_count": count,
        "question_mix": [
            {"type": "single", "label": "单选", "count": max(1, round(count * 0.6))},
            {"type": "multiple", "label": "多选", "count": max(1, round(count * 0.25))},
            {"type": "match", "label": "配伍", "count": max(1, count - max(1, round(count * 0.6)) - max(1, round(count * 0.25)))},
        ],
        "difficulty_mix": {"easy": 0.3, "medium": 0.5, "hard": 0.2},
        "units": _plan_unit_labels(plan),
        "rationale": [
            "题量按 3:1:1 分到单选、多选与配伍，配伍题专门考易混药对。",
            "难度按易 3 : 中 5 : 难 2 配，难题只考辨析，不考生僻记忆。",
        ],
        "assumptions": ["默认面向中医专业学生；如需执业考试口径请说明。"],
    }


def _story_design(params: dict[str, Any], plan: Any) -> dict[str, Any]:
    """场次设计单：几场、谁出场、每场立什么冲突。"""

    scenes = int(params.get("scene_count") or 4)
    units = _plan_unit_labels(plan) or ["起", "承", "转", "合"]
    return {
        "kind": "story",
        "scene_count": scenes,
        "scenes": [
            {"index": index + 1, "title": label, "beat": beat}
            for index, (label, beat) in enumerate(
                zip(units[:scenes], ("铺陈情境", "引出矛盾", "药性登场", "收束回扣")[: len(units[:scenes])])
            )
        ],
        "cast": [{"role": "主角", "note": "跟诊的学徒"}, {"role": "对手", "note": "执意用错药的同行"}],
        "rationale": [
            "按四场起承转合排，每场只推进一件事，观众不必记两条线。",
            "知识点放在第三场的对白里，说破就不好看了。",
        ],
        "assumptions": ["默认四场短剧，单场不超过 90 秒。"],
    }


def normalize_design_for(resource_type: str, raw: dict[str, Any] | None, plan: Any, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """把用户改过的设计单收敛成合法契约。

    确认入口用它：坏字段一律退回默认值，不抛 422——在确认阶段把用户的手改
    打回去，比接受一个略有偏差的版式更糟。
    """

    from app.ppt.design import normalize_design

    params = dict(params or {})
    if resource_type == "ppt":
        return normalize_design(raw, plan, params=params).model_dump(mode="json")
    if resource_type == "doc":
        base = propose_doc_design(str(params.get("topic") or ""), params, plan)
        return DocDesign.model_validate({**base.model_dump(), **(raw or {})}).model_dump(mode="json")
    if resource_type in ("quiz", "story"):
        # 这两类没有严格模型可校验，改动一律按用户给的值合并。
        base = design_for(resource_type, str(params.get("topic") or ""), params, plan)
        return {**base, **(raw or {})}
    base_map = propose_mindmap_design(str(params.get("topic") or ""), params, plan)
    return MindmapDesign.model_validate({**base_map.model_dump(), **(raw or {})}).model_dump(mode="json")


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
