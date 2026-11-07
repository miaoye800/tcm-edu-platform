<template>
  <details v-if="enabled" class="developer-audit" aria-label="画像开发审计记录" open>
    <summary><span><small>本轮账本</small>对话审计</span><b>{{ records.length }} 轮历史</b></summary>
    <p class="audit-intro">默认只展开最新一轮，历史记录可切换查看；展示结构化判断、评分依据与可复算结果，不展示隐藏思维链。</p>
    <label v-if="records.length > 1" class="turn-picker">查看轮次
      <select v-model.number="selectedTurn" aria-label="选择审计轮次">
        <option v-for="record in orderedRecords" :key="record.turn_index" :value="record.turn_index">第 {{ record.turn_index }} 轮{{ record.turn_index === latestTurn ? ' · 最新' : '' }}</option>
      </select>
    </label>
    <ol v-if="currentRecord" class="audit-turns">
      <li :key="currentRecord.turn_index" class="audit-turn">
        <template v-for="record in [currentRecord]" :key="record.turn_index">
        <header class="turn-heading"><strong>第 {{ record.turn_index }} 轮</strong><span>{{ labelFor(record.current_target) }} → 补问 {{ labelFor(record.next_target) }}</span></header>
        <blockquote class="user-quote">{{ record.user_input }}</blockquote>

        <details class="audit-section">
          <summary>本轮处理流程 <span>依次扫描、校验、更新组件、评分判定、规划追问，再生成回答</span></summary>
          <ol class="stage-list">
            <li v-for="(stage, index) in record.stages || []" :key="`${record.turn_index}-${index}-${stage.label}`">
              <b>{{ String(index + 1).padStart(2, '0') }}</b><strong>{{ stage.label }}</strong><span>{{ stage.actor }}</span><p>{{ stage.detail }}</p>
            </li>
          </ol>
        </details>

        <details class="audit-section">
          <summary>全维扫描 <span>{{ methodLabel(record.model_result.method) }}{{ record.model_result.failure_reason ? ` · ${record.model_result.failure_reason}` : '' }} · {{ record.model_result.evidence.length }} 条原子证据</span></summary>
          <p v-if="!record.model_result.scan_available" class="pending-reason">本轮没有可用的模型扫描明细，不能据此判断七维均无证据或给低分。请检查模型配置/输出后重试；本轮仍会显示为待取证。</p>
          <p v-else-if="!record.model_result.scan_complete" class="pending-reason">模型只返回了部分维度扫描；缺失维度按待取证处理，不推断为低分。</p>
          <div class="scan-grid">
            <article v-for="scan in record.model_result.dimension_scan || []" :key="scan.dimension" class="scan-item">
              <strong>{{ labelFor(scan.dimension) }}</strong><span>{{ scan.status === 'evidence_found' ? '有证据' : scan.status === 'pending' ? '待确认' : '未发现可用证据' }}</span>
              <p>{{ scan.reason || '模型未返回判断依据。' }}</p>
              <small v-for="candidate in scan.excluded_candidates || []" :key="`${candidate.dimension}-${candidate.component}-${candidate.reason}`">排除 {{ candidate.dimension ? labelFor(candidate.dimension) : '' }}{{ candidate.component ? ` / ${componentLabel(candidate.component)}` : '' }}：{{ candidate.reason }}</small>
            </article>
          </div>
        </details>

        <details class="audit-section">
          <summary>原子证据 <span>原话归属、关系和确认边界</span></summary>
          <article v-for="evidence in record.model_result.evidence" :key="evidence.evidence_id || `${record.turn_index}-${evidence.dimension}-${evidence.fact}`" class="evidence-row">
            <header><strong>{{ labelFor(evidence.dimension) }}<template v-if="evidence.component"> / {{ componentLabel(evidence.component) }}</template></strong><span>{{ relationLabel(evidence.semantic_relation) }} · {{ evidence.evidence_group_id || '无来源组' }}</span></header>
            <blockquote>{{ evidence.quotes.join('；') || '缺少原话引用' }}</blockquote>
            <p>{{ evidence.fact }}</p>
            <small v-if="evidence.assessment">组件归属依据：{{ evidence.assessment }}</small>
            <small v-if="evidence.supports?.length">同源支持组件：{{ evidence.supports.map(componentLabel).join('、') }}</small>
            <div v-if="evidence.quality_audit" class="evidence-quality">
              <span class="quality-head">质量分 {{ score2(evidence.quality_audit.score) }}<template v-if="evidence.quality_audit.evidence_type"> · {{ evidence.quality_audit.evidence_type }}</template></span>
              <span v-for="item in qualityItems(evidence.quality_audit)" :key="item.key" :class="{ 'is-rule': item.source === 'rule' }">
                {{ item.label }} {{ score2(item.value) }}<template v-if="item.tier_label">（{{ item.tier_label }}档 {{ score2(item.tier_range?.[0]) }}–{{ score2(item.tier_range?.[1]) }}）</template><template v-else>（规则算）</template>
              </span>
              <small v-if="evidence.quality_audit.formula" class="quality-formula">{{ evidence.quality_audit.formula }} = <b>{{ score2(evidence.quality_audit.score) }}</b>　{{ evidence.quality_audit.weight_note }}</small>
              <small v-if="evidence.quality_audit.reason">依据：{{ evidence.quality_audit.reason }}</small>
              <small v-if="evidence.quality_audit.thinking" class="quality-thinking">怎么判的：{{ evidence.quality_audit.thinking }}</small>
            </div>
            <div class="evidence-meta"><span>来源轮次 {{ (evidence.source_turn_ids || [record.turn_index]).join('、') }}</span><span>确认：{{ (evidence.confirmed_aspects || evidence.agent_assessment?.confirmed_aspects || []).join('；') || '无' }}</span><span>待确认：{{ (evidence.pending_aspects || evidence.agent_assessment?.unknown_reasons || []).join('；') || '无' }}</span></div>
            <small v-if="evidence.agent_assessment?.assessment_reason">归属依据：{{ evidence.agent_assessment.assessment_reason }}</small>
          </article>
          <p v-if="!record.model_result.evidence.length" class="audit-empty">本轮未保留可追溯原子证据；未提及不作为低分。</p>
        </details>

        <details class="audit-section" open>
          <summary>七维组件与评分 <span>收齐到哪一步 → 送评 → 三师打分 → 统分</span></summary>
          <article v-for="dimension in record.system_result.dimensions" :key="dimension.key" class="dimension-ledger">
            <header><strong>{{ dimension.label }}</strong><b :class="dimension.scoring_phase === 'scored' ? 'is-scored' : 'is-pending'">{{ dimension.scoring_phase === 'scored' ? '已评分' : '评分暂缓' }}</b><span>{{ coverageText(dimension) }}</span></header>
            <div class="matrix-grid">
              <div v-for="item in dimension.component_matrix || []" :key="item.key" :class="{ collected: item.collected }"><span>{{ item.label }}</span><b>{{ dimension.matrix_before?.[item.key] ? '已收集' : '待收集' }} → {{ dimension.matrix_after?.[item.key] ? '已收集' : '待收集' }}</b><small v-if="item.score != null">{{ item.score }} 分<template v-if="item.score_source"> · {{ item.score_source === 'rescored' ? '本轮重评' : '沿用上轮' }}</template></small></div>
            </div>
            <p v-if="dimension.pending_components?.length" class="pending-reason"><b>还缺：</b>{{ dimension.pending_components.map(componentLabel).join('、') }}</p>
            <p v-if="dimension.pending_components?.length" class="pending-reason"><b>下轮补问：</b>{{ dimension.pending_components.map(key => `${componentLabel(key)}：${dimension.followup_reasons?.[key] || '需要补充可追溯证据'}`).join('；') }}</p>

            <section v-if="dimension.scoring_phase === 'scored'" class="pipeline">
              <header class="pipeline-head"><b>①</b> 送评证据包<small>发给评分模型的全部内容</small></header>
              <div class="package-grid">
                <div v-for="item in scoringComponents(dimension)" :key="item.key" :class="['package-item', { 'is-empty': !item.evidence.length }]">
                  <strong>{{ componentLabel(item.key) }}</strong>
                  <span>权重 {{ percent(item.weight) }} · {{ item.evidence.length }} 条证据</span>
                  <small v-if="item.definition" class="rubric">判分依据：{{ item.definition }}</small>
                  <blockquote v-for="evidence in item.evidence" :key="String(evidence.evidence_id)">{{ evidence.quote }}</blockquote>
                  <small v-if="item.conflicts.length" class="conflict">冲突：{{ item.conflicts.map(conflict => conflict.quote).join('；') }}</small>
                  <small v-if="!item.evidence.length">未收集，不送评</small>
                </div>
              </div>

              <header class="pipeline-head"><b>②</b> 三师会诊<small>每个组件都由严师 / 宽师 / 中立各给一个档内落点，组件分取三人平均{{ dimension.scoring_turn ? ` · 这份分数评于第 ${dimension.scoring_turn} 轮` : '' }}</small></header>
              <p v-if="dimension.scoring_trigger" class="math-line is-note">评分节奏：{{ dimension.scoring_trigger }}<template v-if="dimension.next_scoring_watermark != null"> · 下次水位线 ≥ {{ dimension.next_scoring_watermark }} 条（当前 {{ dimension.current_watermark }}）</template></p>
              <div v-if="componentJuryRows(dimension).length" class="jury-grid">
                <div v-for="row in componentJuryRows(dimension)" :key="row.key" class="jury-line">
                  <header><strong>{{ row.label }}</strong><span>权重 {{ percent(row.weight) }}</span></header>
                  <div class="jury-cells">
                    <div v-for="teacher in row.teachers" :key="`${row.key}-${teacher.teacher}`" class="jury-cell">
                      <b>{{ teacher.teacher }}</b><strong>{{ teacher.score }}</strong>
                      <small v-if="teacher.reason">{{ teacher.reason }}</small>
                    </div>
                  </div>
                  <p class="jury-mean">组件分 = 三师平均（{{ row.teachers.map(teacher => teacher.score).join(' + ') }}）÷ {{ row.teachers.length }} = <b>{{ row.mean }}</b></p>
                  <small v-if="row.reason" class="jury-note">判分理由：{{ row.reason }}</small>
                  <small v-if="row.conflict" class="conflict">冲突处理：{{ row.conflict }}</small>
                </div>
              </div>
              <p v-else class="pending-reason">本次评分没有返回逐组件三师明细（可能是旧记录或规则回退），只有组件综合分可用。</p>
              <p v-if="dimension.jury_panel?.length" class="math-line">三师维度分（各自按组件权重合成）：{{ teacherSummary(dimension) }} → 均值 <b>{{ dimension.jury_mean == null ? '—' : round1(dimension.jury_mean) }}</b></p>
              <p class="math-line is-note">{{ dimension.jury_derivation || '维度级三师由逐组件三师按固定权重合成，不再单独让模型给整维印象分。' }}</p>

              <header class="pipeline-head"><b>③</b> 统分<small>固定权重合成维度分</small></header>
              <p v-if="dimension.carried_components?.length" class="math-line is-note">本轮重评 {{ (dimension.rescored_components || []).map(componentLabel).join('、') || '无' }}；沿用上轮分数 {{ dimension.carried_components.map(componentLabel).join('、') }}（这些组件的证据这轮没变，不重复打模型）。</p>
              <p class="math-line">加权均值 = Σ(组件分 × 权重) ÷ Σ权重 = {{ scoreMath(dimension).numerator }} ÷ {{ scoreMath(dimension).weightSum }} = <b>{{ scoreMath(dimension).weightedMean }}</b></p>
              <p class="math-line">维度分 = 50 + 覆盖率 {{ scoreMath(dimension).coverageText }} × ({{ scoreMath(dimension).weightedMean }} − 50) = <b>{{ scoreMath(dimension).score }}</b></p>
              <p class="math-line is-note">覆盖率固定以 5 为分母：少一个组件就少一份覆盖，分数向中性 50 收，不会替未知的那一格假设水平。</p>
            </section>
            <p v-else class="pending-reason">{{ dimension.scoring_deferred_reason || '覆盖度未达 60%，暂不产生维度分。' }}<template v-if="dimension.unscored_components?.length">；已评分组件：{{ dimension.scoring_output?.length ?? 0 }} 个</template></p>
          </article>
        </details>

        <details class="audit-section">
          <summary>确认度怎么算 <span>证据质量 → 组合区间 → 区间插值</span></summary>
          <p class="audit-intro">{{ record.system_result.confidence_explanation }}</p>
          <article v-for="dimension in record.system_result.dimensions.filter(item => item.evidence_count > 0)" :key="dimension.key" class="confidence-row">
            <strong>{{ dimension.label }}：{{ percent(dimension.confidence_before) }} → {{ percent(dimension.confidence) }}{{ dimension.scoring_phase === 'scored' ? '（已出分）' : '（收集中，仍会变）' }}</strong>
            <ol class="confidence-steps">
              <li v-for="(step, index) in confidenceSteps(dimension)" :key="step.title">
                <b>{{ index + 1 }}</b>
                <div><strong>{{ step.title }}</strong><p>{{ step.detail }}</p></div>
              </li>
            </ol>
            <template v-if="intervalRange(dimension)">
              <div class="interval-bar">
                <div class="interval-band" :style="intervalBandStyle(dimension)"></div>
                <div class="interval-marker" :style="intervalMarkerStyle(dimension)"></div>
              </div>
              <small class="interval-caption">{{ intervalRange(dimension)!.low }}%–{{ intervalRange(dimension)!.high }}% 是「{{ combinationLabel(dimension) }}」允许的区间，竖线是实际落点。确认度衡量证据够不够，和维度分（雷达）是两回事。</small>
            </template>
            <div v-if="(dimension.confidence_timeline ?? []).length" class="timeline">
              <table>
                <thead><tr><th>轮次</th><th>证据</th><th>本轮质量</th><th>置信度</th><th>维度分</th></tr></thead>
                <tbody>
                  <tr v-for="row in dimension.confidence_timeline" :key="row.turn" :class="{ 'is-scored': row.scored_this_turn }">
                    <td>第 {{ row.turn }} 轮</td>
                    <td>{{ row.evidence }}<small v-if="row.new_evidence"> +{{ row.new_evidence }}</small></td>
                    <td>{{ row.quality_new == null ? '—' : score2(row.quality_new) }}</td>
                    <td>{{ percent(row.confidence) }}</td>
                    <td>{{ row.score == null ? '未评' : round1(row.score) }}</td>
                  </tr>
                </tbody>
              </table>
              <small>置信度每轮重算，维度分只在触发轮变。带底色那行是出分的那一轮——同一行里的置信度就是「评分时」的置信度，后面几行置信度继续涨，分数要等下一次水位线才重评。本轮没抽到新证据时质量列显示 —，置信度仍按已有证据重算。</small>
            </div>
          </article>
          <p v-if="!record.system_result.dimensions.some(item => item.evidence_count > 0)" class="audit-empty">本轮没有可计入确认度的有效来源。</p>
        </details>
        </template>
      </li>
    </ol>
    <p v-else class="audit-empty">发送回答后显示本轮扫描、证据与收集变化。</p>
  </details>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { DimensionKey, ProfileDeveloperAuditRecord } from '../../types/profile'

