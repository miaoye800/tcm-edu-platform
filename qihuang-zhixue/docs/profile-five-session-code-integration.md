# 画像五个会话改动整合（代码级）

本文档把这批连续会话的画像改动整理成 5 个主题，直接放关键代码片段，方便快速回看。内容基于当前工作区未提交改动整理，不改业务代码。

## 1. 会话一：优化画像证据分层

核心是给 `EvidenceCard` 增加证据分层、证据唯一 ID、待校准状态，并为历史数据补 tier 推断。

文件：`backend/app/profile/evidence.py`

```python
class EvidenceTier(StrEnum):
    """Reliability tier for profile evidence."""

    MEASURED = "measured"
    BEHAVIORAL = "behavioral"
    SELF_REPORT = "self_report"


class EvidenceCard(BaseModel):
    """Atomic evidence used to update one learning-profile dimension."""

    model_config = ConfigDict(validate_assignment=True)

    evidence_id: str = Field(default_factory=lambda: uuid4().hex)
    dimension: str
    source: str
    tier: EvidenceTier
    fact: str
    objective_signal: dict[str, Any] = Field(default_factory=dict)
    weight: float = Field(ge=0.0)
    claim_status: Literal["pending", "verified", "refuted"] | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_tier(cls, value: Any) -> Any:
        if not isinstance(value, dict) or value.get("tier") is not None:
            return value
        payload = dict(value)
        payload["tier"] = infer_evidence_tier(
            str(payload.get("source") or ""),
            fact=str(payload.get("fact") or ""),
        )
        return payload
```

文件：`backend/app/profile/fusion.py`

```python
OBJECTIVE_CONFIDENCE_DIMENSIONS: frozenset[DimensionKey] = frozenset(
    {"knowledge_base", "weak_points", "pace"}
)
EVIDENCE_TIER_CONFIDENCE_CAPS: dict[EvidenceTier, float] = {
    EvidenceTier.SELF_REPORT: 0.35,
    EvidenceTier.BEHAVIORAL: 0.6,
    EvidenceTier.MEASURED: 1.0,
}

def _cap_confidence_by_tier(
    dimension: DimensionKey,
    confidence: float,
    cards: list[EvidenceCard],
) -> float:
    if dimension not in OBJECTIVE_CONFIDENCE_DIMENSIONS or not cards:
        return confidence
    if any(card.claim_status == "pending" for card in cards):
        return round(min(confidence, 0.35), 4)
    highest_tier = max((_effective_tier(card) for card in cards), key=EVIDENCE_TIER_RANK.__getitem__)
    return round(min(confidence, EVIDENCE_TIER_CONFIDENCE_CAPS[highest_tier]), 4)
```

## 2. 会话二：完善画像待验证与校准闭环

核心是把“自述类但需要实证”的画像先落成 `pending`，再通过校准题把 claim 结算成 `verified / refuted`，并同步调整用户信任系数。

文件：`backend/app/profile/store.py`

```python
def add_evidence(user_id: str, card: EvidenceCard, db_path: Path = DB_PATH) -> EvidenceCard:
    """Persist an objective evidence card."""

    init_db(db_path)
    if (
        card.tier == EvidenceTier.SELF_REPORT
        and card.dimension in {"knowledge_base", "weak_points", "pace"}
        and card.claim_status is None
    ):
        card.claim_status = "pending"
```

```python
def update_evidence_claim_status(
    user_id: str,
    evidence_id: str,
    status: str,
    db_path: Path = DB_PATH,
) -> EvidenceCard:
    """Update one claim in both its structured JSON and denormalized row."""
```

```python
def get_trust(user_id: str, db_path: Path = DB_PATH) -> float:
    """Return the bounded self-report trust coefficient, creating its default."""

def adjust_trust(user_id: str, delta: float, db_path: Path = DB_PATH) -> float:
    """Atomically adjust trust within the supported [0.3, 1.0] interval."""
```

文件：`backend/app/profile/calibration.py`

```python
def next_calibration(user_id: str, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    """Return the oldest pending calibration plan, creating plans as needed."""

def settle_calibration(
    user_id: str,
    submission: CalibrationSubmission | dict[str, Any],
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    """Settle a plan, update trust, and recalibrate the affected profile axis."""
```

```python
    accuracy = sum(1 for item in graded if item["correct"]) / len(graded)
    if accuracy >= 2 / 3:
        status: Literal["verified", "refuted", "pending"] = "verified"
        trust = adjust_trust(user_id, 0.05, db_path)
    elif accuracy <= 1 / 3:
        status = "refuted"
        trust = adjust_trust(user_id, -0.15, db_path)
    else:
        status = "pending"
        trust = get_trust(user_id, db_path)

    claim = update_evidence_claim_status(user_id, parsed.claim_id, status, db_path)
    calibration_card = _calibration_evidence(claim, status, accuracy, reason, now)
    add_evidence(user_id, calibration_card, db_path)
    _recompute_dimension(user_id, claim.dimension, reason, db_path, now)
```

文件：`backend/app/api/profile.py`

