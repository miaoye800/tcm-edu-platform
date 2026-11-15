<template>
  <section class="plan-card">
    <header class="plan-head">
      <span class="plan-tag">{{ confirmed ? '已确认' : '待确认' }}</span>
      <input v-model="draft.title" class="plan-title" :disabled="confirmed || busy" aria-label="方案标题" />
      <span class="plan-meta">{{ unitsLabel }}</span>
    </header>

    <p v-if="draft.goal" class="plan-goal">{{ draft.goal }}</p>

    <ol class="plan-units">
      <li v-for="(unit, index) in draft.units" :key="index" class="plan-unit">
        <span class="plan-index">{{ index + 1 }}</span>
        <div class="plan-unit-body">
          <input v-model="unit.label" class="plan-unit-label" :disabled="confirmed || busy" :aria-label="`第 ${index + 1} 个单元标题`" />
          <textarea
            v-model="unit.detail"
            class="plan-unit-detail"
            rows="2"
            :disabled="confirmed || busy"
            :aria-label="`第 ${index + 1} 个单元要点`"
          />
        </div>
        <span class="plan-source" :data-source="unit.source || 'model'">{{ sourceLabel(unit.source) }}</span>
      </li>
    </ol>

    <p v-if="draft.gaps.length" class="plan-gaps">还没落实：{{ draft.gaps.slice(0, 3).join('；') }}{{ draft.gaps.length > 3 ? ' 等' : '' }}</p>

    <footer class="plan-foot">
      <button v-if="!confirmed" class="plan-confirm" type="button" :disabled="busy" @click="confirm">
        {{ busy ? '正在开炼…' : '确认，按这份方案生成' }}
      </button>
      <span class="plan-hint">{{ confirmed ? '已按这份方案开炼' : '改完再确认；不确认不会生成。' }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { ResourcePlan, ResourcePlanUnit } from '../../types/resource'

const props = defineProps<{
  plan: ResourcePlan
  confirmed?: boolean
  busy?: boolean
}>()

const emit = defineEmits<{ confirm: [plan: ResourcePlan] }>()

/** 深拷贝：用户改的是这份草稿，不直接改父级的方案对象。 */
const clone = (plan: ResourcePlan): ResourcePlan => JSON.parse(JSON.stringify(plan)) as ResourcePlan

const draft = ref<ResourcePlan>(clone(props.plan))
watch(
  () => props.plan,
  (next) => {
    draft.value = clone(next)
  },
  { deep: true },
)

const unitsLabel = computed(() => {
  const kind = draft.value.units.length ? '个单元' : ''
  return `${draft.value.units.length} ${kind}`.trim()
})

const sourceLabel = (source?: ResourcePlanUnit['source']) => {
  if (source === 'knowledge_base') return '教材'
  if (source === 'user_material') return '你的材料'
  return '模型补充'
}

const confirm = () => {
  if (props.busy) return
  emit('confirm', clone(draft.value))
}
</script>

<style scoped lang="scss">
@use '../../styles/tokens.scss' as *;

.plan-card {
  display: grid;
  gap: 12px;
  padding: 16px;
  border: 1px solid var(--qh-accent-line);
  border-radius: var(--qh-radius-stage);
  background: var(--qh-surface);
}

.plan-head {
  display: flex;
  align-items: center;
  gap: 10px;
}

.plan-tag {
  flex: 0 0 auto;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--qh-accent-soft);
  color: var(--qh-accent);
  font-size: 12px;
  font-weight: 700;
}

.plan-title {
  flex: 1;
  min-width: 0;
  border: 0;
  border-bottom: 1px dashed transparent;
  background: transparent;
  color: var(--qh-text);
  font-family: var(--qh-font-reading);
  font-size: 17px;
  font-weight: 700;
}

.plan-title:focus {
  border-bottom-color: var(--qh-accent-line);
  outline: none;
}

.plan-meta {
  flex: 0 0 auto;
  color: var(--qh-text-muted);
  font-size: 12px;
}

.plan-goal {
  margin: 0;
  color: var(--qh-text-muted);
  font-size: 13px;
  line-height: 1.7;
}

.plan-units {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.plan-unit {
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr) auto;
  gap: 10px;
  padding: 8px 10px;
  border: 1px solid var(--qh-border);
  border-radius: 4px;
  background: var(--qh-surface-raised);
}

.plan-index {
  color: var(--qh-text-muted);
  font-size: 12px;
  line-height: 26px;
  text-align: center;
  font-variant-numeric: tabular-nums;
}

.plan-unit-body {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.plan-unit-label,
.plan-unit-detail {
  width: 100%;
  border: 0;
  border-bottom: 1px dashed transparent;
  background: transparent;
  color: var(--qh-text);
  font-family: var(--qh-font-body);
  resize: vertical;
}

.plan-unit-label {
  font-size: 14px;
  font-weight: 700;
}

.plan-unit-detail {
  color: var(--qh-text-muted);
  font-size: 12px;
  line-height: 1.6;
}

.plan-unit-label:focus,
.plan-unit-detail:focus {
  border-bottom-color: var(--qh-accent-line);
  outline: none;
}

.plan-source {
  align-self: start;
  padding: 1px 6px;
  border: 1px solid var(--qh-border);
  border-radius: 999px;
  color: var(--qh-text-muted);
  font-size: 11px;
  white-space: nowrap;
}

.plan-source[data-source='knowledge_base'] {
  border-color: var(--qh-color-jade-line);
  color: var(--qh-color-jade);
}

.plan-gaps {
  margin: 0;
  padding: 8px 10px;
  border-left: 2px solid var(--qh-color-gold);
  color: var(--qh-color-gold-readable);
  font-size: 12px;
  line-height: 1.6;
}

.plan-foot {
  display: flex;
  align-items: center;
  gap: 12px;
}

.plan-confirm {
  padding: 9px 16px;
  border: 1px solid var(--qh-accent);
  border-radius: 4px;
  background: var(--qh-accent);
  color: var(--qh-accent-ink);
  cursor: pointer;
  font-size: 13px;
  font-weight: 700;
}

.plan-confirm:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.plan-confirm:focus-visible {
  outline: 2px solid var(--qh-text);
  outline-offset: 2px;
}

.plan-hint {
  color: var(--qh-text-muted);
  font-size: 12px;
}
</style>
