<template>
  <div class="list-row" :class="`is-${block.type}`">
    <button
      v-if="block.type === 'todo'"
      class="marker marker-todo"
      type="button"
      :class="{ checked: !!block.props?.checked }"
      :aria-checked="!!block.props?.checked"
      role="checkbox"
      @mousedown.prevent="toggleChecked"
    >
      <svg v-if="block.props?.checked" viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 6.2 4.8 8.5 9.5 3.8" /></svg>
    </button>
    <span v-else class="marker marker-text" aria-hidden="true">{{ marker }}</span>
    <EditableText
      class="list-text"
      :model-value="block.text || ''"
      :editable="editable"
      :placeholder="placeholder"
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

const props = defineProps<{ block: DocBlock; editable: boolean; index?: number }>()
const emit = defineEmits<{
  (e: 'update', block: DocBlock): void
  (e: 'enter', payload: { before: string; after: string }): void
  (e: 'backspace-empty'): void
  (e: 'slash'): void
}>()

const marker = computed(() => (props.block.type === 'numbered' ? `${(props.index ?? 0) + 1}.` : '•'))
const placeholder = computed(() => (props.block.type === 'todo' ? '待办事项' : '列表项'))

const updateText = (text: string) => emit('update', { ...props.block, text })

/**
 * 只改 type 之外什么都不动 —— 这正是 Notion「转成」不丢数据的原因：
 * checked 留在 props 里，转成标题再转回来，勾选状态还在。
 *
 * **勾选不受 editable 限制**：正文只读不等于任务清单不能勾。工坊里预览
 * 刚生成的稿子时点不动复选框，用户会以为整个文档都是死的；这里照常
 * 发 update，落不落库由父层决定（预览页会存回资源，流式生成中则忽略）。
 */
const toggleChecked = () => {
  emit('update', { ...props.block, props: { ...(props.block.props || {}), checked: !props.block.props?.checked } })
}
</script>

<style scoped lang="scss">
.list-row {
  display: grid;
  grid-template-columns: 22px 1fr;
  align-items: start;
  padding: 2px 0;
}

.marker {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 29px;
  color: var(--qh-fg);
  font-size: 16px;
  line-height: 1.75;
  user-select: none;
}

.marker-text {
  justify-content: flex-start;
  padding-left: 3px;
}

.marker-todo {
  position: relative;
  width: 16px;
  height: 16px;
  margin: 7px 0 0 1px;
  padding: 0;
  border: 1.5px solid var(--qh-fg-muted);
  border-radius: 3px;
  background: transparent;
  cursor: pointer;
  transition: background 120ms ease, border-color 120ms ease;

  svg {
    width: 12px;
    height: 12px;
    fill: none;
    stroke: #fff;
    stroke-width: 1.8;
    stroke-linecap: round;
    stroke-linejoin: round;
  }

  &.checked {
    border-color: var(--qh-accent);
    background: var(--qh-accent);
  }
}

.list-row.is-todo .list-text :deep(.doc-editable) {
  color: var(--qh-fg);
}

.list-row.is-todo:has(.marker-todo.checked) .list-text :deep(.doc-editable) {
  color: var(--qh-fg-muted);
  text-decoration: line-through;
  text-decoration-color: var(--qh-fg-faint);
}

:deep(.doc-editable) {
  font-size: 16px;
  line-height: 1.75;
}
</style>
