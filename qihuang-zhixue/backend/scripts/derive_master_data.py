"""从 76 味药的结构化 JSON 反推方剂与证候主数据。

Run from backend:

    python scripts/derive_master_data.py            # 写 data/formulas 与 data/syndromes
    python scripts/derive_master_data.py --dry-run  # 只打印统计

**诚实边界**：这里能确定的只有「哪些药出现在哪个方里」（来自 ``combinations``）
和「哪些药主治哪个证」（来自 ``indications``）。方剂的功效、君臣佐使、剂量配比
**不能从这些字段猜**——猜出来就是新的假数据。所以产出里统一标
``incomplete: true`` / ``verified: false``，等 P4 教材抽取补齐。

证候同理：只做**精确去重**，不做同义合并（「气虚」和「气虚欲脱」是两个证，
自动合并会造出假数据）。近似候选单独输出到 ``syndrome_merge_candidates.json``
供人工裁决。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.kb.ingest import load_herbs

DATA_DIR = BACKEND_ROOT / "data"
FORMULA_INDEX = DATA_DIR / "formulas" / "_index.json"
SYNDROME_INDEX = DATA_DIR / "syndromes" / "_index.json"
MERGE_CANDIDATES = DATA_DIR / "syndromes" / "syndrome_merge_candidates.json"


def _formula_names() -> dict[str, str]:
    """方剂 id → 中文名。P2 之后代码常量会被删掉，这里同时兼容两种来源。"""

    index_path = FORMULA_INDEX
    if index_path.exists():
        payload = json.loads(index_path.read_text(encoding="utf-8"))
        existing = payload.get("formulas") or {}
        if existing:
            return {fid: str(item.get("name") or fid) for fid, item in existing.items()}

    from app.agents.graph_intelligence import FORMULA_NAMES

    return dict(FORMULA_NAMES)


def derive_formulas() -> dict[str, Any]:
    names = _formula_names()
    # herb_id → 该方里这味药的作用。同一味药可能对同一方写了多条 purpose，合并而不是重复列出。
    composition: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    referenced_syndromes: dict[str, set[str]] = defaultdict(set)

    for herb in load_herbs():
        for combo in herb.combinations:
            ref = str(combo.formula_ref or "").strip()
            if not ref:
                continue
            names.setdefault(ref, ref.removeprefix("formula_"))
            slot = composition[ref].setdefault(
                herb.id, {"herb_id": herb.id, "herb_name": herb.name, "purposes": []}
            )
            purpose = combo.purpose.strip()
            if purpose and purpose not in slot["purposes"]:
                slot["purposes"].append(purpose)
            for indication in herb.indications:
                syndrome = str(indication.syndrome or "").strip()
                if syndrome:
                    referenced_syndromes[ref].add(syndrome)

    formulas: dict[str, Any] = {}
    for fid, name in sorted(names.items(), key=lambda kv: kv[1]):
        herbs = sorted(composition.get(fid, {}).values(), key=lambda item: item["herb_id"])
        formulas[fid] = {
            "id": fid,
            "name": name,
            "pinyin": "",
            "category": "",
            "composition": herbs,
            "effects": [],
            "indications": sorted(referenced_syndromes.get(fid, set())),
            # 只有「组成」是确定信息；功效/君臣佐使/剂量比必须等教材抽取。
            "incomplete": True,
            "verified": False,
            "sources": [],
        }
    return {
        "_note": "由 data/herbs/*.json 的 combinations.formula_ref 反推。composition 是确定信息；effects / 君臣佐使 / 剂量配比尚未补齐，incomplete=true。",
        "_generated_by": "scripts/derive_master_data.py",
        "formulas": formulas,
    }


def _similar(left: str, right: str) -> float:
    """2-gram Dice 系数，只用来产出候选，不用来自动合并。"""

    def grams(text: str) -> set[str]:
        return {text[i : i + 2] for i in range(len(text) - 1)} or {text}

    a, b = grams(left), grams(right)
    if not a or not b:
        return 0.0
    return 2 * len(a & b) / (len(a) + len(b))


def derive_syndromes() -> tuple[dict[str, Any], dict[str, Any]]:
    related_herbs: dict[str, set[str]] = defaultdict(set)
    related_formulas: dict[str, set[str]] = defaultdict(set)
    descriptions: dict[str, list[str]] = defaultdict(list)

    for herb in load_herbs():
        for indication in herb.indications:
            syndrome = str(indication.syndrome or "").strip()
            if not syndrome:
                continue
            related_herbs[syndrome].add(herb.name)
            if indication.description:
                descriptions[syndrome].append(indication.description)
        for combo in herb.combinations:
            ref = str(combo.formula_ref or "").strip()
            if not ref:
                continue
            for indication in herb.indications:
                syndrome = str(indication.syndrome or "").strip()
                if syndrome:
                    related_formulas[syndrome].add(ref)

    syndromes: dict[str, Any] = {}
    for name in sorted(related_herbs):
        syndromes[name] = {
            "id": f"syn_{name}",
            "name": name,
            "description": "",
            "symptoms": [],
            "tongue": "",
            "pulse": "",
            "related_herbs": sorted(related_herbs[name]),
            "related_formulas": sorted(related_formulas.get(name, set())),
            # 证候的症状/舌脉目前只能从药材主治描述里反推，不是教材证候定义。
            "derived_from": "indications.description",
            "verified": False,
            "sources": [],
        }

    names = sorted(related_herbs)
    candidates: list[dict[str, Any]] = []
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            score = _similar(left, right)
            if score >= 0.6:
                candidates.append(
                    {
                        "left": left,
                        "right": right,
                        "score": round(score, 3),
                        "decision": "pending",
                        "note": "人工裁决：是同一证的不同表述才合并，否则保留两者",
                    }
                )
    candidates.sort(key=lambda item: -item["score"])

    return (
        {
            "_note": "由 data/herbs/*.json 的 indications.syndrome 反推。只做精确去重；症状/舌脉尚未从教材证候条目抽取，verified=false。",
            "_generated_by": "scripts/derive_master_data.py",
            "syndromes": syndromes,
        },
        {
            "_note": "近似证候名候选，仅用于人工裁决，不自动合并。",
            "candidates": candidates,
        },
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="反推方剂与证候主数据")
    parser.add_argument("--dry-run", action="store_true", help="只打印统计，不写文件")
    args = parser.parse_args(argv)

    formulas = derive_formulas()
    syndromes, candidates = derive_syndromes()

    f_items = formulas["formulas"]
    with_composition = sum(1 for item in f_items.values() if item["composition"])
    s_items = syndromes["syndromes"]

    print(f"方剂：{len(f_items)} 个，其中有组成信息的 {with_composition} 个")
    print(f"证候：{len(s_items)} 个（精确去重后）")
    print(f"近似证候候选：{len(candidates['candidates'])} 对，需人工裁决")
    print()
    print("方剂组成覆盖率最高的 5 个：")
    for fid, item in sorted(f_items.items(), key=lambda kv: -len(kv[1]["composition"]))[:5]:
        herb_names = "、".join(h["herb_name"] for h in item["composition"])
        print(f"  {item['name']}（{len(item['composition'])} 味）：{herb_names}")
    print()
    print("关联药材最多的 5 个证候：")
    for name, item in sorted(s_items.items(), key=lambda kv: -len(kv[1]["related_herbs"]))[:5]:
        print(f"  {name}：{len(item['related_herbs'])} 味")

    if args.dry_run:
        print("\n--dry-run：未写文件")
        return 0

    FORMULA_INDEX.parent.mkdir(parents=True, exist_ok=True)
    SYNDROME_INDEX.parent.mkdir(parents=True, exist_ok=True)
    FORMULA_INDEX.write_text(
        json.dumps(formulas, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    SYNDROME_INDEX.write_text(
        json.dumps(syndromes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    MERGE_CANDIDATES.write_text(
        json.dumps(candidates, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\n已写入：{FORMULA_INDEX}")
    print(f"已写入：{SYNDROME_INDEX}")
    print(f"已写入：{MERGE_CANDIDATES}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
