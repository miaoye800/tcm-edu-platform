"""裁定「教材抽取版 vs 已有手写条目」的重名冲突（P4.4）。

## 冲突从哪来

``data/herbs/`` 里有 76 味药已经有**手写**的完整条目（麻黄、桂枝、石膏……），
教材抽取又给它们抽了一份。两份都声称自己是对的，这就是 ``conflict``。

## 裁定规则：手写优先

手写条目是人对着教材一句句核过的，还带 ``combinations`` / ``differentiation``
这类教材正文里没有的补充；抽取版只有【药性】【功效】【应用】【用法用量】。
所以规则是**手写版入库、抽取版不入库**——但抽取版不删，它是这份 diff 报告的
依据：两份数据不一致的地方，正是手写版可能抄错、或者抽取版可能抽错的地方。

**这不是「自动判对错」**：脚本只负责把差异摆出来并落一个可追溯的裁定，
谁对谁错要人看报告。所以报告进 ``deliverables/``，不是默默丢掉。

## 为什么裁定要写进 patch 表

否则 76 条 ``conflict`` 会永远挂着，下一批抽样又把它们算进「待复核」，
人每次都要重新判一遍同一件事。裁定一旦做出就必须留痕，否则「已处理」
和「没处理」在库里长得一样。

用法::

    python -m scripts.textbook.resolve_conflicts --dry-run
    python -m scripts.textbook.resolve_conflicts
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.kb.models import Herb
from app.kg import draft
from scripts.textbook.emit_herbs import build_herb

BACKEND = Path(__file__).resolve().parents[2]
HERBS_DIR = BACKEND / "data" / "herbs"
ALIAS_MAP = BACKEND / "data" / "curated" / "herb_alias_map.json"
OUT_MD = BACKEND.parent / "deliverables" / "draft_conflicts.md"
OWN_PRODUCTS = ("textbook_promoted.json", "textbook_extracted.json")

#: 参与比对的字段。``combinations`` / ``differentiation`` 不比——教材正文里
#: 没有这两块，抽取版必然为空，比了就是 76 条噪声。
COMPARE = ("category", "nature", "flavor", "toxicity", "meridians", "effects", "dosage")


def load_curated() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in sorted(HERBS_DIR.glob("*.json")):
        if path.name in OWN_PRODUCTS:
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = raw if isinstance(raw, list) else [raw]
        for item in items:
            if isinstance(item, dict) and item.get("name"):
                out[str(item["name"])] = item
    return out


def _norm(value: object) -> str:
    """归一成可比的字符串。

    两个坑必须在这里挡掉，否则 76 条会全是噪声、真差异被淹掉：

    1. ``3~9g`` vs ``3.0~9.0g`` —— 只是浮点格式不同，不是数据不同。
    2. ``甘、苦`` vs ``苦、甘`` —— 五味/归经/功效的**顺序不携带信息**，
       教材自己排法都不统一（升麻原文写「微甘、辛」）。按集合比。
    """

    if isinstance(value, list):
        return "、".join(sorted(str(v) for v in value))
    if isinstance(value, float):
        return f"{value:g}"
    return str(value or "")


def _dosage(value: dict | None) -> str:
    value = value or {}
    low, high = value.get("min_g"), value.get("max_g")
    fmt = lambda v: "" if v is None else f"{float(v):g}"  # noqa: E731
    return f"{fmt(low)}~{fmt(high)}g"


def diff_one(name: str, extracted: dict, curated: dict) -> list[tuple[str, str, str]]:
    """返回 ``[(字段, 手写版, 抽取版)]``，只列不一致的。"""

    rows: list[tuple[str, str, str]] = []
    for field in COMPARE:
        if field == "dosage":
            left, right = _dosage(curated.get("dosage")), _dosage(extracted.get("dosage"))
        else:
            left, right = _norm(curated.get(field)), _norm(extracted.get(field))
        if left != right:
            rows.append((field, left, right))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="裁定重名冲突")
    parser.add_argument("--dry-run", action="store_true", help="只出报告，不改库")
    parser.add_argument(
        "--reopen",
        action="store_true",
        help="把之前按本规则裁掉的草稿退回 conflict 重判（比对口径改了之后用）",
    )
    args = parser.parse_args(argv)

    if args.reopen:
        back = 0
        for record in draft.list_drafts("rejected"):
            if "已有手写条目" not in (record.review_note or ""):
                continue
            draft.reopen(record.id)
            draft.transition(record.id, "conflict", reviewer="policy:curated-first",
                             note="比对口径修正，重判")
            back += 1
        print(f"退回 conflict {back}")

    curated = load_curated()
    alias_map = json.loads(ALIAS_MAP.read_text(encoding="utf-8")) if ALIAS_MAP.exists() else {}
    extra = alias_map.get("extra_aliases") or {}

    conflicts = [r for r in draft.list_drafts("conflict")]
    if not conflicts:
        print("没有 conflict 草稿。")
        return 0

    sections: list[str] = [
        "# 重名冲突裁定报告",
        "",
        f"共 {len(conflicts)} 味药在 `data/herbs/` 里已有手写条目，教材抽取又抽了一份。",
        "",
        "裁定规则：**手写版入库，抽取版不入库**（手写版经过逐句核对，且带教材正文之外的补充）。",
        "下表列出两份不一致的地方——这些正是需要人再确认一遍的位置。",
        "",
    ]
    differing = 0
    for record in sorted(conflicts, key=lambda r: r.herb_name):
        payload = record.payload
        herb_id = f"herb_curated_{record.id}"
        try:
            built = build_herb(
                herb_id, record.herb_name, payload.get("pinyin", ""), payload,
                {"aliases": extra.get(record.herb_name, [])},
            )
            Herb.model_validate(built)
        except Exception:  # noqa: BLE001 - 抽取版自身不合格就不比了
            built = {}

        base = curated.get(record.herb_name) or {}
        rows = diff_one(record.herb_name, built, base) if built else []
        if rows:
            differing += 1
            sections += [
                f"## {record.herb_name}",
                "",
                "| 字段 | 手写版 | 抽取版 |",
                "| --- | --- | --- |",
            ]
            sections += [f"| {f} | {a[:60]} | {b[:60]} |" for f, a, b in rows]
            sections.append("")

        if args.dry_run:
            continue
        draft.transition(
            record.id, "rejected", reviewer="policy:curated-first",
            note="已有手写条目，抽取版仅作对照（见 deliverables/draft_conflicts.md）",
        )
        draft.add_patch(
            record.id, "status", "conflict", "rejected",
            "手写条目优先；差异见 draft_conflicts.md",
        )

    print(f"冲突 {len(conflicts)}，其中与手写版有字段差异 {differing}")
    if args.dry_run:
        print("--dry-run，未改库。")
        return 0
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(sections) + "\n", encoding="utf-8")
    print(f"md={OUT_MD}")
    print(f"当前: {draft.counts()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