const props = withDefaults(defineProps<{ records?: ProfileDeveloperAuditRecord[]; enabled?: boolean }>(), { records: () => [], enabled: import.meta.env.DEV })
const orderedRecords = computed(() => [...props.records].sort((left, right) => right.turn_index - left.turn_index))
const latestTurn = computed(() => orderedRecords.value[0]?.turn_index ?? 0)
const selectedTurn = ref(latestTurn.value)
watch(latestTurn, (turn) => { selectedTurn.value = turn })
const currentRecord = computed(() => orderedRecords.value.find((record) => record.turn_index === selectedTurn.value) ?? orderedRecords.value[0] ?? null)
const labels: Record<DimensionKey, string> = { knowledge_base: '掌握程度', learning_goal: '目标锚定度', learning_style: '学习方式偏向', weak_points: '掌握稳定度', pace: '节奏可持续度', motivation: '投入驱动力', resource_preference: '资源适配度' }
const labelFor = (key: DimensionKey | string) => labels[key as DimensionKey] ?? key
const componentLabels: Record<string, string> = {
  concept_foundation: '概念基础', knowledge_scope: '知识范围', retrieval_application: '提取应用', boundary_awareness: '边界误区意识', measured_performance: '实测表现',
  expression_certainty: '表达确定性', goal_specificity: '目标具体度', action_orientation: '行动指向', priority_signal: '优先级信号', cross_turn_stability: '跨轮稳定性',
  preference_clarity: '偏好明确度', cross_task_consistency: '跨任务一致性', comparative_choice: '比较性选择', strategy_execution: '策略执行', effect_fit: '效果匹配',
  recall_correctness: '回忆正确性', application_correctness: '应用正确性', cross_context_stability: '跨场景稳定性', error_recovery: '纠错恢复', measured_validation: '实测验证',
  plan_clarity: '计划清晰度', time_feasibility: '时间可行性', actual_execution: '实际执行', sustained_period: '持续周期', adjustment_recovery: '调整恢复',
  intrinsic_value: '内在价值', external_commitment: '外在承诺', actual_investment: '实际投入', persistence_under_obstacle: '受阻持续性', conflict_regulation: '冲突调节',
  preference_specificity: '偏好具体度', task_match_reason: '任务匹配理由', availability_constraint: '可获得性约束', usage_effect_feedback: '使用效果反馈',
}
const componentLabel = (key: string) => componentLabels[key] ?? key.replace(/_/g, ' ')
const percent = (value?: number) => `${Math.round(Math.max(0, Math.min(1, value ?? 0)) * 100)}%`
// 出分只看"评分覆盖度 = 已评分组件 / 5"，和维度分公式用的是同一个分母。
// 以前这里显示"已收集 x/5"，和覆盖度不是一回事（模型可能给状态为 pending
// 的组件也打了分），4/5 已收集却没分时，用户看到的就是"收集了却没雷达"。
const coverageText = (dimension: ProfileDeveloperAuditRecord['system_result']['dimensions'][number]) => {
  const total = dimension.component_collection?.total ?? dimension.component_matrix?.length ?? 5
  const scoredCount = dimension.component_matrix?.filter((item) => item.score != null).length
    ?? dimension.scoring_output?.length
    ?? 0
  const collected = dimension.component_collection?.collected ?? dimension.component_matrix?.filter((item) => item.collected).length ?? 0
  const coverage = percent(scoredCount / total)
  const head = `评分覆盖 ${coverage}（${scoredCount}/${total} 组件已评分）`
  const radar = dimension.radar_score == null ? '雷达 —' : `雷达 ${Math.round(dimension.radar_score * 10) / 10}`
  if (dimension.scoring_phase === 'scored') return `${head} · ${radar}`
  return `${head} · 未达 60% 门槛 · ${radar}（已收集 ${collected}/${total}）`
}
const relationLabel = (value?: string) => ({ new: '新证据', reinforcement: '补强', semantic_duplicate: '语义重复', conflict: '冲突' }[value || 'new'] ?? value ?? '新证据')

