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
    "ppt": (
        {"id": "plan", "label": "拟定方案", "variants": ("先看清这次要做什么...", "为这份幻灯拟定方案...", "整理主题、受众与讲授脉络...")},
        {"id": "compose", "label": "逐页成稿", "variants": ("按确认方案逐页敷演...", "让每一页回应你的目标...", "把讲授脉络落成幻灯...")},
        {"id": "verify", "label": "校验依据", "variants": ("核对内容与来源...", "检查页面是否回应主题...", "校验引用与内容完整度...")},
        {"id": "seal", "label": "封存入库", "variants": ("封存这份讲稿...", "整理成可编辑资源...", "收笔，交给你修改...")},
    ),
    "doc": (
        {"id": "plan", "label": "拟定方案", "variants": ("先看清这篇文稿要解决什么...", "为文稿拟定章节方案...", "整理读者、主线与材料...")},
        {"id": "compose", "label": "落笔成稿", "variants": ("按确认方案展开写作...", "逐节写成可读内容...", "让文稿回应你的目标...")},
        {"id": "verify", "label": "核对引文", "variants": ("核对引用与来源...", "检查章节是否完整...", "标明资料覆盖与补充内容...")},
        {"id": "seal", "label": "封存入库", "variants": ("整理成可编辑文稿...", "封存这份文稿...", "收笔，交给你修改...")},
    ),
    "mindmap": (
        {"id": "plan", "label": "拟定方案", "variants": ("先梳理主题与分支...", "为这张导图拟定结构...", "整理知识覆盖与层级...")},
        {"id": "resolve", "label": "辨明主题", "variants": ("辨明主题与问法...", "看清这道题问的是什么...", "先分清是分类还是鉴别...")},
        {"id": "gather", "label": "采撷素材", "variants": ("采撷药材与图谱素材...", "翻检教材对应条目...", "把相干的本草取来...")},
        {"id": "shape", "label": "谋篇立骨", "variants": ("谋篇立骨...", "先定一级分支...", "搭起这张图的骨架...")},
        {"id": "compose", "label": "敷演枝叶", "variants": ("敷演枝叶...", "把要点写进每个分支...", "为骨架添上细目...")},
        {"id": "normalize", "label": "收束形制", "variants": ("收束形制...", "理顺层级与字数...", "把长句压成标签...")},
        {"id": "check", "label": "校验枝据", "variants": ("校验枝据...", "逐枝回查出处...", "标出哪些是推断...")},
        {"id": "render", "label": "落墨成像", "variants": ("落墨成像...", "一笔一笔画出枝节...", "让图自己长出来...")},
        {"id": "persist", "label": "封存入库", "variants": ("封存入库...", "把这张图收进书箧...", "记下这次的版本...")},
        {"id": "handoff", "label": "开卷可改", "variants": ("开卷可改...", "图纸已可增删...", "收笔，交给你改...")},
    ),
}


