"""ProfileAgent 的 LangGraph 节点。"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict, cast

from pydantic import BaseModel, Field, ValidationError

from app.llm.clients import ChatMessage
from app.llm.router import ModelRouter
from app.profile.jury import ScoringResult, score_profile
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


class DialogueExtraction(BaseModel):
    """Structured LLM output consumed by dialogue_extract_node."""

    major: str | None = None
    learning_goal: str | None = None
    base_level: str | None = None
    learning_style: str | None = None
    motivation_label: str | None = None
    motivation_score: float | None = Field(default=None, ge=0, le=100)
    resource_preference: str | None = None
    daily_minutes: int | None = Field(default=None, ge=0)
    target_days: int | None = Field(default=None, ge=0)
    evidence: dict[str, list[str]] = Field(default_factory=dict)


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

    text = last_user_content(state["messages"])
    extraction = await _extract_dialogue_structured(text)
    cards = _dialogue_cards(extraction)

    next_state = copy_state(state)
    next_state["dialogue_features"] = extraction.model_dump(mode="json")
    next_state["evidence_cards"].extend(cards)
    summary = extraction.learning_goal or extraction.base_level or "待补充"
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


async def _extract_dialogue_structured(text: str) -> DialogueExtraction:
    router = ModelRouter()
    messages: list[ChatMessage] = [
        {
            "role": "system",
            "content": (
                "你是学习画像信息抽取器。只输出 JSON，不要 Markdown。"
                "从用户首轮话语抽取专业、学习目标、基础、学习偏好，并给 evidence。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"用户话语：{text}\n"
                "输出字段：major, learning_goal(应试提分/临床实用/兴趣科普/学术深造), "
                "base_level, learning_style(视觉型/文字型/记忆(口诀)型), "
                "motivation_label(内驱/外驱/内外混合/被动), motivation_score, "
                "resource_preference(知识图谱/拟人化短剧/汤头歌诀/医案文选), "
                "daily_minutes, target_days, evidence。"
            ),
        },
    ]
    raw = await router.chat("profile_extract", messages, response_format={"type": "json_object"}, temperature=0)
    try:
        return DialogueExtraction.model_validate(_json_object(raw))
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


def _dialogue_cards(extraction: DialogueExtraction) -> list[EvidenceCard]:
    timestamp = datetime.now(UTC)
    cards: list[EvidenceCard] = []
    if extraction.base_level or extraction.major:
        cards.append(
            _card(
                "knowledge_base",
                "冷启动对话",
                _fact("基础自述", extraction.base_level or extraction.major, extraction.evidence.get("base_level")),
                {
                    "prior": True,
                    "label": extraction.base_level,
                    "major": extraction.major,
                    "evidence": extraction.evidence.get("base_level", []),
                },
                0.3,
                timestamp,
            )
        )
    _add_label_card(cards, "learning_goal", "学习目标", extraction.learning_goal, extraction.evidence, timestamp)
    _add_label_card(cards, "learning_style", "学习风格", extraction.learning_style, extraction.evidence, timestamp)
    _add_label_card(cards, "resource_preference", "资源偏好", extraction.resource_preference, extraction.evidence, timestamp)
    if extraction.motivation_label or extraction.motivation_score is not None:
        cards.append(
            _card(
                "motivation",
                "冷启动对话",
                _fact("动机线索", extraction.motivation_label, extraction.evidence.get("motivation_label")),
                {
                    "label": extraction.motivation_label,
                    "motivation_score": extraction.motivation_score,
                    "evidence": extraction.evidence.get("motivation_label", []),
                },
                1.0,
                timestamp,
            )
        )
    if extraction.daily_minutes is not None or extraction.target_days is not None:
        cards.append(
            _card(
                "pace",
                "冷启动对话",
                "学习节奏线索",
                {
                    "daily_minutes": extraction.daily_minutes,
                    "target_days": extraction.target_days,
                    "evidence": extraction.evidence.get("pace", []),
                },
                1.0,
                timestamp,
            )
        )
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
    learning_goal_label = _first_match(
        text,
        [
            ("应试提分", ("考试", "期末", "考证")),
            ("学术深造", ("考研", "论文", "科研")),
            ("临床实用", ("临床", "坐诊", "辨证")),
            ("兴趣科普", ("兴趣", "喜欢", "了解")),
        ],
        default="学术深造",
    )
    style = _first_match(
        text,
        [
            ("视觉型", ("图", "表", "图谱")),
            ("记忆(口诀)型", ("口诀", "歌诀")),
            ("文字型", ("文章", "阅读", "文字")),
        ],
        default="文字型",
    )
    motivation = "被动" if _contains_any(text, ("佛系", "被动", "随便")) else ("外驱" if _contains_any(text, ("考试", "考研")) else "内驱")
    evidence: dict[str, list[str]] = {
        "learning_goal": [learning_goal_label],
        "learning_style": [style],
        "motivation_label": [motivation],
    }
    return DialogueExtraction(
        major="中药学" if "中药学" in text else ("中医学" if "中医学" in text else "中医药相关专业"),
        learning_goal=learning_goal_label,
        base_level=_base_level_from_text(text),
        learning_style=style,
        motivation_label=motivation,
        resource_preference=_resource_from_text(text),
        daily_minutes=_daily_minutes_from_text(text),
        target_days=_target_days_from_text(text),
        evidence=evidence,
    )


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
    label: str | None,
    evidence: dict[str, list[str]],
    timestamp: datetime,
) -> None:
    if label and label.strip():
        cards.append(
            _card(
                dimension,
                "冷启动对话",
                _fact(label_name, label.strip(), evidence.get(dimension) or evidence.get(label_name)),
                {"label": label.strip(), "evidence": evidence.get(dimension, [])},
                1.0,
                timestamp,
            )
        )


def _card(
    dimension: DimensionKey,
    source: str,
    fact: str,
    objective_signal: dict[str, Any],
    weight: float,
    timestamp: datetime,
) -> EvidenceCard:
    return EvidenceCard(
        dimension=dimension,
        source=source,
        fact=fact,
        objective_signal={key: value for key, value in objective_signal.items() if value is not None},
        weight=weight,
        timestamp=timestamp,
    )


def _json_object(raw: str) -> dict[str, Any]:
    stripped = raw.strip()
    if stripped.startswith("[MOCK:"):
        raise ValueError("mock response is not structured JSON")
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
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


def _first_match(text: str, options: list[tuple[str, tuple[str, ...]]], *, default: str) -> str:
    for label, keywords in options:
        if _contains_any(text, keywords):
            return label
    return default


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _base_level_from_text(text: str) -> str:
    if _contains_any(text, ("零基础", "刚开始")):
        return "零基础"
    if _contains_any(text, ("薄弱", "一般")):
        return "一般"
    if _contains_any(text, ("较好", "还可以", "不错")):
        return "较好"
    return "入门"


def _resource_from_text(text: str) -> str:
    if _contains_any(text, ("视频", "短剧")):
        return "拟人化短剧"
    if _contains_any(text, ("口诀", "歌诀")):
        return "汤头歌诀"
    if _contains_any(text, ("医案", "案例")):
        return "医案文选"
    return "知识图谱"


def _daily_minutes_from_text(text: str) -> int | None:
    if "半小时" in text:
        return 30
    if "一小时" in text or "1小时" in text:
        return 60
    match = re.search(r"(\d{1,3})\s*(?:分钟|min)", text, flags=re.I)
    return int(match.group(1)) if match else None


def _target_days_from_text(text: str) -> int | None:
    if "一周" in text or "1周" in text:
        return 7
    if "两周" in text or "2周" in text:
        return 14
    if "一个月" in text or "1个月" in text:
        return 30
    match = re.search(r"(\d{1,3})\s*(?:天|日)", text)
    if match:
        return int(match.group(1))
    match = re.search(r"(\d{1,2})\s*(?:周|星期)", text)
    return int(match.group(1)) * 7 if match else None
