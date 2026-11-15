<template>
  <section class="tool-row" :data-state="step.state">
    <button class="tool-head" type="button" :aria-expanded="open" @click="open = !open">
      <span class="tool-dot" aria-hidden="true" />
      <span class="tool-name">{{ label }}</span>
      <span v-if="progress" class="tool-step">{{ progress }}</span>
      <span class="tool-summary">{{ step.summary || stateText }}</span>
      <span class="tool-caret" aria-hidden="true">{{ open ? '收起' : '详情' }}</span>
    </button>

    <div v-if="open" class="tool-body">
      <p v-if="step.error_code" class="tool-error">
        <strong>{{ step.error_code }}</strong>
        <span v-if="step.remedy">{{ step.remedy }}</span>
      </p>
      <pre v-if="argsText" class="tool-args">{{ argsText }}</pre>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ToolStep } from '../../types/assistant'

const props = defineProps<{
  step: ToolStep
  /** 由父级维护的工序：第几步 / 共几步 / 这一步叫什么。 */
  stepIndex?: number
  totalSteps?: number
  stepLabel?: string
}>()

const open = ref(false)

/**
 * 工具名给人看。
 *
 * 后端现在按能力声明里的 `title` 直接给中文名（「生成试卷」「排一份学习路线」），
 * 所以这里优先用 `step.title`。下面的映射表只兜历史数据——早先落库的 title
 * 存的是能力字段名（`plan.create`），那种不能用。
 */
const label = computed(() => {
  const map: Record<string, string> = {
    mindmap_generate: '生成导图',
    ppt_generate: '生成幻灯',
    doc_generate: '生成文稿',
    kg_ask: '查图谱关系',
    kg_compare: '对比两味药',
    kg_lookup: '对实体',
    kg_path: '找关系链',
    kg_review: '审处方',
    kg_attribute: '错题归因',
  }
  const title = props.step.title || ''
  // 能力字段名长这样：小写、带点。它不是给人看的。
  const isFieldName = /^[a-z_]+\.[a-z_]+$/.test(title)
  if (title && !isFieldName) return title
  return map[props.step.tool] || props.step.tool
})

const stateText = computed(() => {
  if (props.step.state === 'running') return '进行中'
  if (props.step.state === 'failed') return '没走通'
  return '完成'
})

const progress = computed(() => {
  const total = props.totalSteps || 0
  if (!total || props.step.state !== 'running') return ''
  return `${props.stepLabel || '推进'} ${Math.min((props.stepIndex || 0) + 1, total)}/${total}`
})

const argsText = computed(() => {
  const args = props.step.args || {}
  const keys = Object.keys(args)
  if (!keys.length) return ''
  try {
    return JSON.stringify(args, null, 2)
  } catch {
    return ''
  }
})
</script>

<style scoped lang="scss">
@use '../../styles/tokens.scss' as *;

.tool-row {
  border: 1px solid var(--qh-border);
  border-radius: var(--qh-radius-stage);
  background: var(--qh-surface);
  overflow: hidden;
}

.tool-head {
  display: grid;
  grid-template-columns: 10px auto auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 9px 12px;
  border: 0;
  background: transparent;
  color: var(--qh-text);
  cursor: pointer;
  text-align: left;
  font-size: 13px;
}

.tool-head:focus-visible {
  outline: 2px solid var(--qh-accent);
  outline-offset: -2px;
}

.tool-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--qh-text-muted);
}

[data-state='running'] .tool-dot {
  background: var(--qh-accent);
  animation: pulse 1.4s ease-in-out infinite;
}

[data-state='ok'] .tool-dot {
  background: var(--qh-color-jade);
}

[data-state='failed'] .tool-dot {
  background: var(--qh-color-gold);
}

.tool-name {
  font-weight: 700;
}

.tool-step,
.tool-summary {
  min-width: 0;
  overflow: hidden;
  color: var(--qh-text-muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tool-step {
  font-variant-numeric: tabular-nums;
}

.tool-caret {
  color: var(--qh-text-muted);
  font-size: 12px;
}

.tool-body {
  display: grid;
  gap: 8px;
  padding: 0 12px 12px 32px;
}

.tool-error {
  display: grid;
  gap: 2px;
  margin: 0;
  color: var(--qh-color-gold-readable);
  font-size: 12px;
}

.tool-args {
  max-height: 220px;
  margin: 0;
  padding: 10px;
  overflow: auto;
  border-radius: 4px;
  background: var(--qh-surface-sunken);
  color: var(--qh-text-muted);
  font-family: var(--qh-font-body);
  font-size: 12px;
  line-height: 1.6;
}

@keyframes pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.35;
  }
}

@media (prefers-reduced-motion: reduce) {
  .tool-dot {
    animation: none;
  }
}
</style>
