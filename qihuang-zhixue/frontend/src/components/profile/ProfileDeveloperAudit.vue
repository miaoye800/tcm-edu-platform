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
            <div class="evidence-meta"><span>来源轮次 {{ (evidence.source_turn_ids || [record.turn_index]).join('、') }}</span><span>确认：{{ (evidence.confirmed_aspects || evidence.agent_assessment?.confirmed_aspects || []).join('；') || '无' }}</span><span>待确认：{{ (evidence.pending_aspects || evidence.agent_assessment?.unknown_reasons || []).join('；') || '无' }}</span></div>
            <small v-if="evidence.agent_assessment?.assessment_reason">归属依据：{{ evidence.agent_assessment.assessment_reason }}</small>
          </article>
          <p v-if="!record.model_result.evidence.length" class="audit-empty">本轮未保留可追溯原子证据；未提及不作为低分。</p>
        </details>

        <details class="audit-section">
          <summary>七维组件矩阵 <span>看本轮前后变化与补问原因</span></summary>
          <article v-for="dimension in record.system_result.dimensions" :key="dimension.key" class="dimension-ledger">
            <header><strong>{{ dimension.label }}</strong><b :class="dimension.scoring_phase === 'scored' ? 'is-scored' : 'is-pending'">{{ dimension.scoring_phase === 'scored' ? '已评分' : '评分暂缓' }}</b><span>已收集 {{ dimension.component_collection?.collected ?? dimension.component_matrix?.filter(item => item.collected).length ?? 0 }}/{{ dimension.component_collection?.total ?? dimension.component_matrix?.length ?? 5 }} 个组件 · {{ dimension.radar_score == null ? '雷达 —' : `雷达 ${dimension.radar_score}` }}</span></header>
            <div class="matrix-grid">
              <div v-for="item in dimension.component_matrix || []" :key="item.key" :class="{ collected: item.collected }"><span>{{ item.label }}</span><b>{{ dimension.matrix_before?.[item.key] ? '已收集' : '待收集' }} → {{ dimension.matrix_after?.[item.key] ? '已收集' : '待收集' }}</b><small v-if="item.score != null">{{ item.score }} 分</small></div>
            </div>
            <p v-if="dimension.pending_components?.length" class="pending-reason"><b>缺少：</b>{{ dimension.pending_components.map(componentLabel).join('、') }}</p>
            <p v-if="dimension.pending_components?.length" class="pending-reason"><b>下轮补问：</b>{{ dimension.pending_components.map(key => `${componentLabel(key)}：${dimension.followup_reasons?.[key] || '需要补充可追溯证据'}`).join('；') }}</p>
            <details v-if="dimension.scoring_phase === 'scored'" class="score-ledger">
              <summary>评分证据包与统分 · 评分依据（非私有思维链）</summary>
              <div v-for="item in dimension.scoring_input?.components || []" :key="item.key" class="score-input-row"><strong>{{ componentLabel(item.key) }} · 权重 {{ percent(item.weight) }}</strong><p v-for="evidence in item.deduplicated_evidence" :key="String(evidence.evidence_id)">证据 {{ evidence.evidence_id }}（{{ evidence.source_group }}）：{{ evidence.quote }}</p><p v-for="conflict in item.conflicts" :key="String(conflict.evidence_id)" class="conflict">冲突 {{ conflict.quote }}</p><small>{{ item.rubric.definition }} · 低/中/高：{{ item.rubric.low }} / {{ item.rubric.medium }} / {{ item.rubric.high }}</small></div>
              <p v-for="output in dimension.scoring_output || []" :key="output.component" class="score-output"><b>{{ componentLabel(output.component) }}：{{ output.score }} 分</b> {{ output.reason }} <span v-if="output.conflict_handling">冲突处理：{{ output.conflict_handling }}</span></p>
              <p class="formula">统分：{{ dimension.score_formula }}；权重和 {{ dimension.score_weight_sum }}；雷达最终分 {{ dimension.radar_score }}。</p>
            </details>
            <p v-else class="pending-reason">{{ dimension.scoring_deferred_reason || '必需组件未集齐，暂不产生维度分。' }}<template v-if="dimension.unscored_components?.length">；评分模型缺失项：{{ dimension.unscored_components.map(componentLabel).join('、') }}（已尝试 {{ dimension.scoring_attempts ?? 0 }} 次）</template></p>
          </article>
        </details>

        <details class="audit-section">
          <summary>确认度账本 <span>{{ confidenceSummary(record) || '尚无有效证据来源' }}</span></summary>
          <p class="audit-intro">{{ record.system_result.confidence_explanation }}</p>
          <article v-for="dimension in record.system_result.dimensions.filter(item => item.evidence_count > 0)" :key="dimension.key" class="confidence-row">
            <strong>{{ dimension.label }}：{{ dimension.scoring_phase === 'scored' ? `${percent(dimension.confidence_before)} → ${percent(dimension.confidence)}` : `确认度收集中 · 当前证据估计 ${percent(dimension.confidence)}（尚未发布最终确认度）` }}</strong>
            <dl v-if="dimension.confidence_audit && Object.keys(dimension.confidence_audit).length"><div><dt>组合类型</dt><dd>{{ confidenceField(dimension, 'combination_type') }}</dd></div><div><dt>组合区间</dt><dd>{{ confidenceInterval(dimension) }}</dd></div><div><dt>区间进度</dt><dd>{{ confidenceField(dimension, 'combination_progress') }}</dd></div><div><dt>独立来源组</dt><dd>{{ confidenceList(dimension, 'independent_source_groups') }}</dd></div><div><dt>去重证据</dt><dd>{{ confidenceList(dimension, 'deduplicated_evidence_ids') }}</dd></div><div><dt>冲突保留</dt><dd>{{ confidenceList(dimension, 'conflict_evidence_ids') }}</dd></div><div><dt>证据质量项</dt><dd>{{ confidenceQuality(dimension) }}</dd></div><div><dt>公式结果/上限</dt><dd>{{ confidenceField(dimension, 'confidence') }} / {{ confidenceInterval(dimension).split('–')[1] || '—' }}</dd></div></dl>
            <small v-if="dimension.confidence_audit?.formula">确认度公式：{{ dimension.confidence_audit.formula }}</small>
            <small v-if="dimension.confidence_audit?.quality_factors">质量项（来源/明确/具体/新鲜/一致）：{{ confidenceFactorDetails(dimension) }}</small>
            <div v-for="unit in dimension.confidence_units" :key="unit.source_unit">来源 {{ unit.fact }} · 独立性 {{ percent(unit.independence) }} · 衰减 {{ percent(unit.decay) }} · 有效贡献 {{ percent(unit.effective_contribution) }}</div>
            <small>雷达不是置信度；组合区间上限按单句/跨轮/行为/实测证据类型执行。</small>
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
const confidenceSummary = (record: ProfileDeveloperAuditRecord) => record.system_result.dimensions
  .filter((dimension) => dimension.evidence_count > 0)
  .map((dimension) => `${dimension.label} ${percent(dimension.confidence)}${dimension.scoring_phase === 'scored' ? '最终' : '估计'}`)
  .join(' · ')
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
const relationLabel = (value?: string) => ({ new: '新证据', reinforcement: '补强', semantic_duplicate: '语义重复', conflict: '冲突' }[value || 'new'] ?? value ?? '新证据')
const confidenceRecord = (dimension: ProfileDeveloperAuditRecord['system_result']['dimensions'][number]) => dimension.confidence_audit ?? {}
const confidenceField = (dimension: ProfileDeveloperAuditRecord['system_result']['dimensions'][number], key: string) => String(confidenceRecord(dimension)[key] ?? '—')
const confidenceList = (dimension: ProfileDeveloperAuditRecord['system_result']['dimensions'][number], key: string) => {
  const value = confidenceRecord(dimension)[key]
  return Array.isArray(value) && value.length ? value.join('、') : '无'
}
const confidenceInterval = (dimension: ProfileDeveloperAuditRecord['system_result']['dimensions'][number]) => {
  const value = confidenceRecord(dimension).interval
  if (!value || typeof value !== 'object') return '—'
  const interval = value as Record<string, unknown>
  return `${Math.round(Number(interval.lower ?? 0) * 100)}%–${Math.round(Number(interval.upper ?? 0) * 100)}%`
}
const confidenceQuality = (dimension: ProfileDeveloperAuditRecord['system_result']['dimensions'][number]) => {
  const value = confidenceRecord(dimension).quality_values
  if (!value || typeof value !== 'object') return '无质量项'
  return Object.entries(value as Record<string, unknown>).map(([id, score]) => `${id} ${Math.round(Number(score) * 100)}%`).join('；') || '无质量项'
}
const confidenceFactorDetails = (dimension: ProfileDeveloperAuditRecord['system_result']['dimensions'][number]) => {
  const factors = confidenceRecord(dimension).quality_factors
  if (!factors || typeof factors !== 'object') return '无'
  return Object.entries(factors as Record<string, Record<string, unknown>>).map(([id, values]) => `${id}: ${Object.entries(values).filter(([key]) => key !== 'independence_note').map(([key, value]) => `${key} ${percent(Number(value))}`).join('、')}${values.independence_note ? `；${values.independence_note}` : ''}`).join('；')
}
const methodLabel = (method: string) => ({ llm: '模型结构化提取', rule_fallback: '模型输出无效', no_key_rule_fallback: '未配置模型', no_evidence: '扫描未形成证据' }[method] ?? method)
</script>

