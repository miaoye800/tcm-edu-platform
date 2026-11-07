"""图谱能力的对外契约层。

``app/kg/`` 里有九个函数，但它们不是九个「工具」。调用方（对话 Agent、路由、
生成器）不该看见 ``hops`` / ``limit`` / ``tiers`` 这些实现参数——那是把 BFS
的旋钮直接交给模型。这一层把它们收敛成**六个能力**，每个能力只回答三件事：

1. 什么问题该用我（``use_when``，每条带一个真实问句）
2. 什么问题别用我（``avoid_when``）
3. 我返回什么形状、查不到时是什么形状

**为什么 description 要写「别用我」**：模型不是不会调工具，是不知道该在什么
时候想起它。只写「我能查图谱」，等于没写。

溯源用两个**正交布尔**，不用 ``trace_level: 1|2|3``：

- ``has_text``：有没有教材原文。决定「能不能直接引用」
- ``linkable``：有没有 edge_id。决定「能不能跳回图上那条边」

四种组合都真实存在：正常边 (T,T)、处方审查降级路径 (T,F)、相似候选 (F,F)。
把两个维度压成一个 1/2/3 是有损的，会逼调用方二选一。

**证据字段的坑**：5512 条 evidence 里 ``ref`` 只有 2175 条，``book`` 有 3337
条——六成只有书名没有 ref。全链路只能走 :func:`evidence_span` 一个入口，
它会做 ``ref or book``。各自写 ``evidence["ref"]`` 的地方迟早输出空串。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Mapping, Sequence

from app.kg.constants import Tier, is_symmetric
from app.profile.store import DB_PATH

if TYPE_CHECKING:  # pragma: no cover - 只用于类型标注
    from app.kg.ask import AskResult

CapabilityName = Literal["ask", "lookup", "compare", "path", "review", "attribute"]

#: 六个能力的稳定顺序。工具定义与文档都按这个顺序输出，便于逐项比对。
CAPABILITY_ORDER: tuple[CapabilityName, ...] = (
    "ask",
    "lookup",
    "compare",
    "path",
    "review",
    "attribute",
)


# --------------------------------------------------------------------------- #
# 1. 证据归一化
# --------------------------------------------------------------------------- #


def evidence_span(evidence: Mapping[str, Any] | None) -> dict[str, Any]:
    """把任意来源的 evidence 归一成一种形状。

    ``ref`` 缺失时退回 ``book``——六成边只有书名。不这么做，citation 会输出
    空串，用户看到「p149」却不知道是哪本书。
    """

    item = evidence or {}
    quote = str(item.get("quote") or "").strip()
    return {
        "text_span": quote,
        "page": item.get("page"),
        "ref": str(item.get("ref") or item.get("book") or ""),
        "has_text": bool(quote),
    }


def provenance(edge_id: int | None, text_span: str) -> dict[str, bool]:
    """两个正交的溯源维度，不要压成一个等级。"""

    return {"has_text": bool(text_span), "linkable": edge_id is not None}


def _spans(evidence: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    return [evidence_span(item) for item in (evidence or []) if item]


def _first_span(evidence: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    spans = _spans(evidence)
    return spans[0] if spans else evidence_span(None)


# --------------------------------------------------------------------------- #
# 2. 能力声明
# --------------------------------------------------------------------------- #

#: 路由词表。**描述与判定同源**：``Capability.triggers`` 就是这些分组的拼接，
#: 所以文档里承诺「什么时候该用」的东西，就是路由真正认的东西。
#:
#: 命名上刻意与具体的 ``Capability`` 常量（``_CAP_*``）分开：这两组名字
#: 一旦撞车（早先 ``_PATH`` 既是词表又是能力对象），``plan()`` 会拿到一个
#: ``Capability`` 去遍历，直接 ``TypeError``。词表保持裸名，能力对象统一
#: 加 ``_CAP_`` 前缀。
_CAUSAL = ("为什么", "为何", "凭什么", "原因", "怎么会", "依据")
_CONTRA = (
    "能一起",
    "能不能同用",
    "能不能一起",
    "同方",
    "一起吃",
    "配伍禁忌",
    "十八反",
    "十九畏",
    "相反",
    "相畏",
    "相恶",
)
_DIFF = ("区别", "鉴别", "异同", "不同", "差异", "怎么区分", "哪个更", "对比", "不一样")
_PATH = ("路径", "怎么扯上", "怎么联系", "中间经过", "关系链", "怎么连", "通过什么")
_REL = ("一起用", "常配", "配伍", "相须", "相使", "同用", "搭配", "配在一起")
_SINGLE = ("组成", "哪些药", "主治哪些", "能治", "治什么", "和谁", "什么药", "和什么")
_LOOKUP = ("用什么", "吃什么药", "该用哪", "收录", "别名", "也叫", "是不是同一", "图谱里有没有")
_REVIEW = ("处方", "方子", "这副药", "开方", "超量", "剂量")
_ATTR = ("错题", "为什么错了", "错在哪", "要补什么", "这题我错了")


@dataclass(frozen=True)
class Capability:
    """一个对外能力的完整声明。

    ``use_when`` / ``avoid_when`` 里每一条都要带一个真实问句——纯抽象的
    「适用于关系查询」对模型没有约束力，它需要看到「长什么样的句子」。
    """

    name: CapabilityName
    title: str
    one_line: str
    use_when: tuple[str, ...]
    avoid_when: tuple[str, ...]
    triggers: tuple[str, ...]
    single_seed_triggers: tuple[str, ...]
    min_entities: int
    input_schema: dict[str, str]
    output_shape: dict[str, Any]
    failure_note: str
    tool_input: dict[str, str]
    tool_output: str

    def description(self) -> str:
        """完整说明。这是盲测与文档的唯一输入，必须自足。"""

        def bullets(items: Sequence[str]) -> str:
            return "\n".join(f"- {item}" for item in items)

        inputs = "；".join(f"{key}: {value}" for key, value in self.input_schema.items())
        return (
            f"【能力】{self.name} — {self.one_line}\n"
            f"【什么时候该用】\n{bullets(self.use_when)}\n"
            f"【什么时候别用】\n{bullets(self.avoid_when)}\n"
            f"【输入】{inputs}\n"
            f"【输出】{', '.join(self.output_shape)}\n"
            f"【查不到时】{self.failure_note}"
        )

    def tool_schema(self) -> dict[str, Any]:
        """精简版，给 tool 定义用。六百 token 以内能塞下六个能力。"""

        return {
            "name": f"kg_{self.name}",
            "one_line": self.one_line,
            "input": self.tool_input,
            "output": self.tool_output,
            "failure": self.failure_note,
        }


_CAP_ASK = Capability(
    name="ask",
    title="沿图谱查关系",
    one_line="查两个实体之间的教材明文关系，或单味药的主治与组成",
    use_when=(
        "问「为什么」：如「麻黄和桂枝为什么常一起用」",
        "问「能不能一起用」：如「人参和藜芦能一起用吗」",
        "问某味药治哪些证、某个方由哪些药组成：如「麻黄能治哪些证」",
    ),
    avoid_when=(
        "只问单味药的性味、功效、用量：如「麻黄的功效是什么」——普通检索更全",
        "问两味药的区别：如「麻黄和桂枝有什么区别」——用 compare",
        "问教材没收录的药：如「紫河车有什么功效」——先确认实体存在",
    ),
    triggers=_CAUSAL + _CONTRA + _REL,
    single_seed_triggers=_SINGLE,
    min_entities=1,
    input_schema={"question": "问句原文", "seed_ids": "可选，检索命中的实体 id"},
    output_shape={
        "ok": "bool，认出实体即 true",
        "question": "str，原问题",
        "seeds": "list，识别到的起点",
        "relations": "list，每条带 edge_ids/tier/text_span/page/ref",
        "answer": "str，确定性摘要",
        "truncated": "bool",
    },
    failure_note="ok=false 且 reason 说明没认出起点，不编造答案；认出实体但无边时 ok=true、relations=[]。",
    tool_input={"question": "string", "seed_ids": "string[]?"},
    tool_output="seeds[], relations[].{edge_ids,tier,from,to,text_span,page,ref}, answer",
)

_CAP_LOOKUP = Capability(
    name="lookup",
    title="实体定位与反查",
    one_line="把名字对到实体，或从症状倒推证、方、药",
    use_when=(
        "问某症状该用什么：如「咽喉肿痛用什么药」",
        "确认某个名字收没收录、有没有别的叫法：如「淮山是不是山药」",
        "问某味药和哪些药相似：如「麻黄和哪几味药功效相近」",
    ),
    avoid_when=(
        "已经知道两个实体名、要问它们的关系：如「麻黄和桂枝能一起用吗」——用 ask",
        "问某个方剂的完整组成：如「麻黄汤由哪些药组成」——用 ask（图谱有组成边）",
    ),
    triggers=_LOOKUP,
    single_seed_triggers=(),
    min_entities=0,
    input_schema={"query": "名字或症状短语", "mode": "resolve | reverse | similar"},
    output_shape={
        "ok": "bool",
        "mode": "resolve | reverse | similar",
        "query": "str，原查询",
        "entities": "list，resolve 模式命中",
        "syndromes": "list，reverse 模式：证→方→药",
        "similar": "list，similar 模式（linkable 恒 false）",
    },
    failure_note="ok=false 且给 hint 提示教材里的说法，不编近义词。",
    tool_input={"query": "string", "mode": "string?"},
    tool_output="entities[] 或 syndromes[].{herbs[],formulas[]} 或 similar[]",
)

_CAP_COMPARE = Capability(
    name="compare",
    title="两味药逐项对比",
    one_line="两味药在性味、归经、功效、主治、用量、毒性上的逐项差异",
    use_when=(
        "问两味药的区别：如「麻黄和桂枝有什么区别」",
        "问哪个更适合某种情况：如「气虚用黄芪还是党参」",
    ),
    avoid_when=(
        "问「能不能一起用」：如「人参和藜芦能一起用吗」——用 ask，禁忌是硬约束不能被对比表淡化",
        "只问一味药：如「麻黄有什么功效」——用普通检索",
    ),
    triggers=_DIFF,
    single_seed_triggers=(),
    min_entities=2,
    input_schema={"left": "左实体名/别名/id", "right": "右实体名/别名/id"},
    output_shape={
        "ok": "bool",
        "left": "左实体简述",
        "right": "右实体简述",
        "differences": "list，逐项差异",
        "textbook_differentiation": "教材原文鉴别（可空）",
        "constraints": "T1 硬约束列表",
        "shared_relations": "两侧共有的关系类型",
        "summary": "str",
    },
    failure_note="ok=false 且 reason 指出哪一侧没有结构化条目，不硬凑对比表。",
    tool_input={"left": "string", "right": "string"},
    tool_output="differences[].{label,left,right}, textbook_differentiation, constraints[], summary",
)

_CAP_PATH = Capability(
    name="path",
    title="两实体间关系链",
    one_line="两个实体之间最短的关系链，回答「这两者是怎么扯上关系的」",
    use_when=(
        "问两个看似无关的东西怎么连起来：如「麻黄和杏仁是怎么扯上关系的」",
        "问从症状到方剂中间经过什么：如「从恶寒发热到麻黄汤中间经过什么」",
    ),
    avoid_when=(
        "直接问两味药的配伍关系：如「麻黄和桂枝能一起用吗」——用 ask，一跳就够",
        "问两个实体有没有关系（只要一个 yes/no）：如「麻黄和桂枝有关系吗」——用 ask",
    ),
    triggers=_PATH,
    single_seed_triggers=(),
    min_entities=2,
    input_schema={"from_key": "起点实体", "to_key": "终点实体", "max_hops": "默认 4"},
    output_shape={
        "ok": "bool",
        "found": "bool",
        "hops": "int",
        "from": "起点名",
        "to": "终点名",
        "path": "list，每步带 edge_id/tier",
        "sentence": "str",
    },
    failure_note="没有路径时 ok=true、found=false，并说明「教材没写这条关系」而不是「没关系」。",
    tool_input={"from_key": "string", "to_key": "string", "max_hops": "number?"},
    tool_output="hops, path[].{edge_id,tier,from,to,text_span,page}, sentence",
)

_CAP_REVIEW = Capability(
    name="review",
    title="处方审查",
    one_line="审查一张处方：禁忌、剂量、毒性、证候匹配",
    use_when=(
        "给一张处方问有没有问题：如「人参9g，藜芦3g，这方子有问题吗」",
        "问某味药的剂量是否超量：如「附子用30g会不会超量」",
    ),
    avoid_when=(
        "只是问两味药能不能一起用、没有整张方：如「人参和藜芦能一起用吗」——用 ask",
        "问某味药的常规用量是多少：如「附子常规用多少克」——用普通检索",
    ),
    triggers=_REVIEW,
    single_seed_triggers=(),
    min_entities=0,
    input_schema={"prescription": "处方原文", "case": "可选，病例描述"},
    output_shape={
        "ok": "bool",
        "verdict": "pass | warn | block",
        "checks": "compatible/dosage/toxicity/symptom 四项三态",
        "findings": "list，每条带 text_span/severity/rule",
        "herbs": "逐味药解析",
        "submitted_text": "用户提交的处方原文",
        "knowledge_base_refs": "list",
    },
    failure_note="认不出药时 ok=true、verdict=warn，绝不当成 pass。",
    tool_input={"prescription": "string", "case": "string?"},
    tool_output="verdict, checks{}, findings[].{text_span,severity,rule,edge_ids}",
)

_CAP_ATTRIBUTE = Capability(
    name="attribute",
    title="错题归因",
    one_line="一道错题为什么错、该补哪块",
    use_when=(
        "问某道错题为什么错：如「我这题为什么错了」",
        "问错了该补什么：如「这道题错了要补哪块」",
    ),
    avoid_when=("一般知识问答：如「麻黄有什么功效」——用 ask 或普通检索",),
    triggers=_ATTR,
    single_seed_triggers=(),
    min_entities=0,
    input_schema={"mistake_id": "错题记录 id"},
    output_shape={
        "ok": "bool",
        "mistake_id": "错题 id",
        "causes": "list，每条带 cause_type/edge_id/text_span/page",
    },
    failure_note="找不到错题时 ok=false；算不出原因时给 unknown 并说明，不猜。",
    tool_input={"mistake_id": "string"},
    tool_output="causes[].{cause_type,edge_id,text_span,page}",
)

CAPABILITIES: dict[CapabilityName, Capability] = {
    "ask": _CAP_ASK,
    "lookup": _CAP_LOOKUP,
    "compare": _CAP_COMPARE,
    "path": _CAP_PATH,
    "review": _CAP_REVIEW,
    "attribute": _CAP_ATTRIBUTE,
}


def get(name: str) -> Capability:
    try:
        return CAPABILITIES[name]  # type: ignore[index]
    except KeyError as exc:  # pragma: no cover - 防御性
        raise KeyError(f"未知能力：{name}") from exc


def tool_schemas() -> list[dict[str, Any]]:
    """六个能力的 tool 定义，按稳定顺序。"""

    return [CAPABILITIES[name].tool_schema() for name in CAPABILITY_ORDER]


def describe_all() -> str:
    """全部能力的完整说明，段间空行分隔。盲测与文档都吃这一份。"""

    return "\n\n".join(CAPABILITIES[name].description() for name in CAPABILITY_ORDER)


# --------------------------------------------------------------------------- #
# 3. 路由判定
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RoutePlan:
    """一次路由判定的结果。

    ``capability is None`` 的语义是「不该调图谱」，不是「调用失败」。这两件
    事在调用方完全不一样：前者照常走检索，后者要报错。
    """

    capability: CapabilityName | None
    seeds: tuple[Any, ...]
    marker: str
    rule: str
    reason: str


def _hit(text: str, words: Sequence[str]) -> str:
    for word in words:
        if word in text:
            return word
    return ""


#: 问句焦点 → 该优先展示哪一类关系。
#:
#: 单实体问题有两种完全不同的意图：「麻黄能治哪些证」要药证链，「麻黄和谁常配」
#: 要配伍。同一份邻域里两类边都有，只按关系名排序必然答错一半——所以排序必须
#: 看问句，不能只看边。
_FOCUS_PAIR = ("十八反", "十九畏", "配伍禁忌", "妊娠禁忌", "相须", "相使", "相恶", "相畏", "相杀", "配伍")
#: 「这个方由哪些药组成」——组成边是答案本身。
_FOCUS_FORMULA = ("组成", "药证链", "主治", "功效")
#: 「这味药能治什么」——药证链是答案，组成只是邻域背景。
_FOCUS_INDICATION = ("药证链", "主治", "功效", "组成")

_FORMULA_WORDS = ("组成", "哪些药", "什么药")
_INDICATION_WORDS = ("主治哪些", "能治", "治什么")


def relation_focus(text: str) -> tuple[str, ...]:
    """问句问的是「怎么配」「治什么」还是「由什么组成」，决定关系排序的先后。

    只看问句、不看实体个数：``n`` 只说明题面提了几味药，不说明它想问什么。
    配伍类词优先判——「麻黄和谁常配」同时含「和谁」与「常配」，语义上是后者。
    """

    question = text or ""
    if _hit(question, _REL) or _hit(question, _CONTRA) or _hit(question, _CAUSAL):
        return _FOCUS_PAIR
    if _hit(question, _FORMULA_WORDS):
        return _FOCUS_FORMULA
    if _hit(question, _INDICATION_WORDS):
        return _FOCUS_INDICATION
    return ()


def _looks_like_prescription(text: str) -> bool:
    import re

    return bool(re.search(r"\d+(?:\.\d+)?\s*(?:g|克|钱|两)", text))


#: 「像一张方子要人审」的旁证词。缺了它们，「麻黄汤是什么方子」这种知识题
#: 会被当成审查请求——问的是方子叫什么，不是让谁看看这方子能不能吃。
_PRESCRIPTION_CUES = ("超量", "剂量", "克数", "有问题", "行不行", "能不能开", "配伍禁忌")


def plan(question: str, *, db_path: Path = DB_PATH) -> RoutePlan:
    """确定性路由：问题 → 该调哪个能力（或不该调）。

    这是**生产可用的规则路由**，不是测试专用替身。三处共用同一份判定：

    - 判据表（``tests/test_kg_judgement.py``）验收它
    - 主链路用它做图谱扩展的门槛
    - 未来接 LLM tool-calling 时，它作前置门与兜底

    优先级里有两处刻意的顺序：

    1. **禁忌压过鉴别**。「人参和藜芦能一起用吗，有什么区别」必须走 ask——
       安全问题不能输给辨析需求。
    2. **因果压过一切内容型**。「为什么」要的是边，不是对照表。

    实体识别一律用严格模式（``fuzzy=False``）：模糊回落会给「抗生素怎么用」
    硬凑一个实体，把本该走普通检索的问题扩成一张无关的邻图。
    """

    from app.kg import symptoms
    from app.kg.ask import detect_seeds

    text = (question or "").strip()
    if not text:
        return RoutePlan(None, (), "", "empty", "空问题")

    seeds = tuple(detect_seeds(text, db_path=db_path, fuzzy=False))
    n = len(seeds)

    marker = _hit(text, _ATTR)
    if marker:
        return RoutePlan("attribute", seeds, marker, "attribute_word", "命中错题归因词")

    marker = _hit(text, _REVIEW)
    if marker and (
        _looks_like_prescription(text) or "处方" in text or _hit(text, _PRESCRIPTION_CUES)
    ):
        return RoutePlan("review", seeds, marker, "review_shape", "像一张处方且有审查词")

    if n >= 2:
        marker = _hit(text, _PATH)
        if marker:
            return RoutePlan("path", seeds, marker, "path_over_others", "两个实体且问路径")

        marker = _hit(text, _CAUSAL)
        if marker:
            return RoutePlan("ask", seeds, marker, "causal_over_diff", "问因果，要的是边不是对照表")

        marker = _hit(text, _CONTRA)
        if marker:
            return RoutePlan("ask", seeds, marker, "contra_over_diff", "问禁忌，安全优先于辨析")

        marker = _hit(text, _DIFF)
        if marker:
            return RoutePlan("compare", seeds, marker, "diff_words", "问区别")

        marker = _hit(text, _REL)
        if marker:
            return RoutePlan("ask", seeds, marker, "relation_words", "问关系")

    if n == 1:
        marker = _hit(text, _SINGLE)
        if marker:
            return RoutePlan("ask", seeds, marker, "single_seed_relation", "单实体但问的是关系/组成/主治")

    marker = _hit(text, _LOOKUP)
    # 这里刻意不是 ``n == 0``：「麻黄在教材里收录了吗」「X 的别名是什么」都
    # 已经认出实体了，而「收录/别名」问的正是这个实体在不在库里——认出实体
    # 恰恰是能回答的前提。上界收到 ``n <= 1`` 是为了不让它抢走双实体的关系题。
    if marker and n <= 1:
        # 反查只在「一个药都没点名」时才考虑：问「咳嗽用什么药」是反查，
        # 问「麻黄收录了吗」是查收录，后者若被症状词表接走，日志里会写着
        # symptom_reverse，排查时极易误判。
        if n == 0 and symptoms.candidates(text, top_k=1, db_path=db_path):
            return RoutePlan("lookup", seeds, marker, "symptom_reverse", "症状词表命中，走反查")
        return RoutePlan("lookup", seeds, marker, "lookup_words", "问收录或别名")

    return RoutePlan(None, seeds, "", "plain_rag", f"认出 {n} 个实体但无关系意图")


# --------------------------------------------------------------------------- #
# 4. 归一化：原始返回 → 契约形状
# --------------------------------------------------------------------------- #


def failure(capability: CapabilityName, reason: str, *, hint: str = "") -> dict[str, Any]:
    """统一的失败形状。三种性质必须分开，不能都用一句「失败」。"""

    return {"ok": False, "capability": capability, "reason": reason, "hint": hint}


def _fact_key(item: Mapping[str, Any]) -> tuple[str, str, str]:
    source = str((item.get("source") or {}).get("id") or "")
    target = str((item.get("target") or {}).get("id") or "")
    relation = str(item.get("relation_type") or "")
    if is_symmetric(relation):
        source, target = sorted((source, target))
    return (source, target, relation)


def _relation_fact(item: Mapping[str, Any]) -> dict[str, Any]:
    edge_id = item.get("edge_id")
    edge_ids = list(item.get("edge_ids") or ([edge_id] if edge_id else []))
    spans = _spans(item.get("evidence"))
    first = spans[0] if spans else evidence_span(None)
    return {
        "edge_ids": edge_ids,
        "relation_type": str(item.get("relation_type") or ""),
        "tier": int(item.get("tier") or Tier.FACT),
        "from": (item.get("source") or {}).get("name", ""),
        "to": (item.get("target") or {}).get("name", ""),
        "label": str(item.get("label") or ""),
        "evidence": spans,
        "provenance": provenance(edge_id, first["text_span"]),
    }


def normalize_ask(result: "AskResult") -> dict[str, Any]:
    """``ask`` 的对外形状。"""

    if not result.seeds:
        return failure(
            "ask",
            result.answer or "没认出问题里的实体，图谱无法给出起点",
            hint="换个教材里的说法试试，例如用「麻黄」而不是「麻黄草」",
        )

    context = result.context or {}
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in list(context.get("constraints") or []) + list(context.get("relations") or []):
        key = _fact_key(item)
        if key in seen:
            continue
        seen.add(key)
        merged.append(_relation_fact(item))

    return {
        "ok": True,
        "capability": "ask",
        "question": result.question,
        "seeds": [
            {"id": seed.entity_id, "name": seed.name, "kind": seed.kind, "matched_by": seed.matched_by}
            for seed in result.seeds
        ],
        "relations": merged,
        "answer": result.answer,
        "truncated": bool(context.get("truncated")),
    }


def normalize_compare(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not payload.get("ok"):
        return failure("compare", str(payload.get("reason") or "无法对比"))

    textbook = payload.get("textbook_differentiation")
    textbook_out = None
    if textbook:
        span = evidence_span(textbook)
        # ``edge_id`` 必须留着：契约层的 ``provenance.linkable`` 就是靠它，
        # 丢了的话这条「教材鉴别」在调用方眼里变成「有原文但点不进去」，
        # 而它恰恰是整张对比表里最该能点开的一条。
        textbook_out = {
            "edge_id": textbook.get("edge_id"),
            **span,
            "provenance": provenance(textbook.get("edge_id"), span["text_span"]),
        }

    constraints = []
    for item in payload.get("constraints") or []:
        span = evidence_span(item)
        constraints.append(
            {
                "relation_type": str(item.get("relation_type") or ""),
                "tier": int(item.get("tier") or Tier.CONSTRAINT),
                **span,
                "provenance": provenance(item.get("edge_id"), span["text_span"]),
            }
        )

    return {
        "ok": True,
        "capability": "compare",
        "left": payload.get("left"),
        "right": payload.get("right"),
        "differences": [
            {
                "field": str(item.get("field") or ""),
                "label": str(item.get("label") or ""),
                "left": list(item.get("left") or []),
                "right": list(item.get("right") or []),
                "only_left": list(item.get("only_left") or []),
                "only_right": list(item.get("only_right") or []),
            }
            for item in payload.get("differences") or []
        ],
        "textbook_differentiation": textbook_out,
        "constraints": constraints,
        "shared_relations": [str(item.get("relation_type") or "") for item in payload.get("shared_relations") or []],
        "summary": str(payload.get("summary") or ""),
    }


def normalize_path(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not payload.get("ok"):
        return failure("path", str(payload.get("reason") or "无法查找路径"))

    steps = []
    for step in payload.get("path") or []:
        span = _first_span(step.get("evidence"))
        steps.append(
            {
                "edge_id": step.get("edge_id"),
                "tier": int(step.get("tier") or Tier.FACT),
                "relation_type": str(step.get("relation_type") or ""),
                "from": (step.get("from") or {}).get("name", ""),
                "to": (step.get("to") or {}).get("name", ""),
                "label": str(step.get("label") or ""),
                **span,
                "provenance": provenance(step.get("edge_id"), span["text_span"]),
            }
        )

    return {
        "ok": True,
        "capability": "path",
        "found": bool(payload.get("found")),
        "hops": int(payload.get("hops") or len(steps)),
        "from": (payload.get("from") or {}).get("name", ""),
        "to": (payload.get("to") or {}).get("name", ""),
        "path": steps,
        "sentence": str(payload.get("sentence") or payload.get("reason") or ""),
    }


def normalize_resolve(query: str, entity: Any) -> dict[str, Any]:
    """lookup 的 resolve 模式：名字 → 实体。

    解析不到就是解析不到——不按拼音猜、不按近义名补。图谱查不到的东西，
    调用方应该走普通检索，而不是让图谱硬凑一个。
    """

    if entity is None:
        return failure(
            "lookup",
            f"「{query}」在教材里没有对应条目",
            hint="试试教材里的正规名或已收录的别名",
        )

    return {
        "ok": True,
        "capability": "lookup",
        "mode": "resolve",
        "query": query,
        "entities": [
            {
                "id": str(getattr(entity, "id", "")),
                "name": str(getattr(entity, "name", "")),
                "kind": str(getattr(entity, "kind", "")),
                "status": str(getattr(entity, "status", "")),
            }
        ],
    }


def _hop_out(hop: Mapping[str, Any]) -> dict[str, Any]:
    span = _first_span(hop.get("evidence"))
    return {
        "name": str(hop.get("name") or ""),
        "relation_type": str(hop.get("relation_type") or ""),
        "tier": int(hop.get("tier") or Tier.FACT),
        "edge_id": hop.get("edge_id"),
        **span,
        "provenance": provenance(hop.get("edge_id"), span["text_span"]),
    }


def normalize_reverse(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not payload.get("ok"):
        return failure(
            "lookup",
            str(payload.get("reason") or "无法反查"),
            hint=str(payload.get("hint") or ""),
        )

    syndromes = []
    for item in payload.get("syndromes") or []:
        syndromes.append(
            {
                "name": str(item.get("name") or ""),
                "score": float(item.get("score") or 0.0),
                "in_graph": bool(item.get("in_graph")),
                "complete": bool(item.get("complete")),
                "herbs": [_hop_out(hop) for hop in item.get("herbs") or []],
                "formulas": [_hop_out(hop) for hop in item.get("formulas") or []],
            }
        )

    return {"ok": True, "capability": "lookup", "mode": "reverse", "query": payload.get("query"), "syndromes": syndromes}


def normalize_similar(name: str, hits: Sequence[Any]) -> dict[str, Any]:
    """T3 相似：**没有 edge_id，linkable 恒 false**。它只能给候选，不能当证据。"""

    return {
        "ok": True,
        "capability": "lookup",
        "mode": "similar",
        "query": name,
        "similar": [
            {
                "name": str(getattr(hit, "name", "")),
                "score": float(getattr(hit, "score", 0.0)),
                "relation_type": str(getattr(hit, "relation_type", "")),
                "provenance": provenance(None, ""),
            }
            for hit in hits
        ],
    }


def normalize_finding(finding: Any) -> dict[str, Any]:
    """``ReviewFinding.quote`` 是**用户处方原文**，与 citations 的教材原文同名不同义。"""

    spans = _spans(getattr(finding, "evidence", []) or [])
    return {
        "kind": str(finding.kind),
        "severity": str(finding.severity),
        "text_span": str(finding.quote),
        "reason": str(finding.reason),
        "rule": str(finding.rule),
        "edge_ids": list(getattr(finding, "edge_ids", []) or []),
        "evidence": spans,
        "provenance": provenance(
            (getattr(finding, "edge_ids", []) or [None])[0],
            spans[0]["text_span"] if spans else "",
        ),
    }


def normalize_review(result: Any) -> dict[str, Any]:
    return {
        "ok": True,
        "capability": "review",
        "verdict": str(result.verdict),
        "submitted_text": str(result.quote),
        "checks": dict(result.checks),
        "findings": [normalize_finding(item) for item in result.findings],
        "herbs": [
            {
                "name": str(getattr(line, "name", "")),
                "dose_status": str(getattr(line, "dose_status", "")),
            }
            for line in result.herbs
        ],
        "knowledge_base_refs": list(result.knowledge_base_refs),
    }


def normalize_attribution(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not payload.get("ok"):
        return failure("attribute", str(payload.get("reason") or "无法归因"))

    causes = []
    for item in payload.get("causes") or []:
        span = evidence_span(item)
        causes.append(
            {
                "cause_type": str(item.get("cause_type") or ""),
                "label": str(item.get("label") or ""),
                "entity_name": str(item.get("entity_name") or ""),
                "edge_id": item.get("edge_id"),
                "relation_type": str(item.get("relation_type") or ""),
                **span,
                "explanation": str(item.get("explanation") or ""),
                "provenance": provenance(item.get("edge_id"), span["text_span"]),
            }
        )

    return {"ok": True, "capability": "attribute", "mistake_id": payload.get("mistake_id"), "causes": causes}


__all__ = [
    "CAPABILITIES",
    "CAPABILITY_ORDER",
    "Capability",
    "CapabilityName",
    "RoutePlan",
    "describe_all",
    "evidence_span",
    "failure",
    "get",
    "normalize_ask",
    "normalize_attribution",
    "normalize_compare",
    "normalize_finding",
    "normalize_path",
    "normalize_resolve",
    "normalize_reverse",
    "normalize_review",
    "normalize_similar",
    "plan",
    "provenance",
    "relation_focus",
    "tool_schemas",
]
