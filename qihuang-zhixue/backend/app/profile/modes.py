"""Profile evidence collection modes.

This module only collects objective EvidenceCard records. It never writes
scores or synthesizes profile judgments.
"""

from __future__ import annotations

import json
import random
import re
import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from app.config import get_settings
from app.llm.router import ModelRouter
from app.profile.fusion import ALLOWED_LABELS, BASE_LEVEL_SCORES, FusionResult, fuse_profile
from app.profile.jury import ScoringResult, score_profile
from app.profile.radar import LOW_CONFIDENCE_THRESHOLD
from app.profile.schema import (
    DIMENSION_LABELS,
    DimensionKey,
    EvidenceCard,
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
    upsert_profile,
    upsert_profile_session_row,
)


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
KNOWLEDGE_LEVEL_CARD_NAMES: dict[str, str] = {
    "零基础": "药童",
    "入门": "学徒",
    "一般": "坐堂医",
    "较好": "杏林手",
    "熟练": "大医",
}
PACE_LEVEL_ANCHORS: tuple[tuple[float, str], ...] = (
    (20.0, "文火慢炖"),
    (55.0, "温火常煎"),
    (85.0, "武火急煎"),
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
        persisted_db_path=DB_PATH.as_posix() if persisted else None,
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
            )
            data["next_question"] = prompt
        _dialogue_remember(asked_questions, prompt)
        return _dialogue_turn_step(step_index + 1, prompt, target)
    return _initial_step(mode)


def load_diagnostic_questions(limit: int = 5, data_dir: Path | None = None) -> list[dict[str, Any]]:
    """Build diagnostic questions from the herb knowledge files.

    The fallback is intentionally small and only used when the knowledge files
    are absent or malformed.
    """

    herbs = _load_herbs(data_dir)
    if not herbs:
        return FALLBACK_QUESTIONS[:limit]

    questions: list[dict[str, Any]] = []
    all_effects = sorted({effect for herb in herbs for effect in _string_list(herb.get("effects"))})
    all_names = sorted({str(herb.get("name", "")).strip() for herb in herbs if str(herb.get("name", "")).strip()})
    rng = random.Random(20260706)

    for herb in herbs:
        name = str(herb.get("name", "")).strip()
        effects = _string_list(herb.get("effects"))
        if name and effects:
            answer = effects[0]
            distractors = [effect for effect in all_effects if effect != answer]
            questions.append(
                {
                    "id": f"kb_{_safe_id(herb.get('id') or name)}_effect",
                    "question": f"{name}的代表功效之一是？",
                    "options": _options(answer, distractors, rng),
                    "answer": answer,
                    "topic": f"{_category_label(herb)}·{name}功效",
                    "skill": "功效识记",
                }
            )
        combinations = herb.get("combinations")
        if name and isinstance(combinations, list) and combinations:
            pair = _combination_name(combinations[0], all_names)
            if pair:
                questions.append(
                    {
                        "id": f"kb_{_safe_id(herb.get('id') or name)}_pair",
                        "question": f"{name}常见配伍线索中可搭配哪味药？",
                        "options": _options(pair, [item for item in all_names if item not in {name, pair}], rng),
                        "answer": pair,
                        "topic": f"{_category_label(herb)}·配伍",
                        "skill": "配伍判断",
                    }
                )
        if len(questions) >= limit:
            break

    return (questions or FALLBACK_QUESTIONS)[:limit]


async def extract_prescription_signal(payload: dict[str, Any]) -> dict[str, bool]:
    """Extract prescription objective signals; rules are the no-key fallback."""

    llm_signal = await _try_llm_prescription_extract(payload)
    if llm_signal is not None:
        return llm_signal
    return _rule_prescription_extract(payload)


