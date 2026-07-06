<template>
  <section class="immersive-frame">
    <header>
      <RouterLink to="/profile">
        <el-icon><ArrowLeft /></el-icon>
        返回
      </RouterLink>
      <strong>{{ title }}</strong>
    </header>

    <main>
      <p class="eyebrow">沉浸模式</p>
      <h1>{{ title }}</h1>
      <p>这里预留独立沉浸路由。顶部保留返回入口，后续可承载对话、播放或全屏图谱体验。</p>
    </main>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { ArrowLeft } from '@element-plus/icons-vue'

const route = useRoute()
const title = computed(() => String(route.meta.title ?? '沉浸空间'))
</script>

<style scoped lang="scss">
@use "../styles/tokens.scss" as *;

.immersive-frame {
  min-height: 100vh;
  background: var(--qh-page-bg);
}

header {
  position: sticky;
  top: 0;
  z-index: 5;
  display: flex;
  align-items: center;
  gap: 16px;
  min-height: 72px;
  padding: 18px 24px;
  border-bottom: 1px solid var(--qh-border);
  background: rgba(17, 29, 32, 0.82);
  backdrop-filter: blur(18px);
}

a {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 38px;
  padding: 0 12px;
  border-radius: 12px;
  color: #10201d;
  font-weight: 900;
  background: linear-gradient(135deg, var(--qh-color-jade), var(--qh-color-blue));
}

strong {
  font-family: var(--qh-font-display);
  font-size: 18px;
  font-weight: 900;
}

main {
  @include glass-panel(26px);
  width: min(900px, calc(100% - 32px));
  margin: 48px auto;
  padding: 28px;
}

.eyebrow {
  margin: 0 0 8px;
  color: var(--qh-color-gold);
  font-size: 13px;
  font-weight: 900;
}

h1 {
  margin: 0;
  font-family: var(--qh-font-display);
  font-size: clamp(38px, 7vw, 72px);
  font-weight: 900;
}

p:last-child {
  max-width: 620px;
  margin: 18px 0 0;
  color: var(--qh-text-muted);
  line-height: 1.8;
}
</style>
