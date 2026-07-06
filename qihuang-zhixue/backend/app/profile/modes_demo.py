"""Profile collection modes that emit objective EvidenceCard records only."""

from __future__ import annotations

import asyncio
import json
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import get_settings
from app.llm.router import ModelRouter
from app.profile.schema import DimensionKey, EvidenceCard, evidence_card_to_json_dict
from app.profile.store import DB_PATH, add_evidence

SHANGHAI_TZ = timezone(timedelta(hours=8))


class ProfileMode(str, Enum):
    """Supported collection modes."""

    apprentice = "apprentice"
    quickcard = "quickcard"
    practice = "practice"
    passive = "passive"


APPRENTICE_STAGES = (
    "序幕·叩问初心",
    "一幕·药圃初见",
    "二幕·初次抓药",
    "三幕·师父荐宝",
    "四幕·立下学规",
)

IDENTITY_CARDS: dict[str, dict[str, Any]] = {
    "考前冲刺": {
        "goal": "应试提分",
        "motivation": "外驱",
        "knowledge_prior": "基础自评偏应试，需真实题目稀释",
    },
    "临床学徒": {
        "goal": "临床实用",
        "motivation": "内驱",
        "knowledge_prior": "以病例应用为目标的基础先验",
    },
    "草木好奇者": {
        "goal": "兴趣科普",
        "motivation": "内驱",
        "knowledge_prior": "兴趣驱动冷启动先验",
    },
    "研读派": {
        "goal": "学术深造",
        "motivation": "内外混合",
        "knowledge_prior": "偏文献研读的基础先验",
    },
}

STYLE_BY_CLUE = {
    "图谱": "视觉型",
    "图片": "视觉型",
    "药图": "视觉型",
    "文字": "文字型",
    "条文": "文字型",
    "歌诀": "记忆(口诀)型",
    "口诀": "记忆(口诀)型",
}

RESOURCE_TYPES = {"知识图谱", "拟人化短剧", "汤头歌诀", "医案文选"}
TOXIC_OR_FIERCE_HERBS = {"附子", "川乌", "草乌", "马钱子", "巴豆", "雄黄", "朱砂"}
COMPATIBLE_PAIRS = (
    ("麻黄", "桂枝"),
    ("桂枝", "白芍"),
    ("金银花", "连翘"),
    ("黄芩", "黄连"),
    ("半夏", "陈皮"),
    ("黄芪", "白术"),
)


@dataclass
class ProfileSession:
    """In-memory demo session state for one collection run."""

    session_id: str
    user_id: str
    mode: ProfileMode
    stage_index: int = 0
    evidence_cards: list[EvidenceCard] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(SHANGHAI_TZ))
    updated_at: datetime = field(default_factory=lambda: datetime.now(SHANGHAI_TZ))
    finished: bool = False
    persisted: bool = False
    persisted_db_path: str | None = None

    @property
    def current_stage(self) -> str | None:
        if self.mode == ProfileMode.apprentice and self.stage_index < len(APPRENTICE_STAGES):
            return APPRENTICE_STAGES[self.stage_index]
        if self.mode == ProfileMode.quickcard and not self.finished:
            return "身份卡+30秒速判"
        if self.mode == ProfileMode.practice and not self.finished:
            return "开药实战"
        if self.mode == ProfileMode.passive and not self.finished:
            return "纯被动埋点"
        return None


_SESSIONS: dict[str, ProfileSession] = {}


def start_profile_session(user_id: str, mode: ProfileMode | str) -> ProfileSession:
    """Start a collection session without creating any score."""

    parsed_mode = ProfileMode(mode)
    session = ProfileSession(session_id=uuid4().hex, user_id=user_id, mode=parsed_mode)
    _SESSIONS[session.session_id] = session
    return session


