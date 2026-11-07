<template>
  <component :is="tag" class="glass-card" :class="[`is-${padding}`, { 'is-compact': compact }]">
    <slot />
  </component>
</template>

<script setup lang="ts">
withDefaults(
  defineProps<{
    tag?: string
    padding?: 'none' | 'sm' | 'md' | 'lg'
    compact?: boolean
  }>(),
  {
    tag: 'article',
    padding: 'md',
    compact: false,
  },
)
</script>

<style scoped lang="scss">
@use "../../styles/tokens.scss" as *;

.glass-card {
  @include glass-panel;
  position: relative;
  overflow: hidden;
}

.glass-card > :deep(*) {
  position: relative;
}

.is-none {
  padding: 0;
}

.is-sm {
  padding: 14px;
}

.is-md {
  padding: 20px;
}

.is-lg {
  padding: 24px;
}

.is-compact {
  border-radius: var(--qh-radius-card-sm);
}
</style>
