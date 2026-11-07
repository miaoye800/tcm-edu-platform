"""把图谱返回裁到模型能吃的体积。

``app/kg/`` 的返回值是给**人和前端**设计的：节点、边、来源表、派生标记、
置信度，一个不少。直接塞给模型有两个后果——撑上下文，以及让它把注意力
花在 ``confidence=0.709`` 这种它根本不该做判断的字段上。

所以这里再出一条投影：只留模型真正需要的东西，单次约 1200 token。

**``review`` 不在这里。** 处方审查结论不投模型，只走确定性模板直出。
十八反是人工 curated 的 T1 硬约束，经模型复述会从「禁用」降级成「建议谨慎
使用」——这是医疗安全红线，不是效率取舍。要模型解释可以，但不能让模型
转述结论。

**最重要的一条不变量**：保留的关系必须带 ``text_span``。预算不够时整条关系
删掉，绝不留一条「A 和 B 有关系但没依据」的空壳事实——那正好是这套系统
存在的意义所在。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Mapping

from app.kg.contract import evidence_span, relation_focus
from app.kg.constants import Tier

#: 默认预算。约 1600 汉字；真实模型对中文约 0.6 token/字，所以这里偏保守。
DEFAULT_BUDGET = 1200

#: JSON 字符数的硬上限，双保险。token 估算是近似值，字符数不是。
DEFAULT_MAX_CHARS = 2200


class ProjectionOverflow(Exception):
    """删到只剩骨架仍超预算。调用方应降级为「一句话 + 一条引用」。"""


@dataclass(frozen=True)
class Projection:
    payload: dict[str, Any]
    tokens: int
    chars: int
    dropped: tuple[str, ...] = field(default_factory=tuple)

    @property
    def within_budget(self) -> bool:
        return True


# --------------------------------------------------------------------------- #
# token 估算：不引 tiktoken，标准库足够做预算控制
# --------------------------------------------------------------------------- #


def estimate_tokens(text: str) -> int:
    """近似 token 数。

    中文按 0.75 token/字（实际约 0.6，故意高估 15%）、ASCII 字母数字按 0.28、
    其余（标点、括号）按 0.5。宁可提前删内容，也不要真到模型侧被截断——
    被截断的那一半通常是末尾的溯源信息。
    """

    cjk = 0
    alnum = 0
    for char in text:
        if "\u4e00" <= char <= "\u9fff":
            cjk += 1
        elif char.isascii() and char.isalnum():
            alnum += 1
    other = len(text) - cjk - alnum
    return math.ceil(cjk * 0.75 + alnum * 0.28 + other * 0.5)


def _dump(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def payload_tokens(payload: Mapping[str, Any]) -> int:
    return estimate_tokens(_dump(payload))


def _entity_name(value: Any) -> str:
    """实体指代 → 名字。``{"name": "麻黄"}`` 与 ``"麻黄"`` 都要能吃。"""

    if isinstance(value, Mapping):
        return str(value.get("name") or "")
    return str(value or "")


def _clip(text: str, cap: int) -> str:
    """按句读截断，尽量不切断半句话。"""

    value = (text or "").strip()
    if len(value) <= cap:
        return value
    for sep in ("。", "；", "，", ";", ",", " "):
        index = value.rfind(sep, 0, cap)
        if index >= cap * 0.5:
            return value[: index + 1]
    return value[:cap]


# --------------------------------------------------------------------------- #
# 关系投影
# --------------------------------------------------------------------------- #


def _relation_out(item: Mapping[str, Any]) -> dict[str, Any] | None:
    """一条边 → 模型投影。**没有原文的关系直接返回 None，不降级保留。**"""

    evidence = item.get("evidence") or []
    span = evidence_span(evidence[0]) if evidence else evidence_span(None)
    if not span["text_span"]:
        return None

    edge_id = item.get("edge_id")
    if edge_id is None and item.get("edge_ids"):
        edge_id = item["edge_ids"][0]

    return {
        "edge_id": edge_id,
        "relation_type": str(item.get("relation_type") or ""),
        "tier": int(item.get("tier") or Tier.FACT),
        "from": str((item.get("source") or {}).get("name") or ""),
        "to": str((item.get("target") or {}).get("name") or ""),
        "label": str(item.get("label") or ""),
        "text_span": span["text_span"],
        "page": span["page"],
        "ref": span["ref"],
    }


#: 只沾着一端种子的边最多留几条。用户问的是「这两味药为什么配」，不是
#: 「藜芦一共反多少味药」——邻域无边无际，全留就把答案变成禁忌表翻页。
MAX_NEIGHBOR_RELATIONS = 2

#: 同一层内的先后。**这不是审美，是答题顺序**：
#:
#: 用户问「为什么常一起用」，最想看到的是相须/配伍那条边；「同类鉴别」才是
#: 别人（compare）的答案。以前这里按关系名的码点排序，于是「同」排在「相」
#: 前面，ask 的正文第一行永远是鉴别句——读者第一眼就看到「答非所问」。
#:
#: 长的词必须排在短的词前面（「配伍禁忌」先于「配伍」），否则子串匹配会
#: 把禁忌当成普通配伍。
_RELATION_PRIORITY: tuple[str, ...] = (
    "十八反",
    "十九畏",
    "配伍禁忌",
    "妊娠禁忌",
    "相须",
    "相使",
    "相恶",
    "相畏",
    "相杀",
    "配伍",
    "组成",
    "药证链",
    "主治",
    "功效",
    "同类鉴别",
    "鉴别对照",
)


def _relation_rank(relation_type: str, focus: tuple[str, ...] = ()) -> int:
    """关系类型的答题优先级；不认识的排最后，不让它抢版面。

    ``focus`` 是问句焦点（见 ``contract.relation_focus``）：命中的先进整段，
    其余按默认表排在它后面。
    """

    for index, word in enumerate(focus):
        if word in relation_type:
            return index
    for index, word in enumerate(_RELATION_PRIORITY):
        if word in relation_type:
            return len(focus) + index
    return len(focus) + len(_RELATION_PRIORITY)


def _collect_relations(ask_result: Any) -> list[dict[str, Any]]:
    """把 context 里的 constraints + relations 合并、去重、投影、排序。

    排序有四层，顺序不能换：

    1. **两端都是种子**的边优先。问「人参和藜芦」，那一条十八反就是答案；
       「藜芦反西洋参」只是同一段原文的副作用，排前面会让用户以为在答别的。
    2. T1 在前——裁的时候先丢最弱的，不能先丢禁忌。
    3. 关系类型按「禁忌 > 配伍 > 组成 > 药证 > 鉴别」的答题顺序（见
       ``_RELATION_PRIORITY``）。这一层以前是关系名的码点序，结果 ask 的
       正文第一行永远是「同类鉴别」——那是 compare 的答案。
    4. 关系名稳定排序，保证同样输入每次输出一致（可 diff、可回归）。
    """

    context = getattr(ask_result, "context", {}) or {}
    seed_names = {
        str(getattr(seed, "name", "") or "") for seed in getattr(ask_result, "seeds", []) or []
    }
    seed_names.discard("")
    # 排序看问句，不只看边：同一份邻域里「能治哪些证」和「和谁常配」要的是
    # 两类不同的边，只按关系名排会把另一半答成跑题。
    focus = relation_focus(str(getattr(ask_result, "question", "") or ""))
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any] | None] = []
    for item in list(context.get("constraints") or []) + list(context.get("relations") or []):
        source = str((item.get("source") or {}).get("id") or "")
        target = str((item.get("target") or {}).get("id") or "")
        key = (source, target, str(item.get("relation_type") or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(_relation_out(item))

    relations = [item for item in out if item is not None]

    def _rank(item: dict[str, Any]) -> tuple[int, int, int, str]:
        # 两端都是种子 → 0；只沾一端 → 1。认不出种子名时（比如上层只给了 id）
        # 一律按 0 处理，不做这一层排序——宁可少优化，也不要排错。
        if len(seed_names) < 2:
            return (
                0,
                int(item["tier"]),
                _relation_rank(item["relation_type"], focus),
                item["relation_type"],
            )
        both = {str(item.get("from") or ""), str(item.get("to") or "")} & seed_names
        return (
            0 if len(both) == 2 else 1,
            int(item["tier"]),
            _relation_rank(item["relation_type"], focus),
            item["relation_type"],
        )

    relations.sort(key=_rank)

    if len(seed_names) >= 2:
        paired = [item for item in relations if _rank(item)[0] == 0]
        others = [item for item in relations if _rank(item)[0] == 1]
        # 只有「两端都是种子」的边确实存在时才裁邻域：某些问题图里根本没有
        # 这对药之间的关系，那时候邻域是唯一能说的东西，裁掉就等于不答。
        if paired:
            relations = paired + others[:MAX_NEIGHBOR_RELATIONS]

    return relations


def _seed_herbs(ask_result: Any, *, limit: int = 2) -> list[dict[str, Any]]:
    context = getattr(ask_result, "context", {}) or {}
    seed_ids = set(context.get("seed_ids") or [])
    herbs: list[dict[str, Any]] = []
    for item in context.get("herbs") or []:
        if seed_ids and item.get("id") not in seed_ids:
            continue
        if not item.get("has_monograph"):
            continue
        herbs.append(
            {
                "name": str(item.get("name") or ""),
                "nature": str(item.get("nature") or ""),
                "flavor": list(item.get("flavor") or []),
                "meridians": list(item.get("meridians") or []),
                "effects": list(item.get("effects") or [])[:6],
                "dosage": str((item.get("dosage") or {}).get("text") or "")
                if isinstance(item.get("dosage"), dict)
                else str(item.get("dosage") or ""),
            }
        )
        if len(herbs) >= limit:
            break
    return herbs


def _seed_out(ask_result: Any, *, limit: int = 3) -> list[dict[str, str]]:
    seeds = getattr(ask_result, "seeds", []) or []
    return [{"name": seed.name, "kind": seed.kind} for seed in seeds[:limit]]


#: ``ask`` 的降级档位。每档重算 token，够了就停。
_ASK_STEPS: tuple[dict[str, Any], ...] = (
    {"relations": 10, "span": 120, "answer": 200, "herbs": True, "trend": False},
    {"relations": 8, "span": 120, "answer": 200, "herbs": True, "trend": False},
    {"relations": 6, "span": 120, "answer": 160, "herbs": True, "trend": True},
    {"relations": 6, "span": 80, "answer": 120, "herbs": False, "trend": True},
    {"relations": 4, "span": 80, "answer": 120, "herbs": False, "trend": True},
    {"relations": 2, "span": 80, "answer": 100, "herbs": False, "trend": True},
    {"relations": 1, "span": 60, "answer": 80, "herbs": False, "trend": True},
)


def project_ask(ask_result: Any, *, budget: int = DEFAULT_BUDGET) -> Projection:
    """``ask`` → 模型投影。

    ``seeds`` 认不出时给一个空 relations 的合法形状，不抛异常——「教材没录」
    和「调用失败」在模型侧必须长得不一样。
    """

    relations = _collect_relations(ask_result)
    answer = str(getattr(ask_result, "answer", "") or "")
    seeds = _seed_out(ask_result)

    for step in _ASK_STEPS:
        kept = [dict(item) for item in relations[: step["relations"]]]
        for item in kept:
            item["text_span"] = _clip(item["text_span"], step["span"])

        payload: dict[str, Any] = {
            "capability": "ask",
            "answer": _clip(answer, step["answer"]),
            "seeds": seeds,
            "relations": kept,
        }
        if step["herbs"]:
            payload["herbs"] = _seed_herbs(ask_result)

        dropped: list[str] = []
        if len(kept) < len(relations):
            dropped.append(f"relations:{len(relations)}->{len(kept)}")
        if not step["herbs"]:
            dropped.append("herbs")
        if step["trend"]:
            dropped.append("citation_table")  # relations 已自带 ref/page，等同引用

        payload["truncated"] = bool(dropped)
        tokens = payload_tokens(payload)
        if tokens <= budget and len(_dump(payload)) <= DEFAULT_MAX_CHARS:
            return Projection(payload, tokens, len(_dump(payload)), tuple(dropped))

    raise ProjectionOverflow("ask 投影删到只剩 1 条关系仍超预算")


def _span_of(item: Mapping[str, Any]) -> dict[str, Any]:
    """一条依据的出处，三种形状都吃。

    同一份数据在上游有三种写法：``find_path`` 给 ``evidence`` 列表，
    ``compare``/``ask`` 直接把 ``quote`` 放在字典上，``contract.normalize_*``
    则统一压成 ``text_span``。投影层原本只认前两种，于是「先归一化再投影」这条
    路径会**静默丢光所有依据**——不报错、不告警，只是答案里少了一段原文和一条
    十八反。宁可在这里多写十行，也不要让这种丢失变成一个没人发现的空答案。
    """

    if item.get("text_span"):
        return {
            "text_span": str(item.get("text_span") or ""),
            "page": item.get("page"),
            "ref": str(item.get("ref") or ""),
        }
    if item.get("quote"):
        return evidence_span(item)
    evidence = item.get("evidence") or []
    return evidence_span(evidence[0]) if evidence else evidence_span(None)


def project_path(payload: Mapping[str, Any], *, budget: int = DEFAULT_BUDGET) -> Projection:
    steps: list[dict[str, Any]] = []
    for step in payload.get("path") or []:
        span = _span_of(step)
        if not span["text_span"]:
            continue
        steps.append(
            {
                "edge_id": step.get("edge_id"),
                "tier": int(step.get("tier") or Tier.FACT),
                "relation_type": str(step.get("relation_type") or ""),
                "from": _entity_name(step.get("from")),
                "to": _entity_name(step.get("to")),
                "text_span": _clip(span["text_span"], 100),
                "page": span["page"],
                "ref": span["ref"],
            }
        )

    out = {
        "capability": "path",
        "found": bool(payload.get("found")),
        "hops": int(payload.get("hops") or len(steps)),
        # 上游两个来源给的东西不一样：``find_path`` 给的是实体字典，
        # ``contract.normalize_path`` 已经压成了名字字符串。投影层不该要求
        # 调用方先对齐形状——这里两种都认，否则接主链路时会在这一行炸掉。
        "from": _entity_name(payload.get("from")),
        "to": _entity_name(payload.get("to")),
        "steps": steps,
        "sentence": _clip(str(payload.get("sentence") or payload.get("reason") or ""), 160),
    }
    tokens = payload_tokens(out)
    if tokens > budget:
        out["steps"] = steps[:2]
        out["sentence"] = _clip(out["sentence"], 80)
        tokens = payload_tokens(out)
    if tokens > budget:
        raise ProjectionOverflow("path 投影超预算")
    return Projection(out, tokens, len(_dump(out)), ("steps" if len(out["steps"]) < len(steps) else "",))


def _hop_brief(hop: Mapping[str, Any]) -> dict[str, Any]:
    evidence = hop.get("evidence") or []
    span = evidence_span(evidence[0]) if evidence else evidence_span(None)
    return {
        "name": str(hop.get("name") or ""),
        "relation_type": str(hop.get("relation_type") or ""),
        "edge_id": hop.get("edge_id"),
        "text_span": _clip(span["text_span"], 80),
        "page": span["page"],
        "ref": span["ref"],
    }


def project_reverse(payload: Mapping[str, Any], *, budget: int = DEFAULT_BUDGET) -> Projection:
    """症 → 证 → 方 → 药。证最多 3 个，每证药/方各最多 3 条。"""

    syndromes: list[dict[str, Any]] = []
    for item in (payload.get("syndromes") or [])[:3]:
        if not item.get("in_graph"):
            syndromes.append(
                {
                    "name": str(item.get("name") or ""),
                    "score": float(item.get("score") or 0.0),
                    "in_graph": False,
                    "herbs": [],
                    "formulas": [],
                }
            )
            continue
        syndromes.append(
            {
                "name": str(item.get("name") or ""),
                "score": float(item.get("score") or 0.0),
                "in_graph": True,
                "herbs": [_hop_brief(hop) for hop in (item.get("herbs") or [])[:3]],
                "formulas": [_hop_brief(hop) for hop in (item.get("formulas") or [])[:3]],
            }
        )

    out = {
        "capability": "reverse",
        "ok": bool(payload.get("ok")),
        "query": str(payload.get("query") or ""),
        "syndromes": syndromes,
        "reason": str(payload.get("reason") or ""),
    }
    tokens = payload_tokens(out)
    if tokens > budget:
        for item in out["syndromes"]:
            item["herbs"] = item["herbs"][:2]
            item["formulas"] = item["formulas"][:2]
        tokens = payload_tokens(out)
    if tokens > budget:
        out["syndromes"] = out["syndromes"][:2]
        tokens = payload_tokens(out)
    if tokens > budget:
        raise ProjectionOverflow("reverse 投影超预算")
    return Projection(out, tokens, len(_dump(out)), ())


def project_compare(payload: Mapping[str, Any], *, budget: int = DEFAULT_BUDGET) -> Projection:
    """两药对比。差异项按「四气/功效/主治/用量」优先，每侧最多 6 个值。"""

    priority = ("四气", "五味", "归经", "功效", "主治", "用量", "毒性")
    differences: list[dict[str, Any]] = []
    for item in sorted(
        payload.get("differences") or [],
        key=lambda row: priority.index(row.get("label"))
        if row.get("label") in priority
        else len(priority),
    ):
        differences.append(
            {
                "label": str(item.get("label") or ""),
                "left": list(item.get("left") or [])[:6],
                "right": list(item.get("right") or [])[:6],
            }
        )

    textbook = payload.get("textbook_differentiation")
    textbook_out = None
    if textbook:
        span = _span_of(textbook)
        if span["text_span"]:
            textbook_out = {
                "text_span": _clip(span["text_span"], 160),
                "page": span["page"],
                "ref": span["ref"],
                "edge_id": textbook.get("edge_id"),
            }

    constraints = []
    for item in payload.get("constraints") or []:
        span = _span_of(item)
        if not span["text_span"]:
            continue
        constraints.append(
            {
                "relation_type": str(item.get("relation_type") or ""),
                "text_span": _clip(span["text_span"], 100),
                "page": span["page"],
                "ref": span["ref"],
                "edge_id": item.get("edge_id"),
            }
        )

    out = {
        "capability": "compare",
        "ok": bool(payload.get("ok")),
        "left": _side(payload.get("left")),
        "right": _side(payload.get("right")),
        "differences": differences,
        "textbook_differentiation": textbook_out,
        "constraints": constraints[:3],
        "summary": _clip(str(payload.get("summary") or ""), 200),
    }

    tokens = payload_tokens(out)
    while tokens > budget and len(out["differences"]) > 4:
        out["differences"] = out["differences"][:-1]
        tokens = payload_tokens(out)
    if tokens > budget:
        out["constraints"] = out["constraints"][:1]
        out["summary"] = _clip(out["summary"], 100)
        tokens = payload_tokens(out)
    if tokens > budget:
        raise ProjectionOverflow("compare 投影超预算")
    return Projection(out, tokens, len(_dump(out)), ())


def _side(brief: Mapping[str, Any] | None) -> dict[str, Any]:
    item = brief or {}
    return {
        "name": str(item.get("name") or ""),
        "nature": str(item.get("nature") or ""),
        "effects": list(item.get("effects") or [])[:6],
    }


__all__ = [
    "DEFAULT_BUDGET",
    "DEFAULT_MAX_CHARS",
    "Projection",
    "ProjectionOverflow",
    "estimate_tokens",
    "payload_tokens",
    "project_ask",
    "project_compare",
    "project_path",
    "project_reverse",
]