def session_payload(session: ProfileSession, status: str) -> dict[str, Any]:
    """Stable API shape for session responses."""

    return {
        "session_id": session.session_id,
        "user_id": session.user_id,
        "mode": session.mode.value,
        "status": status,
        "step": session.current_step.to_dict() if session.current_step else None,
        "current_stage": session.current_stage,
        "finished": session.finished,
        "evidence_count": len(session.evidence_cards),
    }


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
        questions = load_diagnostic_questions(limit=5)
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
        return [await _prescription_card(payload, source=source, weight=1.5, response_time_ms=response_time_ms)]
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
    cards = _dialogue_cards_from_extraction(extraction)
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
    if _has_profile_extract_key():
        combined = await _try_llm_dialogue_turn(utterance, target, turn_index, evidence_cards, current_confidences or {}, dialogue_history=source_text)
        if combined is not None:
            return combined
        try:
            from app.profile.nodes import _extract_dialogue_structured

            extraction = await _extract_dialogue_structured(source_text)
            return extraction, ""
        except Exception:
            pass
    from app.profile.nodes import _fallback_dialogue_extraction

    return _fallback_dialogue_extraction(utterance), ""


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
    }
    messages = [
        {
            "role": "system",
            "content": (
                "你是学习画像证据抽取器，只输出 JSON。阅读整段对话，为每个维度抽取标签/数值，"
                "并给出用户【原话片段】作为 evidence。没有依据的维度必须 value=null，禁止猜测或填默认。"
                "confidence 反映证据强度：明确陈述→高，含糊暗示→低。不得创造 allowed_labels 之外的新标签。"
                "learning_goal 落不进应试提分/临床实用/兴趣科普/学术深造时，使用“其他”，并仍需原话 evidence。"
            ),
        },
        {
            "role": "user",
            "content": (
                json.dumps(prompt_payload, ensure_ascii=False)
                + "\n输出 JSON：{extraction:{major,learning_goal,base_level,learning_style,weak_points,motivation,"
                "motivation_score,resource_preference,daily_minutes,target_days,mastery_hint}}。"
                "其中每个字段都是 {value,confidence,basis,evidence}；learning_goal/motivation 的 value 可为标签分布对象。"
                "weak_points 只抽用户自述的易错、薄弱、困惑知识点原文短句，没有原文证据则 value=null。"
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
        if not isinstance(parsed, dict):
            return None
        extraction_payload = parsed.get("extraction") if isinstance(parsed.get("extraction"), dict) else parsed
        from app.profile.nodes import DialogueExtraction, _validate_dialogue_evidence

        extraction = _validate_dialogue_evidence(DialogueExtraction.model_validate(extraction_payload), source_text)
        return extraction, ""
    except Exception:
        return None


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
                "Generate exactly one concise, natural next question. "
                "Ask about next_target_dimension only. Do not output analysis."
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
    return fuse_profile(evidence_cards, confidence_weight_k=DIALOGUE_CONFIDENCE_WEIGHT_K)


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
) -> str:
    asked = asked_questions if asked_questions is not None else []
    asked_set = set(asked)
    recent_question = asked[-1] if asked else ""
    target_dimension = _as_dialogue_dimension(target) or "learning_goal"
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
    return []


def _dialogue_confidences(
    evidence_cards: list[EvidenceCard],
    *,
    fusion: dict[DimensionKey, FusionResult] | None = None,
) -> dict[str, float]:
    fused = fusion or _dialogue_fusion(evidence_cards)
    return {dimension: result.confidence for dimension, result in fused.items()}


def dialogue_session_summary(session: ProfileSession) -> dict[str, Any]:
    fusion = _dialogue_fusion(session.evidence_cards)
    profile = _profile_from_fusion(session.user_id, session.evidence_cards, fusion=fusion)
    card_scores, inferred_card = _identity_card_scores(session.evidence_cards, fusion=fusion)
    identity_cards = _dialogue_identity_cards_view(
        session.evidence_cards,
        fusion=fusion,
        revealed_flags=_dialogue_identity_card_revealed_flags(session.step_data),
    )
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
    }


async def stream_dialogue_chat_events(
    session_id: str,
    *,
    user_id: str,
    utterance: str,
    db_path: Path = DB_PATH,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Run the visible companion stream, then hidden extraction/fusion updates."""

    session = get_profile_session(session_id, user_id=user_id, db_path=db_path)
    if session.mode != ProfileMode.dialogue:
        raise ValueError("profile session is not dialogue mode")
    if session.current_step is None:
        yield "state", _dialogue_state_payload(session, target=None, converged=False)
        yield "done", {}
        return

    utterance = utterance.strip()
    turn_index = session.step_index + 1
    current_prompt = session.current_step.prompt
    fusion_before = _dialogue_fusion(session.evidence_cards)
    target = _dialogue_target(session.evidence_cards, fusion=fusion_before)
    current_confidences = _dialogue_confidences(session.evidence_cards, fusion=fusion_before)
    history_messages = list_chat_messages(user_id=user_id, session_id=session.session_id, db_path=db_path)
    companion_messages = _dialogue_companion_messages(
        history_messages=history_messages,
        utterance=utterance,
        target=target,
        evidence_cards=session.evidence_cards,
        fusion=fusion_before,
        current_confidences=current_confidences,
        pure_chat_streak=_dialogue_pure_chat_streak(session.step_data),
    )

    thinking_parts: list[str] = []
    assistant_parts: list[str] = []
    yield "thinking", {"delta": ""}
    try:
        async for kind, delta in ModelRouter().chat_stream(
            "reason",
            companion_messages,
            thinking={"type": "enabled"},
            reasoning_effort="medium",
            temperature=0.7,
        ):
            if kind == "thinking":
                thinking_parts.append(delta)
                yield "thinking", {"delta": delta}
            elif kind == "message":
                assistant_parts.append(delta)
                yield "message", {"delta": delta}
    except Exception as exc:
        _log_dialogue_stream_fulltext(session.session_id, turn_index, thinking_parts, assistant_parts)
        yield "error", {"message": f"visible dialogue stream failed: {exc.__class__.__name__}"}
        yield "message_done", {}
        yield "done", {}
        return

    assistant_text = "".join(assistant_parts)
    asked_dimension = await _dialogue_classify_visible_reply_asked_dimension(assistant_text, target)
    if _dialogue_user_asked_evidence(utterance) and not _dialogue_visible_reply_addresses_evidence(assistant_text):
        evidence_tail = " 你问得对，画像旁边的「证据清单」会按维度列出分/标签、置信度和你的原文引用；我不会编证据，只按你说过的话更新。"
        assistant_parts.append(evidence_tail)
        assistant_text = "".join(assistant_parts)
        yield "message", {"delta": evidence_tail}
    _log_dialogue_stream_fulltext(session.session_id, turn_index, thinking_parts, assistant_parts)
    yield "message_done", {}

    try:
        append_chat_messages(
            user_id=user_id,
            session_id=session.session_id,
            messages=[
                {"role": "user", "content": utterance},
                {"role": "assistant", "content": assistant_text},
            ],
            db_path=db_path,
        )

        response_time_ms = _response_time_ms({"utterance": utterance}, session.current_step.started_at)
        cards = await _dialogue_extract_cards_for_chat(
            session=session,
            utterance=utterance,
            assistant_text=assistant_text,
            history_messages=history_messages,
            target=target,
            turn_index=turn_index,
            response_time_ms=response_time_ms,
            current_prompt=current_prompt,
        )

        session.evidence_cards.extend(cards)
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
            revealed_flags=_dialogue_identity_card_revealed_flags(session.step_data),
        )
        converged = _dialogue_converged(turn_index, session.evidence_cards, fusion=fusion_after)
        profile = _profile_from_fusion(session.user_id, session.evidence_cards, fusion=fusion_after)
        if hit_target or pure_chat_streak >= DIALOGUE_TARGET_RETRY_LIMIT:
            next_target = _dialogue_target(session.evidence_cards, fusion=fusion_after, asked_dimensions=asked_dimensions)
        else:
            next_target = target
        session.finished = False
        session.current_step = _dialogue_turn_step(turn_index + 1, assistant_text, next_target)
        session.step_data["next_question"] = assistant_text
        session.step_data["dialogue_target"] = next_target
        session.step_data["_dialogue_fusion"] = fusion_after
        _persist_dialogue_live_update(session, cards=cards, profile=profile, db_path=db_path)

        yield "evidence", {"cards": cards_to_json(cards)}
        yield "identity_cards", {"items": identity_cards}
        yield "radar", _radar_payload(profile)
        yield "state", _dialogue_state_payload(session, target=next_target, converged=converged)
    except Exception as exc:
        yield "error", {"message": f"profile update delayed: {exc.__class__.__name__}"}
        yield "state", _dialogue_state_payload(session, target=target, converged=False, error="profile_update_failed")
    finally:
        yield "done", {}


def _log_dialogue_stream_fulltext(
    session_id: str,
    turn_index: int,
    thinking_parts: list[str],
    message_parts: list[str],
) -> None:
    thinking_text = "".join(thinking_parts)
    message_text = "".join(message_parts)
    print(
        "[profile.dialogue.stream.fulltext]",
        {
            "session_id": session_id,
            "turn_index": turn_index,
            "thinking_len": len(thinking_text),
            "message_len": len(message_text),
            "thinking": thinking_text,
            "message": message_text,
        },
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
) -> list[dict[str, str]]:
    low_confidence_dims = _dialogue_low_confidence_dims(evidence_cards, fusion=fusion, target=target)
    steering_payload = {
        "target_dimension": target,
        "target_label": DIALOGUE_TARGET_LABELS.get(target, target),
        "current_confidences": current_confidences,
        "low_confidence_dimensions": low_confidence_dims,
        "pure_chat_streak": pure_chat_streak,
        "profile_question_examples": list(DIALOGUE_TARGET_EXTRACTABLE_QUESTIONS.get(target, ())),
    }
    private_prompt = (
        "你是岐黄智学里热情、懂中医药的学长「小岐」。"
        "语气保持温暖、生活化，可以先接住用户的养生或日常话题。"
        "但你有一个陪聊预算：连续纯寒暄/纯养生闲聊不得超过 2 轮；每 2-3 轮必须自然问 1 个能被抽取器识别的画像问题。"
        "本轮优先照顾 profile_steering.target_dimension，不要整轮完全不碰这个目标维度。"
        "目标维度的问题必须落到可抽取答案：学习目标=应试提分/临床实用/兴趣科普/学术深造；"
        "风格=视觉/文字/记忆(口诀)；动机=内驱/外驱/内外混合/被动；"
        "资源=知识图谱/拟人化短剧/汤头歌诀/医案文选；节奏=每天学习分钟数/目标天数；"
        "知识基础=中医知识自评或是否愿意做小测；薄弱点=药性功效/配伍剂量/辨证思路/背诵记忆等卡点。"
        "不要为了取证破坏温暖感：先回应情绪和生活语境，再用一句自然追问推进。"
        "用户问“证据、凭什么打分、为什么这么判断”时，不要回避；简短说明会按原话引用，并引导查看雷达旁的「证据清单」面板。"
        "不要声称看不到证据，也不要把证据塞成聊天气泡长列表。"
        "禁止表单式提问，禁止罗列大段选项。一次只聊一个话题。"
    )
    private_prompt += "正文必须是连贯完整的中文句子，使用正常中文标点；保持单段自然口吻，不要一节一节拆开。"
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
    extraction, _ = await _dialogue_extract_and_question(
        utterance=utterance,
        dialogue_history=extraction_text,
        target=target,
        turn_index=turn_index,
        evidence_cards=session.evidence_cards,
        current_confidences={},
    )
    cards = _dialogue_cards_from_extraction(extraction)
    for card in cards:
        card.objective_signal["turn_index"] = turn_index
        card.objective_signal["response_time_ms"] = response_time_ms
        card.objective_signal["dialogue_target"] = target
        card.objective_signal["asked_question"] = current_prompt
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
    converged: bool,
    error: str | None = None,
) -> dict[str, Any]:
    fusion = _dialogue_fusion(session.evidence_cards)
    payload: dict[str, Any] = {
        "session_id": session.session_id,
        "turn_index": session.step_index,
        "target": target,
        "target_label": DIALOGUE_TARGET_LABELS.get(target, target) if target else None,
        "converged": converged,
        "finished": False,
        "profile_progress": _dialogue_profile_progress(session.evidence_cards, fusion=fusion),
        "evidence_panel": _dialogue_evidence_panel(session.evidence_cards, fusion=fusion),
        "identity_cards": _dialogue_identity_cards_view(
            session.evidence_cards,
            fusion=fusion,
            revealed_flags=_dialogue_identity_card_revealed_flags(session.step_data),
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
            "collected": fused[dimension].confidence >= LOW_CONFIDENCE_THRESHOLD,
            "status": "已取证" if fused[dimension].confidence >= LOW_CONFIDENCE_THRESHOLD else "待取证",
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
            "status": "已取证" if fused[dimension].confidence >= LOW_CONFIDENCE_THRESHOLD else "待取证",
            "evidence_refs": fused[dimension].evidence_refs,
            "narrative": fused[dimension].narrative,
            "quotes": _dialogue_dimension_quotes(dimension, evidence_cards),
        }
        for dimension in DIMENSION_LABELS
    ]


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
        if not raw_quotes:
            raw_quotes = [card.fact]
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


def _dialogue_identity_cards_view(
    evidence_cards: list[EvidenceCard],
    *,
    fusion: dict[DimensionKey, FusionResult] | None = None,
    revealed_flags: dict[str, bool] | None = None,
    keep_revealed: bool = True,
) -> list[dict[str, Any]]:
    """Build the seven-dimension identity-card view from already-fused evidence."""

    fused = fusion or _dialogue_fusion(evidence_cards)
    flags = revealed_flags if revealed_flags is not None else {}
    return [
        _identity_distribution_item(dimension, fused[dimension], evidence_cards, flags, keep_revealed=keep_revealed)
        for dimension in ("learning_goal", "motivation", "resource_preference", "learning_style")
    ] + [
        _identity_level_item("knowledge_base", fused["knowledge_base"], evidence_cards, flags, keep_revealed=keep_revealed),
        _identity_level_item("pace", fused["pace"], evidence_cards, flags, keep_revealed=keep_revealed),
        _identity_tags_item("weak_points", fused["weak_points"], evidence_cards, flags, keep_revealed=keep_revealed),
    ]


def _identity_distribution_item(
    dimension: DimensionKey,
    result: FusionResult,
    evidence_cards: list[EvidenceCard],
    revealed_flags: dict[str, bool],
    *,
    keep_revealed: bool,
) -> dict[str, Any]:
    distribution = _normalize_identity_distribution(result.distribution)
    cards = [
        {
            "canonical_label": label,
            "display_name": _identity_distribution_display_name(dimension, label, evidence_cards),
            "ratio": ratio,
            "quotes": _identity_quotes_for_label(dimension, label, evidence_cards),
        }
        for label, ratio in distribution.items()
    ]
    return {
        "key": dimension,
        "kind": "distribution",
        "revealed": _identity_revealed(dimension, result.confidence, revealed_flags, keep_revealed=keep_revealed),
        "confidence": result.confidence,
        "cards": cards,
    }


def _identity_level_item(
    dimension: DimensionKey,
    result: FusionResult,
    evidence_cards: list[EvidenceCard],
    revealed_flags: dict[str, bool],
    *,
    keep_revealed: bool,
) -> dict[str, Any]:
    return {
        "key": dimension,
        "kind": "level",
        "revealed": _identity_revealed(dimension, result.confidence, revealed_flags, keep_revealed=keep_revealed),
        "confidence": result.confidence,
        "cards": [
            {
                "level_name": _identity_level_name(dimension, result.score),
                "score": result.score,
                "quotes": [quote["text"] for quote in _dialogue_dimension_quotes(dimension, evidence_cards)],
            }
        ],
    }


def _identity_tags_item(
    dimension: DimensionKey,
    result: FusionResult,
    evidence_cards: list[EvidenceCard],
    revealed_flags: dict[str, bool],
    *,
    keep_revealed: bool,
) -> dict[str, Any]:
    distribution = _normalize_identity_distribution(result.distribution)
    cards = [
        {
            "label": label,
            "quotes": _identity_quotes_for_label(dimension, label, evidence_cards),
        }
        for label in distribution
    ]
    return {
        "key": dimension,
        "kind": "tags",
        "revealed": _identity_revealed(dimension, result.confidence, revealed_flags, keep_revealed=keep_revealed),
        "confidence": result.confidence,
        "cards": cards,
    }


def _identity_revealed(
    dimension: DimensionKey,
    confidence: float,
    revealed_flags: dict[str, bool],
    *,
    keep_revealed: bool,
) -> bool:
    current = confidence >= LOW_CONFIDENCE_THRESHOLD
    previous = bool(revealed_flags.get(dimension))
    revealed = current or (keep_revealed and previous)
    if revealed:
        revealed_flags[dimension] = True
    return revealed


def _normalize_identity_distribution(distribution: dict[str, float]) -> dict[str, float]:
    weighted = {str(label): max(0.0, float(weight)) for label, weight in distribution.items() if float(weight) > 0}
    total = sum(weighted.values())
    if total <= 0:
        return {}
    normalized = {label: round(weight / total, 4) for label, weight in weighted.items()}
    drift = round(1.0 - sum(normalized.values()), 4)
    if normalized and drift:
        top = max(weighted.items(), key=lambda item: item[1])[0]
        normalized[top] = round(max(0.0, normalized[top] + drift), 4)
    return dict(sorted(normalized.items(), key=lambda item: (-item[1], item[0])))


def _identity_distribution_display_name(dimension: DimensionKey, label: str, evidence_cards: list[EvidenceCard]) -> str:
    if dimension == "learning_goal":
        return _learning_goal_display_name(label, evidence_cards)
    return IDENTITY_CARD_SKINS.get(dimension, {}).get(label, label)


def _learning_goal_display_name(label: str, evidence_cards: list[EvidenceCard]) -> str:
    quotes = _weighted_identity_quotes_for_label("learning_goal", label, evidence_cards)
    if not quotes:
        return label
    limit = 2 if label == "其他" else 1
    return " / ".join(quote for quote, _ in quotes[:limit])


def _identity_level_name(dimension: DimensionKey, score: float | None) -> str:
    if score is None:
        return "待取证"
    if dimension == "knowledge_base":
        label = _nearest_anchor_label(score, BASE_LEVEL_SCORES)
        return KNOWLEDGE_LEVEL_CARD_NAMES.get(label, label)
    if dimension == "pace":
        return _nearest_anchor_name(score, PACE_LEVEL_ANCHORS)
    return "待取证"


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
        quotes = _evidence_quote_texts(card) or [card.fact]
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
        target.evidence = [card for card in evidence_cards if card.dimension == dimension]
        target.narrative = result.narrative
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
    meta = IDENTITY_CARDS.get(identity, IDENTITY_CARDS["草木好奇者"])
    signal = {"identity_card": identity or "未指定", "response_time_ms": response_time_ms}
    return [
        _card("learning_goal", source, f"选择身份卡“{identity or '未指定'}”，目标标签为{meta['learning_goal']}", {**signal, "label": meta["learning_goal"]}, 1.0),
        _card("motivation", source, f"选择身份卡“{identity or '未指定'}”，动机标签为{meta['motivation']}", {**signal, "label": meta["motivation"]}, prior_weight),
        _card("knowledge_base", source, "身份卡仅作为知识基础先验，不代表正确率", {**signal, "prior": True}, prior_weight),
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
        gave_up = bool(item.get("gave_up") or item.get("abandoned")) or selected == ""
        correct = (selected == expected) if expected else bool(item.get("correct") or item.get("is_correct"))
        difficulty = str(item.get("difficulty") or question.get("difficulty") or "easy")
        weight = 1.2 if difficulty in {"hard", "难", "difficult"} else 0.8
        if gave_up:
            weight = 0.6
        item_rt = _response_time_ms(item, None) or response_time_ms
        topic = str(item.get("topic") or question.get("topic") or "诊断题").strip()
        clue_type = str(item.get("clue_type") or item.get("clue") or question.get("skill") or "").strip()
        if clue_type:
            clue_votes.append(_style_from_clue(clue_type))
        cards.append(
            _card(
                "knowledge_base",
                source,
                f"诊断题第{index}题{'放弃' if gave_up else ('答对' if correct else '答错')}",
                {
                    "question_id": question_id,
                    "selected": selected or None,
                    "expected_answer": expected or None,
                    "correct": correct,
                    "gave_up": gave_up,
                    "difficulty": difficulty,
                    "topic": topic,
                    "response_time_ms": item_rt,
                },
                weight,
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
                        "expected_answer": expected or None,
                        "correct": correct,
                        "gave_up": gave_up,
                        "response_time_ms": item_rt,
                    },
                    weight,
                )
            )
    if clue_votes:
        cards.append(
            _card("learning_style", source, f"线索类型计票：{_count_labels(clue_votes)}", {"votes": _count_labels(clue_votes), "response_time_ms": response_time_ms}, 1.0)
        )
    return cards


async def _prescription_card(payload: dict[str, Any], source: str, weight: float, response_time_ms: int) -> EvidenceCard:
    signal = await extract_prescription_signal(payload)
    weak_labels = _weak_labels_from_prescription_signal(signal)
    objective_signal: dict[str, Any] = {**signal, "response_time_ms": response_time_ms}
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
    return _card("knowledge_base", source, "；".join(fact_parts), objective_signal, weight)


async def _try_llm_prescription_extract(payload: dict[str, Any]) -> dict[str, bool] | None:
    settings = get_settings()
    if not (settings.deepseek_api_key or settings.zhipu_api_key):
        return None
    prompt = (
        "只输出JSON，不要解释。请从中医开方答题中抽取 objective_signal，"
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
    if selected not in RESOURCE_TYPES:
        selected = "知识图谱"
    return _card("resource_preference", source, f"选择资源类型：{selected}", {"label": selected, "response_time_ms": response_time_ms}, 1.0)


def _learning_rule_cards(payload: dict[str, Any], source: str, response_time_ms: int) -> list[EvidenceCard]:
    minutes = _int_or_none(payload.get("daily_minutes") or payload.get("minutes_per_day"))
    period_days = _int_or_none(payload.get("target_days") or payload.get("period_days"))
    challenge = bool(payload.get("accept_challenge") or payload.get("challenge"))
    signal = {
        "daily_minutes": minutes,
        "target_days": period_days,
        "accept_challenge": challenge,
        "response_time_ms": response_time_ms,
    }
    cards = [_card("pace", source, f"约定每日{minutes if minutes is not None else '未填'}分钟，周期{period_days if period_days is not None else '未填'}天", signal, 1.0)]
    if challenge:
        cards.append(_card("motivation", source, "主动接受挑战", signal, 1.0))
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


def _card(dimension: DimensionKey, source: str, fact: str, objective_signal: dict[str, Any], weight: float) -> EvidenceCard:
    return EvidenceCard(
        dimension=dimension,
        source=source,
        fact=fact,
        objective_signal={key: value for key, value in objective_signal.items() if value is not None},
        weight=weight,
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
        {key: value for key, value in question.items() if key != "answer"}
        for question in questions
    ]


def _is_quiz_step(step_id: str) -> bool:
    return step_id in {"quickcard", "apprentice_act1_quiz"}


def _options(answer: str, distractors: list[str], rng: random.Random) -> list[str]:
    pool = [item for item in distractors if item and item != answer]
    chosen = pool[:3]
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
