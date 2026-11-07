<template>
  <header class="page-header" :class="{ 'has-cover': !!cover }">
    <div v-if="cover" class="page-cover" :class="`cover-${cover}`">
      <button class="cover-action" type="button" @click="emit('remove-cover')">移除封面</button>
    </div>
    <div v-else class="cover-ghost">
      <button type="button" @click="emit('add-cover')">添加封面</button>
    </div>

    <div class="header-inner">
      <div class="icon-slot">
        <button class="page-icon" type="button" :title="'更换图标'" @click.stop="iconOpen = !iconOpen">{{ icon || '📄' }}</button>
        <div v-if="iconOpen" class="icon-picker" @mousedown.stop>
          <button v-for="item in ICONS" :key="item" type="button" class="icon-option" @click="pickIcon(item)">{{ item }}</button>
        </div>
      </div>

      <nav v-if="breadcrumb.length" class="breadcrumb" aria-label="位置">
        <template v-for="(crumb, index) in breadcrumb" :key="crumb.label">
          <router-link v-if="crumb.to" :to="crumb.to" class="crumb">{{ crumb.label }}</router-link>
          <span v-else class="crumb is-current">{{ crumb.label }}</span>
          <span v-if="index < breadcrumb.length - 1" class="crumb-sep">/</span>
        </template>
      </nav>

      <h1 class="page-title">{{ title || '正在整理讲解' }}</h1>

      <div class="page-meta">
        <span>{{ kindLabel }}</span>
        <span>{{ createdAt }}</span>
        <span v-if="wordCount">{{ wordCount }} 字</span>
        <span v-if="citationCount">{{ citationCount }} 处引文</span>
      </div>
    </div>
  </header>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import type { Crumb } from '../../types/doc'

const props = defineProps<{
  title: string
  icon?: string
  cover?: string
  createdAt: string
  breadcrumb: Crumb[]
  kindLabel: string
  wordCount?: number
  citationCount?: number
}>()

const emit = defineEmits<{
  (e: 'update-icon', value: string): void
  (e: 'add-cover'): void
  (e: 'remove-cover'): void
}>()

const iconOpen = ref(false)

/** 中药语汇的图标集：比通用 emoji 列表更贴题，选择面窄反而好用 */
const ICONS = ['🌿', '🍃', '🌱', '🪵', '🌸', '🫀', '🧪', '⚗️', '📜', '📄', '🗂', '🫖', '🧭', '☯️']

const pickIcon = (value: string) => {
  iconOpen.value = false
  emit('update-icon', value)
}
</script>

<style scoped lang="scss">
.page-header {
  position: relative;
}

.page-cover {
  height: 180px;
  margin: 0 calc(-1 * var(--doc-bleed, 0px));
  border-radius: 0;
}

/* 6 套封面预设：全部取自 palette 的既有色阶，不引入新颜色 */
.cover-jade { background: linear-gradient(120deg, #e8f0ec, #cfe0d8 55%, #eef4f1); }
.cover-ochre { background: linear-gradient(120deg, #f4ece0, #e6d3b8 55%, #f7f1e6); }
.cover-slate { background: linear-gradient(120deg, #e9eef5, #cdd9e7 55%, #eef2f7); }
.cover-ink { background: linear-gradient(120deg, #ececeb, #d6d6d3 55%, #f2f2f0); }
.cover-sunset { background: linear-gradient(120deg, #f7e7e2, #eccbc2 55%, #f9efec); }
.cover-night { background: linear-gradient(120deg, #2a2a28, #1a1a18 60%, #33332f); }

.cover-action {
  position: absolute;
  right: 16px;
  bottom: 10px;
  padding: 3px 9px;
  border: 1px solid var(--qh-line-strong);
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.8);
  color: var(--qh-fg-muted);
  font-size: 11px;
  opacity: 0;
  cursor: pointer;
  transition: opacity 120ms ease;
}

.page-cover {
  position: relative;
}

.page-cover:hover .cover-action { opacity: 1; }

.cover-ghost {
  height: 32px;
  opacity: 0;
  transition: opacity 120ms ease;

  button {
    padding: 3px 9px;
    border: 1px solid var(--qh-line);
    border-radius: 4px;
    background: transparent;
    color: var(--qh-fg-faint);
    font-size: 11.5px;
    cursor: pointer;
  }
}

.page-header:hover .cover-ghost { opacity: 1; }

.header-inner {
  position: relative;
  padding-top: 22px;
}

.has-cover .header-inner {
  padding-top: 0;
}

.icon-slot {
  position: relative;
  margin-bottom: 6px;
}

.has-cover .icon-slot {
  margin-top: -34px;
}

.page-icon {
  display: grid;
  width: 48px;
  height: 48px;
  place-items: center;
  border: 0;
  border-radius: 8px;
  background: transparent;
  font-size: 40px;
  line-height: 1;
  cursor: pointer;

  &:hover {
    background: color-mix(in srgb, var(--qh-fg) 6%, transparent);
  }
}

.has-cover .page-icon {
  background: var(--qh-bg);
  box-shadow: 0 0 0 4px var(--qh-bg);
}

.icon-picker {
  position: absolute;
  top: 52px;
  left: 0;
  z-index: 20;
  display: grid;
  grid-template-columns: repeat(7, 32px);
  gap: 2px;
  padding: 8px;
  border: 1px solid var(--qh-line-strong);
  border-radius: 8px;
  background: var(--qh-surface-raised);
  box-shadow: var(--qh-shadow-3);
}

.icon-option {
  display: grid;
  width: 32px;
  height: 32px;
  place-items: center;
  border: 0;
  border-radius: 4px;
  background: transparent;
  font-size: 18px;
  cursor: pointer;

  &:hover {
    background: color-mix(in srgb, var(--qh-fg) 8%, transparent);
  }
}

.breadcrumb {
  display: flex;
  gap: 5px;
  align-items: center;
  margin-bottom: 8px;
  font-size: 12px;
}

.crumb {
  color: var(--qh-fg-muted);
  text-decoration: none;

  &:hover {
    color: var(--qh-fg);
    text-decoration: underline;
    text-underline-offset: 2px;
  }

  &.is-current {
    color: var(--qh-fg);
  }
}

.crumb-sep {
  color: var(--qh-fg-faint);
}

.page-title {
  margin: 0 0 10px;
  font-family: var(--qh-font-body);
  font-size: 40px;
  font-weight: 700;
  line-height: 1.2;
  letter-spacing: -0.02em;
  color: var(--qh-fg);
}

.page-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  padding-bottom: 6px;
  color: var(--qh-fg-faint);
  font-size: 12px;
}
</style>
