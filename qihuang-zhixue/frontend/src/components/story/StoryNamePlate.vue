<template>
  <span class="story-name-plate" :class="`tone-${tone}`">{{ name }}</span>
</template>

<script setup lang="ts">
withDefaults(defineProps<{ name: string; tone?: 'master' | 'disciple' | 'narration' }>(), {
  tone: 'master',
})
</script>

<style scoped lang="scss">
.story-name-plate {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 96px;
  height: 34px;
  padding: 0 var(--qh-space-5);
  border-radius: var(--qh-radius-pill);
  font-family: var(--qh-font-body);
  font-size: var(--qh-text-ui);
  font-weight: 600;
  letter-spacing: 0.04em;
  white-space: nowrap;
}

.tone-master {
  color: var(--story-ink);
  background: var(--story-gold);
}

.tone-disciple {
  color: var(--story-cream);
  background: var(--story-jade);
}

.tone-narration {
  color: var(--story-ink);
  background: var(--story-cream-soft);
}
</style>