type AuditDimension = ProfileDeveloperAuditRecord['system_result']['dimensions'][number]

const shortId = (value: unknown) => String(value ?? '').slice(-4) || '—'
const round1 = (value: number) => Math.round(value * 10) / 10
// 质量分是 0–1 的证据质量，不是百分比概率。用小数展示，免得和确认度
// （同样是 0–1、但展示成百分比）混成同一个量纲。
const score2 = (value?: number) => (value ?? 0).toFixed(2)

// ① 送评证据包：模型实际看到的组件、权重与原话。没有证据的组件标成"不送评"，
// 这样"为什么这一维只有 80% 覆盖"在界面上直接看得出来。
const scoringComponents = (dimension: AuditDimension) =>
  (dimension.scoring_input?.components ?? []).map((item) => {
    const rubric = (item.rubric ?? {}) as Record<string, unknown>
    return {
      key: item.key,
      weight: item.weight,
      // 判分细则也属于"送给模型的内容"：不写出来，用户不知道模型按什么标准判档。
      definition: String(rubric.definition ?? ''),
      evidence: (item.deduplicated_evidence ?? []) as Array<{ evidence_id?: string; quote?: string }>,
      conflicts: (item.conflicts ?? []) as Array<{ evidence_id?: string; quote?: string }>,
    }
  })