```python
@router.get("/calibration/next")
async def read_next_calibration(...):
    return next_calibration(current_user.user_id, db_path=db_path) or {
        "claim_id": None,
        "dimension": None,
        "questions": [],
    }


@router.post("/calibration/submit")
async def submit_calibration(...):
    try:
        return settle_calibration(current_user.user_id, payload, db_path=db_path)
```

## 3. 会话三：接入学习实测能力与画像融合

核心是把答题行为沉淀成独立的 Elo 能力快照，再反哺 `knowledge_base` 画像维度，并把覆盖度带进置信度。

文件：`backend/app/profile/ability.py`

```python
@dataclass(frozen=True)
class AbilitySnapshot:
    rating: float = INITIAL_RATING
    attempt_count: int = 0
    coverage_count: int = 0

    @property
    def score(self) -> float:
        return rating_to_score(self.rating)
```

```python
def record_ability_attempt(
    *,
    user_id: str,
    kc_id: str,
    correct: bool,
    difficulty: str | int | None = "medium",
    topic: str | None = None,
    db_path: Path = DB_PATH,
    updated_at: datetime | None = None,
) -> AbilitySnapshot:
    """Apply one answer to overall ability and persist the resulting snapshot."""
```

文件：`backend/app/api/learning.py`

```python
ability = record_ability_attempt(
    user_id=user_id,
    kc_id=kc_id,
    correct=correct,
    difficulty=item.difficulty,
    topic=item.topic,
    db_path=db_path,
)

await update_profile_from_event(
    user_id,
    ProfileUpdateEvent(
        type="quiz",
        payload={"answers": profile_answers, "ability": ability.as_dict() if ability else None},
    ),
    db_path=db_path,
)
```

文件：`backend/app/profile/update.py`

```python
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
```

文件：`backend/app/profile/fusion.py`

```python
def _coverage_from_cards(
    dimension: DimensionKey,
    cards: list[EvidenceCard],
) -> tuple[int | None, int]:
    """Read the latest cumulative Elo coverage carried by measured evidence."""

    if dimension != "knowledge_base":
        return None, 8
```

```python
coverage_count, coverage_target = _coverage_from_cards(dimension, contributing_cards)
if coverage_count is not None:
    coverage_factor = max(0.5, min(1.0, coverage_count / coverage_target))
    confidence = round(confidence * coverage_factor, 4)
```

## 4. 会话四：补全目标规划与画像变迁录

核心是新增 `goal + planning_params`，允许用户确认目标后落库；同时为画像各维变化做审计留痕。

文件：`backend/app/profile/schema.py`

```python
class PlanningParams(BaseModel):
    depth: PlanningDepth = "中"
    breadth: PlanningBreadth = "全面"
    pace: PlanningPace = "中"
    fun_preference: float = Field(default=0.5, ge=0.0, le=1.0)
    assessment_strictness: AssessmentStrictness = "标准"
    time_budget: TimeBudget | None = None


class Goal(BaseModel):
    raw_text: str = ""
    normalized: LearningGoalLabel | None = None
    confirmed_by_user: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
```

文件：`backend/app/profile/goal_agent.py`

```python
class GoalAgentResult(BaseModel):
    planning_params: PlanningParams
    goal: Goal
    restatement: str = Field(min_length=1)


async def run_goal_agent(
    raw_text: str,
    *,
    profile: Profile | None = None,
    llm_call: LLMCall | None = None,
) -> GoalAgentResult:
    """Normalize one stated goal with one guarded LLM retry and safe fallback."""
```

文件：`backend/app/profile/store.py`

```python
def record_profile_change(
    user_id: str,
    dim: str,
    old_value: Any,
    new_value: Any,
    *,
    reason: str,
    source_event: str,
    evidence_ids: list[str],
    db_path: Path = DB_PATH,
    ts: datetime | None = None,
) -> None:
    """Record one material dimension transition through the shared audit sink."""
```

```python
def list_profile_changes(
    user_id: str,
    *,
    limit: int = 20,
    offset: int = 0,
    db_path: Path = DB_PATH,
) -> list[dict[str, Any]]:
    """Return newest profile changes with stable limit/offset pagination."""
```

文件：`backend/app/api/profile.py`

```python
@router.post("/goal")
async def normalize_goal(...):
    profile = get_profile(current_user.user_id, db_path=db_path)
    result = await run_goal_agent(payload.raw_text, profile=profile)
    return result.model_dump(mode="json")


@router.post("/goal/confirm")
async def confirm_goal(...):
    confirmed_goal = candidate.goal.model_copy(update={"confirmed_by_user": True})
    profile.planning_params = candidate.planning_params
    profile.goal = confirmed_goal
    upsert_profile(profile, db_path=db_path)
```

```python
def _profile_response_with_changes(
    profile: Profile,
    *,
    db_path: Path,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    payload = profile_to_response(profile)
    payload["change_log"] = list_profile_changes(
        profile.user_id,
        limit=limit,
        offset=offset,
        db_path=db_path,
    )
    payload["change_log_pagination"] = {"limit": limit, "offset": offset}
    payload["ability"] = get_ability(profile.user_id, db_path=db_path).as_dict()
    return payload
```

