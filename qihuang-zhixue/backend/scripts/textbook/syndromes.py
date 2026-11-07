"""把教材的主治证名建成证候实体，让药证链接得上。

## 为什么需要这一步

P4 把 86 味孤儿药补成了结构化条目，但建图谱时发现**药证链只建成 315 条**：
教材抽出的证名（「寒积便秘」「喉风，喉痹」）在现有 224 个证候里找不到，
边就被跳过了。等于「有药无证」——药材的完整数据进了图，却接不到证候上。

## 现有 224 个证候为什么对不上

它们是早先从 76 味人工 monograph 的 ``indication.syndrome`` 反推的，
而人工那份数据**改写过教材的措辞**：

    教材黄芪第 1 条：气虚乏力，食少便溏，水肿尿少，中气下陷，久泻脱肛，便血崩漏
    人工黄芪第 1 条：脾气虚证

教材原文 1027 个证名里，能和现有 224 个对上的只有 **113** 个。所以不能
让教材去对齐现有表，只能反过来——**教材是权威**。

## 做法

1. 教材证名全部建实体，``id = syn_<原文>``，保持教材粒度不拆
2. 含逗号的并列证名，把各项拆成 **alias**（「肝阳上亢，头痛眩晕」→
   别名「肝阳上亢」「头痛眩晕」），这样学生搜单项也能命中
3. 末尾的「证」字作为别名（「脾胃气滞证」≈「脾胃气滞」）
4. 现有 224 个同名的不重复建；不同名的通过别名桥接，**不删**

不做的事：不合并「X」和「X证」以外的近似名。像「丹毒痈肿」和
「痄腮丹毒痈肿」这种，合并要医学判断，交给人。

用法::

    python -m scripts.textbook.syndromes --stats
    python -m scripts.textbook.syndromes
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
NORMALIZED = BACKEND / "data" / "curated" / "textbook_normalized.json"
OUT_JSON = BACKEND / "data" / "syndromes" / "_textbook.json"

SOURCE_TITLE = "《中药学》(钟赣生·新世纪第四版)"

#: 并列分隔符。教材混用「，、；」，统一成「，」。
_SPLIT = re.compile(r"[，、；;]")

#: 证名两端的杂字符。
_TRIM = re.compile(r"^[\s\u3000，、；。]+|[\s\u3000，、；。]+$")

#: 单项最短长度。1 个字的多半是切分残留，不成证。
_MIN_ITEM = 2


def normalize_name(raw: str) -> str:
    """统一空白与标点，不改任何字。

    **不删字、不改字**——证名是教材原文，改了就对不上原文引用。
    只做「同一句话的不同标点写法」的归一。
    """

    text = re.sub(r"[\s\u3000]+", "", raw or "")
    text = text.replace("、", "，").replace("；", "，").replace(";", "，")
    text = re.sub(r"，+", "，", text)
    return _TRIM.sub("", text)


def split_items(name: str) -> list[str]:
    """拆出并列的单项，用于生成别名。"""

    out: list[str] = []
    for piece in _SPLIT.split(name):
        item = _TRIM.sub("", piece)
        if len(item) >= _MIN_ITEM:
            out.append(item)
    return out


def aliases_for(name: str) -> list[str]:
    """证名的别名：并列单项 + 去掉末尾「证」字。

    只做这两种，都是有依据的纯字面操作：
    - 并列拆分：教材把多个主治写在一个条目里，拆开是为了搜得到
    - 去「证」后缀：「脾胃气滞证」和「脾胃气滞」指同一件事，教材自己混用
    """

    found: list[str] = []
    if "，" in name:
        found.extend(split_items(name))
    if name.endswith("证") and len(name) > 3:
        found.append(name[:-1])
    return [item for item in dict.fromkeys(found) if item != name]


def build() -> dict:
    if not NORMALIZED.exists():
        raise SystemExit(f"缺少 {NORMALIZED}，先跑 scripts.textbook.normalize")
    payload = json.loads(NORMALIZED.read_text(encoding="utf-8"))
    herbs = payload["herbs"]

    # ---- 收集：证名 → 药材 ----
    owners: dict[str, list[str]] = defaultdict(list)
    quotes: dict[str, str] = {}
    #: 证名 → 教材【应用】里的说明文字。
    #:
    #: 这个必须收：``app/kg/symptoms.py`` 的倒排索引是拿「证候名 + 说明」
    #: 建 profile 的。只填名字的话，新证候能匹配到的短语少一大截，
    #: 症状映射的候选质量会明显下降。
    descriptions: dict[str, str] = {}
    for herb in herbs:
        for item in herb.get("indications") or []:
            name = normalize_name(item.get("syndrome") or "")
            if not name:
                continue
            if herb["name"] not in owners[name]:
                owners[name].append(herb["name"])
            desc = (item.get("description") or "").strip()
            if desc and len(desc) > len(descriptions.get(name, "")):
                descriptions[name] = desc
            # 取第一条能对上的原文引用，供证据链用
            if name not in quotes and herb.get("quotes", {}).get("应用"):
                quotes[name] = herb["quotes"]["应用"][:300]

    # ---- 与现有证候表比对 ----
    #
    # **只能读 _index.json，不能用 load_syndromes()。**
    # load_syndromes() 已经把本脚本上一次的输出（_textbook.json）合并进去了，
    # 再拿它当「现有表」比对，教材证名会全部命中自己，entities 变成空。
    # 这个循环依赖第一次跑就踩到了：输出「新建 0 条、别名 0 条」。
    existing_path = BACKEND / "data" / "syndromes" / "_index.json"
    existing: dict[str, dict] = {}
    if existing_path.exists():
        raw = json.loads(existing_path.read_text(encoding="utf-8"))
        existing = dict(raw.get("syndromes") or {})

    entities: dict[str, dict] = {}
    reused: list[str] = []
    for name, herb_names in owners.items():
        if name in existing:
            reused.append(name)
            continue
        chapter_hint = ""
        for herb in herbs:
            if herb["name"] == herb_names[0]:
                parts = [SOURCE_TITLE]
                if herb.get("chapter_no"):
                    parts.append(herb["chapter_no"])
                parts.append(herb["name"])
                chapter_hint = "·".join(parts)
                break
        entities[name] = {
            "id": f"syn_{name}",
            "name": name,
            "description": descriptions.get(name, ""),
            "symptoms": [],
            "tongue": "",
            "pulse": "",
            "related_herbs": herb_names,
            "related_formulas": [],
            "derived_from": "textbook.indications",
            # 证名和说明都是教材原文；symptoms/tongue/pulse 还是空的——
            # 那三项要从【应用】的说明里再抽一层（症状实体），还没做。
            "verified": True,
            "sources": [
                {
                    "type": "教材",
                    "ref": chapter_hint or SOURCE_TITLE,
                    "quote": quotes.get(name, ""),
                }
            ]
            if quotes.get(name)
            else [],
        }

    # ---- 别名表：别名 → 正名 ----
    #
    # 只登记**不冲突**的：如果某个别名本身就是另一条正名，就不覆盖，
    # 否则会出现「搜 A 出来的是 B」这种更糟的情况。
    alias_map: dict[str, str] = {}
    conflicts: list[tuple[str, str, str]] = []
    all_names = set(owners) | set(existing)
    for name in entities:
        for alias in aliases_for(name):
            if alias in all_names:
                conflicts.append((alias, name, "别名本身是另一条正名"))
                continue
            if alias in alias_map and alias_map[alias] != name:
                conflicts.append((alias, name, f"已被 {alias_map[alias]} 占用"))
                continue
            alias_map[alias] = name

    return {
        "version": "textbook_syndromes_v1",
        "source": SOURCE_TITLE,
        "stats": {
            "textbook_names": len(owners),
            "new_entities": len(entities),
            "reused_existing": len(reused),
            "aliases": len(alias_map),
            "alias_conflicts": len(conflicts),
            "existing_total": len(existing),
        },
        "aliases": alias_map,
        "alias_conflicts": [
            {"alias": a, "target": t, "reason": r} for a, t, r in conflicts
        ],
        "syndromes": entities,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="教材证名 → 证候实体")
    parser.add_argument("--stats", action="store_true")
    args = parser.parse_args()

    payload = build()
    stats = payload["stats"]
    print(f"教材证名: {stats['textbook_names']}")
    print(f"新建证候实体: {stats['new_entities']}")
    print(f"复用现有证候: {stats['reused_existing']}")
    print(f"别名: {stats['aliases']}（冲突 {stats['alias_conflicts']} 条已跳过）")

    lens = Counter(min(len(n) // 5 * 5, 40) for n in payload["syndromes"])
    print(f"证名长度分布: {dict(sorted(lens.items()))}")

    if args.stats:
        return 0

    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"json={OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
