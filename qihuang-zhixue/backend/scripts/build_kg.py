"""构建知识图谱的 T1 硬约束与 T2 事实关系。

Run from backend:

    python scripts/build_kg.py
    python scripts/build_kg.py --dry-run
    python scripts/build_kg.py --keep      # 不先清空 kg_edge（增量场景）

**T3 相似边不在这里构建**——它是药材属性的纯函数，由
``app/kg/traverse.similar_neighbors()`` 在查询时算，不落库。旧的 915 条
「同归经/同功效/同性味」边就是因为落库才和属性不同步，占原图 58% 却回答不了
任何一个真实问题。

数据来源与可信度：

| tier | 关系 | 来源 | 可信度 |
|---|---|---|---|
| T1 | 十八反 / 十九畏 | data/curated/incompatibility.json（人工，教材 p128） | 最高，禁改 |
| T2 | 相须/相使/相畏/相杀/相恶 | data/curated/incompatibility.json（教材 p124-125 七情实例） | 教材明文 |
| T2 | 配伍 | data/herbs/*.json 的 combinations（教材未标七情类别，故不臆测） | 教材级，类别未知 |
| T2 | 组成 / 药证链 | data/herbs/*.json 的 formula_ref / indications | 结构化字段 |
| T2 | 证方链 | 由方剂组成药材的主治反推 | derived=1，置信度低 |
| T2 | 同类鉴别 | data/herbs/*.json 的 differentiation | 教材级 |
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.kb.ingest import load_formulas, load_herbs
from app.kg import entities as kg_entities
from app.kg import store
from app.kg.constants import Tier, is_symmetric
from app.kg.schema import ensure_schema
from app.kg.store import KgEdge
from app.profile.store import DB_PATH, connect

CURATED_PATH = BACKEND_ROOT / "data" / "curated" / "incompatibility.json"

#: combinations.purpose 里显式点名的七情类别。没点名的**不猜**。
_QIXING_KEYWORDS = ("相须", "相使", "相畏", "相杀", "相恶")

_BOOK = "《中药学》新世纪第四版（钟赣生主编）"


def _curated() -> dict[str, Any]:
    return json.loads(CURATED_PATH.read_text(encoding="utf-8"))


def _book_ref(page: int | None, section: str = "") -> str:
    if page:
        return f"{_BOOK} p{page}" + (f"·{section}" if section else "")
    return _BOOK + (f"·{section}" if section else "")


def _canonical(a_id: str, b_id: str, relation_type: str) -> tuple[str, str]:
    """对称关系统一成 source < target，避免 A→B 和 B→A 各存一条。"""

    if is_symmetric(relation_type) and a_id > b_id:
        return b_id, a_id
    return a_id, b_id


def _detect_qixing(purpose: str) -> str | None:
    for keyword in _QIXING_KEYWORDS:
        if keyword in purpose:
            return keyword
    return None


class _Resolver:
    """名字/id → 实体。解析不到就记账，绝不新建实体（避免和 P4 抽取撞车）。"""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.missing: Counter[str] = Counter()
        self._cache: dict[str, str | None] = {}

    def id_of(self, key: str) -> str | None:
        if key in self._cache:
            return self._cache[key]
        entity = kg_entities.resolve(key, self.db_path)
        resolved = entity.id if entity else None
        if resolved is None:
            self.missing[key] += 1
        self._cache[key] = resolved
        return resolved

    def entity_of(self, key: str) -> store.KgEntity | None:
        resolved = self.id_of(key)
        return store.get_entity(resolved, self.db_path) if resolved else None


# --------------------------------------------------------------------------- #
# T1 硬约束
# --------------------------------------------------------------------------- #


def build_t1(resolver: _Resolver) -> list[KgEdge]:
    curated = _curated()
    edges: list[KgEdge] = []

    for rule in curated.get("shibafan", []):
        for a_name in rule["a"]:
            for b_name in rule["b"]:
                edge = _t1_edge(
                    resolver, a_name, b_name, "十八反", rule, "反"
                )
                if edge:
                    edges.append(edge)

    for rule in curated.get("shijiuwei", []):
        for a_name in rule["a"]:
            for b_name in rule["b"]:
                edge = _t1_edge(
                    resolver, a_name, b_name, "十九畏", rule, "畏"
                )
                if edge:
                    edges.append(edge)

    return edges


def _t1_edge(
    resolver: _Resolver,
    a_name: str,
    b_name: str,
    relation_type: str,
    rule: dict[str, Any],
    verb: str,
) -> KgEdge | None:
    a_id = resolver.id_of(a_name)
    b_id = resolver.id_of(b_name)
    if not a_id or not b_id or a_id == b_id:
        return None
    src, dst = _canonical(a_id, b_id, relation_type)
    a_entity = store.get_entity(a_id, resolver.db_path)
    b_entity = store.get_entity(b_id, resolver.db_path)
    if not a_entity or not b_entity:
        return None

    return KgEdge(
        source_id=src,
        target_id=dst,
        relation_type=relation_type,
        tier=int(Tier.CONSTRAINT),
        label=f"{a_entity.name}{verb}{b_entity.name}",
        evidence=[
            {
                "page": rule.get("page"),
                "quote": rule.get("quote", ""),
                "ref": _book_ref(rule.get("page"), "第六章·配伍禁忌"),
                "rule_id": rule.get("rule_id", ""),
                "verse": rule.get("verse", ""),
                "source_type": "textbook",
            }
        ],
        confidence=1.0,
        status="approved",
        meta={
            "rule_type": relation_type,
            "group": rule.get("group_name", rule.get("rule_id", "")),
            "severity": rule.get("severity", "禁用"),
        },
    )


def pregnancy_rules() -> dict[str, Any]:
    """妊娠禁忌是**单味药谓词**，不是两药关系，所以不进边表，供处方审查直接查。"""

    curated = _curated()
    pregnancy = curated.get("pregnancy", {})
    result: dict[str, dict[str, Any]] = {}
    for key in ("forbidden", "caution"):
        block = pregnancy.get(key) or {}
        result[key] = {
            "severity": block.get("severity", "禁用" if key == "forbidden" else "慎用"),
            "herbs": list(block.get("herbs") or []),
            "page": block.get("page"),
            "quote": block.get("quote", ""),
            "ref": _book_ref(block.get("page"), "第六章·妊娠用药禁忌"),
        }
    return result


# --------------------------------------------------------------------------- #
# T2 事实关系
# --------------------------------------------------------------------------- #


def build_t2(resolver: _Resolver) -> list[KgEdge]:
    edges: list[KgEdge] = []
    edges.extend(_qixing_edges(resolver))
    edges.extend(_combination_edges(resolver))
    edges.extend(_composition_edges(resolver))
    edges.extend(_herb_syndrome_edges(resolver))
    edges.extend(_syndrome_formula_edges(resolver))
    edges.extend(_differentiation_edges(resolver))
    return edges


def _qixing_edges(resolver: _Resolver) -> list[KgEdge]:
    """教材 p124-125 明确记载的七情实例。这是唯一能确定七情类别的来源。"""

    edges: list[KgEdge] = []
    for item in _curated().get("qixing_examples", []):
        source = resolver.entity_of(item["source"])
        target = resolver.entity_of(item["target"])
        if not source or not target or source.id == target.id:
            continue
        relation_type = item["type"]
        src, dst = _canonical(source.id, target.id, relation_type)
        edges.append(
            KgEdge(
                source_id=src,
                target_id=dst,
                relation_type=relation_type,
                tier=int(Tier.FACT),
                label=f"{relation_type}·{target.name}",
                evidence=[
                    {
                        "page": item.get("page"),
                        "quote": item.get("quote", ""),
                        "ref": _book_ref(item.get("page"), "第五章·中药的配伍·七情"),
                        "source_type": "textbook",
                    }
                ],
                confidence=0.97,
                status="approved",
                meta={
                    "purpose": item.get("purpose", ""),
                    # 相使有主辅之分：source 是主药
                    "role": "主药→辅药" if relation_type == "相使" else "",
                },
            )
        )
    return edges


def _combination_edges(resolver: _Resolver) -> list[KgEdge]:
    """配伍关系。教材未标七情类别，所以只记「配伍」，不臆测相须/相使。"""

    edges: list[KgEdge] = []
    for herb in load_herbs():
        herb_ref = herb.sources[0].ref if herb.sources else ""
        for combo in herb.combinations:
            purpose = (combo.purpose or "").strip()
            for partner_key in combo.with_:
                partner = resolver.entity_of(partner_key)
                if partner is None or partner.id == herb.id:
                    continue
                relation_type = _detect_qixing(purpose) or "配伍"
                src, dst = _canonical(herb.id, partner.id, relation_type)
                # 已有教材明文的七情实例时，不要让 JSON 的弱证据覆盖它
                edges.append(
                    KgEdge(
                        source_id=src,
                        target_id=dst,
                        relation_type=relation_type,
                        tier=int(Tier.FACT),
                        label=f"{relation_type}·{partner.name}",
                        evidence=[
                            {
                                "page": None,
                                "quote": purpose or f"{herb.name}与{partner.name}配伍",
                                "ref": herb_ref or _BOOK,
                                "source_type": "kb_json",
                            }
                        ],
                        confidence=0.85,
                        status="approved",
                        meta={"purpose": purpose},
                    )
                )
    return edges


def _composition_edges(resolver: _Resolver) -> list[KgEdge]:
    edges: list[KgEdge] = []
    for herb in load_herbs():
        herb_ref = herb.sources[0].ref if herb.sources else ""
        for combo in herb.combinations:
            formula_key = (combo.formula_ref or "").strip()
            if not formula_key:
                continue
            formula = resolver.entity_of(formula_key)
            if formula is None:
                continue
            edges.append(
                KgEdge(
                    source_id=herb.id,
                    target_id=formula.id,
                    relation_type="组成",
                    tier=int(Tier.FACT),
                    label=f"方中组成·{formula.name}",
                    evidence=[
                        {
                            "page": None,
                            "quote": (combo.purpose or f"{formula.name}含{herb.name}"),
                            "ref": herb_ref or _BOOK,
                            "source_type": "kb_json",
                        }
                    ],
                    confidence=0.9,
                    status="approved",
                    meta={"formula_id": formula.id},
                )
            )
    return edges


def _herb_syndrome_edges(resolver: _Resolver) -> list[KgEdge]:
    """药证链：药材 → 它主治的证候。

    每条主治带 ``origin``，两种来源的置信度不同：

    - ``textbook``：教材【应用】的原文条目，可以直接当证据引用 → 0.92
    - ``manual``：人工归纳的通用证名（「脾气虚证」），粒度更粗，
      是**我们的归纳**而不是教材原话 → 0.78

    两套都建边，但边上标明凭的是哪一套。混着用才是「大乱炖」，
    标清楚就是两层不同粒度的关系。
    """

    edges: list[KgEdge] = []
    for herb in load_herbs():
        herb_ref = herb.sources[0].ref if herb.sources else ""
        for indication in herb.indications:
            syndrome_name = (indication.syndrome or "").strip()
            if not syndrome_name:
                continue
            syndrome = resolver.entity_of(kg_entities.syndrome_id(syndrome_name))
            if syndrome is None:
                continue
            from_textbook = indication.origin == "textbook"
            excerpt = f"{syndrome_name}：{indication.description}".strip("：")
            edges.append(
                KgEdge(
                    source_id=herb.id,
                    target_id=syndrome.id,
                    relation_type="药证链",
                    tier=int(Tier.FACT),
                    label=f"药证链·{syndrome_name}",
                    evidence=[
                        {
                            "page": None,
                            "quote": excerpt,
                            "ref": herb_ref or _BOOK,
                            "source_type": "textbook" if from_textbook else "kb_json",
                        }
                    ],
                    confidence=0.92 if from_textbook else 0.78,
                    status="approved",
                    meta={"origin": indication.origin},
                )
            )
    return edges


def _syndrome_formula_edges(resolver: _Resolver) -> list[KgEdge]:
    """证方链：由方剂组成药材的主治反推。

    **这是推导关系，不是教材明文**，所以 derived=1、置信度 0.6。每个方剂最多取
    关联最强的 3 个证候，避免一个方剂挂上十几个证。
    """

    # syndrome → formula → 有多少味组成药主治该证
    support: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for herb in load_herbs():
        for combo in herb.combinations:
            formula_key = (combo.formula_ref or "").strip()
            if not formula_key:
                continue
            for indication in herb.indications:
                syndrome_name = (indication.syndrome or "").strip()
                if syndrome_name:
                    support[syndrome_name][formula_key] += 1

    edges: list[KgEdge] = []
    per_formula: dict[str, int] = defaultdict(int)
    formulas = load_formulas()

    ranked = sorted(
        ((syn, fid, count) for syn, by_formula in support.items() for fid, count in by_formula.items()),
        key=lambda item: (-item[2], item[0], item[1]),
    )
    for syndrome_name, formula_key, count in ranked:
        if per_formula[formula_key] >= 3:
            continue
        syndrome = resolver.entity_of(kg_entities.syndrome_id(syndrome_name))
        formula = resolver.entity_of(formula_key)
        if syndrome is None or formula is None:
            continue
        per_formula[formula_key] += 1
        edges.append(
            KgEdge(
                source_id=syndrome.id,
                target_id=formula.id,
                relation_type="证方链",
                tier=int(Tier.FACT),
                label=f"证方链·{formula.name}",
                evidence=[
                    {
                        "page": None,
                        "quote": f"{count} 味组成药主治「{syndrome_name}」",
                        "ref": "由 data/herbs/*.json 的 combinations 与 indications 交叉推导",
                        "source_type": "derived",
                    }
                ],
                confidence=round(min(0.55 + 0.1 * count, 0.8), 3),
                derived=1,
                status="approved",
                meta={"supporting_herbs": count, "formula_composition_size": len(formulas.get(formula_key, {}).get("composition") or [])},
            )
        )
    return edges


def _differentiation_edges(resolver: _Resolver) -> list[KgEdge]:
    edges: list[KgEdge] = []
    for herb in load_herbs():
        herb_ref = herb.sources[0].ref if herb.sources else ""
        for item in herb.differentiation:
            other = resolver.entity_of(item.compare_with)
            if other is None or other.id == herb.id:
                continue
            src, dst = _canonical(herb.id, other.id, "同类鉴别")
            edges.append(
                KgEdge(
                    source_id=src,
                    target_id=dst,
                    relation_type="同类鉴别",
                    tier=int(Tier.FACT),
                    label=f"同类鉴别·{other.name}",
                    evidence=[
                        {
                            "page": None,
                            "quote": item.key_points,
                            "ref": herb_ref or _BOOK,
                            "source_type": "kb_json",
                        }
                    ],
                    confidence=0.93,
                    status="approved",
                )
            )
    return edges


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #


def _clear_edges(db_path: Path) -> None:
    with closing(connect(db_path)) as conn:
        conn.execute("DELETE FROM kg_edge")
        conn.commit()


def _fill_evidence_pages(edges: list[KgEdge]) -> int:
    """给来自 ``data/herbs/*.json`` 的证据补上教材页码，返回补上的边数。

    这些边的 ref 只能拼到章级（「第二十四章·补气药·人参」），P2 的验收标准却要求
    「每条边点开有教材页码」。教材条目表里有 page，ref 的末段又是药材名，两下一拼
    就有页码。查不到就留 ``None``——**编一个页码比没有页码更糟**。
    ``derived`` 边（证方链）本来就没有教材页，跳过。
    """

    filled = 0
    for edge in edges:
        changed = False
        for item in edge.evidence:
            if not isinstance(item, dict) or item.get("page"):
                continue
            if str(item.get("source_type") or "") == "derived":
                continue
            parts = [part.strip() for part in str(item.get("ref") or "").split("·") if part.strip()]
            if not parts:
                continue
            page, ref = kg_entities.textbook_page_ref(parts[-1])
            if page is None:
                continue
            item["page"] = page
            item["ref"] = ref
            changed = True
        if changed:
            filled += 1
    return filled


def build(db_path: Path = DB_PATH, *, dry_run: bool = False, keep: bool = False) -> dict[str, Any]:
    ensure_schema(db_path)

    # 实体先对齐，否则所有边都解析不到两端
    from scripts.kg_align import align

    align(db_path)

    resolver = _Resolver(db_path)
    t1 = build_t1(resolver)
    t2 = build_t2(resolver)
    edges = t1 + t2
    pages_filled = _fill_evidence_pages(edges)

    if not dry_run:
        if not keep:
            _clear_edges(db_path)
        store.upsert_edges(edges, db_path)

    by_type = Counter(edge.relation_type for edge in edges)
    by_tier = Counter(int(edge.tier) for edge in edges)
    stats = store.stats(db_path)

    return {
        "edges_built": len(edges),
        "pages_filled": pages_filled,
        "by_type": dict(sorted(by_type.items(), key=lambda kv: (-kv[1], kv[0]))),
        "by_tier": {f"T{t}": by_tier.get(t, 0) for t in (1, 2, 3)},
        "unresolved": dict(resolver.missing.most_common(20)),
        "unresolved_count": len(resolver.missing),
        "derived_edges": sum(1 for edge in edges if edge.derived),
        "db": stats,
        "dry_run": dry_run,
        "kept_existing": keep,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建 KG 的 T1/T2 关系")
    parser.add_argument("--db", default=None, help="SQLite 路径，默认用 QIHUANG_DB_PATH")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    parser.add_argument("--keep", action="store_true", help="不先清空 kg_edge")
    args = parser.parse_args(argv)

    db_path = Path(args.db) if args.db else DB_PATH
    if not db_path.exists():
        print(f"数据库不存在：{db_path}", file=sys.stderr)
        return 2

    report = build(db_path, dry_run=args.dry_run, keep=args.keep)

    print(f"构建边 {report['edges_built']} 条，分层 {report['by_tier']}")
    print(f"其中推导边（derived）{report['derived_edges']} 条")
    # 证据页码是 P2 验收项（「每条边点开有教材页码」）。来自 herbs/*.json 的边
    # 原本只有章级 ref，这里补上教材页码；补不到就留空，不猜。
    print(f"证据补上教材页码：{report['pages_filled']} 条")
    print()
    print("按关系类型：")
    for relation_type, count in report["by_type"].items():
        tier = int(store_tier(relation_type))
        print(f"  T{tier}  {relation_type:<8} {count:>5}")

    if report["unresolved"]:
        print()
        print(f"解析不到的两端（{report['unresolved_count']} 个，相关边已跳过）：")
        for key, count in report["unresolved"].items():
            print(f"  {key}  ×{count}")
        print("  → 补 data/curated/herb_id_names.json 或 data/curated/incompatibility.json 的 name_aliases")

    if not args.dry_run:
        print()
        print(f"库内：边 {report['db']['edges_total']}（{report['db']['edges_by_tier']}），T3 落库 {report['db']['tier3_persisted']} 条")
        if report["db"]["tier3_persisted"]:
            print("  ✗ T3 不该落库，检查是否有代码把相似边写进了 kg_edge")
    else:
        print()
        print("--dry-run：未写库")
    return 0


def store_tier(relation_type: str) -> Tier:
    from app.kg.constants import tier_of

    return tier_of(relation_type)


if __name__ == "__main__":
    raise SystemExit(main())
