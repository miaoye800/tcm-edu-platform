"""把教材原文并进 76 味人工 monograph：功效替换、主治追加。

## 为什么

那 76 味是早先手写的，**改写过教材的措辞**。最典型的对比：

    教材黄芪第 1 条：气虚乏力，食少便溏，水肿尿少，中气下陷，久泻脱肛，便血崩漏
    人工黄芪第 1 条：脾气虚证

教材那条是一个**并列的主治清单**，人工把它归并成一个证名，等于替教材做了判断。
后果是两头都够不着：

1. **患者按教材措辞说话时接不住**。P4.5 按教材证名建了 1000 多个证候实体，
   人工药的主治却指向另外 224 个证名，两边对不上，审查器只能退到弱一档的功效层。
2. **「图谱虚假」的一部分来源是人，不是模型**。黄柏人工写「泻火除蒸」「解毒疗疮」，
   教材原文是「清热燥湿，泻火解毒，除骨蒸」——**把教材的词重组了**，意思都变了。

但反过来说，人工那 224 个证名也**不是错的**：它们就是中医通用证名，粒度更粗。
所以功效直接换成教材的（那个确实是错的），主治两套都留、各标来源。
详见下面「换哪些字段」。

## 换哪些字段，不换哪些

换：

- ``effects``：教材【功效】切出的词条（过同义归一）
- ``sources``：教材原文引用，每个字段一条——拼成一大段就不再是连续子串，G008 校验不了
- ``embedding_text``：它是「药名 + 性味 + 功效 + 证名」拼出来的，换了字段必须重算

**加而不换**：

- ``indications``：教材【应用】切出的条目**追加**进来，人工那套保留，各自标
  ``origin``。为什么不是替换——实测过：只留教材那套，224 个人工证候里有 183 个
  失去全部药证链，「风寒表实证」「脾气虚证」「肝阳上亢」全变成没有药指向的孤立
  节点，麻黄汤证那类分析直接断掉。那些是**中医通用证名**，粒度粗但不是错。
  两套都留之后，76 味人工药走证候层的从 34 涨到 57。

不换：

- ``combinations`` / ``differentiation`` / ``tags`` / ``media``：人工精编才有的内容，
  教材抽不出来，凭空补就是编
- ``dosage`` / ``cautions`` / ``nature`` / ``flavor`` / ``meridians``：这几项和教材
  实测一致（四气 0 处差异），没必要动，少动少错

## 毒性是例外：只在教材**明写**时才覆盖

教材【药性】的编排惯例是「有毒的才标」，所以绝大多数药没写毒性。但这条惯例
**至少有一处是假的**：

    细辛【药性】辛，温。归心、肺、肾经。      ← 教材没标毒性
    细辛【使用注意】用量不宜过大……多则气闷塞，不通者死。

教材没标，不代表无毒；按「未标=无毒」去覆盖，细辛的 ``toxic_misuse`` 阻断就没了。
所以这里的规则是：

- 教材 ``toxicity_source == "stated"``（明写「有毒/小毒/大毒」）→ 用教材的
- 否则 → **保留人工已有的值**；人工也没有就留 ``毒性未知``

「教材没写」永远不能伪装成「教材说无毒」。

用法::

    python -m scripts.textbook.merge_manual --dry-run   # 只看会改什么
    python -m scripts.textbook.merge_manual             # 真写
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
NORMALIZED = BACKEND / "data" / "curated" / "textbook_normalized.json"
ALIAS_MAP = BACKEND / "data" / "curated" / "herb_alias_map.json"
HERB_DIR = BACKEND / "data" / "herbs"
OUT_MD = BACKEND.parent / "deliverables" / "manual_textbook_merge.md"

SOURCE_TITLE = "《中药学》(钟赣生·新世纪第四版)"

#: 每个字段一条 Source。拼成一大段就不再是连续子串，G008 没法校验。
SOURCE_FIELDS = ("药性", "功效", "应用", "用法用量", "使用注意")

#: 教材生成的文件，不是人工 monograph，不能碰。
GENERATED = "textbook_extracted.json"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def resolve_entry(name: str, entries: dict[str, dict], alias_map: dict[str, dict]) -> tuple[dict | None, str]:
    """人工药名 → 教材条目。返回 ``(条目, 依据)``。"""

    if name in entries:
        return entries[name], "同名"
    mapped = (alias_map.get(name) or {}).get("textbook")
    if mapped and mapped in entries:
        return entries[mapped], f"别名映射（{mapped}）"
    return None, ""


def build_sources(entry: dict) -> list[dict]:
    ref_parts = [SOURCE_TITLE]
    if entry.get("chapter_no"):
        ref_parts.append(entry["chapter_no"])
    if entry.get("section"):
        ref_parts.append(entry["section"])
    ref_parts.append(entry["name"])
    ref = "·".join(ref_parts)
    return [
        {"type": "教材", "ref": ref, "quote": entry["quotes"][field]}
        for field in SOURCE_FIELDS
        if (entry.get("quotes") or {}).get(field)
    ]


def build_embedding_text(herb: dict) -> str:
    """重算向量检索用的拼接文本。

    格式沿用原来的：``药名 别名 小节 味+气 归X经 功效… 证名… 用法备注``。
    换了功效和主治就必须重算，否则向量库里还是旧的那段话。
    """

    pieces: list[str] = [herb["name"]]
    if herb.get("aliases"):
        pieces.append(" ".join(herb["aliases"]))
    if herb.get("subcategory"):
        pieces.append(herb["subcategory"])
    taste = "".join(herb.get("flavor") or []) + (herb.get("nature") or "")
    if taste:
        pieces.append(taste)
    if herb.get("meridians"):
        pieces.append("归" + "".join(herb["meridians"]) + "经")
    if herb.get("effects"):
        pieces.append(" ".join(herb["effects"]))
    syndromes = [item.get("syndrome", "") for item in herb.get("indications") or []]
    syndromes = [item for item in syndromes if item]
    if syndromes:
        pieces.append(" ".join(syndromes))
    notes = ((herb.get("dosage") or {}).get("usage_notes") or "").strip()
    if notes:
        pieces.append(notes)
    return " ".join(pieces)


def merge_one(herb: dict, entry: dict) -> dict:
    """就地改一个 herb dict，返回改动摘要。"""

    before = {
        "indications": len(herb.get("indications") or []),
        "effects": list(herb.get("effects") or []),
        "toxicity": str(herb.get("toxicity") or ""),
    }

    # 两套主治都留，各自标 origin。
    #
    # **只留教材那套是错的，实测过**：换掉之后 224 个人工证候里有 183 个失去全部
    # 药证链——「风寒表实证」「脾气虚证」「肝阳上亢」全变成没有药指向的孤立节点，
    # 麻黄汤证那类分析直接断掉。那些是**中医通用证名**，不是错，只是粒度更粗。
    #
    # 教材那套解决的是另一个问题：患者按教材措辞说「气虚乏力，食少便溏」时，
    # 只有教材条目能接住。实测把两套都留下，76 味人工药走证候层的从 34 涨到 57。
    textbook_items = [
        {
            "syndrome": item["syndrome"],
            "description": item["description"],
            "origin": "textbook",
        }
        for item in entry.get("indications") or []
    ]
    manual_items = [
        {
            "syndrome": item["syndrome"],
            "description": item.get("description", ""),
            "origin": item.get("origin") or "manual",
        }
        for item in herb.get("indications") or []
        if item.get("syndrome")
    ]
    # 同名时以教材那条为准：同一个证名，教材的说明是原文
    seen = {item["syndrome"] for item in textbook_items}
    herb["indications"] = textbook_items + [
        item for item in manual_items if item["syndrome"] not in seen
    ]
    herb["effects"] = list(entry.get("effects") or [])

    # 教材正名和人工药名不同（巴豆 vs 巴豆霜），正名进 aliases，学生搜得到
    if entry["name"] != herb["name"]:
        aliases = list(herb.get("aliases") or [])
        if entry["name"] not in aliases:
            aliases.append(entry["name"])
        herb["aliases"] = sorted(set(aliases))

    # 毒性：只在教材明写时覆盖，理由见模块 docstring
    if entry.get("toxicity_source") == "stated":
        herb["toxicity"] = entry["toxicity"]

    herb["sources"] = build_sources(entry)
    herb["verified"] = True
    herb["confidence"] = "high"
    herb["embedding_text"] = build_embedding_text(herb)

    after = {
        "indications": len(herb["indications"]),
        "effects": list(herb["effects"]),
        "toxicity": str(herb.get("toxicity") or ""),
    }
    return {"before": before, "after": after}


def main() -> int:
    parser = argparse.ArgumentParser(description="人工 monograph → 教材原文")
    parser.add_argument("--dry-run", action="store_true", help="只打印统计，不写文件")
    args = parser.parse_args()

    payload = load_json(NORMALIZED)
    if not payload:
        print(f"缺少 {NORMALIZED}，先跑 scripts.textbook.normalize")
        return 1
    entries = {item["name"]: item for item in payload["herbs"]}
    alias_map = (load_json(ALIAS_MAP) or {}).get("map") or {}

    rows: list[tuple[str, str, dict]] = []
    unmatched: list[str] = []
    touched_files: list[Path] = []

    for path in sorted(HERB_DIR.glob("*.json")):
        if path.name == GENERATED:
            continue
        data = load_json(path)
        if data is None:
            continue
        items = data if isinstance(data, list) else (data.get("herbs") or [])
        if not isinstance(items, list):
            continue

        changed = False
        for herb in items:
            name = herb.get("name") or ""
            entry, basis = resolve_entry(name, entries, alias_map)
            if entry is None:
                unmatched.append(name)
                continue
            diff = merge_one(herb, entry)
            rows.append((name, basis, diff))
            changed = True

        if changed:
            touched_files.append(path)
            if not args.dry_run:
                path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )

    total_before = sum(row[2]["before"]["indications"] for row in rows)
    total_after = sum(row[2]["after"]["indications"] for row in rows)
    print(f"人工 monograph: {len(rows)} 味（文件 {len(touched_files)} 个）")
    print(f"教材未收录: {len(unmatched)} {unmatched}")
    print(f"主治条目: {total_before} → {total_after}")

    effect_changed = [row for row in rows if row[2]["before"]["effects"] != row[2]["after"]["effects"]]
    tox_changed = [row for row in rows if row[2]["before"]["toxicity"] != row[2]["after"]["toxicity"]]
    print(f"功效变化: {len(effect_changed)} 味")
    for name, _basis, diff in effect_changed:
        print(f"  {name}: {diff['before']['effects']} → {diff['after']['effects']}")
    print(f"毒性变化: {len(tox_changed)} 味")
    for name, _basis, diff in tox_changed:
        print(f"  {name}: {diff['before']['toxicity']} → {diff['after']['toxicity']}")

    if args.dry_run:
        return 0

    lines = [
        "# 人工 monograph → 教材原文（P4.6）",
        "",
        f"来源：{SOURCE_TITLE}",
        "",
        "## 一、总账",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| 处理的人工 monograph | {len(rows)} 味 |",
        f"| 涉及文件 | {len(touched_files)} 个 |",
        f"| 主治条目 | {total_before} → {total_after} |",
        f"| 功效有变化的药 | {len(effect_changed)} 味 |",
        f"| 毒性有变化的药 | {len(tox_changed)} 味 |",
        "",
        "## 二、别名映射才找到教材条目的",
        "",
        "| 人工药名 | 依据 |",
        "| --- | --- |",
    ]
    for name, basis, _diff in rows:
        if basis != "同名":
            lines.append(f"| {name} | {basis} |")

    lines += [
        "",
        "## 三、功效与教材不一致的（已按教材改）",
        "",
        "| 药名 | 原值（人工） | 现值（教材） |",
        "| --- | --- | --- |",
    ]
    for name, _basis, diff in effect_changed:
        lines.append(
            f"| {name} | {'、'.join(diff['before']['effects'])} | "
            f"{'、'.join(diff['after']['effects'])} |"
        )

    lines += [
        "",
        "## 四、毒性",
        "",
        "教材明写毒性的才覆盖；没写的一律保留人工值——"
        "「教材没写」不等于「教材说无毒」。",
        "",
        "| 药名 | 原值 | 现值 |",
        "| --- | --- | --- |",
    ]
    for name, _basis, diff in tox_changed:
        lines.append(f"| {name} | {diff['before']['toxicity']} | {diff['after']['toxicity']} |")

    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"md={OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
