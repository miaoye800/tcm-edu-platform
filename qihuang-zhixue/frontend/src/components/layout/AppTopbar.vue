<template>
  <header class="app-topbar">
    <div class="title-stack">
      <div class="crumbs">
        <span>{{ group }}</span>
        <span>/</span>
        <strong>{{ title }}</strong>
      </div>
      <h1>{{ title }}</h1>
    </div>

    <div class="top-actions">
      <el-input
        v-model="searchText"
        class="global-search"
        clearable
        :prefix-icon="Search"
        placeholder="搜索药材、方剂、错题与画像线索"
        @keyup.enter="submitSearch"
      />
      <el-button type="primary" :icon="MagicStick" @click="submitSearch">焕醒画像</el-button>
    </div>
  </header>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { MagicStick, Search } from '@element-plus/icons-vue'
import { useAssistantStore } from '../../stores/assistant'

const route = useRoute()
const router = useRouter()
const assistant = useAssistantStore()
const searchText = ref('')

const title = computed(() => String(route.meta.title ?? '岐黄智学'))
const group = computed(() => String(route.meta.group ?? '学习'))

const submitSearch = async () => {
  const keyword = searchText.value.trim()

  if (!keyword) {
    assistant.show()
    return
  }

  await router.push({
    name: 'learn',
    query: { topic: keyword },
  })
}
</script>

<style scoped lang="scss">
.app-topbar {
  position: sticky;
  top: 0;
  z-index: 18;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(360px, 560px);
  align-items: center;
  gap: 18px;
  min-height: var(--qh-topbar-height);
  padding: 18px 24px 14px;
  border-bottom: 1px solid rgba(143, 208, 173, 0.08);
  background: var(--qh-topbar-fill);
  backdrop-filter: blur(18px);
}

.title-stack {
  min-width: 0;
}

.crumbs {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 7px;
  color: var(--qh-text-muted);
  font-size: 12px;
  font-weight: 800;
}

.crumbs strong {
  color: var(--qh-color-jade);
}

h1 {
  margin: 4px 0 0;
  font-family: var(--qh-font-display);
  font-size: clamp(24px, 3vw, 34px);
  font-weight: 900;
  line-height: 1.1;
}

.top-actions {
  display: grid;
  grid-template-columns: minmax(180px, 1fr) auto;
  gap: 12px;
  align-items: center;
  min-width: 0;
}

.global-search {
  min-width: 0;
}

@media (max-width: 1020px) {
  .app-topbar {
    grid-template-columns: 1fr;
    gap: 12px;
  }
}

@media (max-width: 620px) {
  .app-topbar {
    padding: 14px 14px 12px;
  }

  .top-actions {
    grid-template-columns: 1fr;
  }
}
</style>
