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
}

// 状态标签不呼吸：一直在动的标签会持续抢注意力，而它只是信息编码。
.is-jade {
  color: var(--qh-fg-soft);
  background: var(--qh-line);
}

.is-blue {
  color: var(--qh-slate-300);
  background: var(--qh-line);
}

.is-gold {
  color: var(--qh-color-gold);
  background: var(--qh-line);
}

.is-coral {
  color: var(--qh-accent);
  background: var(--qh-accent-soft);
}

.is-muted {
  color: var(--qh-text-muted);
  background: var(--qh-line);
}
</style>
