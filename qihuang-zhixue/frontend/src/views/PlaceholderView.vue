<template>
  <section class="placeholder-view">
    <GlassCard padding="lg">
      <p class="eyebrow">{{ group }}</p>
      <h2>{{ title }}</h2>
      <p>这个模块已接入新 AppShell，后续只需要替换当前内嵌视图内容，不必改导航和布局骨架。</p>
    </GlassCard>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import GlassCard from '../components/ui/GlassCard.vue'

const route = useRoute()
const title = computed(() => String(route.meta.title ?? '模块'))
const group = computed(() => String(route.meta.group ?? '岐黄智学'))
</script>

<style scoped lang="scss">
.placeholder-view {
  width: min(960px, 100%);
  margin: 0 auto;
}

.eyebrow {
  margin: 0 0 8px;
  color: var(--qh-color-jade);
  font-size: 13px;
  font-weight: 900;
}

h2 {
  margin: 0;
  font-family: var(--qh-font-display);
  font-size: clamp(34px, 6vw, 60px);
  font-weight: 900;
}

p:last-child {
  max-width: 620px;
  margin: 16px 0 0;
  color: var(--qh-text-muted);
  line-height: 1.8;
}
</style>
