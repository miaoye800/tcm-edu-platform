"""把 ``data/herbs/_pending/`` 里已签字的产物搬进 ``data/herbs/``（第 2 层隔离）。

## 为什么发布要单独一步

``promote_draft`` 只写待发布区，``publish_pending`` 才动 ``data/herbs/``。
分成两步的理由是：写待发布区是**幂等且无害**的（没人读那里），动
``data/herbs/`` 会立刻改变处方审查的行为。把「生成」和「生效」分开，
中间就有一个可以停下来看一眼的位置。

## 顺带解决一件旧账：77 味药的重复

早期 ``emit_herbs.py`` 给 86 味「孤儿药」生成过条目，其中 **77 味其实在教材里
有独立条目**（丁香、三棱、丹参……），当时按孤儿处理了。现在它们有了正规的
教材抽取版，两份同时留在 ``data/herbs/`` 就会产生**同一个 id 的两条记录**
（``herb_dingxiang`` 出现两次）——``load_herbs()`` 返回列表不去重，
下游按 id 建索引时会静默丢掉一条。

所以发布时把这 77 味从 ``textbook_extracted.json`` 里剔除：教材抽取版是
权威版，孤儿版只是当时的应急替代。

用法::

    python -m scripts.textbook.publish_pending --dry-run
    python -m scripts.textbook.publish_pending
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
HERBS_DIR = BACKEND / "data" / "herbs"
PENDING_JSON = HERBS_DIR / "_pending" / "textbook_promoted.json"
PUBLISHED_JSON = HERBS_DIR / "textbook_promoted.json"
EXTRACTED_JSON = HERBS_DIR / "textbook_extracted.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="发布待发布区产物")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if not PENDING_JSON.exists():
        print(f"没有待发布产物：{PENDING_JSON}")
        print("先跑 scripts.textbook.promote_draft")
        return 1

    fresh = json.loads(PENDING_JSON.read_text(encoding="utf-8"))
    fresh_names = {str(item["name"]) for item in fresh}

    old_count = 0
    if PUBLISHED_JSON.exists():
        old = json.loads(PUBLISHED_JSON.read_text(encoding="utf-8"))
        old_count = len(old)

    # 从孤儿产物里剔除已经被正规抽取版取代的条目
    dropped: list[str] = []
    remaining: list[dict] = []
    if EXTRACTED_JSON.exists():
        extracted = json.loads(EXTRACTED_JSON.read_text(encoding="utf-8"))
        for item in extracted:
            name = str(item.get("name") or "")
            if name in fresh_names:
                dropped.append(name)
            else:
                remaining.append(item)

    print(f"待发布 {len(fresh)} 条（当前已发布 {old_count} 条）")
    print(f"从孤儿产物剔除 {len(dropped)} 条，剩余 {len(remaining)} 条")
    if dropped:
        print("  " + "、".join(dropped[:30]) + ("…" if len(dropped) > 30 else ""))

    if args.dry_run:
        print("\n--dry-run，未写文件。")
        return 0

    PUBLISHED_JSON.write_text(
        json.dumps(fresh, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if EXTRACTED_JSON.exists():
        EXTRACTED_JSON.write_text(
            json.dumps(remaining, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(f"\n已发布 json={PUBLISHED_JSON}")

    # 发布完立刻验一遍 id 唯一性——这是唯一会让人静默丢数据的地方。
    ids: dict[str, str] = {}
    dupes: list[str] = []
    for path in sorted(HERBS_DIR.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        for item in raw if isinstance(raw, list) else [raw]:
            if not isinstance(item, dict):
                continue
            herb_id = str(item.get("id") or "")
            if herb_id in ids:
                dupes.append(f"{herb_id}（{ids[herb_id]} 与 {path.name}）")
            ids[herb_id] = path.name
    print(f"药材 {len(ids)} 条，重复 id {len(dupes)}")
    for line in dupes[:20]:
        print(f"   ! {line}")
    return 1 if dupes else 0


if __name__ == "__main__":
    raise SystemExit(main())
