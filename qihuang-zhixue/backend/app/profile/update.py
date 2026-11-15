"""Incremental learning-profile updates and conditional jury rescoring."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Awaitable, Callable
from contextlib import closing
from datetime import UTC, datetime, timedelta, timezone
from math import isfinite
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.llm.router import ModelRouter
from app.profile.component_model import COMPONENTS, COMPONENT_WEIGHTS, score_interval
from app.profile.fusion import FusionResult, fuse_dimension, fuse_profile
from app.profile.jury import LLMCall, ScoringResult, score_dimension
from app.profile.prescription import evaluate_prescription
from app.profile.radar import to_radar
from app.profile.schema import DIMENSION_LABELS, DimensionKey, EvidenceCard, EvidenceTier, Profile, WeakPoint
from app.profile.evidence import weak_point_label_of
from app.profile.store import (
    DB_PATH,
    add_evidence,
    connect,
    get_profile,
    get_trust,
    init_db,
    list_evidence,
    record_profile_change,
    upsert_profile,
)

SHANGHAI_TZ = timezone(timedelta(hours=8))
DEFAULT_JURY_WEIGHT_THRESHOLD = 2.0
DEFAULT_JURY_RESCORE_INTERVAL = timedelta(hours=24)

ProfileEventType = Literal["quiz", "prescription", "interaction"]
JuryScorer = Callable[..., Awaitable[ScoringResult]]


class ProfileUpdateEvent(BaseModel):
    """Behavior event accepted by the update hook."""

    type: ProfileEventType
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime | None = None


class ProfileUpdateResult(BaseModel):
    """Incremental update result for tests and callers."""

    user_id: str
    evidence_cards: list[EvidenceCard]
    affected_dimensions: list[DimensionKey]
    jury_called_dimensions: list[DimensionKey]
    profile: Profile

    @property
    def jury_call_count(self) -> int:
        """Number of dimensions rescored through the jury."""

        return len(self.jury_called_dimensions)


async def update_profile_from_event(
    user_id: str,
    event: ProfileUpdateEvent | dict[str, Any],
    *,
    db_path: Path = DB_PATH,
    jury_weight_threshold: float = DEFAULT_JURY_WEIGHT_THRESHOLD,
    jury_scorer: JuryScorer = score_dimension,
    llm_call: LLMCall | None = None,
    router: ModelRouter | None = None,
    settings: Settings | None = None,
    force_llm: bool = False,
    now: datetime | None = None,
) -> ProfileUpdateResult:
    """Convert an event to evidence cards and incrementally update dimensions."""

    active_settings = settings or get_settings()
    parsed = event if isinstance(event, ProfileUpdateEvent) else ProfileUpdateEvent.model_validate(event)
    cards = evidence_cards_from_event(parsed, now=now)
    for card in cards:
        add_evidence(user_id, card, db_path=db_path)

    affected = _ordered_dimensions(card.dimension for card in cards)
    all_cards = list_evidence(user_id, db_path=db_path)
    profile = get_profile(user_id, db_path=db_path)
    trust = get_trust(user_id, db_path=db_path)
    jury_called: list[DimensionKey] = []
    _init_update_state(db_path)

    for dimension in affected:
        dimension_cards = [card for card in all_cards if card.dimension == dimension]
        fusion = fuse_dimension(dimension, dimension_cards, now=now, self_report_trust=trust)
        first_evaluation = not _dimension_has_result(profile, dimension) and _fusion_has_result(fusion)
        _apply_fusion_to_profile(
            profile,
            dimension,
            fusion,
            dimension_cards,
            db_path=db_path,
            reason="规则融合更新",
            source_event=parsed.type,
            provenance="fusion" if _has_configured_llm_key(active_settings) else "no_key_rule_fallback",
        )
        pending_weight = _increment_pending_weight(user_id, dimension, _new_weight(cards, dimension), db_path)
        mandatory_first_jury = (
            first_evaluation
            and _has_configured_llm_key(active_settings)
        )
        if mandatory_first_jury or pending_weight >= jury_weight_threshold:
            result = await jury_scorer(
                dimension,
                dimension_cards,
                router=router,
                settings=active_settings,
                force_llm=force_llm,
                llm_call=llm_call,
            )
            _apply_scoring_to_profile(
                profile,
                dimension,
                result,
                dimension_cards,
                db_path=db_path,
                source_event=parsed.type,
            )
            _reset_pending_weight(user_id, dimension, db_path, now=now)
            jury_called.append(dimension)

    upsert_profile(profile, db_path=db_path)
    return ProfileUpdateResult(
        user_id=user_id,
        evidence_cards=cards,
        affected_dimensions=affected,
        jury_called_dimensions=jury_called,
        profile=profile,
    )


async def get_latest_profile(
    user_id: str,
    *,
    db_path: Path = DB_PATH,
    jury_scorer: JuryScorer = score_dimension,
    llm_call: LLMCall | None = None,
    router: ModelRouter | None = None,
    settings: Settings | None = None,
    force_llm: bool = False,
    now: datetime | None = None,
) -> Profile:
    """Return the current profile, applying decay and user-view jury refresh."""

    active_settings = settings or get_settings()
    cards = list_evidence(user_id, db_path=db_path)
    profile = get_profile(user_id, db_path=db_path)
    _init_update_state(db_path)
    if not cards:
        return profile

    dimensions_with_evidence = _dimensions_with_evidence(cards)
    mandatory_first_jury = {
        dimension
        for dimension in dimensions_with_evidence
        if not _dimension_has_result(profile, dimension) and _has_configured_llm_key(active_settings)
    }
    throttled_dimensions = {
        dimension
        for dimension in dimensions_with_evidence
        if dimension not in mandatory_first_jury
        and _should_throttle_jury_refresh(
            user_id,
            dimension,
            db_path,
            settings=active_settings,
            force_llm=force_llm,
            llm_call=llm_call,
            now=now,
        )
    }

    trust = get_trust(user_id, db_path=db_path)
    fused = fuse_profile(cards, now=now, self_report_trust=trust)
    for dimension, fusion in fused.items():
        if dimension in throttled_dimensions:
            continue
        dimension_cards = [card for card in cards if card.dimension == dimension]
        _apply_fusion_to_profile(
            profile,
            dimension,
            fusion,
            dimension_cards,
            db_path=db_path,
            reason="规则融合刷新",
            source_event="profile_read",
            provenance="fusion" if _has_configured_llm_key(active_settings) else "no_key_rule_fallback",
        )

    for dimension in dimensions_with_evidence:
        if dimension in throttled_dimensions:
            continue
        dimension_cards = [card for card in cards if card.dimension == dimension]
        result = await jury_scorer(
            dimension,
            dimension_cards,
            router=router,
            settings=active_settings,
            force_llm=force_llm,
            llm_call=llm_call,
        )
        _apply_scoring_to_profile(
            profile,
            dimension,
            result,
            dimension_cards,
            db_path=db_path,
            source_event="profile_read",
        )
        _reset_pending_weight(user_id, dimension, db_path, now=now)

    upsert_profile(profile, db_path=db_path)
    return profile


def read_profile_snapshot(
    user_id: str,
    *,
    db_path: Path = DB_PATH,
    settings: Settings | None = None,
) -> Profile:
    """对外「看画像」的唯一入口：只读已落库快照，不跑模型、不写库。

    过去画像页的 GET 走 ``get_latest_profile``，那条路径会在**读**里跑三师评审
    （带证据但还没有结果的维度一律送模型）。后果是：刚结束对话、刚确认完学习
    目标时，一次「看一眼档案」要等几个模型调用；前端几百毫秒的超时先到，就把
    整页换成空的兜底数据——数据库里明明有 59 张证据卡和一个 100KB 的画像。

    读路径因此定为纯读：页面看到的就是收尾时写进库的那份快照，与对话里刚看到
    的雷达完全一致。需要重算的地方都在**写**路径（答题、收尾、确认目标），
    它们各自负责把结论落库。

    唯一的例外是「没配模型就不出分」：写路径会落一个规则融合分，而用户视角
    的契约是维度留空、只报置信度与未评分原因。这一步是纯字符串与字段整理，
    不进模型，仍然算读。
    """

    profile = get_profile(user_id, db_path=db_path)
    return _apply_user_view_contract(profile, settings=settings)


def _apply_user_view_contract(profile: Profile, *, settings: Settings | None = None) -> Profile:
    """把落库快照整理成「用户该看到的那一份」：没有模型密钥就不出分。"""

    active_settings = settings or get_settings()
    if _has_configured_llm_key(active_settings):
        return profile
    provenance = _profile_provenance(profile)
    for dimension in DIMENSION_LABELS:
        if not str(provenance.get(dimension, "")).startswith("no_key"):
            continue
        dim = getattr(profile, dimension)
        for field in ("score", "radar_score"):
            # 不出分就不能留一个旧分：否则雷达上是亮点、下面写"暂不评分"。
            # 分类维度没有 `score` 字段，分只存在 `radar_score` 里，两处都要清。
            if getattr(dim, field, None) is not None:
                setattr(dim, field, None)
        _stamp_scoring_provenance(dim.evidence or [], "no_key")
    return profile


def attach_evidence_to_dimension(
    user_id: str,
    dimension: DimensionKey,
    *,
    db_path: Path = DB_PATH,
    now: datetime | None = None,
) -> Profile:
    """把某维度的新证据接进画像，但不覆盖已经得出的结论。

    典型场景是「对话结束后又确认了一条学习目标」：这条自述要出现在档案的依据
    里，但学习目标的分数是对话里按组件评出来的，不能被一次规则融合改写成另一
    个数字。所以：有结论就只补证据，没结论才按规则融合出一个起步结论。
    """

    profile = get_profile(user_id, db_path=db_path)
    cards = [card for card in list_evidence(user_id, db_path=db_path) if card.dimension == dimension]
    if not cards:
        return profile

    dim = getattr(profile, dimension)
    if _dimension_has_result(profile, dimension):
        if [card.evidence_id for card in dim.evidence] != [card.evidence_id for card in cards]:
            dim.evidence = cards
            upsert_profile(profile, db_path=db_path)
        return profile

    trust = get_trust(user_id, db_path=db_path)
    fusion = fuse_dimension(dimension, cards, now=now, self_report_trust=trust)
    _apply_fusion_to_profile(
        profile,
        dimension,
        fusion,
        cards,
        db_path=db_path,
        reason="新证据接入",
        source_event="evidence_attach",
        provenance="fusion" if _has_configured_llm_key(get_settings()) else "no_key_rule_fallback",
    )
    upsert_profile(profile, db_path=db_path)
    return profile


def evidence_cards_from_event(event: ProfileUpdateEvent, *, now: datetime | None = None) -> list[EvidenceCard]:
    """Event hook: quiz/prescription/interaction behavior to evidence cards."""

    timestamp = event.timestamp or now or datetime.now(SHANGHAI_TZ)
    if event.type == "quiz":
        return _quiz_cards(event.payload, timestamp)
    if event.type == "prescription":
        return _prescription_cards(event.payload, timestamp)
    return _interaction_cards(event.payload, timestamp)


def profile_to_response(profile: Profile) -> dict[str, Any]:
    """Stable API payload with latest profile and per-dimension confidence."""

    payload = profile.model_dump(mode="json")
    for dimension in DIMENSION_LABELS:
        item = payload.get(dimension)
        if not isinstance(item, dict):
            continue
        # 分类维度（学习目标/风格/资源偏好）在模型里只有 `radar_score`，没有
        # `score` 字段；而前端与卡片一律读 `score`。不补这一行，刚聊出来的
        # 53 分在对话牌面是 53、进弟子档案就成了"派生值·待补充"。
        # 对外只暴露一个分数入口：score 是权威，radar_score 是兼容别名。
        if item.get("score") is None and item.get("radar_score") is not None:
            item["score"] = item["radar_score"]
    payload["confidence"] = {
        dimension: getattr(profile, dimension).confidence for dimension in DIMENSION_LABELS
    }
    provenance = _profile_provenance(profile)
    payload["provenance"] = provenance
    radar = to_radar(profile)
    radar["provenance"] = provenance
    for axis in radar.get("axes", []):
        if isinstance(axis, dict):
            axis["provenance"] = provenance.get(str(axis.get("key")), "unknown")
    payload["radar"] = radar
    return payload


def _quiz_cards(payload: dict[str, Any], timestamp: datetime) -> list[EvidenceCard]:
    cards: list[EvidenceCard] = []
    items = payload.get("answers") or payload.get("items") or [payload]
    if not isinstance(items, list):
        items = [items]
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        difficulty = str(item.get("difficulty") or "easy")
        gave_up = bool(item.get("gave_up") or item.get("abandoned"))
        correct = bool(item.get("correct") or item.get("is_correct"))
        weight = 0.6 if gave_up else (1.2 if difficulty in {"hard", "难", "difficult"} else 0.8)
        topic = str(item.get("topic") or item.get("knowledge_point") or "辨药").strip()
        cards.append(
            _card(
                "knowledge_base",
                "做题",
                f"做题第{index}题{'放弃' if gave_up else ('答对' if correct else '答错')}",
                {
                    "question_id": item.get("question_id") or item.get("id") or index,
                    "correct": correct,
                    "difficulty": difficulty,
                    "gave_up": gave_up,
                    "topic": topic,
                },
                weight,
                timestamp,
            )
        )
        if gave_up or not correct:
            cards.append(
                _card(
                    "weak_points",
                    "做题",
                    f"{topic}出现{'放弃' if gave_up else '错误'}",
                    {"label": topic, "correct": correct, "gave_up": gave_up},
                    weight,
                    timestamp,
                )
            )
    ability = payload.get("ability")
    if isinstance(ability, dict):
        rating = ability.get("rating")
        score = ability.get("score")
        coverage_count = ability.get("coverage_count")
        coverage_target = ability.get("coverage_target", 8)
        coverage = ability.get("coverage")
        if _finite_number(score) is not None and _finite_number(rating) is not None:
            cards.append(
                EvidenceCard(
                    dimension="knowledge_base",
                    source="Elo 实测能力",
                    tier=EvidenceTier.MEASURED,
                    fact=(
                        f"实测能力 {float(score):.1f} 分（rating {float(rating):.1f}），"
                        f"已考察主题 {int(coverage_count or 0)}/{int(coverage_target or 8)}"
                    ),
                    objective_signal={
                        "score": float(score),
                        "rating": float(rating),
                        "coverage_count": int(coverage_count or 0),
                        "coverage_target": int(coverage_target or 8),
                        "coverage": float(coverage or 0),
                    },
                    weight=3.0,
                    timestamp=timestamp,
                )
            )
    return cards


def _prescription_cards(payload: dict[str, Any], timestamp: datetime) -> list[EvidenceCard]:
    evaluation = evaluate_prescription(payload)
    signal = evaluation.signal()
    signal.update(
        {
            "quote": evaluation.quote or None,
            "basis": "explicit",
            "confidence": 0.85,
            "extraction_method": evaluation.method,
            "knowledge_base_ref": (
                payload.get("knowledge_base_ref")
                or payload.get("knowledge_ref")
                or (evaluation.knowledge_base_refs[0] if evaluation.knowledge_base_refs else None)
            ),
        }
    )
    weak_labels = _weak_labels_from_signal(signal, payload)
    if weak_labels:
        signal["weak_labels"] = weak_labels
    fact = "；".join(
        [
            f"对症{'是' if signal['对症'] else '否'}",
            f"配伍{'是' if signal['配伍'] else '否'}",
            f"剂量{'合适' if signal['剂量'] else '需核查'}",
            f"误用毒烈{'有' if signal['误用毒烈'] else '无'}",
        ]
    )
    cards = [_card("knowledge_base", "开药实战", fact, signal, 1.5, timestamp)]
    if weak_labels:
        cards.append(
            _card(
                "weak_points",
                "开药实战",
                f"开药薄弱标签：{'、'.join(weak_labels)}",
                {
                    "weak_labels": weak_labels,
                    "correct": False,
                    "quote": evaluation.quote or None,
                    "basis": "explicit",
                    "confidence": 0.85,
                    "knowledge_base_ref": evaluation.knowledge_base_refs[0] if evaluation.knowledge_base_refs else None,
                    "extraction_method": evaluation.method,
                },
                1.5,
                timestamp,
            )
        )
    return cards


def _interaction_cards(payload: dict[str, Any], timestamp: datetime) -> list[EvidenceCard]:
    subtype = _str_or_none(payload.get("subtype") or payload.get("interaction_type"))
    if subtype == "resource_choice":
        label = _str_or_none(payload.get("resource_type") or payload.get("resource_preference"))
        return (
            [_card("resource_preference", "资源选择", f"资源偏好选择：{label}", {"label": label}, 1.0, timestamp)]
            if label
            else []
        )
    if subtype == "skip_or_retry":
        topic = str(payload.get("topic") or payload.get("knowledge_point") or "未标注内容").strip()
        retried = bool(payload.get("retry") or payload.get("retried"))
        skipped = bool(payload.get("skip") or payload.get("skipped"))
        cards = [
            _card(
                "weak_points",
                "跳过重试",
                f"{topic}{'重试' if retried else '跳过'}",
                {"label": topic, "correct": False, "gave_up": skipped},
                0.8 if retried else 0.6,
                timestamp,
            )
        ]
        cards.append(
            _card(
                "motivation",
                "跳过重试",
                "主动重试" if retried else "跳过当前任务",
                {"accept_challenge": retried, "motivation_score": 65 if retried else 35},
                0.5,
                timestamp,
            )
        )
        return cards
    if subtype == "dwell_time":
        seconds = payload.get("dwell_seconds") or payload.get("duration_seconds") or 0
        minutes = max(0.0, float(seconds) / 60.0)
        return [
            _card("pace", "停留时长", f"内容停留 {minutes:.1f} 分钟", {"daily_minutes": minutes}, 0.5, timestamp),
            _card("learning_style", "停留时长", "内容停留行为线索", {"topic": payload.get("topic")}, 0.3, timestamp),
        ]
    # chat_message uses the established generic mapping. Absence of subtype is
    # intentionally identical for backward compatibility.
    cards: list[EvidenceCard] = []
    if label := _str_or_none(payload.get("learning_goal")):
        cards.append(_card("learning_goal", "交互事件", f"学习目标选择：{label}", {"label": label}, 1.0, timestamp))
    if label := _str_or_none(payload.get("style") or payload.get("learning_style")):
        cards.append(_card("learning_style", "交互事件", f"学习风格线索：{label}", {"label": label}, 1.0, timestamp))
    if label := _str_or_none(payload.get("resource_type") or payload.get("resource_preference")):
        cards.append(_card("resource_preference", "交互事件", f"资源偏好选择：{label}", {"label": label}, 1.0, timestamp))
    pace_signal = {
        "daily_minutes": payload.get("daily_minutes") or payload.get("minutes_per_day"),
        "target_days": payload.get("target_days") or payload.get("period_days"),
    }
    if pace_signal["daily_minutes"] is not None or pace_signal["target_days"] is not None:
        cards.append(_card("pace", "交互事件", "学习节奏约定", pace_signal, 1.0, timestamp))
    motivation_signal = {
        "label": payload.get("motivation_label") or payload.get("motivation"),
        "motivation_score": payload.get("motivation_score"),
        "accept_challenge": payload.get("accept_challenge") or payload.get("challenge"),
    }
    if any(value is not None for value in motivation_signal.values()):
        cards.append(_card("motivation", "交互事件", "动机偏好线索", motivation_signal, 1.0, timestamp))
    if not cards:
        topic = str(payload.get("topic") or payload.get("knowledge_point") or "未标注内容").strip()
        cards.append(_card("learning_style", "交互事件", f"普通交互：{topic}", {"topic": topic}, 0.3, timestamp))
    return cards


def _apply_fusion_to_profile(
    profile: Profile,
    dimension: DimensionKey,
    fusion: FusionResult,
    cards: list[EvidenceCard],
    *,
    db_path: Path | None = None,
    reason: str = "规则融合更新",
    source_event: str = "fusion",
    provenance: str = "fusion",
) -> None:
    dim = getattr(profile, dimension)
    old_value = _dimension_snapshot(dim)
    dim.confidence = fusion.confidence
    dim.radar_score = fusion.radar_score
    dim.coverage = fusion.coverage
    dim.unknown_reasons = fusion.unknown_reasons
    dim.confidence_breakdown = fusion.confidence_breakdown
    dim.evidence = cards
    _stamp_scoring_provenance(cards, provenance)
    dim.narrative = fusion.narrative
    if hasattr(dim, "score"):
        dim.score = fusion.score
    if hasattr(dim, "label"):
        dim.label = fusion.label
    if hasattr(dim, "distribution"):
        dim.distribution = fusion.distribution
    gate = fusion.metadata.get("component_collection") if isinstance(fusion.metadata, dict) else None
    _apply_component_state(
        dim,
        dimension,
        gate=gate if isinstance(gate, dict) else None,
        score=fusion.score,
        coverage=fusion.coverage,
        confidence=fusion.confidence,
    )
    if dimension == "weak_points":
        dim.items = _weak_points_from_cards(cards)
    _record_dimension_change(profile, dimension, old_value, reason, source_event, cards, db_path)


def _apply_scoring_to_profile(
    profile: Profile,
    dimension: DimensionKey,
    result: ScoringResult,
    cards: list[EvidenceCard],
    *,
    db_path: Path | None = None,
    source_event: str = "jury",
) -> None:
    dim = getattr(profile, dimension)
    old_value = _dimension_snapshot(dim)
    has_pending_claim = any(card.claim_status == "pending" for card in cards)
    dim.confidence = min(result.confidence, 0.35) if has_pending_claim else result.confidence
    dim.radar_score = result.radar_score
    dim.coverage = result.coverage
    dim.unknown_reasons = result.unknown_reasons
    dim.confidence_breakdown = result.confidence_breakdown
    dim.evidence = cards
    _stamp_scoring_provenance(cards, result.provenance)
    dim.narrative = result.narrative
    if hasattr(dim, "score"):
        # Assign even when the score is None.  Keeping a stale score while the
        # confidence has just been reset to zero produced a lit axis with
        # "置信度 0" -- the worst of both worlds, and the reason the no-key
        # contract leaked through the read path.
        dim.score = result.aggregated_score
    if hasattr(dim, "label") and result.label is not None:
        dim.label = result.label
    if hasattr(dim, "distribution") and result.distribution:
        dim.distribution = result.distribution
    _apply_component_state(
        dim,
        dimension,
        gate=None,
        component_scores=result.component_scores,
        score=result.aggregated_score,
        coverage=result.coverage,
        confidence=result.confidence,
    )
    if dimension == "weak_points":
        dim.items = _weak_points_from_cards(cards)
    warning_text = "、".join(warning.reason for warning in result.warnings)
    reason = "三师评审更新"
    if warning_text:
        reason = f"{reason}；护栏钳制：{warning_text}"
    _record_dimension_change(profile, dimension, old_value, reason, source_event, cards, db_path)


def _apply_component_state(
    dim: Any,
    dimension: DimensionKey,
    *,
    gate: dict[str, Any] | None,
    score: float | None,
    coverage: float,
    confidence: float,
    component_scores: dict[str, float] | None = None,
) -> None:
    """Persist the component ledger and the honest error bar.

    Without this the radar could only render a bare number: the collection
    progress, the pending components and the interval width all lived in the
    in-memory ``FusionResult`` and were dropped on the way to disk.
    """

    components = COMPONENTS[dimension]
    weights = COMPONENT_WEIGHTS[dimension]
    scores = {
        key: float(value)
        for key, value in (component_scores or (gate or {}).get("component_scores") or {}).items()
        if key in components
    }
    missing = set((gate or {}).get("missing_components") or ())
    matrix: dict[str, dict[str, Any]] = {}
    for component in components:
        entry: dict[str, Any] = {"weight": weights[component]}
        if component in scores:
            entry["status"] = "scored"
            entry["score"] = scores[component]
        elif component in missing:
            entry["status"] = "pending"
        else:
            entry["status"] = "collected"
        matrix[component] = entry
    dim.component_matrix = matrix
    dim.component_scores = scores
    dim.pending_components = [component for component in components if component not in scores]
    dim.scoring_phase = "scored" if score is not None else "collecting"
    dim.coverage = round(max(0.0, min(1.0, float(coverage or 0.0))), 4)
    if score is None:
        dim.score_interval = None
    else:
        low, high = score_interval(float(score), dim.coverage, confidence)
        dim.score_interval = {"low": low, "high": high}


def _dimension_snapshot(dim: Any) -> dict[str, Any]:
    return {
        "score": getattr(dim, "score", None),
        "label": getattr(dim, "label", None),
        "distribution": getattr(dim, "distribution", {}),
        "confidence": getattr(dim, "confidence", 0.0),
        "items": [item.model_dump(mode="json") for item in getattr(dim, "items", [])],
    }


def _stamp_scoring_provenance(cards: list[EvidenceCard], provenance: str) -> None:
    for card in cards:
        card.objective_signal = {**card.objective_signal, "scoring_provenance": provenance}


def _profile_provenance(profile: Profile) -> dict[str, str]:
    result: dict[str, str] = {}
    for dimension in DIMENSION_LABELS:
        cards = getattr(profile, dimension).evidence or []
        values = [str(card.objective_signal.get("scoring_provenance")) for card in cards if card.objective_signal.get("scoring_provenance")]
        result[dimension] = values[-1] if values else "unknown"
    return result


def _record_dimension_change(
    profile: Profile,
    dimension: DimensionKey,
    old_value: dict[str, Any],
    reason: str,
    source_event: str,
    cards: list[EvidenceCard],
    db_path: Path | None,
) -> None:
    if db_path is None:
        return
    record_profile_change(
        profile.user_id,
        dimension,
        old_value,
        _dimension_snapshot(getattr(profile, dimension)),
        reason=reason,
        source_event=source_event,
        evidence_ids=[card.evidence_id for card in cards],
        db_path=db_path,
    )


def _dimension_has_result(profile: Profile, dimension: DimensionKey) -> bool:
    dim = getattr(profile, dimension)
    if getattr(dim, "score", None) is not None:
        return True
    if getattr(dim, "label", None) is not None:
        return True
    if bool(getattr(dim, "distribution", {})):
        return True
    return bool(getattr(dim, "items", []))


def _fusion_has_result(fusion: FusionResult) -> bool:
    return fusion.score is not None or fusion.label is not None or bool(fusion.distribution)


def _weak_points_from_cards(cards: list[EvidenceCard]) -> list[WeakPoint]:
    counter: Counter[str] = Counter()
    for card in cards:
        label = weak_point_label_of(card)
        if label:
            counter[label] += 1
        signal = card.objective_signal
        weak_labels = signal.get("weak_labels")
        if isinstance(weak_labels, list) and label:
            for item in weak_labels:
                if isinstance(item, str) and item.strip() and item.strip() != label:
                    counter[item.strip()] += 1
    return [WeakPoint(label=label, strength=strength) for label, strength in counter.most_common()]


def _init_update_state(db_path: Path) -> None:
    init_db(db_path)
    with closing(connect(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profile_update_state (
                user_id TEXT NOT NULL,
                dimension TEXT NOT NULL,
                pending_weight REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                last_scored_at TEXT,
                PRIMARY KEY(user_id, dimension)
            )
            """
        )
        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(profile_update_state)").fetchall()
        }
        if "last_scored_at" not in columns:
            conn.execute("ALTER TABLE profile_update_state ADD COLUMN last_scored_at TEXT")
        conn.commit()


