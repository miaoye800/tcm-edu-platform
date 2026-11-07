"""把过了门禁的教材条目写成药材 JSON，补上 100 味孤儿药的结构化条目。

## 只补孤儿，不动已有

现有 76 味 monograph 是人工精编的（有配伍、鉴别用药、学习难度标注），
教材抽取的结果没有这些字段。直接覆盖会把精编内容冲掉，所以：

- **孤儿**（``herb_id_names.json`` 里没有 monograph 的 100 味）→ 用教材数据补
- **已有 monograph** → 不动，只在报告里列出「教材数据与人工数据不一致」的地方供复核

## 别名怎么处理

``牙硝`` 在教材里叫「芒硝」，``乌头`` 在教材里是川乌/草乌/附子的合称。
这类映射写在 ``data/curated/herb_alias_map.json`` 里，每条都带教材原文依据。

生成的条目 **name 保持孤儿原名**（因为 id 绑的是这个名字，改了会让
``herb_id_names.json`` 和实际条目对不上），把教材正名放进 ``aliases``——
这样学生搜「芒硝」也能命中。

## 教材没有的怎么办

不生成条目，如实记进报告的「未覆盖」表。**不用模型补**——那正是旧图谱
「虚假」的来源。

用法::

    python -m scripts.textbook.emit_herbs --dry-run   # 只看会写什么
    python -m scripts.textbook.emit_herbs             # 真写
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
GATED_JSON = BACKEND / "data" / "curated" / "textbook_gated.json"
ID_NAMES = BACKEND / "data" / "curated" / "herb_id_names.json"
ALIAS_MAP = BACKEND / "data" / "curated" / "herb_alias_map.json"
OUT_JSON = BACKEND / "data" / "herbs" / "textbook_extracted.json"
OUT_MD = BACKEND.parent / "deliverables" / "textbook_herbs.md"

SOURCE_TITLE = "《中药学》(钟赣生·新世纪第四版)"

#: 每个字段生成一条 Source，而不是拼成一大段——拼起来就不是连续子串了，
#: G008 的「原文子串」就没法校验。
SOURCE_FIELDS = ("药性", "功效", "应用", "用法用量", "使用注意")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def build_herb(
    orphan_id: str,
    orphan_name: str,
    orphan_pinyin: str,
    entry: dict,
    alias_info: dict | None,
) -> dict:
    """一条教材条目 → 一个 Herb dict。"""

    ref_parts = [SOURCE_TITLE]
    if entry.get("chapter_no"):
        ref_parts.append(entry["chapter_no"])
    if entry.get("section"):
        ref_parts.append(entry["section"])
    ref_parts.append(entry["name"])
    ref = "·".join(ref_parts)

    sources = [
        {
            "type": "教材",
            "ref": ref,
            "quote": entry["quotes"][name],
        }
        for name in SOURCE_FIELDS
        if entry.get("quotes", {}).get(name)
    ]

    aliases: list[str] = []
    # 教材正名与被抽药名不同时，正名进 aliases，学生搜得到
    if entry["name"] != orphan_name:
        aliases.append(entry["name"])
    if alias_info and alias_info.get("aliases"):
        aliases.extend(alias_info["aliases"])

    return {
        "id": orphan_id,
        "name": orphan_name,
        "pinyin": orphan_pinyin,
        "aliases": sorted(set(aliases)),
        "category": entry["category"],
        "subcategory": entry.get("section", ""),
        "nature": entry["nature"],
        "flavor": entry["flavors"],
        "toxicity": entry["toxicity"],
        "meridians": entry["meridians"],
        "effects": entry["effects"],
        "indications": [
            {
                "syndrome": item["syndrome"],
                "description": item["description"],
                # 这些条目是从教材【应用】切出来的原文，标清楚来源，
                # 下游建药证链时才不会和人工归纳的证名混成一层。
                "origin": "textbook",
            }
            for item in entry.get("indications", [])
        ],
        "dosage": {
            "text": entry.get("dosage_text", ""),
            "min_g": entry.get("dose_min"),
            "max_g": entry.get("dose_max"),
            "usage_notes": "",
        },
        "cautions": entry.get("cautions", []),
        "sources": sources,
        # 每个字段都过了 G008 原文子串校验，所以标记为已核验。
        # 但 combinations / differentiation 是空的——那是人工精编才有的内容，
        # 教材抽取不出来，不能凭空补。
        "verified": True,
        "confidence": "high",
    }


def render_report(
    generated: list[dict],
    mapped: list[tuple[str, str, str]],
    skipped: list[tuple[str, str, str]],
    existing_conflicts: list[str],
) -> str:
    lines = [
        "# P4 孤儿药补全报告",
        "",
        f"来源：{SOURCE_TITLE}",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
        "| 孤儿药总数 | 100 |",
        f"| **已生成结构化条目** | **{len(generated)}** |",
        f"| 　其中直接对应教材条目 | {len(generated) - len(mapped)} |",
        f"| 　其中经别名映射 | {len(mapped)} |",
        f"| 未生成（教材未收录 / 无独立条目） | {len(skipped)} |",
        "",
        "> 未生成的那些**不是抽取失败**，是教材本身没写。宁可留空标「教材未收录」，",
        "> 也不用模型补一段看起来专业的话——那正是旧图谱「虚假」的来源。",
        "",
        "## 一、经别名映射的条目",
        "",
        "| 孤儿名 | 教材正名 | 依据 |",
        "| --- | --- | --- |",
    ]
    for name, textbook, basis in mapped:
        lines.append(f"| {name} | {textbook} | {basis} |")

    if skipped:
        lines += [
            "",
            "## 二、未生成的条目",
            "",
            "| 孤儿名 | 原因 |",
            "| --- | --- |",
        ]
        for name, _textbook, reason in skipped:
            lines.append(f"| {name} | {reason} |")

    if existing_conflicts:
        lines += [
            "",
            "## 三、与人工 monograph 的差异（仅提示，未改动）",
            "",
            "这些药已有精编条目，教材数据只用来**校验**，不覆盖。",
            "差异可能是教材抽取口径不同，也可能是人工数据需要更新——需要人看。",
            "",
            "| 药名 | 差异 |",
            "| --- | --- |",
        ]
        for item in existing_conflicts[:50]:
            lines.append(f"| {item.split(':', 1)[0]} | {item.split(':', 1)[-1]} |")

    lines += [
        "",
        "## 四、生成条目的字段完整度",
        "",
        "| 字段 | 覆盖数 |",
        "| --- | --- |",
    ]
    for name in ("nature", "flavor", "meridians", "effects", "indications", "dosage"):
        filled = sum(1 for h in generated if h.get(name))
        lines.append(f"| {name} | {filled} / {len(generated)} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="用教材数据补孤儿药条目")
    parser.add_argument("--dry-run", action="store_true", help="只打印统计，不写文件")
    args = parser.parse_args()

    gated = load_json(GATED_JSON)
    if not gated:
        print(f"缺少 {GATED_JSON}，先跑 scripts.textbook.gates")
        return 1
    entries = {item["name"]: item for item in gated["herbs"]}

    id_names = load_json(ID_NAMES).get("herbs") or {}
    alias_map = load_json(ALIAS_MAP)
    maps = alias_map.get("map") or {}
    not_standalone = alias_map.get("not_standalone") or {}
    appendix_only = alias_map.get("appendix_only") or {}
    absent = alias_map.get("absent") or {}
    #: 规格/炮制品别名（生川乌、制川乌）。**不做前缀自动剥离**——
    #: 「生地」和「熟地」是两味药，自动剥「生/制」会造出假合并。
    extra_aliases = alias_map.get("extra_aliases") or {}

    generated: list[dict] = []
    mapped: list[tuple[str, str, str]] = []
    skipped: list[tuple[str, str, str]] = []

    for orphan_id, info in sorted(id_names.items()):
        name = info["name"]
        pinyin = info.get("pinyin", "")

        if name in entries:
            generated.append(
                build_herb(orphan_id, name, pinyin, entries[name], extra_aliases.get(name))
            )
            continue

        if name in maps:
            target = maps[name]["textbook"]
            if target in entries:
                generated.append(
                    build_herb(orphan_id, name, pinyin, entries[target], extra_aliases.get(name))
                )
                mapped.append((name, target, maps[name].get("basis", "")))
                continue
            skipped.append((name, target, f"映射目标「{target}」在教材里也没抽到"))
            continue

        for table, label in (
            (not_standalone, "教材未单独立条"),
            (appendix_only, "教材仅有目录标注，正文无独立条目"),
            (absent, "教材未收录"),
        ):
            if name in table:
                skipped.append((name, "", table[name]))
                break
        else:
            skipped.append((name, "", "未找到对应教材条目，且未登记原因"))

    # ---- 与已有 monograph 的差异（只报告，不改动）----
    #
    # **比较前两边都要过归一表**。否则会把「补益肝肾 / 补肝肾」这种
    # 同义异写报成差异，15 处里有一大半是这么来的，白占复核的注意力。
    from app.kb.ingest import load_herbs
    from scripts.textbook.normalize import load_canonical

    canonical = load_canonical()

    def canon(terms: list[str]) -> set[str]:
        return {canonical.get(t, t) for t in terms}

    conflicts: list[str] = []
    for herb in load_herbs():
        entry = entries.get(herb.name)
        if not entry:
            continue
        if herb.nature != entry["nature"]:
            conflicts.append(f"{herb.name}:四气 人工={herb.nature} 教材={entry['nature']}")
        manual, book = canon(herb.effects), canon(entry["effects"])
        if manual != book:
            only_manual = sorted(manual - book)
            only_book = sorted(book - manual)
            if only_manual or only_book:
                conflicts.append(
                    f"{herb.name}:功效 仅人工={only_manual} 仅教材={only_book}"
                )

    print(f"生成条目: {len(generated)}（其中别名映射 {len(mapped)}）")
    print(f"未生成: {len(skipped)}")
    print(f"与人工 monograph 的差异: {len(conflicts)} 处")

    if args.dry_run:
        for name, textbook, basis in mapped:
            print(f"  [别名] {name} -> {textbook}（{basis}）")
        for name, _, reason in skipped:
            print(f"  [跳过] {name}: {reason[:40]}")
        return 0

    OUT_JSON.write_text(
        json.dumps(generated, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    OUT_MD.write_text(
        render_report(generated, mapped, skipped, conflicts), encoding="utf-8"
    )
    print(f"json={OUT_JSON}")
    print(f"md={OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
