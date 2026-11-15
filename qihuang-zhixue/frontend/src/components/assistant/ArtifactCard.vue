<template>
  <section class="artifact-card" :data-state="state">
    <div class="artifact-main">
      <span class="artifact-kind">{{ kindLabel }}</span>
      <div class="artifact-copy">
        <strong>{{ heading }}</strong>
        <span>{{ caption }}</span>
      </div>
    </div>
    <button v-if="openUrl" class="artifact-open" type="button" @click="open">打开</button>
    <span v-else class="artifact-pending">{{ state === 'failed' ? '没炼成' : '生成中' }}</span>
  </section>
  <!-- 依据情况就写在卡上。生成完才去点开才发现「全是模型补的」，那就晚了。 -->
  <p v-if="evidence" class="artifact-evidence" :data-grounded="grounded">{{ evidence }}</p>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'

const props = defineProps<{
  kind: string
  /** 真实标题：「解表药… · 试卷」。拿不到才退回「一份试卷」这种占位。 */
  title?: string
  openUrl?: string
  resourceId?: string
  verify?: Record<string, unknown>
  state?: 'accepted' | 'awaiting_plan' | 'done' | 'failed'
}>()

const router = useRouter()

const kindLabel = computed(() => {
  const map: Record<string, string> = { mindmap: '导图', ppt: '幻灯', doc: '文稿', quiz: '试卷', story: '漫剧', resource: '资源' }
  return map[props.kind] || '产物'
})

/** 把 verify 压成一句话。没有校验结果就不显示——空着比写着「依据：--」诚实。 */
const evidence = computed(() => {
  const verify = props.verify || {}
  const note = typeof verify.note === 'string' ? verify.note : ''
  const sources = Number(verify.sources || 0)
  if (note) return note
  if (sources > 0) return `依据 ${sources} 条教材条目`
  return ''
})

const grounded = computed(() => Boolean(props.verify && props.verify.grounded))

const heading = computed(() => props.title?.trim() || `一份${kindLabel.value}`)

const caption = computed(() => {
  if (props.state === 'failed') return '生成没走完，可以回到对话里重试'
  if (!props.openUrl) return '完成后这里会出现打开按钮'
  return '已入库，可编辑'
})

const open = () => {
  if (!props.openUrl) return
  // 后端给的是站内路径（`/resources/{id}`、`/mindmap?resource=…`），直接交给路由。
  void router.push(props.openUrl)
}
</script>

<style scoped lang="scss">
@use '../../styles/tokens.scss' as *;

.artifact-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px;
  border: 1px solid var(--qh-border);
  border-radius: var(--qh-radius-stage);
  background: var(--qh-surface);
}

.artifact-main {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.artifact-kind {
  flex: 0 0 auto;
  padding: 6px 10px;
  border: 1px solid var(--qh-color-jade-line);
  border-radius: 4px;
  color: var(--qh-color-jade);
  font-size: 12px;
  font-weight: 700;
}

.artifact-copy {
  display: grid;
  min-width: 0;
  gap: 2px;
}

.artifact-copy strong {
  font-size: 14px;
}

.artifact-copy span {
  color: var(--qh-text-muted);
  font-size: 12px;
}

.artifact-open {
  flex: 0 0 auto;
  padding: 7px 14px;
  border: 1px solid var(--qh-border);
  border-radius: 4px;
  background: var(--qh-button-secondary-fill);
  color: var(--qh-text);
  cursor: pointer;
  font-size: 13px;
  font-weight: 700;
}

.artifact-open:hover {
  border-color: var(--qh-accent-line);
}

.artifact-open:focus-visible {
  outline: 2px solid var(--qh-accent);
  outline-offset: 2px;
}

.artifact-pending {
  flex: 0 0 auto;
  color: var(--qh-text-muted);
  font-size: 12px;
}

[data-state='failed'] .artifact-pending {
  color: var(--qh-color-gold-readable);
}

.artifact-evidence {
  margin: 6px 0 0;
  color: var(--qh-text-muted);
  font-size: 11px;
}

.artifact-evidence[data-grounded='false'] {
  color: var(--qh-color-gold-readable);
}
</style>
