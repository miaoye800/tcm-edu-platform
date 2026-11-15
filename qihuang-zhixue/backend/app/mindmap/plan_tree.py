"""把「用户确认过的分支」变成导图骨架。

方案里的每个单元就是一条一级分支。这里只做三件事：

1. **分支以方案为准**：教材原型只用来给同名分支配上已有素材，不能反过来
   把用户删掉的分支再长回来。
2. **依据按分支判**：命中原型/分组的分支继承教材或图谱依据；教材没有覆盖的
   分支明确标 ``model``，不因为「查不到」就整枝删掉。
3. **结构可回退**：方案为空或全部不可用时返回 ``None``，调用方回到原型骨架，
   不让一张图因为方案字段坏了而生成失败。
"""

from __future__ import annotations

from typing import Any

from .archetypes import MAX_LEAVES, build_archetype_tree
from .evidence import EVIDENCE_MODEL, EVIDENCE_TEXTBOOK, EvidencePack
from .schemas import MindElixirData, MindmapMeta, MindmapNode

#: 节点标题的硬上限（``MindmapNode.topic`` 校验）。
TOPIC_LIMIT = 30
#: 一级分支上限，与 ``archetypes.MAX_BRANCHES`` 对齐。
MAX_BRANCHES = 8


def skeleton_from_plan(pack: EvidencePack, units: list[dict[str, Any]]) -> MindElixirData | None:
    """按方案分支重建一级骨架；没有可用分支时返回 ``None``。"""

    labels = [_clip(str(unit.get("label") or "")) for unit in units]
    labels = [label for label in labels if label][:MAX_BRANCHES]
    if not labels:
        return None

    base = build_archetype_tree(pack)
    by_label = {branch.topic: branch for branch in base.nodeData.children}
    fallback_list = list(base.nodeData.children)

    branches: list[MindmapNode] = []
    used: set[str] = set()
    for index, label in enumerate(labels):
        matched = _match(label, by_label, used)
        if matched is not None:
            used.add(matched.topic)
            branches.append(matched.model_copy(update={"id": f"b{index}", "topic": label, "direction": index % 2}))
            continue
        group = _group_children(pack, label)
        if group:
            branches.append(
                MindmapNode(
                    id=f"b{index}",
                    topic=label,
                    direction=index % 2,
                    meta=MindmapMeta(source_indexes=[0], evidence=EVIDENCE_TEXTBOOK),
                    children=[
                        MindmapNode(id=f"b{index}-{child_index}", topic=_clip(name), meta=MindmapMeta(source_indexes=[0], evidence=EVIDENCE_TEXTBOOK))
                        for child_index, name in enumerate(group[:MAX_LEAVES])
                    ],
                )
            )
            continue
        # 教材没有覆盖这条分支：保留分支本身，标成模型补充，内容交给敷演步。
        branches.append(MindmapNode(id=f"b{index}", topic=label, direction=index % 2, meta=MindmapMeta(evidence=EVIDENCE_MODEL)))

    if not branches:
        return None
    return MindElixirData(nodeData=base.nodeData.model_copy(update={"children": branches}))


def _match(label: str, by_label: dict[str, MindmapNode], used: set[str]) -> MindmapNode | None:
    exact = by_label.get(label)
    if exact is not None and exact.topic not in used:
        return exact
    for topic, branch in by_label.items():
        if topic in used:
            continue
        if len(topic) >= 2 and (topic in label or label in topic):
            return branch
    return None


def _group_children(pack: EvidencePack, label: str) -> list[str]:
    """分组型素材（功效倒排 / 分类子树）里，按分支名取回子项。"""

    names = pack.groups.get(label)
    if names:
        return list(names)
    for key, value in pack.groups.items():
        if len(key) >= 2 and (key in label or label in key):
            return list(value)
    return []


def _clip(text: str) -> str:
    return " ".join(text.split())[:TOPIC_LIMIT]


def plan_branch_labels(units: list[dict[str, Any]]) -> list[str]:
    """给敷演提示词用的分支名。顺序即方案顺序。"""

    return [label for label in (_clip(str(unit.get("label") or "")) for unit in units) if label][:MAX_BRANCHES]