#: v3：把「设计」变成独立一步，并让幻灯的「选材」也能被看见。
#:
#: 为什么必须新开一张表，而不是往 v2 里插一步：``current_step`` 是**索引**语义，
#: 在表中间插一步等于让所有在途任务错位重跑。v3 只写给新任务，v2/v1 一字不动。
#:
#: 三步的分工不能合并：
#:   design 回答「长什么样」（版式、密度、槽位、体裁）——用户要能看见并确认；
#:   select 回答「用什么填」（母题 / 库内命中 / 平替 / 降级）——模型不该编 asset_id；
#:   compose 只负责把已确认的设计写成内容。
#: 合成一步的后果是「设计被写进一句形容词」，确认了也没人遵守——这正是升级前的问题。
ALCHEMY_STEPS_V3: Final[dict[str, tuple[StepSpec, ...]]] = {
    "ppt": (
        {"id": "plan", "label": "拟定方案", "variants": ("先看清这次要做什么...", "为这份幻灯拟定方案...", "整理主题、受众与讲授脉络...")},
        {"id": "design", "label": "设计版式", "variants": ("为每一页定版式...", "把主题落成可看的设计单...", "先设计，再动笔...")},
        {"id": "select", "label": "选取素材", "variants": ("为图位找合适的素材...", "先看库里有什么...", "能算的用母题，找不到的就降级...")},
        {"id": "compose", "label": "逐页成稿", "variants": ("按确认的设计逐页敷演...", "让每一页回应你的目标...", "把讲授脉络落成幻灯...")},
        {"id": "verify", "label": "校验依据", "variants": ("核对内容与来源...", "检查页面是否回应主题...", "校验引用与内容完整度...")},
        {"id": "seal", "label": "封存入库", "variants": ("封存这份讲稿...", "整理成可编辑资源...", "收笔，交给你修改...")},
    ),
    "doc": (
        {"id": "plan", "label": "拟定方案", "variants": ("先看清这篇文稿要解决什么...", "为文稿拟定章节方案...", "整理读者、主线与材料...")},
        {"id": "design", "label": "定体例", "variants": ("定下体裁与篇幅...", "先定版式与引用体例...", "把写法说清楚...")},
        {"id": "compose", "label": "落笔成稿", "variants": ("按确认方案逐节写作...", "逐节写成可读内容...", "让文稿回应你的目标...")},
        {"id": "verify", "label": "核对引文", "variants": ("核对引用与来源...", "检查章节是否完整...", "标明资料覆盖与补充内容...")},
        {"id": "seal", "label": "封存入库", "variants": ("整理成可编辑文稿...", "封存这份文稿...", "收笔，交给你修改...")},
    ),
    "mindmap": (
        {"id": "plan", "label": "拟定方案", "variants": ("先梳理主题与分支...", "为这张导图拟定结构...", "整理知识覆盖与层级...")},
        {"id": "design", "label": "定形制", "variants": ("定下结构与视觉语法...", "选一种画法...", "先定骨架与折叠方式...")},
        {"id": "resolve", "label": "辨明主题", "variants": ("辨明主题与问法...", "看清这道题问的是什么...", "先分清是分类还是鉴别...")},
        {"id": "gather", "label": "采撷素材", "variants": ("采撷药材与图谱素材...", "翻检教材对应条目...", "把相干的本草取来...")},
        {"id": "shape", "label": "谋篇立骨", "variants": ("谋篇立骨...", "先定一级分支...", "搭起这张图的骨架...")},
        {"id": "compose", "label": "敷演枝叶", "variants": ("敷演枝叶...", "把要点写进每个分支...", "为骨架添上细目...")},
        {"id": "normalize", "label": "收束形制", "variants": ("收束形制...", "理顺层级与字数...", "把长句压成标签...")},
        {"id": "check", "label": "校验枝据", "variants": ("校验枝据...", "逐枝回查出处...", "标出哪些是推断...")},
        {"id": "render", "label": "落墨成像", "variants": ("落墨成像...", "一笔一笔画出枝节...", "让图自己长出来...")},
        {"id": "persist", "label": "封存入库", "variants": ("封存入库...", "把这张图收进书箧...", "记下这次的版本...")},
        {"id": "handoff", "label": "开卷可改", "variants": ("开卷可改...", "图纸已可增删...", "收笔，交给你改...")},
    ),
    # 试卷与漫剧在 v3 才升到「先出方案再动手」。它们原先只有一张 v1 表
    # （没有 plan / design 步），于是对话里点「生成试卷」会直接开跑，
    # 用户在拿到成品前看不到任何可确认的东西——与三个工坊的门规矩不一致。
    # 这里补齐同一套五步，闸门函数因此自动认出 design。
    "quiz": (
        {"id": "plan", "label": "拟定方案", "variants": ("先圈定这次要考什么...", "为这份卷子拟定考点...", "理清范围、题型与难度...")},
        {"id": "design", "label": "定卷式", "variants": ("定下题型配比与难度档...", "先排定卷面结构...", "把题量与分值说清楚...")},
        {"id": "compose", "label": "拟题设错", "variants": ("按确认的卷式拟题...", "布置易混干扰项...", "一题一题写出来...")},
        {"id": "verify", "label": "核对答案", "variants": ("核对答案与依据...", "回验每道题的出处...", "检查干扰项是否够像...")},
        {"id": "seal", "label": "封存入库", "variants": ("封存这份卷子...", "整理成可练习的题库...", "收笔，交给你做...")},
    ),
    "story": (
        {"id": "plan", "label": "拟定方案", "variants": ("先看这段医案能讲成什么...", "为这出短剧拟定脉络...", "理清人物、冲突与知识点...")},
        {"id": "design", "label": "定场次", "variants": ("定下场次与角色...", "先排幕次与出场人...", "把舞台分格说清楚...")},
        {"id": "compose", "label": "编排药语", "variants": ("按确认的场次编排...", "一句一句写台词...", "让药性在对话里显出来...")},
        {"id": "verify", "label": "校验医据", "variants": ("校验医案出处...", "回查每段情节的依据...", "封存引用药笺...")},
        {"id": "seal", "label": "封存入库", "variants": ("封存这出短剧...", "整理成可播放的漫剧...", "收笔，交给你演...")},
    ),
}

#: 最新一版。新任务一律写这个版本号；老任务读自己的。
CURRENT_PIPELINE_VERSION: Final[int] = 3


def steps_for(resource_type: str, pipeline_version: int = 1) -> tuple[StepSpec, ...]:
    """按管线版本取工序表。

    三张表并存不是技术债，是**在途任务的兼容层**：``current_step`` 是索引，
    换表就等于让跑到一半的任务错位。版本号跟着任务落库，各跑各的表。
    """

    if resource_type in ALCHEMY_STEPS_V3 and pipeline_version >= 3:
        return ALCHEMY_STEPS_V3[resource_type]
    if resource_type in ALCHEMY_STEPS_V2 and pipeline_version >= 2:
        return ALCHEMY_STEPS_V2[resource_type]
    return ALCHEMY_STEPS[resource_type]  # type: ignore[index]


def step_ids(resource_type: str, pipeline_version: int = CURRENT_PIPELINE_VERSION) -> tuple[str, ...]:
    """只要 id 的视图。契约与前端都用它，避免「第三份手写工序表」。"""

    return tuple(step["id"] for step in steps_for(resource_type, pipeline_version))


def gate_step(resource_type: str, pipeline_version: int) -> str:
    """用户确认闸门落在哪一步。

    v3 起是 ``design``（方案与设计**同批**确认）；v2 及以前是 ``plan``。
    这个函数是唯一答案，服务端暂停、前端等待、契约声明都读它。
    """

    ids = step_ids(resource_type, pipeline_version)
    if "design" in ids:
        return "design"
    return "plan"
