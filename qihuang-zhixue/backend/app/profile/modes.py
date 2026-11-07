"""Profile evidence collection modes.

This module only collects objective EvidenceCard records. It never writes
scores or synthesizes profile judgments.
"""

from __future__ import annotations

import json
import hashlib
import asyncio
import logging
import random
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, cast, get_args
from uuid import uuid4

from app.config import get_settings
from app.llm.clients import MockClient
from app.llm.router import ModelRouter
from app.profile.anchors import SCORE_ANCHOR_INTERVALS, interval_midpoint
from app.profile.fusion import ALLOWED_LABELS, BASE_LEVEL_SCORES, FusionResult, fuse_profile
from app.profile.component_model import (
    COMPONENTS,
    COMPONENT_RUBRICS,
    CombinationType,
    COMPONENT_WEIGHTS,
    COVERAGE_THRESHOLD,
    DIMENSION_COMPONENT_LABELS,
    QUALITY_FACTOR_GUIDE,
    QUALITY_FACTOR_TIERS,
    QUALITY_TIER_LABELS,
    QualityFactors,
    REQUIRES_MEASUREMENT,
    deduplicate_component_cards,
    dimension_score,
    geometric_quality,
    quality_tier_of,
    source_reliability_for,
    validate_normalized_claim,
    validate_story_card,
)
from app.profile.jury import ScoringResult, score_dimension, score_profile
from app.profile.prescription import evaluate_prescription
from app.profile.radar import LOW_CONFIDENCE_THRESHOLD
from app.profile.schema import (
    DIMENSION_LABELS,
    DimensionKey,
    EvidenceCard,
    IdentityCardView,
    Profile,
    canonical_dimension_name,
    create_blank_profile,
    evidence_card_from_dict,
    evidence_card_to_json_dict,
)
from app.profile.store import (
    DB_PATH,
    add_evidence,
    append_chat_messages,
    delete_profile_session_rows,
    get_profile,
    get_profile_session_row,
    list_chat_messages,
    claim_dialogue_turn,
    complete_dialogue_turn,
    release_dialogue_turn,
    upsert_profile,
    upsert_profile_session_row,
)

logger = logging.getLogger(__name__)


class ProfileMode(str, Enum):
    """Supported profile collection modes."""

    apprentice = "apprentice"
    quickcard = "quickcard"
    practice = "practice"
    passive = "passive"
    dialogue = "dialogue"


APPRENTICE_STEPS: tuple[str, ...] = (
    "apprentice_prologue",
    "apprentice_act1_quiz",
    "apprentice_act2_prescription",
    "apprentice_act3_resource",
    "apprentice_act4_rule",
)

STEP_LABELS: dict[str, str] = {
    "apprentice_prologue": "序幕·叩问初心",
    "apprentice_act1_quiz": "一幕·药圃初认",
    "apprentice_act2_prescription": "二幕·初次抓药",
    "apprentice_act3_resource": "三幕·师父药宝",
    "apprentice_act4_rule": "四幕·立下学规",
    "quickcard": "身份卡+30秒速判",
    "practice": "开药实战",
    "passive": "纯被动埋点",
    "dialogue": "对话式画像",
    "dialogue_confirm": "确认画像线索",
}

IDENTITY_CARDS: dict[str, dict[str, str]] = {
    "考前冲刺": {"learning_goal": "应试提分", "motivation": "外驱"},
    "临床学徒": {"learning_goal": "临床实用", "motivation": "内驱"},
    "草木好奇者": {"learning_goal": "兴趣科普", "motivation": "内驱"},
    "研读派": {"learning_goal": "学术深造", "motivation": "内外混合"},
}

RESOURCE_TYPES = {"知识图谱", "拟人化短剧", "汤头歌诀", "医案文选"}
IDENTITY_CARD_SKINS: dict[DimensionKey, dict[str, str]] = {
    "learning_goal": {
        "应试提分": "赶考",
        "临床实用": "济世",
        "兴趣科普": "问草",
        "学术深造": "研经",
        "基础巩固": "筑基",
        "能力提升": "精进",
        "其他": "自定",
    },
    "motivation": {
        "内驱": "求道者",
        "外驱": "赶考人",
        "内外混合": "双修者",
        "被动": "随缘客",
    },
    "resource_preference": {
        "知识图谱": "观脉者",
        "拟人化短剧": "入戏郎",
        "汤头歌诀": "诵诀生",
        "医案文选": "读案客",
    },
    "learning_style": {
        "视觉型": "观象者",
        "文字型": "研文客",
        "记忆(口诀)型": "记颂客",
    },
}
IDENTITY_CARD_MEANINGS: dict[DimensionKey, dict[str, str]] = {
    "learning_goal": {
        "应试提分": "以考试、考证或阶段成绩为当前落点",
        "临床实用": "希望把所学用于辨证、用药或真实临床情境",
        "兴趣科普": "由好奇与兴趣牵引，先建立理解与感受",
        "学术深造": "面向考研、科研或更系统的专业研习",
        "基础巩固": "先补齐基础概念，建立稳定的知识根基",
        "能力提升": "以已有基础为起点，继续提升实际学习能力",
        "其他": "来自你的独特目标，仍需后续对话继续辨明",
    },
    "motivation": {"内驱": "主要由自己的兴趣与求知欲推动", "外驱": "主要由考试、课程或任务推动", "内外混合": "个人意愿与外部目标共同推动", "被动": "当前动力较弱，需要更轻的起步节奏"},
    "resource_preference": {"知识图谱": "偏好先看结构关系与知识全貌", "拟人化短剧": "偏好借情境和人物理解知识", "汤头歌诀": "偏好用节律与口诀帮助记忆", "医案文选": "偏好从病例与推演中理解知识"},
    "learning_style": {"视觉型": "借图像、结构和位置关系理解得更快", "文字型": "借条理清楚的文字解释理解得更稳", "记忆(口诀)型": "借口诀、节律与重复形成记忆"},
}
KNOWLEDGE_LEVEL_CARD_NAMES: dict[str, str] = {
    "零基础": "药童",
    "入门": "学徒",
    "一般": "坐堂医",
    "较好": "杏林手",
    "熟练": "大医",
}
PACE_LEVEL_ANCHORS: tuple[tuple[float, str], ...] = (
    (interval_midpoint(SCORE_ANCHOR_INTERVALS["pace"]["daily_minutes"]["lt_15"]), "文火慢炖"),
    (interval_midpoint(SCORE_ANCHOR_INTERVALS["pace"]["daily_minutes"]["15_to_40"]), "温火常煎"),
    (interval_midpoint(SCORE_ANCHOR_INTERVALS["pace"]["daily_minutes"]["gt_40"]), "武火急煎"),
)
STYLE_BY_CLUE = {
    "图": "视觉型",
    "图片": "视觉型",
    "药图": "视觉型",
    "文字": "文字型",
    "条文": "文字型",
    "口诀": "记忆(口诀)型",
    "歌诀": "记忆(口诀)型",
}
TOXIC_OR_FIERCE_HERBS = {"附子", "川乌", "草乌", "马钱子", "巴豆", "雄黄", "朱砂"}

DIALOGUE_OPENING_PROMPT = "先随便聊聊：你最近为什么想学中医药？是为了考试、临床、研究，还是单纯好奇？"
DIALOGUE_MIN_TURNS = 3
DIALOGUE_MAX_TURNS = 5  # Legacy display hint only; dialogue no longer ends by turn count.

# Process-local protection for a session while a streaming turn is being
# committed. SQLite remains the source of truth across processes; this lock
# prevents the common duplicate-submit race within one API worker.
_DIALOGUE_SESSION_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}


def _dialogue_session_lock(session_id: str, db_path: Path) -> asyncio.Lock:
    key = (str(db_path), session_id)
    lock = _DIALOGUE_SESSION_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _DIALOGUE_SESSION_LOCKS[key] = lock
    return lock


def dialogue_session_busy(session_id: str, db_path: Path = DB_PATH) -> bool:
    """Return whether a dialogue turn is currently being committed."""

    return _dialogue_session_lock(session_id, db_path).locked()
DIALOGUE_CONFIDENCE_WEIGHT_K = 1.5
DIALOGUE_TARGET_RETRY_LIMIT = 2
DIALOGUE_CONFIDENCE_THRESHOLDS: dict[DimensionKey, float] = {
    dimension: LOW_CONFIDENCE_THRESHOLD for dimension in DIMENSION_LABELS
}
DIALOGUE_PRIMARY_DIMENSIONS: tuple[DimensionKey, ...] = ("learning_goal", "motivation")
DIALOGUE_TARGET_LABELS: dict[DimensionKey, str] = {
    "learning_goal": "学习目标",
    "motivation": "动机",
    "knowledge_base": "基础",
    "learning_style": "学习风格",
    "resource_preference": "资源偏好",
    "pace": "学习节奏",
    "weak_points": "薄弱点",
}
DIALOGUE_TARGET_EXTRACTABLE_QUESTIONS: dict[DimensionKey, tuple[str, ...]] = {
    "learning_goal": (
        "你这阶段更偏应试提分、临床实用、兴趣科普，还是学术深造？",
        "如果只选一个学习目标，你最想先解决考试、临床、兴趣入门还是科研深造？",
    ),
    "learning_style": (
        "你更吃视觉图表、文字条理，还是口诀记忆这一类材料？",
        "同样一味药，你通常靠图像、文字解释，还是歌诀口诀记得更牢？",
    ),
    "pace": (
        "接下来你每天大概能留多少分钟学中医药，想坚持多少天？",
        "如果按一个小周期来定，你希望每天学几分钟、连续学几天比较舒服？",
    ),
    "motivation": (
        "这件事更像你自己想弄明白，还是考试、课程、实习在推着你？",
        "你的学习动力更偏内驱、外驱、内外混合，还是目前有点被动？",
    ),
    "resource_preference": (
        "给你材料的话，你会先打开知识图谱、拟人化短剧、汤头歌诀，还是医案文选？",
        "这几类资源里，知识图谱、短剧讲解、汤头歌诀、医案文选，你最愿意从哪个开始？",
    ),
    "knowledge_base": (
        "你现在中医药基础大概自评到哪：零基础、入门、一般、较好，还是熟练？",
        "要不要用一句话自评一下基础，或者愿意之后做个很短的小测来校准？",
    ),
    "weak_points": (
        "现在最容易卡住的是药性功效、配伍剂量、辨证思路，还是背诵记忆？",
        "最近学中药时，哪个知识点最常让你不确定？",
    ),
}
DIALOGUE_FALLBACK_QUESTIONS: dict[DimensionKey, tuple[str, ...]] = {
    "learning_goal": (
        "如果把这次学习当成一个小目标，你更想解决考试提分、临床应用、科研深造，还是兴趣入门？",
        "你希望学完这一阶段后，最明显的变化是什么？",
    ),
    "motivation": (
        "这件事对你来说更像是自己想弄明白，还是有考试、课程、实习之类的外部压力在推着？",
        "什么时候你会觉得中医药学起来挺有劲？",
    ),
    "knowledge_base": (
        "你现在大概学到哪一层了：刚入门、能记一些药性功效，还是已经能做简单辨证配伍？",
        "随便举个你熟悉的中药或方药点也行，我想听听你现在的基础落点。",
    ),
    "learning_style": (
        "平时你更吃哪种材料：图谱表格、条文文章、口诀歌诀，还是案例拆解？",
    ),
    "resource_preference": (
        "如果给你一份学习材料，你会先打开知识图谱、短剧讲解、汤头歌诀，还是医案文选？",
    ),
    "pace": (
        "接下来一两周，你每天大概能留多少分钟给这件事？",
    ),
    "weak_points": (
        "现在最容易卡住的是药性功效、配伍剂量、辨证思路，还是背诵记忆？",
    ),
}

FALLBACK_QUESTIONS: list[dict[str, Any]] = [
    {
        "id": "fallback_effect_mahuang",
        "question": "麻黄的代表功效之一是？",
        "options": ["发汗解表", "清热凉血", "补气升阳", "安神定志"],
        "answer": "发汗解表",
        "topic": "解表药·麻黄功效",
        "skill": "功效识记",
    },
    {
        "id": "fallback_effect_jinyinhua",
        "question": "金银花更适合归入哪类功效线索？",
        "options": ["清热解毒", "温经散寒", "补血活血", "收涩固脱"],
        "answer": "清热解毒",
        "topic": "清热药·功效识别",
        "skill": "药性辨识",
    },
    {
        "id": "fallback_pair_guizhi",
        "question": "桂枝常与哪味药配伍以增强发汗解表？",
        "options": ["麻黄", "黄连", "麦冬", "朱砂"],
        "answer": "麻黄",
        "topic": "解表药·配伍",
        "skill": "配伍判断",
    },
]

DIAGNOSTIC_HERB_PHOTO_IDS = {
    "herb_taizishen",
    "herb_dangshen",
    "herb_huangqi",
    "herb_chenpi",
    "herb_jinyinhua",
}


@dataclass
class ProfileStep:
    """One client-facing collection step."""

    id: str
    title: str
    kind: str
    prompt: str
    payload: dict[str, Any] = field(default_factory=dict)
    private_payload: dict[str, Any] = field(default_factory=dict)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "prompt": self.prompt,
            "payload": self.payload,
            "started_at": self.started_at.isoformat(),
        }


@dataclass
class ProfileSession:
    """State for a persisted collection run."""

    session_id: str
    user_id: str
    mode: ProfileMode
    current_step: ProfileStep | None
    step_index: int = 0
    step_data: dict[str, Any] = field(default_factory=dict)
    evidence_cards: list[EvidenceCard] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished: bool = False
    persisted: bool = False
    persisted_db_path: str | None = None

    @property
    def current_stage(self) -> str | None:
        return self.current_step.title if self.current_step else None


def start_profile_session(user_id: str, mode: ProfileMode | str, db_path: Path = DB_PATH) -> ProfileSession:
    """Start a collection session and return the first step."""

    parsed_mode = ProfileMode(mode)
    session = ProfileSession(
        session_id=uuid4().hex,
        user_id=user_id,
        mode=parsed_mode,
        current_step=_initial_step(parsed_mode),
    )
    if session.current_step and _is_quiz_step(session.current_step.id):
        session.step_data["questions"] = session.current_step.private_payload.get("questions", [])
    if parsed_mode == ProfileMode.dialogue and session.current_step:
        session.step_data["radar_baseline"] = _compact_radar_axes(_radar_payload(get_profile(user_id, db_path=db_path)))
        asked_questions = _dialogue_step_list(session.step_data, "asked_questions")
        _dialogue_remember(asked_questions, session.current_step.prompt)
        session.step_data["next_question"] = session.current_step.prompt
    _save_profile_session(session, db_path=db_path)
    return session


async def answer_profile_session(
    session_id: str,
    user_id: str,
    step_id: str,
    payload: dict[str, Any],
    db_path: Path = DB_PATH,
) -> tuple[ProfileSession, list[EvidenceCard]]:
    """Convert one step answer into EvidenceCards and advance the session."""

    session = get_profile_session(session_id, user_id=user_id, db_path=db_path)
    if session.finished:
        return session, []
    if session.current_step is None:
        session.finished = True
        _save_profile_session(session, db_path=db_path)
        return session, []
    if step_id != session.current_step.id:
        raise ValueError(f"expected step_id {session.current_step.id}, got {step_id}")
    if session.current_step.kind == "confirm":
        session.finished = True
        session.current_step = None
        _save_profile_session(session, db_path=db_path)
        return session, []

    response_time_ms = _response_time_ms(payload, session.current_step.started_at)
    if session.mode == ProfileMode.apprentice:
        cards = await _answer_apprentice(session, payload, response_time_ms)
    elif session.mode == ProfileMode.quickcard:
        cards = await _answer_quickcard(session, payload, response_time_ms)
    elif session.mode == ProfileMode.practice:
        cards = [await _prescription_card(payload, source=STEP_LABELS["practice"], weight=1.5, response_time_ms=response_time_ms)]
    elif session.mode == ProfileMode.dialogue:
        cards = await _answer_dialogue(session, payload)
    else:
        cards = _passive_cards(payload, response_time_ms)

    session.evidence_cards.extend(cards)
    session.updated_at = datetime.now(UTC)
    session.current_step = _next_step(session)
    if session.current_step is None:
        session.finished = True
    elif _is_quiz_step(session.current_step.id):
        session.step_data["questions"] = session.current_step.private_payload.get("questions", [])
    if session.mode == ProfileMode.dialogue:
        _dialogue_identity_cards_view(
            session.evidence_cards,
            fusion=_dialogue_fusion(session.evidence_cards),
            step_data=session.step_data,
            consume_revealed_now=False,
        )
    _save_profile_session(session, db_path=db_path)
    return session, cards


def finish_profile_session(session_id: str, user_id: str, db_path: Path = DB_PATH) -> ProfileSession:
    """Finish a session and persist cards once."""

    session = get_profile_session(session_id, user_id=user_id, db_path=db_path)
    if session.mode == ProfileMode.dialogue:
        return asyncio.run(_finish_dialogue_profile_session(session, db_path=db_path))
    return _finish_profile_session(session, db_path=db_path)


async def finish_profile_session_async(session_id: str, user_id: str, db_path: Path = DB_PATH) -> ProfileSession:
    """Async finish path used by FastAPI endpoints."""

    session = get_profile_session(session_id, user_id=user_id, db_path=db_path)
    if session.mode == ProfileMode.dialogue:
        return await _finish_dialogue_profile_session(session, db_path=db_path)
    if session.mode == ProfileMode.apprentice:
        return await _finish_apprentice_profile_session(session, db_path=db_path)
    return _finish_profile_session(session, db_path=db_path)


# 五幕问答全部结束后统一抽取，按维度分三批。分批不是按幕：每幕单独看拿不到
# 跨幕对比，`cross_turn_stability` 这类组件就判不了；一次全量又会因为输出太长
# 被截断。
STORY_EXTRACTION_BATCHES: tuple[tuple[tuple[DimensionKey, ...], int], ...] = (
    (("knowledge_base", "weak_points"), 12),
    (("learning_goal", "motivation"), 10),
    (("learning_style", "pace", "resource_preference"), 13),
)


# 选项标签和一个数字都不是散文，抽不出「谁+做了什么+限定条件」。把它们喂给模型
# 只会诱导它编句子，所以这两种题型的卡不进入 transcript；全是选择题的会话
# transcript 为空，抽取阶段一次模型调用都不发。
_NON_EXTRACTABLE_QUESTION_TYPES = frozenset({"选择", "数字"})


def _story_transcript(session: ProfileSession) -> str:
    """Flatten the five acts into one transcript the extractor can read.

    Built from the rule cards, because they already carry the user's own words
    plus the answer form and speed bucket -- no second copy of the answers to
    keep in sync.
    """

    lines: list[str] = []
    seen: set[tuple[str, str]] = set()
    for card in session.evidence_cards:
        quote = (card.quote or "").strip()
        if not quote:
            continue
        if str((card.objective_signal or {}).get("question_type") or "") in _NON_EXTRACTABLE_QUESTION_TYPES:
            continue
        key = (card.source, quote)
        if key in seen:
            continue
        seen.add(key)
        signal = card.objective_signal or {}
        tags = [str(signal.get("question_type") or ""), str(signal.get("response_speed") or "")]
        tag_text = "，".join(item for item in tags if item)
        suffix = f"（{tag_text}）" if tag_text else ""
        lines.append(f"【{card.source}】用户：{quote}{suffix}")
    return "\n".join(lines)


def _story_extraction_messages(transcript: str, dimensions: tuple[DimensionKey, ...], limit: int) -> list[dict[str, str]]:
    component_lines = "\n".join(
        f"- {DIMENSION_LABELS[dimension]}：" + "、".join(
            f"{DIMENSION_COMPONENT_LABELS[dimension][component]}({component})" for component in COMPONENTS[dimension]
        )
        for dimension in dimensions
    )
    return [
        {
            "role": "system",
            "content": (
                "你是「岐黄智学」的画像证据抽取器。只抽取证据，绝对不评分、不判档、不输出任何分数。"
                "每条证据必须引用用户原话的连续子串；提炼句固定句式「谁+做了什么/具备什么+限定条件」，不超过30字，"
                "不得加入原话里没有的信息，不得出现原话没有的专有名词；原话已经够清楚就直接截取原话。"
                "只输出 JSON，不要 Markdown。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"本批只处理以下 {len(dimensions)} 个维度：{', '.join(dimensions)}\n"
                f"可选组件：\n{component_lines}\n\n"
                f"完整剧情 transcript：\n{transcript}\n\n"
                "输出 JSON：{\"evidence\": [{\"dimension\": string, \"component\": string, "
                "\"quote\": string, \"normalized_claim\": string, "
                "\"evidence_type\": \"self_report\"|\"behavior\"|\"measured\", "
                "\"quality_factors\": {\"expression_clarity\": 0-1, \"content_specificity\": 0-1, \"consistency\": 0-1}, "
                "\"quality_reason\": string, \"quality_thinking\": string}]}\n"
                + QUALITY_FACTOR_GUIDE
                + "\n"
                f"最多 {limit} 条。没有证据就返回空数组，不要编造。"
            ),
        },
    ]


# 审计里显示中文名，不要把英文键名直接摊给用户看。
QUALITY_FACTOR_LABELS = {
    "source_reliability": "来源可靠",
    "expression_clarity": "表达清晰",
    "content_specificity": "内容具体",
    "freshness": "新鲜度",
    "consistency": "一致性",
}

# 模型现在给的是档内区间里的具体数（见 QUALITY_FACTOR_GUIDE），字符串只作
# 兼容回退：老提示词/老缓存会返回 "high"/"medium"/"low"，映射到该档中位，
# 不要映射到 0.9/0.6/0.3 这种贴边的固定值。
_QUALITY_TIERS = {"high": 0.82, "medium": 0.55, "low": 0.30, "高": 0.82, "中": 0.55, "低": 0.30}


def _coerce_quality_value(value: Any, default: float) -> float:
    if isinstance(value, str):
        return _QUALITY_TIERS.get(value.strip().lower(), _QUALITY_TIERS.get(value.strip(), default))
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return round(min(1.0, max(0.0, float(value))), 4)
    return default


def _quality_factors_from_payload(item: dict[str, Any]) -> dict[str, Any]:
    """三项由模型判、两项由规则算。

    以前五项全问模型，提示词只列字段名、没有标准，同一句话两次抽取能给 0.9
    和 0.6；而整个确认度区间插值都建在这五个数上。来源可靠由证据类型决定、
    新鲜度由轮次决定，这两项本来就不该问。
    """

    raw = item.get("quality_factors") if isinstance(item.get("quality_factors"), dict) else {}
    return {
        "source_reliability": source_reliability_for(str(item.get("evidence_type") or "self_report")),
        "expression_clarity": _coerce_quality_value(raw.get("expression_clarity") or raw.get("clarity"), 0.5),
        "content_specificity": _coerce_quality_value(raw.get("content_specificity") or raw.get("specificity"), 0.5),
        "consistency": _coerce_quality_value(raw.get("consistency") or raw.get("coherence"), 0.5),
        "freshness": 1.0,
        "independence_note": str(raw.get("independence_note") or ""),
    }


async def _story_extract_batch(
    session: ProfileSession,
    dimensions: tuple[DimensionKey, ...],
    limit: int,
    *,
    transcript: str,
) -> list[EvidenceCard]:
    """One extraction call. A failure returns nothing and never blocks scoring."""

    if not _has_profile_extract_key() or not transcript:
        return []
    try:
        raw = await ModelRouter().chat(
            "profile_extract",
            _story_extraction_messages(transcript, dimensions, limit),
            response_format={"type": "json_object"},
            temperature=0,
        )
        payload = _parse_json_object(raw)
    except Exception as exc:  # noqa: BLE001 - a failed batch must not fail the finish
        logger.warning("story extraction batch failed dimensions=%s error=%s", dimensions, exc)
        return []
    items = payload.get("evidence") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    allowed = set(dimensions)
    cards: list[EvidenceCard] = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        dimension = str(item.get("dimension") or "").strip()
        component = str(item.get("component") or "").strip()
        if dimension not in allowed or component not in COMPONENTS.get(dimension, ()):  # type: ignore[arg-type]
            continue
        quote = str(item.get("quote") or "").strip()
        if not quote:
            continue
        claim, _issues = validate_normalized_claim(str(item.get("normalized_claim") or ""), quote)
        card = _card(
            dimension,  # type: ignore[arg-type]
            "五幕回顾",
            claim,
            {
                "quote": quote,
                "normalized_claim": claim,
                "evidence_type": str(item.get("evidence_type") or "self_report"),
                "quality_factors": _quality_factors_from_payload(item),
                "quality_reason": str(item.get("quality_reason") or ""),
                "quality_thinking": str(item.get("quality_thinking") or ""),
                "basis": "explicit",
                "confidence": 0.8,
            },
            1.0,
            component=component,
            answer_kind="self_report",
        )
        card.extraction_method = "llm"
        if validate_story_card(card, transcript):
            continue
        cards.append(card)
    return cards


async def _finish_apprentice_profile_session(session: ProfileSession, *, db_path: Path = DB_PATH) -> ProfileSession:
    """Finish the story flow: three extraction batches, then persist.

    Extraction is an enrichment step, never a prerequisite: whatever it returns
    is added on top of the rule cards, and a batch that fails simply contributes
    nothing.  The outcome is recorded on the session so a "rule_only" profile is
    distinguishable from one that never had extraction to begin with.
    """

    transcript = _story_transcript(session)
    # 三批互不依赖（各自只读同一份 transcript，结果在末尾统一去重），所以并发跑。
    # 串行实测 57s，远超浏览器默认 10s 的请求超时；并发后约等于最慢那一批。
    batches = [
        (dimensions, limit)
        for dimensions, limit in STORY_EXTRACTION_BATCHES
        if _has_profile_extract_key() and transcript
    ]
    results = await asyncio.gather(
        *(_story_extract_batch(session, dimensions, limit, transcript=transcript) for dimensions, limit in batches)
    )
    attempted = len(batches)
    succeeded = sum(1 for batch in results if batch)
    for batch in results:
        session.evidence_cards.extend(batch)
    deduped, _duplicates, _notes = deduplicate_component_cards(session.evidence_cards)
    session.evidence_cards = deduped
    if not attempted:
        outcome = "not_attempted"
    elif not succeeded:
        outcome = "rule_only"
    elif succeeded < attempted:
        outcome = "mixed"
    else:
        outcome = "llm"
    session.step_data["extraction_provenance"] = outcome
    return _finish_profile_session(session, db_path=db_path)


def _finish_profile_session(session: ProfileSession, *, db_path: Path = DB_PATH) -> ProfileSession:
    session.finished = True
    session.current_step = None
    session.updated_at = datetime.now(UTC)
    if not session.persisted:
        for card in session.evidence_cards:
            add_evidence(session.user_id, card, db_path=db_path)
        session.persisted = True
        session.persisted_db_path = str(db_path)
    _save_profile_session(session, db_path=db_path)
    return session


def get_profile_session(session_id: str, user_id: str, db_path: Path = DB_PATH) -> ProfileSession:
    """Return an owned session or raise KeyError for API 404 handling."""

    return _session_from_row(get_profile_session_row(session_id, user_id, db_path=db_path))


def reset_profile_sessions(db_path: Path = DB_PATH) -> None:
    """Clear persisted sessions for tests and demos."""

    delete_profile_session_rows(db_path=db_path)


def _save_profile_session(session: ProfileSession, db_path: Path = DB_PATH) -> None:
    session.updated_at = datetime.now(UTC)
    cards_payload = [evidence_card_to_json_dict(card) for card in session.evidence_cards]
    upsert_profile_session_row(
        {
            "session_id": session.session_id,
            "user_id": session.user_id,
            "mode": session.mode.value,
            "current_step": 1 if session.current_step is not None else 0,
            "step_index": session.step_index,
            "finished": 1 if session.finished else 0,
            "persisted": 1 if session.persisted else 0,
            "step_data_json": json.dumps(_persistable_step_data(session.step_data), ensure_ascii=False),
            "evidence_cards_json": json.dumps(cards_payload, ensure_ascii=False),
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
        },
        db_path=db_path,
    )


def _session_from_row(row: dict[str, Any]) -> ProfileSession:
    mode = ProfileMode(str(row["mode"]))
    step_index = int(row["step_index"] or 0)
    finished = bool(row["finished"])
    persisted = bool(row.get("persisted", 0))
    step_data = _load_step_data(str(row.get("step_data_json") or "{}"))
    evidence_payload = json.loads(str(row["evidence_cards_json"] or "[]"))
    cards = [evidence_card_from_dict(item) for item in evidence_payload if isinstance(item, dict)]
    current_step = _step_for_index(mode, step_index, cards, step_data=step_data) if int(row["current_step"]) else None
    session = ProfileSession(
        session_id=str(row["session_id"]),
        user_id=str(row["user_id"]),
        mode=mode,
        current_step=current_step,
        step_index=step_index,
        step_data=step_data,
        evidence_cards=cards,
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
        finished=finished,
        persisted=persisted,
        persisted_db_path=str(row.get("db_path") or DB_PATH) if persisted else None,
    )
    if current_step and _is_quiz_step(current_step.id):
        session.step_data["questions"] = current_step.private_payload.get("questions", [])
    return session


def _persistable_step_data(step_data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in step_data.items() if not key.startswith("_")}


