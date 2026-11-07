<template>
  <Teleport to="body">
    <div v-if="open" class="doc-menu-backdrop" @mousedown.self="emit('close')">
      <div class="doc-menu" :style="panelStyle" role="menu">
        <p class="menu-caption">转成</p>
        <div class="menu-grid">
          <button
            v-for="type in TURN_INTO_TYPES"
            :key="type"
            type="button"
            class="menu-type"
            :class="{ active: type === block?.type }"
            @click="emit('turn-into', type)"
          >
            <span class="menu-type-icon">{{ specOf(type).icon }}</span>
            <span>{{ specOf(type).label }}</span>
          </button>
        </div>
        <div class="menu-divider" />
        <button type="button" class="menu-action" @click="emit('duplicate')">复制此块</button>
        <button type="button" class="menu-action" :disabled="!canMoveUp" @click="emit('move-up')">上移</button>
        <button type="button" class="menu-action" :disabled="!canMoveDown" @click="emit('move-down')">下移</button>
        <button type="button" class="menu-action is-danger" @click="emit('remove')">删除</button>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { BlockType, DocBlock } from '../../types/doc'
import { specOf, TURN_INTO_TYPES } from '../../config/docEditor'

const props = defineProps<{
  open: boolean
  x: number
  y: number
  block: DocBlock | null
  canMoveUp: boolean
  canMoveDown: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'turn-into', type: BlockType): void
  (e: 'duplicate'): void
  (e: 'remove'): void
  (e: 'move-up'): void
  (e: 'move-down'): void
}>()

/** 菜单不允许跑出视口：Notion 同款处理，靠边缘时向内收 */
const panelStyle = computed(() => {
  const width = 268
  const height = 396
  const left = Math.min(Math.max(12, props.x - 24), window.innerWidth - width - 12)
  const top = Math.min(Math.max(12, props.y - 12), window.innerHeight - height - 12)
  return { left: `${left}px`, top: `${top}px`, width: `${width}px` }
})
</script>

<style scoped lang="scss">
.doc-menu-backdrop {
  position: fixed;
  inset: 0;
  z-index: 2600;
}

.doc-menu {
  position: fixed;
  max-height: 396px;
  padding: 8px;
  overflow-y: auto;
  border: 1px solid var(--qh-line-strong);
  border-radius: 8px;
  background: var(--qh-surface-raised);
  box-shadow: var(--qh-shadow-3);
  color: var(--qh-fg);
}

.menu-caption {
  margin: 2px 6px 6px;
  color: var(--qh-fg-faint);
  font-size: 11px;
  letter-spacing: 0.06em;
}

.menu-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1px;
}

.menu-type {
  display: flex;
  gap: 7px;
  align-items: center;
  padding: 5px 7px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--qh-fg);
  font-size: 13px;
  text-align: left;
  cursor: pointer;

  &:hover {
    background: color-mix(in srgb, var(--qh-fg) 7%, transparent);
  }

  &.active {
    color: var(--qh-accent);
    background: color-mix(in srgb, var(--qh-accent) 9%, transparent);
  }
}

.menu-type-icon {
  display: grid;
  width: 18px;
  height: 18px;
  place-items: center;
  border: 1px solid var(--qh-line);
  border-radius: 3px;
  color: var(--qh-fg-muted);
  font-size: 10px;
}

.menu-divider {
  height: 1px;
  margin: 7px 4px;
  background: var(--qh-line);
}

.menu-action {
  display: block;
  width: 100%;
  padding: 6px 7px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--qh-fg);
  font-size: 13px;
  text-align: left;
  cursor: pointer;

  &:hover:not(:disabled) {
    background: color-mix(in srgb, var(--qh-fg) 7%, transparent);
  }

  &:disabled {
    color: var(--qh-fg-faint);
    cursor: default;
  }

  &.is-danger:hover {
    color: #c0392b;
    background: color-mix(in srgb, #c0392b 8%, transparent);
  }
}
</style>
