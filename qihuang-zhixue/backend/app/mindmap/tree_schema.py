"""整树保存的输入校验。

三条边界分开守，因为失败原因不同、前端要给的提示也不同：

- **输入层宽松**（``TreeInNode`` 用 ``extra="ignore"``）：mind-elixir 的
  ``getData()`` 会带上 ``style`` / ``expanded`` / ``branchColor`` 等排版字段，
  按 ``extra="forbid"`` 校验会直接 422，自动保存第一次就崩。
- **语义层严格**（节点数、深度、id 唯一性、根 id）：这些坏了会让整棵树不可
  增量渲染，必须拒绝而不是静默修正。
- **文字层截断**：与生成侧同一套 ``clean_topic``，保证「生成出来的」和
  「用户手改后的」走同一条归一化规则。
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .draft import MAX_TOPIC, clean_topic, collapse_topic
from .schemas import Evidence, MindElixirData, MindmapMeta, MindmapNode

MAX_TREE_NODES = 240
MAX_TREE_DEPTH = 6
MAX_TREE_CHILDREN = 12
ROOT_ID = "root"
ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
#: 只认这三档，其余一律当缺失——存储层不接受自造的第四种「依据」。
EVIDENCE_VALUES = ("textbook", "graph", "model")


class TreeInNode(BaseModel):
    """mind-elixir 的 nodeData 子集。多余字段一律忽略。"""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(default="", max_length=80)
    topic: str = Field(default="", max_length=400)
    direction: int | None = None
    #: 依据档位。前端 ``toSaveTree`` 是把它放在节点顶层而不是 meta 里的，
    #: 这里必须一起收下——否则每自动保存一次，「教材实证」就退化成 None，
    #: 「推断」角标消失、底栏三档只剩兜底值（实测踩过）。
    evidence: str | None = None
    meta: dict[str, Any] | None = None
    children: list["TreeInNode"] = Field(default_factory=list, max_length=64)


class SaveMindmapTreeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    nodeData: TreeInNode
    template_id: str | None = None


class SaveMindmapTreeResponse(BaseModel):
    version: int
    node_count: int
    depth: int
    template_id: str | None = None


def validate_tree(root: TreeInNode, *, source_count: int = 0) -> MindmapNode:
    """把输入树转成受校验的 ``MindmapNode``，并守住节点数/深度/id 三条线。"""

    if (root.id or "") != ROOT_ID:
        raise ValueError("根节点 id 必须为 root")

    seen: set[str] = set()
    node = _convert(root, depth=0, seen=seen, counter=[0], source_count=source_count)
    if len(seen) > MAX_TREE_NODES:
        raise ValueError(f"导图节点数不能超过 {MAX_TREE_NODES} 个")
    return node


def _convert(
    raw: TreeInNode,
    *,
    depth: int,
    seen: set[str],
    counter: list[int],
    source_count: int,
) -> MindmapNode:
    if depth >= MAX_TREE_DEPTH:
        raise ValueError(f"导图层级不能超过 {MAX_TREE_DEPTH} 层")

    node_id = (raw.id or "").strip()
    if not node_id:
        raise ValueError("存在缺少 id 的节点，请刷新后重试")
    if not ID_PATTERN.match(node_id):
        raise ValueError(f"节点 id 含非法字符：{node_id[:20]}")
    if node_id in seen:
        raise ValueError(f"节点 id 重复：{node_id[:20]}")
    seen.add(node_id)

    counter[0] += 1
    if counter[0] > MAX_TREE_NODES:
        raise ValueError(f"导图节点数不能超过 {MAX_TREE_NODES} 个")

    topic = clean_topic(raw.topic)
    if not topic:
        raise ValueError("存在空白节点，请填写内容或删除该节点")
    collapsed = collapse_topic(raw.topic)
    if len(collapsed) > MAX_TOPIC:
        raise ValueError(f"节点文字最多 {MAX_TOPIC} 字：{collapsed[:12]}…")

    if len(raw.children) > MAX_TREE_CHILDREN:
        raise ValueError(f"单个节点最多 {MAX_TREE_CHILDREN} 个子节点")

    children = [
        _convert(child, depth=depth + 1, seen=seen, counter=counter, source_count=source_count)
        for child in raw.children
    ]
    return MindmapNode(
        id=node_id,
        topic=topic,
        direction=_direction(raw.direction),
        meta=MindmapMeta(
            source_indexes=_source_indexes(raw.meta, source_count),
            evidence=_evidence(raw.evidence, raw.meta),
        ),
        children=children,
    )


def _evidence(declared: str | None, meta: dict[str, Any] | None) -> Evidence | None:
    """依据档位：节点顶层优先，其次 meta 里，认不出来就留 None（= 老数据）。

    不按「有没有引用依据」硬判：用户手改过的节点仍带着原来的档位，
    那是它内容的出身，不该因为改了几个字就被重判成推断。
    """

    for value in (declared, (meta or {}).get("evidence") if isinstance(meta, dict) else None):
        if isinstance(value, str) and value in EVIDENCE_VALUES:
            return value  # type: ignore[return-value]
    return None


def _direction(value: int | None) -> int:
    # mind-elixir 的 direction 只有 0/1；前端可能传 undefined 或其它数字。
    return 1 if value == 1 else 0


def _source_indexes(meta: dict[str, Any] | None, source_count: int) -> list[int]:
    if not isinstance(meta, dict):
        return []
    values = meta.get("source_indexes")
    if not isinstance(values, list) or source_count <= 0:
        return []
    kept: set[int] = set()
    for value in values:
        try:
            index = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= index < source_count:
            kept.add(index)
    return sorted(kept)[:3]


def tree_stats(node: MindmapNode) -> tuple[int, int]:
    """返回 (节点数, 层数)。层数按「根算第一层」计。"""

    count = 0
    deepest = 0
    stack: list[tuple[MindmapNode, int]] = [(node, 1)]
    while stack:
        current, level = stack.pop()
        count += 1
        deepest = max(deepest, level)
        stack.extend((child, level + 1) for child in current.children)
    return count, deepest


def apply_tree(content: dict[str, Any], node: MindmapNode, template_id: str | None = None) -> dict[str, Any]:
    """把校验后的树写回 content，保留 sources / choice_reason 等不可变凭据。"""

    merged = dict(content)
    merged["mindmap_data"] = MindElixirData(nodeData=node).model_dump(mode="json")
    if template_id:
        merged["template_id"] = template_id
    return merged
