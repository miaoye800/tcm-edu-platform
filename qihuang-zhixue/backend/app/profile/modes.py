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
from typing import Any, cast
from uuid import uuid4

from app.config import get_settings
from app.llm.clients import MockClient
from app.llm.router import ModelRouter
from app.profile.anchors import SCORE_ANCHOR_INTERVALS, interval_midpoint
from app.profile.fusion import ALLOWED_LABELS, BASE_LEVEL_SCORES, FusionResult, fuse_profile
from app.profile.component_model import COMPONENTS, COMPONENT_RUBRICS, COMPONENT_WEIGHTS, DIMENSION_COMPONENT_LABELS, deduplicate_component_cards
from app.profile.jury import ScoringResult, score_dimension, score_profile
from app.profile.prescription import evaluate_prescription
from app.profile.radar import LOW_CONFIDENCE_THRESHOLD
from app.profile.schema import (
    DIMENSION_LABELS,
    DimensionKey,
    EvidenceCard,
    IdentityCardView,
    Profile,
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
            return _apprentice_step(step_index)
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
    return _initial_step(mode)


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


def _initial_step(mode: ProfileMode) -> ProfileStep:
    if mode == ProfileMode.apprentice:
        return _apprentice_step(0)
    if mode == ProfileMode.quickcard:
        questions = load_diagnostic_questions(limit=5)
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
        return _apprentice_step(session.step_index)
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


def _apprentice_step(index: int) -> ProfileStep:
    step_id = APPRENTICE_STEPS[index]
    if step_id == "apprentice_prologue":
        return ProfileStep(
            id=step_id,
            title=STEP_LABELS[step_id],
            kind="identity",
            prompt="选择你进入药圃前的身份卡。",
            payload={"identity_cards": list(IDENTITY_CARDS)},
        )
    if step_id == "apprentice_act1_quiz":
        questions = load_diagnostic_questions(limit=5, photo_ready_only=True)
        return ProfileStep(
            id=step_id,
            title=STEP_LABELS[step_id],
            kind="diagnostic_quiz",
            prompt="辨药5题，并记录每题反应时长。",
            payload={"questions": _public_questions(questions)},
            private_payload={"questions": questions},
        )
    if step_id == "apprentice_act2_prescription":
        return ProfileStep(
            id=step_id,
            title=STEP_LABELS[step_id],
            kind="prescription",
            prompt="根据病例抓药，系统只抽取客观信号。",
            payload={"objective_signal_fields": ["对症", "配伍", "剂量", "误用毒烈"]},
        )
    if step_id == "apprentice_act3_resource":
        return ProfileStep(
            id=step_id,
            title=STEP_LABELS[step_id],
            kind="resource_choice",
            prompt="挑一件师父药宝带走。",
            payload={"resource_types": sorted(RESOURCE_TYPES)},
        )
    return ProfileStep(
        id=step_id,
        title=STEP_LABELS[step_id],
        kind="learning_rule",
        prompt="约定每日时长、目标周期与挑战意愿。",
        payload={"fields": ["daily_minutes", "target_days", "accept_challenge"]},
    )


async def _answer_apprentice(session: ProfileSession, payload: dict[str, Any], response_time_ms: int) -> list[EvidenceCard]:
    step_id = session.current_step.id if session.current_step else ""
    source = STEP_LABELS.get(step_id, step_id)
    if step_id == "apprentice_prologue":
        return _identity_cards(payload, source=source, response_time_ms=response_time_ms, prior_weight=0.3)
    if step_id == "apprentice_act1_quiz":
        return _quiz_cards(payload, session.step_data.get("questions", []), source=source, response_time_ms=response_time_ms)
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
        return [_resource_card(payload, source=source, response_time_ms=response_time_ms)]
    if step_id == "apprentice_act4_rule":
        return _learning_rule_cards(payload, source=source, response_time_ms=response_time_ms)
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
                "semantic_relation(new/reinforcement/semantic_duplicate/conflict)、related_evidence_ids 和 quality_factors。"
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
        )
        parsed = _parse_json_object(raw)
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
        try:
            extraction = DialogueExtraction.model_validate(extraction_payload)
        except Exception as schema_exc:
            # Older providers may return malformed legacy labels while the new
            # atomic audit contract is valid. Preserve independently valid
            # scan/evidence fields instead of discarding the whole user turn.
            contract_only = {
                key: extraction_payload[key]
                for key in ("turn_id", "dimension_scan", "atomic_evidence", "component_collection", "combination_type")
                if key in extraction_payload
            }
            extraction = DialogueExtraction.model_validate(contract_only)
            extraction.extraction_error = f"legacy_fields_invalid_preserved_component_contract:{type(schema_exc).__name__}"
        extraction = _validate_dialogue_evidence(extraction, user_source_text)
        return extraction, ""
    except Exception as exc:
        from app.profile.nodes import DialogueExtraction

        return DialogueExtraction(
            extraction_method="rule_fallback",
            extraction_error=f"model_schema_or_request_error:{type(exc).__name__}",
        ), ""


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
            evidence = item.get("evidence")
            if not item.get("reason") and evidence:
                item["reason"] = "; ".join(map(str, evidence)) if isinstance(evidence, list) else str(evidence)
            scan_status = str(item.get("status") or "").strip().lower()
            if scan_status in {"partial_evidence", "found", "evidence_found"}:
                item["status"] = "evidence_found"
            elif scan_status in {"no_usable_evidence", "not_found", "no_evidence"}:
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
                item.update({"dimension": dimension, "component": component})
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
    # radar value after every required component has scorable evidence and a
    # component score. Legacy one-card fusion must never bypass this gate.
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
            raw_score = card.objective_signal.get("component_score")
            if component not in required:
                continue
            try:
                score = float(raw_score)
            except (TypeError, ValueError):
                continue
            if 5 <= score <= 95:
                scores[component] = score
        missing = [component for component in required if component not in collected]
        unscored = [component for component in required if component not in scores]
        complete = not missing and not unscored
        gate = {
            "collected": len(collected),
            "total": len(required),
            "missing_components": missing,
            "unscored_components": unscored,
            "component_scores": scores,
            "scoring_phase": "scored" if complete else "collecting",
        }
        metadata = {**result.metadata, "component_collection": gate}
        if complete:
            weighted = round(sum(scores[key] * COMPONENT_WEIGHTS[dimension][key] for key in required), 2)
            fused[dimension] = replace(
                result, score=weighted, radar_score=weighted, metadata=metadata,
                narrative=f"{DIMENSION_LABELS[dimension]}已完成 {len(required)}/{len(required)} 个组件评分。",
            )
        else:
            fused[dimension] = replace(
                result, score=None, radar_score=None, label=None, distribution={}, metadata=metadata,
                narrative=f"{DIMENSION_LABELS[dimension]}已收集 {len(collected)}/{len(required)} 个组件，评分暂缓。",
            )
    return fused


