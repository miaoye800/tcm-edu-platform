<template>
  <Teleport to="body">
    <div v-if="open" class="slash-backdrop" @mousedown.self="emit('close')">
      <div class="slash-panel" :style="panelStyle" role="listbox">
        <div class="slash-input-row">
          <span class="slash-hint">/</span>
          <input
            ref="input"
            v-model="query"
            class="slash-input"
            placeholder="搜索块类型：标题、待办、表格…"
            @keydown="onKeydown"
          />
        </div>
        <div class="slash-list">
          <template v-for="group in grouped" :key="group.name">
            <p class="slash-group">{{ group.label }}</p>
            <button
              v-for="spec in group.items"
              :key="spec.type"
              type="button"
              class="slash-item"
              :class="{ active: spec.type === activeType }"
              @mouseenter="activeIndex = flatIndex(spec.type)"
              @click="choose(spec.type)"
            >
              <span class="slash-icon">{{ spec.icon }}</span>
              <span class="slash-label">{{ spec.label }}</span>
              <span class="slash-desc">{{ spec.hint }}</span>
            </button>
          </template>
          <p v-if="!flat.length" class="slash-empty">没有匹配的块类型</p>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import type { BlockType } from '../../types/doc'
import { BLOCK_GROUP_LABEL, matchSlash, type BlockSpec } from '../../config/docEditor'

const props = defineProps<{ open: boolean; x: number; y: number }>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'select', type: BlockType): void
}>()

const query = ref('')
const input = ref<HTMLInputElement | null>(null)
const activeIndex = ref(0)

const flat = computed(() => matchSlash(query.value))
const grouped = computed(() => {
  const order: BlockSpec['group'][] = ['basic', 'list', 'media', 'advanced']
  return order
    .map(name => ({ name, label: BLOCK_GROUP_LABEL[name], items: flat.value.filter(spec => spec.group === name) }))
    .filter(group => group.items.length)
})

const activeType = computed<BlockType | null>(() => flat.value[activeIndex.value]?.type || null)

const flatIndex = (type: BlockType): number => flat.value.findIndex(spec => spec.type === type)

const panelStyle = computed(() => {
  const width = 320
  const height = 380
  const left = Math.min(Math.max(12, props.x), window.innerWidth - width - 12)
  const top = Math.min(Math.max(12, props.y), window.innerHeight - height - 12)
  return { left: `${left}px`, top: `${top}px`, width: `${width}px` }
})

watch(() => props.open, async open => {
  if (!open) return
  query.value = ''
  activeIndex.value = 0
  await nextTick()
  input.value?.focus()
})

watch(query, () => { activeIndex.value = 0 })

const choose = (type: BlockType) => emit('select', type)

const onKeydown = (event: KeyboardEvent) => {
  if (event.key === 'Escape') { event.preventDefault(); emit('close'); return }
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    activeIndex.value = Math.min(activeIndex.value + 1, flat.value.length - 1)
    return
  }
  if (event.key === 'ArrowUp') {
    event.preventDefault()
    activeIndex.value = Math.max(activeIndex.value - 1, 0)
    return
  }
  if (event.key === 'Enter') {
    event.preventDefault()
    const target = flat.value[activeIndex.value]
    if (target) choose(target.type)
  }
}
</script>

<style scoped lang="scss">
.slash-backdrop {
  position: fixed;
  inset: 0;
  z-index: 2700;
}

.slash-panel {
  position: fixed;
  display: flex;
  flex-direction: column;
  max-height: 380px;
  overflow: hidden;
  border: 1px solid var(--qh-line-strong);
  border-radius: 8px;
  background: var(--qh-surface-raised);
  box-shadow: var(--qh-shadow-3);
  color: var(--qh-fg);
}

.slash-input-row {
  display: flex;
  gap: 6px;
  align-items: center;
  padding: 9px 12px;
  border-bottom: 1px solid var(--qh-line);
}

.slash-hint {
  color: var(--qh-fg-faint);
  font-size: 13px;
}

.slash-input {
  flex: 1;
  border: 0;
  outline: none;
  background: transparent;
  color: var(--qh-fg);
  font-size: 13px;

  &::placeholder {
    color: var(--qh-fg-faint);
  }
}

.slash-list {
  flex: 1;
  padding: 6px;
  overflow-y: auto;
}

.slash-group {
  margin: 6px 7px 3px;
  color: var(--qh-fg-faint);
  font-size: 11px;
  letter-spacing: 0.06em;
}

.slash-item {
  display: grid;
  grid-template-columns: 22px 74px 1fr;
  gap: 8px;
  align-items: center;
  width: 100%;
  padding: 5px 7px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--qh-fg);
  text-align: left;
  cursor: pointer;

  &.active {
    background: color-mix(in srgb, var(--qh-fg) 7%, transparent);
  }
}

.slash-icon {
  display: grid;
  width: 20px;
  height: 20px;
  place-items: center;
  border: 1px solid var(--qh-line);
  border-radius: 3px;
  color: var(--qh-fg-muted);
  font-size: 10px;
}

.slash-label {
  font-size: 13px;
}

.slash-desc {
  color: var(--qh-fg-faint);
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.slash-empty {
  padding: 16px;
  color: var(--qh-fg-faint);
  font-size: 13px;
  text-align: center;
}
</style>
