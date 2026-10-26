<template>
  <div class="app-shell" :class="{ 'sidebar-collapsed': collapsed }">
    <AppSidebar :collapsed="collapsed" />
    <section class="app-main">
      <AppTopbar />
      <main class="content-scroll">
        <button class="collapse-toggle" type="button" :aria-pressed="collapsed" @click="collapsed = !collapsed">
          <el-icon><Fold v-if="!collapsed" /><Expand v-else /></el-icon>
        </button>
        <RouterView />
      </main>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { RouterView } from 'vue-router'
import { Expand, Fold } from '@element-plus/icons-vue'
import AppSidebar from './AppSidebar.vue'
import AppTopbar from './AppTopbar.vue'

const collapsed = ref(false)
</script>

<style scoped lang="scss">
@use "../../styles/tokens.scss" as *;

.app-shell {
  display: flex;
  min-width: 0;
  min-height: 100vh;
  background: var(--qh-page-bg);
}

.app-main {
  display: flex;
  flex: 1;
  min-width: 0;
  max-height: 100vh;
  flex-direction: column;
}

.content-scroll {
  position: relative;
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  padding: 22px 24px 40px;
}

.collapse-toggle {
  position: fixed;
  left: calc(var(--qh-sidebar-width) - 18px);
  top: 24px;
  z-index: 30;
  display: grid;
  width: 36px;
  height: 36px;
  place-items: center;
  border: 1px solid rgba(143, 208, 173, 0.22);
  border-radius: 12px;
  color: var(--qh-text);
  background: var(--qh-collapse-fill);
  box-shadow: var(--qh-shadow-soft);
  cursor: pointer;
  transition: left 220ms ease;
}

.collapse-toggle:focus-visible {
  @include focus-ring;
}

.sidebar-collapsed .collapse-toggle {
  left: calc(var(--qh-sidebar-collapsed) - 18px);
}

@media (max-width: 900px) {
  .collapse-toggle {
    display: none;
  }

  .content-scroll {
    padding: 18px 14px 34px;
  }
}
</style>
