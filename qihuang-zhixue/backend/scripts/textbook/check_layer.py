"""A/B 观测：教材措辞的病例，能不能走「证候层」而不是退到「功效层」。

背景：76 味人工 monograph 的主治是人工改写过的证名（「脾气虚证」），教材抽出的
证名是「气虚乏力，食少便溏，水肿尿少，中气下陷，久泻脱肛，便血崩漏」。
患者按教材措辞描述时，倒排索引命中后者，而人工药的药证链挂在前者上——两边对不上，
审查器只能退到弱一档的功效层。

这个脚本对每味人工药，用**教材证名**当病例、单开这一味药，看它落在哪一层。
``syndrome`` 是强判定（带教材原文边），``effect_*`` 是弱判定，``None`` 是没判。

用法::

    python -m scripts.textbook.check_layer > before.txt
    # 换完数据、重建图谱后
    python -m scripts.textbook.check_layer > after.txt
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
NORMALIZED = BACKEND / "data" / "curated" / "textbook_normalized.json"
ALIAS_MAP = BACKEND / "data" / "curated" / "herb_alias_map.json"
HERB_DIR = BACKEND / "data" / "herbs"
GENERATED = "textbook_extracted.json"


def main() -> int:
    sys.path.insert(0, str(BACKEND))
    from app.kg.prescription import review

    entries = {item["name"]: item for item in json.loads(NORMALIZED.read_text(encoding="utf-8"))["herbs"]}
    alias_map = (json.loads(ALIAS_MAP.read_text(encoding="utf-8")).get("map") or {})

    rows: list[tuple[str, str, str, str]] = []
    for path in sorted(HERB_DIR.glob("*.json")):
        if path.name == GENERATED:
            continue
        for herb in json.loads(path.read_text(encoding="utf-8")):
            name = herb["name"]
            entry = entries.get(name) or entries.get((alias_map.get(name) or {}).get("textbook", ""))
            if not entry or not entry.get("indications"):
                continue
            case = entry["indications"][0]["syndrome"]
            dose = (herb.get("dosage") or {}).get("max_g") or 9
            result = review({"case": case, "prescription": f"{name}{dose}g"})
            rows.append((name, case, str(result.trace.get("layer")), result.symptom_match_status))

    strong = sum(1 for row in rows if row[2] == "syndrome")
    weak = sum(1 for row in rows if row[2] and row[2].startswith("effect"))
    none = sum(1 for row in rows if not row[2] or row[2] == "None")
    print(f"人工药 {len(rows)} 味，用教材证名当病例：")
    print(f"  证候层（强，带教材原文边）: {strong}")
    print(f"  功效层（弱）: {weak}")
    print(f"  未判定: {none}")
    print()
    for name, case, layer, status in rows:
        mark = "强" if layer == "syndrome" else ("弱" if layer.startswith("effect") else "—")
        print(f"  [{mark}] {name:6s} {layer:14s} {status:10s} {case[:32]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
