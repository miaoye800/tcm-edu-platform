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
  animation: vp 3.6s ease-in-out infinite;
}

.is-jade strong {
  color: var(--qh-color-jade);
}

.is-blue strong {
  color: var(--qh-color-blue);
}

.is-gold strong {
  color: var(--qh-color-gold);
}

.is-coral strong {
  color: var(--qh-color-coral);
}
</style>