def _dialogue_component_focus(evidence_cards: list[EvidenceCard], dimension: DimensionKey) -> dict[str, str] | None:
    collected = {
        card.component for card in evidence_cards
        if card.dimension == dimension and card.component in COMPONENTS[dimension] and card.collection_status == "scorable"
    }
    missing = next((key for key in COMPONENTS[dimension] if key not in collected), None)
    if missing is None:
        return None
    rubric = COMPONENT_RUBRICS[dimension][missing]
    return {
        "component": missing,
        "label": DIMENSION_COMPONENT_LABELS[dimension][missing],
        "reason": str(rubric["followup_direction"]),
    }


# Dialogue can stop once learning target and motivation are stable: they drive the next
# learning route, while knowledge_base is lightweight self-report in this mode
# and should not block a useful early confirmation.
def _dialogue_converged(
    turn_index: int,
    evidence_cards: list[EvidenceCard],
    *,
    fusion: dict[DimensionKey, FusionResult] | None = None,
) -> bool:
    if turn_index < DIALOGUE_MIN_TURNS:
        return False
    fused = fusion or _dialogue_fusion(evidence_cards)
    return all(
        fused[dimension].confidence >= DIALOGUE_CONFIDENCE_THRESHOLDS[dimension]
        for dimension in DIALOGUE_PRIMARY_DIMENSIONS
    )


