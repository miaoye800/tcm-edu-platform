"""实体对齐：把知识树、药材 JSON、方剂/证候主数据装进 kg_entity。

Run from backend:

    python scripts/kg_align.py
    python scripts/kg_align.py --dry-run

只写 kg_entity / kg_alias，不动 knowledge_node 与 herb_relation。
可重复执行（upsert 幂等）。

**不追求 orphan = 0**：没有 monograph 的药材显式标 ``status='orphan'``，
前端显示「教材未收录」。编一个映射比留空更假。
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.kg import entities as kg_entities
from app.kg import store
from app.kg.schema import ensure_schema
from app.profile.store import DB_PATH


def align(db_path: Path, *, dry_run: bool = False, prune_derived: bool = False) -> dict[str, object]:
    ensure_schema(db_path)
    entities, aliases = kg_entities.build_registry(db_path)

    pruned = 0
    pruned_herbs = 0
    if not dry_run:
        # **必须先清残留药材，再 upsert。** 拼音/slug 校正会改药材 id
        # （茯苓 `herb_fuilng` → `herb_fuling`），旧行还留在 kg_entity 里，
        # 新行插进去就撞 `UNIQUE(kind, name)`，整个注册表写库失败。
        # 药材实体是 `data/herbs/` 的投影，不在注册表里的就是残留，删掉不会丢东西。
        pruned_herbs = prune_stale_herbs(entities, db_path)
        store.upsert_entities(entities, db_path)
        store.upsert_aliases(aliases, db_path)
        if prune_derived:
            pruned = prune_stale_syndromes(entities, db_path)

    by_kind = Counter(entity.kind for entity in entities)
    by_status = Counter(entity.status for entity in entities)
    orphans = [entity for entity in entities if entity.status == "orphan"]
    unlinked_tree = kg_entities.tree_points_without_entity(db_path) if not dry_run else []

    return {
        "entities": len(entities),
        "by_kind": dict(sorted(by_kind.items())),
        "by_status": dict(sorted(by_status.items())),
        "aliases": len(aliases),
        "orphans": len(orphans),
        "orphan_sample": sorted(entity.name for entity in orphans)[:12],
        "tree_points_without_entity": len(unlinked_tree),
        "tree_unlinked_sample": [item["name"] for item in unlinked_tree[:12]],
        "pruned_syndromes": pruned,
        "pruned_herbs": pruned_herbs,
        "dry_run": dry_run,
    }


def prune_stale_herbs(entities: list[object], db_path: Path) -> int:
    """删掉**注册表里已经没有**的药材实体。

    典型场景：拼音校正把 ``herb_fuilng``（茯苓，PDF 文本层丢了 l）改成
    ``herb_fuling``。旧 id 的行还在库里，新行插进去撞 ``UNIQUE(kind, name)``。
    这不是「多一条脏数据」的小事——它会让整个 ``kg_align`` 直接抛异常，
    图谱一条都建不出来。

    只删 ``kind='herb'``：药材实体是 ``data/herbs/`` 的投影，注册表里没有
    就一定是残留。证候/方剂有各自的推导源，删错会真丢东西，各走各的清理。
    """

    from contextlib import closing

    from app.profile.store import connect

    keep = {entity.id for entity in entities if getattr(entity, "kind", "") == "herb"}
    with closing(connect(db_path)) as conn:
        stale = [
            str(row[0])
            for row in conn.execute("SELECT id FROM kg_entity WHERE kind = 'herb'")
            if str(row[0]) not in keep
        ]
        for entity_id in stale:
            conn.execute(
                "DELETE FROM kg_edge WHERE source_id = ? OR target_id = ?",
                (entity_id, entity_id),
            )
            # staging 也有外键，而且它存的是「已复核的候选边」。只删指向这个
            # 已消失实体的那几条，其余留着——那是人工复核过的成果。
            conn.execute(
                "DELETE FROM kg_edge_staging WHERE source_id = ? OR target_id = ?",
                (entity_id, entity_id),
            )
            conn.execute("DELETE FROM kg_alias WHERE entity_id = ?", (entity_id,))
            conn.execute("DELETE FROM kg_entity WHERE id = ?", (entity_id,))
        conn.commit()
    return len(stale)


def prune_stale_syndromes(entities: list[object], db_path: Path) -> int:
    """删掉**推导源已不存在**的证候实体。

    证候实体不是主数据，是「药材主治里切出来的证名」的投影。抽取规则一改
    （比如把「三棱所主治的病证与莪术相同……」这类整段说明不再当证名），
    旧投影就留在库里成了幽灵节点——它挂在图上，点开没有药指向它，
    名字还是一句教材说明文。

    ``upsert_entities`` 只增不删，所以必须单独扫一遍。**只删 syndrome**：
    药材/方剂有主数据文件兜底，删了会真的丢东西。
    """

    from contextlib import closing

    from app.profile.store import connect

    keep = {entity.id for entity in entities if getattr(entity, "kind", "") == "syndrome"}
    with closing(connect(db_path)) as conn:
        stale = [
            str(row[0])
            for row in conn.execute("SELECT id FROM kg_entity WHERE kind = 'syndrome'")
            if str(row[0]) not in keep
        ]
        for entity_id in stale:
            # 先删指向它的边：kg_edge 有外键，直接删实体会被拒。
            # 这些边本来就悬空（另一端已经不存在），留着也是脏数据；
            # 紧接着的 build_kg 会按新的推导源重建全部边。
            conn.execute(
                "DELETE FROM kg_edge WHERE source_id = ? OR target_id = ?",
                (entity_id, entity_id),
            )
            conn.execute("DELETE FROM kg_alias WHERE entity_id = ?", (entity_id,))
            conn.execute("DELETE FROM kg_entity WHERE id = ?", (entity_id,))
        conn.commit()
    return len(stale)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KG 实体对齐")
    parser.add_argument("--db", default=None, help="SQLite 路径，默认用 QIHUANG_DB_PATH")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    parser.add_argument(
        "--prune-derived",
        action="store_true",
        help="删掉推导源已不存在的证候实体（抽取规则改过之后跑一次）",
    )
    args = parser.parse_args(argv)

    db_path = Path(args.db) if args.db else DB_PATH
    if not db_path.exists():
        print(f"数据库不存在：{db_path}", file=sys.stderr)
        return 2

    report = align(db_path, dry_run=args.dry_run, prune_derived=args.prune_derived)

    print(f"实体 {report['entities']}：{report['by_kind']}")
    print(f"状态 {report['by_status']}，别名 {report['aliases']} 条")
    print()
    print(f"无 monograph 的药材（orphan）{report['orphans']} 个：")
    print(f"  {'、'.join(report['orphan_sample'])} …")
    if not args.dry_run:
        if report["pruned_syndromes"]:
            print()
            print(f"删掉幽灵证候实体 {report['pruned_syndromes']} 个（推导源已不存在）")
        print()
        print(f"知识树里没有对应实体的 point 节点：{report['tree_points_without_entity']} 个")
        if report["tree_unlinked_sample"]:
            print(f"  {'、'.join(report['tree_unlinked_sample'])} …")
        print()
        stats = store.stats(db_path)
        print(f"库内：{stats['entities_by_kind']}，边 {stats['edges_total']}（{stats['edges_by_tier']}）")
    else:
        print()
        print("--dry-run：未写库")

    if report["dry_run"]:
        return 0
    if report["orphans"]:
        print()
        print("注意：orphan 不是错误。它们代表「被引用但教材未收录」，由 P4 教材抽取补齐。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