// ② 三师会诊：每个组件都由严师/宽师/中立各给一个档内落点，组件分是三人平均。
// 旧版只在维度级给三个数，组件只有一个综合分，"严师 44、宽师 66"的分歧看不到。
const componentJuryRows = (dimension: AuditDimension) =>
  (dimension.component_matrix ?? [])
    .filter((item) => (item.jury?.length ?? 0) > 0)
    .map((item) => {
      const teachers = (item.jury ?? []).map((entry) => ({
        teacher: entry.teacher,
        score: entry.score == null ? '—' : round1(entry.score),
        reason: entry.reason ?? '',
      }))
      const values = (item.jury ?? [])
        .map((entry) => entry.score)
        .filter((value): value is number => typeof value === 'number')
      const mean = values.length ? round1(values.reduce((sum, value) => sum + value, 0) / values.length) : '—'
      return {
        key: item.key,
        label: item.label,
        weight: item.weight,
        teachers,
        mean,
        reason: item.reason ?? '',
        conflict: item.conflict_handling ?? '',
      }
    })

const teacherSummary = (dimension: AuditDimension) =>
  Object.entries(dimension.teacher_dimension_scores ?? {})
    .map(([teacher, score]) => `${teacher} ${round1(score)}`)
    .join(' · ')

// ③ 统分：完全照 dimension_score() 的算式把数字代进去，用户可自己按一遍。
const scoreMath = (dimension: AuditDimension) => {
  const terms = (dimension.component_matrix ?? [])
    .filter((item) => item.score != null)
    .map((item) => ({ score: Number(item.score), weight: item.weight }))
  const numerator = terms.reduce((sum, item) => sum + item.score * item.weight, 0)
  const weightSum = terms.reduce((sum, item) => sum + item.weight, 0)
  const weightedMean = weightSum ? numerator / weightSum : 0
  const total = dimension.component_collection?.total ?? dimension.component_matrix?.length ?? 5
  const coverage = dimension.component_collection?.coverage ?? (total ? terms.length / total : 0)
  return {
    numerator: round1(numerator),
    weightSum: round1(weightSum),
    weightedMean: round1(weightedMean),
    coverageText: percent(coverage),
    score: dimension.radar_score == null ? '—' : round1(dimension.radar_score),
  }
}

