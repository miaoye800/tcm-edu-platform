<template>
  <section class="rx-view">
    <header class="rx-hero">
      <div>
        <p class="eyebrow">图谱 · 处方审查</p>
        <h2>这张方子能不能开</h2>
        <p>
          十八反、十九畏、妊娠禁忌走的是图谱 T1 硬约束边，剂量与毒性读教材【用法用量】，
          对症走「药证链」。每条结论都带教材原文与页码——不是模型说了算。
        </p>
      </div>
    </header>

    <GlassCard padding="lg">
      <div class="field-grid">
        <label>
          <span class="field-label">病例（可选）</span>
          <el-input
            v-model="caseText"
            type="textarea"
            :rows="3"
            placeholder="如：妊娠三个月，恶心呕吐。留空则只查配伍与剂量。"
          />
        </label>
        <label>
          <span class="field-label">处方</span>
          <el-input
            v-model="prescription"
            type="textarea"
            :rows="4"
            placeholder="如：人参9g，藜芦3g"
          />
        </label>
      </div>

      <div class="action-row">
        <el-button type="primary" :loading="loading" :disabled="!prescription.trim()" @click="run()">
          开始审查
        </el-button>
        <span class="action-note">同步规则判定，通常 1 秒内返回</span>
      </div>

      <div class="quick-pairs">
        <span>试试：</span>
        <button v-for="item in PRESETS" :key="item.label" @click="usePreset(item)">
          {{ item.label }}
        </button>
      </div>
    </GlassCard>

    <el-alert v-if="error" type="warning" :title="error" show-icon :closable="false" class="rx-alert" />

    <template v-if="result">
      <!-- 结论 -->
      <GlassCard padding="lg" :class="['verdict-card', `is-${result.verdict}`]">
        <div class="verdict-head">
          <div>
            <p class="eyebrow" :class="result.verdict">审查结论</p>
            <h3>{{ VERDICT[result.verdict].title }}</h3>
            <p class="verdict-note">{{ VERDICT[result.verdict].note }}</p>
          </div>
          <Badge :label="`${result.findings.length} 条结论`" :tone="verdictTone" />
        </div>

        <div class="signal-row">
          <div v-for="item in signals" :key="item.label" class="signal" :class="`is-${item.tone}`">
            <span class="signal-label">{{ item.label }}</span>
            <b>{{ item.value }}</b>
            <small>{{ item.note }}</small>
          </div>
        </div>
      </GlassCard>

      <!-- 结论清单 -->
      <GlassCard v-if="visibleFindings.length" padding="lg">
        <div class="panel-head">
          <div>
            <p class="eyebrow">Findings</p>
            <h3>逐条判定</h3>
          </div>
          <span class="head-note">按严重度排序，最要命的排第一</span>
        </div>

        <article
          v-for="(item, index) in visibleFindings"
          :key="`${item.kind}-${index}`"
          class="finding"
          :class="`is-${item.severity}`"
        >
          <div class="finding-head">
            <Badge :label="SEVERITY[item.severity]" :tone="item.severity === 'block' ? 'coral' : item.severity === 'warn' ? 'gold' : 'muted'" />
            <b>{{ kindLabel(item.kind) }}</b>
            <code v-if="item.quote">{{ item.quote }}</code>
          </div>
          <p class="finding-reason">{{ item.reason }}</p>

          <blockquote v-for="(evidence, ev) in item.evidence" :key="ev" class="finding-evidence">
            <p>{{ evidence.quote }}</p>
            <small>{{ evidence.ref }}{{ pageSuffix(evidence) }}</small>
          </blockquote>

          <div class="finding-foot">
            <span class="rule">{{ item.rule }}</span>
            <span v-if="item.edge_ids.length" class="edge">图上那条边 #{{ item.edge_ids.join(' / #') }}</span>
          </div>
        </article>
      </GlassCard>

      <!-- 药味 -->
      <GlassCard v-if="result.herbs.length" padding="lg">
        <div class="panel-head">
          <div>
            <p class="eyebrow">Normalized</p>
            <h3>认出来的药</h3>
          </div>
          <span class="head-note">写了多少 vs 教材允许多少</span>
        </div>

        <div class="herb-table">
          <div class="herb-head">
            <span>药名</span>
            <span>处方写法</span>
            <span>用量</span>
            <span>教材区间</span>
            <span>判定</span>
          </div>
          <div v-for="herb in result.herbs" :key="herb.name" class="herb-row">
            <span class="herb-name">
              {{ herb.name }}
              <em v-if="!herb.has_monograph" class="ghost">无条目</em>
            </span>
            <span class="herb-raw">{{ herb.raw_name }}</span>
            <span>{{ herb.dose_g === null ? '未写' : `${herb.dose_g}g` }}</span>
            <span>{{ rangeText(herb) }}</span>
            <span class="dose-status" :class="`is-${herb.dose_status}`">{{ DOSE_STATUS[herb.dose_status] }}</span>
          </div>
        </div>
      </GlassCard>

      <!-- 对症追溯 -->
      <GlassCard padding="lg">
        <div class="panel-head">
          <div>
            <p class="eyebrow">Trace</p>
            <h3>对症是怎么判的</h3>
          </div>
          <span class="head-note">{{ layerLabel }}</span>
        </div>

        <p class="trace-line">{{ traceNote }}</p>

        <div v-if="result.trace.candidates?.length" class="trace-block">
          <span class="trace-title">病例被映射到的证候</span>
          <div class="chip-row">
            <span v-for="item in result.trace.candidates" :key="item.name" class="chip">
              {{ item.name }}
              <small>{{ item.layer }}</small>
            </span>
          </div>
        </div>

        <div v-if="result.trace.edges?.length" class="trace-block">
          <span class="trace-title">凭的是这些教材边</span>
          <blockquote v-for="edge in result.trace.edges" :key="edge.edge_id" class="finding-evidence">
            <p>{{ edge.quote }}</p>
            <small>{{ edge.herb }} → {{ edge.syndrome }} · {{ edge.ref }}</small>
          </blockquote>
        </div>

        <div v-if="result.trace.syndrome_alternatives?.length" class="trace-block">
          <span class="trace-title">教材里真正主治该证的药</span>
          <div v-for="item in result.trace.syndrome_alternatives" :key="item.syndrome" class="alt-row">
            <b>{{ item.syndrome }}</b>
            <span>{{ item.herbs.join('、') }}</span>
          </div>
        </div>
      </GlassCard>
    </template>

    <GlassCard v-else-if="!loading" padding="lg">
      <p class="empty-line">写下处方开始审查。想先看效果，点上面「试试」里任意一条。</p>
    </GlassCard>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import Badge from '../components/ui/Badge.vue'
