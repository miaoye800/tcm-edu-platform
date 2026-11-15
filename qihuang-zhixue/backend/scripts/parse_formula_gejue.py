"""从教学文档《方剂歌诀》抽出「章 → 节 → 方名」条目，生成
``backend/data/curated/formula_entries.json``。

为什么用这份文档，而不是让模型列方名
------------------------------------
方剂学教材本身 235 首正方 + 216 首附方，但仓库里没有**带章节**的方名数据
（``data/formulas/_index.json`` 是从药味组合反推的 40 首，没有 chapter/section）。
手打一份"看起来完整"的方名表，风险很具体：炙甘草汤在不同版次里落在
「气血双补剂」还是「气血阴阳并补剂」就不一样，填错了没人看得出来。

所以改用可核对的教学文档：安徽中医药大学《方剂歌诀》（掌握★ / 熟悉* / 了解▲），
其方歌出处直接标注为「李冀,左铮云.方剂学[M].北京:中国中医药出版社,2021」——
正是本项目 ``data/syllabus/formula.json`` 用的那一版。这份文档本身就带着
章、节标题，方名与节是**一起**给出来的，不是事后配的。等级标记同时也是一份
真实的考纲信号（全掌握方 = 必考），比编一个 ``exam_frequency`` 靠谱。

它不完美，所以脚本把不完美的地方显式处理：

* 有的方名是**教材简称**（``麻杏甘石汤``），统一映射到教材正名，
  映射表见 ``NORMALIZE``，一条一条都注明了出处；
* 它收的是"要求掌握的方"，不等于教材全部正方。数量对不上教材 235 首是**正常**的，
  所以在 JSON 里写 ``coverage_note`` 说明，而不是拿它冒充全集；
* 驱虫剂、涌吐剂两章教材不分节，方名直接列在章下——归到该章的虚拟节，
  不为了凑层级造一个假节名。

用法::

    python scripts/parse_formula_gejue.py --pdf path/to/方剂歌诀.pdf
    python scripts/parse_formula_gejue.py            # 用 .tmpsrc 下已下载的副本
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "curated" / "formula_entries.json"

SOURCE_URL = (
    "https://gjjk.ahtcm.edu.cn/__local/E/84/6C/1072DF73084204367432706B45B_DC3F4202_75A90.pdf"
)

#: 教材简称 → 教材正名。只收**能确定**的同方异名，拿不准的一律不动
#: （宁可留下简称，也不要改成一个自己都觉得可疑的名字）。
NORMALIZE: dict[str, str] = {
    "麻杏甘石汤": "麻黄杏仁甘草石膏汤",  # 教材正名，歌诀用简称
    "滚痰丸": "礞石滚痰丸",  # 教材作《丹溪心法》礞石滚痰丸
    "葱白七味引": "葱白七味饮",  # 歌诀末字笔误，「饮」为教材名
}

IMPORTANCE = {"★": "掌握", "*": "熟悉", "▲": "了解"}

_HEAD = re.compile(r"^第([一二三四五六七八九十]+)([章节])\s*(.*)$")
_FORMULA = re.compile(r"^([\u4e00-\u9fa5]{2,12}?)\s*([★*▲])\s*$")

_CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def cn_to_int(text: str) -> int:
    if text == "十":
        return 10
    if text.startswith("十"):
        return 10 + _CN.get(text[1], 0)
    if "十" in text:
        head, tail = text.split("十", 1)
        return _CN.get(head, 1) * 10 + _CN.get(tail, 0)
    if text in _CN:
        return _CN[text]
    raise ValueError(f"无法解析中文序数：{text!r}")


def extract_text(pdf: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(pdf))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def parse(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """返回 (章节树, 无节章下直接出现的方名)。

    「无节章」不是解析失败：教材第 19、20 章本来就不分节。

    每首方顺带收下**方歌正文**与出处。方歌是这份文档的主体内容，
    只取方名等于把 46 页资料当成一张名单用；而方歌恰好是"点开穴位能看的东西"，
    且出处逐条可核（格式固定为「（作者.书名[M].版次.出版社,年:页.）」）。
    """

    chapters: dict[int, dict[str, Any]] = {}
    order: list[int] = []
    loose: dict[int, list[tuple[str, str]]] = {}
    chapter_no: int | None = None
    section: dict[str, Any] | None = None
    orphans: list[str] = []
    current: dict[str, Any] | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        head = _HEAD.match(line)
        if head:
            current = None
            no = cn_to_int(head.group(1))
            if head.group(2) == "章":
                chapter_no = no
                if no not in chapters:
                    order.append(no)
                chapters[no] = {"book_no": no, "title": head.group(3).strip(), "sections": []}
                loose.setdefault(no, [])
                section = None
            else:
                if chapter_no is None:
                    raise ValueError(f"节标题出现在章之前：{line!r}")
                section = {"no": no, "title": head.group(3).strip(), "entries": []}
                chapters[chapter_no]["sections"].append(section)
            continue

        m = _FORMULA.match(line)
        if m:
            name = NORMALIZE.get(m.group(1), m.group(1))
            mark = IMPORTANCE[m.group(2)]
            current = {"name": name, "importance": mark, "verse": [], "cite": ""}
            if section is not None:
                section["entries"].append(current)
            elif chapter_no is not None:
                loose[chapter_no].append(current)
            else:
                orphans.append(line)
                current = None
            continue

        if current is None:
            continue
        if line[0] in "（(":
            # 出处行：括号里是「作者.书名[M].版次.出版社,年:页.」
            current["cite"] = line.strip("（）() ")
            continue
        # 歌诀行：四字/七字一句，以逗号或句号收尾。别把页码、页眉当歌诀。
        if len(current["verse"]) < 4 and line[-1] in "，。；,;":
            current["verse"].append(line)

    for no in order:
        if not chapters[no]["sections"]:
            # 不分节的章：给一个与章同名的虚拟节，虚拟标记由 build_syllabus 认定。
            chapters[no]["sections"].append(
                {"no": 1, "title": chapters[no]["title"], "entries": loose.get(no, [])}
            )
        elif loose.get(no):
            # 章下直接列、不在任何节里的方名（理论上不该有），不要静默丢掉。
            orphans.extend(f"{chapters[no]['title']}:{e['name']}" for e in loose[no])

    return [chapters[no] for no in order], orphans


def build(pdf: Path) -> dict[str, Any]:
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    chapters, orphans = parse(extract_text(pdf))
    if orphans:
        raise SystemExit(f"有方名落不到章/节：{orphans}")

    total = sum(len(s["entries"]) for c in chapters for s in c["sections"])
    return {
        "subject": "formula",
        "schema_version": 1,
        "source": {
            "title": "方剂歌诀（掌握★，熟悉*，了解▲）",
            "publisher": "安徽中医药大学 经络腧穴（教学资源）",
            "url": SOURCE_URL,
            "sha256": digest,
            "pages": 46,
            "cites": "方歌出处逐条标注为「李冀,左铮云.方剂学[M].北京:中国中医药出版社,2021」",
            "why": (
                "仓库里没有带章节的方名数据；这份文档的方名与章节是同时给出的，"
                "不需要事后把方名往节里配，避免「炙甘草汤该归气血双补还是阴阳并补」这类错配。"
            ),
        },
        "coverage_note": (
            "本文件收的是该文档要求掌握／熟悉／了解的方（含部分教材附方），"
            f"共 {total} 首；教材正方 235 首、附方 216 首不以此文件为准。"
            "缺的方按「没有数据」处理，不补编。"
        ),
        "chapters": [
            {
                "book_no": c["book_no"],
                "title": c["title"],
                "sections": [
                    {
                        "no": s["no"],
                        "title": s["title"],
                        "entries": [_entry_out(e) for e in s["entries"]],
                    }
                    for s in c["sections"]
                ],
            }
            for c in chapters
        ],
    }


def _entry_out(entry: dict[str, Any]) -> dict[str, Any]:
    """输出形态：``gejue`` 是一整句，方便直接显示；``cite`` 只留括号内的出处。"""

    out = {
        "name": entry["name"],
        "importance": entry["importance"],
        "gejue": "".join(entry.get("verse") or []),
        "cite": entry.get("cite") or "",
    }
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="解析《方剂歌诀》→ formula_entries.json")
    ap.add_argument("--pdf", default=str(ROOT / "data" / "sources" / "formula_gejue.pdf"))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--check", action="store_true", help="只校验，不写盘")
    args = ap.parse_args(argv)

    pdf = Path(args.pdf)
    if not pdf.exists():
        raise SystemExit(f"找不到 PDF：{pdf}（先下载 {SOURCE_URL}）")

    doc = build(pdf)
    chapters = doc["chapters"]
    secs = [s for c in chapters for s in c["sections"]]
    total = sum(len(s["entries"]) for s in secs)
    named = [e["name"] for s in secs for e in s["entries"]]
    print(f"章 {len(chapters)} · 节 {len(secs)} · 方 {total} · 方名去重后 {len(set(named))}")
    for c in chapters:
        print(f"  第{c['book_no']:>2}章 {c['title']:<8} {sum(len(s['entries']) for s in c['sections']):>3} 首")
    dup = {n for n in named if named.count(n) > 1}
    if dup:
        print(f"  ⚠ 同名方出现在多节：{sorted(dup)}")

    if args.check:
        print("--check：未写盘")
        return 0
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
