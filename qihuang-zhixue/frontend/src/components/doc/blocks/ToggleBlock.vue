<template>
  <div class="toggle-row">
    <button class="toggle-arrow" type="button" :class="{ open: !collapsed }" :aria-expanded="!collapsed" @mousedown.prevent="toggle">
      <svg viewBox="0 0 12 12" aria-hidden="true"><path d="M4.5 2.5 8 6l-3.5 3.5" /></svg>
    </button>
    <EditableText
      :model-value="block.text || ''"
      :editable="editable"
      placeholder="折叠块标题"
      @update:model-value="updateText"
      @enter="payload => emit('enter', payload)"
      @backspace-empty="emit('backspace-empty')"
      @slash="emit('slash')"
    />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import EditableText from '../EditableText.vue'
import type { DocBlock } from '../../../types/doc'

const props = defineProps<{ block: DocBlock; editable: boolean }>()
const emit = defineEmits<{
  (e: 'update', block: DocBlock): void
  (e: 'enter', payload: { before: string; after: string }): void
  (e: 'backspace-empty'): void
  (e: 'slash'): void
}>()

const collapsed = computed(() => props.block.props?.collapsed !== false)
const updateText = (text: string) => emit('update', { ...props.block, text })

/** 折叠态存在 props 里，类型不变 —— 与 Notion 一致 */
const toggle = () => emit('update', { ...props.block, props: { ...(props.block.props || {}), collapsed: !collapsed.value } })
</script>

<style scoped lang="scss">
.toggle-row {
  display: grid;
  grid-template-columns: 22px 1fr;
  align-items: start;
  padding: 2px 0;
}

.toggle-arrow {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 28px;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: pointer;

  svg {
    width: 11px;
    height: 11px;
    fill: none;
    stroke: var(--qh-fg-muted);
    stroke-width: 1.7;
    stroke-linecap: round;
    stroke-linejoin: round;
    transition: transform 140ms ease;
  }

  &.open svg {
    transform: rotate(90deg);
  }

  &:hover svg {
    stroke: var(--qh-fg);
  }
}

:deep(.doc-editable) {
  font-size: 16px;
  font-weight: 500;
  line-height: 1.75;
}
</style>