import GlassCard from '../components/ui/GlassCard.vue'
import { reviewPrescription } from '../services/kgApi'
import type { PrescriptionReviewResult, ReviewedHerb, ReviewFinding } from '../types/kg'

/** 预设只是**入口快捷方式**，判定全走接口，前端不算任何医学结论。 */
const PRESETS: { label: string; case: string; prescription: string }[] = [
  { label: '人参 + 藜芦（十八反）', case: '', prescription: '人参9g，藜芦3g' },
  { label: '甘草 + 甘遂（十八反）', case: '', prescription: '甘草9g，甘遂3g' },
  { label: '妊娠用桃仁红花（慎用）', case: '妊娠三个月，跌仆损伤', prescription: '桃仁9g，红花6g' },
  { label: '金银花 + 连翘（正常方）', case: '咽喉肿痛，发热', prescription: '金银花15g，连翘15g' },
]

const VERDICT = {
  block: { title: '不可开方', note: '存在绝对禁忌：十八反/十九畏、妊娠禁用或毒性误用。' },
  warn: { title: '需复核', note: '没有绝对禁忌，但有剂量、证候或未校验项需要人看。' },
  pass: { title: '未见问题', note: '已做的检查全部通过。未覆盖的项会在下方如实标出。' },
} as const

const SEVERITY: Record<ReviewFinding['severity'], string> = {
  block: '阻断',
  warn: '提醒',
  info: '说明',
}

const DOSE_STATUS: Record<ReviewedHerb['dose_status'], string> = {
  ok: '在范围内',
  above: '超出上限',
  below: '低于下限',
  missing: '未写剂量',
  unchecked: '无法校验',
}

/** 结论类型 → 中文。后端返回的是稳定英文码，这里是唯一一处翻译。 */
const KIND_LABELS: Record<string, string> = {
  incompatible_pair: '配伍禁忌',
  pregnancy_forbidden: '妊娠禁用',
  pregnancy_caution: '妊娠慎用',
  toxic_misuse: '毒性误用',
  dose_abnormal: '剂量异常',
  toxicity_unknown: '毒性未标注',
  herb_not_structured: '尚未结构化',
  no_recognized_herb: '未识别出药味',
  case_unresolved: '证候未定位',
  case_mismatch: '证候不匹配',
  unrelated_herb: '与病例无关',
  herb_normalized: '药名归一',
}

const LAYER_LABELS: Record<string, string> = {
  syndrome: '证候层 · 教材药证链',
  effect_overlap: '功效层 · 教材数据推导',
  effect_tag: '功效层 · 词表兜底',
}

