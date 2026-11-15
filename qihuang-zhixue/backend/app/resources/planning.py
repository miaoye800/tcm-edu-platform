"""资源制作方案：先理解任务，再给出一份可协商、可确认的施工图。

三条设计约束，都来自产品要求：

1. **方案不是分类结果**。``task_shape`` 只用来给 PPT/导图/文档挑一组
   「按时间讲 / 按维度比 / 按阶段排」的候选骨架，用户可以在确认阶段整份改掉；
   它不写进任务类型，也不决定最终内容结构。
2. **依据分三档**：``knowledge_base``（教材知识库与图谱）、``user_material``
   （用户带来的材料）、``model``（通用模型补充）。每一页 / 每一节 / 每一支
   都要能指回具体档位；声明了知识库依据却拿不出引用的，一律降级为模型补充，
   不允许「整份标有依据、逐项查无出处」。
3. **没有确认就不生成**。方案连同修订轮次落在任务的 ``steps_done`` 里，
   重试、刷新、断线都读同一份；生成阶段只能消费它，不能重拟。

PPT / 文档 / 导图共用「目标—受众—组织—单元—呈现—缺口」这套概念，
但单元的含义各自不同：PPT 是页，文档是章节，导图是一级分支。
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agents.resource_context import ResourceDoc, collect_source_refs, retrieve_resource_docs
from app.docs.agent import DocAgent
from app.docs.evidence import build_doc_evidence
from app.docs.spec import (
    GENRE_LABEL,
    GENRE_ORG,
    ORG_LINE,
    DocSectionSpec,
    DocSpec,
    align_spec_with_units,
    compose_doc_spec,
    doc_spec_of,
    genre_of,
    words_from_text,
)
from app.llm import router as llm_router
from app.llm.clients import MockClient
from app.llm.router import ModelRouter
from app.mindmap.evidence import build_evidence_pack
from app.mindmap.intents import classify
from app.mindmap.spec import (
    MAX_DEPTH,
    MIN_DEPTH,
    baseline_spec,
    compose_spec,
    depth_from_text,
    fit_structure,
    generation_params_from_plan,
    structure_capacity,
)
from app.ppt.evidence import build_deck_evidence
from app.ppt.params import PptParams
from app.ppt.spec import (
    ROLE_LABEL,
    align_deck_with_units,
    compose_deck_spec,
    deck_spec_of,
)
#: 导图侧也叫 ``baseline_spec``，这里显式起个别名，免得两套基线串台。
from app.ppt.spec import baseline_spec as deck_baseline

SourceKind = Literal["knowledge_base", "user_material", "model"]
PlanKind = Literal["ppt", "doc", "mindmap", "quiz", "story"]

SOURCE_LABELS: dict[str, str] = {
    "knowledge_base": "知识库 / 图谱依据",
    "user_material": "用户材料",
    "model": "模型补充",
}

#: 单元数量上限：与三种资源的成品容量对齐（幻灯 20 页、文档 8 章、导图 8 支）。
MAX_UNITS: dict[str, int] = {"ppt": 20, "doc": 10, "mindmap": 8}
#: 用户材料单条上限。够放一段讲义提纲，又不至于把 prompt 撑爆。
MATERIAL_LIMIT = 2000

LLMCall = Callable[[list[dict[str, str]]], Awaitable[str]]


def _settings():
    """走 router 的配置入口：测试会替换它来保证离线，提案也跟着离线。"""

    return llm_router.get_settings()


class PlanUnit(BaseModel):
    """资源里的一个可操作单元：一页幻灯 / 一节文稿 / 一支导图分支。"""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(default="", max_length=32)
    label: str = Field(default="", max_length=80)
    intent: str = Field(default="", max_length=300)
    detail: str = Field(default="", max_length=600)
    source: SourceKind = "model"
    evidence: list[str] = Field(default_factory=list, max_length=6)
    gap: str = Field(default="", max_length=200)

    @field_validator("label", "intent", "detail")
    @classmethod
    def tidy(cls, value: str) -> str:
        return " ".join(str(value).split())


class ResourcePlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: PlanKind = "doc"
    title: str = Field(default="", max_length=60)
    goal: str = Field(default="", max_length=500)
    audience: str = Field(default="", max_length=120)
    organization: str = Field(default="", max_length=500)
    presentation: str = Field(default="", max_length=300)
    units: list[PlanUnit] = Field(default_factory=list, max_length=24)
    gaps: list[str] = Field(default_factory=list, max_length=16)
    evidence: dict[str, list[str]] = Field(default_factory=dict)
    #: 这次「怎么画」的完整施工图（``mindmap.spec.MindSpec``）。
    #:
    #: 上面那几个字段是给人看的，这一份是给生成读的：层数、结构、分支轴、
    #: 必答项都在这里。方案面板改的是它，生成也读它——面板与生成之间
    #: 不再是「两套各自理解的参数」。
    spec: dict[str, Any] = Field(default_factory=dict)
    clarification: str = Field(default="", max_length=300)
    revision: int = 0
    updated_at: str = ""


# ── 任务形态：只做「候选骨架」的提示，不是分类结果 ──────────────────────────

_SHAPES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("temporal", ("历史", "发展", "演变", "沿革", "源流", "起源", "流传", "史")),
    ("comparison", ("对比", "比较", "区别", "异同", "鉴别", "差异", "选哪个", "怎么选")),
    ("planning", ("规划", "计划", "路径", "路线", "安排", "阶段学习", "备考", "复习计划")),
    ("procedure", ("步骤", "流程", "操作", "怎么做", "如何做", "上手")),
)

#: 各形态的候选骨架。只在方案阶段用；用户改掉任何一条都算数。
_SHAPE_BLUEPRINTS: dict[str, dict[str, list[tuple[str, str]]]] = {
    "temporal": {
        "ppt": [
            ("时代背景与起点", "交代这件事发生在什么条件下"),
            ("关键阶段演进", "按时间顺序拆出三到四个转折点"),
            ("转折背后的原因", "解释每一次变化为什么发生"),
            ("今天的形态与影响", "落到当代认识或应用"),
            ("回顾与要点", "收束时间线，给出可记住的坐标"),
        ],
        "doc": [
            ("源流与早期记载", "交代最早可考的出处与语境"),
            ("历代演变", "按朝代或时期梳理关键变化"),
            ("转折与分歧", "说明演变中的关键节点与不同说法"),
            ("当代认识", "落到今天的教材表述或研究结论"),
            ("小结", "回收主线，指出仍有争议之处"),
        ],
    },
    "comparison": {
        "ppt": [
            ("为什么要比较", "点明容易混淆的原因与比较的意义"),
            ("比较维度一览", "先给维度，再逐项展开"),
            ("逐维对照", "同一维度下并排看两者的差异"),
            ("各自适用场景", "把差异翻译成选择建议"),
            ("易错点与记忆抓手", "指出最常见的混淆"),
        ],
        "doc": [
            ("为什么容易混", "说明混淆来源"),
            ("核心差异", "给出最关键的区分点"),
            ("逐项对比", "按维度展开，可用表格"),
            ("各自适用场景", "把差异落到用法上"),
            ("选择建议与记忆抓手", "给出可操作的判断规则"),
        ],
    },
    "planning": {
        "ppt": [
            ("目标与起点", "先说清楚学完要能做到什么"),
            ("前置基础", "开始之前需要具备什么"),
            ("阶段安排", "按周或按阶段排布任务"),
            ("练习与检验", "每个阶段怎么确认学会了"),
            ("常见误区", "最容易走弯路的几处"),
        ],
        "doc": [
            ("目标与现状", "明确终点与起点"),
            ("前置准备", "开始前需要补齐的基础"),
            ("阶段路线", "分阶段列出任务与产出"),
            ("练习与自测", "如何检验每个阶段"),
            ("调整与复盘", "进度落后时怎么改"),
        ],
    },
    "procedure": {
        "ppt": [
            ("要解决的问题", "先定义任务边界"),
            ("准备清单", "开始前需要准备什么"),
            ("操作步骤", "按顺序拆成可执行的步骤"),
            ("关键细节", "容易出错的环节"),
            ("验收标准", "怎么判断做对了"),
        ],
        "doc": [
            ("适用场景", "什么情况下按这个流程做"),
            ("准备清单", "材料与前置条件"),
            ("操作步骤", "逐步展开"),
            ("常见错误", "最容易失败的地方"),
            ("验收标准", "怎样算完成"),
        ],
    },
}


def task_shape(text: str) -> str:
    """从请求里看出「用户要的是哪一种组织方式」。空串表示没有明显倾向。

    **导图不再用它决定结构**：一句话里只要出现「对比」就套同一套四支蓝图的
    做法已经拆掉，导图的分支由 ``mindmap.spec.compose_spec`` 读整句话来定。
    这里只剩幻灯与文稿的候选骨架，以及各处兜底方案。
    """

    content = str(text or "")
    for shape, words in _SHAPES:
        if any(word in content for word in words):
            return shape
    return ""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def empty_evidence() -> dict[str, list[str]]:
    return {"knowledge_base": [], "user_material": [], "model": []}


def user_materials(params: dict[str, Any] | None) -> list[str]:
    """用户带来的材料。当前产品以「一段可解析文本」的形式接入，不做文件解析。"""

    raw = (params or {}).get("materials")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    cleaned = [" ".join(str(item).split())[:MATERIAL_LIMIT] for item in raw]
    return [item for item in cleaned if item][:5]


def _kb_terms(docs: list[ResourceDoc]) -> list[str]:
    """知识库的可核验词表。只用于判断「这个单元有没有依据」，不参与写作。"""

    terms: list[str] = []
    if docs:
        # 药材条目本身就按这些结构组织；章节名与它们对得上，才算「教材覆盖」。
        # 少了这一组，「性味归经」这种最典型的教材章节会被误判成模型补充。
        terms.extend(["性味归经", "性味", "归经", "功效", "主治", "用量", "禁忌", "配伍", "鉴别", "概览", "使用注意"])
    for doc in docs:
        terms.append(str(doc.get("name", "")))
        terms.extend(str(item) for item in doc.get("effects", [])[:10])
        terms.extend(str(item.get("syndrome", "")) for item in doc.get("indications", [])[:8])
        terms.extend(str(item) for item in doc.get("cautions", [])[:5])
        terms.append(str(doc.get("dosage", {}).get("text", "")))
        for item in doc.get("differentiation", [])[:4]:
            terms.append(str(item.get("compare_name", "")))
            terms.append(str(item.get("key_points", "")))
        terms.extend(str(item) for item in doc.get("meridians", []))
        terms.extend(str(item) for item in doc.get("flavor", []))
        terms.append(str(doc.get("nature", "")))
    return [term for term in dict.fromkeys(terms) if term.strip()]


def _catalog_terms(pack: Any) -> list[str]:
    """教材目录里的名字，用来判断一条分支是不是「教材真的这么分」。

    只取目录（章名 / 节名 / 条目名），不取模型给的分支名——后者不是证据。
    没有命中学科的包这里自然是空，不影响原有的判定。
    """

    terms: list[str] = []
    for row in getattr(pack, "nodes", []) or []:
        if isinstance(row, dict):
            terms.append(str(row.get("name") or ""))
    # 篇名（总论/各论）单独收：各论几十章放不进 groups 时，它的名字仍是真的。
    terms.extend(str(slot) for slot in getattr(pack, "branch_slots", []) or [])
    for key, names in (getattr(pack, "groups", {}) or {}).items():
        # 分组名也要算：方案里的分支常写成「总论·基础理论」，命中的是篇名「总论」。
        terms.append(str(key))
        terms.extend(str(name) for name in names)
    return [term for term in dict.fromkeys(terms) if term.strip()]


def _hits(label: str, terms: list[str]) -> bool:
    """标签里是否出现了知识库能核验的词。

    两字以上的词才算，避免「药」「用」这种单字把任何一页都判成「有依据」。
    """

    text = str(label or "")
    if not text:
        return False
    for term in terms:
        term = term.strip()
        if len(term) < 2:
            continue
        if term in text or (len(text) >= 2 and text in term):
            return True
    return False


def evidence_tiers(docs: list[ResourceDoc], materials: list[str]) -> dict[str, list[str]]:
    tiers = empty_evidence()
    refs = collect_source_refs(docs)
    labels: list[str] = []
    for ref in refs:
        name = str(ref.get("name") or "").strip()
        location = str(ref.get("ref") or "").strip()
        label = f"{name}·{location}" if name and location else (name or location)
        if label:
            labels.append(label)
    tiers["knowledge_base"] = list(dict.fromkeys(labels))[:12]
    tiers["user_material"] = [f"用户材料：{item[:24]}" for item in materials][:5]
    return tiers


# ── 提案 ────────────────────────────────────────────────────────────────────


async def propose(
    resource_type: str,
    topic: str,
    params: dict[str, Any] | None = None,
    *,
    llm_call: LLMCall | None = None,
) -> dict[str, Any]:
    """给这一次请求拟一份方案。失败也要给一份诚实的方案，不能拦住创作。"""

    params = dict(params or {})
    topic = " ".join(str(topic or "").split())[:200]
    materials = user_materials(params)
    docs = await retrieve_resource_docs(topic)
    tiers = evidence_tiers(docs, materials)
    shape = task_shape(f"{topic} {params.get('brief_note') or ''}")

    builder = {
        "ppt": _propose_ppt,
        "mindmap": _propose_mindmap,
        "doc": _propose_doc,
        "quiz": _propose_quiz,
        "story": _propose_story,
    }.get(resource_type, _propose_doc)
    try:
        plan = await builder(topic, docs, params, shape, materials, tiers, llm_call=llm_call)
    except Exception:  # noqa: BLE001 - 提案失败不能让用户连方案都看不到
        plan = _fallback_plan(resource_type, topic, params, materials)
        plan.gaps.append("提案时未能完成领域分析，方案按通用结构给出，可逐项修改")

    plan = _finalize(plan, resource_type, docs, materials, tiers, shape)
    return plan.model_dump(mode="json")


async def _propose_ppt(
    topic: str,
    docs: list[ResourceDoc],
    params: dict[str, Any],
    shape: str,
    materials: list[str],
    tiers: dict[str, list[str]],
    *,
    llm_call: LLMCall | None = None,
) -> ResourcePlan:
    """讲稿方案 = 把整句话读成一份讲稿施工图，再把施工图摆成可确认的卡片。

    以前这里只有两条路：要么让大纲提示词写（提示词把讲授顺序**写死成药材八股**：
    封面→导览→性味归经→功效→主治→鉴别→用量禁忌→总结），要么按 ``shape`` 取
    一张固定蓝图。两条路都不读那句话，于是「给新生做的入门试讲」和「给临床
    医生做的用药对比」出的是同一份；封面单元写的是用户原句，「目标」栏是
    ``围绕「{原句}」做一次{受众}演示`` 这句拼接模板；版式全靠关键词猜，
    猜不中一律要点页——九页稿子七页长得一模一样。

    现在场合、标题、受众、页数、叙事弧、每页的角色与必讲点、每页要什么素材，
    全部来自 ``compose_deck_spec``；``shape`` 只在无模型时当基线线索。
    """

    ppt_params = PptParams.parse(params)
    evidence = build_deck_evidence(topic, docs)
    # 依据要能核：药材条目名 + 教材目录名。整门课的主题没有药材条目，
    # 可章名明明就在库里，不把它们算进来每一页都会被标成「模型补充」。
    terms = list(dict.fromkeys([*_kb_terms(docs), *_coverage_terms(evidence.coverage)]))
    spec = await compose_deck_spec(
        topic,
        coverage=evidence.coverage,
        docs=docs,
        params={**params, "theme": ppt_params.theme, **({"page_count": params["page_count"]} if params.get("page_count") else {})},
        materials=materials,
        shape=shape,
        channels_digest=evidence.asset_menu,
        llm_call=llm_call,
    )

    citations = tiers["knowledge_base"][:1]
    if not citations and evidence.coverage is not None:
        # 目录本身就是依据：这条引用说的是「结构来自这本书的目录」，
        # 不是「内容取自某一页」。没有它，整门课的方案会一页依据都没有。
        try:
            from app.mindmap import catalog as catalog_module

            ref = catalog_module.source_ref(evidence.coverage)
        except Exception:  # noqa: BLE001 - 目录异常不影响出方案
            ref = {}
        label = "·".join(item for item in (str(ref.get("name") or ""), str(ref.get("ref") or "")) if item)
        if label:
            citations = [label]
            tiers["knowledge_base"] = [label]

    units: list[PlanUnit] = []
    # 「教材没按这个路子编排」的组织方式（历史分期、案情复盘）里，页名带上药名
    # 不等于这一页有依据——「唐宋：人参地位的跃升」在任何一条药材条目里都查不到。
    # 这种稿子只有页名命中**教材章名**时才算知识库依据，其余老实标模型补充。
    structural = spec.organization_kind in ("timeline", "case")
    chapter_terms = _coverage_terms(evidence.coverage)
    for page in spec.pages:
        label = page.clean_heading
        if not label:
            continue
        # 依据档位不能由模型自报：命中教材条目/目录名才算有依据，否则如实标模型补充。
        hits = _hits(f"{label} {' '.join(page.must_cover)}", terms)
        pinned = bool(citations) and hits and (not structural or _hits(label, chapter_terms))
        units.append(
            PlanUnit(
                label=label,
                intent=page.goal or f"这一页要回答：{label}",
                # 方案卡上可改的是 detail 这一栏，也是生成时「这一页必须讲到什么」。
                detail="；".join(page.must_cover),
                source="knowledge_base" if pinned else "model",
                evidence=citations if pinned else [],
            )
        )

    role_text = "、".join(dict.fromkeys(ROLE_LABEL.get(page.role, page.role) for page in spec.pages))
    look = spec.look
    # 母版名是用户在确认卡上最先要看到的信息：主题词（paper/ink）太抽象，
    # 「宣纸宋楷」四个字一眼就知道长什么样。没选母版的老路才回落到主题词。
    from app.ppt.masters import by_id as _master_by_id

    master = _master_by_id(getattr(look, "master_id", ""))
    look_text = f"{master.spec.label}母版" if master is not None else f"{look.theme} 主题"
    ornament_text = (
        "不挂角花"
        if not look.ornament.motif or look.ornament.motif == "none"
        else f"{look.ornament.motif} 角饰"
    )
    presentation = (
        f"共 {len(units)} 页（含封面），{look_text}，{spec.variety} 种版式（{role_text[:40]}），"
        f"{len({page.composition for page in spec.pages})} 种构图，{ornament_text}，逐页可改"
    )
    gaps = list(dict.fromkeys([*spec.notes, *evidence.notes]))
    if evidence.asset_menu:
        gaps.append("素材来自本库图库：饮片实拍、馆藏彩绘、器物纹样；找不到就留白，不拿相近的凑")
    return ResourcePlan(
        kind="ppt",
        title=spec.title[:60],
        # 「目标」是这份稿子做什么用；thesis 是讲完记住的那句，两者分开写。
        # 以前两者共用一个字段，卡片第一栏写的是一句演讲词，读不出「你要做什么」。
        goal=(spec.goal or spec.thesis)[:500],
        audience=spec.audience,
        organization=spec.organization,
        presentation=presentation[:300],
        units=units,
        gaps=gaps[:8],
        evidence=tiers,
        spec=spec.model_dump(mode="json"),
    )


async def _propose_doc(
    topic: str,
    docs: list[ResourceDoc],
    params: dict[str, Any],
    shape: str,
    materials: list[str],
    tiers: dict[str, list[str]],
    *,
    llm_call: LLMCall | None = None,
) -> ResourcePlan:
    """文稿方案 = 把整句话读成一份写作施工图，再把施工图摆成可确认的卡片。

    以前这里是 ``if shape: 用固定的五节蓝图``——只要出现「计划」二字，不管用户
    还说了「我过四级又要学中药学」，都长出同一套「目标与现状 / 前置准备 /
    阶段路线 / 练习与自测 / 调整与复盘」，目标栏还是
    ``写一份关于「{原句}」的{组织方式}`` 这句拼接模板。现在体裁、标题、章节、
    篇幅都来自 ``compose_doc_spec``；``shape`` 只在无模型时当基线线索。
    """

    evidence = build_doc_evidence(topic, docs)
    # 依据要能核：药材条目名 + 教材目录名。整门课的主题没有药材条目，
    # 可章节名明明就在库里，不把它们算进来每一节都会被标成「模型补充」。
    terms = list(dict.fromkeys([*_kb_terms(docs), *_coverage_terms(evidence.coverage)]))
    spec = await compose_doc_spec(
        topic,
        coverage=evidence.coverage,
        docs=docs,
        params=params,
        materials=materials,
        llm_call=llm_call,
    )

    citations = tiers["knowledge_base"][:1]
    if not citations and evidence.coverage is not None:
        # 目录本身就是依据：这条引用说的是「结构来自这本书的目录」，
        # 不是「内容取自某一页」。没有它，整门课的方案会一节依据都没有。
        try:
            from app.mindmap import catalog as catalog_module

            ref = catalog_module.source_ref(evidence.coverage)
        except Exception:  # noqa: BLE001 - 目录异常不影响出方案
            ref = {}
        label = "·".join(item for item in (str(ref.get("name") or ""), str(ref.get("ref") or "")) if item)
        if label:
            citations = [label]
            tiers["knowledge_base"] = [label]

    units: list[PlanUnit] = []
    for index, section in enumerate(spec.sections):
        label = section.clean_heading
        # 依据档位不能由模型自报：命中教材条目/目录名才算有依据，否则如实标模型补充。
        pinned = bool(citations) and _hits(label, terms)
        units.append(
            PlanUnit(
                label=label,
                intent=section.goal or f"这一节要回答：{label}",
                # 方案卡上可改的是 detail 这一栏，也是生成时「这一节必须覆盖什么」。
                detail="；".join(section.must_cover),
                source="knowledge_base" if pinned else "model",
                evidence=citations if pinned else [],
            )
        )

    device_text = "、".join(_DEVICE_LABELS.get(device, device) for device in spec.devices[:3])
    presentation = f"{spec.genre_label} · 约 {spec.target_words} 字 · {len(units)} 章"
    if device_text:
        presentation = f"{presentation} · 含{device_text}"
    return ResourcePlan(
        kind="doc",
        title=spec.title[:60],
        # 目标就是这份文档要交付的判断，不是「写一份关于 X 的 Y」这种拼接句。
        goal=spec.thesis[:500],
        audience=spec.audience,
        organization=spec.organization,
        presentation=presentation[:300],
        units=units,
        gaps=list(dict.fromkeys([*spec.notes, *evidence.notes]))[:8],
        evidence=tiers,
        spec=spec.model_dump(mode="json"),
    )


#: 排版要素 → 方案卡上的说法。用户看不懂 table / checklist 这种词。
_DEVICE_LABELS: Final[dict[str, str]] = {
    "table": "对照表",
    "checklist": "行动清单",
    "steps": "分步说明",
    "timeline": "时间线",
    "callout": "重点提示",
    "summary": "速记小结",
    "glossary": "术语表",
}


def _coverage_terms(coverage: Any) -> list[str]:
    """教材目录里的名字，用来判断一节是不是「教材真的这么分」。"""

    if coverage is None:
        return []
    terms: list[str] = []
    for subject in coverage.subjects:
        terms.append(subject.title)
        terms.extend(subject.parts())
        for chapter in subject.chapters:
            terms.append(chapter.title)
            for section in chapter.sections:
                terms.append(section.title)
                terms.extend(section.entries)
    return [term for term in dict.fromkeys(terms) if term.strip()]


async def _propose_mindmap(
    topic: str,
    docs: list[ResourceDoc],
    params: dict[str, Any],
    shape: str,
    materials: list[str],
    tiers: dict[str, list[str]],
    *,
    llm_call: LLMCall | None = None,
) -> ResourcePlan:
    """导图方案 = 把整句话读成一份施工图，再把施工图摆成可确认的卡片。

    以前这里是``if shape: 用固定的四支蓝图``——一句话里只要出现「对比」，
    不管用户还说了「多分几个层次」「多总结怎么学」，都长出同一棵
    对比维度/相同点/关键差异/选用提示。现在分支轴来自 ``compose_spec``：
    有模型时模型读句子来说，无模型时用确定性基线，两者都产出 ``MindSpec``。
    """

    intent = classify(topic)
    pack = await build_evidence_pack(intent, docs=docs)
    terms = _kb_terms(docs)
    # 整门课/整章的方案里没有药材条目，``terms`` 会是空的，于是每一支都被标成
    # 「模型补充」——可分支名明明就是教材章名。把目录名加进可核验词表，
    # 这些分支才算「教材覆盖」。
    terms = list(dict.fromkeys([*terms, *_catalog_terms(pack)]))
    spec = await compose_spec(topic, pack, params, llm_call=llm_call)
    citations = tiers["knowledge_base"][:1]
    if not citations and pack.catalog_ref:
        # 整门课 / 整章的方案没有药材条目，但这本书的目录就在库里。目录就是依据，
        # 不能被算成「什么也没检索到」——那会让每一支都挂上「模型补充」，
        # 而分支名其实一字不差地来自教材目录。
        ref = pack.catalog_ref
        label = f"{ref.get('name', '')}·{ref.get('ref', '')}"
        citations = [label]
        tiers["knowledge_base"] = [label]

    units: list[PlanUnit] = []
    for branch in spec.branches:
        label = branch.label
        # 依据档位不能由模型自报：命中教材条目才算有依据，否则如实标模型补充。
        pinned = bool(citations) and not pack.degraded and _hits(label, terms)
        covers = list(branch.must_cover)
        units.append(
            PlanUnit(
                label=label,
                intent=branch.purpose or f"这一支要回答：{label}",
                # 方案卡上可改的是 detail 这一栏，也是生成时「这一支必须覆盖什么」。
                detail="；".join(covers),
                source="knowledge_base" if pinned else "model",
                evidence=list(citations) if pinned else [],
                gap="" if pinned or not citations else "教材未覆盖此分支，按模型补充处理",
            )
        )
    if not units:
        # 理论上不会到这一步（compose_spec 至少给基线分支），但真到了也不能
        # 交一张空方案：空方案连确认按钮都点不下去。
        spec = baseline_spec(topic, pack, params, intent=intent)
        units = [PlanUnit(label=branch.label, intent=branch.purpose, source="model") for branch in spec.branches]

    goal = f"梳理「{topic}」的知识关系，用于{spec.audience or params.get('audience') or '理解与复习'}"
    if params.get("brief_note"):
        goal = f"{goal}；额外要求：{params['brief_note']}"
    presentation = f"{spec.depth} 层 · {len(units)} 支 · 可编辑思维导图，生成后可换布局、扩节点"
    if spec.depth_gap():
        presentation = f"{presentation}（{spec.depth_gap()}）"

    return ResourcePlan(
        kind="mindmap",
        title=(spec.root_topic or topic)[:60],
        goal=goal[:500],
        audience=spec.audience,
        organization=spec.organization,
        presentation=presentation[:300],
        units=units,
        gaps=list(dict.fromkeys([*pack.notes, *spec.notes]))[:8],
        evidence=tiers,
        spec=spec.model_dump(mode="json"),
    )


#: 试卷与漫剧的方案骨架。这两类不走模型拟方案——卷子的「考什么、怎么配比、
#: 易混点在哪」从检索结果就能定得七七八八，让模型再猜一遍只会多一次等待，
#: 且猜出来的单元标题用户一样要改。确定性方案在这里反而更诚实。
#:
#: 三元组的第三位是「这一行取不取教材」。考什么、点哪几味药是检索结果决定的，
#: 题型配比、难度梯度是编排决定——分开标，别让整份方案读起来像全靠模型补。
_QUIZ_UNITS: Final[tuple[tuple[str, str, bool], ...]] = (
    ("考点范围", "圈定本次覆盖的药材与证候，写清哪些必考、哪些只作干扰", True),
    ("题型与题量", "单选、多选、配伍各出几道，总分怎么分", False),
    ("难度梯度", "易 / 中 / 难三档各占多少，难题考的是辨析还是应用", False),
    ("易混设错", "干扰项取自哪几味近邻药，错在哪一处才算「像」", True),
)

_STORY_UNITS: Final[tuple[tuple[str, str, bool], ...]] = (
    ("人物与场景", "谁出场、在什么场合、观众跟着谁的视角走", True),
    ("冲突与转折", "这一段的矛盾是什么，靠哪一味药的一句台词化开", False),
    ("知识点植入", "性味归经与禁忌藏在哪几句对白里，不说破但要能听出来", True),
    ("结尾与回扣", "收在什么画面上，最后一句如何回扣开头的疑问", False),
)

#: 「把 X 讲成一出短剧」这类句式里包着真主题。只在动词框架后面**就是剧种、
#: 没有别的诉求**时才剥壳——「…讲成一出短剧并强调禁忌」里的后半句是用户真的
#: 提了要求，剥掉就等于把他要的东西从标题里删了；这种情况原样保留。
_STORY_FRAME: Final = re.compile(
    r"^(?:把|将|用)(?P<subject>.+?)(?:讲成|演成|做成|编成|改成|拍成|写成)"
    r"一?(?:出|幕|个|段|篇|部)?(?:短剧|小剧场|情景剧|舞台剧|短片|脚本|剧|戏)[。！!]?$"
)


def story_subject(topic: str) -> str:
    """取漫剧的主题：「把麻黄汤证讲成一出短剧」→「麻黄汤证」。

    方案卡与资源卡共用这一份：用户确认的方案叫什么，成品就该叫什么。
    """

    matched = _STORY_FRAME.match(str(topic or "").strip())
    subject = (matched.group("subject") if matched else "").strip()
    return subject or str(topic or "").strip()


async def _propose_quiz(
    topic: str,
    docs: list[ResourceDoc],
    params: dict[str, Any],
    shape: str,
    materials: list[str],
    tiers: dict[str, list[str]],
    *,
    llm_call: LLMCall | None = None,
) -> ResourcePlan:
    return _structured_plan("quiz", "出一份试卷", _QUIZ_UNITS, topic, docs, params, tiers)


async def _propose_story(
    topic: str,
    docs: list[ResourceDoc],
    params: dict[str, Any],
    shape: str,
    materials: list[str],
    tiers: dict[str, list[str]],
    *,
    llm_call: LLMCall | None = None,
) -> ResourcePlan:
    return _structured_plan("story", "演一出短剧", _STORY_UNITS, topic, docs, params, tiers)


def _structured_plan(
    kind: PlanKind,
    verb: str,
    units: tuple[tuple[str, str, bool], ...],
    topic: str,
    docs: list[ResourceDoc],
    params: dict[str, Any],
    tiers: dict[str, list[str]],
) -> ResourcePlan:
    """把固定骨架落成一份方案：命中教材的单元才算有依据，其余标 model。

    依据档位不能整份糊成「教材」——用户改方案时看到的每个标签都得是真的。
    骨架自带的 ``anchored`` 表示「这一行本来就从教材条目里取」，检索到条目才算数；
    没检索到就照旧降级成 model，不能因为它是固定骨架就免检。
    """

    terms = _kb_terms(docs)
    citations = tiers["knowledge_base"][:1]

    def grounded(label: str, anchored: bool) -> bool:
        return bool(citations) and (anchored or _hits(label, terms))

    # 漫剧的标题与目标都取剥过壳的主题：「演一出短剧：把麻黄汤证讲成一出短剧」
    # 读着像口吃，用户确认的方案名也该和最终资源卡一样。
    subject = story_subject(topic) if kind == "story" else topic

    return ResourcePlan(
        kind=kind,
        title=subject[:60],
        goal=f"{verb}：{subject}",
        audience=str(params.get("audience") or "").strip() or ("中医专业学生" if docs else "通用读者"),
        organization="按内容要点组织",
        presentation="",
        units=[
            PlanUnit(
                label=label,
                intent=intent,
                # 方案卡上可改的是 ``detail`` 这一栏。固定骨架只有一句说明，
                # 就直接铺进去当默认文案——空着会让用户看到四个没内容的行。
                detail=intent,
                source="knowledge_base" if grounded(label, anchored) else "model",
                evidence=list(citations) if grounded(label, anchored) else [],
            )
            for label, intent, anchored in units
        ],
        gaps=[],
        evidence=tiers,
    )


def _fallback_plan(resource_type: str, topic: str, params: dict[str, Any], materials: list[str]) -> ResourcePlan:
    kind: PlanKind = resource_type if resource_type in ("ppt", "doc", "mindmap", "quiz", "story") else "doc"  # type: ignore[assignment]
    if kind == "mindmap":
        # 导图的兜底也走同一份施工图，不另抄一张分支表：提案步骤炸了的时候，
        # 用户至少要看到「按 4 支 3 层这样画」，而不是两行占位。
        spec = baseline_spec(topic, None, params)
        return ResourcePlan(
            kind="mindmap",
            title=(spec.root_topic or topic)[:60],
            goal=f"梳理「{topic}」的知识关系，用于理解与复习",
            audience=spec.audience,
            organization=spec.organization,
            presentation=f"{spec.depth} 层 · {len(spec.branches)} 支 · 可编辑思维导图",
            units=[PlanUnit(label=branch.label, intent=branch.purpose, detail="；".join(branch.must_cover), source="model") for branch in spec.branches],
            evidence=empty_evidence(),
            spec=spec.model_dump(mode="json"),
        )
    if kind == "doc":
        # 文稿兜底也走同一份施工图：提案步骤炸了的时候，用户至少要看到
        # 「这次写成 5 节的计划」这种能改的东西，而不是一行占位。
        spec = baseline_spec(topic, params=params)
        return ResourcePlan(
            kind="doc",
            title=spec.title[:60],
            goal=spec.thesis[:500],
            audience=spec.audience,
            organization=spec.organization,
            presentation=f"{spec.genre_label} · 约 {spec.target_words} 字 · {len(spec.sections)} 章",
            units=[
                PlanUnit(label=section.clean_heading, intent=section.goal, detail="；".join(section.must_cover), source="model")
                for section in spec.sections
            ],
            evidence=empty_evidence(),
            spec=spec.model_dump(mode="json"),
        )
    if kind == "ppt":
        # 讲稿同理：兜底也要给「9 页、4 种版式、每页干什么」，不能给一行占位。
        deck = deck_baseline(topic, params=params)
        return ResourcePlan(
            kind="ppt",
            title=deck.title[:60],
            goal=deck.thesis[:500],
            audience=deck.audience,
            organization=deck.organization,
            presentation=f"共 {len(deck.pages)} 页（含封面），{deck.theme} 主题，{deck.variety} 种版式，逐页可改",
            units=[
                PlanUnit(label=page.clean_heading, intent=page.goal, detail="；".join(page.must_cover), source="model")
                for page in deck.pages
            ],
            evidence=empty_evidence(),
            spec=deck.model_dump(mode="json"),
        )
    fixed = {"quiz": _QUIZ_UNITS, "story": _STORY_UNITS}.get(kind)
    blueprint = fixed or _SHAPE_BLUEPRINTS.get(task_shape(topic), {}).get(kind) or [("核心内容", "围绕主题展开"), ("小结", "收束要点")]
    # 骨架有带 anchored 的三元组，也有两元组；兜底方案不区分依据，都只取前两位。
    pairs = [(row[0], row[1]) for row in blueprint]
    return ResourcePlan(
        kind=kind,
        title=(story_subject(topic) if kind == "story" else topic)[:60],
        goal=f"完成用户要求：{topic}",
        audience=str(params.get("audience") or ""),
        organization="按主题与用途组织内容",
        presentation="",
        units=[PlanUnit(label=label, intent=intent, detail=intent, source="model") for label, intent in pairs],
        evidence=empty_evidence(),
    )


def _finalize(
    plan: ResourcePlan,
    resource_type: str,
    docs: list[ResourceDoc],
    materials: list[str],
    tiers: dict[str, list[str]],
    shape: str,
) -> ResourcePlan:
    """收口：单元数量、依据档位、缺口说明。"""

    limit = MAX_UNITS.get(resource_type, 8)
    units: list[PlanUnit] = []
    for index, unit in enumerate(plan.units[:limit]):
        if not unit.label.strip():
            continue
        unit.id = unit.id or f"u{index}"
        units.append(_verify_unit(unit, tiers, materials))
    plan.units = units
    plan.evidence = tiers
    plan.kind = resource_type if resource_type in ("ppt", "doc", "mindmap") else plan.kind  # type: ignore[assignment]
    plan.gaps = list(dict.fromkeys([gap for gap in plan.gaps if gap]))[:8]

    if not docs and not materials and not tiers["knowledge_base"]:
        note = "当前知识库没有检索到直接依据，正文将由通用模型补充并标注"
        if note not in plan.gaps:
            plan.gaps.append(note)
        if not tiers["model"]:
            tiers["model"] = ["未检索到领域资料，以下内容为通用模型补充"]
    elif not docs and not tiers["knowledge_base"]:
        plan.gaps.append("只使用了用户材料，未检索到教材依据")
    elif shape and all(unit.source == "model" for unit in plan.units):
        if plan.kind == "doc":
            plan.gaps.append("章节按这类文档的通用结构排，教材只提供内容范围；可在方案卡上改章节")
        elif plan.kind == "ppt":
            plan.gaps.append("页序按这类讲稿的通用叙事排，教材只提供内容范围；可在方案卡上改页序或版式")
        else:
            plan.gaps.append("该主题的组织方式不在教材条目内，分支结构由模型提出，可自行调整")
    plan = sync_mindmap_spec(plan)
    plan = sync_doc_spec(plan)
    return sync_deck_spec(plan)


def sync_doc_spec(plan: ResourcePlan) -> ResourcePlan:
    """让写作施工图与方案卡上的字保持一致。

    与导图同一个道理：方案卡是用户真正在改的东西——他加一节、改标题、把
    「呈现方式」里的「约 2200 字」改成 4000，改的都是 ``units`` 与那几行字。
    ``spec`` 是生成要读的结构化版本，两者必须先对齐，否则又是「改了不生效」。

    收口放在这里而不是面板里：直接调接口、老任务重放、修订模型改写都要走这条
    路径，一处对齐，所有入口都对齐。
    """

    if plan.kind != "doc" or not plan.units:
        return plan

    spec = doc_spec_of(plan.model_dump())
    if spec is None:
        # 老方案没有 spec：用 units 与那几行字拼一份，别让生成侧再另起一套。
        genre = genre_of(f"{plan.organization} {plan.goal}") or "explain"
        org = GENRE_ORG.get(genre, "topic")
        spec = DocSpec(
            title=(plan.title or "")[:40],
            genre=genre,  # type: ignore[arg-type]
            audience=plan.audience,
            organization=plan.organization or ORG_LINE[org],
            organization_kind=org,  # type: ignore[arg-type]
            thesis=plan.goal,
            sections=[
                DocSectionSpec(
                    heading=unit.label,
                    goal=unit.intent,
                    must_cover=[item.strip() for item in re.split(r"[；;、,，]", str(unit.detail or "")) if item.strip()][:5],
                )
                for unit in plan.units
            ],
        )

    spec = align_spec_with_units(spec, [unit.model_dump() for unit in plan.units])
    # 卡片上那几栏是用户看得见、改得动的，它们比 spec 里存的旧值更算数。
    if plan.goal.strip():
        spec = spec.model_copy(update={"thesis": plan.goal.strip()[:300]})
    if plan.audience.strip():
        spec = spec.model_copy(update={"audience": plan.audience.strip()[:60]})
    if plan.organization.strip():
        spec = spec.model_copy(update={"organization": plan.organization.strip()[:300]})
    if plan.title.strip():
        spec = spec.model_copy(update={"title": plan.title.strip()[:40]})

    words = words_from_text(plan.presentation) or spec.target_words
    spec = spec.model_copy(update={"target_words": min(max(int(words), 400), 12000)})

    plan.spec = spec.model_dump(mode="json")
    plan.title = spec.title
    if not plan.presentation.strip():
        plan.presentation = f"{spec.genre_label} · 约 {spec.target_words} 字 · {len(spec.sections)} 章"
    return plan


def sync_deck_spec(plan: ResourcePlan) -> ResourcePlan:
    """让讲稿施工图与方案卡上的字保持一致。

    与文稿、导图同一个道理：用户真正在改的是卡片——他加一页、改页名、把
    「共 9 页」改成 12 页，改的都是 ``units`` 与那几行字。``spec`` 是生成要读的
    结构化版本（每页什么版式、必须讲到什么、要什么素材），两者必须先对齐，
    否则又是「改了不生效」。

    **老方案（没有 spec）不在这里补**：那些稿子的版式由 ``design.propose_design``
    按关键词判，凭空补一份全是 ``points`` 的 spec 只会让老稿子变难看。
    """

    if plan.kind != "ppt" or not plan.units:
        return plan
    spec = deck_spec_of(plan.model_dump())
    if spec is None:
        return plan

    spec = align_deck_with_units(spec, [unit.model_dump() for unit in plan.units])
    # 卡片上那几栏是用户看得见、改得动的，它们比 spec 里存的旧值更算数。
    if plan.goal.strip():
        spec = spec.model_copy(update={"thesis": plan.goal.strip()[:300]})
    if plan.audience.strip():
        spec = spec.model_copy(update={"audience": plan.audience.strip()[:60]})
    if plan.organization.strip():
        spec = spec.model_copy(update={"organization": plan.organization.strip()[:300]})
    if plan.title.strip():
        spec = spec.model_copy(update={"title": plan.title.strip()[:40]})

    count = _pages_from_presentation(plan.presentation)
    if count:
        spec = spec.model_copy(update={"target_pages": min(max(count, 4), 20)})
    else:
        spec = spec.model_copy(update={"target_pages": len(spec.pages)})

    plan.spec = spec.model_dump(mode="json")
    plan.title = spec.title
    if not plan.presentation.strip():
        plan.presentation = f"共 {len(spec.pages)} 页（含封面），{spec.theme} 主题，{spec.variety} 种版式，逐页可改"
    return plan


def _pages_from_presentation(text: str) -> int | None:
    match = re.search(r"共\s*(\d+)\s*页", str(text or ""))
    return int(match.group(1)) if match else None


def sync_mindmap_spec(plan: ResourcePlan) -> ResourcePlan:
    """让 spec 与方案卡上的字保持一致。

    方案卡是用户真正在改的东西：他加一支、删一支、把「呈现方式」里的 3 层改成
    5 层，改的都是 ``units`` 与那几行字。``spec`` 是生成要读的结构化版本，
    两者必须先对齐，否则又会回到「改了但没生效」。

    放在这里而不是面板里，是因为直接调接口、老任务重放、修订模型改写都会经过
    这条收口——一处对齐，所有入口都对齐。
    """

    if plan.kind != "mindmap":
        return plan
    if not plan.units:
        return plan
    spec = dict(plan.spec or {})
    labels = [unit.label for unit in plan.units if unit.label.strip()]

    branches: list[dict[str, Any]] = []
    for unit in plan.units:
        if not unit.label.strip():
            continue
        covers = [item.strip() for item in re.split(r"[；;、,，]", str(unit.detail or "")) if item.strip()]
        branches.append(
            {
                "label": unit.label[:40],
                "purpose": (unit.intent or "")[:160],
                "must_cover": covers[:6],
            }
        )
    spec["branches"] = branches

    depth = depth_from_text(str(plan.presentation or ""))
    if depth is None:
        try:
            depth = int(spec.get("depth") or 0) or None
        except (TypeError, ValueError):
            depth = None
    if depth is not None:
        structure = str(spec.get("structure") or "logic-right")
        switched = False
        structure, switched = fit_structure(structure, depth)
        depth = min(max(depth, MIN_DEPTH), structure_capacity(structure), MAX_DEPTH)
        # 用户手改的层数就是他要的层数：别再拿旧的 depth_requested 去解释它。
        spec["depth"] = depth
        spec["depth_requested"] = max(int(spec.get("depth_requested") or 0), depth)
        spec["structure"] = structure
        if switched:
            note = f"原结构装不下 {spec['depth_requested']} 层，已改用「{structure}」；要在工具栏换回原结构也可以，但会被折叠。"
            if note not in plan.gaps:
                plan.gaps = [*plan.gaps, note][:8]
    if plan.audience.strip():
        spec["audience"] = plan.audience.strip()[:60]
    if plan.organization.strip():
        spec["organization"] = plan.organization.strip()[:200]
    spec.setdefault("basis", "rules")
    spec["branch_count"] = len(labels)
    plan.spec = spec
    return plan


def _verify_unit(unit: PlanUnit, tiers: dict[str, list[str]], materials: list[str]) -> PlanUnit:
    """逐项核对来源声明。声明了依据却拿不出引用的，降级并写清缺口。"""

    pool = {
        "knowledge_base": set(tiers["knowledge_base"]),
        "user_material": set(tiers["user_material"]),
    }
    known = pool.get(unit.source)
    if known is None:
        unit.source = "model"
        unit.evidence = []
        return unit
    unit.evidence = [item for item in unit.evidence if item in known][:6]
    if unit.source == "model":
        unit.evidence = []
        return unit
    if not unit.evidence:
        # 没带引用但词面能对上，补第一条；对不上就降级——不能凭标签冒充依据。
        haystack = " ".join(tiers[unit.source])
        if unit.label and unit.label in haystack and tiers[unit.source]:
            unit.evidence = tiers[unit.source][:1]
        else:
            unit.gap = unit.gap or ("教材未覆盖此单元，暂按模型补充处理" if unit.source == "knowledge_base" else "未找到对应用户材料")
            unit.source = "model"
            unit.evidence = []
    return unit


# ── 修订 ────────────────────────────────────────────────────────────────────


class _Revision(BaseModel):
    model_config = ConfigDict(extra="ignore")
    plan: ResourcePlan
    clarification: str = ""


async def revise(
    resource_type: str,
    topic: str,
    current: dict[str, Any],
    feedback: str,
    *,
    goal: str = "",
    params: dict[str, Any] | None = None,
    llm_call: LLMCall | None = None,
) -> dict[str, Any]:
    """按一条合并后的反馈重拟方案；用户重新描述目标则整份重来。

    三种结果都必须可解释：
      - ``applied=True``：方案已按反馈改写；
      - ``applied=False``：模型不可用或输出不可用，**原方案一字未动**；
      - ``overturned=True``：用户推翻了目标，方案按新目标重建。
    """

    feedback = " ".join(str(feedback or "").split())[:2000]
    goal = " ".join(str(goal or "").split())[:200]
    params = dict(params or {})

    if goal and goal != str(current.get("goal") or ""):
        rebuilt = await propose(resource_type, goal, params, llm_call=llm_call)
        rebuilt["revision"] = int(current.get("revision") or 0) + 1
        rebuilt["updated_at"] = _now()
        rebuilt["overturned"] = True
        rebuilt["applied"] = True
        if feedback:
            merged = await _apply_feedback(resource_type, goal, rebuilt, feedback, params, llm_call=llm_call)
            if merged is not None:
                merged["overturned"] = True
                return merged
        return rebuilt

    merged = await _apply_feedback(resource_type, topic, current, feedback, params, llm_call=llm_call)
    if merged is not None:
        return merged

    # 降级：保留原方案，明确告诉用户「没改」，而不是假装改过。
    kept = normalize_plan(resource_type, current) if current.get("kind") else None
    payload = (kept or _fallback_plan(resource_type, topic, params, user_materials(params))).model_dump(mode="json")
    payload["applied"] = False
    payload["revision"] = int(current.get("revision") or 0)
    payload["updated_at"] = str(current.get("updated_at") or _now())
    payload["notice"] = "当前没有可用的修订模型，方案未改动；可直接在面板里逐项编辑，或稍后重试。"
    return payload


async def _apply_feedback(
    resource_type: str,
    topic: str,
    current: dict[str, Any],
    feedback: str,
    params: dict[str, Any],
    *,
    llm_call: LLMCall | None,
) -> dict[str, Any] | None:
    base = normalize_plan(resource_type, current)
    prompt = _revision_prompt(resource_type)
    payload = {
        "topic": topic,
        "resource_type": resource_type,
        "current_plan": base.model_dump(mode="json"),
        "feedback": feedback,
        "user_materials": user_materials(params)[:3],
    }
    try:
        if llm_call is not None:
            raw = await llm_call(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ]
            )
            revision = _Revision.model_validate_json(_extract_json(raw))
        else:
            router = ModelRouter()
            if isinstance(router.select("generate"), MockClient):
                return None
            from app.agents.structured_output import structured_create

            revision = await structured_create(
                task_type="generate",
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                response_model=_Revision,
                mock_factory=lambda: _Revision(plan=base),
                router=router,
            )
    except Exception:  # noqa: BLE001 - 修订失败必须可解释地退回原方案
        return None

    revised = revision.plan
    revised.kind = base.kind
    revised.evidence = base.evidence
    # 修订模型不一定记得带上施工图：没带就沿用上一版，免得用户改一句话
    # 就把层数/画法/必答项全丢了。
    if not revised.spec:
        revised.spec = dict(base.spec or {})
    revised.gaps = list(dict.fromkeys([*base.gaps, *revised.gaps]))[:8]
    revised.revision = int(current.get("revision") or 0) + 1
    revised.updated_at = _now()
    revised.clarification = revision.clarification[:300]
    result = _finalize(
        revised,
        resource_type,
        [],
        user_materials(params),
        base.evidence or empty_evidence(),
        "",
    )
    output = result.model_dump(mode="json")
    output["applied"] = True
    output["overturned"] = False
    if not output["units"]:
        return None
    return output


def _revision_prompt(resource_type: str) -> str:
    unit_hint = {
        "ppt": "units 是幻灯页：label 为页标题，intent 为该页要达成的目的，detail 为要点（用；分隔）。",
        "doc": "units 是正文章节：label 为章节标题，intent 为该节要回答的问题，detail 为要点（用；分隔）。",
        "mindmap": (
            "units 是一级分支：label 为分支名，intent 为该分支要覆盖的内容，detail 为必须出现的子要点（用；分隔）。"
            "spec 是这张图的施工图（depth 层数、structure 画法、organization 组织方式、branches 分支轴、"
            "emphasis 用户明确提的要求、must_answer 必答项）。改层数、改画法、改受众都改 spec；"
            "**spec.branches 必须与 units 一一对应**，改了分支就同步改两边。"
        ),
    }[resource_type]
    spec_rule = ""
    if resource_type == "mindmap":
        spec_rule = (
            "8. 用户说「多分几层 / 更细 / 多几个角度」这类没有数字的要求时，把 spec.depth 提到 4~5，"
            "并把 presentation 里的层数一起改掉；用户说「改成 N 层」时以用户给的数为准。\n"
            "9. 用户提了新的内容要求（如「再加一项怎么学」），要真的加一支或加进 must_answer，"
            "只在 organization 里写一句不算改。\n"
        )
    return (
        "你在修订一份资源制作方案。要求：\n"
        "1. 先理解用户这条反馈真正想改什么，再把反馈合并进方案；不要逐条罗列，也不要复述反馈。\n"
        "2. 保留上一次方案里没有被反馈否定的目标、受众、组织方式和单元；只改动受影响的部分。\n"
        "3. 只能返回完整方案，不能只返回 diff。\n"
        "4. 不得虚构引用。source 只取 knowledge_base / user_material / model 三值；"
        "拿不出依据的单元一律标 model，并在 gaps 里说明缺口。\n"
        "5. 只有反馈缺少关键信息、无法确定改法时才填 clarification（一句话提问），其余情况留空。\n"
        f"6. {unit_hint}\n"
        f"{spec_rule}"
        "最后只输出 JSON：{\"plan\": {...}, \"clarification\": \"\"}。"
    )


def _extract_json(text: str) -> str:
    stripped = (text or "").strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        return stripped[start : end + 1]
    raise ValueError("修订输出中没有 JSON")


# ── 校验与兼容 ──────────────────────────────────────────────────────────────


def normalize_plan(resource_type: str, raw: dict[str, Any] | None) -> ResourcePlan:
    """把前端回传的方案收敛成合法契约。

    兼容两种历史形态：``units``（新）与 ``items: string[]``（旧实验版）。
    兼容不是目的，避免「确认时 500」才是。
    """

    value = dict(raw or {})
    if not value.get("units") and value.get("items"):
        value["units"] = [{"label": str(item)[:80]} for item in value["items"] if str(item).strip()]
    value.pop("items", None)
    value["kind"] = resource_type if resource_type in ("ppt", "doc", "mindmap") else value.get("kind", "doc")
    plan = ResourcePlan.model_validate(value)
    plan.evidence = {
        "knowledge_base": list(plan.evidence.get("knowledge_base") or [])[:12],
        "user_material": list(plan.evidence.get("user_material") or [])[:5],
        "model": list(plan.evidence.get("model") or [])[:5],
    }
    # 确认闸门也是「方案 → 生成」的入口：手改过的单元、层数、篇幅在这里对齐到
    # spec，生成读到的就是用户最后看到的那一版。
    plan = sync_mindmap_spec(plan)
    plan = sync_doc_spec(plan)
    return sync_deck_spec(plan)


def confirm_payload(resource_type: str, raw: dict[str, Any]) -> ResourcePlan:
    """确认入口的强校验：空方案、错类型、超长单元都在这里拦住。"""

    plan = normalize_plan(resource_type, raw)
    if not plan.units:
        raise ValueError("方案至少需要包含一个内容单元")
    if len(plan.units) > MAX_UNITS.get(resource_type, 8):
        raise ValueError(f"方案单元过多（上限 {MAX_UNITS.get(resource_type, 8)}）")
    if not plan.goal.strip():
        raise ValueError("方案缺少目标描述")
    if any(not unit.label.strip() for unit in plan.units):
        raise ValueError("存在没有标题的内容单元")
    return plan


def plan_units(raw: dict[str, Any] | None) -> list[PlanUnit]:
    """生成阶段读方案：只认已经落到任务里的那一份。"""

    if not isinstance(raw, dict):
        return []
    try:
        return list(normalize_plan(str(raw.get("kind") or "doc"), raw).units)
    except Exception:  # noqa: BLE001 - 老/坏数据不该让生成直接炸
        return []
