<template>
  <GlassCard class="jury-card" compact padding="sm">
    <div class="jury-card__head">
      <div>
        <span>{{ title }}</span>
        <small>{{ role }}</small>
      </div>
      <Badge :tone="pending ? 'muted' : 'gold'" :label="pending ? '待会诊' : '已会诊'" />
    </div>
    <p v-if="pending">暂无会诊评语。等待证据卡累计后，将以该视角生成评分与建议。</p>
    <div v-else class="jury-card__entries">
      <p v-for="entry in entries" :key="`${title}-${entry.dimension}`">
        <strong>{{ entry.dimension }}</strong>
        <template v-if="entry.score !== null"> {{ Math.round(entry.score) }} 分</template>
        ：{{ entry.comment }}
      </p>
    </div>
  </GlassCard>
</template>

<script setup lang="ts">
import Badge from '../ui/Badge.vue'
import GlassCard from '../ui/GlassCard.vue'

defineProps<{
  title: string
  role: string
  pending: boolean
  entries: Array<{
    dimension: string
    score: number | null
    comment: string
  }>
}>()
</script>

<style scoped lang="scss">
.jury-card {
  display: grid;
  gap: 12px;
}

.jury-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.jury-card__head div {
  display: grid;
  gap: 3px;
}

.jury-card__head span {
  font-size: 16px;
  font-weight: 900;
}

small,
p {
  color: var(--qh-text-muted);
  font-size: 13px;
  line-height: 1.75;
}

p {
  margin: 0;
}

.jury-card__entries {
  display: grid;
  gap: 8px;
}

strong {
  color: var(--qh-color-jade);
}
</style>