<style scoped lang="scss">
@use '../../styles/tokens.scss' as *;
.developer-audit{margin-top:14px;border:1px solid color-mix(in srgb,var(--qh-color-jade) 35%,transparent);border-radius:var(--qh-radius-card);background:color-mix(in srgb,var(--qh-vault-fill) 90%,#0f2526);color:var(--qh-text)}
.developer-audit>summary,.audit-section>summary{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 14px;cursor:pointer;font-weight:900;list-style:none}
.developer-audit>summary span{display:flex;align-items:baseline;gap:8px}.developer-audit>summary small{color:var(--qh-color-gold);font-size:10px}.developer-audit>summary>b{color:var(--qh-color-jade);font-size:11px}
.audit-intro{margin:0;padding:0 14px 10px;color:var(--qh-text-muted);font-size:11px;line-height:1.5}.turn-picker{display:flex;align-items:center;gap:8px;margin:0 14px 10px;color:var(--qh-text-muted);font-size:11px}.turn-picker select{padding:5px 8px;border:1px solid var(--qh-color-jade-line);border-radius:8px;background:var(--qh-vault-fill);color:var(--qh-text);font:inherit}.audit-turns{display:grid;gap:10px;margin:0;padding:0 12px 12px;list-style:none}.audit-turn{padding:12px;border:1px solid color-mix(in srgb,var(--qh-color-jade-line) 55%,transparent);background:color-mix(in srgb,var(--qh-card-caption-fill) 72%,transparent)}
.turn-heading,.evidence-row>header,.dimension-ledger>header{display:flex;justify-content:space-between;align-items:baseline;gap:10px}.turn-heading strong{font-size:13px}.turn-heading span,.evidence-row>header span{color:var(--qh-text-muted);font-size:11px}.user-quote{margin:8px 0 10px;padding:10px 12px;border-left:3px solid var(--qh-color-gold);background:color-mix(in srgb,var(--qh-color-gold) 8%,transparent);font-size:13px;line-height:1.6;white-space:pre-wrap}
.audit-section{border-top:1px solid color-mix(in srgb,var(--qh-color-jade-line) 42%,transparent)}.audit-section>summary{padding:10px 0;font-size:12px}.audit-section>summary span{color:var(--qh-text-muted);font-size:10px;font-weight:500}.scan-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}.scan-item,.evidence-row,.dimension-ledger,.confidence-row{padding:9px;border:1px solid color-mix(in srgb,var(--qh-color-jade-line) 35%,transparent);background:color-mix(in srgb,var(--qh-vault-fill) 55%,transparent)}.scan-item{display:grid;grid-template-columns:1fr auto;gap:3px 8px}.scan-item strong,.evidence-row>header strong,.dimension-ledger>header strong{font-size:11px}.scan-item>span{color:var(--qh-color-jade);font-size:10px}.scan-item p,.scan-item small,.evidence-row p,.evidence-row small,.dimension-ledger p,.confidence-row p{grid-column:1/-1;margin:3px 0;color:var(--qh-text-muted);font-size:10px;line-height:1.5}.scan-item small{color:var(--qh-color-gold)}
.stage-list{display:grid;gap:5px;margin:0 0 9px;padding:0;list-style:none}.stage-list li{display:grid;grid-template-columns:28px minmax(90px,auto) 38px 1fr;align-items:start;gap:6px;padding:7px 8px;border-left:2px solid var(--qh-color-jade-line);background:color-mix(in srgb,var(--qh-vault-fill) 50%,transparent)}.stage-list li>b{color:var(--qh-color-gold);font-variant-numeric:tabular-nums}.stage-list li>strong{font-size:10px}.stage-list li>span{color:var(--qh-color-jade);font-size:9px}.stage-list li>p{margin:0;color:var(--qh-text-muted);font-size:10px;line-height:1.45}
.evidence-row{margin:6px 0;border-left:2px solid var(--qh-color-jade)}.evidence-row blockquote{margin:7px 0;padding-left:8px;border-left:1px solid var(--qh-color-gold);font-size:12px;line-height:1.5}.evidence-meta{display:grid;gap:3px;color:var(--qh-text-muted);font-size:10px}.evidence-row>small{display:block;margin-top:6px}
.dimension-ledger{margin:7px 0}.dimension-ledger>header b{font-size:10px}.is-pending{color:var(--qh-color-gold)}.is-scored{color:var(--qh-color-jade)}.dimension-ledger>header>span{font-size:11px}.matrix-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:4px;margin-top:8px}.matrix-grid>div{display:grid;gap:4px;padding:6px;background:color-mix(in srgb,var(--qh-text-muted) 8%,transparent);font-size:9px}.matrix-grid>div.collected{background:color-mix(in srgb,var(--qh-color-jade) 12%,transparent)}.matrix-grid span{line-height:1.3}.matrix-grid b{color:var(--qh-color-gold);font-size:9px}.matrix-grid .collected b,.matrix-grid small{color:var(--qh-color-jade)}.pending-reason{margin:7px 0 0;color:var(--qh-color-gold)!important;font-size:10px!important}.score-ledger{margin-top:8px;padding-top:6px;border-top:1px solid var(--qh-color-jade-line)}.score-ledger>summary{cursor:pointer;color:var(--qh-color-jade);font-size:10px}.score-input-row{padding:6px 0;border-bottom:1px solid color-mix(in srgb,var(--qh-color-jade-line) 30%,transparent)}.score-input-row strong,.score-output b{font-size:10px}.score-input-row p,.score-input-row small,.score-output{margin:4px 0;color:var(--qh-text-muted);font-size:10px;line-height:1.5}.score-input-row .conflict,.conflict{color:var(--qh-color-gold)}.formula{padding:7px;background:color-mix(in srgb,var(--qh-color-jade) 8%,transparent);font-size:10px}.confidence-row{margin:6px 0}.confidence-row strong{font-size:11px}.confidence-row>div{padding:4px 0;border-top:1px solid color-mix(in srgb,var(--qh-color-jade-line) 26%,transparent);color:var(--qh-text-muted);font-size:10px}.audit-empty{margin:0;padding:8px 0;color:var(--qh-text-muted);font-size:11px;line-height:1.5}
@media(max-width:680px){.scan-grid{grid-template-columns:1fr}.matrix-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.audit-section>summary{align-items:flex-start}.dimension-ledger>header{flex-wrap:wrap}}
</style>
