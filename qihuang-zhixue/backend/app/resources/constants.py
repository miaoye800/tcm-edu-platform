"""The alchemy performance contract shared by resource generators and the UI."""

from __future__ import annotations

from typing import Final, Literal, TypedDict

ResourceType = Literal["doc", "graph", "mindmap", "quiz", "story", "ppt"]
RESOURCE_TYPES: Final[tuple[ResourceType, ...]] = ("doc", "graph", "mindmap", "quiz", "story", "ppt")


class StepSpec(TypedDict):
    id: str
    label: str
    variants: tuple[str, ...]


# Keep all stage ids and presentation copy here.  The ids are persisted in
# resource_task; copy may evolve without invalidating an in-flight task.
ALCHEMY_STEPS: Final[dict[ResourceType, tuple[StepSpec, ...]]] = {
    "doc": (
        # 文稿不一定是讲药材：用户要计划、对比、指南时同样走这五步。工序名因此
        # 按「写一篇文档」的实际动作取，而不是按「讲一味药」取。
        {"id": "source", "label": "审题定调", "variants": ("辨明这份文稿要解决什么...", "看清读者要的是哪一种交付...", "先把题目掂量清楚...")},
        {"id": "relate", "label": "谋篇立序", "variants": ("搭起章节骨架...", "排定先说什么后说什么...", "为这篇文稿立骨...")},
        {"id": "distill", "label": "落笔成稿", "variants": ("落笔成稿...", "逐节展开细写...", "把要点写成可读的段落...")},
        {"id": "verify", "label": "核对引文", "variants": ("核对引文出处...", "逐条回查教材依据...", "封存可追溯证据...")},
        {"id": "layout", "label": "排版成卷", "variants": ("排版成卷...", "定为可读的版式...", "落墨为一份文档...")},
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
    # 幻灯的四道工序：先立骨架，再定页序，然后逐页敷演，最后校色收束。
    # outline 这一步在「先定大纲」模式下会暂停等用户确认，id 一旦落库不可再改。
    "ppt": (
        {"id": "frame", "label": "立骨", "variants": ("立起讲稿骨架...", "铺定讲授脉络...", "先理清这一课要说什么...")},
        {"id": "outline", "label": "谋篇", "variants": ("谋篇定页序...", "排布章节先后...", "勾出每页的要点骨架...")},
        {"id": "compose", "label": "敷演", "variants": ("敷演成页...", "把药性写进每一页...", "铺陈讲义图文...")},
        {"id": "polish", "label": "校色", "variants": ("校色收束...", "调停版式与用词...", "定稿封页...")},
    ),
}


#: 导图 v2 的九道工序。
#:
#: 与 v1 的四步表并存，**不改 v1 一个字**：``current_step`` 是索引语义，
#: ``service.stream_task`` 拿它切片续跑，改表就等于让所有在途任务错位。
#: 新任务在 meta 里写 ``pipeline_version=2``，由 ``_steps_for`` 分流；
#: 老任务读不到这个键，继续走四步表，产物逐字段不变。
#:
#: 九步不是把四步拆细凑数，每一步都对应一件真事：
#:   0 辨明主题：问法判定 + 实体锚定（八类问法）
#:   1 采撷素材：按问法取素材包（分类子树 / 功效倒排 / 关系边…）
#:   2 谋篇立骨：定一级分支（分类题=教材亚类名，鉴别题=对比维度）
#:   3 敷演枝叶：模型在骨架上填叶子
#:   4 收束形制：分配 id、截断、去重、配额
#:   5 校验枝据：越界索引裁剪 + 三档依据统计
#:   6 落墨成像：按层序推 node 事件
#:   7 封存入库：落库并写 meta
#:   8 开卷可改：回填 artifact / open_url
ALCHEMY_STEPS_V2: Final[dict[str, tuple[StepSpec, ...]]] = {
    "mindmap": (
        {"id": "resolve", "label": "辨明主题", "variants": ("辨明主题与问法...", "看清这道题问的是什么...", "先分清是分类还是鉴别...")},
        {"id": "gather", "label": "采撷素材", "variants": ("采撷药材与图谱素材...", "翻检教材对应条目...", "把相干的本草取来...")},
        {"id": "plan", "label": "谋篇立骨", "variants": ("谋篇立骨...", "先定一级分支...", "搭起这张图的骨架...")},
        {"id": "compose", "label": "敷演枝叶", "variants": ("敷演枝叶...", "把要点写进每个分支...", "为骨架添上细目...")},
        {"id": "normalize", "label": "收束形制", "variants": ("收束形制...", "理顺层级与字数...", "把长句压成标签...")},
        {"id": "check", "label": "校验枝据", "variants": ("校验枝据...", "逐枝回查出处...", "标出哪些是推断...")},
        {"id": "render", "label": "落墨成像", "variants": ("落墨成像...", "一笔一笔画出枝节...", "让图自己长出来...")},
        {"id": "persist", "label": "封存入库", "variants": ("封存入库...", "把这张图收进书箧...", "记下这次的版本...")},
        {"id": "handoff", "label": "开卷可改", "variants": ("开卷可改...", "图纸已可增删...", "收笔，交给你改...")},
    ),
}


def steps_for(resource_type: str, pipeline_version: int = 1) -> tuple[StepSpec, ...]:
    """按管线版本取工序表。只有导图有 v2，其余一律 v1。"""

    if resource_type == "mindmap" and pipeline_version >= 2:
        return ALCHEMY_STEPS_V2["mindmap"]
    return ALCHEMY_STEPS[resource_type]  # type: ignore[index]