const combinationLabels: Record<string, string> = {
  single_vague_self_report: '单句模糊自述',
  single_clear_self_report: '单句清晰自述',
  multi_turn_clear_pending_crosscheck: '多轮清晰、待交叉验证',
  multi_turn_clear_consistent: '多轮清晰一致',
  multi_turn_self_report_with_priority: '多轮自述且有优先级',
  dialogue_behavior_consistent: '对话与行为一致',
  dialogue_behavior_measurement_long_term: '行为与长期实测',
}
const qualityLabels: Record<string, string> = {
  source_reliability: '来源可靠',
  expression_clarity: '表达清晰',
  content_specificity: '内容具体',
  freshness: '新鲜度',
  consistency: '一致性',
}
// 质量分逐项：新记录带 items（档位 + 区间 + 谁算的），旧记录只有 factors，
// 回退成纯数值展示，不让历史审计整块消失。
type QualityAudit = NonNullable<ProfileDeveloperAuditRecord['model_result']['evidence'][number]['quality_audit']>
const qualityItems = (audit: QualityAudit) => {
  if (audit.items?.length) return audit.items
  return Object.entries(audit.factors ?? {})
    .filter(([, value]) => typeof value === 'number')
    .map(([key, value]) => ({
      key,
      label: qualityLabels[key] ?? key,
      value: Number(value),
      source: audit.rule_computed?.includes(key) ? 'rule' : 'model',
      tier_label: null as string | null,
      tier_range: null as [number, number] | null,
    }))
}
const combinationLabel = (dimension: AuditDimension) => {
  const key = String(dimension.confidence_audit?.combination_type ?? '')
  return combinationLabels[key] ?? (key || '尚未判定')
}
const intervalRange = (dimension: AuditDimension): { low: number; high: number } | null => {
  const value = dimension.confidence_audit?.interval
  if (!value || typeof value !== 'object') return null
  const interval = value as Record<string, unknown>
  return { low: Math.round(Number(interval.lower ?? 0) * 100), high: Math.round(Number(interval.upper ?? 0) * 100) }
}
const intervalBandStyle = (dimension: AuditDimension) => {
  const range = intervalRange(dimension)
  return range ? { left: `${range.low}%`, width: `${Math.max(1, range.high - range.low)}%` } : {}
}
const intervalMarkerStyle = (dimension: AuditDimension) => ({ left: `${Math.round((dimension.confidence ?? 0) * 100)}%` })

