<template>
  <article v-if="item.role === 'user'" class="msg msg--user">
    <p class="msg-user-text">{{ userText }}</p>
  </article>

  <article v-else class="msg msg--assistant">
    <span class="msg-rail" aria-hidden="true" />
    <div class="msg-body">
      <p class="msg-author">小岐</p>

      <div v-if="steps.length" class="msg-tools">
        <ToolRow
          v-for="step in steps"
          :key="step.call_id"
          :step="step"
          :step-index="progressOf(step.call_id)?.index"
          :total-steps="progressOf(step.call_id)?.total"
          :step-label="progressOf(step.call_id)?.label"
        />
      </div>

      <p v-if="text" class="msg-text">{{ text }}<span v-if="item.streaming" class="msg-caret" aria-hidden="true" /></p>

      <!-- 定位类工具给的动作按钮（「在地图上看麻黄」）。不自动跳页：
           用户可能只是想听一句解释，把他拽到地图上等于替他决定。 -->
      <div v-if="actions.length" class="msg-actions">
        <button
          v-for="action in actions"
          :key="action.url"
          type="button"
          class="msg-action"
          @click="emit('action', action.url)"
        >
          {{ action.label }}
        </button>
      </div>

      <PlanCard
        v-if="item.content.type === 'plan'"
        :plan="item.content.plan"
        :confirmed="item.content.confirmed"
        :busy="planBusy"
        @confirm="(plan) => emit('confirm-plan', item.content.type === 'plan' ? item.content.taskId : '', plan)"
      />

      <ArtifactCard
        v-if="item.content.type === 'artifact'"
        :kind="item.content.kind"
        :title="item.content.title"
        :open-url="item.content.openUrl"
        :resource-id="item.content.resourceId"
        :verify="item.content.verify"
        :state="item.content.openUrl ? 'done' : 'accepted'"
      />

      <div v-if="item.content.type === 'error'" class="msg-error-row">
        <p class="msg-error">{{ item.content.text }}</p>
        <button class="msg-retry" type="button" @click="emit('retry')">重试</button>
      </div>
    </div>
  </article>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import ArtifactCard from './ArtifactCard.vue'
import PlanCard from './PlanCard.vue'
import ToolRow from './ToolRow.vue'
import type { ChatItem, StepProgress, ToolStep } from '../../types/assistant'
import type { ResourcePlan } from '../../types/resource'

const props = defineProps<{
  item: ChatItem
  planBusy?: boolean
  progress?: Record<string, StepProgress>
}>()

const emit = defineEmits<{
  'confirm-plan': [taskId: string, plan: ResourcePlan]
  retry: []
  action: [url: string]
}>()

const progressOf = (callId: string) => props.progress?.[callId]

const userText = computed(() => {
  const content = props.item.content
  if (content.type === 'text' || content.type === 'error') return content.text
  return ''
})

const text = computed(() => {
  const content = props.item.content
  if (content.type === 'text' || content.type === 'error') return content.text
  return ''
})

const actions = computed(() => {
  const content = props.item.content
  return content.type === 'text' ? content.ui_actions ?? [] : []
})

const steps = computed<ToolStep[]>(() => {
  if (props.item.steps?.length) return props.item.steps
  const content = props.item.content
  if (content.type === 'text' || content.type === 'tool' || content.type === 'error') return content.steps || []
  return []
})
</script>

<style scoped lang="scss">
@use '../../styles/tokens.scss' as *;

.msg {
  display: flex;
  gap: 12px;
}

.msg--user {
  justify-content: flex-end;
}

.msg-user-text {
  max-width: 80%;
  margin: 0;
  padding: 10px 14px;
  border: 1px solid var(--qh-border);
  border-radius: 10px 10px 2px 10px;
  background: var(--qh-user-surface, var(--qh-surface-raised));
  color: var(--qh-text);
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}

.msg--assistant {
  align-items: stretch;
}

/* 助手消息不用头像气泡：一条竖线就是它的身份，长回答也因此不会缩进成窄条。 */
.msg-rail {
  flex: 0 0 3px;
  border-radius: 2px;
  background: linear-gradient(var(--qh-color-jade-line), transparent);
}

.msg-body {
  display: grid;
  min-width: 0;
  flex: 1;
  gap: 10px;
}

.msg-author {
  margin: 0;
  color: var(--qh-text-muted);
  font-family: var(--qh-font-display);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.08em;
}

.msg-tools {
  display: grid;
  gap: 6px;
}

.msg-text {
  margin: 0;
  color: var(--qh-text);
  font-size: 15px;
  line-height: 1.85;
  white-space: pre-wrap;
  word-break: break-word;
}

.msg-caret {
  display: inline-block;
  width: 2px;
  height: 15px;
  margin-left: 2px;
  vertical-align: -2px;
  background: var(--qh-accent);
  animation: blink 1s steps(2, start) infinite;
}

.msg-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}

.msg-action {
  padding: 5px 12px;
  border: 1px solid color-mix(in srgb, var(--qh-color-jade) 45%, transparent);
  border-radius: 999px;
  background: color-mix(in srgb, var(--qh-color-jade) 8%, transparent);
  color: var(--qh-color-jade);
  font-size: 12.5px;
  cursor: pointer;

  &:hover,
  &:focus-visible {
    background: color-mix(in srgb, var(--qh-color-jade) 18%, transparent);
    outline: none;
  }
}

.msg-error-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.msg-error {
  flex: 1;
  margin: 0;
  padding: 8px 12px;
  border-left: 2px solid var(--qh-color-gold);
  color: var(--qh-color-gold-readable);
  font-size: 13px;
}

.msg-retry {
  flex: 0 0 auto;
  padding: 5px 12px;
  border: 1px solid var(--qh-border);
  border-radius: 999px;
  background: transparent;
  color: var(--qh-text);
  cursor: pointer;
  font-size: 12px;
}

.msg-retry:hover {
  border-color: var(--qh-accent-line);
}

@keyframes blink {
  50% {
    opacity: 0;
  }
}

@media (prefers-reduced-motion: reduce) {
  .msg-caret {
    animation: none;
  }
}
</style>
