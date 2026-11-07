"""反向检索：症 → 证 → 方 → 药。

方案 §0 的四类必答问题里，第 2 类是「这个症状该往哪个证、哪个方、哪几味药走」。
这一条最容易做错，因为它的形状太像「让模型想一想」：给一句口语描述，让模型
吐出一串证名方名药名，看起来又快又全。但那样做有三个后果，每一个都致命：

1. **不可溯源**：模型给出的证名，图里可能根本没有这条边，评审问「凭什么」答不上；
2. **不可复现**：同一个症状问两次得到两个答案，教学场景没法用；
3. **会编**：口语描述与教材词表之间的缺口永远补不完（「嗓子疼」vs「咽喉肿痛」），
   模型遇到缺口不会说「不知道」，它会就近编一个证。

所以这里拆成三段，每段都必须是可复核的机械动作：

- **症 → 证**：走 ``symptoms.candidates()`` 的倒排索引（6262 条教材症状短语 +
  口语别名表），每级都标明命中层级（name/alias/phrase/fuzzy）；
- **证 → 方**：走 ``证方链`` 边，教材明文写过「某证常用某方」才有这条边；
- **证 → 药 / 方 → 药**：走 ``药证链`` 与 ``组成`` 边。

三段都是查表，没有一步生成。**空结果就是空结果**：候选证候一条都没有时，
接口明确说「教材词表里对不上」，而不是给一批看着像的证名。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.kg import store
from app.kg import symptoms
from app.kg.store import KgEdge
from app.profile.store import DB_PATH

#: 一个证候下最多列几味药 / 几个方。教材写「主治」常列十几味，全列出来
#: 反而看不出重点；超过的部分用 ``truncated`` 标出来，不静默截断。
MAX_HERBS = 12
MAX_FORMULAS = 6
MAX_COMPOSITION = 12


def _evidence(edge: KgEdge) -> list[dict[str, Any]]:
    """边的教材依据，原样透传 ``page/quote/ref``。

    不做措辞加工：加工过的「出处」就不再是出处。
    """

    rows: list[dict[str, Any]] = []
    for item in edge.evidence or []:
        rows.append(
            {
                "page": item.get("page"),
                "quote": item.get("quote"),
                "ref": item.get("ref"),
            }
        )
    return rows


def _name_of(entity_id: str, db_path: Path) -> str:
    entity = store.get_entity(entity_id, db_path)
    return entity.name if entity else entity_id


def _hop(edge: KgEdge, *, peer_id: str, db_path: Path) -> dict[str, Any]:
    """一条边 → 一个可点开的跳点。

    ``edge_id`` 必填：前端点「依据」要能指回 ``kg_edge`` 的那一行，
    否则这条结论只是一句话，不是一条证据。
    """

    return {
        "id": peer_id,
        "name": _name_of(peer_id, db_path),
        "relation_type": edge.relation_type,
        "label": edge.label,
        "tier": edge.tier,
        "confidence": edge.confidence,
        "edge_id": edge.id,
        "evidence": _evidence(edge),
    }


def _composition(formula_id: str, db_path: Path) -> list[dict[str, Any]]:
    """方剂的组成药。只取 ``组成``，不取配伍——「方里有它」和「它俩常配」
    是两件事，混在一起会让「这个方治这个证」变成「这些药都沾边」。
    """

    edges = store.edges_of(formula_id, tiers=(1, 2), db_path=db_path)
    herbs = [
        _hop(edge, peer_id=edge.source_id, db_path=db_path)
        for edge in edges
        if edge.relation_type == "组成" and edge.target_id == formula_id
    ]
    herbs.sort(key=lambda item: (-item["confidence"], item["name"]))
    return herbs


def reverse_lookup(
    target: str,
    *,
    top_k: int = 3,
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    """症状 / 证候名 → 候选证 → 常用方 → 相关药。

    ``top_k`` 控候选证数量：给 3 是刻意的。给出 5 个以上，用户会以为
    「这些都是对的」，而实际只有第一两个有真实边支撑。
    """

    target = (target or "").strip()
    if not target:
        return {
            "ok": False,
            "query": target,
            "reason": "请给一个症状或证候名，比如「咽喉肿痛」「气虚欲脱」。",
            "syndromes": [],
        }

    candidates = symptoms.candidates(target, top_k=top_k, db_path=db_path)
    if not candidates:
        return {
            "ok": False,
            "query": target,
            # 这句话是在描述能力边界，不是在道歉：教材词表对不上就是没有，
            # 编一个近义词只会让下游的处方审查跟着错。
            "reason": f"教材词表里没有能对上「{target}」的症状或证候，图谱无法给出路径。",
            "syndromes": [],
            "hint": "试试教材里的说法：咽喉肿痛 / 恶寒发热 / 气虚欲脱 / 食少便溏。",
        }

    syndromes: list[dict[str, Any]] = []
    for candidate in candidates:
        entity = store.get_entity(candidate.syndrome_id, db_path)
        if entity is None:
            # 词表里有这个证名，但图里没有对应实体：这是「不在图谱覆盖范围」，
            # 与「没有这个证」是两件事，必须分开说。
            syndromes.append(
                {
                    **candidate.as_dict(),
                    "in_graph": False,
                    "note": "教材词表命中，但该证候未进图谱（无实体），因此没有可用路径。",
                    "herbs": [],
                    "formulas": [],
                }
            )
            continue

        edges = store.edges_of(candidate.syndrome_id, tiers=(1, 2), db_path=db_path)
        herb_edges = [
            edge for edge in edges if edge.relation_type == "药证链" and edge.target_id == candidate.syndrome_id
        ]
        formula_edges = [
            edge for edge in edges if edge.relation_type == "证方链" and edge.source_id == candidate.syndrome_id
        ]

        herbs = [_hop(edge, peer_id=edge.source_id, db_path=db_path) for edge in herb_edges]
        herbs.sort(key=lambda item: (-item["confidence"], item["name"]))

        formulas: list[dict[str, Any]] = []
        for edge in formula_edges:
            hop = _hop(edge, peer_id=edge.target_id, db_path=db_path)
            composition = _composition(hop["id"], db_path)
            hop["composition"] = composition[:MAX_COMPOSITION]
            hop["composition_total"] = len(composition)
            formulas.append(hop)
        formulas.sort(key=lambda item: (-item["confidence"], item["name"]))

        syndromes.append(
            {
                **candidate.as_dict(),
                "in_graph": True,
                "herbs": herbs[:MAX_HERBS],
                "herbs_total": len(herbs),
                "formulas": formulas[:MAX_FORMULAS],
                "formulas_total": len(formulas),
                # 有证无药、或有证无方都要显式标出来。图谱覆盖不全时，
                # 空数组和「本来就没有」长得一模一样。
                "complete": bool(herbs and formulas),
            }
        )

    usable = [item for item in syndromes if item["in_graph"]]
    return {
        "ok": bool(usable),
        "query": target,
        "reason": None
        if usable
        else "命中的证候都还没进图谱，无法给出「证→方→药」路径。",
        "syndromes": syndromes,
        # 一步一跳说明这四级是怎么接起来的，前端据此画链条。
        "chain": ["症", "证", "方", "药"],
        "tiers_used": [1, 2],
    }


__all__ = ["reverse_lookup"]
