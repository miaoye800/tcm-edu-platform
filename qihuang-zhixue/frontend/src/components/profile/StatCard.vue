<template>
  <GlassCard class="stat-card" compact padding="sm" :class="`is-${tone}`">
    <span class="stat-card__label">{{ label }}</span>
    <strong>{{ value }}</strong>
  </GlassCard>
</template>

<script setup lang="ts">
import GlassCard from '../ui/GlassCard.vue'

defineProps<{
  label: string
  value: string
  tone: 'jade' | 'blue' | 'gold' | 'coral'
}>()
</script>

<style scoped lang="scss">
.stat-card {
  min-height: 104px;
}

.stat-card__label {
  display: block;
  margin-bottom: 12px;
  color: var(--qh-text-muted);
  font-size: 13px;
  font-weight: 800;
}

strong {
  display: block;
  font-family: var(--qh-font-display);
  font-size: clamp(24px, 3.4vw, 34px);
  font-weight: 900;
  line-height: 1.05;
}

// 数字统一用前景色：四个 KPI 各给一种颜色会让版面变杂，层次应该靠字号和
// 明度，不靠色相。只有「今日到期」这种需要注意的指标才保留朱砂。
.is-jade strong,
.is-blue strong,
.is-gold strong {
  color: var(--qh-fg);
}

.is-coral strong {
  color: var(--qh-accent);
}
</style>
