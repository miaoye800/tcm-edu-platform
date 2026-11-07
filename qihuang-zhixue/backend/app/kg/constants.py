"""知识图谱的共享常量：关系分层与方向性。

**这是 tier 的唯一权威定义。** 任何需要判断「某条边属于哪一层」的地方都必须
从这里导入，不要各自写一份映射——现有 915 条相似边正是因为「相似」和「关系」
没有在代码里被明确区分才混进图里的。

分层依据（来自教材，见 data/curated/incompatibility.json 的 page 引用）：
- 第五章 中药的配伍（p124-126）定义七情：相须/相使/相畏/相杀/相恶/相反
- 第六章 第一节 配伍禁忌（p128）定义十八反、十九畏
- 第六章 第三节 妊娠用药禁忌（p131）定义妊娠禁用/慎用

注意两个易混点（教材 p128 明确警告过）：
1. **十九畏的「畏」不是七情里的相畏**。教材原文：「从宋代开始，一些医药著作中，
   出现畏、恶、反名称使用混乱的状况，与《神农本草经》'相畏'的原义相悖。」
   所以「十九畏」必须是与「相畏」分开的关系类型。
2. **相畏与相杀是同一配伍关系的两种提法**（教材 p125）：「相畏和相杀没有本质的
   区别，是从自身的毒副作用受到对方的抑制和自身能消除对方毒副作用的不同角度
   提出来的」。所以 A 畏 B ⟺ B 杀 A，两者都要建，方向相反。
"""

from __future__ import annotations

from enum import IntEnum


class Tier(IntEnum):
    """关系强度分层。数值越小越硬。"""

    CONSTRAINT = 1  # 硬约束：禁忌，错了就是教学事故
    FACT = 2        # 事实：教材明确记载的关系
    SIMILARITY = 3  # 相似：由药材属性推导，用于召回扩展，不进图


#: relation_type → tier。未列出的关系类型一律按 T3 处理（保守：默认不进图）。
RELATION_TIER: dict[str, Tier] = {
    # ---- T1 硬约束（只能来自 curated 表） ----
    "十八反": Tier.CONSTRAINT,
    "十九畏": Tier.CONSTRAINT,
    "相反": Tier.CONSTRAINT,  # 旧数据遗留，等价于十八反
    # ---- T2 事实 ----
    "相须": Tier.FACT,
    "相使": Tier.FACT,
    "相畏": Tier.FACT,
    "相杀": Tier.FACT,
    "相恶": Tier.FACT,
    # 「配伍」= 教材记载这两味药配伍应用，但**没有标注具体七情类别**。
    # 旧代码把 217 条这种边全打成「相须」，等于编造。现在诚实地叫「配伍」。
    "配伍": Tier.FACT,
    "组成": Tier.FACT,
    "药证链": Tier.FACT,
    "证方链": Tier.FACT,
    "同类鉴别": Tier.FACT,
    # ---- T3 相似（不落库） ----
    "功效相通": Tier.SIMILARITY,
    "性味相通": Tier.SIMILARITY,
    "性味相反": Tier.SIMILARITY,
    "归经聚类": Tier.SIMILARITY,
}

#: 对称关系：A→B 存在则 B→A 等价，建库时只写一条，查询时双向匹配。
SYMMETRIC_RELATIONS: frozenset[str] = frozenset(
    {"相须", "配伍", "相反", "十八反", "十九畏", "同类鉴别", "功效相通", "性味相通", "性味相反", "归经聚类"}
)

#: 有方向的关系：方向本身携带语义（主辅、谁制谁、谁恶谁），不能自动补反向边。
#: - 相使：source 是主药，target 是辅药
#: - 相畏：source 的毒副作用被 target 抑制
#: - 相杀：source 能消除 target 的毒副作用
#: - 相恶：source 的功效被 target 削弱
#: - 组成/药证链/证方链：单向从属
DIRECTIONAL_RELATIONS: frozenset[str] = frozenset(
    {"相使", "相畏", "相杀", "相恶", "组成", "药证链", "证方链"}
)

#: 只允许来自人工 curated 表的关系类型。LLM 候选一律拒绝——
#: 模型一定会「补充」出不存在的反药，这是医疗安全红线。
CURATED_ONLY_RELATIONS: frozenset[str] = frozenset({"十八反", "十九畏", "相反"})

#: 12 脏腑归经白名单。现有知识库用脏腑名（"脾"/"肺"），不是「手太阴肺经」。
MERIDIAN_WHITELIST: frozenset[str] = frozenset(
    {"肺", "大肠", "胃", "脾", "心", "小肠", "膀胱", "肾", "心包", "三焦", "胆", "肝"}
)


def tier_of(relation_type: str) -> Tier:
    """未知关系类型按 T3 处理——宁可不上图，也不要让没定义的东西混进来。"""

    return RELATION_TIER.get(relation_type, Tier.SIMILARITY)


def is_symmetric(relation_type: str) -> bool:
    return relation_type in SYMMETRIC_RELATIONS


def is_directional(relation_type: str) -> bool:
    return relation_type in DIRECTIONAL_RELATIONS


def is_curated_only(relation_type: str) -> bool:
    return relation_type in CURATED_ONLY_RELATIONS


__all__ = [
    "Tier",
    "RELATION_TIER",
    "SYMMETRIC_RELATIONS",
    "DIRECTIONAL_RELATIONS",
    "CURATED_ONLY_RELATIONS",
    "MERIDIAN_WHITELIST",
    "tier_of",
    "is_symmetric",
    "is_directional",
    "is_curated_only",
]
