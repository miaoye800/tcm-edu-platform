"""把教材 PDF 文本切成**结构化药材条目**。

只做定位和切分，**不做任何判断**——判断留给 ``gates.py`` 和人工。

## 为什么用「拼音行」当锚点

教材每个药材条目的开头是固定的三行::

    麻　黄                              ← 药名（中间是全角空格）
    Máhuáng（《神农本草经》）            ← 拼音 + 出处
    本品为麻黄科植物草麻黄…               ← 性状描述

用拼音行当锚点比用 ``【药性】`` 稳：

- ``【药性】`` 有 2 处写成 ``【性味】``（教材自身的笔误），会漏
- 条目跨页时 ``【功效】`` 前面可能夹着页眉，回溯药名容易跑偏
- 拼音行的格式 ``Xxx（《…》）`` 全书统一，443 个条目一个不差

## 字段切分

条目内按 ``【字段名】`` 切块。全书标记分布（实测）::

    【功效】443  【应用】443  【用法用量】443  【现代研究】443
    【药性】441  【使用注意】299  【鉴别用药】76  【其他】27

``【现代研究】`` 不抽——那是药理成分，不是中医知识，且会污染功效词。

用法::

    python -m scripts.textbook.parse --stats     # 只看统计
    python -m scripts.textbook.parse             # 生成 data/curated/textbook_entries.json
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from scripts.textbook.extract_text import DEFAULT_PDF, extract

BACKEND = Path(__file__).resolve().parents[2]
OUT_JSON = BACKEND / "data" / "curated" / "textbook_entries.json"

#: 药名后的拼音行。
#:
#: 两种写法都要认（教材自身不统一）::
#:
#:     Máhuáng（《神农本草经》）        ← 常见
#:     Xuěliánhuā《　本草纲目拾遗》      ← 少数缺左括号
#:     Zǐsūyè（　《名医别录》）          ← 括号和书名号之间夹了全角空格
#:
#: 不能只列声调字母：``Cāng′ěrzǐ`` 里的 ``′``（prime）不在任何字母区段，
#: 早期版本因此漏掉「苍耳子」这类条目。改成「开头不是汉字、也不以左括号起头，
#: 中间出现 ``《``」——正文里不会有这种行，学名行也不会带书名号。
#:
#: 括号和书名号之间的空白必须容忍。``紫苏叶`` 是全书唯一这样排的条目，
#: 早期版本因此**整条丢掉**——它不在 441 条里，图谱里也就没有它的教材数据。
_PINYIN = re.compile(r"^[^\u4e00-\u9fa5（]{1,40}[（]?[\u3000 ]*《")

_CHAPTER = re.compile(r"^第([一二三四五六七八九十]+)章[　 ]+(\S+)$")

_SECTION = re.compile(r"^第([一二三四五六七八九十]+)节[　 ]+(\S+)$")

#: 条目内的字段标记，例如 ``【药性】``。
_FIELD = re.compile(r"【([^】]{2,6})】")

#: 这些字段不抽进知识库。
#: - 现代研究：药理成分与动物实验，不属于中医辨证体系，且会把「抗炎」「抑菌」
#:   这类现代术语混进功效词表，污染闭集白名单
#: - 其他：杂项，内容不稳定
_SKIP_FIELDS = frozenset({"现代研究", "其他"})

#: 要抽的字段（顺序即输出顺序）。
WANTED_FIELDS = ("药性", "性味", "功效", "应用", "用法用量", "使用注意", "鉴别用药")

#: 药名里用来分隔两字的空白。教材写「麻　黄」「铅\t丹」不写「麻黄」。
_NAME_SPACE = re.compile(r"[　 \t]+")

#: 药名行不可能长这样——这些是正文、页眉、标题的典型开头。
_NOT_A_NAME = re.compile(r"^(本|其|如|故|凡|又|若|此|按|见|见附录|【|第.{1,3}[章节])")


@dataclass
class RawEntry:
    """一个未归一的药材条目。所有字段都是**教材原文**，未经改写。"""

    name: str
    pinyin: str
    chapter: str = ""
    chapter_no: str = ""
    section: str = ""
    page: int = 0
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def category_hint(self) -> str:
        """章名就是 ``Category`` 的取值——教材把它编在哪章，它就是哪类。"""

        return self.chapter


def _line_pages(pages: list[str]) -> tuple[str, list[int]]:
    """把分页文本拼成单一全文，并记录每一行属于第几页。"""

    text_parts: list[str] = []
    page_of_line: list[int] = []
    for page_no, page in enumerate(pages):
        for line in page.split("\n"):
            text_parts.append(line)
            page_of_line.append(page_no)
    return "\n".join(text_parts), page_of_line


def _clean_name(line: str) -> str:
    return _NAME_SPACE.sub("", line).strip()


def parse(pages: list[str]) -> list[RawEntry]:
    """按拼音行切条目，返回全部 ``RawEntry``（含重复药名，去重交给上层）。"""

    text, page_of_line = _line_pages(pages)
    lines = text.split("\n")

    # 拼音行的行号
    anchors = [i for i, line in enumerate(lines) if _PINYIN.match(line.strip())]
    if not anchors:
        return []

    # 预先扫出章/节标题的位置，之后每个条目只需往前查最近的标题
    marks: list[tuple[int, str, str]] = []  # (行号, 'chapter'|'section', 名称)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if match := _CHAPTER.match(stripped):
            marks.append((i, "chapter", f"第{match.group(1)}章|{match.group(2)}"))
        elif match := _SECTION.match(stripped):
            marks.append((i, "section", match.group(2)))

    def context_at(index: int) -> tuple[str, str, str]:
        """返回 ``(章名, 节名, 章序号)``。章序号用于生成 sources.ref。"""

        chapter = section = chapter_no = ""
        for line_no, kind, name in marks:
            if line_no >= index:
                break
            if kind == "chapter":
                # marks 里章项存成「第X章|章名」，拆开用
                chapter_no, chapter = name.split("|", 1)
                section = ""  # 新章开始，节要重置
            else:
                section = name
        return chapter, section, chapter_no

    entries: list[RawEntry] = []
    for order, anchor in enumerate(anchors):
        # 药名在拼音行的上一行（可能隔一个空行）
        name = ""
        for back in range(anchor - 1, max(anchor - 5, -1), -1):
            candidate = lines[back].strip()
            if not candidate:
                continue
            if _NOT_A_NAME.match(candidate):
                break
            if _PINYIN.match(candidate) or _FIELD.search(candidate):
                break
            name = _clean_name(candidate)
            break
        if not name:
            continue

        # 条目正文 = 本拼音行 → 下一个拼音行
        end = anchors[order + 1] if order + 1 < len(anchors) else len(lines)
        body = "\n".join(lines[anchor:end])

        chapter, section, chapter_no = context_at(anchor)
        fields = _split_fields(body)
        # 目录页也按「药名 / 拼音行」排版，和正文一模一样（前 60 多页的
        # 详细目录里每个条目名下面都跟着拼音行）。按页码切太脆——换一版
        # PDF 页码就变了。这里用**内容**区分：正文条目一定有【功效】，
        # 目录条目只有一个名字。
        if not any(key in fields for key in ("功效", "应用", "药性", "性味")):
            continue
        entries.append(
            RawEntry(
                name=name,
                pinyin=lines[anchor].strip(),
                chapter=chapter,
                chapter_no=chapter_no,
                section=section,
                page=page_of_line[anchor],
                fields=fields,
            )
        )
    return entries


def _split_fields(body: str) -> dict[str, str]:
    """按 ``【字段名】`` 把条目正文切成字段块。"""

    marks = list(_FIELD.finditer(body))
    out: dict[str, str] = {}
    for index, match in enumerate(marks):
        label = match.group(1)
        if label in _SKIP_FIELDS:
            continue
        start = match.end()
        end = marks[index + 1].start() if index + 1 < len(marks) else len(body)
        value = _normalize_ws(body[start:end])
        if not value:
            continue
        # 同一字段出现两次时保留第一次（教材个别条目有重复标记）
        out.setdefault(label, value)
    return out


def _normalize_ws(value: str) -> str:
    """压掉 PDF 断行造成的空白，但**保留全角空格**。

    全角空格 ``\u3000`` 在教材里是**语义分隔符**，不是排版产物::

        1.风寒感冒　本品味辛发散…     ← 证名 ｜ 说明
        2.胸闷喘咳　本品辛散苦泄…

    压掉它，「风寒感冒」和后面的说明就粘成一串，主治条目再也切不开。

    只压：普通空格、制表符（PDF 里大量用于对齐）、换行。
    同时**不改任何标点**——G008 要求 quote 是原文的字面子串，
    把全角标点转成半角，子串校验就会失败。
    """

    value = re.sub(r"[ \t]+", "", value)
    value = re.sub(r"\n+", "", value)
    return value.strip()


def dedupe(entries: list[RawEntry]) -> tuple[list[RawEntry], list[tuple[str, int]]]:
    """同名条目只留字段最全的一个。

    教材里同一味药可能出现两次（如「附子」在温里药和附药各有一条），
    也可能因为目录页被误抓。返回 ``(保留的条目, [(被丢弃的药名, 条数)])``。
    """

    best: dict[str, RawEntry] = {}
    dropped: dict[str, int] = {}
    for entry in entries:
        current = best.get(entry.name)
        if current is None:
            best[entry.name] = entry
            continue
        dropped[entry.name] = dropped.get(entry.name, 0) + 1
        # 字段更多、正文更长的胜出——通常是正条而不是附药或残片
        if len(entry.fields) > len(current.fields):
            best[entry.name] = entry
    return list(best.values()), sorted(dropped.items())


def load(pages: list[str] | None = None) -> list[RawEntry]:
    """读取缓存并解析，供其他脚本直接调用。"""

    if pages is None:
        pages = extract(DEFAULT_PDF)
    entries, _ = dedupe(parse(pages))
    return entries


def main() -> int:
    parser = argparse.ArgumentParser(description="把教材切成结构化药材条目")
    parser.add_argument("--stats", action="store_true", help="只打印统计，不写文件")
    args = parser.parse_args()

    pages = extract(DEFAULT_PDF)
    raw = parse(pages)
    entries, dropped = dedupe(raw)

    by_field: dict[str, int] = {}
    for entry in entries:
        for key in entry.fields:
            by_field[key] = by_field.get(key, 0) + 1
    by_chapter: dict[str, int] = {}
    for entry in entries:
        by_chapter[entry.chapter or "(无章)"] = by_chapter.get(entry.chapter or "(无章)", 0) + 1

    print(f"拼音行锚点条目: {len(raw)}")
    print(f"去重后: {len(entries)}")
    print(f"字段覆盖: {dict(sorted(by_field.items(), key=lambda kv: -kv[1]))}")
    print(f"章节分布: {dict(sorted(by_chapter.items(), key=lambda kv: -kv[1]))}")
    if dropped:
        print(f"同名丢弃: {len(dropped)} 组 -> {dropped[:10]}")

    if args.stats:
        return 0

    payload = {
        "version": "textbook_entries_v1",
        "source": "《中药学》第十版（钟赣生）",
        "stats": {
            "anchors": len(raw),
            "entries": len(entries),
            "by_field": by_field,
            "by_chapter": by_chapter,
        },
        "entries": [
            {
                "name": e.name,
                "pinyin": e.pinyin,
                "chapter": e.chapter,
                "chapter_no": e.chapter_no,
                "section": e.section,
                "page": e.page,
                "fields": e.fields,
            }
            for e in sorted(entries, key=lambda x: x.page)
        ],
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"json={OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