// ④ 确认度四步：去重分源 → 质量分 → 选区间 → 插值。每一步都把数字写出来，
// 不再把 combination_type / quality_values 这类键名直接摊给用户看。// 档位不是模型随口定的：它由"几个独立来源组、清不清晰、有没有同组件多来源
// 确认、有没有冲突"推出来。这几个数不写出来，用户没法自己核一遍。
const bandBasisText = (dimension: AuditDimension) => {
  const basis = dimension.confidence_audit?.band_basis as Record<string, unknown> | undefined
  if (!basis) return ''
  const groups = Number(basis.source_groups ?? 0)
  const quality = Number(basis.mean_quality ?? 0)
  const floor = Number(basis.quality_floor ?? 0.65)
  const confirmed = (basis.cross_confirmed_components as string[] | undefined) ?? []
  const conflicts = Number(basis.conflicts ?? 0)
  const parts = [
    `${groups} 个独立来源组`,
    basis.is_clear
      ? `平均质量 ${score2(quality)} ≥ ${score2(floor)}（算清晰）`
      : `平均质量 ${score2(quality)} < ${score2(floor)}（只能算模糊自述）`,
    confirmed.length
      ? `${confirmed.map(componentLabel).join('、')} 被两个以上来源确认`
      : '没有组件被两个以上来源确认',
    conflicts ? `有 ${conflicts} 条冲突证据，不能判一致` : '无冲突',
  ]
  return `定档依据：${parts.join('；')}。`
}
const confidenceSteps = (dimension: AuditDimension) => {
  const audit = dimension.confidence_audit ?? {}
  const deduped = (audit.deduplicated_evidence_ids as string[] | undefined) ?? []
  const groups = (audit.independent_source_groups as string[] | undefined) ?? []
  const conflicts = (audit.conflict_evidence_ids as string[] | undefined) ?? []
  const quality = (audit.quality_values as Record<string, number> | undefined) ?? {}
  const factors = (audit.quality_factors as Record<string, Record<string, number>> | undefined) ?? {}
  const progress = Number(audit.combination_progress ?? 0)
  const range = intervalRange(dimension)
  const quotes = new Map((dimension.deduplicated_evidence_package ?? []).map((item) => [item.evidence_id, item.quote ?? '']))
  const qualityText = Object.entries(quality)
    .map(([id, value]) => `证据 ${shortId(id)}（${quotes.get(id) || '原话'}）${percent(Number(value))}`)
    .join('；')
  const factorText = Object.entries(factors)
    .slice(0, 3)
    .map(([id, values]) => `证据 ${shortId(id)}：${Object.entries(values)
      .filter(([key]) => key !== 'independence_note')
      .map(([key, value]) => `${qualityLabels[key] ?? key} ${percent(Number(value))}`)
      .join('、')}`)
    .join('；')
  return [
    {
      title: '证据去重、按来源分组',
      detail: `${deduped.length} 条去重证据 → ${groups.length} 个独立来源组${conflicts.length ? `，另有 ${conflicts.length} 条冲突证据保留` : ''}。同一句回答拆出的多条只算一组，重复说同一件事不加确认度。`,
    },
    {
      title: '每条证据算五项质量分',
      detail: `${qualityText || '本轮没有可用质量项'}。${factorText ? `明细：${factorText}。` : ''}五项各占 20% 权重，取加权几何平均作为这条证据的贡献（短板会拉低整体）。`,
    },
    {
      title: '按证据组合类型定区间',
      detail: `组合类型「${combinationLabel(dimension)}」→ 允许区间 ${range ? `${range.low}%–${range.high}%` : '—'}。${bandBasisText(dimension)}单句模糊自述只有 10%–25%，行为与长期实测才到 80%–95%。`,
    },
    {
      title: '在区间里按进度插值',
      detail: range
        ? `确认度 = ${range.low}% + ${percent(progress)} × (${range.high}% − ${range.low}%) = ${percent(dimension.confidence)}`
        : `证据还不够形成区间，当前只保留估计值 ${percent(dimension.confidence)}。`,
    },
  ]
}
const methodLabel = (method: string) => ({ llm: '模型结构化提取', rule_fallback: '模型输出无效', no_key_rule_fallback: '未配置模型', no_evidence: '扫描未形成证据' }[method] ?? method)
</script>