def _increment_pending_weight(user_id: str, dimension: DimensionKey, weight: float, db_path: Path) -> float:
    now = datetime.now(UTC).isoformat()
    with closing(connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO profile_update_state(user_id, dimension, pending_weight, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, dimension) DO UPDATE SET
                pending_weight = pending_weight + excluded.pending_weight,
                updated_at = excluded.updated_at
            """,
            (user_id, dimension, weight, now),
        )
        row = conn.execute(
            "SELECT pending_weight FROM profile_update_state WHERE user_id = ? AND dimension = ?",
            (user_id, dimension),
        ).fetchone()
        conn.commit()
    return float(row[0]) if row else 0.0


def _reset_pending_weight(
    user_id: str,
    dimension: DimensionKey,
    db_path: Path,
    *,
    now: datetime | None = None,
) -> None:
    scored_at = _utc_now_iso(now)
    with closing(connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO profile_update_state(user_id, dimension, pending_weight, updated_at, last_scored_at)
            VALUES (?, ?, 0, ?, ?)
            ON CONFLICT(user_id, dimension) DO UPDATE SET
                pending_weight = 0,
                updated_at = excluded.updated_at,
                last_scored_at = excluded.last_scored_at
            """,
            (user_id, dimension, scored_at, scored_at),
        )
        conn.commit()


