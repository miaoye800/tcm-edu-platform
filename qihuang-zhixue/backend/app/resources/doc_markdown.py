"""块结构 → Markdown。

与前端 `utils/docBlocks.ts: toMarkdown` 保持同一套规则，用于导出与往返校验。
"""

from __future__ import annotations

from typing import Any


def _table(columns: list[str], rows: list[list[str]]) -> str:
    head = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    body = ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join([head, sep, *body])


def _block(block: dict[str, Any], depth: int) -> list[str]:
    kind = str(block.get("type") or "paragraph")
    text = str(block.get("text") or "")
    props = block.get("props") or {}
    pad = "  " * depth
    children = block.get("children") or []

    if kind in {"heading1", "heading2", "heading3"}:
        level = {"heading1": 1, "heading2": 2, "heading3": 3}[kind]
        return [f"{'#' * level} {text}"]
    if kind == "paragraph":
        return [text]
    if kind == "bulleted":
        return [f"{pad}- {text}"]
    if kind == "numbered":
        return [f"{pad}1. {text}"]
    if kind == "todo":
        mark = "x" if props.get("checked") else " "
        return [f"{pad}- [{mark}] {text}"]
    if kind == "toggle":
        out = [f"{pad}- {text}"]
        for child in children:
            out.extend(_block(child, depth + 1))
        return out
    if kind == "quote":
        return ["> " + text.replace("\n", "\n> ")]
    if kind == "callout":
        icon = str(props.get("icon") or "")
        return ["> " + (f"{icon} " if icon else "") + text]
    if kind == "divider":
        return ["---"]
    if kind == "code":
        return [f"```{props.get('lang') or ''}\n{text}\n```"]
    if kind == "table":
        columns = props.get("columns") or []
        rows = props.get("rows") or []
        return [_table(columns, rows)] if columns else [text]
    if kind == "image":
        return [f"![{props.get('alt') or ''}]({props.get('assetId') or ''})"]
    if kind == "columns":
        out: list[str] = []
        for child in children:
            out.extend(_block(child, depth))
        return out
    if kind == "page":
        return [f"{pad}- 📄 {text}"]
    return [text]


def blocks_to_markdown(blocks: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for block in blocks:
        lines.extend(line for line in _block(block, 0) if line != "")
    return "\n\n".join(lines) + "\n"
