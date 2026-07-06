"""Profile evidence collection modes.

This module only collects objective EvidenceCard records. It never writes
scores or synthesizes profile judgments.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import get_settings
from app.llm.router import ModelRouter
from app.profile.schema import DimensionKey, EvidenceCard, evidence_card_from_dict, evidence_card_to_json_dict
from app.profile.store import (
    DB_PATH,
    add_evidence,
    delete_profile_session_rows,
    get_profile_session_row,
    upsert_profile_session_row,
)


class ProfileMode(str, Enum):
    """Supported profile collection modes."""

    apprentice = "apprentice"
    quickcard = "quickcard"
    practice = "practice"
    passive = "passive"


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
}

IDENTITY_CARDS: dict[str, dict[str, str]] = {
    "考前冲刺": {"goal": "应试提分", "motivation": "外驱"},
    "临床学徒": {"goal": "临床实用", "motivation": "内驱"},
    "草木好奇者": {"goal": "兴趣科普", "motivation": "内驱"},
    "研读派": {"goal": "学术深造", "motivation": "内外混合"},
}

RESOURCE_TYPES = {"知识图谱", "拟人化短剧", "汤头歌诀", "医案文选"}
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

    response_time_ms = _response_time_ms(payload, session.current_step.started_at)
    if session.mode == ProfileMode.apprentice:
        cards = await _answer_apprentice(session, payload, response_time_ms)
    elif session.mode == ProfileMode.quickcard:
        cards = await _answer_quickcard(session, payload, response_time_ms)
    elif session.mode == ProfileMode.practice:
        cards = [await _prescription_card(payload, source=STEP_LABELS["practice"], weight=1.5, response_time_ms=response_time_ms)]
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
            "finished": 1 if session.persisted else 0,
            "evidence_cards_json": json.dumps(cards_payload, ensure_ascii=False),
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
        },
        db_path=db_path,
    )


def _session_from_row(row: dict[str, Any]) -> ProfileSession:
    mode = ProfileMode(str(row["mode"]))
    step_index = int(row["step_index"] or 0)
    persisted = bool(row["finished"])
    current_step = _step_for_index(mode, step_index) if int(row["current_step"]) else None
    evidence_payload = json.loads(str(row["evidence_cards_json"] or "[]"))
    cards = [evidence_card_from_dict(item) for item in evidence_payload if isinstance(item, dict)]
    session = ProfileSession(
        session_id=str(row["session_id"]),
        user_id=str(row["user_id"]),
        mode=mode,
        current_step=current_step,
        step_index=step_index,
        evidence_cards=cards,
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
        finished=persisted or current_step is None,
        persisted=persisted,
        persisted_db_path=DB_PATH.as_posix() if persisted else None,
    )
    if current_step and _is_quiz_step(current_step.id):
        session.step_data["questions"] = current_step.private_payload.get("questions", [])
    return session


def _step_for_index(mode: ProfileMode, step_index: int) -> ProfileStep | None:
    if mode == ProfileMode.apprentice:
        if 0 <= step_index < len(APPRENTICE_STEPS):
            return _apprentice_step(step_index)
        return None
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


def _identity_cards(payload: dict[str, Any], source: str, response_time_ms: int, prior_weight: float) -> list[EvidenceCard]:
    identity = str(payload.get("identity_card") or payload.get("choice") or "").strip()
    meta = IDENTITY_CARDS.get(identity, IDENTITY_CARDS["草木好奇者"])
    signal = {"identity_card": identity or "未指定", "response_time_ms": response_time_ms}
    return [
        _card("learning_goal", source, f"选择身份卡“{identity or '未指定'}”，目标标签为{meta['goal']}", {**signal, "label": meta["goal"]}, 1.0),
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
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    match = re.search(r"\{.*\}", stripped, flags=re.S)
    return json.loads(match.group(0) if match else stripped)


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
    "extract_prescription_signal",
    "finish_profile_session",
    "get_profile_session",
    "load_diagnostic_questions",
    "reset_profile_sessions",
    "session_payload",
    "start_profile_session",
]
