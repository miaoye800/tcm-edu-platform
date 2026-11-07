"""文档块的落库校验。

前端可编辑意味着后端不能再把 `content.blocks` 当作「只由自己写入的可信 JSON」。
这里做四件事：类型白名单、文本长度上限、嵌套深度上限、总块数上限。
不校验业务语义（比如「表格必须两列」）——那属于渲染层的自由，卡死只会让用户编不出来。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DocBlockType = Literal[
    "paragraph", "heading1", "heading2", "heading3",
    "bulleted", "numbered", "todo", "toggle",
    "quote", "callout", "divider", "code",
    "table", "image", "columns", "page",
]

MAX_BLOCKS = 500
MAX_DEPTH = 3
MAX_TEXT = 4000
MAX_TOTAL_TEXT = 200_000


class DocBlockIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    type: DocBlockType
    text: str = ""
    props: dict[str, Any] = Field(default_factory=dict)
    children: list["DocBlockIn"] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)

    @field_validator("text")
    @classmethod
    def _limit_text(cls, value: str) -> str:
        if len(value) > MAX_TEXT:
            raise ValueError(f"单块文本超过 {MAX_TEXT} 字上限")
        return value


DocBlockIn.model_rebuild()


class SaveDocumentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blocks: list[DocBlockIn]
    version: int = Field(ge=0)
    meta: dict[str, Any] = Field(default_factory=dict)


class SaveDocumentResponse(BaseModel):
    version: int
    block_count: int


def _walk_depth(blocks: list[DocBlockIn], depth: int = 1) -> None:
    if depth > MAX_DEPTH:
        raise ValueError(f"块嵌套超过 {MAX_DEPTH} 层")
    for block in blocks:
        if block.children:
            _walk_depth(block.children, depth + 1)


def count_blocks(blocks: list[DocBlockIn]) -> int:
    return sum(1 + count_blocks(block.children) for block in blocks)


def validate_blocks(blocks: list[DocBlockIn]) -> list[dict[str, Any]]:
    """校验并返回可直接落库的纯 JSON 结构。"""
    if not blocks:
        raise ValueError("文档不能为空")
    total = count_blocks(blocks)
    if total > MAX_BLOCKS:
        raise ValueError(f"文档块数 {total} 超过 {MAX_BLOCKS} 上限")
    _walk_depth(blocks)
    if sum(len(block.text) for block in _flatten(blocks)) > MAX_TOTAL_TEXT:
        raise ValueError("文档总字数超过上限")
    return [block.model_dump(mode="json") for block in blocks]


def _flatten(blocks: list[DocBlockIn]) -> list[DocBlockIn]:
    out: list[DocBlockIn] = []
    for block in blocks:
        out.append(block)
        if block.children:
            out.extend(_flatten(block.children))
    return out


#: 允许前端写回的 meta 键。其余键（generator / params / doc_version）属于服务端，
#: 放开就等于让客户端改自己的溯源信息。
EDITABLE_META_KEYS = {"icon", "cover", "fullWidth"}


def sanitize_meta(patch: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key in EDITABLE_META_KEYS:
        if key in patch:
            value = patch[key]
            if isinstance(value, str):
                clean[key] = value[:200]
            elif isinstance(value, bool):
                clean[key] = value
    return clean