async def answer_profile_session(session_id: str, payload: dict[str, Any]) -> tuple[ProfileSession, list[EvidenceCard]]:
    """Advance a session by converting the submitted behavior into cards."""

    session = get_profile_session(session_id)
    if session.finished:
        return session, []

    response_time_ms = _response_time_ms(payload)
    if session.mode == ProfileMode.apprentice:
        cards = await _answer_apprentice(session, payload, response_time_ms)
    elif session.mode == ProfileMode.quickcard:
        cards = _quickcard_cards(payload, response_time_ms)
        session.finished = True
    elif session.mode == ProfileMode.practice:
        cards = [await _prescription_card(payload, source="开药实战", weight=1.5, response_time_ms=response_time_ms)]
    elif session.mode == ProfileMode.passive:
        cards = _passive_cards(payload, response_time_ms)
    else:
        cards = []

    session.evidence_cards.extend(cards)
    session.updated_at = datetime.now(SHANGHAI_TZ)
    return session, cards


def finish_profile_session(session_id: str, db_path: Path = DB_PATH) -> ProfileSession:
    """Mark a session finished and persist cards to the evidence table once."""

    session = get_profile_session(session_id)
    session.finished = True
    session.updated_at = datetime.now(SHANGHAI_TZ)
    if not session.persisted:
        for card in session.evidence_cards:
            add_evidence(session.user_id, card, db_path=db_path)
        session.persisted = True
        session.persisted_db_path = str(db_path)
    return session


def get_profile_session(session_id: str) -> ProfileSession:
    """Return a session or raise KeyError for API-level 404 handling."""

    return _SESSIONS[session_id]


def reset_profile_sessions() -> None:
    """Clear demo sessions for tests."""

    _SESSIONS.clear()


async def _answer_apprentice(
    session: ProfileSession,
    payload: dict[str, Any],
    response_time_ms: int | None,
) -> list[EvidenceCard]:
    stage = session.current_stage
    if stage == "序幕·叩问初心":
        cards = _identity_cards(payload, source=stage, response_time_ms=response_time_ms, prior_weight=0.3)
    elif stage == "一幕·药圃初见":
        cards = _herb_quiz_cards(payload, source=stage, response_time_ms=response_time_ms)
    elif stage == "二幕·初次抓药":
        cards = [await _prescription_card(payload, source=stage, weight=1.5, response_time_ms=response_time_ms)]
    elif stage == "三幕·师父荐宝":
        cards = [_resource_card(payload, source=stage, response_time_ms=response_time_ms)]
    elif stage == "四幕·立下学规":
        cards = _learning_rule_cards(payload, source=stage, response_time_ms=response_time_ms)
    else:
        cards = []

    session.stage_index += 1
    if session.stage_index >= len(APPRENTICE_STAGES):
        session.finished = True
    return cards


def _identity_cards(
    payload: dict[str, Any],
    source: str,
    response_time_ms: int | None,
    prior_weight: float,
) -> list[EvidenceCard]:
    identity = str(payload.get("identity_card") or payload.get("choice") or "").strip()
    meta = IDENTITY_CARDS.get(identity, IDENTITY_CARDS["草木好奇者"])
    signal = {"identity_card": identity or "未指定", "response_time_ms": response_time_ms}
    return [
        _card(
            "learning_goal",
            source,
            f"选择身份卡“{identity or '未指定'}”，目标标签为{meta['goal']}",
            {**signal, "label": meta["goal"]},
            1.0,
        ),
        _card(
            "motivation",
            source,
            f"选择身份卡“{identity or '未指定'}”，动机标签为{meta['motivation']}",
            {**signal, "label": meta["motivation"]},
            prior_weight,
        ),
        _card(
            "knowledge_base",
            source,
            str(meta["knowledge_prior"]),
            {**signal, "prior": True},
            prior_weight,
        ),
    ]


