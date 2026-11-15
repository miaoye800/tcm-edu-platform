<template>
  <nav v-if="items.length" class="doc-toc" aria-label="目录">
    <p class="toc-caption">
      <!-- 一个 11px 的小图标定住「这是导航」，比再加一行标题省地方 -->
      <QhIcon name="fluent:list-24-regular" :size="13" />
      目录
    </p>
    <button
      v-for="item in items"
      :key="item.id"
      type="button"
      :class="[`level-${item.level}`, { active: item.id === activeId }]"
      :title="item.text"
      @click="emit('jump', item.id)"
    >
      {{ item.text }}
    </button>
  </nav>
</template>

<script setup lang="ts">
import QhIcon from '../ui/QhIcon.vue'
import type { TocItem } from '../../types/doc'

defineProps<{ items: TocItem[]; activeId: string }>()
const emit = defineEmits<{ (e: 'jump', id: string): void }>()
</script>

<style scoped lang="scss">
.doc-toc {
  position: sticky;
  top: 72px;
  align-self: start;
  display: grid;
  gap: 1px;
  width: 100%;
  max-height: calc(100vh - 140px);
  padding-top: 28px;
  overflow-y: auto;
}

.toc-caption {
  display: flex;
  align-items: center;
  gap: 5px;
  margin: 0 0 4px;
  color: var(--qh-fg-faint);
  font-size: 11px;
  letter-spacing: 0.08em;
}

button {
  padding: 3px 0 3px 8px;
  border: 0;
  border-left: 2px solid transparent;
  background: transparent;
  color: var(--qh-fg-faint);
  font-size: 12px;
  line-height: 1.45;
  text-align: left;
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;

  &:hover {
    color: var(--qh-fg-muted);
  }

  &.active {
    border-left-color: var(--qh-fg);
    color: var(--qh-fg);
    font-weight: 500;
  }

  &.level-1 {
    padding-left: 0;
    border-left: 0;
    font-weight: 600;
    color: var(--qh-fg-muted);
  }

  &.level-3 {
    padding-left: 18px;
    font-size: 11.5px;
  }
}

@media (max-width: 1240px) {
  .doc-toc { display: none; }
}
</style>