文件：`backend/app/profile/update.py`

```python
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
```

## 5. 会话五：完善画像待验证展示与变迁录前端呈现

核心是把 `pending_verification`、信任度、目标确认、画像变迁录和校准题入口全部接到前端页面。

文件：`frontend/src/utils/profileMath.ts`

```ts
const pendingVerification = Boolean(profile?.[key].evidence?.some((card) => card.claim_status === 'pending'))
return {
  key,
  label: radarLabels[key],
  value: derivedValues[key],
  confidence,
  lowConfidence: confidence < lowConfidenceThreshold,
  anchor: anchorText(key, derivedValues[key]),
  kind: axisKind(key),
  pending_verification: pendingVerification,
}
```

文件：`frontend/src/components/profile/DimRow.vue`

```vue
<div class="dim-row__badges">
  <Badge :tone="axis.lowConfidence ? 'muted' : 'jade'" :label="axis.confidenceStatus" />
  <span v-if="axis.pending_verification" class="pending-verification">待验证</span>
</div>
```

文件：`frontend/src/views/ProfileView.vue`

```vue
<GlassCard v-if="!profileStore.usingMock" class="trust-card" padding="md">
  <el-tooltip content="师父会根据你的自述与实测是否相符调整信任" placement="bottom">
    <div class="trust-card__label">师父信任度</div>
  </el-tooltip>
  <strong>{{ trust?.tier ?? '读取中' }}</strong>
  <span>{{ trust ? `${Math.round(trust.trust * 100)}%` : '' }}</span>
</GlassCard>
```

```vue
<GlassCard v-if="!profileStore.usingMock" class="change-log" padding="lg">
  <div class="panel-head"><div><p class="eyebrow">档案留痕</p><h3>画像变迁录</h3></div></div>
  <div v-if="profileStore.profile.change_log?.length" class="change-log__list">
    <article v-for="item in profileStore.profile.change_log" :key="`${item.ts}-${item.dim}`">
      <span class="change-icon">{{ calibrationChange(item.source_event) ? '◈' : '·' }}</span>
      <div><strong>{{ dimensionName(item.dim) }}</strong><small>{{ formatDateTime(item.ts) }}</small><p>{{ changeValue(item.old_value) }} → {{ changeValue(item.new_value) }}</p><em>{{ item.reason }}</em></div>
    </article>
  </div>
</GlassCard>
```

文件：`frontend/src/components/profile/ProfileDialogue.vue`

```vue
<section v-if="converged" class="dialogue-goal">
  <template v-if="goalCandidate">
    <p>小岐：{{ goalCandidate.restatement }}</p>
    <div><el-button size="small" type="primary" :loading="goalBusy" @click="confirmGoal">确认</el-button><el-button size="small" @click="goalCandidate = null">重新说</el-button></div>
  </template>
  <template v-else>
    <el-input v-model="goalText" size="small" placeholder="说说接下来的学习目标" @keyup.enter="proposeGoal" />
    <el-button size="small" :loading="goalBusy" @click="proposeGoal">让小岐复述</el-button>
  </template>
</section>
```

```vue
'is-veiled': activeIdentityDimension?.pending_verification && activeIdentityDimension?.claim_status !== 'verified',
...
<span v-if="activeIdentityDimension?.pending_verification && activeIdentityDimension?.claim_status !== 'verified'" class="veil-copy">尚待实证</span>
```

文件：`frontend/src/views/QuizView.vue`

```ts
const generateQuiz = async () => {
  await learning.generateResources(topicInput.value || '麻黄', ['quiz', 'explain'])
  try {
    const plan = await readNextCalibration()
    calibrationQuestions.value = plan.claim_id
      ? plan.questions.map((question) => ({
          stem: question.prompt,
          type: '单选',
          options: question.options.map((text, index) => ({ key: String.fromCharCode(65 + index), text })),
          answer: [],
          explanation: '',
          source: { name: learning.activeTopic, type: '题库', ref: question.question_id },
          bloom_level: '应用',
          difficulty: 1,
          distractor_rationale: {},
          claims: [],
          calibration: { claimId: plan.claim_id!, questionId: question.question_id },
        }))
      : []
  } catch { calibrationQuestions.value = [] }
}
```

## 关联文件总览

- 后端：`backend/app/profile/evidence.py`、`fusion.py`、`update.py`、`store.py`、`schema.py`、`ability.py`、`calibration.py`、`goal_agent.py`、`api/profile.py`、`api/learning.py`
- 前端：`frontend/src/types/profile.ts`、`services/profileApi.ts`、`utils/profileMath.ts`、`views/ProfileView.vue`、`components/profile/DimRow.vue`、`components/profile/ProfileDialogue.vue`、`views/QuizView.vue`、`components/learning/QuizPanel.vue`

## 一句话总结

这五个会话串起来后的主线是：先把画像证据按可信度分层，再让自述类画像进入待验证状态，用测验和校准题做实证回补，同时把目标规划、信任度、能力值和画像变迁录完整打通到前后端展示。