def _herb_quiz_cards(payload: dict[str, Any], source: str, response_time_ms: int | None) -> list[EvidenceCard]:
    answers = _as_list(payload.get("answers") or payload.get("items"))
    if not answers:
        answers = [payload]

    cards: list[EvidenceCard] = []
    clue_votes: list[str] = []
    for index, item in enumerate(answers, start=1):
        if not isinstance(item, dict):
            continue
        difficulty = str(item.get("difficulty") or "easy")
        weight = 1.2 if difficulty in {"hard", "难", "difficult"} else 0.8
        gave_up = bool(item.get("gave_up") or item.get("abandoned"))
        if gave_up:
            weight = 0.6
        correct = bool(item.get("correct") or item.get("is_correct"))
        clue_type = str(item.get("clue_type") or item.get("clue") or "").strip()
        if clue_type:
            clue_votes.append(_style_from_clue(clue_type))
        item_rt = _response_time_ms(item, default=response_time_ms)
        fact = f"辨药第{index}题{'放弃请师父指点' if gave_up else ('答对' if correct else '答错')}"
        cards.append(
            _card(
                "knowledge_base",
                source,
                fact,
                {
                    "question_id": item.get("question_id") or item.get("id") or index,
                    "correct": correct,
                    "difficulty": difficulty,
                    "gave_up": gave_up,
                    "selected": item.get("selected"),
                    "answer": item.get("answer"),
                    "response_time_ms": item_rt,
                },
                weight,
            )
        )
        if not correct or gave_up:
            topic = str(item.get("topic") or item.get("knowledge_point") or "辨药").strip()
            cards.append(
                _card(
                    "weak_points",
                    source,
                    f"{topic}出现{'放弃' if gave_up else '错误'}",
                    {
                        "label": topic,
                        "question_id": item.get("question_id") or item.get("id") or index,
                        "correct": correct,
                        "gave_up": gave_up,
                        "response_time_ms": item_rt,
                    },
                    weight,
                )
            )

    if clue_votes:
        cards.append(
            _card(
                "learning_style",
                source,
                f"线索类型计票：{_count_labels(clue_votes)}",
                {"votes": _count_labels(clue_votes), "response_time_ms": response_time_ms},
                1.0,
            )
        )
    return cards


async def _prescription_card(
    payload: dict[str, Any],
    source: str,
    weight: float,
    response_time_ms: int | None,
) -> EvidenceCard:
    signal = await extract_prescription_signal(payload)
    weak_labels = _weak_labels_from_prescription_signal(signal)
    signal["response_time_ms"] = response_time_ms
    fact_parts = [
        f"对症{'是' if signal['对症'] else '否'}",
        f"配伍{'是' if signal['配伍'] else '否'}",
        f"剂量{'合适' if signal['剂量'] else '需核查'}",
        f"误用毒烈{'有' if signal['误用毒烈'] else '无'}",
    ]
    if weak_labels:
        signal["weak_labels"] = weak_labels
        fact_parts.append(f"薄弱标签：{'、'.join(weak_labels)}")
    return _card("knowledge_base", source, "；".join(fact_parts), signal, weight)


