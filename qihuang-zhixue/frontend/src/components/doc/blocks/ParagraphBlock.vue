<template>
  <EditableText
    :model-value="block.text || ''"
    :editable="editable"
    placeholder="输入正文，或按 / 插入内容"
    @update:model-value="updateText"
    @enter="payload => emit('enter', payload)"
    @backspace-empty="emit('backspace-empty')"
    @slash="emit('slash')"
  />
</template>

<script setup lang="ts">
import EditableText from '../EditableText.vue'
import type { DocBlock } from '../../../types/doc'

const props = defineProps<{ block: DocBlock; editable: boolean }>()
const emit = defineEmits<{
  (e: 'update', block: DocBlock): void
  (e: 'enter', payload: { before: string; after: string }): void
  (e: 'backspace-empty'): void
  (e: 'slash'): void
}>()

const updateText = (text: string) => emit('update', { ...props.block, text })
</script>

<style scoped lang="scss">
:deep(.doc-editable) {
  font-size: 16px;
  line-height: 1.75;
  color: var(--qh-fg);
}
</style>
