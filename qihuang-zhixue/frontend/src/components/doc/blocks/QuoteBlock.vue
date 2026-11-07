<template>
  <blockquote class="quote-block">
    <EditableText
      :model-value="block.text || ''"
      :editable="editable"
      placeholder="引用一段原文"
      @update:model-value="updateText"
      @enter="payload => emit('enter', payload)"
      @backspace-empty="emit('backspace-empty')"
      @slash="emit('slash')"
    />
    <cite v-if="sourceLabel" class="quote-source">{{ sourceLabel }}</cite>
  </blockquote>
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

const sourceLabel = computed(() => {
  const source = props.block.props?.source as Record<string, string | undefined> | undefined
  return source?.['出处'] || source?.source || source?.['定位'] || source?.ref || ''
})

const updateText = (text: string) => emit('update', { ...props.block, text })
</script>

<style scoped lang="scss">
.quote-block {
  margin: 6px 0;
  padding: 2px 0 2px 14px;
  border-left: 3px solid var(--qh-fg);
}

:deep(.doc-editable) {
  font-size: 16px;
  line-height: 1.75;
  color: var(--qh-fg);
}

.quote-source {
  display: block;
  margin-top: 4px;
  color: var(--qh-fg-muted);
  font-size: 14px;
  font-style: normal;
}
</style>
