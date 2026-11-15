<template>
  <div class="dim-row" :class="{ 'is-low': axis.lowConfidence }">
    <div class="dim-row__head">
      <div>
        <span>{{ axis.sourceLabel }}</span>
        <small>{{ axis.metric }} · {{ axis.raw }}</small>
      </div>
      <div class="dim-row__badges">
        <Badge :tone="axis.confidenceStatus === '已取证' ? 'jade' : 'muted'" :label="axis.confidenceStatus" />
        <span v-if="axis.pending_verification" class="pending-verification">待验证</span>
      </div>
    </div>
    <p>{{ axis.anchor }}</p>
    <div class="dim-row__bar" aria-hidden="true">
      <span :style="{ width: `${Math.round(axis.confidence * 100)}%` }" />
    </div>
  </div>
</template>

<script setup lang="ts">
import type { RadarAxis } from '../../types/profile'
import Badge from '../ui/Badge.vue'

defineProps<{
  axis: RadarAxis
}>()
</script>

<style scoped lang="scss">
.dim-row {
  display: grid;
  gap: 9px;
  padding: 12px;
  border: 1px solid var(--qh-line);
  border-radius: 14px;
  background: var(--qh-line);
}

.dim-row__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.dim-row__head div {
  display: grid;
  min-width: 0;
  gap: 4px;
}

.dim-row__badges { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 6px; }
.pending-verification { padding: 3px 7px; border: 1px solid var(--qh-color-gold); border-radius: var(--qh-radius-pill); color: var(--qh-color-gold); font-size: 11px; font-weight: 900; }

.dim-row__head span {
  color: var(--qh-text);
  font-size: 14px;
  font-weight: 900;
}

small,
p {
  color: var(--qh-text-muted);
  font-size: 12px;
  line-height: 1.55;
}

p {
  margin: 0;
}

.dim-row__bar {
  height: 7px;
  overflow: hidden;
  border-radius: 999px;
  background: var(--qh-line);
}

.dim-row__bar span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, var(--qh-color-jade-d), var(--qh-color-gold));
}

.is-low .dim-row__bar span {
  background: linear-gradient(90deg, var(--qh-fg-muted), var(--qh-line-strong));
}
</style>