const caseText = ref('')
const prescription = ref('')
const loading = ref(false)
const error = ref('')
const result = ref<PrescriptionReviewResult | null>(null)

const kindLabel = (kind: string) => KIND_LABELS[kind] ?? kind

const verdictTone = computed(() => {
  if (result.value?.verdict === 'block') return 'coral' as const
  if (result.value?.verdict === 'warn') return 'gold' as const
  return 'jade' as const
})

/** info 级是「药名归一」这类流水账，默认折叠，否则真正的结论会被淹掉。 */
const visibleFindings = computed(() =>
  (result.value?.findings ?? []).filter((item) => item.severity !== 'info'),
)

const signals = computed(() => {
  const payload = result.value
  if (!payload) return []
  // 后端给的是三态（pass / fail / unknown）。**不能退化成布尔**：
  // 草乌在图谱里只有身份没有性味功效，剂量和毒性根本没查，
  // 用 true 表示就成了绿色的「合规」「未发现」——把「不知道」说成「没问题」。
  const checks = payload.checks ?? {}
  const tri = (key: 'compatible' | 'dosage' | 'toxicity', fallback: string): string =>
    (checks[key] as string | undefined) ?? fallback
  const dosage = tri('dosage', payload.dosage_ok ? 'pass' : 'fail')
  const toxicity = tri('toxicity', payload.toxic_misuse ? 'fail' : 'pass')
  const compatible = tri('compatible', payload.compatible ? 'pass' : 'fail')
  const symptom = payload.symptom_match_status ?? (payload.symptom_match ? 'matched' : 'uncertain')

  return [
    {
      label: '配伍',
      value: compatible === 'pass' ? '无冲突' : compatible === 'fail' ? '有禁忌' : '未校验',
      tone: compatible === 'pass' ? 'ok' : compatible === 'fail' ? 'bad' : 'neutral',
      note:
        compatible === 'pass'
          ? 'T1 边全部通过'
          : compatible === 'fail'
            ? '见下方配伍禁忌'
            : '处方里有认不出的药名，这一对没查',
    },
    {
      label: '对症',
      value: symptom === 'matched' ? '对症' : symptom === 'uncertain' ? '未知' : '不对症',
      tone: symptom === 'matched' ? 'ok' : symptom === 'uncertain' ? 'neutral' : 'warn',
      note:
        symptom === 'matched'
          ? '证候或功效层命中'
          : symptom === 'uncertain'
            ? caseText.value.trim()
              ? '病例未定位到证候'
              : '未填病例，未做对症检查'
            : '有目标但药不对',
    },
    {
      label: '剂量',
      value: dosage === 'pass' ? '合规' : dosage === 'fail' ? '异常' : '未校验',
      tone: dosage === 'pass' ? 'ok' : dosage === 'fail' ? 'warn' : 'neutral',
      note:
        dosage === 'pass'
          ? '均在教材区间内'
          : dosage === 'fail'
            ? '有药超出教材区间'
            : unknownCoverageNote(payload, '剂量'),
    },
    {
      label: '毒性',
      value: toxicity === 'pass' ? '未发现' : toxicity === 'fail' ? '误用' : '未校验',
      tone: toxicity === 'pass' ? 'ok' : toxicity === 'fail' ? 'bad' : 'neutral',
      note:
        toxicity === 'pass'
          ? '未见毒性药超量或漏写剂量'
          : toxicity === 'fail'
            ? '毒性药无安全剂量'
            : unknownCoverageNote(payload, '毒性'),
    },
  ]
})

/** 说清楚「为什么没校验」——是这味药没条目，还是教材没标毒性。 */
function unknownCoverageNote(payload: PrescriptionReviewResult, field: string): string {
  const missing = (payload.herbs ?? []).filter((item) => !item.has_monograph).map((item) => item.name)
  if (missing.length) return `${missing.join('、')} 无结构化条目，未校验${field}`
  if (field === '毒性') return '教材未标毒性，未做毒性校验'
  return '处方未写克数，无法比对区间'
}

const layerLabel = computed(() => {
  const layer = result.value?.trace?.layer
  return layer ? LAYER_LABELS[layer] ?? layer : '未命中任何层'
})