<style scoped lang="scss">
@use '../../styles/tokens.scss' as *;
.developer-audit{margin-top:14px;border:1px solid color-mix(in srgb,var(--qh-color-jade) 35%,transparent);border-radius:var(--qh-radius-card);background:color-mix(in srgb,var(--qh-vault-fill) 90%,var(--qh-surface-sunken));color:var(--qh-text)}
.developer-audit>summary,.audit-section>summary{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 14px;cursor:pointer;font-weight:900;list-style:none}
.developer-audit>summary span{display:flex;align-items:baseline;gap:8px}.developer-audit>summary small{color:var(--qh-color-gold);font-size:10px}.developer-audit>summary>b{color:var(--qh-color-jade);font-size:11px}
.audit-intro{margin:0;padding:0 14px 10px;color:var(--qh-text-muted);font-size:11px;line-height:1.5}.turn-picker{display:flex;align-items:center;gap:8px;margin:0 14px 10px;color:var(--qh-text-muted);font-size:11px}.turn-picker select{padding:5px 8px;border:1px solid var(--qh-color-jade-line);border-radius:8px;background:var(--qh-vault-fill);color:var(--qh-text);font:inherit}.audit-turns{display:grid;gap:10px;margin:0;padding:0 12px 12px;list-style:none}.audit-turn{padding:12px;border:1px solid color-mix(in srgb,var(--qh-color-jade-line) 55%,transparent);background:color-mix(in srgb,var(--qh-card-caption-fill) 72%,transparent)}
.turn-heading,.evidence-row>header,.dimension-ledger>header{display:flex;justify-content:space-between;align-items:baseline;gap:10px}.turn-heading strong{font-size:13px}.turn-heading span,.evidence-row>header span{color:var(--qh-text-muted);font-size:11px}.user-quote{margin:8px 0 10px;padding:10px 12px;border-left:3px solid var(--qh-color-gold);background:color-mix(in srgb,var(--qh-color-gold) 8%,transparent);font-size:13px;line-height:1.6;white-space:pre-wrap}
.audit-section{border-top:1px solid color-mix(in srgb,var(--qh-color-jade-line) 42%,transparent)}.audit-section>summary{padding:10px 0;font-size:12px}.audit-section>summary span{color:var(--qh-text-muted);font-size:10px;font-weight:500}.scan-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}.scan-item,.evidence-row,.dimension-ledger,.confidence-row{padding:9px;border:1px solid color-mix(in srgb,var(--qh-color-jade-line) 35%,transparent);background:color-mix(in srgb,var(--qh-vault-fill) 55%,transparent)}.scan-item{display:grid;grid-template-columns:1fr auto;gap:3px 8px}.scan-item strong,.evidence-row>header strong,.dimension-ledger>header strong{font-size:11px}.scan-item>span{color:var(--qh-color-jade);font-size:10px}.scan-item p,.scan-item small,.evidence-row p,.evidence-row small,.dimension-ledger p,.confidence-row p{grid-column:1/-1;margin:3px 0;color:var(--qh-text-muted);font-size:10px;line-height:1.5}.scan-item small{color:var(--qh-color-gold)}
.stage-list{display:grid;gap:5px;margin:0 0 9px;padding:0;list-style:none}.stage-list li{display:grid;grid-template-columns:28px minmax(90px,auto) 38px 1fr;align-items:start;gap:6px;padding:7px 8px;border-left:2px solid var(--qh-color-jade-line);background:color-mix(in srgb,var(--qh-vault-fill) 50%,transparent)}.stage-list li>b{color:var(--qh-color-gold);font-variant-numeric:tabular-nums}.stage-list li>strong{font-size:10px}.stage-list li>span{color:var(--qh-color-jade);font-size:9px}.stage-list li>p{margin:0;color:var(--qh-text-muted);font-size:10px;line-height:1.45}
.evidence-row{margin:6px 0;border-left:2px solid var(--qh-color-jade)}.evidence-row blockquote{margin:7px 0;padding-left:8px;border-left:1px solid var(--qh-color-gold);font-size:12px;line-height:1.5}.evidence-meta{display:grid;gap:3px;color:var(--qh-text-muted);font-size:10px}
.evidence-quality{display:flex;flex-wrap:wrap;gap:3px 9px;margin-top:6px;padding:5px 6px;background:color-mix(in srgb,var(--qh-color-jade) 7%,transparent);color:var(--qh-text-muted);font-size:9px}
.evidence-quality .quality-head{color:var(--qh-color-jade);font-weight:900}
.evidence-quality .is-rule{color:color-mix(in srgb,var(--qh-color-gold) 75%,var(--qh-text-muted))}
.evidence-quality small{flex-basis:100%;line-height:1.4}
.evidence-quality .quality-formula{color:var(--qh-text-muted);font-variant-numeric:tabular-nums}
.evidence-quality .quality-formula b{color:var(--qh-color-jade)}
.evidence-quality .quality-thinking{color:color-mix(in srgb,var(--qh-color-gold) 70%,var(--qh-text-muted))}
.evidence-quality .quality-note{opacity:.75}.evidence-row>small{display:block;margin-top:6px}
.dimension-ledger{margin:7px 0}.dimension-ledger>header b{font-size:10px}.is-pending{color:var(--qh-color-gold)}.is-scored{color:var(--qh-color-jade)}.dimension-ledger>header>span{font-size:11px}.matrix-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:4px;margin-top:8px}.matrix-grid>div{display:grid;gap:4px;padding:6px;background:color-mix(in srgb,var(--qh-text-muted) 8%,transparent);font-size:9px}.matrix-grid>div.collected{background:color-mix(in srgb,var(--qh-color-jade) 12%,transparent)}.matrix-grid span{line-height:1.3}.matrix-grid b{color:var(--qh-color-gold);font-size:9px}.matrix-grid .collected b,.matrix-grid small{color:var(--qh-color-jade)}.pending-reason{margin:7px 0 0;color:var(--qh-color-gold)!important;font-size:10px!important}
.pipeline{margin-top:8px;padding-top:6px;border-top:1px solid var(--qh-color-jade-line)}
.pipeline-head{display:flex;align-items:baseline;gap:6px;margin:9px 0 5px;font-size:11px;font-weight:900}
.pipeline-head>b{color:var(--qh-color-gold)}.pipeline-head>small{color:var(--qh-text-muted);font-size:10px;font-weight:500}
.package-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(148px,1fr));gap:5px}
.package-item{display:grid;gap:3px;padding:6px 7px;background:color-mix(in srgb,var(--qh-color-jade) 9%,transparent);font-size:10px}
.package-item>strong{font-size:10px}.package-item>span{color:var(--qh-text-muted)}.package-item .rubric{color:color-mix(in srgb,var(--qh-color-jade) 75%,var(--qh-text-muted));line-height:1.4}
.package-item blockquote{margin:0;padding-left:6px;border-left:1px solid var(--qh-color-gold);color:var(--qh-text-muted);font-size:10px;line-height:1.45}
.package-item.is-empty{background:transparent;border:1px dashed color-mix(in srgb,var(--qh-text-muted) 40%,transparent)}.package-item.is-empty>strong,.package-item.is-empty>span,.package-item.is-empty>small{color:var(--qh-text-muted)}
.package-item .conflict,.score-rows .conflict{color:var(--qh-color-gold)}
.jury-grid{display:grid;gap:5px}
.jury-line{padding:6px 7px;background:color-mix(in srgb,var(--qh-vault-fill) 65%,transparent);font-size:10px}
.jury-line>header{display:flex;justify-content:space-between;align-items:baseline;gap:6px}.jury-line>header strong{font-size:10px}.jury-line>header span{color:var(--qh-text-muted);font-size:9px}
.jury-cells{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:4px;margin-top:4px}
.jury-cell{display:grid;grid-template-columns:auto auto;justify-content:space-between;align-items:baseline;gap:4px;padding:4px 6px;background:color-mix(in srgb,var(--qh-color-jade) 8%,transparent)}
.jury-cell b{color:var(--qh-color-jade);font-size:9px}.jury-cell strong{font-size:11px}
.jury-cell small{grid-column:1/-1;color:var(--qh-text-muted);font-size:9px;line-height:1.4}
.jury-mean{margin:4px 0 0;font-size:10px}.jury-mean b{color:var(--qh-color-jade)}
.jury-note{display:block;margin-top:3px;color:var(--qh-text-muted);font-size:9px}
.score-rows{display:grid;gap:4px;margin-top:6px}.score-rows>div{display:grid;gap:2px;padding:5px 7px;border-left:2px solid var(--qh-color-jade);font-size:10px}
.score-rows strong{font-size:10px}.score-rows span{color:var(--qh-text-muted);line-height:1.45}
.math-line{margin:4px 0;padding:5px 7px;background:color-mix(in srgb,var(--qh-color-jade) 8%,transparent);font-size:10px;line-height:1.5}.math-line b{color:var(--qh-color-jade)}.math-line.is-note{background:transparent;color:var(--qh-text-muted)}
.confidence-row{margin:6px 0}.confidence-row>strong{font-size:11px}
.confidence-steps{display:grid;gap:6px;margin:7px 0;padding:0;list-style:none}
.confidence-steps>li{display:grid;grid-template-columns:18px 1fr;gap:6px;align-items:start}
.confidence-steps>li>b{display:flex;align-items:center;justify-content:center;width:18px;height:18px;border-radius:50%;background:color-mix(in srgb,var(--qh-color-jade) 18%,transparent);color:var(--qh-color-jade);font-size:9px}
.confidence-steps strong{font-size:10px}.confidence-steps p{margin:2px 0 0;color:var(--qh-text-muted);font-size:10px;line-height:1.5}
.timeline{margin-top:7px}
.timeline table{width:100%;border-collapse:collapse;font-size:10px;font-variant-numeric:tabular-nums}
.timeline th{padding:2px 4px;text-align:left;font-weight:500;color:var(--qh-text-muted);border-bottom:1px solid color-mix(in srgb,var(--qh-color-jade-line) 45%,transparent)}
.timeline td{padding:2px 4px;border-bottom:1px solid color-mix(in srgb,var(--qh-color-jade-line) 25%,transparent)}
.timeline td small{color:var(--qh-text-muted);font-size:9px}
.timeline tr.is-scored{background:color-mix(in srgb,var(--qh-color-jade) 12%,transparent)}
.timeline tr.is-scored td{font-weight:500}
.timeline>small{display:block;margin-top:5px;color:var(--qh-text-muted);font-size:9px;line-height:1.5}
.interval-bar{position:relative;height:8px;margin:8px 0 5px;border-radius:4px;background:color-mix(in srgb,var(--qh-text-muted) 18%,transparent)}
.interval-band{position:absolute;top:0;bottom:0;border-radius:2px;background:color-mix(in srgb,var(--qh-color-jade) 45%,transparent)}
.interval-marker{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--qh-color-gold)}
.interval-caption{display:block;color:var(--qh-text-muted);font-size:10px;line-height:1.5}
.audit-empty{margin:0;padding:8px 0;color:var(--qh-text-muted);font-size:11px;line-height:1.5}
@media(max-width:680px){.scan-grid{grid-template-columns:1fr}.matrix-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.jury-row{grid-template-columns:1fr}.audit-section>summary{align-items:flex-start}.dimension-ledger>header{flex-wrap:wrap}}
</style>
