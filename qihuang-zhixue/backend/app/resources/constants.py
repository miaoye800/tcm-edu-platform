"""The alchemy performance contract shared by resource generators and the UI."""

from __future__ import annotations

from typing import Final, Literal, TypedDict

ResourceType = Literal["doc", "graph", "mindmap", "quiz", "story"]
RESOURCE_TYPES: Final[tuple[ResourceType, ...]] = ("doc", "graph", "mindmap", "quiz", "story")


class StepSpec(TypedDict):
    id: str
    label: str
    variants: tuple[str, ...]


# Keep all stage ids and presentation copy here.  The ids are persisted in
# resource_task; copy may evolve without invalidating an in-flight task.
ALCHEMY_STEPS: Final[dict[ResourceType, tuple[StepSpec, ...]]] = {
    "doc": (
        {"id": "source", "label": "翻检本草", "variants": ("翻检《本草》原文...", "启开药箧核对古籍...", "循着药性查验本源...")},
        {"id": "relate", "label": "梳理药证", "variants": ("梳理药证关联...", "调和功效与主治...", "归拢辨证脉络...")},
        {"id": "distill", "label": "淬炼考点", "variants": ("淬炼核心考点...", "收束易混之处...", "提炼学习关窍...")},
        {"id": "verify", "label": "校验溯源", "variants": ("校验溯源引文...", "逐条核对药典依据...", "封存可追溯证据...")},
        {"id": "layout", "label": "排版成卷", "variants": ("排版成卷...", "将药材装订成册...", "落墨为学习讲义...")},
    ),
    "quiz": (
        {"id": "scope", "label": "圈定考点", "variants": ("圈定考点范围...", "择定应试药证...", "标出易错穴位...")},
        {"id": "source", "label": "翻检题源", "variants": ("翻检历年真题...", "比照经典问法...", "搜罗辨析材料...")},
        {"id": "draft", "label": "拟题设错", "variants": ("拟题与设错...", "布置易混干扰项...", "炼制辨析题眼...")},
        {"id": "verify", "label": "核对答案", "variants": ("核对答案溯源...", "回验每道题据...", "校准题目火候...")},
    ),
    "graph": (
        {"id": "collect", "label": "采撷实体", "variants": ("采撷药证实体...", "拣选关联药材...", "收集方证线索...")},
        {"id": "weave", "label": "织连关系", "variants": ("织连功效关系...", "牵引方药脉络...", "结成药证经纬...")},
        {"id": "temper", "label": "淬炼图谱", "variants": ("淬炼知识图谱...", "调校节点主次...", "熔合关联脉络...")},
        {"id": "verify", "label": "校验图据", "variants": ("校验图谱依据...", "核对每条连线...", "封存图谱引证...")},
    ),
    "mindmap": (
        {"id": "root", "label": "立起主枝", "variants": ("立起导图主枝...", "安放知识根节点...", "定下药性主脉...")},
        {"id": "branch", "label": "分梳支脉", "variants": ("分梳功效支脉...", "展开主治枝叶...", "理顺配伍分支...")},
        {"id": "temper", "label": "凝练导图", "variants": ("凝练思维导图...", "收束层级结构...", "点化记忆线索...")},
        {"id": "verify", "label": "校验枝据", "variants": ("校验导图依据...", "逐枝回查出处...", "封存可溯节点...")},
    ),
    "story": (
        {"id": "collect", "label": "采集医案", "variants": ("采集医案线索...", "翻检延伸文选...", "拾取临证材料...")},
        {"id": "script", "label": "编排药语", "variants": ("编排药语小剧...", "铺陈临证情境...", "调配故事药引...")},
        {"id": "temper", "label": "炼成短剧", "variants": ("炼成拟人短剧...", "收束为临证片段...", "点化药性角色...")},
        {"id": "verify", "label": "校验医据", "variants": ("校验医案出处...", "回查故事依据...", "封存引用药笺...")},
    ),
}