async def extract_prescription_signal(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract objective prescription signals, using LLM when configured and rules as fallback."""

    llm_signal = await _try_llm_prescription_extract(payload)
    if llm_signal is not None:
        return llm_signal
    return _rule_prescription_extract(payload)


async def _try_llm_prescription_extract(payload: dict[str, Any]) -> dict[str, Any] | None:
    settings = get_settings()
    if not (settings.deepseek_api_key or settings.zhipu_api_key):
        return None

    prompt = (
        "只输出 JSON，不要解释。请从中医开方答题中抽取 objective_signal，"
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
        parsed = _parse_json_object(raw)
        return _validate_prescription_signal(parsed)
    except Exception:
        return None


def _rule_prescription_extract(payload: dict[str, Any]) -> dict[str, Any]:
    text = _prescription_text(payload)
    herbs = _extract_herbs(payload, text)
    case_text = str(payload.get("case") or payload.get("symptoms") or payload.get("scenario") or "")
    dosage_values = _extract_dosages(payload, text)

    symptom_match = _symptom_matches(case_text, herbs)
    compatible = any(left in herbs and right in herbs for left, right in COMPATIBLE_PAIRS)
    if not compatible and len(herbs) >= 2:
        compatible = not _has_toxic_misuse(herbs, dosage_values)
    dosage_ok = bool(dosage_values) and all(1 <= value <= 30 for value in dosage_values)
    toxic_misuse = _has_toxic_misuse(herbs, dosage_values)
    return {"对症": symptom_match, "配伍": compatible, "剂量": dosage_ok, "误用毒烈": toxic_misuse}


def _resource_card(payload: dict[str, Any], source: str, response_time_ms: int | None) -> EvidenceCard:
    selected = str(payload.get("resource_type") or payload.get("choice") or "").strip()
    if selected not in RESOURCE_TYPES:
        selected = "知识图谱"
    return _card(
        "resource_preference",
        source,
        f"选择资源类型：{selected}",
        {"label": selected, "response_time_ms": response_time_ms},
        1.0,
    )


def _learning_rule_cards(payload: dict[str, Any], source: str, response_time_ms: int | None) -> list[EvidenceCard]:
    minutes = _int_or_none(payload.get("daily_minutes") or payload.get("minutes_per_day"))
    period_days = _int_or_none(payload.get("target_days") or payload.get("period_days"))
    challenge = bool(payload.get("accept_challenge") or payload.get("challenge"))
    signal = {
        "daily_minutes": minutes,
        "target_days": period_days,
        "accept_challenge": challenge,
        "response_time_ms": response_time_ms,
    }
    cards = [
        _card(
            "pace",
            source,
            f"约定每日{minutes if minutes is not None else '未填'}分钟，周期{period_days if period_days is not None else '未填'}天",
            signal,
            1.0,
        )
    ]
    if challenge:
        cards.append(_card("motivation", source, "主动接受挑战", signal, 1.0))
    return cards


def _quickcard_cards(payload: dict[str, Any], response_time_ms: int | None) -> list[EvidenceCard]:
    cards = _identity_cards(payload, source="身份卡+30秒速判", response_time_ms=response_time_ms, prior_weight=0.3)
    answer_cards = _herb_quiz_cards(payload, source="30秒速判", response_time_ms=response_time_ms)
    elapsed = _int_or_none(payload.get("elapsed_seconds"))
    if elapsed is not None:
        for card in answer_cards:
            card.objective_signal["elapsed_seconds"] = elapsed
    cards.extend(answer_cards)
    return cards


def _passive_cards(payload: dict[str, Any], response_time_ms: int | None) -> list[EvidenceCard]:
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
        item_rt = _response_time_ms(event, default=response_time_ms)
        dimension = _dimension_from_passive_event(event_type, resource_type)
        signal = {
            "event_type": event_type,
            "resource_type": resource_type or None,
            "topic": topic,
            "duration_ms": _int_or_none(event.get("duration_ms") or event.get("stay_ms")),
            "response_time_ms": item_rt,
            "sequence": index,
        }
        cards.append(_card(dimension, "纯被动埋点", f"{event_type}：{topic}", signal, 1.0))
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
) -> EvidenceCard:
    return EvidenceCard(
        dimension=dimension,
        source=source,
        fact=fact,
        objective_signal=objective_signal,
        weight=weight,
        timestamp=datetime.now(SHANGHAI_TZ),
    )


def _weak_labels_from_prescription_signal(signal: dict[str, Any]) -> list[str]:
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


def _response_time_ms(payload: dict[str, Any], default: int | None = None) -> int | None:
    return _int_or_none(payload.get("response_time_ms") or payload.get("reaction_time_ms")) or default


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


def _validate_prescription_signal(value: Any) -> dict[str, Any] | None:
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
    raw_herbs = payload.get("herbs") or payload.get("selected_herbs")
    herbs: set[str] = set()
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
        "白芍",
        "金银花",
        "连翘",
        "黄芩",
        "黄连",
        "石膏",
        "半夏",
        "陈皮",
        "黄芪",
        "白术",
        "附子",
        "川乌",
        "草乌",
        "马钱子",
        "巴豆",
        "雄黄",
        "朱砂",
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
                dosage = item.get("dose") or item.get("dosage") or item.get("grams")
                parsed = _int_or_none(dosage)
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
    if any(key in case_text for key in ("热", "咽痛", "口渴", "痈", "毒")):
        return bool({"金银花", "连翘", "黄芩", "黄连", "石膏"} & herbs)
    if any(key in case_text for key in ("气虚", "乏力", "自汗")):
        return bool({"黄芪", "白术"} & herbs)
    return bool(herbs)


def _has_toxic_misuse(herbs: set[str], dosage_values: list[int]) -> bool:
    has_toxic = bool(TOXIC_OR_FIERCE_HERBS & herbs)
    excessive = any(value > 9 for value in dosage_values)
    return has_toxic and excessive


def main() -> None:
    """Run a local four-mode evidence-card demo without touching the real DB."""

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(_run_demo())


async def _run_demo() -> None:
    reset_profile_sessions()
    with tempfile.TemporaryDirectory(prefix="qihuang-profile-demo-") as temp_dir:
        db_path = Path(temp_dir) / "profile_demo.db"
        demos = [
            await _demo_apprentice(db_path),
            await _demo_quickcard(db_path),
            await _demo_practice(db_path),
            await _demo_passive(db_path),
        ]

    for mode, cards in demos:
        print(f"===== mode: {mode} =====")
        print(json.dumps([evidence_card_to_json_dict(card) for card in cards], ensure_ascii=False, indent=2))
        print()


async def _demo_apprentice(db_path: Path) -> tuple[str, list[EvidenceCard]]:
    session = start_profile_session("demo-apprentice", ProfileMode.apprentice)
    apprentice_answers = [
        {"identity_card": "临床学徒", "response_time_ms": 1200},
        {
            "answers": [
                {
                    "question_id": "herb-1",
                    "correct": True,
                    "difficulty": "hard",
                    "clue_type": "药图",
                    "response_time_ms": 2600,
                },
                {
                    "question_id": "herb-2",
                    "correct": False,
                    "difficulty": "easy",
                    "topic": "清热药·剂量",
                    "selected": "黄芩",
                    "answer": "金银花",
                    "response_time_ms": 4200,
                },
            ]
        },
        {
            "case": "风寒表证，恶寒无汗",
            "herbs": [{"name": "麻黄", "dose": 6}, {"name": "桂枝", "dose": 6}],
            "response_time_ms": 9000,
        },
        {"resource_type": "医案文选", "response_time_ms": 1500},
        {"daily_minutes": 45, "target_days": 14, "accept_challenge": True, "response_time_ms": 1800},
    ]
    for answer in apprentice_answers:
        await answer_profile_session(session.session_id, answer)
    finished = finish_profile_session(session.session_id, db_path=db_path)
    return session.mode.value, finished.evidence_cards


async def _demo_quickcard(db_path: Path) -> tuple[str, list[EvidenceCard]]:
    session = start_profile_session("demo-quickcard", ProfileMode.quickcard)
    await answer_profile_session(
        session.session_id,
        {
            "identity_card": "考前冲刺",
            "elapsed_seconds": 28,
            "answers": [
                {
                    "question_id": "quick-1",
                    "correct": False,
                    "difficulty": "hard",
                    "topic": "解表药·配伍",
                    "response_time_ms": 28000,
                }
            ],
        },
    )
    finished = finish_profile_session(session.session_id, db_path=db_path)
    return session.mode.value, finished.evidence_cards


async def _demo_practice(db_path: Path) -> tuple[str, list[EvidenceCard]]:
    session = start_profile_session("demo-practice", ProfileMode.practice)
    await answer_profile_session(
        session.session_id,
        {
            "case": "热毒咽痛",
            "prescription": "金银花15g，连翘12g",
            "response_time_ms": 7000,
        },
    )
    finished = finish_profile_session(session.session_id, db_path=db_path)
    return session.mode.value, finished.evidence_cards


async def _demo_passive(db_path: Path) -> tuple[str, list[EvidenceCard]]:
    session = start_profile_session("demo-passive", ProfileMode.passive)
    await answer_profile_session(
        session.session_id,
        {
            "events": [
                {"event_type": "click", "resource_type": "知识图谱", "topic": "麻黄", "duration_ms": 2000},
                {"event_type": "skip", "topic": "剂量换算", "response_time_ms": 300},
                {"event_type": "replay", "resource_type": "汤头歌诀", "topic": "麻黄汤", "duration_ms": 5400},
            ]
        },
    )
    finished = finish_profile_session(session.session_id, db_path=db_path)
    return session.mode.value, finished.evidence_cards


__all__ = [
    "APPRENTICE_STAGES",
    "ProfileMode",
    "ProfileSession",
    "answer_profile_session",
    "extract_prescription_signal",
    "finish_profile_session",
    "get_profile_session",
    "main",
    "reset_profile_sessions",
    "start_profile_session",
]


if __name__ == "__main__":
    main()