def _should_throttle_jury_refresh(
    user_id: str,
    dimension: DimensionKey,
    db_path: Path,
    *,
    settings: Settings,
    force_llm: bool,
    llm_call: LLMCall | None,
    now: datetime | None,
) -> bool:
    if not _uses_llm_for_jury(settings=settings, force_llm=force_llm, llm_call=llm_call):
        return False

    pending_weight, last_scored_at = _get_update_state(user_id, dimension, db_path)
    if pending_weight >= DEFAULT_JURY_WEIGHT_THRESHOLD:
        return False
    if last_scored_at is None:
        return False

    elapsed = _utc_now(now) - last_scored_at
    return elapsed < DEFAULT_JURY_RESCORE_INTERVAL


def _uses_llm_for_jury(*, settings: Settings, force_llm: bool, llm_call: LLMCall | None) -> bool:
    return force_llm or llm_call is not None or _has_configured_llm_key(settings)


def _has_configured_llm_key(settings: Settings) -> bool:
    return bool(_str_or_none(settings.deepseek_api_key) or _str_or_none(settings.zhipu_api_key))


def _get_update_state(user_id: str, dimension: DimensionKey, db_path: Path) -> tuple[float, datetime | None]:
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            """
            SELECT pending_weight, last_scored_at
            FROM profile_update_state
            WHERE user_id = ? AND dimension = ?
            """,
            (user_id, dimension),
        ).fetchone()
    if row is None:
        return 0.0, None
    return float(row[0] or 0.0), _parse_datetime(row[1])


