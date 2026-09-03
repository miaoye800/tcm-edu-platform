"""ProfileAgent 的 LangGraph 节点。"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict, cast

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.llm.clients import ChatMessage
from app.llm.router import ModelRouter
from app.profile.fusion import ALLOWED_LABELS, BASE_LEVEL_SCORES
from app.profile.jury import ScoringResult, score_profile
from app.profile.evidence import ExtractionMethod
from app.profile.modes import load_diagnostic_questions
from app.profile.schema import (
    DIMENSION_LABELS,
    DimensionKey,
    EvidenceCard,
    Profile,
    WeakPoint,
    create_blank_profile,
)

ProfileIntent = Literal["profile_setup", "knowledge_qa", "other"]


class TraceItem(TypedDict):
    node: str
    summary: str


class ProfileState(TypedDict):
    user_id: str
    db_path: str | None
    messages: list[ChatMessage]
    diagnostic_answers: dict[str, str]
    intent: ProfileIntent | None
    dialogue_features: dict[str, Any]
    questions: list[dict[str, str]]
    quiz_features: dict[str, Any]
    evidence_cards: list[EvidenceCard]
    profile: dict[str, Any]
    trace: list[TraceItem]


EvidenceBasis = Literal["explicit", "inferred"]
SignalValue = str | dict[str, float] | None


class ExtractedSignal(BaseModel):
    """One traceable label/distribution extracted from dialogue."""

    value: SignalValue = None
    confidence: float = Field(default=0.0, ge=0, le=1)
    basis: EvidenceBasis = "inferred"
    evidence: list[str] = Field(default_factory=list)
    extraction_method: ExtractionMethod = "llm"

    @field_validator("value", mode="before")
    @classmethod
    def _empty_value_to_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("value")
    @classmethod
    def _clean_distribution(cls, value: SignalValue) -> SignalValue:
        if not isinstance(value, dict):
            return value.strip() if isinstance(value, str) else value
        cleaned: dict[str, float] = {}
        for raw_label, raw_weight in value.items():
            label = str(raw_label).strip()
            try:
                weight = float(raw_weight)
            except (TypeError, ValueError):
                continue
            if label and weight > 0:
                cleaned[label] = weight
        return cleaned or None

    @field_validator("evidence", mode="before")
    @classmethod
    def _clean_evidence(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            candidates = [value]
        elif isinstance(value, list):
            candidates = value
        else:
            return []
        snippets: list[str] = []
        for item in candidates:
            snippet = str(item).strip()
            if snippet and snippet not in snippets:
                snippets.append(snippet)
        return snippets


class ExtractedNumber(BaseModel):
    """One traceable numeric clue extracted from dialogue."""

    value: float | None = Field(default=None, ge=0)
    confidence: float = Field(default=0.0, ge=0, le=1)
    basis: EvidenceBasis = "inferred"
    evidence: list[str] = Field(default_factory=list)
    extraction_method: ExtractionMethod = "llm"

    @field_validator("evidence", mode="before")
    @classmethod
    def _clean_evidence(cls, value: Any) -> list[str]:
        return ExtractedSignal._clean_evidence(value)


class ExtractedScore(ExtractedNumber):
    value: float | None = Field(default=None, ge=0, le=100)


def _empty_signal() -> ExtractedSignal:
    return ExtractedSignal()


def _empty_number() -> ExtractedNumber:
    return ExtractedNumber()


def _empty_score() -> ExtractedScore:
    return ExtractedScore()


class DialogueExtraction(BaseModel):
    """Structured LLM output consumed by dialogue_extract_node."""

    major: ExtractedSignal = Field(default_factory=_empty_signal)
    learning_goal: ExtractedSignal = Field(default_factory=_empty_signal)
    base_level: ExtractedSignal = Field(default_factory=_empty_signal)
    learning_style: ExtractedSignal = Field(default_factory=_empty_signal)
    weak_points: ExtractedSignal = Field(default_factory=_empty_signal)
    motivation: ExtractedSignal = Field(default_factory=_empty_signal)
    motivation_score: ExtractedScore = Field(default_factory=_empty_score)
    resource_preference: ExtractedSignal = Field(default_factory=_empty_signal)
    daily_minutes: ExtractedNumber = Field(default_factory=_empty_number)
    target_days: ExtractedNumber = Field(default_factory=_empty_number)
    mastery_hint: ExtractedScore = Field(default_factory=_empty_score)
    extraction_method: ExtractionMethod = "llm"

    @model_validator(mode="before")
    @classmethod
    def _upgrade_legacy_shape(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        legacy_evidence = data.get("evidence") if isinstance(data.get("evidence"), dict) else {}
        aliases = {
            "motivation_label": "motivation",
            "knowledge_mastery_hint": "mastery_hint",
        }
        for old_key, new_key in aliases.items():
            if old_key in data and new_key not in data:
                data[new_key] = data.pop(old_key)
        for key in (
            "major",
            "learning_goal",
            "base_level",
            "learning_style",
            "weak_points",
            "motivation",
            "resource_preference",
            "daily_minutes",
            "target_days",
            "motivation_score",
            "mastery_hint",
        ):
            raw = data.get(key)
            if isinstance(raw, (ExtractedSignal, ExtractedNumber)):
                continue
            if isinstance(raw, dict) and {"value", "confidence", "basis", "evidence"} & set(raw):
                continue
            if raw is None:
                data[key] = {}
                continue
            evidence_key = "motivation_label" if key == "motivation" else key
            if key in {"daily_minutes", "target_days", "motivation_score", "mastery_hint"}:
                data[key] = {
                    "value": raw,
                    "confidence": 0.6,
                    "basis": "explicit",
                    "evidence": legacy_evidence.get(evidence_key, []),
                }
            else:
                data[key] = {
                    "value": raw,
                    "confidence": 0.6,
                    "basis": "explicit",
                    "evidence": legacy_evidence.get(evidence_key, []),
                }
        data.pop("evidence", None)
        return data

    @model_validator(mode="after")
    def _drop_unknown_labels(self) -> "DialogueExtraction":
        _restrict_signal(self.learning_goal, ALLOWED_LABELS["learning_goal"])
        _restrict_signal(self.learning_style, ALLOWED_LABELS["learning_style"])
        _restrict_signal(self.motivation, ALLOWED_LABELS["motivation"])
        _restrict_signal(self.resource_preference, ALLOWED_LABELS["resource_preference"])
        _restrict_signal(self.base_level, tuple(BASE_LEVEL_SCORES))
        return self


class DiagnosticItemEvaluation(BaseModel):
    """One diagnostic/practice answer evaluation from structured output."""

    question_id: str
    correct: bool
    topic: str
    skill: str | None = None
    weak_label: str | None = None
    evidence: str


class DiagnosticQuestion(BaseModel):
    """Cold-start diagnostic question."""

    id: str
    question: str
    answer: str
    topic: str
    skill: str


class DiagnosticEvaluation(BaseModel):
    """Structured diagnostic output consumed by diagnostic_eval_node."""

    items: list[DiagnosticItemEvaluation] = Field(default_factory=list)
    summary: str = ""


async def router_node(state: ProfileState) -> ProfileState:
    """识别画像冷启动意图，并通过条件边决定是否进入构建链路。"""

    router = ModelRouter()
    intent = await router.classify(state["messages"])
    next_state = copy_state(state)
    next_state["intent"] = cast(ProfileIntent, intent)
    append_trace(next_state, "profile_router", f"识别意图：{intent}")
    return next_state


async def dialogue_extract_node(state: ProfileState) -> ProfileState:
    """用 LLM 结构化抽取专业、目标、基础、偏好，并写成证据卡。"""

    text = dialogue_transcript(state["messages"])
    extraction = await _extract_dialogue_structured(text)
    cards = _dialogue_cards(extraction)

    next_state = copy_state(state)
    next_state["dialogue_features"] = extraction.model_dump(mode="json")
    next_state["evidence_cards"].extend(cards)
    summary = _signal_label(extraction.learning_goal) or _signal_label(extraction.base_level) or "待补充"
    append_trace(next_state, "dialogue_extract_agent", f"结构化抽取画像线索：{summary}，证据卡 {len(cards)} 张")
    return next_state


async def diagnostic_question_node(state: ProfileState) -> ProfileState:
    """从知识库动态生成冷启动诊断小测。"""

    questions = [
        DiagnosticQuestion.model_validate(item)
        for item in load_diagnostic_questions(limit=5)
    ]
    next_state = copy_state(state)
    next_state["questions"] = [question.model_dump(mode="json") for question in questions]
    append_trace(next_state, "diagnostic_question_agent", f"生成 {len(questions)} 道知识库诊断题")
    return next_state


async def diagnostic_eval_node(state: ProfileState) -> ProfileState:
    """消费 LLM 结构化诊断评估，把结果转换为 EvidenceCard。"""

    evaluation = await _evaluate_diagnostic_structured(
        questions=state["questions"],
        answers=state["diagnostic_answers"],
    )
    cards = _diagnostic_cards(evaluation)
    correct_count = sum(1 for item in evaluation.items if item.correct)
    total = max(len(evaluation.items), 1)
    weak_points = [item.weak_label or item.topic for item in evaluation.items if not item.correct]

    next_state = copy_state(state)
    next_state["quiz_features"] = {
        "correct_count": correct_count,
        "total": total,
        "weak_points": weak_points,
        "score": round(correct_count / total * 100),
        "summary": evaluation.summary,
    }
    next_state["evidence_cards"].extend(cards)
    append_trace(next_state, "diagnostic_eval_agent", f"诊断正确 {correct_count}/{total}，证据卡 {len(cards)} 张")
    return next_state


async def profile_synthesis_node(state: ProfileState) -> ProfileState:
    """调用 jury + guardrail + fusion，合成带 confidence 的 7 维画像。"""

    evidence_cards = list(state["evidence_cards"])
    scoring = await score_profile(evidence_cards)
    profile = _profile_from_scoring_results(
        user_id=state["user_id"],
        scoring=scoring,
        evidence_cards=evidence_cards,
    )
    next_state = copy_state(state)
    next_state["profile"] = profile.model_dump(mode="json")
    next_state["profile"]["provenance"] = {
        dimension: result.provenance for dimension, result in scoring.items()
    }
    append_trace(next_state, "profile_synthesis_agent", "按证据卡、三师会诊、护栏与融合生成 7 维画像")
    return next_state


def route_by_intent(state: ProfileState) -> ProfileIntent:
    """LangGraph 条件边路由函数。"""

    return state["intent"] or "other"


def initial_state(
    user_id: str,
    first_message: str,
    diagnostic_answers: dict[str, str] | None = None,
    db_path: str | None = None,
) -> ProfileState:
    """构造冷启动图初始状态。"""

    return {
        "user_id": user_id,
        "db_path": db_path,
        "messages": [{"role": "user", "content": first_message}],
        "diagnostic_answers": dict(diagnostic_answers or {}),
        "intent": None,
        "dialogue_features": {},
        "questions": [],
        "quiz_features": {},
        "evidence_cards": [],
        "profile": {},
        "trace": [],
    }


def copy_state(state: ProfileState) -> ProfileState:
    """复制状态，避免节点间共享可变对象。"""

    return {
        "user_id": state["user_id"],
        "db_path": state["db_path"],
        "messages": list(state["messages"]),
        "diagnostic_answers": dict(state["diagnostic_answers"]),
        "intent": state["intent"],
        "dialogue_features": dict(state["dialogue_features"]),
        "questions": [dict(item) for item in state["questions"]],
        "quiz_features": dict(state["quiz_features"]),
        "evidence_cards": list(state["evidence_cards"]),
        "profile": dict(state["profile"]),
        "trace": list(state["trace"]),
    }


def append_trace(state: ProfileState, node: str, summary: str) -> None:
    """追加可观测执行轨迹。"""

    state["trace"].append({"node": node, "summary": summary})


def last_user_content(messages: list[ChatMessage]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content", ""))
    if messages:
        return str(messages[-1].get("content", ""))
    return ""


def dialogue_transcript(messages: list[ChatMessage]) -> str:
    rows: list[str] = []
    for message in messages:
        role = str(message.get("role") or "unknown")
        content = str(message.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            rows.append(f"{role}: {content}")
    return "\n".join(rows) or last_user_content(messages)


async def _extract_dialogue_structured(text: str) -> DialogueExtraction:
    router = ModelRouter()
    messages: list[ChatMessage] = [
        {
            "role": "system",
            "content": (
                "你是学习画像证据抽取器，只输出 JSON。阅读整段对话，为每个维度抽取标签/数值，"
                "并给出用户【原话片段】作为 evidence。没有依据的维度必须 value=null，禁止猜测或填默认。"
                "你只负责提取证据，不得输出或决定最终画像分数；数值只能是用户原话中的线索。"
                "confidence 反映证据强度：明确陈述→高，含糊暗示→低。不得创造 allowed_labels 之外的新标签。"
                "learning_goal 落不进应试提分/临床实用/兴趣科普/学术深造时，只能用 allowed_labels 中的“其他”，"
                "且 evidence 必须是用户原话子串。"
            ),
        },
        {
            "role": "user",
            "content": (
                json.dumps(
                    {
                        "dialogue_history": text,
                        "allowed_labels": {
                            "learning_goal": list(ALLOWED_LABELS["learning_goal"]),
                            "base_level": list(BASE_LEVEL_SCORES),
                            "learning_style": list(ALLOWED_LABELS["learning_style"]),
                            "motivation": list(ALLOWED_LABELS["motivation"]),
                            "resource_preference": list(ALLOWED_LABELS["resource_preference"]),
                        },
                        "schema": (
                            "major, learning_goal, base_level, learning_style, weak_points, motivation, resource_preference "
                            "均为 {value:string|object|null, confidence:0..1, basis:explicit|inferred, evidence:string[]}；"
                            "weak_points 只抽用户自述的易错/薄弱/困惑知识点原文短句，没有原文依据则 value=null；"
                            "daily_minutes, target_days, motivation_score, mastery_hint "
                            "均为 {value:number|null, confidence:0..1, basis:explicit|inferred, evidence:string[]}。"
                        ),
                    },
                    ensure_ascii=False,
                )
            ),
        },
    ]
    raw = await router.chat(
        "profile_extract",
        messages,
        response_format={"type": "json_object"},
        temperature=0,
        thinking={"type": "disabled"},
    )
    try:
        return _validate_dialogue_evidence(DialogueExtraction.model_validate(_json_object(raw)), text)
    except (ValueError, ValidationError):
        return _fallback_dialogue_extraction(text)


async def _evaluate_diagnostic_structured(
    *,
    questions: list[dict[str, str]],
    answers: dict[str, str],
) -> DiagnosticEvaluation:
    router = ModelRouter()
    messages: list[ChatMessage] = [
        {
            "role": "system",
            "content": (
                "你是中药学诊断小测评估器。只输出 JSON，不要 Markdown。"
                "逐题判断 correct，并给可追溯 evidence；错误题必须给 weak_label。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {"questions": questions, "answers": answers},
                ensure_ascii=False,
            ),
        },
    ]
    raw = await router.chat("profile_extract", messages, response_format={"type": "json_object"}, temperature=0)
    try:
        parsed = DiagnosticEvaluation.model_validate(_json_object(raw))
    except (ValueError, ValidationError):
        parsed = _fallback_diagnostic_evaluation(questions, answers)
    if not parsed.items:
        return _fallback_diagnostic_evaluation(questions, answers)
    return parsed


def _dialogue_cards(extraction: DialogueExtraction | dict[str, Any]) -> list[EvidenceCard]:
    if isinstance(extraction, dict):
        extraction = DialogueExtraction.model_validate(extraction)
    timestamp = datetime.now(UTC)
    cards: list[EvidenceCard] = []
    base_level = _signal_label(extraction.base_level)
    if base_level:
        cards.append(
            _card(
                "knowledge_base",
                "冷启动对话",
                _fact("基础自述", base_level, extraction.base_level.evidence),
                {
                    "prior": True,
                    "label": base_level,
                    "base_level": base_level,
                    "major": _signal_label(extraction.major),
                    "evidence": extraction.base_level.evidence,
                    "confidence": extraction.base_level.confidence,
                    "basis": extraction.base_level.basis,
                },
                extraction.base_level.confidence,
                timestamp,
            )
        )
    if extraction.mastery_hint.value is not None:
        cards.append(
            _card(
                "knowledge_base",
                "冷启动对话",
                _fact("掌握度自述", _format_number(extraction.mastery_hint.value), extraction.mastery_hint.evidence),
                {
                    "value": extraction.mastery_hint.value,
                    "mastery_hint": extraction.mastery_hint.value,
                    "evidence": extraction.mastery_hint.evidence,
                    "confidence": extraction.mastery_hint.confidence,
                    "basis": extraction.mastery_hint.basis,
                },
                extraction.mastery_hint.confidence,
                timestamp,
            )
        )
    _add_label_card(cards, "learning_goal", "学习目标", extraction.learning_goal, timestamp)
    _add_label_card(cards, "learning_style", "学习风格", extraction.learning_style, timestamp)
    _add_label_card(cards, "resource_preference", "资源偏好", extraction.resource_preference, timestamp)
    weak_label = _signal_label(extraction.weak_points)
    weak_distribution = _signal_distribution(extraction.weak_points)
    if weak_label or weak_distribution:
        weak_labels = list(weak_distribution or {})
        if weak_label and weak_label not in weak_labels:
            weak_labels.insert(0, weak_label)
        cards.append(
            _card(
                "weak_points",
                "冷启动对话",
                _fact("薄弱点自述", weak_label or "薄弱点分布", extraction.weak_points.evidence),
                {
                    "label": weak_label,
                    "weak_labels": weak_labels,
                    "evidence": extraction.weak_points.evidence,
                    "confidence": extraction.weak_points.confidence,
                    "basis": extraction.weak_points.basis,
                },
                extraction.weak_points.confidence,
                timestamp,
            )
        )
    motivation_label = _signal_label(extraction.motivation)
    if motivation_label or _signal_distribution(extraction.motivation):
        cards.append(
            _card(
                "motivation",
                "冷启动对话",
                _fact("动机线索", motivation_label or "倾向分布", extraction.motivation.evidence),
                {
                    "label": motivation_label,
                    "distribution": _signal_distribution(extraction.motivation),
                    "evidence": extraction.motivation.evidence,
                    "confidence": extraction.motivation.confidence,
                    "basis": extraction.motivation.basis,
                },
                extraction.motivation.confidence,
                timestamp,
            )
        )
    if extraction.motivation_score.value is not None:
        cards.append(
            _card(
                "motivation",
                "冷启动对话",
                _fact("动机强度", _format_number(extraction.motivation_score.value), extraction.motivation_score.evidence),
                {
                    "motivation_score": extraction.motivation_score.value,
                    "evidence": extraction.motivation_score.evidence,
                    "confidence": extraction.motivation_score.confidence,
                    "basis": extraction.motivation_score.basis,
                },
                extraction.motivation_score.confidence,
                timestamp,
            )
        )
    if extraction.daily_minutes.value is not None or extraction.target_days.value is not None:
        evidence = [*extraction.daily_minutes.evidence, *extraction.target_days.evidence]
        confidence = max(extraction.daily_minutes.confidence, extraction.target_days.confidence)
        cards.append(
            _card(
                "pace",
                "冷启动对话",
                "学习节奏线索",
                {
                    "daily_minutes": _int_if_whole(extraction.daily_minutes.value),
                    "target_days": _int_if_whole(extraction.target_days.value),
                    "evidence": evidence,
                    "confidence": confidence,
                    "basis": "explicit"
                    if "explicit" in {extraction.daily_minutes.basis, extraction.target_days.basis}
                    else "inferred",
                },
                confidence,
                timestamp,
            )
        )
    method = extraction.extraction_method
    for card in cards:
        evidence = card.objective_signal.get("evidence")
        quotes = evidence if isinstance(evidence, list) else [evidence] if isinstance(evidence, str) else []
        card.quote = str(quotes[0]).strip() if quotes and str(quotes[0]).strip() else None
        basis = card.objective_signal.get("basis")
        if basis in {"explicit", "inferred"}:
            card.basis = basis
        confidence = card.objective_signal.get("confidence")
        if isinstance(confidence, (int, float)):
            card.extraction_confidence = max(0.0, min(1.0, float(confidence)))
        card.extraction_method = method
    return cards


def _diagnostic_cards(evaluation: DiagnosticEvaluation) -> list[EvidenceCard]:
    timestamp = datetime.now(UTC)
    cards: list[EvidenceCard] = []
    for index, item in enumerate(evaluation.items, start=1):
        cards.append(
            _card(
                "knowledge_base",
                "诊断小测",
                item.evidence or f"诊断第{index}题{'答对' if item.correct else '答错'}",
                {
                    "question_id": item.question_id,
                    "correct": item.correct,
                    "topic": item.topic,
                    "skill": item.skill,
                },
                0.8,
                timestamp,
            )
        )
        if not item.correct:
            weak_label = item.weak_label or item.topic
            cards.append(
                _card(
                    "weak_points",
                    "诊断小测",
                    f"{weak_label}出现错误",
                    {
                        "label": weak_label,
                        "topic": item.topic,
                        "knowledge_point": item.skill,
                        "correct": False,
                    },
                    0.8,
                    timestamp,
                )
            )
    return cards


def _profile_from_scoring_results(
    *,
    user_id: str,
    scoring: dict[DimensionKey, ScoringResult],
    evidence_cards: list[EvidenceCard],
) -> Profile:
    profile = create_blank_profile(user_id)
    for dimension in DIMENSION_LABELS:
        result = scoring[dimension]
        cards = _cards_for_dimension(evidence_cards, dimension)
        for card in cards:
            card.objective_signal = {**card.objective_signal, "scoring_provenance": result.provenance}
        target = getattr(profile, dimension)
        target.confidence = result.confidence
        target.evidence = cards
        target.narrative = result.narrative
        if hasattr(target, "score") and result.aggregated_score is not None:
            target.score = result.aggregated_score
        if hasattr(target, "label") and result.label is not None:
            target.label = result.label
        if hasattr(target, "distribution") and result.distribution:
            target.distribution = result.distribution
        if dimension == "weak_points":
            target.items = _weak_points_from_cards(cards)
    return profile


def _fallback_dialogue_extraction(text: str) -> DialogueExtraction:
    learning_goal_label, learning_goal_evidence = _goal_signal_from_text(text)
    if learning_goal_label is None:
        learning_goal_label, learning_goal_evidence = _other_learning_goal_from_text(text)
    style, style_evidence = _first_match(
        text,
        [
            ("视觉型", ("图谱", "图", "表")),
            ("记忆(口诀)型", ("口诀", "歌诀")),
        ],
    )
    motivation, motivation_evidence = _first_match(
        text,
        [
            ("被动", ("佛系", "被动", "随便")),
            ("外驱", ("考试", "考研")),
        ],
    )
    major, major_evidence = _first_match(
        text,
        [
            ("中药学", ("中药学",)),
            ("中医学", ("中医学",)),
        ],
    )
    base_level, base_level_evidence = _base_level_from_text(text)
    resource_preference, resource_evidence = _resource_from_text(text)
    daily_minutes, daily_evidence = _daily_minutes_from_text(text)
    target_days, target_evidence = _target_days_from_text(text)
    weak_labels = _weak_points_from_dialogue_text(text)
    weak_value: str | dict[str, float] | None = None
    if len(weak_labels) == 1:
        weak_value = weak_labels[0]
    elif weak_labels:
        weak_value = {label: 1.0 for label in weak_labels}
    return _validate_dialogue_evidence(
        DialogueExtraction(
            major=_signal(major, 0.7 if major else 0.0, "explicit", major_evidence),
            learning_goal=_signal(learning_goal_label, 0.75 if learning_goal_label else 0.0, "explicit", learning_goal_evidence),
            base_level=_signal(base_level, 0.8 if base_level else 0.0, "explicit", base_level_evidence),
            learning_style=_signal(style, 0.7 if style else 0.0, "explicit", style_evidence),
            weak_points=_signal(weak_value, 0.85 if weak_labels else 0.0, "explicit", weak_labels),
            motivation=_signal(motivation, 0.65 if motivation else 0.0, "inferred", motivation_evidence),
            resource_preference=_signal(resource_preference, 0.7 if resource_preference else 0.0, "explicit", resource_evidence),
            daily_minutes=_number_signal(daily_minutes, 0.8 if daily_minutes is not None else 0.0, "explicit", daily_evidence),
            target_days=_number_signal(target_days, 0.8 if target_days is not None else 0.0, "explicit", target_evidence),
            extraction_method="no_key_rule_fallback",
        ),
        text,
    )


def _weak_points_from_dialogue_text(text: str) -> list[str]:
    issue_keywords = (
        "记不住",
        "背不下来",
        "分不清",
        "搞不清",
        "不确定",
        "卡住",
        "薄弱",
        "易错",
        "老错",
        "常错",
        "总错",
        "混淆",
        "困惑",
    )
    labels: list[str] = []
    for raw_clause in re.split(r"[，。！？；,.!?;]\s*", text):
        clause = raw_clause.strip()
        if not clause or not any(keyword in clause for keyword in issue_keywords):
            continue
        snippet = re.sub(r"^(我|自己|就是|还是|也|但|不过|最近|现在|目前|学习中药时)", "", clause).strip()
        if not snippet:
            snippet = clause
        if len(snippet) > 28:
            match = re.search(r"[^，。！？；,.!?;]{0,14}(?:记不住|背不下来|分不清|搞不清|不确定|卡住|薄弱|易错|老错|常错|总错|混淆|困惑)[^，。！？；,.!?;]{0,14}", snippet)
            snippet = match.group(0).strip() if match else snippet[:28].strip()
        if snippet and snippet in text and snippet not in labels:
            labels.append(snippet)
        if len(labels) >= 3:
            break
    return labels


def _other_learning_goal_from_text(text: str) -> tuple[str | None, list[str]]:
    """Return the traceable fallback bucket for explicit non-canonical learning targets."""

    goal_markers = ("目标", "想", "希望", "为了", "打算", "准备", "用来", "先学")
    topic_markers = ("学", "中医", "中药", "药", "方", "调理", "辨证")
    # Assumption: the fallback bucket is schema-level normalization only. It does
    # not change scoring semantics; every emitted evidence snippet must still be
    # a source-text substring and pass _validate_dialogue_evidence.
    for raw_clause in re.split(r"[，。！？；,.!?;]\s*", text):
        clause = raw_clause.strip()
        if not clause:
            continue
        if not any(marker in clause for marker in goal_markers):
            continue
        if not any(marker in clause for marker in topic_markers):
            continue
        if "先聊聊养生" in clause and "学" not in clause:
            continue
        snippet = clause[:28].strip()
        if snippet and snippet in text:
            return "其他", [snippet]
    return None, []


def _fallback_diagnostic_evaluation(
    questions: list[dict[str, str]],
    answers: dict[str, str],
) -> DiagnosticEvaluation:
    items: list[DiagnosticItemEvaluation] = []
    for question in questions:
        qid = str(question.get("id", ""))
        expected = str(question.get("answer", ""))
        actual = str(answers.get(qid, ""))
        correct = bool(expected and expected in actual)
        topic = str(question.get("topic") or "诊断题")
        skill = str(question.get("skill") or "")
        items.append(
            DiagnosticItemEvaluation(
                question_id=qid,
                correct=correct,
                topic=topic,
                skill=skill,
                weak_label=None if correct else topic,
                evidence=f"{topic}{'作答正确' if correct else '作答错误'}",
            )
        )
    return DiagnosticEvaluation(items=items, summary="本地规则完成诊断评估")


def _cards_for_dimension(cards: list[EvidenceCard], dimension: DimensionKey) -> list[EvidenceCard]:
    return [card for card in cards if card.dimension == dimension]


def _weak_points_from_cards(cards: list[EvidenceCard]) -> list[WeakPoint]:
    counter: Counter[str] = Counter()
    for card in cards:
        signal = card.objective_signal
        for key in ("label", "topic", "knowledge_point"):
            value = signal.get(key)
            if isinstance(value, str) and value.strip():
                counter[value.strip()] += 1
        weak_labels = signal.get("weak_labels")
        if isinstance(weak_labels, list):
            for label in weak_labels:
                if isinstance(label, str) and label.strip():
                    counter[label.strip()] += 1
    return [WeakPoint(label=label, strength=strength) for label, strength in counter.most_common()]


def _add_label_card(
    cards: list[EvidenceCard],
    dimension: DimensionKey,
    label_name: str,
    signal: ExtractedSignal,
    timestamp: datetime,
) -> None:
    label = _signal_label(signal)
    distribution = _signal_distribution(signal)
    if label or distribution:
        cards.append(
            _card(
                dimension,
                "冷启动对话",
                _fact(label_name, label or "倾向分布", signal.evidence),
                {
                    "label": label,
                    "distribution": distribution,
                    "evidence": signal.evidence,
                    "confidence": signal.confidence,
                    "basis": signal.basis,
                },
                signal.confidence,
                timestamp,
            )
        )


def _signal(
    value: SignalValue,
    confidence: float,
    basis: EvidenceBasis,
    evidence: str | list[str] | None,
) -> ExtractedSignal:
    return ExtractedSignal(
        value=value,
        confidence=confidence,
        basis=basis,
        evidence=_evidence_list(evidence),
        extraction_method="no_key_rule_fallback",
    )


def _number_signal(
    value: float | int | None,
    confidence: float,
    basis: EvidenceBasis,
    evidence: str | list[str] | None,
) -> ExtractedNumber:
    return ExtractedNumber(
        value=float(value) if value is not None else None,
        confidence=confidence,
        basis=basis,
        evidence=_evidence_list(evidence),
        extraction_method="no_key_rule_fallback",
    )


def _evidence_list(evidence: str | list[str] | None) -> list[str]:
    if evidence is None:
        return []
    if isinstance(evidence, str):
        return [evidence] if evidence else []
    return evidence


def _restrict_signal(signal: ExtractedSignal, allowed_labels: tuple[str, ...]) -> None:
    allowed = set(allowed_labels)
    if isinstance(signal.value, str):
        if signal.value not in allowed:
            signal.value = None
            signal.confidence = 0.0
            signal.evidence = []
        return
    if isinstance(signal.value, dict):
        signal.value = {label: weight for label, weight in signal.value.items() if label in allowed}
        if not signal.value:
            signal.value = None
            signal.confidence = 0.0
            signal.evidence = []


def _validate_dialogue_evidence(extraction: DialogueExtraction, source_text: str) -> DialogueExtraction:
    """Drop extracted conclusions whose evidence cannot be traced to the dialogue text."""

    for signal in _iter_extracted_signals(extraction):
        if signal.value is None:
            signal.evidence = []
            signal.confidence = 0.0
            continue
        evidence = [snippet for snippet in signal.evidence if snippet and snippet in source_text]
        if not evidence or len(evidence) != len(signal.evidence):
            signal.value = None
            signal.evidence = []
            signal.confidence = 0.0
        else:
            signal.evidence = evidence
    for signal in (extraction.daily_minutes, extraction.target_days, extraction.motivation_score, extraction.mastery_hint):
        if signal.value is None:
            signal.evidence = []
            signal.confidence = 0.0
            continue
        evidence = [snippet for snippet in signal.evidence if snippet and snippet in source_text]
        if not evidence or len(evidence) != len(signal.evidence):
            signal.value = None
            signal.evidence = []
            signal.confidence = 0.0
        else:
            signal.evidence = evidence
    return extraction


def _iter_extracted_signals(extraction: DialogueExtraction) -> tuple[ExtractedSignal, ...]:
    return (
        extraction.major,
        extraction.learning_goal,
        extraction.base_level,
        extraction.learning_style,
        extraction.weak_points,
        extraction.motivation,
        extraction.resource_preference,
    )


def _signal_label(signal: ExtractedSignal) -> str | None:
    if isinstance(signal.value, str) and signal.value.strip():
        return signal.value.strip()
    if isinstance(signal.value, dict) and signal.value:
        return max(signal.value.items(), key=lambda item: item[1])[0]
    return None


def _signal_distribution(signal: ExtractedSignal) -> dict[str, float] | None:
    if isinstance(signal.value, dict) and signal.value:
        return dict(signal.value)
    return None


def _format_number(value: float | int | None) -> str | None:
    if value is None:
        return None
    numeric = float(value)
    return str(int(numeric)) if numeric.is_integer() else str(round(numeric, 2))


def _int_if_whole(value: float | int | None) -> float | int | None:
    if value is None:
        return None
    numeric = float(value)
    return int(numeric) if numeric.is_integer() else numeric


def _card(
    dimension: DimensionKey,
    source: str,
    fact: str,
    objective_signal: dict[str, Any],
    weight: float,
    timestamp: datetime,
) -> EvidenceCard:
    signal = {key: value for key, value in objective_signal.items() if value is not None}
    evidence = signal.get("evidence")
    quote = signal.get("quote") or (evidence[0] if isinstance(evidence, list) and evidence else evidence if isinstance(evidence, str) else None)
    basis = signal.get("basis") if signal.get("basis") in {"explicit", "inferred"} else "inferred"
    confidence = signal.get("confidence", weight)
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
        timestamp=timestamp,
    )


def _json_object(raw: str) -> dict[str, Any]:
    stripped = raw.strip()
    if stripped.startswith("[MOCK:"):
        raise ValueError("mock response is not structured JSON")
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.I).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    match = re.search(r"\{.*\}", stripped, flags=re.S)
    text = match.group(0) if match else stripped
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("structured output must be a JSON object")
    return parsed


def _fact(name: str, value: str | None, evidence: list[str] | None) -> str:
    suffix = f"；证据：{'、'.join(evidence)}" if evidence else ""
    return f"{name}：{value or '未明'}{suffix}"


def _first_match(text: str, options: list[tuple[str, tuple[str, ...]]]) -> tuple[str | None, str | None]:
    for label, keywords in options:
        keyword = _first_keyword(text, keywords)
        if keyword:
            return label, keyword
    return None, None


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _first_keyword(text: str, keywords: tuple[str, ...]) -> str | None:
    for keyword in keywords:
        start = text.find(keyword)
        while start >= 0:
            if not _is_negated(text, start):
                return keyword
            start = text.find(keyword, start + len(keyword))
    return None


def _is_negated(text: str, start: int) -> bool:
    clause_start = max((text.rfind(mark, 0, start) for mark in "，。！？；,.!?;"), default=-1) + 1
    prefix = text[clause_start:start].strip()
    return bool(re.search(r"(?:不是|并非|不想|不为|不准备|没有|没打算|无意|非|不)\s*(?:为了|为|想|要|准备|打算)?\s*$", prefix))


def _goal_signal_from_text(text: str) -> tuple[str | dict[str, float] | None, list[str]]:
    options = [
        ("应试提分", ("考试", "期末", "考证", "exam", "exams", "test", "tests")),
        ("学术深造", ("考研", "论文", "科研")),
        ("临床实用", ("临床", "坐诊", "辨证")),
        ("兴趣科普", ("兴趣", "喜欢", "了解")),
    ]
    matches: list[tuple[str, str]] = []
    for label, keywords in options:
        keyword = _first_keyword(text, keywords)
        if keyword and (label, keyword) not in matches:
            matches.append((label, keyword))
    if len(matches) == 1:
        return matches[0][0], [matches[0][1]]
    if len(matches) > 1:
        return {label: 1.0 for label, _ in matches}, [keyword for _, keyword in matches]
    return _other_learning_goal_from_text(text)


def _add_evidence(evidence: dict[str, list[str]], key: str, snippet: str | None) -> None:
    if snippet:
        evidence[key] = [snippet]


def _base_level_from_text(text: str) -> tuple[str | None, str | None]:
    return _first_match(
        text,
        [
            ("零基础", ("零基础", "没接触", "没有接触", "完全不知道", "刚开始", "basic", "basics", "starting")),
            ("入门", ("入门", "初学")),
            ("一般", ("薄弱", "一般")),
            ("较好", ("较好", "还可以", "不错")),
            ("熟练", ("熟练", "很熟", "掌握得好")),
        ],
    )


def _resource_from_text(text: str) -> tuple[str | None, str | None]:
    return _first_match(
        text,
        [
            ("拟人化短剧", ("视频", "短剧")),
            ("汤头歌诀", ("口诀", "歌诀")),
            ("医案文选", ("医案", "案例")),
            ("知识图谱", ("知识图谱", "图谱")),
        ],
    )


def _daily_minutes_from_text(text: str) -> tuple[int | None, str | None]:
    if "半小时" in text:
        return 30, "半小时"
    if "一小时" in text or "1小时" in text:
        return 60, "一小时" if "一小时" in text else "1小时"
    match = re.search(r"(\d{1,3})\s*(?:分钟|min)", text, flags=re.I)
    return (int(match.group(1)), match.group(0)) if match else (None, None)


def _target_days_from_text(text: str) -> tuple[int | None, str | None]:
    if "一周" in text or "1周" in text:
        return 7, "一周" if "一周" in text else "1周"
    if "两周" in text or "2周" in text:
        return 14, "两周" if "两周" in text else "2周"
    if "一个月" in text or "1个月" in text:
        return 30, "一个月" if "一个月" in text else "1个月"
    match = re.search(r"(\d{1,3})\s*(?:天|日)", text)
    if match:
        return int(match.group(1)), match.group(0)
    match = re.search(r"(\d{1,2})\s*(?:周|星期)", text)
    return (int(match.group(1)) * 7, match.group(0)) if match else (None, None)
