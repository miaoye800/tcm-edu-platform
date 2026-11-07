"""Markdown → Notion 风格块。

模型写 Markdown 远比写嵌套 JSON 稳定：Markdown 是它的母语。因此生成侧只约束
「输出 Markdown」，块结构在这里确定性还原——同一份文稿在页面上、在导出里、
在粘贴导入时长得一样。

与 ``app/resources/doc_markdown.py``（块 → Markdown）互为逆运算：
``blocks_to_markdown(markdown_to_blocks(x))`` 应保持结构不丢（标题、列表、
表格、callout、toggle）。

支持的块级语法：

===========  ============================================================
Markdown     块
===========  ============================================================
``# ~ ###``  heading1 / heading2 / heading3
段落          paragraph（连续行合并，中文不加空格）
``- / * +``  bulleted；带子项时升为 toggle（折叠块）
``1.``       numbered；带子项时同样升为 toggle
``- [ ]``    todo（``props.checked``）
``| a | b |`` table（``props.columns/rows``，同时保留管道表文本）
``> 引用``    quote；首行是 ``[!TIP]`` 类标记或 emoji 时升为 callout
``---``      divider
``````````   code（``props.lang``）
===========  ============================================================
"""

from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE = re.compile(r"^```([\w+#.-]*)\s*$")
_HR = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_TODO_MARK = re.compile(r"^\[([ xX])\]\s*(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_CALLOUT_TAG = re.compile(r"^\[!(NOTE|TIP|WARNING|IMPORTANT|CAUTION)\]\s*", re.IGNORECASE)

#: emoji → callout 语气。与前端 ``markdownToBlocks.ts`` 的 ICON_TONE 同一套规则。
_ICON_TONE: dict[str, str] = {
    "⚠": "warn", "❗": "warn", "‼": "warn", "🚨": "warn", "⛔": "warn",
    "✦": "tip", "✨": "tip", "💡": "tip", "🔥": "tip", "✅": "tip",
    "📌": "info", "🧠": "info", "🚪": "info", "📖": "info", "🧪": "info",
}
_TAG_TONE = {"NOTE": "info", "IMPORTANT": "info", "TIP": "tip", "WARNING": "warn", "CAUTION": "warn"}
_TAG_ICON = {"info": "💡", "tip": "✦", "warn": "⚠"}

_EMOJI_RANGES = (
    (0x1F300, 0x1FAFF), (0x1F000, 0x1F0FF), (0x2600, 0x27BF),
    (0x2B00, 0x2BFF), (0x2190, 0x21FF), (0xFE00, 0xFE0F), (0x200D, 0x200D),
)

_PREFIX = {
    "heading1": "h1", "heading2": "h2", "heading3": "h3", "paragraph": "p",
    "bulleted": "l", "numbered": "l", "todo": "t", "toggle": "g",
    "quote": "q", "callout": "c", "divider": "d", "code": "code", "table": "tb",
}


def _block(kind: str, text: str, props: dict[str, Any] | None = None,
           children: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": f"blk_{_PREFIX.get(kind, 'x')}_{uuid4().hex[:8]}",
        "type": kind,
        "text": text,
        "props": props or {},
    }
    if children:
        out["children"] = children
    return out


def _is_emoji(char: str) -> bool:
    code = ord(char)
    return any(low <= code <= high for low, high in _EMOJI_RANGES)


def extract_leading_emoji(text: str) -> tuple[str, str]:
    """把开头的 emoji（含变体选择符/ZWJ 序列）切出来。

    ``⚠️`` 是两个码点（U+26A0 + U+FE0F），只取一个会把变体符留进正文。
    """

    stripped = text.lstrip()
    icon = ""
    index = 0
    while index < len(stripped) and _is_emoji(stripped[index]):
        icon += stripped[index]
        index += 1
    return icon, stripped[index:].strip()


def _join_lines(parts: list[str]) -> str:
    """合并段落内的软换行：中文之间不加空格，西文之间保留一个空格。"""

    text = ""
    for part in parts:
        piece = part.strip()
        if not piece:
            continue
        if not text:
            text = piece
            continue
        if text[-1].isascii() and text[-1].isalnum() and piece[0].isascii() and piece[0].isalnum():
            text += " " + piece
        else:
            text += piece
    return text


def _split_row(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [cell.strip() for cell in body.split("|")]


def _starts_block(line: str, nxt: str | None = None) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if _FENCE.match(stripped) or _HR.match(line) or _HEADING.match(stripped):
        return True
    if stripped.startswith(">"):
        return True
    if _LIST_ITEM.match(line):
        return True
    if stripped.startswith("|") and nxt is not None and _TABLE_SEP.match(nxt) and "-" in nxt:
        return True
    return False


def _quote_blocks(quoted: list[str]) -> list[dict[str, Any]]:
    if not quoted:
        return []
    head = quoted[0]
    tag = _CALLOUT_TAG.match(head)
    icon, rest = extract_leading_emoji(head)
    if tag:
        tone = _TAG_TONE.get(tag.group(1).upper(), "info")
        body = _CALLOUT_TAG.sub("", head).strip()
        icon2, rest2 = extract_leading_emoji(body)
        text = _join_lines([rest2 or body, *quoted[1:]])
        return [_block("callout", text, {"tone": tone, "icon": icon2 or _TAG_ICON[tone]})]
    if icon:
        # ⚠️ 是「⚠ + 变体选择符」，查表要退到基字符，否则认不出 -> 退化成人引用块。
        tone = _ICON_TONE.get(icon) or _ICON_TONE.get(icon[0])
        if tone:
            text = _join_lines([rest, *quoted[1:]])
            return [_block("callout", text, {"tone": tone, "icon": icon[0]})]
    return [_block("quote", "\n".join(quoted))]


class _Item:
    __slots__ = ("indent", "ordered", "text")

    def __init__(self, indent: int, ordered: bool, text: str) -> None:
        self.indent = indent
        self.ordered = ordered
        self.text = text


def _tree(items: list[_Item]) -> list[dict[str, Any]]:
    root: list[dict[str, Any]] = []
    stack: list[tuple[int, list[dict[str, Any]]]] = [(-1, root)]
    for item in items:
        while len(stack) > 1 and stack[-1][0] >= item.indent:
            stack.pop()
        node = {"text": item.text, "ordered": item.ordered, "children": []}
        stack[-1][1].append(node)
        stack.append((item.indent, node["children"]))
    return root


def _render_items(nodes: list[dict[str, Any]], depth: int = 1) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for node in nodes:
        text = node["text"].strip()
        children = node["children"]
        if children and depth < 3:
            out.append(_block("toggle", text, {"collapsed": True}, _render_items(children, depth + 1)))
            continue
        todo = _TODO_MARK.match(text)
        if todo:
            out.append(_block("todo", todo.group(2).strip(), {"checked": todo.group(1).lower() == "x"}))
        else:
            out.append(_block("numbered" if node["ordered"] else "bulleted", text))
    return out


def markdown_to_blocks(markdown: str) -> list[dict[str, Any]]:
    """把 Markdown 正文解析成块。空输入返回空列表。"""

    lines = (markdown or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[dict[str, Any]] = []
    index = 0
    total = len(lines)

    while index < total:
        line = lines[index]
        stripped = line.strip()

        if not stripped:
            index += 1
            continue

        fence = _FENCE.match(stripped)
        if fence:
            index += 1
            body: list[str] = []
            while index < total and not _FENCE.match(lines[index].strip()):
                body.append(lines[index])
                index += 1
            index += 1  # 吃掉收尾围栏（缺失时也安全）
            blocks.append(_block("code", "\n".join(body), {"lang": fence.group(1)}))
            continue

        if _HR.match(line):
            blocks.append(_block("divider", ""))
            index += 1
            continue

        heading = _HEADING.match(stripped)
        if heading:
            level = min(3, len(heading.group(1)))
            text = heading.group(2).strip().rstrip("#").strip()
            if text:
                blocks.append(_block(f"heading{level}", text))
            index += 1
            continue

        if stripped.startswith("|") and index + 1 < total and _TABLE_SEP.match(lines[index + 1]) and "-" in lines[index + 1]:
            columns = _split_row(lines[index])
            index += 2
            rows: list[list[str]] = []
            while index < total and lines[index].strip().startswith("|"):
                rows.append(_split_row(lines[index]))
                index += 1
            width = len(columns)
            rows = [row + [""] * (width - len(row)) if len(row) < width else row[:width] for row in rows]
            body_lines = ["| " + " | ".join(columns) + " |",
                          "| " + " | ".join("---" for _ in columns) + " |"]
            body_lines += ["| " + " | ".join(row) + " |" for row in rows]
            blocks.append(_block("table", "\n".join(body_lines),
                                 {"columns": columns, "rows": rows, "header": True}))
            continue

        if stripped.startswith(">"):
            quoted: list[str] = []
            while index < total and lines[index].strip().startswith(">"):
                quoted.append(re.sub(r"^\s*>\s?", "", lines[index]).rstrip())
                index += 1
            blocks.extend(_quote_blocks(quoted))
            continue

        if _LIST_ITEM.match(line):
            items: list[_Item] = []
            while index < total:
                match = _LIST_ITEM.match(lines[index])
                if not match:
                    break
                indent = len(match.group(1).replace("\t", "  "))
                items.append(_Item(indent, match.group(2)[0].isdigit(), match.group(3).strip()))
                index += 1
            blocks.extend(_render_items(_tree(items)))
            continue

        # 段落：吃到空行或下一个块起点
        paragraph: list[str] = []
        while index < total:
            nxt = lines[index + 1] if index + 1 < total else None
            if paragraph and _starts_block(lines[index], nxt):
                break
            if not paragraph and _starts_block(lines[index], nxt):
                break
            paragraph.append(lines[index])
            index += 1
        text = _join_lines(paragraph)
        if text:
            blocks.append(_block("paragraph", text))

    return blocks