const traceNote = computed(() => {
  const trace = result.value?.trace
  if (!trace) return ''
  if (trace.layer === 'syndrome') {
    return `病例被映射到「${trace.syndromes.map((item) => item.name).join('、') || '—'}」，处方里的 ${trace.matched_herbs.join('、')} 在教材里明确主治该证。这是最强的一层证据。`
  }
  if (trace.layer === 'effect_overlap') {
    const shared = Object.entries(trace.shared_effects ?? {})
      .map(([herb, effects]) => `${herb}（${effects.join('、')}）`)
      .join('；')
    return `没有药直接主治该证，但处方里的 ${shared} 与教材主治该证的药共享同一条功效项。属于间接对症，依据仍是教材数据，不是关键词。`
  }
  if (trace.layer === 'effect_tag') {
    return `只有功效词表命中（${(trace.effect_tags ?? []).join('、')}）。这是最弱的一层，仅供参考。`
  }
  return '病例文本没能映射到任何证候，也没有功效层命中。这不等于「不对症」，只是我们没看懂——请把症状写得更接近教材措辞。'
})

function rangeText(herb: ReviewedHerb) {
  if (!herb.dose_range) return '—'
  const [min, max] = herb.dose_range
  if (min === null && max === null) return '—'
  if (min !== null && max !== null) return `${min}~${max}g`
  return min !== null ? `≥${min}g` : `≤${max}g`
}

/**
 * 页码后缀。``ref`` 里已经写了 ``p128`` 时不再补一遍——同一页显示两次
 * 会让人以为引了两处不同的出处。
 */
function pageSuffix(evidence: { page: number | null; ref: string }) {
  if (!evidence.page) return ''
  return evidence.ref.includes(`p${evidence.page}`) ? '' : ` · 第 ${evidence.page} 页`
}

function usePreset(item: { case: string; prescription: string }) {
  caseText.value = item.case
  prescription.value = item.prescription
  void run()
}

async function run() {
  if (!prescription.value.trim()) return
  loading.value = true
  error.value = ''
  try {
    result.value = await reviewPrescription({
      case: caseText.value,
      prescription: prescription.value,
    })
  } catch (err) {
    error.value = err instanceof Error ? err.message : '审查失败'
    result.value = null
  } finally {
    loading.value = false
  }
}
</script>

<style scoped lang="scss">
@use "../styles/tokens.scss" as *;

.rx-view {
  display: grid;
  gap: 18px;
  width: min(1320px, 100%);
  margin: 0 auto;

  // 「提醒」这一档的黄色。必须用 --qh-color-gold 而不是 ochre-300：
  // 前者在浅色主题下被覆写成 #8a6f3a，后者在米白底上几乎看不见，
  // 而这一档恰恰是「剂量超出范围」的提示。
  --rx-warn: var(--qh-color-gold);
}

.rx-hero {
  @include glass-panel;
  padding: 24px;
}

.eyebrow {
  margin: 0 0 8px;
  color: var(--qh-color-jade);
  font-size: 13px;
  font-weight: 900;

  &.block {
    color: var(--qh-accent);
  }

  &.warn {
    color: var(--rx-warn);
  }
}

h2,
h3 {
  margin: 0;
  font-family: var(--qh-font-display);
  font-weight: 900;
}

h2 {
  @include text-sheen;
  font-size: clamp(34px, 4.6vw, 56px);
}

h3 {
  font-size: 22px;
}

.rx-hero p {
  margin: 10px 0 0;
  color: var(--qh-text-muted);
  line-height: 1.7;
}

.rx-alert {
  border-radius: 16px;
}

.field-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.2fr);
  gap: 14px;

  @media (max-width: 880px) {
    grid-template-columns: minmax(0, 1fr);
  }
}

.field-label {
  display: block;
  margin-bottom: 8px;
  color: var(--qh-fg-soft);
  font-size: 13px;
  font-weight: 900;
}

.action-row {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  margin-top: 16px;
}

.action-note {
  color: var(--qh-text-muted);
  font-size: 13px;
}

.quick-pairs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-top: 14px;
  color: var(--qh-text-muted);
  font-size: 13px;

  button {
    padding: 5px 11px;
    border: 1px solid var(--qh-line);
    border-radius: var(--qh-radius-pill);
    background: transparent;
    color: var(--qh-fg-soft);
    font-size: 13px;
    cursor: pointer;

    &:hover {
      border-color: var(--qh-color-jade);
    }
  }
}

.verdict-card {
  border: 1px solid var(--qh-line);

  &.is-block {
    border-color: var(--qh-accent-line);
    background: var(--qh-accent-soft);
  }

  &.is-warn {
    border-color: rgba(176, 154, 110, 0.42);
  }
}

.verdict-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 14px;
}

.verdict-note {
  margin: 8px 0 0;
  color: var(--qh-text-muted);
  line-height: 1.7;
}

.signal-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px;
  margin-top: 18px;
}