def _load_step_data(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _step_for_index(
    mode: ProfileMode,
    step_index: int,
    evidence_cards: list[EvidenceCard] | None = None,
    *,
    step_data: dict[str, Any] | None = None,
) -> ProfileStep | None:
    if mode == ProfileMode.apprentice:
        if 0 <= step_index < len(APPRENTICE_STEPS):
            return _apprentice_step(step_index, step_data=step_data)
        return None
    if mode == ProfileMode.dialogue:
        cards = evidence_cards or []
        data = step_data if step_data is not None else {}
        fusion = _dialogue_fusion(cards)
        asked_dimensions = _dialogue_step_list(data, "asked_dimensions")
        asked_questions = _dialogue_step_list(data, "asked_questions")
        target = _as_dialogue_dimension(data.get("dialogue_target")) or _dialogue_target(
            cards,
            fusion=fusion,
            asked_dimensions=asked_dimensions,
        )
        prompt = str(data.get("next_question") or "")
        if not prompt:
            prompt = _fallback_dialogue_question(
                target,
                step_index,
                asked_questions=asked_questions,
                asked_dimensions=asked_dimensions,
                evidence_cards=cards,
            )
            data["next_question"] = prompt
        _dialogue_remember(asked_questions, prompt)
        return _dialogue_turn_step(step_index + 1, prompt, target)
    return _initial_step(mode, step_data=step_data)


def load_diagnostic_questions(
    limit: int = 5,
    data_dir: Path | None = None,
    *,
    photo_ready_only: bool = False,
) -> list[dict[str, Any]]:
    """Build diagnostic questions from the herb knowledge files.

    The fallback is intentionally small and only used when the knowledge files
    are absent or malformed.
    """

    herbs = _load_herbs(data_dir)
    if not herbs:
        if photo_ready_only:
            return _photo_diagnostic_fallback_questions(limit)
        return FALLBACK_QUESTIONS[:limit]

    questions: list[dict[str, Any]] = []
    all_effects = sorted({effect for herb in herbs for effect in _string_list(herb.get("effects"))})
    all_names = sorted({str(herb.get("name", "")).strip() for herb in herbs if str(herb.get("name", "")).strip()})
    # Effects grouped by subcategory, so a question can offer confusable options
    # ("补气药的题就用补气药的功效当干扰") instead of the same three words every time.
    effects_by_subcategory: dict[str, list[str]] = {}
    for herb in herbs:
        bucket = str(herb.get("subcategory") or "").strip()
        if not bucket:
            continue
        effects_by_subcategory.setdefault(bucket, []).extend(_string_list(herb.get("effects")))
    for bucket, values in effects_by_subcategory.items():
        effects_by_subcategory[bucket] = sorted(set(values))
    # A new session gets a fresh set/order of herbs so the same answer path is
    # not memorised. Tests that need a stable fixture pass their own empty data
    # directory and use the explicit fallback list below.
    rng = random.SystemRandom()

    for herb in herbs:
        name = str(herb.get("name", "")).strip()
        herb_id = str(herb.get("id") or "")
        if photo_ready_only and herb_id not in DIAGNOSTIC_HERB_PHOTO_IDS:
            continue
        effects = _string_list(herb.get("effects"))
        if name and effects:
            answer = effects[0]
            # Every effect of this herb is a defensible answer, so none of them
            # may be used as a distractor - otherwise a correct answer gets
            # graded wrong.
            own = set(effects)
            local = [effect for effect in effects_by_subcategory.get(str(herb.get("subcategory") or "").strip(), []) if effect not in own]
            distractors = local if len(local) >= 3 else local + [effect for effect in all_effects if effect not in own]
            questions.append(
                {
                    "id": f"kb_{_safe_id(herb.get('id') or name)}_effect",
                    # 题面仅供非剧情入口使用（quickcard / 问诊），剧情题面由前端
                    # apprenticeQuizPrompt 渲染，因为剧情里不能出现药名。
                    "question": f"{name}的代表功效之一是？",
                    "options": _options(answer, distractors, rng),
                    "answer": answer,
                    "accepted_answers": effects,
                    "topic": f"{_category_label(herb)}·{name}功效",
                    "skill": "功效识记",
                    "herb_id": herb_id,
                    "herb_name": name,
                    "herb_hint": " · ".join([str(herb.get("nature") or "").strip(), "、".join(_string_list(herb.get("flavor"))) ]).strip(" ·"),
                }
            )
    selected = list(questions or (_photo_diagnostic_fallback_questions(limit) if photo_ready_only else FALLBACK_QUESTIONS))
    rng.shuffle(selected)
    return selected[:limit]


def _photo_diagnostic_fallback_questions(limit: int) -> list[dict[str, Any]]:
    """Keep the illustrated apprentice quiz usable when the herb KB is offline."""

    rows = [
        ("herb_taizishen", "太子参", "益气健脾", "平性 · 甘、微苦"),
        ("herb_dangshen", "党参", "补中益气", "平性 · 甘"),
        ("herb_huangqi", "黄芪", "补气升阳", "微温 · 甘"),
        ("herb_chenpi", "陈皮", "理气健脾", "温性 · 辛、苦"),
        ("herb_jinyinhua", "金银花", "清热解毒", "寒性 · 甘"),
    ]
    effects = [row[2] for row in rows]
    rng = random.SystemRandom()
    questions = []
    for herb_id, name, answer, hint in rows:
        questions.append(
            {
                "id": f"fallback_{_safe_id(herb_id)}_effect",
                "question": f"{name}的代表功效之一是？",
                "options": _options(answer, [effect for effect in effects if effect != answer], rng),
                "answer": answer,
                "accepted_answers": [answer],
                "topic": f"药性识记·{name}功效",
                "skill": "功效识记",
                "herb_id": herb_id,
                "herb_name": name,
                "herb_hint": hint,
            }
        )
    rng.shuffle(questions)
    return questions[:limit]


# 题型 → 证据落点。题面不是手写的，而是从药材知识图谱里派生出来的；这里
# 只声明"这类题能证明哪个组件"，所以加题不需要再加代码。
QUESTION_TYPES: dict[str, dict[str, str]] = {
    "effect": {
        "dimension": "knowledge_base",
        "component_tag": "measured_performance",
        "label": "代表功效",
    },
    "nature_flavor": {
        "dimension": "knowledge_base",
        "component_tag": "knowledge_scope",
        "label": "性味",
    },
    "meridian": {
        "dimension": "knowledge_base",
        "component_tag": "knowledge_scope",
        "label": "归经",
    },
    "caution": {
        "dimension": "knowledge_base",
        "component_tag": "boundary_awareness",
        "label": "使用注意",
    },
    "similar_herb": {
        "dimension": "weak_points",
        "component_tag": "application_correctness",
        "label": "相似药鉴别",
    },
}


def _build_herb_questions(limit: int = 3, data_dir: Path | None = None) -> list[dict[str, Any]]:
    """Derive 性味 / 使用注意 / 相似药 questions from the herb knowledge files.

    Every question carries its own ``component_tag``, so the answer lands in the
    right drawer without any per-question code.
    """

    herbs = _load_herbs(data_dir)
    if not herbs:
        return []
    rng = random.SystemRandom()
    picked = list(herbs)
    rng.shuffle(picked)
    questions: list[dict[str, Any]] = []

    nature_answers = sorted({
        f"{str(herb.get('nature') or '').strip()} · {'、'.join(_string_list(herb.get('flavor')))}".strip(" ·")
        for herb in herbs
        if str(herb.get("nature") or "").strip()
    })
    caution_answers = sorted({
        str(item).strip() for herb in herbs for item in _string_list(herb.get("cautions")) if str(item).strip()
    })
    by_subcategory: dict[str, list[str]] = {}
    for herb in herbs:
        bucket = str(herb.get("subcategory") or "").strip()
        name = str(herb.get("name") or "").strip()
        if bucket and name:
            by_subcategory.setdefault(bucket, []).append(name)

    # 每种题型各出一题（而不是每味药轮一遍），否则连出三道性味题，另外两个
    # 组件永远收集不到。
    used_herbs: set[str] = set()
    for question_type in ("nature_flavor", "caution", "similar_herb"):
        if len(questions) >= limit:
            break
        for herb in picked:
            name = str(herb.get("name") or "").strip()
            herb_id = str(herb.get("id") or _safe_id(name))
            if not name or herb_id in used_herbs:
                continue
            question = _herb_question(
                question_type,
                herb,
                rng=rng,
                nature_answers=nature_answers,
                caution_answers=caution_answers,
                by_subcategory=by_subcategory,
            )
            if question is None:
                continue
            questions.append(question)
            used_herbs.add(herb_id)
            break
    return questions[:limit]


def _herb_question(
    question_type: str,
    herb: dict[str, Any],
    *,
    rng: random.Random,
    nature_answers: list[str],
    caution_answers: list[str],
    by_subcategory: dict[str, list[str]],
) -> dict[str, Any] | None:
    name = str(herb.get("name") or "").strip()
    if not name:
        return None
    herb_id = _safe_id(str(herb.get("id") or name))
    spec = QUESTION_TYPES[question_type]
    if question_type == "nature_flavor":
        nature = str(herb.get("nature") or "").strip()
        if not nature:
            return None
        answer = f"{nature} · {'、'.join(_string_list(herb.get('flavor')))}".strip(" ·")
        return {
            "id": f"extra_nature_{herb_id}",
            "question": f"{name}的性味是？",
            "options": _options(answer, [item for item in nature_answers if item != answer], rng),
            "answer": answer,
            "accepted_answers": [answer],
            "topic": f"药性识记·{name}性味",
            "question_type": question_type,
            "dimension": spec["dimension"],
            "component": spec["component_tag"],
            "herb_name": name,
        }
    if question_type == "caution":
        cautions = _string_list(herb.get("cautions"))
        if not cautions:
            return None
        answer = str(cautions[0]).strip()
        return {
            "id": f"extra_caution_{herb_id}",
            "question": f"关于{name}的使用注意，下列哪条正确？",
            "options": _options(answer, [item for item in caution_answers if item != answer], rng),
            "answer": answer,
            "accepted_answers": [answer],
            "topic": f"用药安全·{name}使用注意",
            "question_type": question_type,
            "dimension": spec["dimension"],
            "component": spec["component_tag"],
            "herb_name": name,
        }
    bucket = str(herb.get("subcategory") or "").strip()
    siblings = [item for item in by_subcategory.get(bucket, []) if item != name]
    if not bucket or not siblings:
        return None
    answer = siblings[0]
    others = [item for names in by_subcategory.values() for item in names if item != name and item != answer]
    return {
        "id": f"extra_similar_{herb_id}",
        "question": f"下列哪味药和{name}同属{bucket}？",
        "options": _options(answer, others, rng),
        "answer": answer,
        "accepted_answers": [answer],
        "topic": f"相似药鉴别·{bucket}",
        "question_type": question_type,
        "dimension": spec["dimension"],
        "component": spec["component_tag"],
        "herb_name": name,
    }


# 剧情里除了辨药题，还要问"为什么学、怎么学"。这些不是药材知识图谱能派生的，
# 但每个维度都得凑够 3 个组件，否则覆盖度上不了 60%，整个维度不出分。
STORY_EXTRA_QUESTIONS: dict[str, tuple[dict[str, Any], ...]] = {
    "apprentice_prologue": (
        {
            "id": "extra_goal_scene",
            "kind": "text",
            "dimension": "learning_goal",
            "component": "goal_specificity",
            "prompt": "这次学中医，你主要是为了什么场景？大概什么时候要用上？",
        },
        {
            "id": "extra_goal_priority",
            "kind": "choice",
            "dimension": "learning_goal",
            "component": "priority_signal",
            "prompt": "如果这一个月只能先攻一块，你最想先拿下哪块？",
            "options": ["药性功效", "配伍剂量", "辨证思路", "背诵记忆"],
        },
    ),
    # 辨药题全答对时，"回忆错误"这一类证据根本不会产生，薄弱点就只剩两个组件、
    # 过不了 60% 门槛。所以这里再问一道跨情境题，让它不依赖答错也能出分。
    "apprentice_act1_quiz": (
        {
            "id": "extra_weak_cross_context",
            "kind": "text",
            "dimension": "weak_points",
            "component": "cross_context_stability",
            "prompt": "刚认过的这几味药，换到病例里换个说法，你还认得出来吗？举个你会犹豫的例子。",
        },
    ),
    "apprentice_act3_resource": (
        {
            "id": "extra_style_memory",
            "kind": "choice",
            "dimension": "learning_style",
            "component": "preference_clarity",
            "prompt": "记药性的时候，你更靠哪种方式？",
            "options": ["看图/图谱", "读文字", "背口诀"],
        },
        {
            "id": "extra_style_compare",
            "kind": "choice",
            "dimension": "learning_style",
            "component": "comparative_choice",
            "prompt": "同样讲配伍，图谱和短剧你更愿意看哪个？",
            "options": ["知识图谱", "拟人化短剧"],
        },
        {
            "id": "extra_resource_reason",
            "kind": "text",
            "dimension": "resource_preference",
            "component": "task_match_reason",
            "prompt": "为什么挑这件药宝？",
        },
        {
            "id": "extra_resource_constraint",
            "kind": "choice",
            "dimension": "resource_preference",
            "component": "availability_constraint",
            "prompt": "你平时更方便用哪种设备学？",
            "options": ["手机", "电脑", "平板", "纸质书"],
        },
    ),
    "apprentice_act4_rule": (
        {
            "id": "extra_pace_window",
            "kind": "choice",
            "dimension": "pace",
            "component": "time_feasibility",
            "prompt": "你一般什么时候能腾出这段时间？",
            "options": ["早起", "午休", "晚上", "通勤路上"],
        },
        {
            "id": "extra_pace_recover",
            "kind": "text",
            "dimension": "pace",
            "component": "adjustment_recovery",
            "prompt": "如果中间断了三天，你会怎么接上？",
        },
        {
            "id": "extra_motivation_conflict",
            "kind": "text",
            "dimension": "motivation",
            "component": "conflict_regulation",
            "prompt": "如果这周临时加班或考试撞车，你会先放下哪边？",
        },
        {
            "id": "extra_weak_recover",
            "kind": "text",
            "dimension": "weak_points",
            "component": "error_recovery",
            "prompt": "发现自己记错一味药时，你一般怎么纠正？",
        },
    ),
}


def _story_extra_cards(
    step_id: str,
    payload: dict[str, Any],
    *,
    source: str,
) -> list[EvidenceCard]:
    """Turn the extra story questions into evidence cards.

    A choice answer is a measured fact; a free-text answer is a self report that
    the local rules deliberately do not grade.
    """

    answers = payload.get("story_answers")
    if not isinstance(answers, dict):
        answers = {}
    cards: list[EvidenceCard] = []
    for definition in STORY_EXTRA_QUESTIONS.get(step_id, ()):
        raw = answers.get(str(definition["id"]))
        value = str(raw).strip() if isinstance(raw, (str, int, float)) else ""
        if not value:
            continue
        kind = str(definition.get("kind") or "text")
        component = str(definition["component"])
        dimension = str(definition["dimension"])  # type: ignore[arg-type]
        prompt = str(definition["prompt"])
        signal: dict[str, Any] = {
            "prompt": prompt,
            "selected": value if kind == "choice" else None,
            "free_text": value if kind != "choice" else None,
            "quote": value,
            "basis": "explicit",
            "confidence": 0.9 if kind == "choice" else 0.75,
            "topic": prompt,
        }
        cards.append(
            _card(
                dimension,  # type: ignore[arg-type]
                source,
                f"{prompt} → {value}",
                signal,
                1.0,
                component=component,
                answer_kind="choice" if kind == "choice" else "self_report",
            )
        )
    return cards


async def extract_prescription_signal(payload: dict[str, Any]) -> dict[str, bool]:
    """Return deterministic prescription checks for backwards-compatible callers."""

    llm_signal = await _try_llm_prescription_extract(payload)
    if llm_signal is not None:
        return llm_signal
    evaluation = evaluate_prescription(payload)
    signal = evaluation.signal()
    return {key: bool(signal[key]) for key in ("\u5bf9\u75c7", "\u914d\u4f0d", "\u5242\u91cf", "\u8bef\u7528\u6bd2\u70c8")}


def session_payload(session: ProfileSession, status: str) -> dict[str, Any]:
    """Stable API shape for session responses."""

    payload = {
        "session_id": session.session_id,
        "user_id": session.user_id,
        "mode": session.mode.value,
        "status": status,
        "step": session.current_step.to_dict() if session.current_step else None,
        "current_stage": session.current_stage,
        "finished": session.finished,
        "evidence_count": len(session.evidence_cards),
    }
    if session.mode == ProfileMode.dialogue and session.step_data.get("goal_proposal"):
        payload["goal_proposal"] = session.step_data["goal_proposal"]
    return payload


def cards_to_json(cards: list[EvidenceCard]) -> list[dict[str, Any]]:
    return [evidence_card_to_json_dict(card) for card in cards]


def _initial_step(mode: ProfileMode, *, step_data: dict[str, Any] | None = None) -> ProfileStep:
    if mode == ProfileMode.apprentice:
        return _apprentice_step(0, step_data=step_data)
    if mode == ProfileMode.quickcard:
        # Same reason as the story quiz: the pool is random, so it has to be
        # drawn once and kept, otherwise every reload asks a different paper.
        cached = (step_data or {}).get("questions")
        if isinstance(cached, list) and cached:
            questions = cached
        else:
            questions = load_diagnostic_questions(limit=5)
            if step_data is not None:
                step_data["questions"] = questions
        return ProfileStep(
            id="quickcard",
            title=STEP_LABELS["quickcard"],
            kind="identity_quiz",
            prompt="选择身份卡，并完成30秒速判。",
            payload={"identity_cards": list(IDENTITY_CARDS), "questions": _public_questions(questions), "time_limit_seconds": 30},
            private_payload={"questions": questions},
        )
    if mode == ProfileMode.practice:
        return ProfileStep(
            id="practice",
            title=STEP_LABELS["practice"],
            kind="prescription",
            prompt="根据病例提交开药方案。",
            payload={"case": "外感或热毒类病例，可提交 prescription/herbs。"},
        )
    if mode == ProfileMode.dialogue:
        return _dialogue_turn_step(1, DIALOGUE_OPENING_PROMPT, "learning_goal")
    return ProfileStep(
        id="passive",
        title=STEP_LABELS["passive"],
        kind="events",
        prompt="提交点击、停留、重看、跳过等被动事件。",
        payload={"event_types": ["click", "stay", "replay", "skip"]},
    )


def _next_step(session: ProfileSession) -> ProfileStep | None:
    if session.mode == ProfileMode.apprentice:
        session.step_index += 1
        if session.step_index >= len(APPRENTICE_STEPS):
            return None
        return _apprentice_step(session.step_index, step_data=session.step_data)
    if session.mode == ProfileMode.dialogue:
        session.step_index += 1
        fusion = session.step_data.get("_dialogue_fusion")
        if not isinstance(fusion, dict):
            fusion = _dialogue_fusion(session.evidence_cards)
        asked_dimensions = _dialogue_step_list(session.step_data, "asked_dimensions")
        asked_questions = _dialogue_step_list(session.step_data, "asked_questions")
        target = _as_dialogue_dimension(session.step_data.get("dialogue_target")) or _dialogue_target(
            session.evidence_cards,
            fusion=cast(dict[DimensionKey, FusionResult], fusion),
            asked_dimensions=asked_dimensions,
        )
        prompt = str(session.step_data.get("next_question") or "")
        if not prompt:
            prompt = _fallback_dialogue_question(
                target,
                session.step_index,
                asked_questions=asked_questions,
                asked_dimensions=asked_dimensions,
                evidence_cards=session.evidence_cards,
            )
        return _dialogue_turn_step(
            session.step_index + 1,
            prompt,
            target,
        )
    return None


def _apprentice_step(index: int, *, step_data: dict[str, Any] | None = None) -> ProfileStep:
    step_id = APPRENTICE_STEPS[index]
    extras = list(STORY_EXTRA_QUESTIONS.get(step_id, ()))
    if step_id == "apprentice_prologue":
        return ProfileStep(
            id=step_id,
            title=STEP_LABELS[step_id],
            kind="identity",
            prompt="选择你进入药圃前的身份卡。",
            payload={"identity_cards": list(IDENTITY_CARDS), "story_questions": _public_story_questions(extras)},
        )
    if step_id == "apprentice_act1_quiz":
        # 题库是随机的，必须只抽一次并存进会话：重载时重新抽会换一套题，
        # 客户端答的是 A 套、服务端拿 B 套比对，correct 恒为 False，
        # 性味/使用注意/相似药三个落点全部退化成"实测表现"。
        cached = (step_data or {}).get("questions")
        if isinstance(cached, list) and cached:
            questions = cached
        else:
            questions = load_diagnostic_questions(limit=5, photo_ready_only=True)
            # 性味 / 使用注意 / 相似药三类题从知识图谱派生，补足知识基础与薄弱点的
            # 组件覆盖度；题面仍然由前端渲染。
            questions = questions + _build_herb_questions(limit=3)
            if step_data is not None:
                step_data["questions"] = questions
        return ProfileStep(
            id=step_id,
            title=STEP_LABELS[step_id],
            kind="diagnostic_quiz",
            prompt="辨药5题，并记录每题反应时长。",
            payload={"questions": _public_questions(questions), "story_questions": _public_story_questions(extras)},
            private_payload={"questions": questions},
        )
    if step_id == "apprentice_act2_prescription":
        return ProfileStep(
            id=step_id,
            title=STEP_LABELS[step_id],
            kind="prescription",
            prompt="根据病例抓药，系统只抽取客观信号。",
            payload={
                "objective_signal_fields": ["对症", "配伍", "剂量", "误用毒烈"],
                "story_questions": _public_story_questions(extras),
            },
        )
    if step_id == "apprentice_act3_resource":
        return ProfileStep(
            id=step_id,
            title=STEP_LABELS[step_id],
            kind="resource_choice",
            prompt="挑一件师父药宝带走。",
            payload={"resource_types": sorted(RESOURCE_TYPES), "story_questions": _public_story_questions(extras)},
        )
    return ProfileStep(
        id=step_id,
        title=STEP_LABELS[step_id],
        kind="learning_rule",
        prompt="约定每日时长、目标周期与挑战意愿。",
        payload={
            "fields": ["daily_minutes", "target_days", "accept_challenge"],
            "story_questions": _public_story_questions(extras),
        },
    )


def _public_story_questions(definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Client view: prompt + options only. The component tag stays server side."""

    return [
        {
            "id": item["id"],
            "kind": item.get("kind", "text"),
            "prompt": item["prompt"],
            "options": list(item.get("options") or []),
        }
        for item in definitions
    ]


async def _answer_apprentice(session: ProfileSession, payload: dict[str, Any], response_time_ms: int) -> list[EvidenceCard]:
    step_id = session.current_step.id if session.current_step else ""
    source = STEP_LABELS.get(step_id, step_id)
    extras = _story_extra_cards(step_id, payload, source=source)
    if step_id == "apprentice_prologue":
        return _identity_cards(payload, source=source, response_time_ms=response_time_ms, prior_weight=0.3) + extras
    if step_id == "apprentice_act1_quiz":
        return _quiz_cards(payload, session.step_data.get("questions", []), source=source, response_time_ms=response_time_ms) + extras
    if step_id == "apprentice_act2_prescription":
        prescription_card = await _prescription_card(payload, source=source, weight=1.5, response_time_ms=response_time_ms)
        cards = [prescription_card]
        preference_note = str(payload.get("preference_note") or "").strip()
        preferences = [str(item).strip() for item in _as_list(payload.get("preferences")) if str(item).strip()]
        if preference_note or preferences:
            preference_text = preference_note or "、".join(preferences)
            cards.append(
                _card(
                    "learning_style",
                    source,
                    f"偏好的辨证法门：{preference_text}",
                    {
                        "preferences": preferences,
                        "preference_note": preference_note or None,
                        "quote": preference_note or preference_text,
                        "basis": "explicit",
                        "confidence": 0.8,
                    },
                    1.0,
                    component="strategy_execution",
                )
            )
        return cards
    if step_id == "apprentice_act3_resource":
        return [_resource_card(payload, source=source, response_time_ms=response_time_ms)] + extras
    if step_id == "apprentice_act4_rule":
        return _learning_rule_cards(payload, source=source, response_time_ms=response_time_ms) + extras
    return []


async def _answer_quickcard(session: ProfileSession, payload: dict[str, Any], response_time_ms: int) -> list[EvidenceCard]:
    cards = _identity_cards(payload, source=STEP_LABELS["quickcard"], response_time_ms=response_time_ms, prior_weight=0.3)
    quiz_cards = _quiz_cards(payload, session.step_data.get("questions", []), source="30秒速判", response_time_ms=response_time_ms)
    elapsed = _int_or_none(payload.get("elapsed_seconds"))
    if elapsed is not None:
        for card in quiz_cards:
            card.objective_signal["elapsed_seconds"] = elapsed
    return cards + quiz_cards


async def _answer_dialogue(session: ProfileSession, payload: dict[str, Any]) -> list[EvidenceCard]:
    utterance = str(payload.get("utterance") or payload.get("quick_reply") or payload.get("answer") or "").strip()
    if not utterance:
        utterance = "暂时说不清"
    turn_index = session.step_index + 1
    response_time_ms = _response_time_ms(payload, session.current_step.started_at if session.current_step else None)
    asked_dimensions = _dialogue_step_list(session.step_data, "asked_dimensions")
    if not asked_dimensions:
        asked_dimensions.extend(_dialogue_targets_from_cards(session.evidence_cards))
    asked_questions = _dialogue_step_list(session.step_data, "asked_questions")
    if not asked_questions:
        asked_questions.extend(_dialogue_questions_from_cards(session.evidence_cards))
    current_prompt = session.current_step.prompt if session.current_step else ""
    _dialogue_remember(asked_questions, current_prompt)
    fusion_before = _dialogue_fusion(session.evidence_cards)
    target = _dialogue_target(session.evidence_cards, fusion=fusion_before)
    dialogue_history = _dialogue_history_for_turn(session.step_data, current_prompt=current_prompt, utterance=utterance)
    asked_dimensions.append(target)
    extraction, next_question = await _dialogue_extract_and_question(
        utterance=utterance,
        dialogue_history=dialogue_history,
        target=target,
        turn_index=turn_index,
        evidence_cards=session.evidence_cards,
        current_confidences=_dialogue_confidences(session.evidence_cards, fusion=fusion_before),
    )
    if isinstance(extraction, dict):
        from app.profile.nodes import DialogueExtraction

        extraction = DialogueExtraction.model_validate(extraction)
    cards = _dialogue_cards_from_extraction(extraction)
    session.step_data["_last_component_contract"] = {
        "turn_id": extraction.turn_id or turn_index,
        "combination_type": extraction.combination_type,
        "extraction_error": extraction.extraction_error,
        "extraction_method": extraction.extraction_method,
        "dimension_scan": [item.model_dump(mode="json") for item in extraction.dimension_scan],
        "component_collection": [item.model_dump(mode="json") for item in extraction.component_collection],
        "evidence_ids": [item.evidence_id for item in extraction.atomic_evidence],
    }
    for card in cards:
        card.objective_signal["turn_index"] = turn_index
        card.objective_signal["response_time_ms"] = response_time_ms
        card.objective_signal["dialogue_target"] = target
        card.objective_signal["asked_question"] = current_prompt

    projected_cards = [*session.evidence_cards, *cards]
    fusion = _dialogue_fusion(projected_cards)
    next_target = _dialogue_target(projected_cards, fusion=fusion, asked_dimensions=asked_dimensions)
    selected_next_question = next_question if next_question and next_question not in asked_questions else ""
    if not selected_next_question and _has_profile_extract_key():
        selected_next_question = await _try_llm_dialogue_question(
            utterance=utterance,
            current_target=target,
            next_target=next_target,
            turn_index=turn_index,
            evidence_cards=projected_cards,
            current_confidences=_dialogue_confidences(projected_cards, fusion=fusion),
        )
        if selected_next_question in asked_questions:
            selected_next_question = ""
    if not selected_next_question:
        selected_next_question = _fallback_dialogue_question(
            next_target,
            turn_index,
            asked_questions=asked_questions,
            asked_dimensions=asked_dimensions,
            evidence_cards=projected_cards,
        )
    _dialogue_remember(asked_questions, selected_next_question)
    session.step_data["next_question"] = selected_next_question
    _store_dialogue_history(session.step_data, dialogue_history, assistant_text=selected_next_question)
    for card in cards:
        card.objective_signal["next_question"] = selected_next_question
    session.step_data["dialogue_target"] = next_target
    session.step_data["_dialogue_fusion"] = fusion
    return cards


async def _dialogue_extract_and_question(
    *,
    utterance: str,
    dialogue_history: str | None = None,
    target: DimensionKey,
    turn_index: int,
    evidence_cards: list[EvidenceCard],
    current_confidences: dict[str, float] | None = None,
) -> tuple[Any, str]:
    source_text = dialogue_history or utterance
    user_source_text = _dialogue_user_source_text(source_text, latest_utterance=utterance)
    if _has_profile_extract_key():
        combined = await _try_llm_dialogue_turn(utterance, target, turn_index, evidence_cards, current_confidences or {}, dialogue_history=source_text)
        if combined is not None:
            return combined
    from app.profile.nodes import DialogueExtraction

    # Never turn an unavailable extractor into a keyword-driven pseudo-agent.
    # The UI can honestly show that no semantic evidence was collected yet.
    return DialogueExtraction(
        extraction_method="rule_fallback" if _has_profile_extract_key() else "no_key_rule_fallback"
    ), ""


EXTRACTION_MAX_TOKENS = 8192

# 实测（真实模型 8 轮）：抽取输出到 1.6–2.3 万字符时会被 max_tokens 截断，
# 断在半截 JSON 上，`_parse_json_object` 拿不到对象，整轮降级成 rule_fallback、
# 0 条证据——用户看到"小岐答了话但画像一点没动"。截断在客户端无法修复，只能
# 换一份更小的输出重问一次：丢掉 legacy 字段与七维组件清单，只保留证据本身。
COMPACT_RETRY_SUFFIX = (
    "\n上一次输出被长度上限截断，整份 JSON 作废。这一次只输出三个顶层字段，"
    "不要输出 component_collection，不要输出 major/learning_goal 等 legacy 字段："
    '{"turn_id":N,"dimension_scan":[{"dimension","status","reason","components"}],'
    '"atomic_evidence":[{"evidence_id","dimension","component","evidence_group_id",'
    '"source_turn_ids","quote","normalized_claim","evidence_type","collection_status",'
    '"semantic_relation","quality_factors","quality_reason","quality_thinking"}]}。'
    "atomic_evidence 最多 4 条；reason 每维不超过 20 字，不要复述量表原文。"
)


async def _retry_compact_extraction(messages: list[dict[str, Any]], raw: str) -> dict[str, Any] | None:
    """输出被截断时，用"只留证据"的精简提示重问一次。"""

    if not raw or len(raw) < 4000:
        # 太短的坏输出不是截断，重问也只会再坏一次。
        return None
    compact_messages = [*messages, {"role": "user", "content": COMPACT_RETRY_SUFFIX}]
    try:
        raw_retry = await ModelRouter().chat(
            "profile_extract",
            compact_messages,
            response_format={"type": "json_object"},
            temperature=0,
            thinking={"type": "disabled"},
            max_tokens=EXTRACTION_MAX_TOKENS,
        )
    except Exception as exc:  # noqa: BLE001 - 重试失败就保持原降级结果
        logger.warning("compact extraction retry failed: %s", exc)
        return None
    parsed = _parse_json_object(raw_retry)
    return parsed if isinstance(parsed, dict) else None


async def _try_llm_dialogue_turn(
    utterance: str,
    target: DimensionKey,
    turn_index: int,
    evidence_cards: list[EvidenceCard],
    current_confidences: dict[str, float],
    *,
    dialogue_history: str | None = None,
) -> tuple[Any, str] | None:
    source_text = dialogue_history or utterance
    user_source_text = _dialogue_user_source_text(source_text, latest_utterance=utterance)
    prompt_payload = {
        "turn_index": turn_index,
        "target_dimension": target,
        "target_label": DIALOGUE_TARGET_LABELS.get(target, target),
        "latest_user_utterance": utterance,
        "dialogue_history": source_text,
        "current_confidences": current_confidences,
        "allowed_labels": {
            "learning_goal": list(ALLOWED_LABELS["learning_goal"]),
            "base_level": ["零基础", "入门", "一般", "较好", "熟练"],
            "learning_style": ["视觉型", "文字型", "记忆(口诀)型"],
            "motivation": ["内驱", "外驱", "内外混合", "被动"],
            "resource_preference": sorted(RESOURCE_TYPES),
        },
        "components": COMPONENTS,
        "component_rubrics": COMPONENT_RUBRICS,
    }
    messages = [
        {
            "role": "system",
            "content": (
                "你是学习画像证据抽取器，只输出 JSON。阅读整段对话，为每个维度抽取标签/数值，"
                "并给出用户【原话片段】作为 evidence。没有依据的维度必须 value=null，禁止猜测或填默认。"
                "target_dimension 只说明本轮原本想了解什么，绝不限制提取范围：用户即使答非所问，也必须扫描并提取全部七维线索。"
                "审计只展示可复核的判断摘要，不输出私有思维链。dimension_scan 每维必须给简明依据；"
                "另返回 excluded_candidates 数组，记录确实考虑过但不成立的维度/组件候选及排除理由，未考虑的候选不要臆造。"
                "一句回答可同时形成多个维度的多张牌，分别保留可追溯原话。不得创造 allowed_labels 之外的新标签。"
                "对每条已有原话的维度附 assessment，由你依据上下文判断，不得用词语到数值的固定映射。"
                "assessment 包含 radar_score(5..95，非0/100的当前雷达估计)、confidence_contribution(0..0.95)、"
                "coverage(0..1)、priority_strength(0..1)、independence(0..1)、unknown_reasons、assessment_reason。"
                "它分别表达该维度的状态和已确认程度：提到单一目标不等于不存在其他目标，必须在未知项中说明。"
                "同时返回 confirmed_aspects，列出本句实际确认的组成。明确的计划或意愿可以有很高的事实提取把握，"
                "但不自动证明行为稳定或整维已充分确认；请根据上下文识别仍缺少的组成，不得采用词语到分数的固定映射。"
                "learning_goal 落不进应试提分/临床实用/兴趣科普/学术深造时，使用“其他”，并仍需原话 evidence。"
                "例如‘我想治病救人’是明确的学习目标方向（学习医学以帮助患者），可支持目标表达确定性和宽泛目标具体度；"
                "帮助他人的价值也可能是内在动机线索，但不能据此推断长期投入、行动计划、专业资格或目标优先级。"
                "优先输出完整 component_contract：dimension_scan 扫描全部七维；atomic_evidence 为每条可评分原子证据，"
                "每条必须包含 evidence_id、dimension、component、evidence_group_id、source_turn_ids、quote(用户原话子串)、"
                "normalized_claim、evidence_type、supports、collection_status、assessment、confirmed_aspects、pending_aspects、"
                "semantic_relation(new/reinforcement/semantic_duplicate/conflict)、related_evidence_ids、"
                "quality_factors、quality_reason 与 quality_thinking。\n"
                + QUALITY_FACTOR_GUIDE
                + "\n"
                "component_collection 必须列出七维全部组件；没有证据时写 no_usable_evidence/pending 及原因，不能用低分代替未提及。"
            ),
        },
        {
            "role": "user",
            "content": (
                json.dumps(prompt_payload, ensure_ascii=False)
                + "\n输出一个扁平 JSON 对象，legacy 字段与审计字段放在同一层；不要嵌套 extraction。字段包含 major,learning_goal,base_level,learning_style,weak_points,motivation,"
                "motivation_score,resource_preference,daily_minutes,target_days,mastery_hint；"
                "其中每个字段都是 {value,confidence,basis,evidence,card_names,assessment}；assessment 为 "
                "{radar_score,confidence_contribution,coverage,priority_strength,independence,confirmed_aspects,unknown_reasons,assessment_reason}。"
                "learning_goal/motivation 的 value 可为标签分布对象。"
                "weak_points 只抽用户自述的易错、薄弱、困惑知识点原文短句，没有原文证据则 value=null。"
                "并在同一 JSON 顶层返回 turn_id、dimension_scan(每项含 excluded_candidates:[{dimension,component,reason}])、"
                "atomic_evidence、component_collection；组件名必须来自系统提供的组件量表。不要省略扫描明细。"
            ),
        },
    ]
    try:
        raw = await ModelRouter().chat(
            "profile_extract",
            messages,
            response_format={"type": "json_object"},
            temperature=0,
            thinking={"type": "disabled"},
            max_tokens=EXTRACTION_MAX_TOKENS,
        )
        parsed = _parse_json_object(raw)
        compact_retry = False
        if not isinstance(parsed, dict):
            # 半截 JSON：换精简输出重问一次，别让整轮证据凭空消失。
            parsed = await _retry_compact_extraction(messages, raw)
            if isinstance(parsed, dict):
                compact_retry = True
        from app.profile.nodes import DialogueExtraction

        if not isinstance(parsed, dict):
            return DialogueExtraction(
                extraction_method="rule_fallback",
                extraction_error=_dialogue_model_response_failure(raw),
            ), ""
        # Older adapters wrapped legacy signals in `extraction` while returning
        # the component audit contract beside it. Merge both shapes so the
        # full-dimension scan is not silently discarded before validation.
        nested = parsed.get("extraction") if isinstance(parsed.get("extraction"), dict) else {}
        extraction_payload = {**nested, **{key: value for key, value in parsed.items() if key != "extraction"}}
        extraction_payload = _normalize_dialogue_extraction_payload(extraction_payload)
        from app.profile.nodes import _validate_dialogue_evidence

        extraction = _validate_dialogue_payload_leniently(extraction_payload)
        extraction = _validate_dialogue_evidence(extraction, user_source_text)
        if compact_retry:
            # 审计要能看出"这一轮是截断后重问拿到的证据"，而不是正常抽取。
            extraction.extraction_method = "llm_compact_retry"
        return extraction, ""
    except Exception as exc:
        from app.profile.nodes import DialogueExtraction

        return DialogueExtraction(
            extraction_method="rule_fallback",
            extraction_error=f"model_schema_or_request_error:{type(exc).__name__}",
        ), ""


def _validate_dialogue_payload_leniently(payload: dict[str, Any]) -> Any:
    """Validate one turn's extraction without letting a single bad item void it.

    Measured on the real model: eight of ten dialogue turns produced zero
    evidence because one malformed legacy field or one malformed
    ``atomic_evidence`` entry failed the whole model.  The evidence is per-item
    data, so validation has to be per-item too -- drop what cannot be parsed,
    keep what can, and record why.
    """

    from app.profile.nodes import AtomicEvidence, ComponentCollection, DialogueExtraction, DimensionScan

    try:
        return DialogueExtraction.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 - only the reason is recorded
        legacy_error = type(exc).__name__

    contract: dict[str, Any] = {
        key: payload[key]
        for key in ("turn_id", "dimension_scan", "atomic_evidence", "component_collection")
        if key in payload
    }
    # 这两个标量字段模型也会写错（字符串轮次号、自造的组合类型）。它们只影响
    # 置信度区间选择，写错就丢掉用默认值，不该让整轮证据归零。
    turn_id = contract.get("turn_id")
    if not isinstance(turn_id, int) or isinstance(turn_id, bool):
        contract.pop("turn_id", None)
    combination = payload.get("combination_type")
    if combination in get_args(CombinationType):
        contract["combination_type"] = combination
    models: tuple[tuple[str, Any], ...] = (
        ("dimension_scan", DimensionScan),
        ("atomic_evidence", AtomicEvidence),
        ("component_collection", ComponentCollection),
    )
    for key, model in models:
        items = contract.get(key)
        if not isinstance(items, list):
            contract.pop(key, None)
            continue
        kept: list[Any] = []
        for item in items:
            try:
                kept.append(model.model_validate(item))
            except Exception:  # noqa: BLE001 - a bad entry is dropped, not fatal
                continue
        contract[key] = kept
    try:
        extraction = DialogueExtraction.model_validate(contract)
    except Exception as exc:  # noqa: BLE001
        return DialogueExtraction(
            extraction_method="rule_fallback",
            extraction_error=f"model_schema_or_request_error:{type(exc).__name__}",
        )
    extraction.extraction_error = f"legacy_fields_invalid_preserved_component_contract:{legacy_error}"
    return extraction


def _canonical_dimension_name(value: Any) -> Any:
    """Map the legacy dimension names the model still emits onto canonical keys."""

    return canonical_dimension_name(value)


def _normalize_dialogue_extraction_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Adapt common model JSON shapes to the typed contract without semantic inference.

    Models sometimes serialize a keyed map instead of a list, use a textual
    rubric tier instead of its numeric quality field, or return a reason in a
    legacy `basis` slot. This adapter only normalizes those representations;
    it never selects dimensions/components from the user's words.
    """

    data = dict(payload)
    # Preserve the model's explanation when older output placed it in `basis`;
    # the enum itself is normalized to the safe, non-assertive `inferred` value.
    signal_keys = (
        "major", "learning_goal", "base_level", "learning_style", "weak_points",
        "motivation", "motivation_score", "resource_preference", "daily_minutes",
        "target_days", "mastery_hint",
    )
    for key in signal_keys:
        signal = data.get(key)
        if not isinstance(signal, dict):
            continue
        signal = dict(signal)
        basis = signal.get("basis")
        if basis not in {"explicit", "inferred"}:
            explanation = str(basis or "").strip()
            assessment = signal.get("assessment")
            if explanation and isinstance(assessment, dict):
                assessment = dict(assessment)
                assessment.setdefault("assessment_reason", explanation[:160])
                signal["assessment"] = assessment
            signal["basis"] = "inferred"
        data[key] = signal

    scan = data.get("dimension_scan")
    if isinstance(scan, dict):
        normalized_scan = []
        for dimension, raw in scan.items():
            item = dict(raw) if isinstance(raw, dict) else {"reason": str(raw or "")}
            item.setdefault("dimension", dimension)
            item["dimension"] = _canonical_dimension_name(item.get("dimension"))
            evidence = item.get("evidence")
            if not item.get("reason") and evidence:
                item["reason"] = "; ".join(map(str, evidence)) if isinstance(evidence, list) else str(evidence)
            scan_status = str(item.get("status") or "").strip().lower()
            # 模型偶尔用中文写状态（"有证据"/"无可用证据"）。不映射就会被
            # 兜底成 pending，审计里明明抽到了证据却显示"待确认"。
            if scan_status in {"partial_evidence", "found", "evidence_found", "有证据", "部分证据", "已找到"}:
                item["status"] = "evidence_found"
            elif scan_status in {"no_usable_evidence", "not_found", "no_evidence", "无证据", "无可用证据", "未找到"}:
                item["status"] = "no_usable_evidence"
            elif scan_status != "pending":
                item["status"] = "pending"
            if not item.get("reason"):
                item["reason"] = "模型未返回该维度的可追溯原子证据；当前仅标记待取证，不推断为低分。"
            normalized_scan.append(item)
        data["dimension_scan"] = normalized_scan
    elif isinstance(scan, list):
        normalized_scan = []
        for raw in scan:
            if not isinstance(raw, dict):
                normalized_scan.append(raw)
                continue
            item = dict(raw)
            item["dimension"] = _canonical_dimension_name(item.get("dimension"))
            status = str(item.get("status") or "").strip().lower()
            if status in {"partial_evidence", "found"}:
                item["status"] = "evidence_found"
            elif status in {"not_found", "no_evidence"}:
                item["status"] = "no_usable_evidence"
            if not item.get("reason"):
                evidence = item.get("evidence")
                item["reason"] = "; ".join(map(str, evidence)) if isinstance(evidence, list) and evidence else "模型未提供扫描理由；当前仅标记待取证。"
            normalized_scan.append(item)
        data["dimension_scan"] = normalized_scan

    collection = data.get("component_collection")
    if isinstance(collection, dict):
        normalized_collection = []
        for dimension, components in collection.items():
            if not isinstance(components, dict):
                continue
            for component, raw in components.items():
                item = dict(raw) if isinstance(raw, dict) else {"status": raw}
                item.update({"dimension": _canonical_dimension_name(dimension), "component": component})
                status = str(item.get("status") or "").strip().lower()
                if status in {"confirmed", "collected", "valid", "scorable"}:
                    item["status"] = "scorable"
                elif status == "conflict":
                    item["status"] = "conflict"
                else:
                    item["status"] = "pending"
                normalized_collection.append(item)
        data["component_collection"] = normalized_collection

    atomic = data.get("atomic_evidence")
    if isinstance(atomic, list):
        normalized_atomic = []
        for raw in atomic:
            if not isinstance(raw, dict):
                normalized_atomic.append(raw)
                continue
            item = dict(raw)
            item["dimension"] = _canonical_dimension_name(item.get("dimension"))
            supports = item.get("supports")
            if isinstance(supports, str):
                item["supports"] = [supports] if supports.strip() else []
            status = str(item.get("collection_status") or "").strip().lower()
            if status in {"confirmed", "collected", "valid", "scorable"}:
                item["collection_status"] = "scorable"
            elif status in {"not_mentioned", "not_collected", "待收集", "待确认"}:
                item["collection_status"] = "pending"
            elif status == "conflicted":
                item["collection_status"] = "conflict"
            elif status not in {"pending", "conflict", "invalid"}:
                item["collection_status"] = "pending"
            assessment = item.get("assessment")
            if isinstance(assessment, dict):
                reason = assessment.get("assessment_reason") or assessment.get("reason") or assessment.get("summary")
                item["assessment"] = str(reason or json.dumps(assessment, ensure_ascii=False))[:500]
            factors = item.get("quality_factors")
            if isinstance(factors, dict):
                # Accept descriptive tiers produced by models when numeric
                # quality values are required by the public confidence formula.
                tier_values = {"high": 0.9, "medium": 0.65, "low": 0.3, "高": 0.9, "中": 0.65, "低": 0.3}
                aliases = {
                    "source_reliability": ("source_reliability", "reliability"),
                    "expression_clarity": ("expression_clarity", "clarity", "directness"),
                    "content_specificity": ("content_specificity", "specificity"),
                    "freshness": ("freshness", "recency"),
                    "consistency": ("consistency", "coherence"),
                }
                normalized_factors: dict[str, Any] = {}
                for target, candidates in aliases.items():
                    value = next((factors[name] for name in candidates if name in factors), None)
                    if isinstance(value, str):
                        value = tier_values.get(value.strip().lower(), tier_values.get(value.strip()))
                    if isinstance(value, (int, float)) and 0 <= float(value) <= 1:
                        normalized_factors[target] = float(value)
                    elif target == "freshness":
                        # The source turn is the current turn unless the model
                        # explicitly supplies an age/recency assessment.
                        normalized_factors[target] = 1.0
                    else:
                        normalized_factors[target] = 0.5
                normalized_factors["independence_note"] = str(
                    factors.get("independence_note") or factors.get("independence") or "模型未提供独立性说明；同轮证据共享来源组"
                )
                item["quality_factors"] = normalized_factors
            normalized_atomic.append(item)
        data["atomic_evidence"] = normalized_atomic
    return data


def _dialogue_model_response_failure(raw: Any) -> str:
    """Return a bounded, non-sensitive extraction failure code for the audit."""

    response = str(raw or "")
    fallback = re.search(r"\[FallbackReason:([A-Za-z0-9_]+)\]", response)
    if fallback:
        return f"provider_fallback_to_mock:{fallback.group(1)}"
    if response.lstrip().startswith("[MOCK:"):
        return "mock_model_response_not_semantic_json"
    if "模型服务暂时不可用" in response:
        return "provider_unavailable"
    return "model_output_not_valid_json"


async def _try_llm_dialogue_question(
    *,
    utterance: str,
    current_target: DimensionKey,
    next_target: DimensionKey,
    turn_index: int,
    evidence_cards: list[EvidenceCard],
    current_confidences: dict[str, float],
) -> str:
    prompt_payload = {
        "turn_index": turn_index,
        "previous_target_dimension": current_target,
        "next_target_dimension": next_target,
        "next_target_label": DIALOGUE_TARGET_LABELS.get(next_target, next_target),
        "component_focus": _dialogue_component_focus(evidence_cards, next_target),
        "current_confidences": current_confidences,
        "recent_evidence": [
            {
                "dimension": card.dimension,
                "signal": card.objective_signal,
                "weight": card.weight,
            }
            for card in evidence_cards[-8:]
        ],
        "last_user_utterance": utterance,
    }
    messages = [
        {
            "role": "system",
            "content": (
                "You are a dialogue-based learning profile interviewer. "
                "Extract evidence only; never assign final profile scores. "
                "Generate exactly one concise, natural next question. "
                "Prefer a natural question that helps next_target_dimension, but do not imply the user's answer is limited to it; "
                "when component_focus exists, ask specifically and naturally about that missing component before changing dimensions; "
                "any later answer will be assessed across all seven dimensions. Do not output analysis."
            ),
        },
        {
            "role": "user",
            "content": (
                json.dumps(prompt_payload, ensure_ascii=False)
                + "\nOutput JSON only: {\"next_question\": string}"
            ),
        },
    ]
    try:
        raw = await ModelRouter().chat("profile_extract", messages, response_format={"type": "json_object"}, temperature=0.7)
        parsed = _parse_json_object(raw)
        if not isinstance(parsed, dict):
            return ""
        return str(parsed.get("next_question") or "").strip()
    except Exception:
        return ""


def _dialogue_cards_from_extraction(extraction: Any) -> list[EvidenceCard]:
    from app.profile.nodes import _dialogue_cards

    return _dialogue_cards(extraction)


def _has_profile_extract_key() -> bool:
    settings = get_settings()
    return bool((settings.deepseek_api_key and settings.deepseek_api_key.strip()) or (settings.zhipu_api_key and settings.zhipu_api_key.strip()))


def _dialogue_turn_step(turn_number: int, prompt: str, target: DimensionKey | str) -> ProfileStep:
    return ProfileStep(
        id=f"dialogue_turn_{turn_number}",
        title=STEP_LABELS["dialogue"],
        kind="dialogue",
        prompt=prompt,
        payload={
            "turn_index": turn_number,
            "target": target,
            "quick_replies": _dialogue_quick_replies(target),
        },
    )


def _dialogue_confirm_step(evidence_cards: list[EvidenceCard], *, fusion: dict[DimensionKey, FusionResult] | None = None) -> ProfileStep:
    card_scores, inferred_card = _identity_card_scores(evidence_cards, fusion=fusion)
    return ProfileStep(
        id="dialogue_confirm",
        title=STEP_LABELS["dialogue_confirm"],
        kind="confirm",
        prompt="我已经抓到一版画像线索了，确认后就可以生成并保存。",
        payload={"card_scores": card_scores, "inferred_card": inferred_card},
    )


def _dialogue_fusion(evidence_cards: list[EvidenceCard]) -> dict[DimensionKey, FusionResult]:
    fused = fuse_profile(
        evidence_cards,
        confidence_weight_k=DIALOGUE_CONFIDENCE_WEIGHT_K,
        confidence_weight_dimensions=DIALOGUE_PRIMARY_DIMENSIONS,
    )
    # Dialogue has one public scoring contract: a dimension only receives a
    # radar value after its coverage gate opens. The arithmetic itself lives in
    # ``dimension_score`` so this flow and the story flow cannot drift apart.
    for dimension, result in fused.items():
        required = COMPONENTS[dimension]
        dimension_cards = [card for card in evidence_cards if card.dimension == dimension]
        collected = {
            card.component for card in dimension_cards
            if card.component in required and card.collection_status == "scorable"
        }
        scores: dict[str, float] = {}
        for card in dimension_cards:
            component = card.component
            if component not in required:
                continue
            try:
                score = float(card.objective_signal.get("component_score"))
            except (TypeError, ValueError):
                continue
            if 5 <= score <= 95:
                scores[component] = score
        outcome = dimension_score(dimension, scores)
        gate = {
            "collected": len(collected),
            "total": len(required),
            "coverage": outcome["coverage"],
            "weighted_mean": outcome["weighted_mean"],
            "missing_components": [component for component in required if component not in collected],
            "unscored_components": list(outcome["missing"]),
            "component_scores": outcome["component_scores"],
            "scoring_phase": "scored" if outcome["gate_ok"] else "collecting",
        }
        metadata = {**result.metadata, "component_collection": gate}
        if outcome["gate_ok"]:
            fused[dimension] = replace(
                result,
                score=outcome["score"],
                radar_score=outcome["score"],
                coverage=outcome["coverage"],
                metadata=metadata,
                narrative=(
                    f"{DIMENSION_LABELS[dimension]}已评分 {len(scores)}/{len(required)} 个组件"
                    f"（覆盖 {outcome['coverage']:.0%}），维度分 {outcome['score']}。"
                ),
            )
        else:
            fused[dimension] = replace(
                result, score=None, radar_score=None, label=None, distribution={},
                coverage=outcome["coverage"], metadata=metadata,
                narrative=(
                    f"{DIMENSION_LABELS[dimension]}已评分 {len(scores)}/{len(required)} 个组件"
                    f"（覆盖 {outcome['coverage']:.0%}），未达 60%，评分暂缓。"
                ),
            )
    return fused


# 35 个组件各配一个「意图锚点」。提示词只给意思和角度，措辞交给模型；本地事后
# 用 guard_keywords 检查这轮有没有真的问到那一格，全挂才退回固定问句。
# askable=False 的那 7 个正是 REQUIRES_MEASUREMENT 里的：只有答题或长期行为才能
# 观察到，聊天里问也填不上，问了就是白烧一轮。
COMPONENT_ANCHORS: dict[DimensionKey, dict[str, dict[str, Any]]] = {
    "knowledge_base": {
        "concept_foundation": {
            "must_cover": "这味药的功效和它的性味之间有什么关系",
            "angles": ["你觉得它为什么能起这个作用？跟性味对得上吗？", "同为补气药，它和另一味差在哪？", "只给你性味，你能推出它大概治什么吗？"],
            "extractable": "说出性味与功效之间的因果，或点出同类药的区别",
            "guard_keywords": ("性味", "苦", "甘", "辛", "温", "寒", "所以", "因为"),
        },
        "knowledge_scope": {
            "must_cover": "除了这几味，还知道哪个类别或哪几味药",
            "angles": ["你还认识哪些别的类别的药？", "解表药里你熟悉哪几味？", "除了补虚药，你还系统看过哪一类？"],
            "extractable": "说出一个类别名，或点出两味以上同类之外的药",
            "guard_keywords": ("解表", "清热", "补虚", "化痰", "活血", "类", "还知道", "别的"),
        },
        "retrieval_application": {
            "must_cover": "给一个具体症状，先想到哪味药、为什么",
            "angles": ["有人怕冷又没汗，你会先想到哪味药？", "咳嗽有黄痰，你第一反应用哪味？", "给你一个症状，你会怎么从记忆里把它捞出来？"],
            "extractable": "给出药名并说明取舍理由",
            "guard_keywords": ("先想到", "会用", "第一反应", "选", "因为", "怕冷", "咳嗽", "痰"),
        },
        "boundary_awareness": {
            "must_cover": "这味药什么情况下不能用，容易和哪味混",
            "angles": ["这味药有什么情况是不能用的？", "它和哪味药最容易记混？", "用错了会出什么问题？"],
            "extractable": "说出一个禁忌、一个易混药或一个误用后果",
            "guard_keywords": ("不能用", "禁忌", "忌", "容易混", "记混", "孕妇", "误用"),
        },
        "measured_performance": {
            "askable": False,
            "must_cover": "",
            "angles": [],
            "extractable": "",
            "guard_keywords": (),
        },
    },
    "learning_goal": {
        "expression_certainty": {
            "must_cover": "这件事是已经定了，还是还在考虑",
            "angles": ["学中医这件事你是已经定了，还是在看看？", "这个目标你跟别人说过吗，还是先自己想想？", "你有多确定要按这个方向走？"],
            "extractable": "明确表态「已定/在考虑」，或给出确定性线索",
            "guard_keywords": ("定了", "确定", "已经决定", "还在考虑", "看看", "没想好"),
        },
        "goal_specificity": {
            "must_cover": "到什么程度算学好了，打算多久做到",
            "angles": ["到什么程度你觉得算是学好了？", "你打算多久做到这一步？", "有没有一个能拿出来验收的结果？"],
            "extractable": "给出可验收的标准，或一个明确的时间点",
            "guard_keywords": ("学好", "程度", "多久", "个月", "考试", "能做到", "标准"),
        },
        "action_orientation": {
            "must_cover": "最近为学中医做过的一件具体事，或准备从哪一步开始",
            "angles": ["这阵子有没有为学中医做过点什么？", "如果明天就开始，你打算先干哪件事？", "之前试过什么办法，管用吗？"],
            "extractable": "提到一个具体动作，加时间或频率",
            "guard_keywords": ("准备", "开始", "做过", "计划", "试过", "每天", "这周"),
        },
        "priority_signal": {
            "must_cover": "两件事冲突时先放哪个",
            "angles": ["如果背药和做题只能选一个，你先做哪个？", "时间不够时，你会砍掉哪一块？", "这几件事里哪件最不能拖？"],
            "extractable": "在两项之间做出明确取舍并给理由",
            "guard_keywords": ("先", "优先", "砍掉", "选", "最不能拖", "放一放"),
        },
        "cross_turn_stability": {
            "must_cover": "复述一次目标，看和前几轮说的是不是一回事",
            "angles": ["前面你说想先把常用药性弄扎实，现在还是这个打算吗？", "这几天目标有变化吗？", "把你想达到的状态再说一遍？"],
            "extractable": "复述出的目标与前几轮一致，或明确说明改了",
            "guard_keywords": ("还是", "没变", "一样", "改了", "调整为", "依然是"),
        },
    },
    "learning_style": {
        "preference_clarity": {
            "must_cover": "平时怎么记东西最牢，为什么",
            "angles": ["你平时记东西，哪种方式最牢？", "看图和读文字，哪个进脑子快？", "背口诀对你有用吗？"],
            "extractable": "点明一种方式，并说明为什么适合自己",
            "guard_keywords": ("看图", "读", "背", "口诀", "笔记", "抄", "最牢", "记得住"),
        },
        "cross_task_consistency": {
            "must_cover": "换个学习任务，方法会不会变",
            "angles": ["背药性和读方子，你会用同一种方法吗？", "换成学辨证，你还这么学吗？", "方法会跟着任务变吗？"],
            "extractable": "说明方法在不同任务间保持一致或如何调整",
            "guard_keywords": ("一样", "不变", "换", "会变", "同样", "看情况"),
        },
        "comparative_choice": {
            "must_cover": "视频和看书二选一，为什么",
            "angles": ["视频和看书，你选哪个？", "两种资料差在哪？", "只能留一种，你留哪种？"],
            "extractable": "做出二选一，并说出两者的差别",
            "guard_keywords": ("选", "更", "比", "差别", "不如", "还是"),
        },
        "strategy_execution": {
            "askable": False,
            "must_cover": "这个方法实际用了多久，效果怎么样",
            "angles": ["这个方法你实际用了多久了？", "用了之后效果怎么样？", "是偶尔用还是一直在用？"],
            "extractable": "给出使用时长，加一个效果判断",
            "guard_keywords": ("用了", "坚持", "多久", "一直", "偶尔", "效果"),
        },
        "effect_fit": {
            "must_cover": "这个方法哪部分管用、哪部分不管用",
            "angles": ["这个方法哪部分对你最管用？", "哪部分其实没什么用？", "哪一步你老是跳过？"],
            "extractable": "分别指出有效和无效的部分",
            "guard_keywords": ("管用", "没用", "有效", "跳过", "卡在", "不管用"),
        },
    },
    "weak_points": {
        "recall_correctness": {
            "must_cover": "回忆一味药的性味归经，看能不能说全",
            "angles": ["你回忆一下黄芪的性味归经？", "这味药的归经是哪几条？", "不看资料，你能说全吗？"],
            "extractable": "给出性味或归经的具体内容，或明确说记不全",
            "guard_keywords": ("性味", "归经", "味", "性", "记不全", "想不起来"),
        },
        "application_correctness": {
            "must_cover": "给一个病例，你会怎么配",
            "angles": ["遇到这个证，你会怎么配？", "这组药里你会加哪味？", "剂量上你会怎么定？"],
            "extractable": "给出配伍组合或剂量思路",
            "guard_keywords": ("配", "加", "剂量", "克", "g", "组合", "一起用"),
        },
        "cross_context_stability": {
            "must_cover": "同一味药换个问法，还会不会",
            "angles": ["同一味药我换个问法，你还认得吗？", "如果病人表现不一样，你还会用它吗？", "换个场景你还会选它吗？"],
            "extractable": "在换过的问法下仍给出正确或一致的判断",
            "guard_keywords": ("还是会", "一样", "也会", "照样", "不一定", "看情况"),
        },
        "error_recovery": {
            "must_cover": "刚才说的和前面有冲突，能看出是哪边吗",
            "angles": ["你刚才说的和前面有点不一样，是哪边？", "发现记错了，你一般怎么改过来？", "错了一次之后你会怎么补？"],
            "extractable": "识别出矛盾并给出更正，或说明纠错办法",
            "guard_keywords": ("记错", "改", "更正", "应该是", "说错", "补"),
        },
        "measured_validation": {
            "askable": False,
            "must_cover": "",
            "angles": [],
            "extractable": "",
            "guard_keywords": (),
        },
    },
    "pace": {
        "plan_clarity": {
            "must_cover": "打算怎么安排，一天多少，怎么检查",
            "angles": ["你打算怎么安排每天的学习？", "一天大概多少量？", "怎么知道自己有没有做到？"],
            "extractable": "给出频次或数量，加一个检查方式",
            "guard_keywords": ("每天", "一天", "分钟", "小时", "检查", "打卡", "复习"),
        },
        "time_feasibility": {
            "must_cover": "一天真正能拿出多少时间，什么时候",
            "angles": ["一天里你真正能拿出多少时间？", "一般是什么时段？", "这个时间稳定吗？"],
            "extractable": "给出一个具体时段和时长",
            "guard_keywords": ("早上", "晚上", "中午", "通勤", "睡前", "分钟", "小时"),
        },
        "actual_execution": {
            "askable": False,
            "must_cover": "已经坚持多久，中间断过没有",
            "angles": ["这个安排你坚持多久了？", "中间断过吗？", "最近一周做到了几天？"],
            "extractable": "给出已执行时长或近期完成天数",
            "guard_keywords": ("坚持", "已经", "天", "周", "断过", "做到", "没断"),
        },
        "sustained_period": {
            "must_cover": "这件事你保持多久了",
            "angles": ["学中医这件事你保持多久了？", "是从什么时候开始的？", "中间有停过一段时间吗？"],
            "extractable": "给出持续时长或起始时间",
            "guard_keywords": ("开始", "半年", "几个月", "一直", "从", "持续"),
        },
        "adjustment_recovery": {
            "must_cover": "中间断过吗，断了之后怎么接上",
            "angles": ["断了之后你一般怎么接上？", "有没有过连着几天没学？", "重新开始的时候难吗？"],
            "extractable": "描述一次中断以及恢复方式",
            "guard_keywords": ("断", "接上", "恢复", "重新", "补", "接着"),
        },
    },
    "motivation": {
        "intrinsic_value": {
            "must_cover": "中医里哪部分让你觉得有意思",
            "angles": ["中医里哪块让你觉得有意思？", "有没有哪个瞬间让你觉得这事挺带劲？", "抛开考试，你自己想弄懂什么？"],
            "extractable": "点出一个具体让他在意的内容",
            "guard_keywords": ("有意思", "好奇", "喜欢", "想弄懂", "着迷", "带劲"),
        },
        "external_commitment": {
            "must_cover": "有没有跟谁说过、报名或交钱这类动作",
            "angles": ["这事你跟谁说过吗？", "有没有报班或者买课？", "有没有人盯着你学？"],
            "extractable": "给出一个对外承诺或已付费的动作",
            "guard_keywords": ("报名", "买", "交钱", "跟他说", "答应", "打卡群", "老师"),
        },
        "actual_investment": {
            "askable": False,
            "must_cover": "为这件事已经花了多少时间和钱",
            "angles": ["为这事你已经花了多少时间？", "买书买课花了多少？", "到现在投入算多还是少？"],
            "extractable": "给出已投入的时间或金额",
            "guard_keywords": ("花了", "买了", "投入", "块", "元", "小时", "本"),
        },
        "persistence_under_obstacle": {
            "askable": False,
            "must_cover": "学的时候卡住过吗，当时怎么处理的",
            "angles": ["学的时候卡住过吗？", "卡住那次你怎么办的？", "有没有想过放弃？"],
            "extractable": "描述一次受阻以及当时的应对",
            "guard_keywords": ("卡", "放弃", "难", "看不懂", "问", "查", "硬啃"),
        },
        "conflict_regulation": {
            "must_cover": "学习和其他事撞了，怎么排",
            "angles": ["学习和工作撞了，你先顾哪个？", "家里有事那几天你还学吗？", "怎么给自己腾出时间？"],
            "extractable": "给出一个明确的排序规则",
            "guard_keywords": ("先", "排", "让", "腾", "加班", "忙", "顾"),
        },
    },
    "resource_preference": {
        "preference_specificity": {
            "must_cover": "具体用什么资料，为什么用这个",
            "angles": ["你具体用什么资料学？", "为什么用这个而不是别的？", "是哪本书或者哪个课？"],
            "extractable": "点出具体资料名或形态，加选择理由",
            "guard_keywords": ("书", "课", "视频", "图谱", "公众号", "教材", "APP", "用"),
        },
        "task_match_reason": {
            "must_cover": "背药和读方会不会用同一种资料，为什么",
            "angles": ["背药和读方，你会用同一种资料吗？", "为什么这两件事要用不一样的？", "哪种资料更适合哪一种任务？"],
            "extractable": "说明任务与资料形态的匹配理由",
            "guard_keywords": ("背", "读方", "适合", "因为", "不一样", "对应"),
        },
        "comparative_choice": {
            "must_cover": "书和视频更用哪个，两者差在哪",
            "angles": ["书和视频你更用哪个？", "两者差在哪？", "只能留一种你留哪种？"],
            "extractable": "做出二选一，并说出差异",
            "guard_keywords": ("选", "更", "比", "差别", "不如", "还是"),
        },
        "availability_constraint": {
            "must_cover": "买书、看视频这些有没有不方便的地方",
            "angles": ["买书看视频这些有什么不方便？", "有没有找不到或者太贵的情况？", "设备或网络上有卡点吗？"],
            "extractable": "指出一个可获得性上的限制",
            "guard_keywords": ("贵", "找不到", "没时间", "不方便", "网络", "手机", "设备"),
        },
        "usage_effect_feedback": {
            "askable": False,
            "must_cover": "这些资料用下来哪部分管用，有没有换过",
            "angles": ["这些资料用下来哪部分管用？", "有没有换过资料？为什么换？", "哪部分你基本没打开过？"],
            "extractable": "给出使用效果判断，或一次更换经历",
            "guard_keywords": ("管用", "没用", "换过", "打开", "效果", "弃"),
        },
    },
}


def _dialogue_component_focus(
    evidence_cards: list[EvidenceCard],
    dimension: DimensionKey,
    *,
    asked_counts: dict[str, int] | None = None,
) -> dict[str, Any] | None:
    collected = {
        card.component for card in evidence_cards
        if card.dimension == dimension and card.component in COMPONENTS[dimension] and card.collection_status == "scorable"
    }
    weights = COMPONENT_WEIGHTS[dimension]
    # Ask about the heaviest answerable hole first: filling it moves the score
    # the most, and it is the one the jury will actually weigh.  Holes that
    # already ate two questions without filling are handed off (see
    # ``DIALOGUE_COMPONENT_ASK_LIMIT``).
    candidates = _dialogue_askable_holes(dimension, collected, asked_counts)
    if not candidates:
        return None
    missing = max(candidates, key=lambda key: weights[key])
    rubric = COMPONENT_RUBRICS[dimension][missing]
    anchor = COMPONENT_ANCHORS.get(dimension, {}).get(missing, {})
    return {
        "component": missing,
        "label": DIMENSION_COMPONENT_LABELS[dimension][missing],
        "reason": str(rubric["followup_direction"]),
        "must_cover": str(anchor.get("must_cover") or rubric["followup_direction"]),
        "angles": list(anchor.get("angles") or ()),
        "extractable": str(anchor.get("extractable") or ""),
        "guard_keywords": list(anchor.get("guard_keywords") or ()),
    }


# Dialogue can stop once learning target and motivation are stable: they drive the next
# learning route, while knowledge_base is lightweight self-report in this mode
# and should not block a useful early confirmation.
DIALOGUE_COVERAGE_THRESHOLD = COVERAGE_THRESHOLD


def _dialogue_coverage(evidence_cards: list[EvidenceCard]) -> dict[DimensionKey, float]:
    """How many of each dimension's 5 components have a scorable card.

    Only collected components count: a dimension cannot be judged "done" on
    evidence that never landed on a component, because that evidence cannot
    enter the scoring path either.
    """

    coverage: dict[DimensionKey, float] = {}
    for dimension, components in COMPONENTS.items():
        collected = {
            card.component
            for card in evidence_cards
            if card.dimension == dimension
            and card.component in components
            and card.collection_status == "scorable"
        }
        coverage[dimension] = len(collected) / len(components)
    return coverage


def _dialogue_converged(
    turn_index: int,
    evidence_cards: list[EvidenceCard],
    *,
    fusion: dict[DimensionKey, FusionResult] | None = None,
) -> bool:
    """Stop only when something can be scored and nothing is half-baked.

    Confidence is not the criterion any more: a dimension can be talked about a
    lot and still be one component short of the 60% gate, in which case it would
    end up unscored.  A "half-baked" dimension (0 < coverage < 60%) is exactly
    that case, so the dialogue keeps going until it is either filled or empty.
    """

    del fusion  # kept for call-site compatibility; coverage is what decides now
    if turn_index < DIALOGUE_MIN_TURNS:
        return False
    coverage = _dialogue_coverage(evidence_cards)
    if not any(value >= DIALOGUE_COVERAGE_THRESHOLD for value in coverage.values()):
        return False
    return not any(0 < value < DIALOGUE_COVERAGE_THRESHOLD for value in coverage.values())


def _dialogue_collected_components(dimension: DimensionKey, evidence_cards: list[EvidenceCard]) -> set[str]:
    return {
        card.component for card in evidence_cards
        if card.dimension == dimension and card.component in COMPONENTS[dimension] and card.collection_status == "scorable"
    }


# 同一格最多问两轮。实测 10 轮对话里有 5 轮都在问同一个"中药里哪块让你觉得
# 有意思"，因为那一格一直填不上、软锁定又一直把它选回来，只是每轮换个说法。
# 问满两轮还没答出来，就说明用户要么没理解、要么不愿说，再问第三轮是白烧预算，
# 应该让位给别的格子（或别的维度）。
DIALOGUE_COMPONENT_ASK_LIMIT = 2


def _dialogue_asked_components(step_data: dict[str, Any]) -> dict[str, int]:
    """How many turns each component has already been asked about."""

    raw = step_data.get("asked_components")
    if not isinstance(raw, dict):
        return {}
    counts: dict[str, int] = {}
    for key, value in raw.items():
        try:
            counts[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return counts


def _dialogue_note_asked_component(step_data: dict[str, Any], focus: dict[str, Any] | None) -> None:
    """Record that this turn spends its question on ``focus``'s component."""

    component = str((focus or {}).get("component") or "")
    if not component:
        return
    counts = _dialogue_asked_components(step_data)
    counts[component] = counts.get(component, 0) + 1
    step_data["asked_components"] = counts


def _dialogue_askable_holes(
    dimension: DimensionKey,
    collected: set[str],
    asked_counts: dict[str, int] | None,
) -> list[str]:
    unmeasurable = REQUIRES_MEASUREMENT.get(dimension, frozenset())
    anchors = COMPONENT_ANCHORS.get(dimension, {})
    holes = [
        component for component in COMPONENTS[dimension]
        if component not in collected
        and component not in unmeasurable
        and anchors.get(component, {}).get("askable", True)
    ]
    if not asked_counts:
        return holes
    fresh = [
        component for component in holes
        if asked_counts.get(component, 0) < DIALOGUE_COMPONENT_ASK_LIMIT
    ]
    # 问满两轮就真的让位，不再"退回原集合"。实测这一条会把整个对话钉死：
    # 节奏可持续度只剩「坚持了多久」一个可问的洞，模型连续 6 轮把它换个说法
    # 重问（"你保持多久了""中间有停过吗""是从什么时候开始的"）。那一维停在
    # 4/5 = 0.8 仍然过 60% 门槛、照样能出分，硬问第六遍只是折磨用户。
    return fresh


def _dialogue_answerable_holes(
    dimension: DimensionKey,
    evidence_cards: list[EvidenceCard],
    *,
    asked_counts: dict[str, int] | None = None,
) -> list[str]:
    """Components of this dimension that conversation can still fill."""

    return _dialogue_askable_holes(
        dimension,
        _dialogue_collected_components(dimension, evidence_cards),
        asked_counts,
    )


def _dialogue_soft_lock(
    current: DimensionKey | None,
    evidence_cards: list[EvidenceCard],
    *,
    stalled: bool = False,
    component_ask_counts: dict[str, int] | None = None,
) -> bool:
    """Keep asking the current dimension unless one of the switch triggers fires.

    Jumping to whatever drawer is fullest was the reason a dimension could be
    asked about and then abandoned one component short of the 60% gate.  The
    four documented triggers are: no answerable hole left, another dimension two
    or more components ahead, two stalled turns, and a dimension already full.
    """

    if current is None or stalled:
        return False
    if not _dialogue_answerable_holes(current, evidence_cards, asked_counts=component_ask_counts):
        return False
    mine = len(_dialogue_collected_components(current, evidence_cards))
    ahead = max(
        (len(_dialogue_collected_components(other, evidence_cards)) for other in COMPONENTS if other != current),
        default=0,
    )
    return ahead < mine + 2


def _dialogue_stalled(session: ProfileSession) -> bool:
    state = session.step_data.get("dialogue_stall")
    return isinstance(state, dict) and int(state.get("rounds", 0)) >= 2


def _dialogue_stalled_dimension(session: ProfileSession) -> DimensionKey | None:
    """The dimension that has gone two turns without a new component."""

    if not _dialogue_stalled(session):
        return None
    state = session.step_data.get("dialogue_stall")
    dimension = _as_dialogue_dimension((state or {}).get("dimension"))
    return cast(DimensionKey, dimension) if dimension else None


def _dialogue_note_stall(session: ProfileSession, dimension: DimensionKey) -> None:
    """Count consecutive turns on one dimension that added no new component."""

    collected = len(_dialogue_collected_components(dimension, session.evidence_cards))
    state = session.step_data.get("dialogue_stall")
    if isinstance(state, dict) and state.get("dimension") == dimension and int(state.get("collected", -1)) == collected:
        rounds = int(state.get("rounds", 1)) + 1
    else:
        rounds = 1
    session.step_data["dialogue_stall"] = {"dimension": dimension, "collected": collected, "rounds": rounds}


def _dialogue_target(
    evidence_cards: list[EvidenceCard],
    *,
    fusion: dict[DimensionKey, FusionResult] | None = None,
    asked_dimensions: list[str] | None = None,
    current_target: DimensionKey | None = None,
    stalled: bool = False,
    stalled_dimension: DimensionKey | None = None,
    component_ask_counts: dict[str, int] | None = None,
) -> DimensionKey:
    if _dialogue_soft_lock(
        current_target,
        evidence_cards,
        stalled=stalled,
        component_ask_counts=component_ask_counts,
    ):
        return cast(DimensionKey, current_target)
    fused = fusion or _dialogue_fusion(evidence_cards)
    asked = asked_dimensions or []
    recent = _as_dialogue_dimension(asked[-1]) if asked else None
    asked_counts = {dimension: asked.count(dimension) for dimension in DIALOGUE_CONFIDENCE_THRESHOLDS}
    partially_collected = []
    for index, dimension in enumerate(COMPONENTS):
        collected = _dialogue_collected_components(dimension, evidence_cards)
        if collected and len(collected) < len(COMPONENTS[dimension]):
            partially_collected.append((dimension, len(collected), len(COMPONENTS[dimension]), index))
    # 卡住的那一维必须先出局，否则"先补完半满的维度"这条规则会把它自己选回来：
    # 它半满、它比率最高、于是永远选它，软锁定也拦不住（实测连问 6 轮）。
    if stalled_dimension and len(partially_collected) > 1:
        without_stalled = [item for item in partially_collected if item[0] != stalled_dimension]
        if without_stalled:
            partially_collected = without_stalled
    # 已经问不出一格的维度也不该当目标：它的可问洞都被问满了，选它只会让这一轮
    # 没有问句（focus 为空），等于白白浪费一次提问机会。
    askable = [
        item for item in partially_collected
        if _dialogue_answerable_holes(item[0], evidence_cards, asked_counts=component_ask_counts)
    ]
    if askable:
        partially_collected = askable
    if partially_collected:
        return max(
            partially_collected,
            key=lambda item: (item[1] / item[2], item[1], -asked_counts.get(item[0], 0), -item[3]),
        )[0]
    below_threshold = [
        dimension
        for dimension, threshold in DIALOGUE_CONFIDENCE_THRESHOLDS.items()
        if fused[dimension].confidence < threshold
    ]
    if not below_threshold:
        below_threshold = list(DIALOGUE_CONFIDENCE_THRESHOLDS)
    if stalled_dimension and len(below_threshold) > 1:
        without_stalled = [dimension for dimension in below_threshold if dimension != stalled_dimension]
        if without_stalled:
            below_threshold = without_stalled
    stale_dimensions = [
        dimension
        for dimension in below_threshold
        if asked_counts.get(dimension, 0) >= DIALOGUE_TARGET_RETRY_LIMIT
    ]
    if stale_dimensions:
        fresh_below = [dimension for dimension in below_threshold if dimension not in stale_dimensions]
        if fresh_below:
            below_threshold = fresh_below
        else:
            fallback_dimensions = [
                dimension
                for dimension in DIALOGUE_CONFIDENCE_THRESHOLDS
                if dimension not in stale_dimensions
            ]
            if fallback_dimensions:
                below_threshold = fallback_dimensions

    return min(
        below_threshold,
        key=lambda dimension: (
            fused[dimension].confidence / max(DIALOGUE_CONFIDENCE_THRESHOLDS[dimension], 0.001),
            asked_counts.get(dimension, 0),
            1 if dimension == recent else 0,
            list(DIALOGUE_CONFIDENCE_THRESHOLDS).index(dimension),
        ),
    )


def _fallback_dialogue_question(
    target: DimensionKey | str,
    turn_index: int,
    *,
    asked_questions: list[str] | None = None,
    asked_dimensions: list[str] | None = None,
    evidence_cards: list[EvidenceCard] | None = None,
    focus: dict[str, Any] | None = None,
) -> str:
    asked = asked_questions if asked_questions is not None else []
    asked_set = set(asked)
    recent_question = asked[-1] if asked else ""
    target_dimension = _as_dialogue_dimension(target) or "learning_goal"
    # 调用方已经算好这一轮该问哪一格时就直接用：自己重算会绕过"问满两次
    # 就让位"的账本，把刚放弃的格子又问回来。
    focus = focus or _dialogue_component_focus(evidence_cards or [], target_dimension)
    if focus:
        focused_questions = {
            "expression_certainty": "这是你已经比较确定的方向，还是还在考虑中的想法？",
            "goal_specificity": "学医救人对你来说，最希望实现的具体变化是什么？",
            "action_orientation": "你为这个方向做过哪些准备，或者准备从哪一步开始？",
            "priority_signal": "如果这个目标和其他重要安排冲突，你通常会怎样取舍时间和精力？",
            "cross_turn_stability": "这个想法持续多久了？最近有没有发生变化？",
        }
        question = focused_questions.get(focus["component"]) or ""
        if not question:
            # 模板句会把维度标签和"追问"这类内部措辞带进正文（实测出现
            # "关于知识范围，追问除了这几味…"这种读不通的句子）。锚点里已经
            # 写好自然问法，优先用它们，模板只作最后兜底。
            question = next(
                (item for item in focus.get("angles") or () if item and item not in asked_set),
                "",
            )
        if not question:
            # 兜底也别用"关于{维度标签}，追问{方向}"这种模板：它会把维度名和
            # "追问"带进正文（实测出现"关于持续周期，追问这件事你保持多久了"）。
            # 锚点的 must_cover 本身就是一句自然的问法，直接拿来用。
            cover = str(focus.get("must_cover") or "").strip().rstrip("。？?！!，,")
            question = f"{cover}？" if cover else "能就这一点再具体说说吗？"
        if question not in asked_set:
            _dialogue_remember(asked, question)
            return question
    question = _first_unused_dialogue_question(target_dimension, turn_index, asked_set)
    if question is None:
        covered = set(asked_dimensions or [])
        dimensions = [
            dimension
            for dimension in DIALOGUE_FALLBACK_QUESTIONS
            if dimension != target_dimension and dimension not in covered
        ]
        dimensions.extend(dimension for dimension in DIALOGUE_FALLBACK_QUESTIONS if dimension != target_dimension and dimension not in dimensions)
        for dimension in dimensions:
            question = _first_unused_dialogue_question(dimension, turn_index, asked_set)
            if question is not None:
                break
    if question is None:
        all_questions = [item for questions in DIALOGUE_FALLBACK_QUESTIONS.values() for item in questions]
        question = next((item for item in all_questions if item != recent_question), all_questions[0])
    _dialogue_remember(asked, question)
    return question


def _first_unused_dialogue_question(dimension: DimensionKey, turn_index: int, asked_questions: set[str]) -> str | None:
    questions = DIALOGUE_FALLBACK_QUESTIONS.get(dimension, ())
    if not questions:
        return None
    for offset in range(len(questions)):
        question = questions[(turn_index + offset) % len(questions)]
        if question not in asked_questions:
            return question
    return None


def _as_dialogue_dimension(value: Any) -> DimensionKey | None:
    text = str(value or "")
    if text in DIALOGUE_TARGET_LABELS:
        return cast(DimensionKey, text)
    return None


def _dialogue_step_list(step_data: dict[str, Any], key: str) -> list[str]:
    value = step_data.get(key)
    if isinstance(value, list):
        cleaned = [str(item) for item in value if str(item)]
    else:
        cleaned = []
    step_data[key] = cleaned
    return cleaned


def _dialogue_remember(items: list[str], item: str | None) -> None:
    text = str(item or "").strip()
    if text and text not in items:
        items.append(text)


def _dialogue_targets_from_cards(evidence_cards: list[EvidenceCard]) -> list[str]:
    targets: list[str] = []
    for card in evidence_cards:
        target = _as_dialogue_dimension(card.objective_signal.get("dialogue_target"))
        if target is not None:
            targets.append(target)
    return targets


def _dialogue_questions_from_cards(evidence_cards: list[EvidenceCard]) -> list[str]:
    questions: list[str] = []
    for card in evidence_cards:
        _dialogue_remember(questions, str(card.objective_signal.get("asked_question") or ""))
        _dialogue_remember(questions, str(card.objective_signal.get("next_question") or ""))
    return questions


def _dialogue_quick_replies(target: DimensionKey | str) -> list[str]:
    if target == "learning_goal":
        return ["考试提分", "临床实用", "科研深造", "兴趣入门"]
    if target == "motivation":
        return ["自己想学", "考试推动", "实习需要", "先看看"]
    if target == "knowledge_base":
        return ["刚入门", "学过药性", "能做辨证", "基础不稳"]
    if target == "learning_style":
        return ["先看图谱", "读教材条文", "用口诀记忆", "结合案例理解"]
    if target == "pace":
        return ["每天15分钟", "每天30分钟", "每天45分钟", "周末集中学习"]
    if target == "resource_preference":
        return ["知识图谱", "短剧讲解", "汤头歌诀", "医案拆解"]
    return ["我想具体说说", "先从基础开始", "帮我找薄弱点"]


def _dialogue_quick_replies_for_question(question: str, target: DimensionKey | str) -> list[str]:
    """Return chips that answer the visible question, before dimension defaults.

    A profile dimension is too broad for questions such as “四诊合参还是套证型”.
    These narrowly scoped patterns keep one-tap answers honest; an unmatched
    conversational question deliberately has no chips rather than stale ones.
    """
    text = str(question or "")
    if any(term in text for term in ("四诊", "合参", "证型", "辨证")):
        return ["会四诊合参", "常按证型判断", "两种都会一些", "还在练习"]
    if any(term in text for term in ("药性", "功效", "归经")):
        return ["能说出功效", "能联系归经", "记忆还不稳", "想从例子学"]
    if any(term in text for term in ("病例", "医案", "临床")):
        return ["愿意拆病例", "先学辨证思路", "想练方药配伍", "还没接触过"]
    if any(term in text for term in ("分钟", "每天", "节奏", "时间")):
        return ["每天15分钟", "每天30分钟", "每天45分钟", "周末集中学习"]
    if any(term in text for term in ("图谱", "教材", "口诀", "资源", "喜欢怎么学", "学习方式", "学习偏好", "记忆")):
        return ["知识图谱", "教材条文", "口诀歌诀", "案例拆解"]
    if any(term in text for term in ("目标", "想学什么", "想达到", "最想", "学习目的")):
        return _dialogue_quick_replies("learning_goal")
    if any(term in text for term in ("为什么学", "动力", "原因", "谁推动")):
        return _dialogue_quick_replies("motivation")
    if any(term in text for term in ("基础", "学到哪", "掌握", "熟悉程度")):
        return _dialogue_quick_replies("knowledge_base")
    # A casual acknowledgement without a concrete profile question should not
    # pretend that generic chips are an answer to it.
    if "？" not in text and "?" not in text:
        return []
    # Do not fall back to a broad dimension: the router's next target can
    # differ from the component asked in this visible question.
    return []


def _dialogue_confidences(
    evidence_cards: list[EvidenceCard],
    *,
    fusion: dict[DimensionKey, FusionResult] | None = None,
) -> dict[str, float]:
    fused = fusion or _dialogue_fusion(evidence_cards)
    return {dimension: result.confidence for dimension, result in fused.items()}


def dialogue_session_summary(session: ProfileSession, *, db_path: Path | None = None) -> dict[str, Any]:
    fusion = _dialogue_fusion(session.evidence_cards)
    profile = _profile_from_fusion(session.user_id, session.evidence_cards, fusion=fusion)
    card_scores, inferred_card = _identity_card_scores(session.evidence_cards, fusion=fusion)
    identity_cards = _dialogue_identity_cards_view(
        session.evidence_cards,
        fusion=fusion,
        step_data=session.step_data,
    )
    if db_path is not None:
        _save_profile_session(session, db_path=db_path)
    chat_history = list_chat_messages(session.user_id, session.session_id, db_path=db_path)
    next_question = session.current_step.prompt if session.current_step and session.current_step.kind == "dialogue" else None
    radar_changes = session.step_data.get("radar_changes")
    summary_text = session.step_data.get("summary_text")
    return {
        "card_scores": card_scores,
        "inferred_card": inferred_card,
        "radar": _radar_payload(profile),
        "turn_index": session.step_index,
        "converged": _dialogue_converged(session.step_index, session.evidence_cards, fusion=fusion),
        "next_question": next_question,
        "radar_changes": radar_changes if isinstance(radar_changes, list) else [],
        "summary_text": summary_text if isinstance(summary_text, str) else "",
        "profile_progress": _dialogue_profile_progress(session.evidence_cards, fusion=fusion),
        "evidence_panel": _dialogue_evidence_panel(session.evidence_cards, fusion=fusion),
        "identity_cards": identity_cards,
        "developer_audit": _dialogue_developer_audit(session.step_data),
        "chat_history": chat_history,
        "evidence_cards": cards_to_json(session.evidence_cards),
        "evidence_count": len(session.evidence_cards),
    }


def _dialogue_scored_components(dimension: DimensionKey, evidence_cards: list[EvidenceCard]) -> set[str]:
    """Components of this dimension that already carry a model score."""

    return {
        card.component for card in evidence_cards
        if card.dimension == dimension
        and card.component in COMPONENTS[dimension]
        and isinstance(card.objective_signal.get("component_score"), (int, float))
    }


# 重评步长：一个维度每新增 3 条去重后的原子证据重评一次。
# 时效性让位给成本：每轮都重评意味着用户每说一句话就打一次评分模型，而
# 证据条数没变时重评只会得到同一份结论。
SCORING_RERUN_EVIDENCE_STEP = 3


def _dialogue_scorable_cards(dimension: DimensionKey, evidence_cards: list[EvidenceCard]) -> list[EvidenceCard]:
    """本维度可评分的原子证据（已归属组件、状态可评）。"""

    return [
        card for card in evidence_cards
        if card.dimension == dimension
        and card.component in COMPONENTS[dimension]
        and card.collection_status == "scorable"
    ]


def _dialogue_evidence_watermark(dimension: DimensionKey, evidence_cards: list[EvidenceCard]) -> int:
    """证据水位线 = 本维度去重后的原子证据条数（冲突证据也算一条）。

    用去重后条数而不是原始条数：同一句话被抽成三张卡时，原始条数会虚增，
    水位线一跳就是 +3，等于每轮都触发重评。
    """

    kept, conflicts, _ = deduplicate_component_cards(_dialogue_scorable_cards(dimension, evidence_cards))
    return len(kept) + len(conflicts)


def _dialogue_scoring_due(
    dimension: DimensionKey,
    evidence_cards: list[EvidenceCard],
    audit_entry: dict[str, Any],
) -> bool:
    """Whether this dimension should call the scoring model right now.

    两条触发规则：

    1. **首评**：该维度收集到的组件数首次达到 60% 门槛（5 个里 3 个）。
       旧实现要 5 个全收齐才调评分模型，于是 4/5 已收集的维度永远停在
       "评分暂缓"、雷达轴一直是暗的。
    2. **重评**：距上次评分又新增了 3 条去重后的原子证据（同组件多条、或
       不同组件各一条都算）。组件数不变但证据变密时，分数也应该跟着动，
       否则"多说几句"对画像完全没有影响。
    """

    required = COMPONENTS[dimension]
    collected = _dialogue_collected_components(dimension, evidence_cards)
    if len(collected) / len(required) < COVERAGE_THRESHOLD:
        return False
    watermark = _dialogue_evidence_watermark(dimension, evidence_cards)
    last_watermark = audit_entry.get("evidence_watermark")
    if not isinstance(last_watermark, int):
        # 从未评过（或旧记录没有水位线）：组件门槛一到就评第一次。
        return True
    if watermark - last_watermark >= SCORING_RERUN_EVIDENCE_STEP:
        return True
    # 上次一个组件分都没拿到（模型失败/返回空），允许用同一批证据重试一次，
    # 但不能无限重试：同一批组件试过就不再打模型。
    return (
        not audit_entry.get("component_scores")
        and audit_entry.get("attempt_signature") != sorted(collected)
    )


def _dialogue_scoring_trigger(
    dimension: DimensionKey,
    evidence_cards: list[EvidenceCard],
    audit_entry: dict[str, Any],
    collected: set[str],
) -> str:
    """审计里一句话说清"这次为什么又评了一遍"。"""

    watermark = _dialogue_evidence_watermark(dimension, evidence_cards)
    last = audit_entry.get("evidence_watermark")
    if isinstance(last, int):
        return f"重评：新增 {watermark - last} 条去重原子证据（水位线 {last} → {watermark}）"
    return (
        f"首评：已收集 {len(collected)}/{len(COMPONENTS[dimension])} 个组件，"
        f"首次达到 {COVERAGE_THRESHOLD:.0%} 门槛（评分时该维度有 {watermark} 条去重证据）"
    )


def _dialogue_scoring_record(
    result: ScoringResult,
    *,
    attempts: int,
    signature: list[str],
    turn_index: int | None = None,
    watermark: int | None = None,
    trigger: str = "",
) -> dict[str, Any]:
    """Persist one scoring call for the developer audit and the retry guard."""

    return {
        "provenance": result.provenance,
        "dimension_summary": result.narrative,
        "component_scores": result.component_scores,
        "scoring_deferred": result.radar_score is None,
        "scoring_attempts": attempts,
        # 这份分数是哪一轮评出来的：评分明细跨轮保留后，审计必须能说清
        # "现在看到的三师落点来自第 2 轮，不是本轮"。
        "scoring_turn": turn_index,
        # 评分水位线：评这一份分时，本维度去重后有 N 条原子证据。下次要等
        # 水位线再涨 3 条才重评（见 SCORING_RERUN_EVIDENCE_STEP）。
        "evidence_watermark": watermark,
        "scoring_trigger": trigger,
        # 本次模型实际重评了哪些组件、哪些沿用了上一轮的分数。
        "rescored_components": list(result.rescored_components),
        "carried_components": list(result.carried_components),
        "missing_component_scores": [
            component for component in COMPONENTS[result.dimension_key]
            if component not in result.component_scores
        ],
        "attempt_signature": signature,
        # 三师会诊的分数、推理摘要与评语要进审计：用户要求"模型怎么打分的、
        # 附带思考过程"。这是结构化评分依据，不是私有思维链。
        "jury": {
            name: item.model_dump(mode="json")
            for name, item in result.jury.items()
        },
        "jury_mean": result.jury_mean,
        # 逐组件三师落点 + 每位老师加权出来的维度分。维度级 jury 现在由这两项
        # 推导（不再让模型另给一组整维印象分），审计要能还原推导过程。
        "component_jury": {
            component: {name: item.model_dump(mode="json") for name, item in teachers.items()}
            for component, teachers in result.component_jury.items()
        },
        "teacher_dimension_scores": result.teacher_dimension_scores,
        "coverage": result.coverage,
        "radar_score": result.radar_score,
        "warnings": [
            {"reason": warning.reason, "original": warning.original, "corrected": warning.corrected}
            for warning in result.warnings
        ],
    }


async def recover_dialogue_component_scores(session: ProfileSession, *, db_path: Path = DB_PATH) -> bool:
    """Retry deferred component scoring when a saved dialogue is restored."""
    changed = False
    scoring_audit = session.step_data.setdefault("component_scoring_audit", {})
    for dimension, required in COMPONENTS.items():
        cards = [card for card in session.evidence_cards if card.dimension == dimension]
        entry = scoring_audit.get(dimension)
        entry = entry if isinstance(entry, dict) else {}
        if not _dialogue_scoring_due(dimension, session.evidence_cards, entry):
            continue
        collected = _dialogue_collected_components(dimension, session.evidence_cards)
        result = await score_dimension(dimension, cards)
        attempts = 1
        rescored = set(result.rescored_components) or set(result.component_scores)
        if collected - rescored and result.provenance != "no_key_rule_fallback":
            result = await score_dimension(dimension, cards)
            attempts = 2
        if result.component_scores:
            for card in cards:
                component = card.component
                if component in result.component_scores:
                    card.objective_signal = {
                        **card.objective_signal,
                        "component_score": result.component_scores[component],
                    }
            changed = True
        scoring_audit[dimension] = _dialogue_scoring_record(
            result,
            attempts=attempts,
            signature=sorted(collected),
            turn_index=session.step_index,
            watermark=_dialogue_evidence_watermark(dimension, session.evidence_cards),
            trigger=_dialogue_scoring_trigger(dimension, session.evidence_cards, entry, collected),
        )
    if changed:
        fusion = _dialogue_fusion(session.evidence_cards)
        profile = _profile_from_fusion(session.user_id, session.evidence_cards, fusion=fusion)
        upsert_profile(profile, db_path=db_path)
        audits = _dialogue_developer_audit(session.step_data)
        if audits:
            latest = audits[-1]
            latest.setdefault("system_result", {})["dimensions"] = _dialogue_audit_record(
                turn_index=session.step_index,
                target=_as_dialogue_dimension(session.step_data.get("dialogue_target")) or "learning_goal",
                next_target=_as_dialogue_dimension(session.step_data.get("dialogue_target")) or "learning_goal",
                utterance="",
                cards=[],
                fusion_before=fusion,
                fusion=fusion,
                extraction_contract={},
                prior_cards=session.evidence_cards,
                all_cards=session.evidence_cards,
                scoring_audit=scoring_audit,
                )["system_result"]["dimensions"]
        _save_profile_session(session, db_path=db_path)
    elif scoring_audit:
        _save_profile_session(session, db_path=db_path)
    return changed


async def stream_dialogue_chat_events(
    session_id: str,
    *,
    user_id: str,
    utterance: str,
    client_turn_id: str | None = None,
    db_path: Path = DB_PATH,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Run the visible companion stream, then hidden extraction/fusion updates."""

    session = get_profile_session(session_id, user_id=user_id, db_path=db_path)
    if session.mode != ProfileMode.dialogue:
        raise ValueError("profile session is not dialogue mode")
    # Legacy callers cannot replay a generated request id after refresh. Keep
    # a stable, user/session-scoped compatibility boundary rather than tying
    # it to mutable step_index; identical legacy text is conservatively
    # treated as the same request for this session.
    legacy_client_turn_id = client_turn_id or hashlib.sha256(
        f"legacy:{user_id}:{session_id}:{utterance}".encode("utf-8")
    ).hexdigest()
    claim = claim_dialogue_turn(user_id, session_id, legacy_client_turn_id, db_path=db_path)
    if claim == "completed":
        session = get_profile_session(session_id, user_id=user_id, db_path=db_path)
        yield "state", _dialogue_state_payload(session, target=session.step_data.get("dialogue_target"), converged=False)
        yield "done", {}
        return
    if claim == "processing":
        if session.step_data.get("last_client_turn_id") == legacy_client_turn_id:
            complete_dialogue_turn(user_id, session_id, legacy_client_turn_id, db_path=db_path)
            yield "state", _dialogue_state_payload(session, target=session.step_data.get("dialogue_target"), converged=False)
            yield "done", {}
            return
        yield "error", {"message": "本轮对话正在处理中，请勿重复提交"}
        yield "state", _dialogue_state_payload(session, target=None, converged=False, error="turn_in_progress")
        yield "done", {}
        return
    lock = _dialogue_session_lock(session_id, db_path)
    if lock.locked():
        # A second request must not interleave step_index/chat/evidence writes.
        if claim == "claimed":
            release_dialogue_turn(user_id, session_id, legacy_client_turn_id, db_path=db_path)
        yield "error", {"message": "本轮对话正在处理中，请勿重复提交"}
        yield "state", _dialogue_state_payload(session, target=None, converged=False, error="turn_in_progress")
        yield "done", {}
        return
    await lock.acquire()
    if session.current_step is None:
        complete_dialogue_turn(user_id, session_id, legacy_client_turn_id, db_path=db_path)
        yield "state", _dialogue_state_payload(session, target=None, converged=False)
        yield "done", {}
        lock.release()
        return

    utterance = utterance.strip()
    turn_index = session.step_index + 1
    current_prompt = session.current_step.prompt
    fusion_before = _dialogue_fusion(session.evidence_cards)
    target = _dialogue_target(session.evidence_cards, fusion=fusion_before)
    current_confidences = _dialogue_confidences(session.evidence_cards, fusion=fusion_before)
    history_messages = list_chat_messages(user_id=user_id, session_id=session.session_id, db_path=db_path)
    # Do not expose a provider's raw chain-of-thought.  The UI receives only
    # short, user-facing Chinese progress summaries while the model reasons.
    thinking_parts: list[str] = []
    assistant_parts: list[str] = []
    message_done_sent = False
    thinking_status = "正在扫描这句回答涉及的七个画像维度与组件。"
    thinking_parts.append(thinking_status)
    yield "thinking", {"delta": thinking_status}
    response_time_ms = _response_time_ms({"utterance": utterance}, session.current_step.started_at)
    prior_evidence_cards = list(session.evidence_cards)
    try:
        cards = await _dialogue_extract_cards_for_chat(
            session=session,
            utterance=utterance,
            assistant_text="",
            history_messages=history_messages,
            target=target,
            turn_index=turn_index,
            response_time_ms=response_time_ms,
            current_prompt=current_prompt,
        )
    except Exception as exc:
        from app.profile.nodes import DialogueExtraction

        extraction = DialogueExtraction(
            extraction_method="rule_fallback",
            extraction_error=f"extraction_pipeline_error:{type(exc).__name__}",
        )
        session.step_data["_last_component_contract"] = {
            "turn_id": turn_index,
            "combination_type": extraction.combination_type,
            "extraction_error": extraction.extraction_error,
            "extraction_method": extraction.extraction_method,
            "dimension_scan": [],
            "atomic_evidence": [],
            "component_collection": [],
        }
        cards = []
    yield "thinking", {"delta": "\n正在校验原话引用，并整理证据归属与来源关系。"}
    fusion_before = _dialogue_fusion(prior_evidence_cards)
    session.evidence_cards.extend(cards)
    # Commit the newly extracted evidence before any component-scoring model
    # calls. A transient scorer failure must not discard a completed turn or
    # its component matrix when the client restores the session.
    if cards:
        for card in cards:
            add_evidence(user_id, card, db_path=db_path)
        session.persisted = True
        session.persisted_db_path = str(db_path)
    upsert_profile(
        _profile_from_fusion(user_id, session.evidence_cards, fusion=_dialogue_fusion(session.evidence_cards)),
        db_path=db_path,
    )
    _save_profile_session(session, db_path=db_path)
    # 这个键必须持久化（不带下划线前缀）。以前叫 _component_scoring_audit，
    # 保存时被 _persistable_step_data 过滤掉，于是第 2 轮起审计里就查不到
    # 第 1 轮评出来的三师落点，只剩一个孤零零的组件分。
    scoring_audit = session.step_data.setdefault("component_scoring_audit", {})
    for dimension, required_components in COMPONENTS.items():
        dimension_cards = [card for card in session.evidence_cards if card.dimension == dimension]
        entry = scoring_audit.get(dimension)
        entry = entry if isinstance(entry, dict) else {}
        if not _dialogue_scoring_due(dimension, session.evidence_cards, entry):
            continue
        collected = _dialogue_collected_components(dimension, session.evidence_cards)
        trigger = _dialogue_scoring_trigger(dimension, session.evidence_cards, entry, collected)
        yield "thinking", {"delta": f"\n{DIMENSION_LABELS[dimension]}触发评分——{trigger}。"}
        score_result = await score_dimension(dimension, dimension_cards)
        scoring_attempts = 1
        # 只看本次真正重评的组件：合并后的 component_scores 里还带着沿用的旧分，
        # 拿它判断"模型漏了谁"会永远判成不漏。规则回退路径不填 rescored_components，
        # 此时退回 component_scores——否则每次都会白重试一次模型。
        rescored = set(score_result.rescored_components) or set(score_result.component_scores)
        missing_model_scores = collected - rescored
        if missing_model_scores and score_result.provenance != "no_key_rule_fallback":
            yield "thinking", {"delta": f"\n评分模型尚未覆盖全部已收集组件，正在携带完整证据包重试缺失评分（{len(missing_model_scores)} 项）。"}
            score_result = await score_dimension(dimension, dimension_cards)
            scoring_attempts = 2
        # A scoring result is the only source allowed to publish component
        # scores.  Persist whatever came back onto the live cards so the
        # deterministic fusion pass can see it.  Partial coverage is fine:
        # ``dimension_score`` already folds the score toward 50 by coverage.
        # 原子证据本体（原话、归属、质量分）只增不改；这里写回的只是派生分。
        for card in dimension_cards:
            component = card.component
            if component not in score_result.component_scores:
                continue
            card.objective_signal = {
                **card.objective_signal,
                "component_score": score_result.component_scores[component],
            }
        scoring_audit[dimension] = _dialogue_scoring_record(
            score_result,
            attempts=scoring_attempts,
            signature=sorted(collected),
            turn_index=turn_index,
            watermark=_dialogue_evidence_watermark(dimension, session.evidence_cards),
            trigger=trigger,
        )
    planning_fusion = _dialogue_fusion(session.evidence_cards)
    # Soft lock: keep working the current drawer while it still has an
    # answerable hole, unless it stalled or another one is far ahead.
    component_ask_counts = _dialogue_asked_components(session.step_data)
    planning_target = _dialogue_target(
        session.evidence_cards,
        fusion=planning_fusion,
        asked_dimensions=_dialogue_step_list(session.step_data, "asked_dimensions"),
        current_target=_as_dialogue_dimension(session.step_data.get("dialogue_target")),
        stalled=_dialogue_stalled(session),
        stalled_dimension=_dialogue_stalled_dimension(session),
        component_ask_counts=component_ask_counts,
    )
    planning_focus = _dialogue_component_focus(
        session.evidence_cards,
        planning_target,
        asked_counts=component_ask_counts,
    )
    # 这一轮的提问预算花在哪一格，现在就记账——下一轮选格时才知道哪些格
    # 已经问过两遍还没答上来，该让位了。
    _dialogue_note_asked_component(session.step_data, planning_focus)
    planning_question = _fallback_dialogue_question(
        planning_target,
        turn_index,
        asked_questions=list(_dialogue_step_list(session.step_data, "asked_questions")),
        asked_dimensions=list(_dialogue_step_list(session.step_data, "asked_dimensions")),
        evidence_cards=session.evidence_cards,
        focus=planning_focus,
    ) if planning_focus else ""
    if planning_focus:
        focus_status = f"正在补全{DIMENSION_LABELS[planning_target]}：缺少{planning_focus['label']}，后续追问原因是{planning_focus['reason']}。"
    else:
        focus_status = "组件收集已完成，正在核对评分与确认度。"
    yield "thinking", {"delta": f"\n{focus_status}"}
    target = planning_target
    current_confidences = _dialogue_confidences(session.evidence_cards, fusion=planning_fusion)
    companion_messages = _dialogue_companion_messages(
        history_messages=history_messages,
        utterance=utterance,
        target=target,
        evidence_cards=session.evidence_cards,
        fusion=planning_fusion,
        current_confidences=current_confidences,
        pure_chat_streak=_dialogue_pure_chat_streak(session.step_data),
        component_focus=planning_focus,
        component_question=planning_question,
    )
    yield "thinking", {"delta": "\n证据与缺项已整理，正在让小岐据此组织自然回应和下一问。"}
    router = ModelRouter()
    use_rule_reply = _dialogue_router_uses_mock_client(router)
    if use_rule_reply:
        thinking_delta = "正在生成小岐的回应。"
        thinking_parts.append(thinking_delta)
        yield "thinking", {"delta": thinking_delta}
    else:
        mock_prefix = "[MOCK:"
        pending_prefix = ""
        prefix_released = False
        sentence_buffer = ""
        asked_question = False
        try:
            async for kind, delta in router.chat_stream(
                "reason",
                companion_messages,
                # 对话回复保留浅思考（比不思考更像"老师在回应"），但不再用
                # medium —— 这是唯一显式开启思考的调用点，成本要单独盯住。
                thinking={"type": "enabled"},
                reasoning_effort="low",
                temperature=0.7,
            ):
                if kind == "thinking":
                    # `delta` can contain the model's private reasoning. Keep
                    # it out of the SSE/UI and retain only a safe status above.
                    continue
                elif kind == "message" and not use_rule_reply:
                    if not prefix_released:
                        pending_prefix += delta
                        if len(pending_prefix) >= len(mock_prefix) and pending_prefix.startswith(mock_prefix):
                            use_rule_reply = True
                            continue
                        if mock_prefix.startswith(pending_prefix):
                            continue
                        prefix_released = True
                        sentence_buffer += pending_prefix
                        pending_prefix = ""
                        # pending_prefix 里已经含这次 delta；不清零就会把回复的
                        # 头一两个字再写一遍（实测每条回复都以"备考备考""三三周"
                        # 这种叠字开头）。
                        delta = ""
                    if prefix_released:
                        sentence_buffer += delta
                        sentences = re.split(r"(?<=[。！？!?])", sentence_buffer)
                        sentence_buffer = sentences.pop() if sentences else ""
                        for sentence in sentences:
                            safe_sentence = sanitize_dialogue_reply(
                                _remove_component_rubric_leak(
                                    _sanitize_visible_dialogue_text(sentence), planning_focus
                                )
                            )
                            if not safe_sentence:
                                continue
                            # One question per bubble: later questions are dropped
                            # so the user is never handed a form.
                            if asked_question and re.search(r"[？?]", safe_sentence):
                                continue
                            asked_question = asked_question or bool(re.search(r"[？?]", safe_sentence))
                            assistant_parts.append(safe_sentence)
                            for index in range(0, len(safe_sentence), 12):
                                yield "message", {"delta": safe_sentence[index:index + 12]}
                                await asyncio.sleep(0.035)
            if pending_prefix and not use_rule_reply:
                sentence_buffer += pending_prefix
            if not use_rule_reply and sentence_buffer:
                safe_sentence = sanitize_dialogue_reply(
                    _remove_component_rubric_leak(
                        _sanitize_visible_dialogue_text(sentence_buffer), planning_focus
                    )
                )
                if safe_sentence and not (asked_question and re.search(r"[？?]", safe_sentence)):
                    assistant_parts.append(safe_sentence)
                    for index in range(0, len(safe_sentence), 12):
                        yield "message", {"delta": safe_sentence[index:index + 12]}
                        await asyncio.sleep(0.035)
        except Exception:
            use_rule_reply = True

    if use_rule_reply:
        assistant_parts.clear()
        assistant_text = _fallback_visible_dialogue_reply(session, target=target, turn_index=turn_index, utterance=utterance)
        # Keep the fallback path visually streaming as well.
        assistant_parts.extend(assistant_text)
        for index in range(0, len(assistant_text), 2):
            yield "message", {"delta": assistant_text[index:index + 2]}
            await asyncio.sleep(max(0.055, get_settings().mock_stream_delay))
    else:
        # Provider content is sanitized and streamed sentence-by-sentence as
        # it arrives above, instead of being buffered until generation ends.
        pass

    assistant_text = "".join(assistant_parts)

    asked_dimension = await _dialogue_classify_visible_reply_asked_dimension(assistant_text, target)
    if _dialogue_user_asked_evidence(utterance) and not _dialogue_visible_reply_addresses_evidence(assistant_text):
        evidence_tail = " 你问得对，画像旁边的「证据清单」会按维度列出分/标签、置信度和你的原文引用；我不会编证据，只按你说过的话更新。"
        assistant_parts.append(evidence_tail)
        assistant_text = "".join(assistant_parts)
        yield "message", {"delta": evidence_tail}
    _log_dialogue_stream_fulltext(session.session_id, turn_index, thinking_parts, assistant_parts)

    try:
        session.step_index = turn_index
        asked_dimensions = _dialogue_step_list(session.step_data, "asked_dimensions")
        _dialogue_remember(asked_dimensions, target)
        asked_questions = _dialogue_step_list(session.step_data, "asked_questions")
        _dialogue_remember(asked_questions, current_prompt)
        _dialogue_remember(asked_questions, assistant_text)
        hit_target = asked_dimension == target
        pure_chat_streak = 0 if hit_target else _dialogue_pure_chat_streak(session.step_data) + 1
        _store_dialogue_pure_chat_streak(
            session.step_data,
            pure_chat_streak,
        )

        fusion_after = _dialogue_fusion(session.evidence_cards)
        identity_cards = _dialogue_identity_cards_view(
            session.evidence_cards,
            fusion=fusion_after,
            step_data=session.step_data,
        )
        converged = _dialogue_converged(turn_index, session.evidence_cards, fusion=fusion_after)
        profile = _profile_from_fusion(session.user_id, session.evidence_cards, fusion=fusion_after)
        # 记下这一维是不是连着两轮没长出新组件；长不出来就该换抽屉了。
        _dialogue_note_stall(session, target)
        if hit_target or pure_chat_streak >= DIALOGUE_TARGET_RETRY_LIMIT:
            next_target = _dialogue_target(
                session.evidence_cards,
                fusion=fusion_after,
                asked_dimensions=asked_dimensions,
                current_target=target,
                stalled=_dialogue_stalled(session),
                stalled_dimension=_dialogue_stalled_dimension(session),
                component_ask_counts=_dialogue_asked_components(session.step_data),
            )
        else:
            next_target = target
        focus = _dialogue_component_focus(
            session.evidence_cards,
            next_target,
            asked_counts=_dialogue_asked_components(session.step_data),
        )
        # 只有下一轮还是问同一格时，才复用这一轮的规划问句。若这一格刚被
        # "问满两次"让位（focus 换了），复用旧问句等于把刚放弃的格子又问一遍。
        same_component = bool(
            focus
            and planning_focus
            and focus.get("component") == planning_focus.get("component")
        )
        next_component_question = planning_question if same_component and next_target == target else ""
        if focus and not next_component_question:
            next_component_question = _fallback_dialogue_question(
                next_target,
                turn_index,
                asked_questions=asked_questions,
                asked_dimensions=asked_dimensions,
                evidence_cards=session.evidence_cards,
                focus=focus,
            )
        # The response model is instructed to ask exactly one question.  Only
        # add the calibrated fallback when it produced no question at all;
        # appending on a text mismatch caused duplicate questions and exposed
        # the internal rubric direction in otherwise natural replies.
        has_visible_question = "？" in assistant_text or "?" in assistant_text
        # 问了，但问偏了：整段回复没碰到这一格的关键词，等于这轮白问。
        guard_keywords = tuple((focus or {}).get("guard_keywords") or ())
        drifted_off_component = bool(
            guard_keywords
            and has_visible_question
            and not any(word in assistant_text for word in guard_keywords)
        )
        if (
            focus
            and next_component_question
            and next_component_question not in assistant_text
            and (not has_visible_question or drifted_off_component)
        ):
            # 计划要求的是"替换漂移问句"，不是叠成两个问题。文字已经流式发出去
            # 了，所以这里发一条 replace 让前端整段换掉，而不是再追加一句。
            assistant_text = sanitize_dialogue_reply(
                assistant_text,
                fallback_question=next_component_question,
                anchor=focus,
            )
            assistant_parts.append(f"\n\n[replace]{assistant_text}")
            for card in cards:
                card.objective_signal["next_question"] = next_component_question
            # 替换出来的问句也是用户真正看到的提问，必须进账本；否则这一格
            # 可以靠"被替换"绕过两轮上限，实测就是这样又问出了第三、第四遍
            # "你坚持多久了"。同一格已在规划时记过一次的不要重复记。
            if not same_component:
                _dialogue_note_asked_component(session.step_data, focus)
            yield "replace", {"text": assistant_text}
        append_chat_messages(
            user_id=user_id,
            session_id=session.session_id,
            messages=[
                {"role": "user", "content": utterance},
                {"role": "assistant", "content": assistant_text},
            ],
            db_path=db_path,
        )
        _log_dialogue_stream_fulltext(session.session_id, turn_index, thinking_parts, assistant_parts)
        yield "message_done", {}
        message_done_sent = True
        _append_dimension_timeline(
            session.step_data,
            turn_index=turn_index,
            fusion=fusion_after,
            cards=cards,
            all_cards=session.evidence_cards,
        )
        audit_record = _dialogue_audit_record(
            turn_index=turn_index,
            target=target,
            next_target=next_target,
            utterance=utterance,
            cards=cards,
            fusion_before=fusion_before,
            fusion=fusion_after,
            extraction_contract=session.step_data.pop("_last_component_contract", {}),
            prior_cards=prior_evidence_cards,
            all_cards=session.evidence_cards,
            # 不 pop：评分明细要跨轮保留。否则第 3 轮再看"学习目标"，上一轮
            # 的三师落点和三师维度分就凭空消失了，只剩一个组件分。
            scoring_audit=session.step_data.get("component_scoring_audit", {}),
            timeline=session.step_data.get("dimension_timeline", {}),
        )
        _store_dialogue_audit(session.step_data, audit_record)
        session.finished = False
        session.current_step = _dialogue_turn_step(turn_index + 1, assistant_text, next_target)
        session.step_data["next_question"] = assistant_text
        session.step_data["dialogue_target"] = next_target
        session.step_data["last_client_turn_id"] = legacy_client_turn_id
        session.step_data["_dialogue_fusion"] = fusion_after
        persist_status = "正在保存本轮对话与画像更新。"
        thinking_parts.append(persist_status)
        yield "thinking", {"delta": "\n" + persist_status}
        _persist_dialogue_live_update(session, cards=[], profile=profile, db_path=db_path)
        complete_dialogue_turn(user_id, session_id, legacy_client_turn_id, db_path=db_path)

        yield "evidence", {"cards": cards_to_json(cards)}
        yield "audit", audit_record
        yield "identity_cards", {"items": identity_cards}
        yield "radar", _radar_payload(profile)
        # The chips answer the question the user can see now, not the internal
        # dimension selected for the following turn.
        yield "state", _dialogue_state_payload(
            session,
            target=next_target,
            quick_replies=_dialogue_quick_replies_for_question(assistant_text, asked_dimension or target),
            converged=converged,
        )
    except Exception as exc:
        release_dialogue_turn(user_id, session_id, legacy_client_turn_id, db_path=db_path)
        logger.warning(
            "profile dialogue update failed session_suffix=%s turn_index=%s error_type=%s",
            session.session_id[-8:],
            turn_index,
            exc.__class__.__name__,
        )
        if not message_done_sent:
            # The visible answer may have streamed before extraction failed.
            # Close that message before emitting the recoverable turn error.
            yield "message_done", {}
        yield "error", {"message": "本轮证据整理暂时不可用，已保留对话内容，请稍后重试"}
        yield "state", _dialogue_state_payload(session, target=target, converged=False, error="profile_update_failed")
    finally:
        if lock.locked():
            lock.release()
        yield "done", {}


def _dialogue_router_uses_mock_client(router: Any) -> bool:
    try:
        return isinstance(router.select("reason"), MockClient)
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def _fallback_visible_dialogue_reply(
    session: ProfileSession,
    *,
    target: DimensionKey,
    turn_index: int,
    utterance: str,
) -> str:
    asked_questions = list(_dialogue_step_list(session.step_data, "asked_questions"))
    if session.current_step is not None:
        _dialogue_remember(asked_questions, session.current_step.prompt)
    question = _fallback_dialogue_question(
        target,
        turn_index,
        asked_questions=asked_questions,
        asked_dimensions=list(_dialogue_step_list(session.step_data, "asked_dimensions")),
        evidence_cards=session.evidence_cards,
    )
    if _dialogue_user_asked_evidence(utterance):
        return f"画像只会引用你说过的原话，并在证据清单中标明来源和置信度。{question}"
    recent_claim = next(
        (
            str(card.normalized_claim or card.fact).strip()
            for card in reversed(session.evidence_cards)
            if card.source_turn_ids and turn_index in card.source_turn_ids and (card.normalized_claim or card.fact)
        ),
        "",
    )
    if recent_claim:
        return f"听起来，{recent_claim}。{question}"
    return f"我还没能从这句话里确认具体的学习线索，换个角度再说说也可以。{question}"


def _sanitize_visible_dialogue_text(text: str) -> str:
    """Remove internal mock/fallback diagnostics from visible assistant text."""

    cleaned = re.sub(r"\[MOCK:[^\]]*\]", "", str(text or ""))
    cleaned = re.sub(r"\s*\[FallbackReason:[^\]]*\]", "", cleaned)
    return cleaned.strip()


# 这些词只应该出现在私有提示词里。一旦漏到正文，用户会看到系统的内部结构。
_DIALOGUE_INTERNAL_PHRASES: tuple[str, ...] = (
    "自然追问", "补问理由", "关于实际投入", "组件", "量表", "证据整理",
    "profile_steering", "target_dimension", "component_focus",
)

# 只接住话、没有推进的回复，需要补一个真问题，否则这一轮等于白聊。
_DIALOGUE_FILLER_REPLIES: frozenset[str] = frozenset(
    {"嗯嗯", "嗯", "好的", "好", "在的", "收到", "明白", "哈哈", "是的是的"}
)


def _strip_internal_phrases(text: str) -> str:
    if not text:
        return ""
    sentences = re.split(r"(?<=[。！？!?])", text)
    kept = [sentence for sentence in sentences if not any(phrase in sentence for phrase in _DIALOGUE_INTERNAL_PHRASES)]
    return "".join(kept).strip()


def _keep_single_question(text: str) -> str:
    """Keep only one question and drop the others.

    Two questions in one bubble turns a conversation into a form: the user
    answers one, ignores the other, and the second dimension silently starves.
    The kept question is the longest one, because a greeting like "你好呀？"
    is not the question we actually need answered.
    """

    if not text:
        return ""
    parts = [part for part in re.split(r"(?<=[？?])", text) if part]
    question_indexes = [index for index, part in enumerate(parts) if re.search(r"[？?]", part)]
    if len(question_indexes) <= 1:
        return text.strip()
    best = max(question_indexes, key=lambda index: len(parts[index].strip("？?。！!，, ")))
    kept = [part for index, part in enumerate(parts) if index == best or index not in question_indexes]
    return "".join(kept).strip()


def _keep_last_question(text: str) -> str:
    """Keep the trailing question and every statement before it."""

    parts = [part for part in re.split(r"(?<=[？?])", text) if part]
    question_indexes = [index for index, part in enumerate(parts) if re.search(r"[？?]", part)]
    if len(question_indexes) <= 1:
        return text.strip()
    last = question_indexes[-1]
    kept = [part for index, part in enumerate(parts) if index == last or index not in question_indexes]
    return "".join(kept).strip()


def sanitize_dialogue_reply(text: str, *, fallback_question: str = "", anchor: dict[str, Any] | None = None) -> str:
    """Four local checks applied to every visible reply.

    1. internal steering vocabulary never reaches the user;
    2. at most one question mark survives;
    3. a filler-only reply gets a real question appended;
    4. a reply that never touches the component being probed is steered back
       onto it with the calibrated question.

    Everything is repaired locally -- the model is never called twice for the
    same turn.
    """

    cleaned = _keep_single_question(_strip_internal_phrases(_sanitize_visible_dialogue_text(text)))
    if not cleaned:
        return fallback_question.strip()
    if cleaned.strip("。！？!?，, ") in _DIALOGUE_FILLER_REPLIES and fallback_question:
        return f"{cleaned}{fallback_question.strip()}"
    if len(cleaned) < 6 and fallback_question:
        return f"{cleaned}{fallback_question.strip()}"
    guard = tuple((anchor or {}).get("guard_keywords") or ())
    if guard and fallback_question and not any(word in cleaned for word in guard):
        # The reply drifted off the component: keep its statements, replace the
        # question with the one that actually lands on that cell.
        return _keep_last_question(f"{cleaned}{fallback_question.strip()}")
    return cleaned


def _remove_component_rubric_leak(text: str, focus: dict[str, str] | None) -> str:
    """Drop complete sentences that repeat the private rubric direction verbatim.

    The component question remains separately supplied as the user-facing
    follow-up. This is exact-string output hygiene, not evidence classification.
    """

    direction = str((focus or {}).get("reason") or "").strip()
    if not direction or direction not in text:
        return text.strip()
    sentences = re.split(r"(?<=[。！？!?])", text)
    return "".join(sentence for sentence in sentences if direction not in sentence).strip()


def _log_dialogue_stream_fulltext(
    session_id: str,
    turn_index: int,
    thinking_parts: list[str],
    message_parts: list[str],
) -> None:
    thinking_text = "".join(thinking_parts)
    message_text = "".join(message_parts)
    logger.info(
        "profile dialogue stream completed session_suffix=%s turn_index=%s thinking_len=%s message_len=%s",
        session_id[-8:],
        turn_index,
        len(thinking_text),
        len(message_text),
    )


def _dialogue_companion_messages(
    *,
    history_messages: list[dict[str, str]],
    utterance: str,
    target: DimensionKey,
    evidence_cards: list[EvidenceCard],
    fusion: dict[DimensionKey, FusionResult],
    current_confidences: dict[str, float],
    pure_chat_streak: int = 0,
    component_focus: dict[str, Any] | None = None,
    component_question: str = "",
) -> list[dict[str, str]]:
    low_confidence_dims = _dialogue_low_confidence_dims(evidence_cards, fusion=fusion, target=target)
    # 本轮回复必须覆盖到的内容要点。不是要照抄的句子，而是"这一轮不能空手而归"的清单。
    must_cover: list[str] = []
    if component_focus:
        must_cover.append(str(component_focus.get("must_cover") or component_focus.get("label") or ""))
    must_cover.extend(list(DIALOGUE_TARGET_EXTRACTABLE_QUESTIONS.get(target, ()))[:2])
    must_cover = [item for item in must_cover if item]
    # guard_keywords 只用于本地事后校验，进提示词会让模型直接往里面塞词。
    focus_payload = (
        {key: value for key, value in component_focus.items() if key != "guard_keywords"}
        if component_focus
        else None
    )
    steering_payload = {
        "target_dimension": target,
        "target_label": DIALOGUE_TARGET_LABELS.get(target, target),
        "current_confidences": current_confidences,
        "low_confidence_dimensions": low_confidence_dims,
        "pure_chat_streak": pure_chat_streak,
        "component_focus": focus_payload,
        "component_question": component_question,
        "must_cover": must_cover,
        "profile_question_examples": list(DIALOGUE_TARGET_EXTRACTABLE_QUESTIONS.get(target, ())),
    }
    private_prompt = (
        "你是岐黄智学里热情、懂中医药的学长「小岐」。"
        "语气保持温暖、生活化，可以先接住用户的养生或日常话题。"
        "但你有一个陪聊预算：连续纯寒暄/纯养生闲聊不得超过 2 轮；每 2-3 轮必须自然问 1 个能被抽取器识别的画像问题。"
        "本轮优先照顾 profile_steering.target_dimension，不要整轮完全不碰这个目标维度。"
        "如果 profile_steering.component_focus 非空，必须优先围绕该缺失组件自然提出一个补问；问题可放在回应末尾。"
        "profile_steering.component_question 是这一格要问到的内容要点，用你自己的话、结合他刚说的内容问出来即可；"
        "不许照抄这句话，不许一次问两个问题，也不许改问其他画像组件。"
        "profile_steering.component_focus.angles 只是几个角度示例，可以借用意思，绝不许原句搬用。"
        "目标维度的问题必须落到可抽取答案：学习目标=应试提分/临床实用/兴趣科普/学术深造；"
        "风格=视觉/文字/记忆(口诀)；动机=内驱/外驱/内外混合/被动；"
        "资源=知识图谱/拟人化短剧/汤头歌诀/医案文选；节奏=每天学习分钟数/目标天数；"
        "知识基础=中医知识自评或是否愿意做小测；薄弱点=药性功效/配伍剂量/辨证思路/背诵记忆等卡点。"
        "回答只需用一句话接住用户刚才真正表达的意思，再用一句自然追问推进；总长尽量不超过80个汉字。"
        "不要复述内部分析、组件名、量表方向、补问理由、‘关于实际投入’、‘自然追问’、证据整理过程或‘我记下了’等流程话术；不要扩写用户没有提到的经历。"
        "用户问“证据、凭什么打分、为什么这么判断”时，不要回避；简短说明会按原话引用，并引导查看雷达旁的「证据清单」面板。"
        "不要声称看不到证据，也不要把证据塞成聊天气泡长列表。"
        "禁止表单式提问，禁止罗列大段选项。一次只聊一个话题。"
    )
    private_prompt += "正文必须是连贯完整的中文句子，使用正常中文标点；只问一个问题，保持单段自然口吻，不要一节一节拆开。"
    messages: list[dict[str, str]] = [
        {"role": "system", "content": private_prompt},
        {"role": "user", "content": "profile_steering=" + json.dumps(steering_payload, ensure_ascii=False)},
    ]
    for item in history_messages[-10:]:
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": utterance})
    return messages


async def _dialogue_classify_visible_reply_asked_dimension(text: str, target: DimensionKey) -> DimensionKey | None:
    content = str(text or "")
    if _has_profile_extract_key() and content.strip():
        payload = {
            "assistant_reply": content,
            "target_dimension": target,
            "dimension_options": list(DIALOGUE_CONFIDENCE_THRESHOLDS),
            "instruction": "Return the single profile dimension the visible reply actually asks the user about, or null.",
        }
        messages = [
            {
                "role": "system",
                "content": "Classify the user-facing reply. Output JSON only. Do not infer evidence; classify the asked profile dimension.",
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False) + '\nOutput JSON: {"asked_dimension": string|null}'},
        ]
        try:
            raw = await ModelRouter().chat(
                "profile_extract",
                messages,
                response_format={"type": "json_object"},
                temperature=0,
                thinking={"type": "disabled"},
            )
            parsed = _parse_json_object(raw)
            if isinstance(parsed, dict):
                dimension = _as_dialogue_dimension(parsed.get("asked_dimension"))
                if dimension is not None:
                    return dimension
        except Exception:
            pass
    return _dialogue_visible_reply_asked_dimension(content, target)


def _dialogue_visible_reply_asked_dimension(text: str, target: DimensionKey | str | None = None) -> DimensionKey | None:
    content = str(text or "")
    if not content.strip():
        return None
    if "？" not in content and "?" not in content:
        return None
    question_text = content.rsplit("？", 1)[0].rsplit("?", 1)[0] + "？"
    candidates = _dialogue_visible_reply_dimension_candidates(question_text)
    target_dimension = _as_dialogue_dimension(target)
    if target_dimension in candidates:
        return target_dimension
    return candidates[0] if candidates else None


def _dialogue_visible_reply_dimension_candidates(text: str) -> list[DimensionKey]:
    content = str(text or "")
    patterns: tuple[tuple[DimensionKey, tuple[str, ...]], ...] = (
        ("knowledge_base", ("基础", "自评", "刚入门", "学到哪", "掌握", "熟悉", "会做")),
        ("learning_goal", ("目标", "想先解决", "考试", "提分", "临床", "科研", "兴趣", "深造")),
        ("learning_style", ("呈现方式", "材料", "图", "图表", "关系图", "文字", "口诀", "歌诀", "记忆句", "案例拆解")),
        ("weak_points", ("卡住", "薄弱", "易错", "老错", "不确定", "分不清", "记不住", "困惑", "混淆")),
        ("pace", ("每天", "多久", "多长时间", "几分钟", "一小段", "安排", "节奏", "坚持", "周期")),
        ("motivation", ("动力", "动机", "自己想", "推着", "压力", "内驱", "外驱", "为什么想学")),
        ("resource_preference", ("资源", "知识图谱", "短剧", "汤头歌诀", "医案", "文选", "打开哪")),
    )
    candidates: list[DimensionKey] = []
    for dimension, markers in patterns:
        if any(marker in content for marker in markers):
            candidates.append(dimension)
    return candidates


def _dialogue_visible_reply_hits_target(text: str, target: DimensionKey) -> bool:
    return _dialogue_visible_reply_asked_dimension(text, target) == target


def _dialogue_user_asked_evidence(utterance: str) -> bool:
    text = str(utterance or "")
    return any(keyword in text for keyword in ("证据", "凭什么", "为什么这么打分", "依据", "原文", "引用"))


def _dialogue_visible_reply_addresses_evidence(text: str) -> bool:
    content = str(text or "")
    return any(keyword in content for keyword in ("证据清单", "原文引用", "引用", "证据"))


def _dialogue_pure_chat_streak(step_data: dict[str, Any]) -> int:
    try:
        return max(0, int(step_data.get("dialogue_pure_chat_streak") or 0))
    except (TypeError, ValueError):
        return 0


def _store_dialogue_pure_chat_streak(step_data: dict[str, Any], value: int) -> None:
    step_data["dialogue_pure_chat_streak"] = max(0, int(value))


def _dialogue_low_confidence_dims(
    evidence_cards: list[EvidenceCard],
    *,
    fusion: dict[DimensionKey, FusionResult],
    target: DimensionKey,
) -> list[str]:
    dims: list[DimensionKey] = []
    for dimension, threshold in DIALOGUE_CONFIDENCE_THRESHOLDS.items():
        if fusion[dimension].confidence < threshold:
            dims.append(dimension)
    if target not in dims:
        dims.insert(0, target)
    return [f"{dimension}({DIALOGUE_TARGET_LABELS.get(dimension, dimension)})" for dimension in dims[:3]]


async def _dialogue_extract_cards_for_chat(
    *,
    session: ProfileSession,
    utterance: str,
    assistant_text: str,
    history_messages: list[dict[str, str]],
    target: DimensionKey,
    turn_index: int,
    response_time_ms: int,
    current_prompt: str,
) -> list[EvidenceCard]:
    transcript = _dialogue_transcript(history_messages, utterance=utterance, assistant_text=assistant_text)
    extraction_text = transcript if _has_profile_extract_key() else utterance
    try:
        extraction, _ = await _dialogue_extract_and_question(
            utterance=utterance,
            dialogue_history=extraction_text,
            target=target,
            turn_index=turn_index,
            evidence_cards=session.evidence_cards,
            current_confidences={},
        )
    except Exception:
        from app.profile.nodes import DialogueExtraction

        extraction = DialogueExtraction(
            extraction_method="rule_fallback" if _has_profile_extract_key() else "no_key_rule_fallback"
        )
    from app.profile.nodes import DialogueExtraction

    if isinstance(extraction, dict):
        extraction = DialogueExtraction.model_validate(extraction)
    if isinstance(extraction, DialogueExtraction):
        session.step_data["_last_component_contract"] = {
            "turn_id": extraction.turn_id if extraction.turn_id is not None else turn_index,
            "combination_type": extraction.combination_type,
            "extraction_error": extraction.extraction_error,
            "extraction_method": extraction.extraction_method,
            "dimension_scan": [item.model_dump(mode="json") for item in extraction.dimension_scan],
            "atomic_evidence": [item.model_dump(mode="json") for item in extraction.atomic_evidence],
            "component_collection": [item.model_dump(mode="json") for item in extraction.component_collection],
            "extraction_method": extraction.extraction_method,
        }
    cards = _dialogue_cards_from_extraction(extraction)
    for card in cards:
        card.objective_signal["turn_index"] = turn_index
        card.objective_signal["response_time_ms"] = response_time_ms
        card.objective_signal["dialogue_target"] = target
        card.objective_signal["asked_question"] = current_prompt
        # All cards extracted from one response are dependent observations. This
        # source identity lets fusion avoid counting one utterance as several
        # independent confirmations while still preserving every dimension.
        card.objective_signal["evidence_unit_id"] = f"dialogue:{session.session_id}:{turn_index}"
    return cards


def _dialogue_transcript(
    history_messages: list[dict[str, str]],
    *,
    utterance: str,
    assistant_text: str,
) -> str:
    rows = [
        f"{item.get('role', 'unknown')}: {item.get('content', '')}"
        for item in history_messages[-12:]
        if item.get("content")
    ]
    rows.append(f"user: {utterance}")
    if assistant_text:
        rows.append(f"assistant: {assistant_text}")
    return "\n".join(rows)


def _dialogue_user_source_text(dialogue_text: str, *, latest_utterance: str) -> str:
    """Return only user-authored text for quote validation."""

    user_texts = [
        content.strip()
        for line in dialogue_text.splitlines()
        for role, separator, content in [line.partition(":")]
        if separator and role.strip() == "user" and content.strip()
    ]
    if latest_utterance.strip() and latest_utterance.strip() not in user_texts:
        user_texts.append(latest_utterance.strip())
    return "\n".join(user_texts)


def _validate_dialogue_extraction(extraction: Any, user_source_text: str) -> Any:
    from app.profile.nodes import _validate_dialogue_evidence

    return _validate_dialogue_evidence(extraction, user_source_text)


def _dialogue_history_for_turn(step_data: dict[str, Any], *, current_prompt: str, utterance: str) -> str:
    messages = _dialogue_history_messages(step_data)
    if current_prompt and (not messages or messages[-1].get("content") != current_prompt):
        messages.append({"role": "assistant", "content": current_prompt})
    messages.append({"role": "user", "content": utterance})
    return _dialogue_messages_text(messages)


def _store_dialogue_history(step_data: dict[str, Any], dialogue_history: str, *, assistant_text: str | None = None) -> None:
    messages = _messages_from_dialogue_text(dialogue_history)
    if assistant_text:
        messages.append({"role": "assistant", "content": assistant_text})
    step_data["dialogue_history"] = messages[-12:]


def _dialogue_history_messages(step_data: dict[str, Any]) -> list[dict[str, str]]:
    raw = step_data.get("dialogue_history")
    if not isinstance(raw, list):
        return []
    messages: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        content = str(item.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    return messages


def _messages_from_dialogue_text(text: str) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for line in text.splitlines():
        role, sep, content = line.partition(":")
        role = role.strip()
        content = content.strip() if sep else line.strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    return messages


def _dialogue_messages_text(messages: list[dict[str, str]]) -> str:
    return "\n".join(
        f"{item.get('role', 'unknown')}: {item.get('content', '')}"
        for item in messages[-12:]
        if item.get("content")
    )


async def _profile_from_dialogue_scoring(user_id: str, evidence_cards: list[EvidenceCard]) -> Profile:
    from app.profile.nodes import _profile_from_scoring_results

    scoring: dict[DimensionKey, ScoringResult] = await score_profile(evidence_cards)
    return _profile_from_scoring_results(user_id=user_id, scoring=scoring, evidence_cards=evidence_cards)


async def _finish_dialogue_profile_session(session: ProfileSession, *, db_path: Path) -> ProfileSession:
    if session.finished:
        return session
    baseline = _dialogue_radar_baseline(session, db_path=db_path)
    profile = await _profile_from_dialogue_scoring(session.user_id, session.evidence_cards)
    final_radar = _radar_payload(profile)
    changes = _radar_changes(
        baseline,
        final_radar,
        evidence_cards=session.evidence_cards,
    )

    session.finished = True
    session.current_step = None
    session.updated_at = datetime.now(UTC)
    session.step_data["radar_changes"] = changes
    session.step_data["summary_text"] = _radar_summary_text(changes)
    _dialogue_identity_cards_view(
        session.evidence_cards,
        fusion=_dialogue_fusion(session.evidence_cards),
        step_data=session.step_data,
        consume_revealed_now=False,
    )
    # The dialogue entry only stages a proposal; confirmation remains explicit.
    from app.profile.goal_agent import run_goal_agent

    user_goal_text = "\n".join(
        str(item.get("content") or "")
        for item in _dialogue_history_messages(session.step_data)
        if item.get("role") == "user"
    ).strip()
    if user_goal_text:
        proposal = await run_goal_agent(user_goal_text, profile=profile)
        session.step_data["goal_proposal"] = proposal.model_dump(mode="json")
    if not session.persisted:
        for card in session.evidence_cards:
            add_evidence(session.user_id, card, db_path=db_path)
        session.persisted = True
        session.persisted_db_path = str(db_path)
    upsert_profile(profile, db_path=db_path)
    _save_profile_session(session, db_path=db_path)
    return session


def _persist_dialogue_live_update(
    session: ProfileSession,
    *,
    cards: list[EvidenceCard],
    profile: Profile,
    db_path: Path,
) -> None:
    for card in cards:
        add_evidence(session.user_id, card, db_path=db_path)
    if cards:
        session.persisted = True
        session.persisted_db_path = str(db_path)
    upsert_profile(profile, db_path=db_path)
    _save_profile_session(session, db_path=db_path)


def _dialogue_state_payload(
    session: ProfileSession,
    *,
    target: DimensionKey | None,
    quick_reply_target: DimensionKey | None = None,
    quick_replies: list[str] | None = None,
    converged: bool,
    error: str | None = None,
) -> dict[str, Any]:
    fusion = _dialogue_fusion(session.evidence_cards)
    payload: dict[str, Any] = {
        "session_id": session.session_id,
        "turn_index": session.step_index,
        "target": target,
        "target_label": DIALOGUE_TARGET_LABELS.get(target, target) if target else None,
        "quick_replies": quick_replies if quick_replies is not None else (_dialogue_quick_replies(quick_reply_target or target) if (quick_reply_target or target) else []),
        "converged": converged,
        "finished": session.finished,
        "profile_progress": _dialogue_profile_progress(session.evidence_cards, fusion=fusion),
        "evidence_panel": _dialogue_evidence_panel(session.evidence_cards, fusion=fusion),
        "identity_cards": _dialogue_identity_cards_view(
            session.evidence_cards,
            fusion=fusion,
            step_data=session.step_data,
        ),
    }
    if error:
        payload["error"] = error
    return payload


def _dialogue_profile_progress(
    evidence_cards: list[EvidenceCard],
    *,
    fusion: dict[DimensionKey, FusionResult] | None = None,
) -> dict[str, Any]:
    fused = fusion or _dialogue_fusion(evidence_cards)
    dimensions = [
        {
            "key": dimension,
            "label": DIALOGUE_TARGET_LABELS.get(dimension, DIMENSION_LABELS[dimension]),
            "confidence": fused[dimension].confidence,
            "collected": _dialogue_evidence_collected(dimension, fused[dimension], evidence_cards),
            "status": (
                "已收集到"
                if _dialogue_evidence_collected(dimension, fused[dimension], evidence_cards)
                else "待取证"
            ),
        }
        for dimension in DIMENSION_LABELS
    ]
    collected = sum(1 for item in dimensions if item["collected"])
    return {
        "collected": collected,
        "total": len(dimensions),
        "threshold": LOW_CONFIDENCE_THRESHOLD,
        "dimensions": dimensions,
    }


def _dialogue_evidence_panel(
    evidence_cards: list[EvidenceCard],
    *,
    fusion: dict[DimensionKey, FusionResult] | None = None,
) -> list[dict[str, Any]]:
    fused = fusion or _dialogue_fusion(evidence_cards)
    return [
        {
            "key": dimension,
            "label": DIALOGUE_TARGET_LABELS.get(dimension, DIMENSION_LABELS[dimension]),
            "score": fused[dimension].score,
            "value": _dialogue_fusion_value(fused[dimension]),
            "tag": fused[dimension].label,
            "distribution": fused[dimension].distribution,
            "confidence": fused[dimension].confidence,
            "status": (
                "已收集到"
                if _dialogue_evidence_collected(dimension, fused[dimension], evidence_cards)
                else "待取证"
            ),
            "evidence_refs": fused[dimension].evidence_refs,
            "narrative": fused[dimension].narrative,
            "quotes": _dialogue_dimension_quotes(dimension, evidence_cards),
        }
        for dimension in DIMENSION_LABELS
    ]


def _quality_audit(card: EvidenceCard) -> dict[str, Any]:
    """把"这条证据的质量分是怎么来的"写清楚，供审计直接展示。

    五项里三项由模型判（表达清晰、内容具体、一致性），两项由规则算
    （来源可靠看证据类型、新鲜度看来源轮次）。三项模型判断项各有三档区间，
    模型先判档、再在档内选点；档位由系统按落点反推，所以"档"和"数"不会
    互相矛盾。五项各占 20%，几何平均后才是这条证据的贡献值——短板会拉低
    整体，所以一句含糊的话很难靠其余四项撑起来。
    """

    raw = card.quality_factors or card.objective_signal.get("quality_factors") or {}
    try:
        factors = QualityFactors.model_validate(raw) if raw else QualityFactors()
    except Exception:  # noqa: BLE001 - 历史卡可能存了残缺结构，不能让审计整体失败
        factors = QualityFactors()
    model_judged = ["expression_clarity", "content_specificity", "consistency"]
    rule_computed = ["source_reliability", "freshness"]
    values = factors.model_dump(mode="json")
    items = []
    for key, value in values.items():
        if not isinstance(value, (int, float)):
            continue
        source = "rule" if key in rule_computed else "model"
        tier = quality_tier_of(float(value)) if source == "model" else None
        span = QUALITY_FACTOR_TIERS.get(tier) if tier else None
        items.append({
            "key": key,
            "label": QUALITY_FACTOR_LABELS.get(key, key),
            "value": value,
            "source": source,
            "tier": tier,
            "tier_label": QUALITY_TIER_LABELS.get(tier) if tier else None,
            "tier_range": [span[0], span[1]] if span else None,
        })
    weights = [float(v) for v in values.values() if isinstance(v, (int, float))]
    formula = (
        "(" + " × ".join(f"{w:g}" for w in weights) + f")^(1/{len(weights)})"
        if weights else ""
    )
    return {
        "factors": values,
        "items": items,
        "score": geometric_quality(factors),
        "model_judged": model_judged,
        "rule_computed": rule_computed,
        "weight_note": "五项等权，各占 20%，几何平均：短板会明显拉低整体。",
        "formula": formula,
        "evidence_type": card.objective_signal.get("evidence_type"),
        "reason": card.quality_reason or str(card.objective_signal.get("quality_reason") or ""),
        "thinking": card.quality_thinking or str(card.objective_signal.get("quality_thinking") or ""),
    }


TIMELINE_MAX_TURNS = 24


def _card_quality_score(card: EvidenceCard) -> float | None:
    """证据质量分；算不出来返回 None——一条坏卡不该让整条时间线断掉。"""

    try:
        value = _quality_audit(card)["score"]
    except Exception:  # noqa: BLE001 - 审计是旁路，任何异常都不能影响主流程
        return None
    return round(float(value), 4) if isinstance(value, (int, float)) else None


def _append_dimension_timeline(
    step_data: dict[str, Any],
    *,
    turn_index: int,
    fusion: dict[DimensionKey, FusionResult],
    cards: list[EvidenceCard],
    all_cards: list[EvidenceCard],
) -> None:
    """每轮给每个维度记一行「证据 / 质量 / 置信度 / 分数」，跨轮累积。

    置信度每轮重算、维度分只在触发轮变，两个数天然来自不同时刻。以前审计
    只显示当前值，看到「80 分 + 71%」会以为 80 分是在 71% 下算的。逐轮记
    一行之后，评分轮那行的置信度就是"评分时"的置信度，不必再单独存快照。
    """

    timeline = step_data.get("dimension_timeline")
    if not isinstance(timeline, dict):
        timeline = step_data["dimension_timeline"] = {}
    scoring_audit = step_data.get("component_scoring_audit", {})
    for dimension, result in fusion.items():
        rows = timeline.get(dimension)
        if not isinstance(rows, list):
            rows = timeline[dimension] = []
        # 同一轮重试或重放时覆盖最后一行，避免出现两条"第 N 轮"。
        if rows and rows[-1].get("turn") == turn_index:
            rows.pop()
        new_scores = [
            score
            for score in (_card_quality_score(card) for card in cards if card.dimension == dimension)
            if score is not None
        ]
        all_scores = [
            score
            for score in (_card_quality_score(card) for card in all_cards if card.dimension == dimension)
            if score is not None
        ]
        entry = scoring_audit.get(dimension) if isinstance(scoring_audit, dict) else None
        rows.append(
            {
                "turn": turn_index,
                "evidence": sum(1 for card in all_cards if card.dimension == dimension),
                "new_evidence": sum(1 for card in cards if card.dimension == dimension),
                "watermark": _dialogue_evidence_watermark(dimension, all_cards),
                "confidence": round(float(result.confidence or 0.0), 4),
                # 本轮新增证据的平均质量（这轮说得清不清楚）与累计平均（证据底子）。
                "quality_new": round(sum(new_scores) / len(new_scores), 4) if new_scores else None,
                "quality_avg": round(sum(all_scores) / len(all_scores), 4) if all_scores else None,
                "score": result.radar_score,
                "scored_this_turn": bool(isinstance(entry, dict) and entry.get("scoring_turn") == turn_index),
            }
        )
        if len(rows) > TIMELINE_MAX_TURNS:
            del rows[:-TIMELINE_MAX_TURNS]


def _dialogue_audit_record(
    *,
    turn_index: int,
    target: DimensionKey,
    next_target: DimensionKey,
    utterance: str,
    cards: list[EvidenceCard],
    fusion_before: dict[DimensionKey, FusionResult],
    fusion: dict[DimensionKey, FusionResult],
    extraction_contract: dict[str, Any] | None = None,
    prior_cards: list[EvidenceCard] | None = None,
    all_cards: list[EvidenceCard] | None = None,
    scoring_audit: dict[str, Any] | None = None,
    timeline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a developer-readable trace without exposing prompts or reasoning.

    The record differentiates agent-authored semantic assessments from the
    system's deterministic quote validation, source grouping and aggregation.
    It is deliberately a result trace, never a model chain-of-thought dump.
    """

    evidence: list[dict[str, Any]] = []
    for card in cards:
        signal = card.objective_signal
        assessment = signal.get("agent_assessment")
        evidence.append(
            {
                "dimension": card.dimension,
                "dimension_label": DIALOGUE_TARGET_LABELS.get(card.dimension, DIMENSION_LABELS[card.dimension]),
                "fact": card.fact,
                "normalized_claim": card.normalized_claim,
                "assessment": signal.get("assessment"),
                "supports": card.supports or signal.get("supports", []),
                "evidence_id": card.evidence_id,
                "quotes": _evidence_quote_texts(card),
                "basis": card.basis,
                "extraction_confidence": card.extraction_confidence,
                "extraction_method": card.extraction_method,
                "agent_assessment": assessment if isinstance(assessment, dict) else None,
                "validation": {
                    "quote_traceable": bool(_evidence_quote_texts(card)),
                    "same_response_unit": str(signal.get("evidence_unit_id") or ""),
                },
                "component": card.component,
                "evidence_group_id": card.evidence_group_id or signal.get("evidence_group_id"),
                "source_turn_ids": card.source_turn_ids or [turn_index],
                "semantic_relation": card.semantic_relation or signal.get("semantic_relation") or "new",
                "related_evidence_ids": card.related_evidence_ids,
                "confirmed_aspects": card.confirmed_aspects or signal.get("confirmed_aspects", []),
                "pending_aspects": card.pending_aspects or signal.get("pending_aspects", []),
                "quality_factors": card.quality_factors or signal.get("quality_factors", {}),
                "quality_audit": _quality_audit(card),
            }
        )
    dimensions = []
    extraction_contract = extraction_contract or {}
    prior_cards = prior_cards or []
    all_cards = all_cards or [*prior_cards, *cards]
    scoring_audit = scoring_audit or {}
    timeline = timeline or {}
    scan_by_dimension = {
        str(item.get("dimension")): item
        for item in extraction_contract.get("dimension_scan", [])
        if isinstance(item, dict)
    }
    component_collection_by_key = {
        (str(item.get("dimension")), str(item.get("component"))): item
        for item in extraction_contract.get("component_collection", [])
        if isinstance(item, dict)
    }
    for dimension, result in fusion.items():
        before = fusion_before[dimension]
        dimension_cards = [card for card in cards if card.dimension == dimension]
        prior_dimension_cards = [card for card in prior_cards if card.dimension == dimension]
        all_dimension_cards = [card for card in all_cards if card.dimension == dimension]
        deduplicated_cards, conflict_cards, dropped_evidence_ids = deduplicate_component_cards(all_dimension_cards)
        scoring_package_cards = [*deduplicated_cards, *conflict_cards]
        prior_components = {card.component for card in prior_dimension_cards if card.component}
        collected_components = {card.component for card in all_dimension_cards if card.component and card.collection_status == "scorable"}
        matrix_before = {component: component in prior_components for component in COMPONENTS[dimension]}
        matrix_after = {component: component in collected_components for component in COMPONENTS[dimension]}
        pending_components = [component for component in COMPONENTS[dimension] if component not in collected_components]
        missing_reasons = {
            component: next((
                str(missing)
                for item in all_dimension_cards if item.component == component
                for missing in item.pending_aspects if str(missing).strip()
            ), COMPONENT_RUBRICS[dimension][component]["followup_direction"])
            for component in pending_components
        }
        dimension_scoring = scoring_audit.get(dimension, {}) if isinstance(scoring_audit.get(dimension), dict) else {}
        scored = dimension_scoring.get("component_scores") or (result.metadata.get("component_collection", {}).get("component_scores", {}) if isinstance(result.metadata.get("component_collection"), dict) else {})
        missing_scores = [component for component in COMPONENTS[dimension] if component not in scored]
        # 出分只看覆盖度门槛，不看"组件是否收齐"。4/5 已收集时覆盖 80%、
        # 门槛已开，维度就有分；旧实现把 5/5 当成出分条件，于是这类维度
        # 明明有分可算，审计里却写着"评分暂缓"、雷达一直是暗的。
        scored_coverage = round(len(scored) / len(COMPONENTS[dimension]), 4)
        if result.radar_score is None:
            missing_labels = [DIMENSION_COMPONENT_LABELS[dimension][key] for key in pending_components]
            deferred_reason = (
                f"评分暂缓：组件覆盖 {len(scored)}/{len(COMPONENTS[dimension])}（{scored_coverage:.0%}）"
                f"未达 {COVERAGE_THRESHOLD:.0%} 门槛"
                + (f"，还缺 {', '.join(missing_labels)}" if missing_labels else "")
                + "。"
            )
        else:
            deferred_reason = None
        component_records = []
        rescored_components = set(dimension_scoring.get("rescored_components") or [])
        carried_components = set(dimension_scoring.get("carried_components") or [])
        for component in COMPONENTS[dimension]:
            component_cards = [card for card in all_dimension_cards if card.component == component]
            rubric = COMPONENT_RUBRICS[dimension][component]
            score_card = next((card for card in component_cards if isinstance(card.objective_signal.get("component_score"), (int, float))), None)
            component_records.append({
                "key": component,
                "label": DIMENSION_COMPONENT_LABELS[dimension][component],
                "weight": COMPONENT_WEIGHTS[dimension][component],
                "rubric": rubric,
                "collected": matrix_after[component],
                "score": scored.get(component),
                # 这个分是刚评的，还是上一轮评完这轮没动？重评只覆盖"证据变了的
                # 组件"，不区分的话用户会以为所有组件每轮都重新判过。
                "score_source": (
                    "rescored" if component in rescored_components
                    else "carried" if component in carried_components
                    else None
                ),
                "reason": score_card.objective_signal.get("component_score_reason") if score_card else None,
                "conflict_handling": score_card.objective_signal.get("component_conflict_handling") if score_card else None,
                "evidence_ids": [card.evidence_id for card in component_cards],
                # 这个组件的三师落点：审计里要能回答"为什么是 58 分"。
                "jury": [
                    {
                        "teacher": name,
                        "score": item.get("score"),
                        "reason": "；".join(str(line) for line in (item.get("reasoning_chain") or []) if str(line).strip()),
                    }
                    for name, item in (dimension_scoring.get("component_jury") or {}).get(component, {}).items()
                    if isinstance(item, dict)
                ],
                "jury_mean": scored.get(component),
            })
        scan_item = scan_by_dimension.get(dimension, {})
        assessments = [
            card.objective_signal.get("agent_assessment")
            for card in dimension_cards
            if isinstance(card.objective_signal.get("agent_assessment"), dict)
        ]
        confirmed_aspects = list(
            dict.fromkeys(
                aspect
                for assessment in assessments
                for aspect in assessment.get("confirmed_aspects", [])
                if isinstance(aspect, str) and aspect.strip()
            )
        )[:5]
        dimensions.append(
            {
            "key": dimension,
            "label": DIALOGUE_TARGET_LABELS.get(dimension, DIMENSION_LABELS[dimension]),
            "status": "本轮已收集" if dimension_cards else "本轮未收集",
            "confidence_before": before.confidence,
            "radar_score": result.radar_score,
            "confidence": result.confidence,
            "coverage": result.coverage,
            "confidence_breakdown": result.confidence_breakdown,
            "confidence_formula": str(result.metadata.get("confidence_formula") or ""),
            "radar_formula": str(result.metadata.get("radar_formula") or ""),
            "confidence_units": result.metadata.get("confidence_units", []),
            "confirmed_aspects": confirmed_aspects,
            "unknown_reasons": result.unknown_reasons,
            "observed_distribution": result.distribution,
            "evidence_count": len(dimension_cards),
            "component_collection": result.metadata.get("component_collection", {}),
            "scoring_deferred": bool(result.metadata.get("component_collection", {}).get("scoring_phase") == "collecting") if isinstance(result.metadata.get("component_collection"), dict) else False,
            "component_matrix": component_records,
            "matrix_before": matrix_before,
            "matrix_after": matrix_after,
            "pending_components": pending_components,
            "followup_reasons": missing_reasons,
            "dimension_scan": scan_item,
            "score_formula": "Σ(component_score × component_weight)",
            "score_weight_sum": sum(COMPONENT_WEIGHTS[dimension].values()),
            "deduplicated_evidence_package": [
                {
                    "evidence_id": card.evidence_id,
                    "component": card.component,
                    "quote": card.quote,
                    "claim": card.normalized_claim or card.fact,
                    "group_id": card.evidence_group_id,
                    "relation": card.semantic_relation,
                    "source_turn_ids": card.source_turn_ids,
                    "conflict": card.semantic_relation == "conflict",
                    "quality_score": _quality_audit(card)["score"],
                    "quality_reason": _quality_audit(card)["reason"],
                }
                for card in scoring_package_cards
            ],
            "confidence_audit": result.metadata.get("component_confidence", {}),
            "scoring_phase": "scored" if result.radar_score is not None else "collecting",
            "scoring_provenance": dimension_scoring.get("provenance"),
            "scoring_attempts": dimension_scoring.get("scoring_attempts", 0),
            "scoring_turn": dimension_scoring.get("scoring_turn"),
            # 评分节奏：这次为什么评、下次什么时候评。
            "scoring_trigger": dimension_scoring.get("scoring_trigger"),
            "scoring_watermark": dimension_scoring.get("evidence_watermark"),
            "current_watermark": _dialogue_evidence_watermark(dimension, all_cards),
            # 逐轮时间线：置信度每轮都动、维度分只在触发轮动，并排看才知道
            # 当前这个分是哪一轮、在多少置信度下评出来的。
            "confidence_timeline": timeline.get(dimension) if isinstance(timeline.get(dimension), list) else [],
            "next_scoring_watermark": (
                int(dimension_scoring["evidence_watermark"]) + SCORING_RERUN_EVIDENCE_STEP
                if isinstance(dimension_scoring.get("evidence_watermark"), int) else None
            ),
            "rescored_components": sorted(rescored_components),
            "carried_components": sorted(carried_components),
            "unscored_components": missing_scores,
            "dimension_summary": dimension_scoring.get("dimension_summary"),
            "scoring_input": {
                "dimension": dimension,
                "components": [
                    {
                        "key": component,
                        "weight": COMPONENT_WEIGHTS[dimension][component],
                        "rubric": COMPONENT_RUBRICS[dimension][component],
                        "deduplicated_evidence": [
                            {
                                "evidence_id": card.evidence_id,
                                "quote": card.quote,
                                "claim": card.normalized_claim or card.fact,
                                "source_group": card.evidence_group_id,
                                "source_turn_ids": card.source_turn_ids,
                                "turn_label": "、".join(f"第{index}轮" for index in card.source_turn_ids) or "本轮",
                                "relation": card.semantic_relation,
                                "quality_score": _quality_audit(card)["score"],
                            }
                            for card in scoring_package_cards
                            if card.component == component and card.semantic_relation != "conflict"
                        ],
                        "conflicts": [
                            {"evidence_id": card.evidence_id, "quote": card.quote, "claim": card.normalized_claim or card.fact}
                            for card in conflict_cards
                            if card.component == component
                        ],
                        "source_groups": sorted({card.evidence_group_id for card in scoring_package_cards if card.component == component and card.evidence_group_id}),
                    }
                    for component in COMPONENTS[dimension]
                ],
                "deduplicated_evidence_ids": dropped_evidence_ids,
            },
            "scoring_output": [
                {
                    "component": component,
                    "score": scored.get(component),
                    "source": "rescored" if component in rescored_components else "carried",
                    "reason": next((card.objective_signal.get("component_score_reason") for card in all_dimension_cards if card.component == component and card.objective_signal.get("component_score_reason")), None),
                    "conflict_handling": next((card.objective_signal.get("component_conflict_handling") for card in all_dimension_cards if card.component == component and card.objective_signal.get("component_conflict_handling")), None),
                    "jury": [
                        {"teacher": name, "score": item.get("score"), "reason": "；".join(str(line) for line in (item.get("reasoning_chain") or []) if str(line).strip())}
                        for name, item in (dimension_scoring.get("component_jury") or {}).get(component, {}).items()
                        if isinstance(item, dict)
                    ],
                }
                for component in COMPONENTS[dimension]
                if component in scored
            ],
            # 三师会诊的结构化判分依据：分数、逐条比对摘要、评语。
            # 这是"模型怎么打分的"唯一可展示来源，不是私有思维链。
            # 维度级三师现在是"逐组件三师 → 按权重合成"，不是模型另给的一组印象分。
            "jury_panel": [
                {
                    "teacher": name,
                    "score": item.get("score"),
                    "reasoning_chain": [str(line) for line in (item.get("reasoning_chain") or []) if str(line).strip()],
                    "comment": item.get("comment"),
                }
                for name, item in (dimension_scoring.get("jury") or {}).items()
                if isinstance(item, dict)
            ],
            "jury_derivation": "每位老师先对自己的组件落点按固定权重合成维度分，再取三人平均；维度分本身仍由组件分加权得出。",
            "teacher_dimension_scores": dimension_scoring.get("teacher_dimension_scores") or {},
            "jury_mean": dimension_scoring.get("jury_mean"),
            "scoring_warnings": dimension_scoring.get("warnings") or [],
            "scoring_deferred_reason": deferred_reason,
        }
        )
    return {
        "turn_index": turn_index,
        "stages": [
            {"actor": "模型", "label": "七维扫描", "detail": f"{extraction_contract.get('extraction_method', 'unknown')}；扫描 {len(scan_by_dimension)}/7 维；产出 {len(evidence)} 条可追溯原子证据。"},
            {"actor": "系统", "label": "原话与来源校验", "detail": f"保留 {len(cards)} 条可追溯证据；同轮来源关联按 evidence_group_id 复用，不重复计为独立来源。"},
            {"actor": "系统", "label": "组件矩阵更新", "detail": f"七维矩阵均已比较本轮前后状态；本轮涉及 {sum(1 for item in dimensions if item['status'] == '本轮已收集')} 个维度。"},
            {"actor": "模型", "label": "组件评分门禁", "detail": f"已评分 {sum(1 for item in dimensions if item['scoring_phase'] == 'scored')} 个维度；其余维度保留待收组件并暂缓评分。"},
            {"actor": "系统", "label": "分数与确认度", "detail": "覆盖度达到 60% 的维度进入固定权重统分，未达门槛的维度保留待收组件；确认度只随已出分的维度展示。"},
            {"actor": "系统", "label": "补问规划", "detail": f"下一目标：{DIALOGUE_TARGET_LABELS.get(next_target, next_target)}；优先补齐该维度仍缺少的组件。"},
            {"actor": "模型", "label": "回答组织", "detail": "回答模型在扫描、组件缺项和补问目标确定后生成可见回应。"},
        ],
        "current_target": target,
        "next_target": next_target,
        "user_input": utterance,
        "model_result": {
            "method": cards[0].extraction_method if cards else extraction_contract.get("extraction_method", "no_evidence"),
            "failure_reason": extraction_contract.get("extraction_error"),
            "scan_available": bool(scan_by_dimension),
            "scan_complete": set(scan_by_dimension) == set(DIMENSION_LABELS),
            "collected_count": len(cards),
            "evidence": evidence,
            "combination_type": extraction_contract.get("combination_type"),
            "dimension_scan": [
                {
                    "dimension": dimension,
                    **scan_by_dimension.get(dimension, {
                        "status": "evidence_found" if any(card.dimension == dimension for card in cards) else "no_usable_evidence",
                        "reason": "本轮存在可追溯原子证据。" if any(card.dimension == dimension for card in cards) else "模型未返回扫描明细；系统只标记本轮未收集，不推断为低分。",
                    }),
                }
                for dimension in DIMENSION_LABELS
            ],
            "component_collection": extraction_contract.get("component_collection", []),
            "excluded_candidates": [
                {"dimension": item.get("dimension"), "component": item.get("component"), "reason": item.get("reason")}
                for scan in scan_by_dimension.values()
                for item in scan.get("excluded_candidates", [])
                if isinstance(item, dict)
            ],
        },
        "system_result": {
            "confidence_explanation": "每个独立来源先计算 证据贡献 x 独立性 x 时间衰减；再用 1-连乘(1-有效贡献) 合并。相同回答只计一个最强单元。",
            "radar_explanation": "雷达值是模型给出的维度估计按 证据贡献、覆盖度、优先性 和时间衰减加权平均；它不等同于卡牌组成占比。",
            "dimensions": dimensions,
        },
    }


def _store_dialogue_audit(step_data: dict[str, Any], record: dict[str, Any]) -> None:
    existing = _dialogue_developer_audit(step_data)
    step_data["developer_audit"] = [*existing, record][-24:]


def _dialogue_developer_audit(step_data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = step_data.get("developer_audit")
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _dialogue_evidence_collected(
    dimension: DimensionKey,
    result: FusionResult,
    evidence_cards: list[EvidenceCard],
) -> bool:
    if result.confidence >= LOW_CONFIDENCE_THRESHOLD:
        return True
    has_cards = any(card.dimension == dimension for card in evidence_cards)
    has_result = result.score is not None or result.label is not None or bool(result.distribution)
    return has_cards and has_result


def _dialogue_fusion_value(result: FusionResult) -> str:
    if result.label:
        return result.label
    if result.score is not None:
        return f"{round(result.score)} 分"
    if result.distribution:
        return max(result.distribution.items(), key=lambda item: item[1])[0]
    return "待取证"


def _dialogue_dimension_quotes(dimension: DimensionKey, evidence_cards: list[EvidenceCard]) -> list[dict[str, Any]]:
    quotes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, card in enumerate((item for item in evidence_cards if item.dimension == dimension), start=1):
        raw_quotes = _evidence_quote_texts(card)
        for quote in raw_quotes:
            text = str(quote or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            quotes.append(
                {
                    "text": text,
                    "fact": card.fact,
                    "source": card.source,
                    "ref": f"{card.source}#{index}",
                }
            )
    return quotes


def _evidence_quote_texts(card: EvidenceCard) -> list[str]:
    if card.quote:
        return [card.quote]
    value = card.objective_signal.get("evidence")
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _dialogue_identity_card_revealed_flags(step_data: dict[str, Any]) -> dict[str, bool]:
    raw = step_data.get("identity_cards_revealed")
    if isinstance(raw, dict):
        flags = {str(key): bool(value) for key, value in raw.items()}
    else:
        flags = {}
    step_data["identity_cards_revealed"] = flags
    return flags


def _dialogue_identity_card_revealed_now_flags(step_data: dict[str, Any]) -> set[str]:
    raw = step_data.get("identity_cards_revealed_now")
    flags = {str(key) for key in raw} if isinstance(raw, list) else set()
    step_data["identity_cards_revealed_now"] = sorted(flags)
    return flags


def _dialogue_identity_cards_view(
    evidence_cards: list[EvidenceCard],
    *,
    fusion: dict[DimensionKey, FusionResult] | None = None,
    revealed_flags: dict[str, bool] | None = None,
    keep_revealed: bool = True,
    step_data: dict[str, Any] | None = None,
    consume_revealed_now: bool = True,
) -> list[dict[str, Any]]:
    """Build the seven-dimension identity-card view from already-fused evidence."""

    fused = fusion or _dialogue_fusion(evidence_cards)
    flags = _dialogue_identity_card_revealed_flags(step_data) if step_data is not None else (revealed_flags if revealed_flags is not None else {})
    revealed_now_flags = _dialogue_identity_card_revealed_now_flags(step_data) if step_data is not None else set()
    items = [
        _identity_distribution_item(
            dimension,
            fused[dimension],
            evidence_cards,
            flags,
            revealed_now_flags,
            keep_revealed=keep_revealed,
        )
        for dimension in ("learning_goal", "motivation", "resource_preference", "learning_style")
    ] + [
        _identity_level_item("knowledge_base", fused["knowledge_base"], evidence_cards, flags, revealed_now_flags, keep_revealed=keep_revealed),
        _identity_level_item("pace", fused["pace"], evidence_cards, flags, revealed_now_flags, keep_revealed=keep_revealed),
        _identity_tags_item("weak_points", fused["weak_points"], evidence_cards, flags, revealed_now_flags, keep_revealed=keep_revealed),
    ]
    if step_data is not None:
        step_data["identity_cards_revealed_now"] = [] if consume_revealed_now else sorted(revealed_now_flags)
    return items


def _identity_distribution_item(
    dimension: DimensionKey,
    result: FusionResult,
    evidence_cards: list[EvidenceCard],
    revealed_flags: dict[str, bool],
    revealed_now_flags: set[str],
    *,
    keep_revealed: bool,
) -> dict[str, Any]:
    distribution = _normalize_identity_distribution(result.distribution)
    cards = [
        {
            "canonical_label": label,
            "display_name": _identity_distribution_display_name(dimension, label, evidence_cards),
            "meaning": _identity_card_meaning(dimension, label, evidence_cards),
            "ratio": ratio,
            "quotes": _identity_quotes_for_label(dimension, label, evidence_cards),
            "evidence_refs": _identity_evidence_refs_for_label(dimension, label, evidence_cards),
        }
        for label, ratio in distribution.items()
    ]
    revealable, revealed, revealed_now = _identity_reveal_state(
        dimension, result.confidence, revealed_flags, revealed_now_flags, keep_revealed=keep_revealed,
        component_pending=_dialogue_components_pending(result),
    )
    return _identity_card_view(
        dimension=dimension,
        kind="distribution",
        result=result,
        evidence_cards=evidence_cards,
        items=cards,
        revealable=revealable,
        revealed=revealed,
        revealed_now=revealed_now,
        display_value=_dialogue_fusion_value(result),
        distribution=distribution,
    )


def _identity_level_item(
    dimension: DimensionKey,
    result: FusionResult,
    evidence_cards: list[EvidenceCard],
    revealed_flags: dict[str, bool],
    revealed_now_flags: set[str],
    *,
    keep_revealed: bool,
) -> dict[str, Any]:
    level_name = _identity_level_name(dimension, result.score)
    anchors = _identity_level_anchors(dimension, result.score, evidence_cards)
    cards = [
            {
                "level_name": level_name,
                "anchor": anchors.get("anchor"),
                "score": result.score,
                **({"daily_minutes": anchors.get("daily_minutes"), "target_days": anchors.get("target_days")} if dimension == "pace" else {}),
                "quotes": [quote["text"] for quote in _dialogue_dimension_quotes(dimension, evidence_cards)],
                "evidence_refs": result.evidence_refs,
            }
        ]
    revealable, revealed, revealed_now = _identity_reveal_state(
        dimension, result.confidence, revealed_flags, revealed_now_flags, keep_revealed=keep_revealed,
        component_pending=_dialogue_components_pending(result),
    )
    return _identity_card_view(
        dimension=dimension,
        kind="level",
        result=result,
        evidence_cards=evidence_cards,
        items=cards,
        revealable=revealable,
        revealed=revealed,
        revealed_now=revealed_now,
        display_value=_identity_level_display_value(dimension, level_name, result.score, anchors, revealable=revealable),
    )


def _identity_tags_item(
    dimension: DimensionKey,
    result: FusionResult,
    evidence_cards: list[EvidenceCard],
    revealed_flags: dict[str, bool],
    revealed_now_flags: set[str],
    *,
    keep_revealed: bool,
) -> dict[str, Any]:
    fused_distribution = _normalize_identity_distribution(result.distribution)
    distribution = {
        label: ratio
        for label, ratio in fused_distribution.items()
        if _identity_has_weak_point_evidence(label, evidence_cards)
    }
    cards = [
        {
            "label": label,
            "strength": _identity_tag_strength(dimension, label, evidence_cards),
            "quotes": _identity_quotes_for_label(dimension, label, evidence_cards),
            "evidence_refs": _identity_evidence_refs_for_label(dimension, label, evidence_cards),
        }
        for label in distribution
    ]
    revealable, revealed, revealed_now = _identity_reveal_state(
        dimension, result.confidence, revealed_flags, revealed_now_flags, keep_revealed=keep_revealed,
        component_pending=_dialogue_components_pending(result),
    )
    return _identity_card_view(
        dimension=dimension,
        kind="tags",
        result=result,
        evidence_cards=evidence_cards,
        items=cards,
        revealable=revealable,
        revealed=revealed,
        revealed_now=revealed_now,
        display_value=cards[0]["label"] if cards else "待取证",
        distribution=distribution,
    )


def _identity_reveal_state(
    dimension: DimensionKey,
    confidence: float,
    revealed_flags: dict[str, bool],
    revealed_now_flags: set[str],
    *,
    keep_revealed: bool,
    component_pending: bool = False,
) -> tuple[bool, bool, bool]:
    if component_pending:
        # A partial component set must not reveal a final identity card or
        # persist a reveal flag that could open it before scoring is complete.
        revealed_flags.pop(dimension, None)
        revealed_now_flags.discard(dimension)
        return False, False, False
    revealable = confidence >= LOW_CONFIDENCE_THRESHOLD
    previous = bool(revealed_flags.get(dimension))
    revealed = revealable or (keep_revealed and previous)
    if revealable and not previous:
        revealed_now_flags.add(dimension)
    if revealed:
        revealed_flags[dimension] = True
    return revealable, revealed, dimension in revealed_now_flags


def _dialogue_components_pending(result: FusionResult) -> bool:
    collection = result.metadata.get("component_collection")
    return isinstance(collection, dict) and collection.get("scoring_phase") == "collecting"


def _identity_card_view(
    *,
    dimension: DimensionKey,
    kind: str,
    result: FusionResult,
    evidence_cards: list[EvidenceCard],
    items: list[dict[str, Any]],
    revealable: bool,
    revealed: bool,
    revealed_now: bool,
    display_value: str,
    distribution: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Serialize an identity card without deriving another score or confidence."""

    quotes = [item["text"] for item in _dialogue_dimension_quotes(dimension, evidence_cards)]
    component_collection = result.metadata.get("component_collection")
    component_pending = isinstance(component_collection, dict) and component_collection.get("scoring_phase") == "collecting"
    if component_pending:
        revealable = False
        revealed = False
        revealed_now = False
    confidence_status = "已取证" if revealable else "待取证"
    provenance = _identity_provenance(dimension, evidence_cards)
    if not any(card.dimension == dimension for card in evidence_cards):
        pending_reason = "尚未收集该维度的可追溯证据。"
    elif component_pending:
        pending_reason = f"已收集 {component_collection.get('collected', 0)}/{component_collection.get('total', 0)} 个组件，评分暂缓。"
    elif not revealable:
        pending_reason = f"融合置信度 {result.confidence:.2f}，尚未达到 {LOW_CONFIDENCE_THRESHOLD:.2f} 的翻开阈值。"
    else:
        pending_reason = None
    explanation = result.narrative if result.narrative else "当前结论仅来自已融合的可追溯证据。"
    if provenance == "no_key_rule_fallback":
        explanation = f"{explanation} 本卡使用无 Key 规则回退结果。"
    payload = IdentityCardView(
        key=dimension,
        label=DIMENSION_LABELS[dimension],
        kind=cast(Any, kind),
        revealed=revealed,
        revealable=revealable,
        revealed_now=revealed_now,
        confidence=result.confidence,
        confidence_status=confidence_status,
        score=result.score,
        radar_score=result.radar_score,
        coverage=result.coverage,
        unknown_reasons=result.unknown_reasons,
        confidence_breakdown=result.confidence_breakdown,
        display_value=display_value,
        distribution=distribution or {},
        items=items,
        cards=items,
        evidence_refs=result.evidence_refs,
        quotes=quotes,
        explanation=explanation,
        provenance=provenance,
        pending_reason=pending_reason,
    ).model_dump()
    payload.update(_identity_claim_state(dimension, evidence_cards))
    return payload


def _identity_provenance(dimension: DimensionKey, evidence_cards: list[EvidenceCard]) -> str:
    cards = [card for card in evidence_cards if card.dimension == dimension]
    explicit = [
        str(card.objective_signal.get("scoring_provenance"))
        for card in cards
        if card.objective_signal.get("scoring_provenance")
    ]
    if explicit:
        return explicit[-1]
    methods = {card.extraction_method for card in cards}
    if "no_key_rule_fallback" in methods:
        return "no_key_rule_fallback"
    if "rule_fallback" in methods:
        return "rule_fallback"
    return "fusion"


def _identity_evidence_refs_for_label(
    dimension: DimensionKey,
    label: str,
    evidence_cards: list[EvidenceCard],
) -> list[str]:
    refs: list[str] = []
    for index, card in enumerate((item for item in evidence_cards if item.dimension == dimension), start=1):
        if _identity_label_vote_weight(card.objective_signal, label, dimension=dimension) > 0:
            refs.append(f"{card.source}#{index}")
    return refs


def _identity_tag_strength(dimension: DimensionKey, label: str, evidence_cards: list[EvidenceCard]) -> int:
    count = sum(
        1
        for card in evidence_cards
        if card.dimension == dimension
        and _identity_label_vote_weight(card.objective_signal, label, dimension=dimension) > 0
        and (
            card.claim_status == "verified"
            or card.tier.value != "self_report"
            or card.objective_signal.get("correct") is False
            or card.objective_signal.get("is_correct") is False
        )
    )
    return max(1, count)


def _identity_has_weak_point_evidence(label: str, evidence_cards: list[EvidenceCard]) -> bool:
    return any(
        card.dimension == "weak_points"
        and _identity_label_vote_weight(card.objective_signal, label, dimension="weak_points") > 0
        and (
            card.claim_status == "verified"
            or card.objective_signal.get("correct") is False
            or card.objective_signal.get("is_correct") is False
            or card.tier.value != "self_report"
        )
        for card in evidence_cards
    )


def _identity_claim_state(dimension: DimensionKey, evidence_cards: list[EvidenceCard]) -> dict[str, Any]:
    """Expose calibration state so the client can veil self-report card faces."""

    statuses = [card.claim_status for card in evidence_cards if card.dimension == dimension and card.claim_status]
    if "pending" in statuses:
        status = "pending"
    elif "refuted" in statuses:
        status = "refuted"
    elif "verified" in statuses:
        status = "verified"
    else:
        status = None
    return {"pending_verification": status == "pending", "claim_status": status}


def _normalize_identity_distribution(distribution: dict[str, float]) -> dict[str, float]:
    # Fusion already owns ratio normalization. The view layer only orders and
    # drops zero entries so it cannot subtly rewrite the scoring result.
    values = {str(label): float(ratio) for label, ratio in distribution.items() if float(ratio) > 0}
    return dict(sorted(values.items(), key=lambda item: (-item[1], item[0])))


def _identity_distribution_display_name(dimension: DimensionKey, label: str, evidence_cards: list[EvidenceCard]) -> str:
    preset = IDENTITY_CARD_SKINS.get(dimension, {}).get(label)
    if preset and label != "其他":
        return preset
    return _identity_generated_card_name(dimension, label, evidence_cards) or preset or label


def _identity_generated_card_name(dimension: DimensionKey, label: str, evidence_cards: list[EvidenceCard]) -> str | None:
    for card in reversed(evidence_cards):
        if card.dimension != dimension or _identity_label_vote_weight(card.objective_signal, label, dimension=dimension) <= 0:
            continue
        names = card.objective_signal.get("card_names")
        if not isinstance(names, dict):
            continue
        name = str(names.get(label) or "").strip()
        if 1 <= len(name) <= 5:
            return name
    return None


def _identity_card_meaning(dimension: DimensionKey, label: str, evidence_cards: list[EvidenceCard]) -> str:
    preset = IDENTITY_CARD_MEANINGS.get(dimension, {}).get(label)
    if preset:
        return preset
    quotes = _identity_quotes_for_label(dimension, label, evidence_cards)
    return f"由“{quotes[0]}”提炼出的个人线索" if quotes else "由当前对话线索提炼，仍会随新证据调整"


def _identity_level_name(dimension: DimensionKey, score: float | None) -> str:
    if score is None:
        return "待取证"
    if dimension == "knowledge_base":
        label = _nearest_anchor_label(score, BASE_LEVEL_SCORES)
        return KNOWLEDGE_LEVEL_CARD_NAMES.get(label, label)
    if dimension == "pace":
        return _nearest_anchor_name(score, PACE_LEVEL_ANCHORS)
    return "待取证"


def _identity_level_anchors(
    dimension: DimensionKey,
    score: float | None,
    evidence_cards: list[EvidenceCard],
) -> dict[str, Any]:
    anchors: dict[str, Any] = {}
    if score is not None and dimension == "knowledge_base":
        anchors["anchor"] = _nearest_anchor_label(score, BASE_LEVEL_SCORES)
    elif score is not None and dimension == "pace":
        anchors["anchor"] = _nearest_anchor_name(score, PACE_LEVEL_ANCHORS)
    if dimension == "pace":
        for card in evidence_cards:
            if card.dimension != "pace":
                continue
            for source_key, output_key in (("daily_minutes", "daily_minutes"), ("minutes_per_day", "daily_minutes"), ("target_days", "target_days"), ("period_days", "target_days")):
                value = card.objective_signal.get(source_key)
                if isinstance(value, (int, float)) and value > 0:
                    anchors[output_key] = int(value) if float(value).is_integer() else float(value)
    return anchors


def _identity_level_display_value(
    dimension: DimensionKey,
    level_name: str,
    score: float | None,
    anchors: dict[str, Any],
    *,
    revealable: bool,
) -> str:
    if score is None:
        return "待取证"
    if dimension == "pace" and (anchors.get("daily_minutes") or anchors.get("target_days")):
        parts = []
        if anchors.get("daily_minutes"):
            parts.append(f"每天{anchors['daily_minutes']}分钟")
        if anchors.get("target_days"):
            parts.append(f"目标{anchors['target_days']}天")
        value = " · ".join(parts)
    else:
        value = f"{level_name} · {round(score)}分"
    return value if revealable else f"待验证：{value}"


def _nearest_anchor_label(score: float, anchors: dict[str, float]) -> str:
    ordered = sorted(anchors.items(), key=lambda item: item[1])
    return min(ordered, key=lambda item: (abs(score - item[1]), item[1]))[0]


def _nearest_anchor_name(score: float, anchors: tuple[tuple[float, str], ...]) -> str:
    return min(anchors, key=lambda item: (abs(score - item[0]), item[0]))[1]


def _identity_quotes_for_label(dimension: DimensionKey, label: str, evidence_cards: list[EvidenceCard]) -> list[str]:
    return [quote for quote, _ in _weighted_identity_quotes_for_label(dimension, label, evidence_cards)]


def _weighted_identity_quotes_for_label(
    dimension: DimensionKey,
    label: str,
    evidence_cards: list[EvidenceCard],
) -> list[tuple[str, float]]:
    weighted: dict[str, float] = {}
    for card in evidence_cards:
        if card.dimension != dimension:
            continue
        vote_weight = _identity_label_vote_weight(card.objective_signal, label, dimension=dimension)
        if vote_weight <= 0:
            continue
        quotes = _evidence_quote_texts(card)
        for quote in quotes:
            text = str(quote or "").strip()
            if not text:
                continue
            weighted[text] = max(weighted.get(text, 0.0), card.weight * vote_weight)
    return sorted(weighted.items(), key=lambda item: (-item[1], item[0]))


def _identity_label_vote_weight(signal: dict[str, Any], label: str, *, dimension: DimensionKey) -> float:
    weight = 0.0
    if signal.get("label") == label:
        weight += 1.0
    distribution = signal.get("distribution")
    if isinstance(distribution, dict):
        try:
            weight += max(0.0, float(distribution.get(label, 0.0) or 0.0))
        except (TypeError, ValueError):
            pass
    if dimension == "weak_points":
        weak_labels = signal.get("weak_labels")
        if isinstance(weak_labels, list) and label in {str(item).strip() for item in weak_labels}:
            weight += 1.0
        if signal.get("topic") == label or signal.get("knowledge_point") == label:
            weight += 1.0
    return weight


def _identity_card_scores(
    evidence_cards: list[EvidenceCard],
    *,
    fusion: dict[DimensionKey, FusionResult] | None = None,
) -> tuple[dict[str, float], str | None]:
    fused = fusion or _dialogue_fusion(evidence_cards)
    goal_distribution = fused["learning_goal"].distribution
    motivation_distribution = fused["motivation"].distribution
    scores: dict[str, float] = {}
    for card_name, meta in IDENTITY_CARDS.items():
        goal_score = goal_distribution.get(meta["learning_goal"], 0.0)
        motivation_score = motivation_distribution.get(meta["motivation"], 0.0)
        scores[card_name] = round(max(0.0, min(1.0, (goal_score + motivation_score) / 2)), 4)
    inferred = max(scores.items(), key=lambda item: item[1])[0] if any(scores.values()) else None
    return scores, inferred


def _profile_from_fusion(
    user_id: str,
    evidence_cards: list[EvidenceCard],
    *,
    fusion: dict[DimensionKey, FusionResult] | None = None,
) -> Profile:
    profile = create_blank_profile(user_id)
    fused = fusion or fuse_profile(evidence_cards)
    for dimension in DIMENSION_LABELS:
        result = fused[dimension]
        target = getattr(profile, dimension)
        target.confidence = result.confidence
        target.radar_score = result.radar_score
        target.coverage = result.coverage
        target.unknown_reasons = result.unknown_reasons
        target.confidence_breakdown = result.confidence_breakdown
        target.evidence = [card for card in evidence_cards if card.dimension == dimension]
        target.narrative = result.narrative
        gate = result.metadata.get("component_collection")
        if isinstance(gate, dict):
            target.scoring_phase = "scored" if gate.get("scoring_phase") == "scored" else "collecting"
            target.pending_components = [str(item) for item in gate.get("missing_components", []) if str(item)]
            target.component_matrix = {
                key: {
                    "status": "pending" if key in target.pending_components else "collected",
                    "score": target.component_scores.get(key),
                }
                for key in COMPONENTS.get(dimension, ())
            }
            target.component_scores = {
                str(key): float(value)
                for key, value in (gate.get("component_scores") or {}).items()
                if isinstance(value, (int, float)) and 5 <= float(value) <= 95
            }
            target.score_summary = result.narrative
        if hasattr(target, "score") and result.score is not None:
            target.score = result.score
        if hasattr(target, "label") and result.label is not None:
            target.label = result.label
        if hasattr(target, "distribution") and result.distribution:
            target.distribution = result.distribution
    return profile


def _radar_payload(profile: Profile) -> dict[str, Any]:
    from app.profile.radar import to_radar

    return to_radar(profile)


def _compact_radar_axes(radar: dict[str, Any]) -> list[dict[str, Any]]:
    axes = radar.get("axes")
    if not isinstance(axes, list):
        return []
    compact: list[dict[str, Any]] = []
    for axis in axes:
        if not isinstance(axis, dict):
            continue
        compact.append(
            {
                "key": str(axis.get("key") or ""),
                "label": str(axis.get("label") or axis.get("source_label") or axis.get("sourceLabel") or axis.get("key") or ""),
                "score": _int_axis_value(axis.get("value")),
                "lit": bool(axis.get("lit")),
                "status": str(axis.get("status") or "dark"),
                "confidence": _float_axis_value(axis.get("confidence")),
            }
        )
    return [axis for axis in compact if axis["key"]]


def _dialogue_radar_baseline(session: ProfileSession, *, db_path: Path) -> list[dict[str, Any]]:
    baseline = session.step_data.get("radar_baseline")
    if isinstance(baseline, list):
        return [axis for axis in baseline if isinstance(axis, dict)]
    baseline = _compact_radar_axes(_radar_payload(get_profile(session.user_id, db_path=db_path)))
    session.step_data["radar_baseline"] = baseline
    return baseline


def _radar_changes(
    baseline: list[dict[str, Any]],
    final_radar: dict[str, Any],
    *,
    evidence_cards: list[EvidenceCard],
) -> list[dict[str, Any]]:
    before = {str(axis.get("key")): axis for axis in baseline if isinstance(axis, dict)}
    after = {axis["key"]: axis for axis in _compact_radar_axes(final_radar)}
    counts: dict[str, int] = {}
    for card in evidence_cards:
        counts[card.dimension] = counts.get(card.dimension, 0) + 1

    changes: list[dict[str, Any]] = []
    for key, final_axis in after.items():
        initial_axis = before.get(key, {})
        from_score = _int_axis_value(initial_axis.get("score", initial_axis.get("value")))
        to_score = _int_axis_value(final_axis.get("score", final_axis.get("value")))
        from_status = str(initial_axis.get("status") or "dark")
        to_status = str(final_axis.get("status") or "dark")
        from_lit = bool(initial_axis.get("lit"))
        to_lit = bool(final_axis.get("lit"))
        newly_lit = to_lit and not from_lit
        delta = to_score - from_score
        if not newly_lit and delta == 0 and from_status == to_status:
            continue
        changes.append(
            {
                "key": key,
                "label": str(final_axis.get("label") or key),
                "newly_lit": newly_lit,
                "from_score": from_score,
                "to_score": to_score,
                "delta": delta,
                "from_status": from_status,
                "to_status": to_status,
                "evidence_count": counts.get(key, 0),
            }
        )
    return changes


def _radar_summary_text(changes: list[dict[str, Any]]) -> str:
    if not changes:
        return "本次对话已完成定稿，画像暂未出现新的雷达分数变化。"
    lit_labels = [str(change["label"]) for change in changes if change.get("newly_lit")]
    changed = [
        f"{change['label']}从{change['from_score']}升到{change['to_score']}"
        if int(change.get("delta") or 0) > 0
        else f"{change['label']}从{change['from_score']}调整到{change['to_score']}"
        for change in changes
        if int(change.get("delta") or 0) != 0
    ]
    parts: list[str] = []
    if lit_labels:
        parts.append(f"本次为你点亮了{'、'.join(lit_labels)}")
    if changed:
        parts.append("，".join(changed))
    return "；".join(parts) + "。"


def _int_axis_value(value: Any) -> int:
    try:
        return int(max(0, min(100, round(float(value)))))
    except (TypeError, ValueError):
        return 0


def _float_axis_value(value: Any) -> float:
    try:
        return round(max(0.0, min(1.0, float(value))), 4)
    except (TypeError, ValueError):
        return 0.0


def _identity_cards(payload: dict[str, Any], source: str, response_time_ms: int, prior_weight: float) -> list[EvidenceCard]:
    identity = str(payload.get("identity_card") or payload.get("choice") or "").strip()
    custom_identity = str(payload.get("identity_custom") or "").strip()
    utterance = str(payload.get("utterance") or "").strip()
    meta = IDENTITY_CARDS.get(identity, IDENTITY_CARDS["草木好奇者"])
    signal = {
        "identity_card": identity or "未指定",
        "identity_custom": custom_identity or None,
        "utterance": utterance or None,
        "answer_detail_length": len(utterance),
        "response_time_ms": response_time_ms,
    }
    custom_parts = []
    if custom_identity:
        custom_parts.append(f"自定义身份：{custom_identity}")
    if utterance:
        custom_parts.append(f"自述：{utterance}")
    custom_fact = f"；{'；'.join(custom_parts)}" if custom_parts else ""
    return [
        _card("learning_goal", source, f"选择身份卡“{identity or '未指定'}”，目标标签为{meta['learning_goal']}{custom_fact}", {**signal, "label": meta["learning_goal"], "quote": utterance or None}, 1.0, component="expression_certainty", answer_kind="choice" if not utterance else "self_report"),
        _card("motivation", source, f"选择身份卡“{identity or '未指定'}”，动机标签为{meta['motivation']}{custom_fact}", {**signal, "label": meta["motivation"], "quote": utterance or None}, prior_weight, component="intrinsic_value", answer_kind="choice" if not utterance else "self_report"),
        _card("knowledge_base", source, "身份卡仅作为知识基础先验，不代表正确率", {**signal, "prior": True}, prior_weight, component="concept_foundation", collection_status="pending", answer_kind="choice"),
    ]


def _quiz_cards(
    payload: dict[str, Any],
    questions: list[dict[str, Any]],
    source: str,
    response_time_ms: int,
) -> list[EvidenceCard]:
    answers = _as_list(payload.get("answers") or payload.get("items"))
    if not answers and any(key in payload for key in ("question_id", "selected", "answer")):
        answers = [payload]

    question_by_id = {str(question.get("id")): question for question in questions if isinstance(question, dict)}
    cards: list[EvidenceCard] = []
    clue_votes: list[str] = []
    for index, item in enumerate(answers, start=1):
        if not isinstance(item, dict):
            continue
        question_id = str(item.get("question_id") or item.get("id") or index)
        question = question_by_id.get(question_id, {})
        expected = str(question.get("answer") or item.get("expected_answer") or "").strip()
        selected = str(item.get("selected") or item.get("answer") or "").strip()
        free_text = str(item.get("free_text") or item.get("custom_text") or item.get("answer_detail") or "").strip()
        answer_text = selected or free_text
        gave_up = bool(item.get("gave_up") or item.get("abandoned")) or answer_text == ""
        accepted_answers = [str(value).strip() for value in question.get("accepted_answers", [expected]) if str(value).strip()]
        correct = ((selected == expected) or (bool(free_text) and any(answer in free_text for answer in accepted_answers))) if expected else bool(item.get("correct") or item.get("is_correct"))
        difficulty = str(item.get("difficulty") or question.get("difficulty") or "easy")
        weight = 1.2 if difficulty in {"hard", "难", "difficult"} else 0.8
        if gave_up:
            weight = 0.6
        item_rt = _response_time_ms(item, None) or response_time_ms
        topic = str(item.get("topic") or question.get("topic") or "诊断题").strip()
        clue_type = str(item.get("clue_type") or item.get("clue") or question.get("skill") or "").strip()
        detail_length = len(free_text)
        detail_quality = min(1.0, detail_length / 48.0) if free_text else 0.0
        answer_quality = round((0.72 if correct else 0.18) + detail_quality * (0.28 if correct else 0.12), 3)
        if clue_type:
            clue_votes.append(_style_from_clue(clue_type))
        # 题目自带落点：性味题进「知识范围」，使用注意题进「边界误区意识」，
        # 相似药题进薄弱点的「应用正确性」。没写落点才默认记到实测表现。
        target_dimension = str(question.get("dimension") or "knowledge_base")
        target_component = str(question.get("component") or "measured_performance")
        question_type = str(question.get("question_type") or "effect")
        cards.append(
            _card(
                target_dimension,  # type: ignore[arg-type]
                source,
                f"诊断题第{index}题{'放弃' if gave_up else ('答对' if correct else '答错')}" + (f"，补充说明{detail_length}字" if free_text else ""),
                {
                    "question_id": question_id,
                    "question_type": question_type,
                    "selected": selected or None,
                    "free_text": free_text or None,
                    "answer_text": answer_text or None,
                    "expected_answer": expected or None,
                    "correct": correct,
                    "gave_up": gave_up,
                    "difficulty": difficulty,
                    "topic": topic,
                    "response_time_ms": item_rt,
                    "answer_detail_length": detail_length,
                    "answer_quality": answer_quality,
                    "quote": free_text or selected or None,
                },
                weight,
                component=target_component,
                # 点选项＝实测（选择）；写一段＝自述，本地规则不判对错。
                answer_kind="self_report" if free_text and not selected else "choice",
            )
        )
        if (gave_up or not correct) and target_dimension != "weak_points":
            cards.append(
                _card(
                    "weak_points",
                    source,
                    f"{topic}出现{'放弃' if gave_up else '错误'}",
                    {
                        "label": topic,
                        "topic": topic,
                        "question_id": question_id,
                        "selected": selected or None,
                        "free_text": free_text or None,
                        "answer_text": answer_text or None,
                        "expected_answer": expected or None,
                        "correct": correct,
                        "gave_up": gave_up,
                        "response_time_ms": item_rt,
                        "answer_detail_length": detail_length,
                        "quote": free_text or selected or None,
                    },
                    weight,
                    component="recall_correctness",
                    answer_kind="self_report" if free_text and not selected else "choice",
                )
            )
        if free_text:
            cards.append(
                _card(
                    "knowledge_base",
                    source,
                    f"诊断题第{index}题的辨析说明：{free_text}",
                    {
                        "question_id": question_id,
                        "topic": topic,
                        "free_text": free_text,
                        "answer_detail_length": detail_length,
                        "answer_quality": answer_quality,
                        "quote": free_text,
                        "basis": "explicit",
                        "confidence": min(0.95, 0.55 + detail_quality * 0.4),
                    },
                    weight,
                    component="concept_foundation",
                    answer_kind="self_report",
                )
            )
    if clue_votes:
        cards.append(
            _card("learning_style", source, f"线索类型计票：{_count_labels(clue_votes)}", {"votes": _count_labels(clue_votes), "response_time_ms": response_time_ms}, 1.0, answer_kind="choice")
        )
    return cards


async def _prescription_card(payload: dict[str, Any], source: str, weight: float, response_time_ms: int) -> EvidenceCard:
    evaluation = evaluate_prescription(payload)
    signal = evaluation.signal()
    weak_labels = _weak_labels_from_prescription_signal(signal)
    objective_signal: dict[str, Any] = {
        **signal,
        "response_time_ms": response_time_ms,
        "quote": evaluation.quote or None,
        "basis": "explicit",
        "confidence": 0.85,
        "extraction_method": evaluation.method,
        "knowledge_base_ref": payload.get("knowledge_base_ref") or payload.get("knowledge_ref") or (evaluation.knowledge_base_refs[0] if evaluation.knowledge_base_refs else None),
        "preferences": payload.get("preferences") or [],
        "preference_note": str(payload.get("preference_note") or "").strip() or None,
        "answer_detail_length": len(str(payload.get("prescription") or "").strip()),
    }
    if weak_labels:
        objective_signal["weak_labels"] = weak_labels
    fact_parts = [
        f"对症{'是' if signal['对症'] else '否'}",
        f"配伍{'是' if signal['配伍'] else '否'}",
        f"剂量{'合适' if signal['剂量'] else '需核查'}",
        f"误用毒烈{'有' if signal['误用毒烈'] else '无'}",
    ]
    if weak_labels:
        fact_parts.append(f"薄弱标签：{'、'.join(weak_labels)}")
    if objective_signal.get("preference_note"):
        fact_parts.append(f"法门偏好：{objective_signal['preference_note']}")
        objective_signal["quote"] = objective_signal["preference_note"]
    return _card("knowledge_base", source, "；".join(fact_parts), objective_signal, weight, component="retrieval_application", answer_kind="behavior")


async def _try_llm_prescription_extract(payload: dict[str, Any]) -> dict[str, bool] | None:
    settings = get_settings()
    if not (settings.deepseek_api_key or settings.zhipu_api_key):
        return None
    prompt = (
        "只输出JSON，不要解释。请从中医开方答题中抽取 objective_signal，不能输出最终画像分数，"
        "字段必须且只能包含：对症、配伍、剂量、误用毒烈。字段值为布尔值。"
        f"\n输入：{json.dumps(payload, ensure_ascii=False)}"
    )
    try:
        raw = await ModelRouter(settings=settings).chat(
            "profile_extract",
            [{"role": "user", "content": prompt}],
        )
        return _validate_prescription_signal(_parse_json_object(raw))
    except Exception:
        return None


def _rule_prescription_extract(payload: dict[str, Any]) -> dict[str, bool]:
    text = _prescription_text(payload)
    herbs = _extract_herbs(payload, text)
    case_text = str(payload.get("case") or payload.get("symptoms") or payload.get("scenario") or "")
    dosage_values = _extract_dosages(payload, text)
    toxic_misuse = _has_toxic_misuse(herbs, dosage_values)
    return {
        "对症": _symptom_matches(case_text, herbs),
        "配伍": _compatible(herbs) and not toxic_misuse,
        "剂量": bool(dosage_values) and all(1 <= value <= 30 for value in dosage_values),
        "误用毒烈": toxic_misuse,
    }


def _resource_card(payload: dict[str, Any], source: str, response_time_ms: int) -> EvidenceCard:
    selected = str(payload.get("resource_type") or payload.get("choice") or "").strip()
    custom = str(payload.get("resource_custom") or payload.get("custom_text") or "").strip()
    if not selected and not custom:
        selected = "知识图谱"
    label = selected if selected in RESOURCE_TYPES else (custom or selected)
    signal = {
        "label": label,
        "preset": selected if selected in RESOURCE_TYPES else None,
        "custom_text": custom or None,
        "quote": custom or label,
        "response_time_ms": response_time_ms,
    }
    return _card("resource_preference", source, f"选择资源类型：{label}", signal, 1.0, component="preference_specificity", answer_kind="choice")


def _learning_rule_cards(payload: dict[str, Any], source: str, response_time_ms: int) -> list[EvidenceCard]:
    minutes = _int_or_none(payload.get("daily_minutes") or payload.get("minutes_per_day"))
    period_days = _int_or_none(payload.get("target_days") or payload.get("period_days"))
    challenge = bool(payload.get("accept_challenge") or payload.get("challenge"))
    signal = {
        "daily_minutes": minutes,
        "target_days": period_days,
        "accept_challenge": challenge,
        "learning_note": str(payload.get("learning_note") or payload.get("custom_text") or "").strip() or None,
        "response_time_ms": response_time_ms,
    }
    note = signal.get("learning_note")
    if note:
        signal["quote"] = note
    # 数字题的"用户原话"就是他自己填的数字，不能因为没写文字就丢掉可追溯性。
    numeric_quote = " ".join(str(value) for value in (minutes, period_days) if value is not None)
    if not note and numeric_quote:
        signal["quote"] = numeric_quote
    pace_fact = f"约定每日{minutes if minutes is not None else '未填'}分钟，周期{period_days if period_days is not None else '未填'}天"
    if note:
        pace_fact += f"；自述：{note}"
    cards = [_card("pace", source, pace_fact, signal, 1.0, component="plan_clarity", answer_kind="number")]
    # 立下学规这个动作本身就构成投入承诺，不再依赖用户勾选"接受挑战"。
    cards.append(
        _card(
            "motivation",
            source,
            f"立下学规：每日 {minutes if minutes is not None else '未填'} 分钟、周期 {period_days if period_days is not None else '未填'} 天",
            {**signal, "commitment_made": True, "quote": note or numeric_quote or None},
            1.0,
            component="external_commitment",
            answer_kind="behavior",
        )
    )
    if challenge:
        cards.append(_card("motivation", source, "主动接受挑战", signal, 1.0, component="actual_investment", answer_kind="behavior"))
    return cards


def _passive_cards(payload: dict[str, Any], response_time_ms: int) -> list[EvidenceCard]:
    events = _as_list(payload.get("events"))
    if not events:
        events = [payload]
    cards: list[EvidenceCard] = []
    for index, event in enumerate(events, start=1):
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type") or event.get("type") or "click").strip()
        resource_type = str(event.get("resource_type") or event.get("resource") or "").strip()
        topic = str(event.get("topic") or event.get("knowledge_point") or "未标注内容").strip()
        item_rt = _response_time_ms(event, None) or response_time_ms
        dimension = _dimension_from_passive_event(event_type, resource_type)
        signal = {
            "event_type": event_type,
            "resource_type": resource_type or None,
            "topic": topic,
            "duration_ms": _int_or_none(event.get("duration_ms") or event.get("stay_ms")),
            "response_time_ms": item_rt,
            "sequence": index,
        }
        cards.append(_card(dimension, STEP_LABELS["passive"], f"{event_type}：{topic}", signal, 1.0, answer_kind="behavior"))
    return cards


def _dimension_from_passive_event(event_type: str, resource_type: str) -> DimensionKey:
    if event_type in {"replay", "重看", "skip", "跳过"}:
        return "weak_points"
    if resource_type in RESOURCE_TYPES:
        return "resource_preference"
    if event_type in {"stay", "停留", "click", "点击"}:
        return "motivation"
    return "learning_style"


# Rule-card quality per answer form.  A click on an option is a measured fact;
# a free-text sentence is a self report the local rules cannot verify.
_RULE_QUALITY: dict[str, dict[str, float]] = {
    "choice": {"source_reliability": 0.95, "expression_clarity": 0.90, "content_specificity": 0.90, "freshness": 1.0, "consistency": 0.90},
    "number": {"source_reliability": 0.95, "expression_clarity": 0.95, "content_specificity": 0.90, "freshness": 1.0, "consistency": 0.95},
    "behavior": {"source_reliability": 0.95, "expression_clarity": 0.90, "content_specificity": 0.85, "freshness": 1.0, "consistency": 0.85},
    "self_report": {"source_reliability": 0.85, "expression_clarity": 0.70, "content_specificity": 0.60, "freshness": 1.0, "consistency": 0.75},
}

_QUESTION_TYPE_BY_KIND: dict[str, str] = {
    "choice": "选择",
    "number": "数字",
    "behavior": "行为",
    "self_report": "自述",
}

# A 18-second answer is normal for a sentence and slow for a click, so each
# question type gets its own baseline.
_SPEED_BUCKETS: dict[str, tuple[int, int]] = {
    "选择": (4_000, 15_000),
    "自述": (15_000, 60_000),
    "数字": (5_000, 20_000),
    "行为": (5_000, 20_000),
}


def _response_speed(response_time_ms: int | None, question_type: str) -> str | None:
    """Bucket a duration against its own question type.

    Raw milliseconds are deliberately not kept: the model has no feel for
    "8400ms" and treats it as just another number to copy into a score.
    """

    if not response_time_ms or response_time_ms <= 0:
        return None
    fast, slow = _SPEED_BUCKETS.get(question_type, _SPEED_BUCKETS["自述"])
    if response_time_ms < fast:
        return "快"
    if response_time_ms <= slow:
        return "正常"
    return "慢"


def _detail_level(text: str) -> str:
    length = len(text.strip())
    if length < 15:
        return "简短"
    if length <= 60:
        return "适中"
    return "详细"


def _rule_quality_factors(answer_kind: str | None) -> dict[str, float]:
    return dict(_RULE_QUALITY.get(answer_kind or "self_report", _RULE_QUALITY["self_report"]))


def _card(
    dimension: DimensionKey,
    source: str,
    fact: str,
    objective_signal: dict[str, Any],
    weight: float,
    *,
    component: str | None = None,
    collection_status: str | None = None,
    answer_kind: str | None = None,
) -> EvidenceCard:
    signal = {key: value for key, value in objective_signal.items() if value is not None}
    evidence = signal.get("evidence")
    quote = signal.get("quote") or (evidence[0] if isinstance(evidence, list) and evidence else evidence if isinstance(evidence, str) else None)
    basis = signal.get("basis") if signal.get("basis") in {"explicit", "inferred"} else "inferred"
    confidence = signal.get("confidence", 0.0)
    try:
        extraction_confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        extraction_confidence = 0.0
    method = signal.get("extraction_method")
    if method not in {"llm", "no_key_rule_fallback", "rule_fallback", "legacy"}:
        method = "legacy"
    # Answer speed becomes a bucket, never a millisecond count.
    question_type = signal.get("question_type") or _QUESTION_TYPE_BY_KIND.get(answer_kind or "")
    if question_type:
        signal["question_type"] = question_type
        speed = _response_speed(_int_or_none(signal.get("response_time_ms")), question_type)
        if speed:
            signal["response_speed"] = speed
    signal.pop("response_time_ms", None)
    quote_text = str(quote).strip() if quote else ""
    if quote_text and answer_kind == "self_report":
        signal.setdefault("detail", _detail_level(quote_text))
    quality = signal.get("quality_factors") or _rule_quality_factors(answer_kind)
    group_id = signal.get("evidence_group_id") or f"{source}:{dimension}:{component or 'none'}"
    return EvidenceCard(
        dimension=dimension,
        source=source,
        fact=fact,
        objective_signal=signal,
        weight=weight,
        quote=quote_text or None,
        basis=basis,
        extraction_confidence=extraction_confidence,
        extraction_method=method,
        knowledge_base_ref=signal.get("knowledge_base_ref") or signal.get("knowledge_ref"),
        component=component,
        collection_status=collection_status or ("scorable" if component else None),
        evidence_group_id=group_id,
        normalized_claim=signal.get("normalized_claim") or fact,
        supports=[str(item) for item in signal.get("supports", [])] if isinstance(signal.get("supports"), list) else [],
        confirmed_aspects=[str(item) for item in signal.get("confirmed_aspects", [])] if isinstance(signal.get("confirmed_aspects"), list) else [],
        quality_factors=quality if isinstance(quality, dict) else {},
        timestamp=datetime.now(UTC),
    )


def _load_herbs(data_dir: Path | None) -> list[dict[str, Any]]:
    root = data_dir or Path(__file__).resolve().parents[2] / "data" / "herbs"
    herbs: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows = data if isinstance(data, list) else [data]
        herbs.extend(row for row in rows if isinstance(row, dict))
    return herbs


def _public_questions(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: value for key, value in question.items() if key not in {"answer", "accepted_answers"}}
        for question in questions
    ]


def _is_quiz_step(step_id: str) -> bool:
    return step_id in {"quickcard", "apprentice_act1_quiz"}


def _options(answer: str, distractors: list[str], rng: random.Random) -> list[str]:
    # Sample instead of slicing the head of a sorted pool: slicing gave every
    # question the same three distractors, which let players win without reading.
    pool = list(dict.fromkeys(item for item in distractors if item and item != answer))
    chosen = rng.sample(pool, 3) if len(pool) >= 3 else pool
    while len(chosen) < 3:
        chosen.append(f"干扰项{len(chosen) + 1}")
    options = [answer, *chosen[:3]]
    rng.shuffle(options)
    return options


def _combination_name(combination: Any, all_names: list[str]) -> str | None:
    if not isinstance(combination, dict):
        return None
    raw = combination.get("with") or combination.get("with_") or []
    values = raw if isinstance(raw, list) else [raw]
    for item in values:
        text = str(item).strip()
        if not text:
            continue
        for name in all_names:
            if text == name or name in text:
                return name
        if text.startswith("herb_"):
            suffix = text.removeprefix("herb_").replace("_", "")
            for name in all_names:
                if suffix and suffix in _safe_id(name):
                    return name
    return None


def _category_label(herb: dict[str, Any]) -> str:
    return str(herb.get("subcategory") or herb.get("category") or "中药").strip()


def _safe_id(value: Any) -> str:
    text = str(value)
    return re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", text).strip("_") or "item"


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _weak_labels_from_prescription_signal(signal: dict[str, bool]) -> list[str]:
    labels: list[str] = []
    if not signal.get("对症"):
        labels.append("辨证·对症")
    if not signal.get("配伍"):
        labels.append("方药·配伍")
    if not signal.get("剂量"):
        labels.append("中药·剂量")
    if signal.get("误用毒烈"):
        labels.append("用药安全·毒烈药")
    return labels


def _style_from_clue(clue_type: str) -> str:
    for key, label in STYLE_BY_CLUE.items():
        if key in clue_type:
            return label
    return "文字型"


def _count_labels(labels: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    return counts


def _response_time_ms(payload: dict[str, Any], started_at: datetime | None) -> int:
    explicit = _int_or_none(payload.get("response_time_ms") or payload.get("reaction_time_ms"))
    if explicit is not None:
        return max(0, explicit)
    if started_at is None:
        return 0
    return max(0, int((datetime.now(UTC) - started_at).total_seconds() * 1000))


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_json_object(text: str) -> Any:
    from app.profile.nodes import _json_object

    try:
        return _json_object(text)
    except (ValueError, json.JSONDecodeError):
        return None


def _validate_prescription_signal(value: Any) -> dict[str, bool] | None:
    if not isinstance(value, dict):
        return None
    required = ("对症", "配伍", "剂量", "误用毒烈")
    if any(key not in value for key in required):
        return None
    return {key: bool(value[key]) for key in required}


def _prescription_text(payload: dict[str, Any]) -> str:
    chunks = [
        str(payload.get("prescription") or ""),
        str(payload.get("answer") or ""),
        str(payload.get("selected_herbs") or ""),
        str(payload.get("herbs") or ""),
    ]
    return " ".join(chunks)


def _extract_herbs(payload: dict[str, Any], text: str) -> set[str]:
    herbs: set[str] = set()
    raw_herbs = payload.get("herbs") or payload.get("selected_herbs")
    if isinstance(raw_herbs, list):
        for item in raw_herbs:
            if isinstance(item, dict):
                name = str(item.get("name") or item.get("herb") or "").strip()
            else:
                name = str(item).strip()
            if name:
                herbs.add(name)
    known = {
        "麻黄",
        "桂枝",
        "白芷",
        "金银花",
        "连翘",
        "黄芩",
        "黄连",
        "石膏",
        "半夏",
        "陈皮",
        "黄芪",
        "白术",
        *TOXIC_OR_FIERCE_HERBS,
    }
    for name in known:
        if name in text:
            herbs.add(name)
    return herbs


def _extract_dosages(payload: dict[str, Any], text: str) -> list[int]:
    values: list[int] = []
    raw_herbs = payload.get("herbs") or payload.get("selected_herbs")
    if isinstance(raw_herbs, list):
        for item in raw_herbs:
            if isinstance(item, dict):
                parsed = _int_or_none(item.get("dose") or item.get("dosage") or item.get("grams"))
                if parsed is not None:
                    values.append(parsed)
    for match in re.finditer(r"(\d{1,3})\s*(?:g|克)", text, flags=re.I):
        values.append(int(match.group(1)))
    return values


def _symptom_matches(case_text: str, herbs: set[str]) -> bool:
    if not case_text:
        return bool(herbs)
    if any(key in case_text for key in ("风寒", "恶寒", "无汗", "表寒")):
        return bool({"麻黄", "桂枝", "防风", "荆芥"} & herbs)
    if any(key in case_text for key in ("热毒", "咽痛", "口渴", "热", "毒")):
        return bool({"金银花", "连翘", "黄芩", "黄连", "石膏"} & herbs)
    if any(key in case_text for key in ("气虚", "乏力", "自汗")):
        return bool({"黄芪", "白术"} & herbs)
    return bool(herbs)


def _compatible(herbs: set[str]) -> bool:
    compatible_pairs = (
        ("麻黄", "桂枝"),
        ("桂枝", "白芷"),
        ("金银花", "连翘"),
        ("黄芩", "黄连"),
        ("半夏", "陈皮"),
        ("黄芪", "白术"),
    )
    return len(herbs) >= 1 and (len(herbs) == 1 or any(left in herbs and right in herbs for left, right in compatible_pairs))


def _has_toxic_misuse(herbs: set[str], dosage_values: list[int]) -> bool:
    return bool(TOXIC_OR_FIERCE_HERBS & herbs) and any(value > 9 for value in dosage_values)


__all__ = [
    "APPRENTICE_STEPS",
    "ProfileMode",
    "ProfileSession",
    "ProfileStep",
    "answer_profile_session",
    "cards_to_json",
    "dialogue_session_summary",
    "extract_prescription_signal",
    "finish_profile_session",
    "finish_profile_session_async",
    "get_profile_session",
    "load_diagnostic_questions",
    "reset_profile_sessions",
    "session_payload",
    "start_profile_session",
    "stream_dialogue_chat_events",
]