def _dialogue_target(
    evidence_cards: list[EvidenceCard],
    *,
    fusion: dict[DimensionKey, FusionResult] | None = None,
    asked_dimensions: list[str] | None = None,
) -> DimensionKey:
    fused = fusion or _dialogue_fusion(evidence_cards)
    asked = asked_dimensions or []
    recent = _as_dialogue_dimension(asked[-1]) if asked else None
    asked_counts = {dimension: asked.count(dimension) for dimension in DIALOGUE_CONFIDENCE_THRESHOLDS}
    partially_collected = []
    for index, dimension in enumerate(COMPONENTS):
        collected = {
            card.component for card in evidence_cards
            if card.dimension == dimension and card.component in COMPONENTS[dimension] and card.collection_status == "scorable"
        }
        if collected and len(collected) < len(COMPONENTS[dimension]):
            partially_collected.append((dimension, len(collected), len(COMPONENTS[dimension]), index))
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
) -> str:
    asked = asked_questions if asked_questions is not None else []
    asked_set = set(asked)
    recent_question = asked[-1] if asked else ""
    target_dimension = _as_dialogue_dimension(target) or "learning_goal"
    focus = _dialogue_component_focus(evidence_cards or [], target_dimension)
    if focus:
        focused_questions = {
            "expression_certainty": "这是你已经比较确定的方向，还是还在考虑中的想法？",
            "goal_specificity": "学医救人对你来说，最希望实现的具体变化是什么？",
            "action_orientation": "你为这个方向做过哪些准备，或者准备从哪一步开始？",
            "priority_signal": "如果这个目标和其他重要安排冲突，你通常会怎样取舍时间和精力？",
            "cross_turn_stability": "这个想法持续多久了？最近有没有发生变化？",
        }
        question = focused_questions.get(
            focus["component"],
            f"关于{focus['label']}，{focus['reason']}。你能结合最近一次学习经历具体说说吗？",
        )
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


