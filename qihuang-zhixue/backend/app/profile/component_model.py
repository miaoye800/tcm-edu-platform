"""Unified seven-dimension component evidence and scoring contract.

Semantic decisions are authored by the extraction/scoring agents.  This module
only validates traceability, stores their relations, deduplicates by the
returned group ids, and applies the published arithmetic.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from math import prod
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.profile.schema import DIMENSION_LABELS, DimensionKey, EvidenceCard


COMPONENTS: dict[DimensionKey, tuple[str, ...]] = {
    "knowledge_base": ("concept_foundation", "knowledge_scope", "retrieval_application", "boundary_awareness", "measured_performance"),
    "learning_goal": ("expression_certainty", "goal_specificity", "action_orientation", "priority_signal", "cross_turn_stability"),
    "learning_style": ("preference_clarity", "cross_task_consistency", "comparative_choice", "strategy_execution", "effect_fit"),
    "weak_points": ("recall_correctness", "application_correctness", "cross_context_stability", "error_recovery", "measured_validation"),
    "pace": ("plan_clarity", "time_feasibility", "actual_execution", "sustained_period", "adjustment_recovery"),
    "motivation": ("intrinsic_value", "external_commitment", "actual_investment", "persistence_under_obstacle", "conflict_regulation"),
    "resource_preference": ("preference_specificity", "task_match_reason", "comparative_choice", "availability_constraint", "usage_effect_feedback"),
}

COMPONENT_WEIGHTS: dict[DimensionKey, dict[str, float]] = {
    "knowledge_base": {"concept_foundation": .25, "knowledge_scope": .20, "retrieval_application": .20, "boundary_awareness": .20, "measured_performance": .15},
    "learning_goal": {"expression_certainty": .25, "goal_specificity": .25, "action_orientation": .20, "priority_signal": .15, "cross_turn_stability": .15},
    "learning_style": {"preference_clarity": .25, "cross_task_consistency": .20, "comparative_choice": .20, "strategy_execution": .15, "effect_fit": .20},
    "weak_points": {"recall_correctness": .25, "application_correctness": .25, "cross_context_stability": .20, "error_recovery": .15, "measured_validation": .15},
    "pace": {"plan_clarity": .20, "time_feasibility": .20, "actual_execution": .30, "sustained_period": .15, "adjustment_recovery": .15},
    "motivation": {"intrinsic_value": .25, "external_commitment": .15, "actual_investment": .25, "persistence_under_obstacle": .20, "conflict_regulation": .15},
    "resource_preference": {"preference_specificity": .25, "task_match_reason": .25, "comparative_choice": .20, "availability_constraint": .15, "usage_effect_feedback": .15},
}

DIMENSION_COMPONENT_LABELS: dict[DimensionKey, dict[str, str]] = {
    "knowledge_base": {"concept_foundation": "概念基础", "knowledge_scope": "知识范围", "retrieval_application": "提取应用", "boundary_awareness": "边界误区意识", "measured_performance": "实测表现"},
    "learning_goal": {"expression_certainty": "表达确定性", "goal_specificity": "目标具体度", "action_orientation": "行动指向", "priority_signal": "优先级信号", "cross_turn_stability": "跨轮稳定性"},
    "learning_style": {"preference_clarity": "偏好明确度", "cross_task_consistency": "跨任务一致性", "comparative_choice": "比较性选择", "strategy_execution": "策略执行", "effect_fit": "效果匹配"},
    "weak_points": {"recall_correctness": "回忆正确性", "application_correctness": "应用正确性", "cross_context_stability": "跨场景稳定性", "error_recovery": "纠错恢复", "measured_validation": "实测验证"},
    "pace": {"plan_clarity": "计划清晰度", "time_feasibility": "时间可行性", "actual_execution": "实际执行", "sustained_period": "持续周期", "adjustment_recovery": "调整恢复"},
    "motivation": {"intrinsic_value": "内在价值", "external_commitment": "外在承诺", "actual_investment": "实际投入", "persistence_under_obstacle": "受阻持续性", "conflict_regulation": "冲突调节"},
    "resource_preference": {"preference_specificity": "偏好具体度", "task_match_reason": "任务匹配理由", "comparative_choice": "比较性选择", "availability_constraint": "可获得性约束", "usage_effect_feedback": "使用效果反馈"},
}

# ---------------------------------------------------------------------------
# Tier bands.  The band is a semantic judgement; the number is only chosen
# inside that band.  Every component score is clamped here so a "high"
# judgement can never leak a medium number.
# ---------------------------------------------------------------------------
TIER_BANDS: dict[str, tuple[float, float]] = {
    "low": (5.0, 40.0),
    "medium": (40.0, 70.0),
    "high": (70.0, 95.0),
}
TIER_ORDER: tuple[str, ...] = ("low", "medium", "high")


def clamp_to_band(tier: str, score: float) -> float:
    """Clamp a model-supplied score into the band it declared."""

    lo, hi = TIER_BANDS.get(str(tier), (5.0, 95.0))
    return round(max(lo, min(hi, float(score))), 2)


# Components that pure conversation can never observe.  They are skipped when
# choosing the next follow-up target (asking is pointless) but still count in
# the coverage denominator of five.
REQUIRES_MEASUREMENT: dict[DimensionKey, dict[str, bool]] = {
    "knowledge_base": {"measured_performance": True},
    "weak_points": {"measured_validation": True},
    "pace": {"actual_execution": True},
    "learning_style": {"strategy_execution": True},
    "resource_preference": {"usage_effect_feedback": True},
    "motivation": {"actual_investment": True, "persistence_under_obstacle": True},
    "learning_goal": {},
}

ACCEPTED_EVIDENCE_TYPES: dict[DimensionKey, tuple[str, ...]] = {
    "knowledge_base": ("self_report", "behavioral", "measured"),
    "learning_goal": ("self_report", "behavioral"),
    "learning_style": ("self_report", "behavioral"),
    "weak_points": ("behavioral", "measured", "self_report"),
    "pace": ("self_report", "behavioral"),
    "motivation": ("self_report", "behavioral"),
    "resource_preference": ("self_report", "behavioral"),
}

NOT_SCORABLE_WHEN = "未提及、尚未确认、只有模糊推测，或没有可追溯原话"
CONFLICT_PRINCIPLE = "保留全部冲突证据；由评分智能体说明冲突如何降低或改变本组件分，不得静默丢弃"
MINIMUM_EVIDENCE = "至少一条与本组件直接相关、可追溯且非空泛的证据"


@dataclass(frozen=True)
class _Rule:
    """One component's own three-tier rubric.

    The rubric is deliberately data, not executable semantic rules: the scoring
    agent reads the prose and returns a tier plus a number inside that tier.
    Every rule must describe *this* component only -- a rule that would still
    read correctly under a sibling component's label is a template in disguise.
    """

    definition: str
    low: str
    medium: str
    high: str
    example_high: str
    example_not_high: str
    numeric_hint: str
    followup: str


_BASE_RUBRIC: dict[DimensionKey, dict[str, _Rule]] = {
    "knowledge_base": {
        "concept_foundation": _Rule(
            "对中药基本范畴（性味、归经、功效分类、气血津液）的理解深度",
            "说不出性味归经各指什么，把功效和主治混为一谈，或只会背药名讲不出所以然",
            "能说清性味归经的含义、知道功效按类别划分，但讲不透同类药之间的差别",
            "能自己举例说明性味到功效到主治的推导链，并能解释某类药为什么归某经",
            "「黄芪甘温归脾肺经，甘能补、温能助阳，所以走脾肺补气升阳，中气下陷用它」",
            "「黄芪是补气药」——只报了类别，没有性味也没有推导",
            "能讲通一条完整推导链 +8；三条以上且跨类别 +15；只报类别上限 45",
            "追问为什么这味药是这个功效，看能不能从性味推出来",
        ),
        "knowledge_scope": _Rule(
            "已掌握的药味数量与类别覆盖广度",
            "只认得 5 味以内的常见药，类别集中在补气补血，其它类别空白",
            "认得 10 到 20 味，覆盖 3 到 4 个类别，偏科明显",
            "认得 30 味以上，解表、清热、补益、理气、收涩等多个类别都能举例",
            "「解表药里麻黄发汗、桂枝解肌、荆芥祛风；清热药还分气分和血分」",
            "「我知道黄芪当归」——只有 2 味，判断不出广度",
            "每多一个能举例的类别 +6；同类别能举 3 味以上再加 4；上限 95",
            "追问除了这几味，还知道哪个类别的药",
        ),
        "retrieval_application": _Rule(
            "面对证候或需求时能否反向调出对应的药",
            "给定气虚乏力想不起该用什么，或答出与证无关的药",
            "常见证能对上主药，但配不出组合，或换个说法就卡住",
            "能给证到功效到药到配伍的完整链路，并能说明为什么不用另一味",
            "「气虚下陷用黄芪配升麻柴胡取补气升阳；如果是气虚水肿就改配防己」",
            "「气虚就补气」——没有落到具体药",
            "一条完整的证到药到理由链 +10；能说出替代方案再加 5；只有类别词上限 40",
            "追问给你一个具体症状，你会先想到哪味药，为什么",
        ),
        "boundary_awareness": _Rule(
            "对易混药、配伍禁忌、用量上下限的辨识",
            "把功效相近的药当同一味用，不知道十八反十九畏，或认为中药没有副作用",
            "知道部分禁忌和易混药，但说不清区别在哪，或只知道不能一起用不知原因",
            "能主动指出易混药的关键差异、配伍禁忌、用量上限与特殊人群限制",
            "「人参和党参都能补气，但人参偏救急、党参偏日常；人参反藜芦，实热证不能用」",
            "「人参不能乱吃」——有边界意识但没有具体边界",
            "每指出一条具体禁忌或易混差异 +8；能说出原因再加 4；只说有禁忌上限 45",
            "追问这味药有什么情况不能用，和哪味药容易混",
        ),
        "measured_performance": _Rule(
            "答题实测的正确率与题型稳定性",
            "有实测但正确率低于四成，或同一味药换个问法就答错",
            "正确率四到七成，常见药稳、生僻药易错",
            "正确率七成以上，功效、性味、配伍、禁忌各题型都能答对",
            "「8 题答对 7 题，唯一错的是一道相似药区分」",
            "「答对 2 题」——样本太少或正确率过低，撑不起中高档",
            "按正确率映射：低于 40% 落 5 到 40，40% 到 70% 落 40 到 70，高于 70% 落 70 到 95",
            "无需追问，靠答题实测",
        ),
    },
    "learning_goal": {
        "expression_certainty": _Rule(
            "目标表达的坚定程度与措辞确定性",
            "用可能、随便、看看再说这类措辞，或前后表述互相矛盾",
            "有意向但带条件（如果时间允许），承诺的边界没有说清",
            "明确陈述且多轮措辞稳定，不含条件退路",
            "「我就是要考中药学的研究生，明年 12 月」",
            "「大概想学学中医吧，看情况」",
            "出现确定性措辞（就是、一定、打算）且无条件句 +10；多轮一致再加 8；含可能或随便上限 40",
            "追问这件事你是已经定了，还是在考虑",
        ),
        "goal_specificity": _Rule(
            "目标是否包含可验证的对象、成果与期限",
            "只有想学中医这类无对象无期限的说法",
            "有目标类别（考研、考证、兴趣），但缺期限或验收标准",
            "对象、成果、期限三者齐全且都能验证",
            "「明年 6 月前把中药学 380 味过一遍，能默写功效归类」",
            "「想把中医学好」——没有期限也没有验收标准",
            "三要素各 +8；有可验收标准再加 6；只有类别词上限 45",
            "追问到什么程度算学好了，打算多久做到",
        ),
        "action_orientation": _Rule(
            "目标是否已经落到行动、路径和第一步",
            "只有愿望，说不出任何已做或要做的具体动作",
            "有初步路径或近期做过一两件事，但不成体系",
            "目标、期限、路径、第一步一致，且已有实施记录",
            "「每天晚上 8 点背 10 味，已经背到第 6 天了」",
            "「准备开始学」——准备不是动作",
            "每有一个可核实的已执行动作 +10；动作与目标对得上再加 5；只有打算或准备上限 40",
            "追问这阵子为学中医具体做过什么，从哪一步开始",
        ),
        "priority_signal": _Rule(
            "目标之间的主次排序与资源取舍",
            "目标一堆但没有排序，或回避取舍问题",
            "有弱顺序信号（先怎样再怎样），但没验证过真实取舍",
            "明确排出主次，说明时间精力怎么分配，有实际取舍记录",
            "「考研优先，社团退了，周末全天给中药学」",
            "「都挺重要的」——没有排序",
            "明确排出主次 +8；说出被放弃的选项再加 6；回答都重要上限 40",
            "追问如果两件事冲突，你先放哪个",
        ),
        "cross_turn_stability": _Rule(
            "目标在多次独立表达中是否一致",
            "前后明显矛盾，或反复放弃又重提",
            "只表达过一次，或第二次表述有偏移但方向一致",
            "多次独立表达一致，并有持续行动佐证",
            "「第一次说考研，三天后还是考研，并提到已经买了资料」",
            "「第一次说考研，第二次说先工作」——方向变了",
            "每多一次独立一致表达 +8；有行动佐证再加 5；出现矛盾上限 40",
            "在后续轮次复述目标并核对是否有变化",
        ),
    },
    "learning_style": {
        "preference_clarity": _Rule(
            "对自己学习方式偏好的清楚程度",
            "说怎么都行、没想过，或偏好随问题变化",
            "能说出偏好方向（喜欢看图、喜欢背），但说不清为什么",
            "明确说出偏好、适用条件，以及为什么适合自己",
            "「我看图记性更好，尤其药材照片配功效，纯文字看不进去」",
            "「我喜欢学习」——这不是方式偏好",
            "说出具体方式 +8；说出适用条件再加 6；回答都行或没想过上限 40",
            "追问你平时怎么记东西最牢，为什么",
        ),
        "cross_task_consistency": _Rule(
            "同一偏好在不同学习任务上是否稳定",
            "不同任务上说法互相打架，或每次都换一种方式",
            "主要任务上一致，换个场景就说不清",
            "背药、读方、做题等不同任务上都体现同一偏好，并能解释共性",
            "「背药我编口诀，读方我也先找规律再记，做题还是先归类」",
            "「背药看视频，读方靠抄」——看不出共同偏好",
            "每多一个场景印证同一偏好 +8；能解释共性再加 5；场景间矛盾上限 40",
            "追问换个学习任务，你的方法会变吗",
        ),
        "comparative_choice": _Rule(
            "在两种学习方式之间做取舍的能力与理由",
            "被问 A 还是 B 时答都行，或根本无法比较",
            "能选出一个，但理由空泛（感觉好）",
            "能选出并给出可检验的理由：效率、留存率、时间成本",
            "「视频比看书快但看完记不住，所以第一遍看视频、第二遍看书」",
            "「视频更好吧」——没有理由",
            "明确选边 +6；给出可检验理由再加 8；回答都行上限 40",
            "追问视频和看书你选哪个，为什么",
        ),
        "strategy_execution": _Rule(
            "声称的学习方法是否真的执行过",
            "只有方法设想，没有任何执行痕迹",
            "试过一两次没坚持，或只在小范围用过",
            "方法已稳定使用一段时间，并能说出效果",
            "「口诀法用了两个月，现在 30 味能当场背出来」",
            "「我打算用口诀背」——还是打算",
            "有明确执行时长 +10；能说出效果再加 6；只有设想上限 40",
            "追问这个方法你实际用了多久，效果怎么样",
        ),
        "effect_fit": _Rule(
            "所用方法与自身实际效果是否对得上",
            "说不出方法有没有用，或明知没用还在用",
            "觉得有用但没有可对照的证据",
            "能指出方法在哪个环节起效、哪个环节无效，并据此调整过",
            "「看图记功效很快，但配伍还是记不住，所以配伍改用案例记」",
            "「挺有用的」——没有具体环节",
            "指出具体起效环节 +8；说出据此做过的调整再加 6；只说有用上限 45",
            "追问这个方法哪部分管用，哪部分不管用",
        ),
    },
    "weak_points": {
        "recall_correctness": _Rule(
            "回忆药物知识点时的准确度",
            "回忆出的功效或性味有明显错误，或把两味药记混",
            "大方向对，细节（归经、用量）常错",
            "回忆准确，偶有偏差能自己察觉",
            "「黄芪甘温归脾肺，补气升阳——归经我说得准」",
            "「黄芪是清热的」——方向就错了",
            "按回忆错误条数扣：0 错 +15，1 到 2 错 +8，3 错以上落低档",
            "追问你回忆一下这味药的性味归经，看能不能说全",
        ),
        "application_correctness": _Rule(
            "把知识用到具体情境时的正确度",
            "给出的药与证不符，或配伍明显错误",
            "主药对，但配伍或剂量不合理",
            "证、药、配伍、剂量都对得上，并能说明理由",
            "「脾虚泄泻用白术配茯苓健脾渗湿，各 10 克」",
            "「泄泻就用黄连」——没有辨证",
            "主药对 +8；配伍合理再加 6；剂量合理再加 4；主药错落低档",
            "追问给你一个具体病例，你会怎么配",
        ),
        "cross_context_stability": _Rule(
            "同一知识点换个问法或场景是否还答得对",
            "换个问法就答错，说明是死记位置而非掌握",
            "同一题型稳定，换题型（选择到问答）就掉",
            "选择、问答、案例三种问法都稳定",
            "「选择题能选对，问答题能写出来，案例里也认得」",
            "「选择题会，一问就说不出来」",
            "每多一种问法答对 +8；出现换个问法就不会上限 45",
            "追问同一味药我换个问法，你还会吗",
        ),
        "error_recovery": _Rule(
            "答错之后能否自我修正",
            "答错后坚持原答案，或改成一个新的错答案",
            "提示后能改对，但不提示就发现不了",
            "能自己发现矛盾并修正，且能说出错在哪",
            "「我刚说黄芪补血，但它明明补气——应该是补气，我把当归串进来了」",
            "「那我再想想」——没有实质修正",
            "自主发现并修正 +10；能定位错误原因再加 6；坚持错答案落低档",
            "追问你刚才说的和前面有点冲突，你看是哪边",
        ),
        "measured_validation": _Rule(
            "薄弱点是否有实测数据支撑",
            "没有任何实测，薄弱点全靠自述猜测",
            "有少量实测，能定位 1 到 2 个薄弱点",
            "有多次实测，薄弱点分布清晰且可复现",
            "「三轮答题里配伍错了 5 次、功效错 1 次，薄弱点集中在配伍」",
            "「我觉得我配伍不行」——没有实测",
            "有实测数据 +10；错题分布可归类再加 6；纯自述上限 40",
            "无需追问，靠答题实测",
        ),
    },
    "pace": {
        "plan_clarity": _Rule(
            "学习计划的粒度与可执行性",
            "没有计划，或只有多花时间这类无法执行的说法",
            "有周计划或日计划，但粒度粗（这周学补益药）",
            "计划到日、到量、到验收（每天 10 味，周日自测）",
            "「周一到周五每天 10 味，周六复习，周日默写测试」",
            "「尽量每天学一点」",
            "有明确周期 +6；有每日量 +6；有验收方式 +6；无计划上限 30",
            "追问你打算怎么安排，一天多少，怎么检查",
        ),
        "time_feasibility": _Rule(
            "计划投入与实际可用时间是否匹配",
            "计划每天 3 小时但实际可用不足 30 分钟，明显不可行",
            "时间大致够，但没算上考试周、加班这类冲突",
            "投入量与可用时间匹配，并预留了缓冲",
            "「每天 40 分钟，通勤听、睡前背，周末留一天补进度」",
            "「每天学 4 小时」——与学生作息明显不符",
            "每日分钟数与身份作息匹配 +8；预留缓冲再加 5；明显超载上限 40",
            "追问你一天真正能拿出多少时间，什么时候",
        ),
        "actual_execution": _Rule(
            "计划是否真的在执行",
            "计划存在但零执行，或执行一天就停",
            "断断续续，执行率约三到六成",
            "连续执行两周以上，执行率七成以上",
            "「连续 18 天，只有 2 天没完成」",
            "「上周开始了」——判断不出持续性",
            "按连续天数映射：少于 3 天落 5 到 35，3 到 13 天落 40 到 65，14 天以上落 70 到 95",
            "追问已经坚持多久了，中间断过没有",
        ),
        "sustained_period": _Rule(
            "已保持学习状态的时间跨度",
            "一周以内，或刚刚开始",
            "一到三个月",
            "三个月以上持续学习",
            "「从三月开始，除了考试周基本没断」",
            "「最近在学」——没有时间跨度",
            "按跨度映射：少于 7 天落 5 到 35，7 到 90 天落 40 到 65，90 天以上落 70 到 95",
            "追问这件事你保持多久了",
        ),
        "adjustment_recovery": _Rule(
            "计划被打断后能否恢复",
            "断一次就再没继续",
            "能恢复，但每次都要从头开始，进度反复归零",
            "有明确的恢复机制（补进度、降量），断过也能接上",
            "「考试周停了一周，回来按 1.5 倍补落下的，没有从头来」",
            "「断了就断了」",
            "有具体恢复做法 +10；实际恢复过再加 6；断即停落低档",
            "追问中间断过吗，断了之后怎么接上",
        ),
    },
    "motivation": {
        "intrinsic_value": _Rule(
            "学习本身的兴趣与价值认同",
            "只因为别人要求或考试压力，说不出任何自身兴趣",
            "有一定兴趣，但说不清喜欢哪一部分",
            "能具体说出哪一部分让 ta 觉得有意思或有意义",
            "「我特别喜欢看方子怎么配，像解谜一样」",
            "「中医挺有意思的」——空泛",
            "说出具体吸引点 +10；能举例说明再加 6；纯外部理由上限 40",
            "追问中医里哪部分让你觉得有意思",
        ),
        "external_commitment": _Rule(
            "对外做出的、可被检验的承诺",
            "没有任何对外承诺",
            "口头说过但没有约束（跟朋友提过）",
            "有具体承诺对象与内容：报名、缴费、组队、立学规",
            "「已经报名 12 月考试交了报名费，还跟室友约了一起学」",
            "「我打算坚持」——自我承诺不算外部",
            "有可核验的对外动作 +10；有第三方监督再加 5；仅口头上限 40",
            "追问有没有跟谁说过、报名或交钱这类动作",
        ),
        "actual_investment": _Rule(
            "已经投入的时间、金钱与精力",
            "除了聊天没有任何投入",
            "买过书或资料，投入时间零散",
            "持续投入时间，并有金钱或机会成本支出",
            "「买了 3 本书和一套网课，每天 40 分钟，已经两个月」",
            "「打算买本书」",
            "有金钱投入 +6；有持续时间投入 +8；两者都有再加 6；纯意愿上限 30",
            "追问为这件事已经花了多少时间和钱",
        ),
        "persistence_under_obstacle": _Rule(
            "遇到困难时的坚持程度",
            "一遇难就放弃，或明确表示会放弃",
            "会卡住但能求助或换方法，尚未验证长期",
            "有过具体受阻经历并坚持下来，能说出当时怎么处理",
            "「性味归经完全看不懂，硬啃了两周，后来改用表格才通」",
            "「应该能坚持吧」——尚未发生",
            "有具体受阻事例 +10；说出应对方式再加 6；只有假设上限 40",
            "追问学的时候卡住过吗，当时怎么处理的",
        ),
        "conflict_regulation": _Rule(
            "学习与其他事务冲突时的处理方式",
            "冲突时总是放弃学习，且事后没有任何补救",
            "冲突时暂停，之后能补一部分",
            "有明确的优先级规则与补偿机制，冲突后学习不掉队",
            "「考试周停学习，考完两天内补完；优先级是考试、中药学、社团」",
            "「冲突就往后推」——没有补偿",
            "有明确优先级规则 +8；有补偿机制再加 6；冲突就停上限 40",
            "追问学习和其他事撞了，你怎么排",
        ),
    },
    "resource_preference": {
        "preference_specificity": _Rule(
            "对学习资源（书、视频、App、图谱）的偏好是否具体",
            "说都行、随便找点资料",
            "能说出资源类型（视频、书），但说不出具体哪一个",
            "能指名具体资源，并说明为什么选它",
            "「我用中药学第十版配 Anki 卡片，教材权威、卡片能刷」",
            "「看视频学」——太泛",
            "指名具体资源 +8；说明选择理由再加 6；回答都行上限 40",
            "追问你具体用什么资料，为什么用这个",
        ),
        "task_match_reason": _Rule(
            "资源选择与学习任务之间的匹配逻辑",
            "资源与任务无关（背功效却只用视频看）",
            "大致匹配，但说不出为什么",
            "能说明这个任务用这个资源更合适的具体理由",
            "「背功效用 Anki，因为要反复刷；理解配伍看案例书，因为要情境」",
            "「都用同一本书」",
            "任务与资源一一对应 +8；说出理由再加 6；不匹配上限 40",
            "追问背药和读方，你会用同一种资料吗，为什么",
        ),
        "comparative_choice": _Rule(
            "在多种学习资源之间做取舍的能力",
            "被问书还是视频时答都看",
            "能选一个，但理由空泛",
            "能比较两种资源的优劣并说明各自适用场景",
            "「视频快但记不住，书慢但能回查，所以第一遍视频、第二遍书」",
            "「都挺好的」",
            "明确选边 +6；说出两者优劣再加 8；回答都看上限 40",
            "追问书和视频你更用哪个，两者差在哪",
        ),
        "availability_constraint": _Rule(
            "资源获取的现实限制（钱、网络、设备、时间）",
            "完全不提限制，或提出的方案明显不现实",
            "提到限制，但没有影响最终选择",
            "明确说出限制，并据此调整了资源选择",
            "「宿舍没网，所以视频先下好离线看；书太贵就借图书馆的」",
            "「买全套网课」——没有考虑成本",
            "说出具体限制 +8；据此调整选择再加 6；无视限制上限 40",
            "追问买书、看视频这些有没有什么不方便的地方",
        ),
        "usage_effect_feedback": _Rule(
            "对所用资源实际效果的反馈",
            "用了但说不出效果",
            "觉得有效，但没有可对照的证据",
            "能指出资源在哪个环节有效、哪个环节无效，并据此换过资源",
            "「那套视频讲配伍很清楚，但性味讲得太浅，所以性味另找了本书」",
            "「挺好的」",
            "指出有效环节 +8；据此换过资源再加 6；只有挺好上限 45",
            "追问这些资料用下来哪部分管用，有没有换过",
        ),
    },
}

COMPONENT_RUBRICS: dict[DimensionKey, dict[str, dict[str, Any]]] = {
    dimension: {
        key: {
            "component": key,
            "label": DIMENSION_COMPONENT_LABELS[dimension][key],
            "weight": COMPONENT_WEIGHTS[dimension][key],
            "definition": rule.definition,
            "low": rule.low,
            "medium": rule.medium,
            "high": rule.high,
            "example_high": rule.example_high,
            "example_not_high": rule.example_not_high,
            "numeric_hint": rule.numeric_hint,
            "minimum_evidence": MINIMUM_EVIDENCE,
            "not_scorable_when": NOT_SCORABLE_WHEN,
            "accepted_evidence_types": list(ACCEPTED_EVIDENCE_TYPES[dimension]),
            "conflict_principle": CONFLICT_PRINCIPLE,
            "followup_direction": rule.followup,
        }
        for key, rule in rules.items()
    }
    for dimension, rules in _BASE_RUBRIC.items()
}

# The quality dimensions have equal published importance. Keeping the weights
# explicit makes this a weighted geometric mean and prevents accidentally
# replacing it with a six-factor product when the contract evolves.
QUALITY_WEIGHTS = {
    "source_reliability": .2,
    "expression_clarity": .2,
    "content_specificity": .2,
    "freshness": .2,
    "consistency": .2,
}

CombinationType = Literal[
    "single_vague_self_report", "single_clear_self_report", "multi_turn_clear_pending_crosscheck",
    "multi_turn_clear_consistent", "multi_turn_self_report_with_priority", "dialogue_behavior_consistent",
    "dialogue_behavior_measurement_long_term",
]

COMBINATION_INTERVALS: dict[CombinationType, tuple[float, float]] = {
    "single_vague_self_report": (.10, .25), "single_clear_self_report": (.20, .38),
    "multi_turn_clear_pending_crosscheck": (.32, .55), "multi_turn_clear_consistent": (.45, .68),
    "multi_turn_self_report_with_priority": (.55, .75), "dialogue_behavior_consistent": (.65, .85),
    "dialogue_behavior_measurement_long_term": (.80, .95),
}


class QualityFactors(BaseModel):
    """Five evidence-quality inputs to the confidence formula.

    ``source_reliability`` and ``freshness`` are **computed by rules**, never
    asked from the model: the evidence type already fixes how trustworthy a
    source is, and the turn distance already fixes how fresh it is.  Letting the
    model guess them produced numbers nobody could explain (the prompt listed
    the field names but no standard, so the same sentence could score 0.9 or
    0.6).  The three remaining factors are model judgements with a written
    three-tier standard (see ``QUALITY_FACTOR_GUIDE``).
    """

    source_reliability: float = Field(default=0.85, ge=0, le=1)
    expression_clarity: float = Field(default=0.5, ge=0, le=1)
    content_specificity: float = Field(default=0.5, ge=0, le=1)
    freshness: float = Field(default=1.0, ge=0, le=1)
    consistency: float = Field(default=0.5, ge=0, le=1)
    independence_note: str = ""


# 三项模型判断项的三档区间。档内留出余量让模型自己落点：只给 0.9 / 0.6 / 0.3
# 三个固定值会把"能懂但有点省略"和"能懂但严重指代不明"压成同一个数，几何平均
# 之后所有证据的置信度都趋同，置信度就失去分辨力。判档仍由模型做，档位由系统
# 按落点反推（见 ``quality_tier_of``），这样审计里"档位"和"数值"永远不会互相
# 矛盾——模型只负责在区间里选一个位置。
QUALITY_FACTOR_TIERS: dict[str, tuple[float, float]] = {
    "low": (0.20, 0.40),
    "medium": (0.45, 0.65),
    "high": (0.70, 0.95),
}

# 落点反推档位的分界：取相邻区间中点。
_QUALITY_TIER_CUTS: tuple[tuple[float, str], ...] = ((0.425, "low"), (0.675, "medium"), (1.01, "high"))


def quality_tier_of(value: float) -> str:
    """按落点反推三档标签，用于审计展示。"""

    for cut, tier in _QUALITY_TIER_CUTS:
        if value < cut:
            return tier
    return "high"


QUALITY_TIER_LABELS: dict[str, str] = {"low": "低", "medium": "中", "high": "高"}

# 三档区间写进提示词，模型不再"看着字段名猜数"。每档给出区间，要求先判档再在
# 档内选点，并说清判档依据与档内落点理由，用户能在审计里按原话复核。
QUALITY_FACTOR_GUIDE = (
    "quality_factors 只评下面三项（不要给 source_reliability 和 freshness，系统按证据类型与轮次自动计算）。\n"
    "每一项都分三档，先判档，再在档内区间里选一个具体数（不要机械填区间中点，也不要用 0.9/0.6/0.3 这三个固定值）：\n"
    "  expression_clarity（表达清晰）：低 0.20–0.40＝含糊、套话、只有情绪词；"
    "中 0.45–0.65＝能懂但有省略或指代不明；高 0.70–0.95＝主谓宾齐全、指代明确、一句话能读懂。\n"
    "  content_specificity（内容具体）：低 0.20–0.40＝泛泛而谈；"
    "中 0.45–0.65＝给了方向但没有细节；高 0.70–0.95＝带数字/时间/书名/药名/动作等可核对细节。\n"
    "  consistency（一致性）：低 0.20–0.40＝与此前证据矛盾；"
    "中 0.45–0.65＝首次出现、暂无可比；高 0.70–0.95＝与此前证据互相印证。\n"
    "  另给两项文字：\n"
    "  quality_reason：一句话结论，必须引用原话里的词，说明为什么落在这个档。\n"
    "  quality_thinking：两三句判定过程，按「看到什么原话 → 命中哪一档的哪个特征 → "
    "档内为什么落在这个位置而不是区间两端」的顺序写，不要复述量表原文。"
)


#: 维度/组件的互斥边界。真实模型抽样（8 次调用）里最常犯的五类越界：
#: 把"我不太清楚"当成实测表现、把看视频的平台当成知识范围、把"跟不上讲授
#: 节奏"当成知识薄弱点、只提一个目标就填目标优先级、一次困难就填动机坚持。
#: 这些卡会照常进评分，把 coverage 和雷达分一起抬高，所以在抽取侧拦住。
DIMENSION_BOUNDARY_GUIDE = (
    "维度边界（越界即错，宁缺勿滥）：\n"
    "- 知识基础：只收对知识本身的掌握；measured_performance（实测表现）必须有答题或操作结果，"
    "“我不太清楚”“记不准”这类自评只能落自评类组件；在哪里看视频、用什么资料属资源偏好，"
    "不计入 knowledge_scope（知识范围）。\n"
    "- 薄弱点：只收自述记不住、易错的知识点；“老师讲得快就跟不上”“这个方法不适合我”属"
    "学习风格的效果匹配（effect_fit），不是薄弱点。跨场景稳定性（cross_context_stability）"
    "需要同一知识点在两个场景下都表现过，单次困难不算。\n"
    "- 学习风格只收学习方式与策略；“我第一反应是小柴胡汤”“辨证先看什么”是知识基础的调取应用，"
    "不算学习风格；比较选择（comparative_choice）指在两种学习方式之间取舍，不是临床选方。\n"
    "- 学习目标的目标优先级（priority_signal）需要用户明确比较或放弃过另一个目标；只提一个目标时不得填。\n"
    "- 动机的困境坚持（persistence_under_obstacle）需要持续投入的证据；一次困难表达不算。\n"
    "- 学习目标标签：考研/读研/升学归“学术深造”；“应试提分”指为某门课程考试提分。\n"
)


class AtomicEvidence(BaseModel):
    model_config = ConfigDict(extra="allow")
    evidence_id: str
    dimension: DimensionKey
    component: str
    evidence_group_id: str
    source_turn_ids: list[int] = Field(default_factory=list)
    quote: str
    normalized_claim: str
    evidence_type: str
    supports: list[str] = Field(default_factory=list)
    collection_status: Literal["scorable", "pending", "conflict", "invalid"] = "pending"
    # The extraction agent may return either prose or a structured object.
    # Both are accepted; structured payloads are flattened in assessment_text().
    assessment: str | dict[str, Any] = ""
    confirmed_aspects: list[str] = Field(default_factory=list)
    pending_aspects: list[str] = Field(default_factory=list)
    semantic_relation: Literal["new", "reinforcement", "semantic_duplicate", "conflict"] = "new"
    related_evidence_ids: list[str] = Field(default_factory=list)
    quality_factors: QualityFactors = Field(default_factory=QualityFactors)
    # 模型对三项质量分的一句话依据，审计里逐条展示，用户可对着原话复核。
    quality_reason: str = ""
    # 判定过程：看到什么原话 → 命中哪一档 → 档内为什么落在这个位置。
    quality_thinking: str = ""

    def assessment_text(self) -> str:
        if isinstance(self.assessment, str):
            return self.assessment.strip()
        for key in ("assessment_reason", "reason", "summary", "note"):
            value = self.assessment.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return json.dumps(self.assessment, ensure_ascii=False)

    @field_validator("quote", "normalized_claim", "evidence_group_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("evidence text and group id must be non-empty")
        return value


class DimensionScan(BaseModel):
    dimension: DimensionKey
    status: Literal["evidence_found", "no_usable_evidence", "pending"]
    reason: str
    components: list[str] = Field(default_factory=list)
    # Structured, user-visible rationale for nearby alternatives considered
    # by the extractor. This is an explanation summary, not private reasoning.
    excluded_candidates: list[dict[str, str]] = Field(default_factory=list)


class ComponentCollection(BaseModel):
    dimension: DimensionKey
    component: str
    status: Literal["scorable", "pending", "conflict"]
    evidence_ids: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)


class EvidenceCollectionOutput(BaseModel):
    turn_id: int
    combination_type: CombinationType = "single_clear_self_report"
    dimension_scan: list[DimensionScan] = Field(default_factory=list)
    atomic_evidence: list[AtomicEvidence] = Field(default_factory=list)
    component_collection: list[ComponentCollection] = Field(default_factory=list)


class ComponentJuror(BaseModel):
    """一位老师对**某一个组件**给出的档内落点。

    三师会诊原来只在维度层出三个数，组件层只出一个综合分——于是"严师觉得
    这个组件只有 40、宽师觉得能到 65"这种真实分歧在审计里彻底消失，用户
    看到的永远是三个老师给整维打分。现在每位老师对每个组件各给一个落点。
    """

    model_config = ConfigDict(extra="ignore")

    score: float | None = Field(default=None, ge=5, le=95)
    reason: str = ""

    @field_validator("score", mode="before")
    @classmethod
    def _empty_to_none(cls, value: Any) -> Any:
        if value in ("", "null", None):
            return None
        return value

    @field_validator("reason", mode="before")
    @classmethod
    def _none_to_empty(cls, value: Any) -> Any:
        return "" if value is None else value


_JURY_KEY_ALIASES = {
    "严师": "严师", "strict": "严师", "strict_teacher": "严师", "严厉": "严师",
    "宽师": "宽师", "lenient": "宽师", "lenient_teacher": "宽师", "宽松": "宽师",
    "中立": "中立", "neutral": "中立", "neutral_teacher": "中立", "中立师": "中立",
}


class ComponentScore(BaseModel):
    """One component's score.  ``score`` is null when there is no evidence.

    A null score is a first-class answer: it means "this grid has not been
    collected yet", not "this grid is bad".  Coverage (see ``dimension_score``)
    counts only non-null scores, so a null keeps the dimension in the
    collecting phase instead of fabricating a number.
    """

    component: str = ""
    tier: Literal["low", "medium", "high"] | None = None
    score: float | None = Field(default=None, ge=5, le=95)
    # 逐组件三师：有值时组件分 = 三位老师落点的平均；为空时退回单个 score，
    # 这样历史库里的旧记录和回退路径都不用改。
    jury: dict[str, ComponentJuror] = Field(default_factory=dict)
    missing: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    reason: str = ""
    conflict_handling: str = ""

    @field_validator("jury", mode="before")
    @classmethod
    def _normalize_jury_keys(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return {}
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            name = _JURY_KEY_ALIASES.get(str(key).strip(), str(key).strip())
            if name in {"严师", "宽师", "中立"} and item is not None:
                normalized[name] = item
        return normalized

    @property
    def jury_mean(self) -> float | None:
        scores = [item.score for item in self.jury.values() if item.score is not None]
        if not scores:
            return None
        return round(sum(scores) / len(scores), 2)

    @field_validator("component", "missing", "reason", "conflict_handling", mode="before")
    @classmethod
    def _optional_text_none_to_empty(cls, value: Any) -> Any:
        # 模型习惯把"这个字段不适用"写成 null（有证据时 missing 就是 null）。
        # 这些字段都只是说明文字，空串与 null 等价；若在这里报错，整批（连同
        # 同维已经打好的分）都会作废，维度直接退回不出分。
        return "" if value is None else value

    @field_validator("score", mode="before")
    @classmethod
    def _empty_to_none(cls, value: Any) -> Any:
        if value in ("", "null", None):
            return None
        return value


class DimensionScoreOutput(BaseModel):
    component_scores: list[ComponentScore] = Field(default_factory=list)
    dimension_summary: str = ""


@dataclass(frozen=True)
class ComponentResult:
    dimension: DimensionKey
    collected: int
    total: int
    component_scores: dict[str, float]
    dimension_score: float | None
    evidence_ids: list[str]
    pending_components: list[str]
    conflicts: list[str]
    coverage: float = 0.0
    weighted_mean: float | None = None

    @property
    def complete(self) -> bool:
        return self.dimension_score is not None


def quote_is_traceable(quote: str, source_text: str) -> bool:
    return bool(quote and source_text and quote.strip() in source_text)


def validate_atomic_evidence(payload: AtomicEvidence, source_text: str) -> AtomicEvidence | None:
    if payload.dimension not in COMPONENTS or payload.component not in COMPONENTS[payload.dimension]:
        return None
    if not quote_is_traceable(payload.quote, source_text):
        return None
    return payload


_COMBINATION_RANK: dict[CombinationType, int] = {
    "single_vague_self_report": 0,
    "single_clear_self_report": 1,
    "multi_turn_clear_pending_crosscheck": 2,
    "multi_turn_clear_consistent": 3,
    "multi_turn_self_report_with_priority": 4,
    "dialogue_behavior_consistent": 5,
    "dialogue_behavior_measurement_long_term": 6,
}
# 公开只读别名：融合层要比较"模型给的类型"和"推断出的类型"谁更强。
COMBINATION_RANK = _COMBINATION_RANK

# 一条证据算"说得清楚"的质量门槛。五项几何平均低于此值时，即使多轮重复
# 也只能进"模糊自述"档——含糊说三遍不会变成清晰。
CLEAR_EVIDENCE_QUALITY = 0.65


def infer_combination_type(
    *,
    group_count: int,
    is_clear: bool,
    consistent: bool = False,
    has_priority: bool = False,
    has_behavioral: bool = False,
    has_long_term_measurement: bool = False,
) -> CombinationType:
    """Pick the evidence-combination band from countable, objective facts.

    Only four things matter and all of them are observable locally: how many
    independent source groups there are, whether the wording was clear, whether
    the groups agree, and whether behavioural / measured evidence exists.  No
    free parameter is involved, which is why the confidence ceiling moves in
    steps rather than along an invented curve.
    """

    if not is_clear:
        # 清晰度是进档的硬门槛，不是加分项：一句含糊的话说三遍还是含糊，
        # 多轮重复只说明"说法稳定"，不说明"我掌握得确定"。
        return "single_vague_self_report"
    if has_long_term_measurement:
        return "dialogue_behavior_measurement_long_term"
    if has_behavioral:
        return "dialogue_behavior_consistent"
    if group_count >= 2:
        if has_priority:
            return "multi_turn_self_report_with_priority"
        if consistent:
            return "multi_turn_clear_consistent"
        return "multi_turn_clear_pending_crosscheck"
    return "single_clear_self_report" if is_clear else "single_vague_self_report"


def refine_combination_type(local: CombinationType, model: str | None) -> CombinationType:
    """The model may only move the band up, never down."""

    if not model or model not in _COMBINATION_RANK:
        return local
    return model if _COMBINATION_RANK[model] > _COMBINATION_RANK[local] else local  # type: ignore[return-value]


STORY_CARD_FIELDS = ("quote", "quality_factors", "evidence_group_id", "component", "collection_status")


def validate_story_card(card: EvidenceCard, source_text: str) -> list[str]:
    """Return the list of contract violations for a story-flow evidence card.

    An empty list means the card may enter the scoring path.  Anything else is
    logged as a warning and the card is dropped, so a half-built card can never
    silently distort coverage.
    """

    problems: list[str] = []
    quote = (card.quote or "").strip()
    if not quote:
        problems.append("quote_empty")
    elif not quote_is_traceable(quote, source_text):
        problems.append("quote_not_traceable")
    if not card.quality_factors:
        problems.append("quality_factors_missing")
    if not (card.evidence_group_id or "").strip():
        problems.append("evidence_group_id_empty")
    if card.dimension not in COMPONENTS or card.component not in COMPONENTS[card.dimension]:
        problems.append("component_not_in_dimension")
    if card.collection_status != "scorable":
        problems.append("not_scorable")
    return problems


_CLAIM_BLACKLIST = (
    "用户表达了观点", "用户提到", "用户说了", "表达了相关想法", "有相关表述",
    "user expressed", "用户表示了一些", "内容不明确",
)


def validate_normalized_claim(claim: str, quote: str) -> tuple[str, list[str]]:
    """Guard the extracted claim against invented information.

    The claim is never dropped -- it degrades to the raw quote, which is exactly
    what the card would have contained without extraction.  So the worst case is
    "no summarisation", never "fabrication".
    """

    warnings: list[str] = []
    raw = (quote or "").strip()
    text = (claim or "").strip()
    if not text:
        return raw, ["claim_empty"]
    if any(phrase in text for phrase in _CLAIM_BLACKLIST):
        return raw, ["claim_boilerplate"]
    digits = re.findall(r"\d+(?:\.\d+)?", text)
    if any(number not in raw for number in digits):
        return raw, ["claim_number_not_in_quote"]
    if raw and len(text) > len(raw) * 1.5:
        return raw, ["claim_too_long"]
    return text, warnings


def deduplicate_evidence(evidence: list[AtomicEvidence]) -> tuple[list[AtomicEvidence], list[AtomicEvidence]]:
    """Return strongest/most recent semantic groups and all conflicts.

    The agent supplies relation and group ids.  No text similarity or keyword
    rule is used here; source groups are the only deterministic identity.
    """
    selected: dict[tuple[str, str, str], AtomicEvidence] = {}
    conflicts: list[AtomicEvidence] = []
    for item in evidence:
        # A quote may support multiple components within a dimension. Keep
        # component attribution distinct while confidence later merges by the
        # shared source group so it cannot count as multiple independent turns.
        key = (item.dimension, item.component, item.evidence_group_id)
        if item.semantic_relation == "conflict":
            conflicts.append(item)
            continue
        current = selected.get(key)
        if current is None or _quality_value(item) > _quality_value(current):
            selected[key] = item
    return list(selected.values()), conflicts


def _quote_identity(card: EvidenceCard) -> str:
    """同一条原话的规范化指纹（去空白与标点）。

    模型的 ``evidence_group_id`` 是"它认为这几条同源"，但实测它会跨轮给同一句
    原话换一个组号：用户说"想学来能让我有钱赚"，第 3 轮和第 5 轮各抽一次、
    组号不同，送评证据包里就出现两条一模一样的引用。用户看到的正是
    "我明明只说了一次，为什么算两条证据"。
    """

    text = str(card.quote or "").strip() or str(card.normalized_claim or card.fact or "").strip()
    return re.sub(r"[\s，。！？、,.!?；;：:\"'“”‘’（）()【】\[\]…—\-]+", "", text)


def deduplicate_component_cards(cards: list[EvidenceCard]) -> tuple[list[EvidenceCard], list[EvidenceCard], list[str]]:
    """Apply the agent-authored source grouping to persisted cards.

    Returns kept evidence, conflicts (always retained), and IDs omitted as
    same-group duplicates. This performs no text similarity or semantic test.
    """
    selected: dict[tuple[str, str, str], EvidenceCard] = {}
    conflicts: list[EvidenceCard] = []
    dropped: list[str] = []
    for card in cards:
        if card.semantic_relation == "conflict":
            conflicts.append(card)
            continue
        group = card.evidence_group_id or str(card.objective_signal.get("evidence_group_id") or card.evidence_id)
        key = (card.dimension, card.component or "", group)
        current = selected.get(key)
        if current is None:
            selected[key] = card
            continue
        current_quality = _card_quality(current)
        candidate_quality = _card_quality(card)
        if candidate_quality > current_quality or (candidate_quality == current_quality and card.timestamp > current.timestamp):
            dropped.append(current.evidence_id)
            selected[key] = card
        else:
            dropped.append(card.evidence_id)
    # 第二道闸门：组号不同、原话相同的，按"同一表达只认定一次"合并。
    # 只比字符串本身，不做语义相似度——宁可在提示词里要求模型给对组号，
    # 也不要在这里猜两句话是不是一个意思。
    by_quote: dict[tuple[str, str, str], EvidenceCard] = {}
    for card in list(selected.values()):
        fingerprint = _quote_identity(card)
        if not fingerprint:
            by_quote[(card.dimension, card.component or "", f"id:{card.evidence_id}")] = card
            continue
        quote_key = (card.dimension, card.component or "", f"quote:{fingerprint}")
        current = by_quote.get(quote_key)
        if current is None:
            by_quote[quote_key] = card
            continue
        current_quality = _card_quality(current)
        candidate_quality = _card_quality(card)
        if candidate_quality > current_quality or (candidate_quality == current_quality and card.timestamp > current.timestamp):
            dropped.append(current.evidence_id)
            by_quote[quote_key] = card
        else:
            dropped.append(card.evidence_id)
    return list(by_quote.values()), conflicts, dropped


def _card_quality(card: EvidenceCard) -> float:
    raw = card.quality_factors or card.objective_signal.get("quality_factors")
    if not isinstance(raw, dict):
        return 0.0
    try:
        return geometric_quality(QualityFactors.model_validate(raw))
    except Exception:
        return 0.0


def _quality_value(item: AtomicEvidence) -> float:
    q = item.quality_factors
    return geometric_quality(q) * (0.75 if item.semantic_relation == "semantic_duplicate" else 1.0)


def geometric_quality(q: QualityFactors) -> float:
    values = {
        "source_reliability": q.source_reliability,
        "expression_clarity": q.expression_clarity,
        "content_specificity": q.content_specificity,
        "freshness": q.freshness,
        "consistency": q.consistency,
    }
    weighted_product = prod(value ** QUALITY_WEIGHTS[key] for key, value in values.items())
    return round(weighted_product, 4)


# 来源可靠由证据类型决定：实测/行为/选择是客观记录，自述只能算中等。
_SOURCE_RELIABILITY_BY_TYPE = {
    "measured": 0.95, "measurement": 0.95, "behavior": 0.95, "choice": 0.95,
    "self_report": 0.85, "inferred": 0.7,
}


def source_reliability_for(evidence_type: str | None) -> float:
    return _SOURCE_RELIABILITY_BY_TYPE.get(str(evidence_type or "").strip().lower(), 0.75)


def freshness_for(current_turn: int | None, source_turns: list[int] | None) -> float:
    """同一轮 1.0，往前每多一轮衰减 0.05，最低 0.85。"""

    if not current_turn or not source_turns:
        return 1.0
    try:
        distance = max(0, int(current_turn) - max(int(turn) for turn in source_turns))
    except (TypeError, ValueError):
        return 1.0
    return round(max(0.85, 1.0 - 0.05 * distance), 4)


def apply_quality_rules(
    factors: QualityFactors,
    *,
    evidence_type: str | None,
    current_turn: int | None,
    source_turns: list[int] | None,
) -> QualityFactors:
    """Overwrite the two rule-derived factors, keep the three model judgements."""

    return factors.model_copy(
        update={
            "source_reliability": source_reliability_for(evidence_type),
            "freshness": freshness_for(current_turn, source_turns),
        }
    )


def collect_component_results(evidence: list[AtomicEvidence]) -> dict[DimensionKey, ComponentResult]:
    deduped, conflicts = deduplicate_evidence(evidence)
    results: dict[DimensionKey, ComponentResult] = {}
    for dimension, components in COMPONENTS.items():
        by_component: dict[str, list[AtomicEvidence]] = {key: [] for key in components}
        for item in deduped:
            if item.dimension == dimension and item.component in by_component and item.collection_status == "scorable":
                by_component[item.component].append(item)
        pending = [key for key, items in by_component.items() if not items]
        results[dimension] = ComponentResult(
            dimension=dimension, collected=len(components) - len(pending), total=len(components),
            component_scores={}, dimension_score=None,
            evidence_ids=[item.evidence_id for item in deduped if item.dimension == dimension],
            pending_components=pending,
            conflicts=[item.evidence_id for item in conflicts if item.dimension == dimension],
        )
    return results


COVERAGE_THRESHOLD = 0.6


def dimension_score(dimension: DimensionKey, component_scores: dict[str, float]) -> dict[str, Any]:
    """The single authoritative dimension-score calculation.

    Every flow must call this.  The formula is deliberately flow-agnostic so the
    dialogue path and the story path stay comparable:

        coverage      = scored components / 5
        weighted_mean = sum(s * w) / sum(w)          # normalised over scored only
        D             = clamp(50 + coverage * (weighted_mean - 50), 5, 95)

    ``coverage`` uses a denominator of five on purpose.  Scoring 3 of 5 keeps
    the score pulled toward the neutral 50 because the two unseen components are
    genuinely unknown -- they could be as low as 5.  Dividing by the number of
    *reachable* components would silently assume the unseen ones look like the
    seen ones, and would also make the same evidence score differently in the
    two flows.
    """

    components = COMPONENTS[dimension]
    weights = COMPONENT_WEIGHTS[dimension]
    scored = {
        key: round(max(5.0, min(95.0, float(score))), 2)
        for key, score in component_scores.items()
        if key in components
    }
    missing = [key for key in components if key not in scored]
    coverage = round(len(scored) / len(components), 4)
    weight_sum = round(sum(weights[key] for key in scored), 4)
    weighted_mean = (
        round(sum(scored[key] * weights[key] for key in scored) / weight_sum, 2)
        if weight_sum
        else None
    )
    gate_ok = coverage >= COVERAGE_THRESHOLD and weighted_mean is not None
    score = (
        round(max(5.0, min(95.0, 50.0 + coverage * (weighted_mean - 50.0))), 2)
        if gate_ok
        else None
    )
    return {
        "dimension": dimension,
        "score": score,
        "coverage": coverage,
        "weight_sum": weight_sum,
        "weighted_mean": weighted_mean,
        "gate_ok": gate_ok,
        "missing": missing,
        "component_scores": scored,
    }


def score_interval(score: float, coverage: float, confidence: float) -> tuple[float, float]:
    """Half-width grows with missing components and with weak evidence."""

    half = 25.0 * (0.6 * (1.0 - coverage) + 0.4 * (1.0 - float(confidence)))
    return (round(max(5.0, score - half), 2), round(min(95.0, score + half), 2))


def apply_dimension_scores(
    evidence: list[AtomicEvidence],
    scores: DimensionScoreOutput,
    *,
    dimension: DimensionKey | None = None,
) -> ComponentResult:
    if not scores.component_scores:
        raise ValueError("component scores are empty")
    resolved = dimension
    if resolved is None:
        # Legacy fallback: infer from the evidence itself.  New callers should
        # pass ``dimension`` explicitly -- inferring from the first score used to
        # raise when that card had been filtered out.
        candidates = {item.dimension for item in evidence if item.dimension in COMPONENTS}
        if len(candidates) != 1:
            raise ValueError("dimension must be provided when evidence spans several dimensions")
        resolved = next(iter(candidates))
    collection = collect_component_results([item for item in evidence if item.dimension == resolved])[resolved]
    score_map = {item.component: item.score for item in scores.component_scores if item.component in COMPONENTS[resolved]}
    outcome = dimension_score(resolved, score_map)
    pending = list(outcome["missing"])
    return ComponentResult(
        resolved, collection.collected, collection.total, score_map,
        outcome["score"], collection.evidence_ids, pending, collection.conflicts,
        coverage=outcome["coverage"], weighted_mean=outcome["weighted_mean"],
    )


def confidence_from_evidence(evidence: list[AtomicEvidence], combination_type: CombinationType) -> dict[str, Any]:
    deduped, conflicts = deduplicate_evidence(evidence)
    groups: dict[str, float] = {}
    for item in deduped:
        contribution = geometric_quality(item.quality_factors)
        groups[item.evidence_group_id] = max(groups.get(item.evidence_group_id, 0.0), contribution)
    progress = 1 - prod(1 - value for value in groups.values()) if groups else 0.0
    lower, upper = COMBINATION_INTERVALS[combination_type]
    final = lower + (upper - lower) * progress
    return {
        "combination_type": combination_type,
        "quality_values": {item.evidence_id: geometric_quality(item.quality_factors) for item in deduped},
        "quality_factors": {item.evidence_id: item.quality_factors.model_dump(mode="json") for item in deduped},
        "independent_source_groups": sorted(groups), "deduplicated_evidence_ids": [item.evidence_id for item in deduped],
        "conflict_evidence_ids": [item.evidence_id for item in conflicts],
        "combination_progress": round(progress, 4), "interval": {"lower": lower, "upper": upper},
        "confidence": round(min(upper, max(lower, final)), 4),
        "formula": "lower + (upper - lower) * (1 - product(1 - effective_contribution))",
    }


def rubric_for(dimension: DimensionKey, component: str) -> dict[str, Any]:
    return COMPONENT_RUBRICS[dimension][component]


__all__ = [
    "COMPONENTS", "COMPONENT_WEIGHTS", "COMPONENT_RUBRICS", "QUALITY_WEIGHTS", "COMBINATION_INTERVALS", "DIMENSION_COMPONENT_LABELS",
    "TIER_BANDS", "TIER_ORDER", "clamp_to_band", "COVERAGE_THRESHOLD", "REQUIRES_MEASUREMENT",
    "ACCEPTED_EVIDENCE_TYPES", "NOT_SCORABLE_WHEN", "CONFLICT_PRINCIPLE", "MINIMUM_EVIDENCE",
    "AtomicEvidence", "ComponentCollection", "ComponentResult", "ComponentScore", "ComponentJuror", "DimensionScan", "DimensionScoreOutput",
    "EvidenceCollectionOutput", "QualityFactors", "apply_dimension_scores", "collect_component_results",
    "dimension_score", "score_interval", "infer_combination_type", "refine_combination_type",
    "validate_story_card", "validate_normalized_claim", "STORY_CARD_FIELDS",
    "confidence_from_evidence", "deduplicate_evidence", "deduplicate_component_cards", "geometric_quality", "quote_is_traceable", "rubric_for",
    "QUALITY_FACTOR_GUIDE", "QUALITY_FACTOR_TIERS", "QUALITY_TIER_LABELS", "quality_tier_of",
    "apply_quality_rules", "source_reliability_for", "freshness_for",
]
