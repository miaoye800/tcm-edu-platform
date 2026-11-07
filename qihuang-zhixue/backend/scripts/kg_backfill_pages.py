"""给已落库的 ``kg_edge`` 证据补教材页码（P2 遗留项）。

**问题**：P2 重建三层边时，凡证据来自 ``data/herbs/*.json`` 的边
（配伍 / 组成 / 药证链 / 同类鉴别，共 1214 条），``evidence.page`` 全是 ``None``，
``ref`` 只到「第二十四章·补气药·人参」这种**章级**出处。P2 的验收标准写的是
「每条边点开有教材页码」——章级出处做不到，读者翻不到原文那一页。

**做法**：ref 的最后一段就是产生这条证据的药材名；拿它去
``data/curated/textbook_entries.json``（442 条教材独立条目，每条带 page）查页码，
命中就补 ``page``，并把 ``ref`` 统一成
``《中药学》新世纪第四版（钟赣生主编） p412·第二十四章·补虚药·补气药·人参``。

**不猜**：查不到页码的边保持 ``None``。``source_type='derived'`` 的证方链本来就
没有教材页（它是从组成药的主治反推出来的），跳过——给它编一个页码等于伪造证据。

用法（在 backend 下）：

    python scripts/kg_backfill_pages.py --dry-run
    python scripts/kg_backfill_pages.py --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.kg.entities import textbook_page_ref
from app.profile.store import DB_PATH, connect


def _herb_name_of(ref: str) -> str:
    """从「《中药学》…·第二十四章·补气药·人参」里取出最后的药材名。

    书名内部也有「·」（钟赣生·新世纪第四版），所以只取最后一段——
    ref 的拼接顺序固定是「书名·章节·小节·药名」，药名永远在末尾。
    """

    parts = [part.strip() for part in (ref or "").split("·") if part.strip()]
    return parts[-1] if parts else ""


def _fill(evidence: list[dict], *, only_missing_page: bool = True) -> tuple[bool, str]:
    """给一条证据补页码。返回 (是否改动, 未命中的药名)。"""

    changed = False
    missing = ""
    for item in evidence:
        if not isinstance(item, dict):
            continue
        if item.get("page") not in (None, "", 0):
            if only_missing_page:
                continue
        if str(item.get("source_type") or "") == "derived":
            # 推导边没有教材页，编一个页码就是伪造证据
            continue
        name = _herb_name_of(str(item.get("ref") or ""))
        if not name:
            continue
        page, ref = textbook_page_ref(name)
        if page is None:
            missing = name
            continue
        item["page"] = page
        item["ref"] = ref
        changed = True
    return changed, missing


def main() -> int:
    parser = argparse.ArgumentParser(description="给 kg_edge 证据补教材页码")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    group.add_argument("--apply", action="store_true", help="真写库")
    parser.add_argument("--db", default=str(DB_PATH), help="数据库路径")
    args = parser.parse_args()

    db_path = Path(args.db)
    before: Counter[str] = Counter()
    after: Counter[str] = Counter()
    missing: Counter[str] = Counter()
    touched = 0

    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, relation_type, evidence_json FROM kg_edge"
        ).fetchall()
        updates: list[tuple[str, int]] = []
        for row in rows:
            edge_id, relation_type, raw = row[0], row[1], row[2]
            try:
                evidence = json.loads(raw or "[]")
            except json.JSONDecodeError:
                continue
            if not isinstance(evidence, list) or not evidence:
                continue
            first = evidence[0] if isinstance(evidence[0], dict) else {}
            key = str(relation_type)
            if first.get("page"):
                before["already_has_page"] += 1
                after["already_has_page"] += 1
                continue
            if str(first.get("source_type") or "") == "derived":
                before["derived_no_page"] += 1
                after["derived_no_page"] += 1
                continue
            before["missing_page"] += 1
            changed, missed = _fill(evidence)
            if missed:
                missing[missed] += 1
            if changed:
                after["filled"] += 1
                touched += 1
                updates.append((json.dumps(evidence, ensure_ascii=False), int(edge_id)))
            else:
                after["still_missing"] += 1
            del key

        print(f"库: {db_path}")
        print(f"边总数: {len(rows)}")
        print(f"  改前 已有页码 : {before['already_has_page']}")
        print(f"  改前 缺页码   : {before['missing_page']}")
        print(f"  改前 推导边(无教材页) : {before['derived_no_page']}")
        print(f"  → 可补页码     : {after['filled']}")
        print(f"  → 仍缺（查不到条目）: {after['still_missing']}")
        if missing:
            print(f"  未命中药材: {missing.most_common(10)}")

        if args.dry_run:
            print("\n--dry-run：未写库")
            return 0

        with conn:
            conn.executemany(
                "UPDATE kg_edge SET evidence_json=? WHERE id=?", updates
            )
        remaining = conn.execute(
            """
            SELECT COUNT(*) FROM kg_edge
            WHERE json_extract(evidence_json, '$[0].page') IS NULL
            """
        ).fetchone()[0]
        total = len(rows)
        print(f"\n已写入 {touched} 条")
        print(
            f"现在有页码的边: {total - remaining}/{total} "
            f"({(total - remaining) / total:.1%})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