async def recover_dialogue_component_scores(session: ProfileSession, *, db_path: Path = DB_PATH) -> bool:
    """Retry deferred complete component sets when a saved dialogue is restored."""
    changed = False
    scoring_audit = session.step_data.setdefault("_component_scoring_audit", {})
    for dimension, required in COMPONENTS.items():
        cards = [card for card in session.evidence_cards if card.dimension == dimension]
        collected = {
            card.component for card in cards
            if card.component in required and card.collection_status == "scorable"
        }
        scored = {
            card.component for card in cards
            if card.component in required and isinstance(card.objective_signal.get("component_score"), (int, float))
        }
        if len(collected) != len(required) or set(required).issubset(scored):
            continue
        result = await score_dimension(dimension, cards)
        attempts = 1
        if not set(required).issubset(result.component_scores) and result.provenance != "no_key_rule_fallback":
            result = await score_dimension(dimension, cards)
            attempts = 2
        complete = set(required).issubset(result.component_scores)
        if complete:
            for card in cards:
                component = card.component
                if component in required:
                    card.objective_signal = {
                        **card.objective_signal,
                        "component_score": result.component_scores[component],
                    }
            changed = True
        else:
            # Don't repeat the same automatic recovery on every page refresh
            # after the bounded retry has already been exhausted.
            attempts = 2 if result.provenance != "no_key_rule_fallback" else 1
        scoring_audit[dimension] = {
            "provenance": result.provenance,
            "dimension_summary": result.narrative,
            "component_scores": result.component_scores,
            "scoring_deferred": not complete,
            "scoring_attempts": attempts,
            "missing_component_scores": sorted(set(required) - set(result.component_scores)),
        }
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
    for dimension, required_components in COMPONENTS.items():
        dimension_cards = [card for card in session.evidence_cards if card.dimension == dimension]
        collected = {
            card.component for card in dimension_cards
            if card.component in required_components and card.collection_status == "scorable"
        }
        existing_scores = {
            card.component for card in dimension_cards
            if card.component and isinstance(card.objective_signal.get("component_score"), (int, float))
        }
        if len(collected) == len(required_components) and not set(required_components).issubset(existing_scores):
            yield "thinking", {"delta": f"\n{DIMENSION_LABELS[dimension]}组件已齐，正在调用评分模型逐项评分。"}
            score_result = await score_dimension(dimension, dimension_cards)
            scoring_attempts = 1
            missing_model_scores = set(required_components) - set(score_result.component_scores)
            if missing_model_scores and score_result.provenance != "no_key_rule_fallback":
                yield "thinking", {"delta": f"\n评分模型尚未覆盖全部组件，正在携带完整证据包重试缺失评分（{len(missing_model_scores)} 项）。"}
                score_result = await score_dimension(dimension, dimension_cards)
                scoring_attempts = 2
            # A scoring result is the only source allowed to publish component
            # scores.  Persist the model's complete result back onto the live
            # cards so the subsequent deterministic fusion pass can see it.
            # In particular, do not leave the scores only in the transient
            # audit record: that made a visibly complete 5/5 matrix remain in
            # ``collecting`` forever and caused the same dimension to be
            # rescored on every turn.
            if set(required_components).issubset(score_result.component_scores):
                for card in dimension_cards:
                    component = card.component
                    if component not in score_result.component_scores:
                        continue
                    card.objective_signal = {
                        **card.objective_signal,
                        "component_score": score_result.component_scores[component],
                    }
            session.step_data.setdefault("_component_scoring_audit", {})[dimension] = {
                "provenance": score_result.provenance,
                "dimension_summary": score_result.narrative,
                "component_scores": score_result.component_scores,
                "scoring_deferred": not set(required_components).issubset(score_result.component_scores),
                "scoring_attempts": scoring_attempts,
                "missing_component_scores": sorted(set(required_components) - set(score_result.component_scores)),
            }
    planning_fusion = _dialogue_fusion(session.evidence_cards)
    planning_target = _dialogue_target(session.evidence_cards, fusion=planning_fusion)
    planning_focus = _dialogue_component_focus(session.evidence_cards, planning_target)
    planning_question = _fallback_dialogue_question(
        planning_target,
        turn_index,
        asked_questions=list(_dialogue_step_list(session.step_data, "asked_questions")),
        asked_dimensions=list(_dialogue_step_list(session.step_data, "asked_dimensions")),
        evidence_cards=session.evidence_cards,
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
        try:
            async for kind, delta in router.chat_stream(
                "reason",
                companion_messages,
                thinking={"type": "enabled"},
                reasoning_effort="medium",
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
                    if prefix_released:
                        sentence_buffer += delta
                        sentences = re.split(r"(?<=[。！？!?])", sentence_buffer)
                        sentence_buffer = sentences.pop() if sentences else ""
                        for sentence in sentences:
                            safe_sentence = _remove_component_rubric_leak(
                                _sanitize_visible_dialogue_text(sentence), planning_focus
                            )
                            if not safe_sentence:
                                continue
                            assistant_parts.append(safe_sentence)
                            for index in range(0, len(safe_sentence), 12):
                                yield "message", {"delta": safe_sentence[index:index + 12]}
                                await asyncio.sleep(0.035)
            if pending_prefix and not use_rule_reply:
                sentence_buffer += pending_prefix
            if not use_rule_reply and sentence_buffer:
                safe_sentence = _remove_component_rubric_leak(
                    _sanitize_visible_dialogue_text(sentence_buffer), planning_focus
                )
                if safe_sentence:
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
        if hit_target or pure_chat_streak >= DIALOGUE_TARGET_RETRY_LIMIT:
            next_target = _dialogue_target(session.evidence_cards, fusion=fusion_after, asked_dimensions=asked_dimensions)
        else:
            next_target = target
        focus = _dialogue_component_focus(session.evidence_cards, next_target)
        next_component_question = planning_question if focus and next_target == target else ""
        if focus and not next_component_question:
            next_component_question = _fallback_dialogue_question(
                next_target,
                turn_index,
                asked_questions=asked_questions,
                asked_dimensions=asked_dimensions,
                evidence_cards=session.evidence_cards,
            )
        # The response model is instructed to ask exactly one question.  Only
        # add the calibrated fallback when it produced no question at all;
        # appending on a text mismatch caused duplicate questions and exposed
        # the internal rubric direction in otherwise natural replies.
        has_visible_question = "？" in assistant_text or "?" in assistant_text
        if focus and next_component_question and next_component_question not in assistant_text and not has_visible_question:
            assistant_text = f"{assistant_text.rstrip()}\n\n{next_component_question}".strip()
            assistant_parts.append(f"\n\n{next_component_question}")
            for card in cards:
                card.objective_signal["next_question"] = next_component_question
            yield "message", {"delta": f"\n\n{next_component_question}"}
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
            scoring_audit=session.step_data.pop("_component_scoring_audit", {}),
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
    component_focus: dict[str, str] | None = None,
    component_question: str = "",
) -> list[dict[str, str]]:
    low_confidence_dims = _dialogue_low_confidence_dims(evidence_cards, fusion=fusion, target=target)
    steering_payload = {
        "target_dimension": target,
        "target_label": DIALOGUE_TARGET_LABELS.get(target, target),
        "current_confidences": current_confidences,
        "low_confidence_dimensions": low_confidence_dims,
        "pure_chat_streak": pure_chat_streak,
        "component_focus": component_focus,
        "component_question": component_question,
        "profile_question_examples": list(DIALOGUE_TARGET_EXTRACTABLE_QUESTIONS.get(target, ())),
    }
    private_prompt = (
        "你是岐黄智学里热情、懂中医药的学长「小岐」。"
        "语气保持温暖、生活化，可以先接住用户的养生或日常话题。"
        "但你有一个陪聊预算：连续纯寒暄/纯养生闲聊不得超过 2 轮；每 2-3 轮必须自然问 1 个能被抽取器识别的画像问题。"
        "本轮优先照顾 profile_steering.target_dimension，不要整轮完全不碰这个目标维度。"
        "如果 profile_steering.component_focus 非空，必须优先围绕该缺失组件自然提出一个补问；问题可放在回应末尾。"
        "profile_steering.component_question 是已校准的唯一追问，请逐字放在回答末尾，不要再提出第二个问题，也不要自行改问其他画像组件。"
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
            }
        )
    dimensions = []
    extraction_contract = extraction_contract or {}
    prior_cards = prior_cards or []
    all_cards = all_cards or [*prior_cards, *cards]
    scoring_audit = scoring_audit or {}
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
        if pending_components:
            deferred_reason = f"评分暂缓：证据组件尚未集齐，缺少 {', '.join(DIMENSION_COMPONENT_LABELS[dimension][key] for key in pending_components)}。"
        elif missing_scores:
            deferred_reason = f"评分暂缓：证据组件已集齐，但评分模型没有返回完整组件分，缺少 {', '.join(DIMENSION_COMPONENT_LABELS[dimension][key] for key in missing_scores)} 的评分。请重试评分，不能用已评分组件均值代替。"
        else:
            deferred_reason = None
        component_records = []
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
                "reason": score_card.objective_signal.get("component_score_reason") if score_card else None,
                "conflict_handling": score_card.objective_signal.get("component_conflict_handling") if score_card else None,
                "evidence_ids": [card.evidence_id for card in component_cards],
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
                }
                for card in scoring_package_cards
            ],
            "confidence_audit": result.metadata.get("component_confidence", {}),
            "scoring_phase": "scored" if set(COMPONENTS[dimension]).issubset(scored) else "collecting",
            "scoring_provenance": dimension_scoring.get("provenance"),
            "scoring_attempts": dimension_scoring.get("scoring_attempts", 0),
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
                                "relation": card.semantic_relation,
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
                    "reason": next((card.objective_signal.get("component_score_reason") for card in all_dimension_cards if card.component == component and card.objective_signal.get("component_score_reason")), None),
                    "conflict_handling": next((card.objective_signal.get("component_conflict_handling") for card in all_dimension_cards if card.component == component and card.objective_signal.get("component_conflict_handling")), None),
                }
                for component in COMPONENTS[dimension]
                if component in scored
            ],
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
            {"actor": "系统", "label": "分数与确认度", "detail": "仅完整组件集进入固定权重统分；确认度仅随最终维度评分展示。"},
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
        _card("learning_goal", source, f"选择身份卡“{identity or '未指定'}”，目标标签为{meta['learning_goal']}{custom_fact}", {**signal, "label": meta["learning_goal"], "quote": utterance or None}, 1.0, component="expression_certainty"),
        _card("motivation", source, f"选择身份卡“{identity or '未指定'}”，动机标签为{meta['motivation']}{custom_fact}", {**signal, "label": meta["motivation"], "quote": utterance or None}, prior_weight, component="intrinsic_value"),
        _card("knowledge_base", source, "身份卡仅作为知识基础先验，不代表正确率", {**signal, "prior": True}, prior_weight, component="concept_foundation", collection_status="pending"),
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
        cards.append(
            _card(
                "knowledge_base",
                source,
                f"诊断题第{index}题{'放弃' if gave_up else ('答对' if correct else '答错')}" + (f"，补充说明{detail_length}字" if free_text else ""),
                {
                    "question_id": question_id,
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
                component="measured_performance",
            )
        )
        if gave_up or not correct:
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
                )
            )
    if clue_votes:
        cards.append(
            _card("learning_style", source, f"线索类型计票：{_count_labels(clue_votes)}", {"votes": _count_labels(clue_votes), "response_time_ms": response_time_ms}, 1.0)
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
    return _card("knowledge_base", source, "；".join(fact_parts), objective_signal, weight, component="retrieval_application")


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
            thinking={"type": "enabled"},
            reasoning_effort="medium",
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
    return _card("resource_preference", source, f"选择资源类型：{label}", signal, 1.0, component="preference_specificity")


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
    pace_fact = f"约定每日{minutes if minutes is not None else '未填'}分钟，周期{period_days if period_days is not None else '未填'}天"
    if note:
        pace_fact += f"；自述：{note}"
    cards = [_card("pace", source, pace_fact, signal, 1.0, component="plan_clarity")]
    if challenge:
        cards.append(_card("motivation", source, "主动接受挑战", signal, 1.0, component="actual_investment"))
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
        cards.append(_card(dimension, STEP_LABELS["passive"], f"{event_type}：{topic}", signal, 1.0))
    return cards


def _dimension_from_passive_event(event_type: str, resource_type: str) -> DimensionKey:
    if event_type in {"replay", "重看", "skip", "跳过"}:
        return "weak_points"
    if resource_type in RESOURCE_TYPES:
        return "resource_preference"
    if event_type in {"stay", "停留", "click", "点击"}:
        return "motivation"
    return "learning_style"


def _card(
    dimension: DimensionKey,
    source: str,
    fact: str,
    objective_signal: dict[str, Any],
    weight: float,
    *,
    component: str | None = None,
    collection_status: str | None = None,
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
    return EvidenceCard(
        dimension=dimension,
        source=source,
        fact=fact,
        objective_signal=signal,
        weight=weight,
        quote=str(quote).strip() if quote else None,
        basis=basis,
        extraction_confidence=extraction_confidence,
        extraction_method=method,
        knowledge_base_ref=signal.get("knowledge_base_ref") or signal.get("knowledge_ref"),
        component=component,
        collection_status=collection_status or ("scorable" if component else None),
        evidence_group_id=signal.get("evidence_group_id"),
        normalized_claim=signal.get("normalized_claim") or fact,
        supports=[str(item) for item in signal.get("supports", [])] if isinstance(signal.get("supports"), list) else [],
        confirmed_aspects=[str(item) for item in signal.get("confirmed_aspects", [])] if isinstance(signal.get("confirmed_aspects"), list) else [],
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
