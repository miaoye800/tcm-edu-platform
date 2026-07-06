<template>
  <span class="q-badge" :class="`is-${tone}`">
    <slot>{{ label }}</slot>
  </span>
</template>

<script setup lang="ts">
withDefaults(
  defineProps<{
    label?: string | number
    tone?: 'jade' | 'blue' | 'gold' | 'coral' | 'muted'
  }>(),
  {
    label: '',
    tone: 'jade',
  },
)
</script>

<style scoped lang="scss">
.q-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 24px;
  min-height: 24px;
  padding: 3px 9px;
  border: 1px solid currentColor;
  border-radius: var(--qh-radius-pill);
  font-family: var(--qh-font-display);
  font-size: 12px;
  font-weight: 900;
  line-height: 1;
  animation: bp 2.8s ease-in-out infinite;
}

.is-jade {
  color: var(--qh-color-jade);
  background: rgba(143, 208, 173, 0.1);
}

.is-blue {
  color: var(--qh-color-blue);
  background: rgba(159, 182, 214, 0.1);
}

.is-gold {
  color: var(--qh-color-gold);
  background: rgba(231, 194, 116, 0.1);
}

.is-coral {
  color: var(--qh-color-coral);
  background: rgba(233, 167, 156, 0.1);
}

.is-muted {
  color: var(--qh-text-muted);
  background: rgba(238, 245, 239, 0.06);
}
</style>
