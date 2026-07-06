<template>
  <section class="coming-view">
    <GlassCard padding="lg" class="coming-card">
      <p class="eyebrow">{{ group }}</p>
      <h2>{{ title }}</h2>
      <p>敬请期待</p>
    </GlassCard>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import GlassCard from '../components/ui/GlassCard.vue'

const route = useRoute()
const title = computed(() => String(route.meta.title ?? '阶段三模块'))
const group = computed(() => String(route.meta.group ?? '互动'))
</script>

<style scoped lang="scss">
@use "../styles/tokens.scss" as *;

.coming-view {
  display: grid;
  min-height: calc(100vh - 180px);
  place-items: center;
}

.coming-card {
  width: min(620px, 100%);
  text-align: center;
  opacity: 0.72;
  filter: grayscale(0.35);
}

.eyebrow {
  margin: 0 0 8px;
  color: var(--qh-text-muted);
  font-size: 13px;
  font-weight: 900;
}

h2 {
  margin: 0;
  color: var(--qh-text);
  font-family: var(--qh-font-display);
  font-size: clamp(36px, 6vw, 62px);
  font-weight: 900;
}

p:last-child {
  margin: 16px 0 0;
  color: var(--qh-color-gold);
  font-family: var(--qh-font-display);
  font-size: 22px;
  font-weight: 900;
}
</style>