def _utc_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    if now.tzinfo is None:
        return now.replace(tzinfo=UTC)
    return now.astimezone(UTC)


def _utc_now_iso(now: datetime | None = None) -> str:
    return _utc_now(now).isoformat()


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _new_weight(cards: list[EvidenceCard], dimension: DimensionKey) -> float:
    return sum(card.weight for card in cards if card.dimension == dimension)


def _dimensions_with_evidence(cards: list[EvidenceCard]) -> list[DimensionKey]:
    return _ordered_dimensions(card.dimension for card in cards)


def _ordered_dimensions(dimensions: Any) -> list[DimensionKey]:
    seen = set(dimensions)
    return [dimension for dimension in DIMENSION_LABELS if dimension in seen]


def _weak_labels_from_signal(signal: dict[str, bool], payload: dict[str, Any]) -> list[str]:
    provided = payload.get("weak_labels")
    if isinstance(provided, list):
        return [str(item).strip() for item in provided if str(item).strip()]
    labels: list[str] = []
    if not signal["对症"]:
        labels.append("辨证·对症")
    if not signal["配伍"]:
        labels.append("方药·配伍")
    if not signal["剂量"]:
        labels.append("中药·剂量")
    if signal["误用毒烈"]:
        labels.append("用药安全·毒烈药")
    return labels


def _card(
    dimension: DimensionKey,
    source: str,
    fact: str,
    objective_signal: dict[str, Any],
    weight: float,
    timestamp: datetime,
) -> EvidenceCard:
    signal = {key: value for key, value in objective_signal.items() if value is not None}
    quote = signal.get("quote")
    basis = signal.get("basis") if signal.get("basis") in {"explicit", "inferred"} else "inferred"
    method = signal.get("extraction_method")
    if method not in {"llm", "no_key_rule_fallback", "rule_fallback", "legacy"}:
        method = "legacy"
    confidence = signal.get("confidence", 0.0)
    try:
        extraction_confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        extraction_confidence = 0.0
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


def _str_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


__all__ = [
    "DEFAULT_JURY_RESCORE_INTERVAL",
    "DEFAULT_JURY_WEIGHT_THRESHOLD",
    "ProfileUpdateEvent",
    "ProfileUpdateResult",
    "evidence_cards_from_event",
    "get_latest_profile",
    "profile_to_response",
    "update_profile_from_event",
]