.signal {
  display: grid;
  gap: 4px;
  padding: 12px 14px;
  border: 1px solid var(--qh-line);
  border-radius: 14px;
  background: var(--qh-surface-sunken);

  .signal-label {
    color: var(--qh-text-muted);
    font-size: 12px;
    font-weight: 900;
  }

  b {
    font-family: var(--qh-font-display);
    font-size: 20px;
  }

  small {
    color: var(--qh-text-muted);
    font-size: 12px;
    line-height: 1.5;
  }

  &.is-ok b {
    color: var(--qh-positive);
  }

  &.is-warn b {
    color: var(--rx-warn);
  }

  &.is-bad b {
    color: var(--qh-accent);
  }

  &.is-neutral b {
    color: var(--qh-fg-soft);
  }
}

.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 14px;
  margin-bottom: 14px;
}

.head-note {
  color: var(--qh-text-muted);
  font-size: 13px;
}

.finding {
  padding: 14px 16px;
  border: 1px solid var(--qh-line);
  border-left-width: 3px;
  border-radius: 0 14px 14px 0;
  background: var(--qh-surface-sunken);

  & + .finding {
    margin-top: 12px;
  }

  &.is-block {
    border-left-color: var(--qh-accent);
  }

  &.is-warn {
    border-left-color: var(--qh-color-gold);
  }

  &.is-info {
    border-left-color: var(--qh-line-strong);
  }
}

.finding-head {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;

  b {
    font-size: 15px;
  }

  code {
    padding: 2px 8px;
    border-radius: 8px;
    background: var(--qh-surface-hover);
    color: var(--qh-fg-soft);
    font-family: var(--qh-font-mono, monospace);
    font-size: 12px;
  }
}

.finding-reason {
  margin: 10px 0 0;
  color: var(--qh-text);
  line-height: 1.7;
}

.finding-evidence {
  margin: 10px 0 0;
  padding: 12px 14px;
  border-left: 2px solid var(--qh-line-strong);
  border-radius: 0 12px 12px 0;
  background: var(--qh-surface-hover);

  p {
    margin: 0;
    color: var(--qh-fg-soft);
    line-height: 1.75;
  }

  small {
    display: block;
    margin-top: 6px;
    color: var(--qh-text-muted);
    font-size: 12px;
  }
}

.finding-foot {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 10px;
  color: var(--qh-fg-faint);
  font-size: 12px;
}

.herb-table {
  display: grid;
  gap: 6px;
}

.herb-head,
.herb-row {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(0, 1.1fr) minmax(0, 0.8fr) minmax(0, 1fr) minmax(0, 0.9fr);
  gap: 10px;
  align-items: center;
  padding: 8px 12px;
}

.herb-head {
  color: var(--qh-text-muted);
  font-size: 12px;
  font-weight: 900;
}

.herb-row {
  border: 1px solid var(--qh-line);
  border-radius: 12px;
  background: var(--qh-surface-sunken);
  font-size: 14px;
}

.herb-name {
  display: flex;
  gap: 6px;
  align-items: center;
  font-weight: 900;
}

.ghost {
  padding: 1px 6px;
  border-radius: var(--qh-radius-pill);
  background: var(--qh-surface-hover);
  color: var(--qh-text-muted);
  font-size: 11px;
  font-style: normal;
  font-weight: 700;
}

.herb-raw {
  color: var(--qh-text-muted);
}

.dose-status {
  font-size: 13px;
  font-weight: 900;

  &.is-ok {
    color: var(--qh-positive);
  }

  &.is-above,
  &.is-below {
    color: var(--qh-accent);
  }

  &.is-missing,
  &.is-unchecked {
    color: var(--rx-warn);
  }
}

.trace-line {
  margin: 0;
  color: var(--qh-text-muted);
  line-height: 1.8;
}

.trace-block {
  margin-top: 16px;
}

.trace-title {
  display: block;
  margin-bottom: 8px;
  color: var(--qh-fg-soft);
  font-size: 13px;
  font-weight: 900;
}

.chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.chip {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  padding: 5px 11px;
  border: 1px solid var(--qh-line);
  border-radius: var(--qh-radius-pill);
  color: var(--qh-fg-soft);
  font-size: 13px;

  small {
    color: var(--qh-text-muted);
    font-size: 11px;
  }
}

.alt-row {
  display: flex;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px dashed var(--qh-line);
  font-size: 14px;

  b {
    flex: 0 0 auto;
  }

  span {
    color: var(--qh-text-muted);
  }
}

.empty-line {
  margin: 0;
  color: var(--qh-text-muted);
  line-height: 1.8;
}
</style>
