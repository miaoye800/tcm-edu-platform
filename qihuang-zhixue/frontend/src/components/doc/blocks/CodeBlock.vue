<template>
  <div class="code-block">
    <span v-if="lang" class="code-lang">{{ lang }}</span>
    <pre><EditableText
      :model-value="block.text || ''"
      :editable="editable"
      :single-line="false"
      tag="code"
      placeholder="代码"
      @update:model-value="updateText"
      @backspace-empty="emit('backspace-empty')"
    /></pre>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import EditableText from '../EditableText.vue'
import type { DocBlock } from '../../../types/doc'

const props = defineProps<{ block: DocBlock; editable: boolean }>()
const emit = defineEmits<{
  (e: 'update', block: DocBlock): void
  (e: 'backspace-empty'): void
}>()

const lang = computed(() => String(props.block.props?.lang || ''))
const updateText = (text: string) => emit('update', { ...props.block, text })
</script>

<style scoped lang="scss">
.code-block {
  position: relative;
  margin: 8px 0;
  padding: 14px 16px;
  border: 1px solid var(--qh-line);
  border-radius: 4px;
  background: var(--qh-surface-sunken);
}

.code-lang {
  position: absolute;
  top: 8px;
  right: 12px;
  color: var(--qh-fg-faint);
  font-size: 11px;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

pre {
  margin: 0;
  overflow-x: auto;
}

:deep(code.doc-editable) {
  display: block;
  color: var(--qh-fg);
  font-family: "SFMono-Regular", "Cascadia Code", Consolas, "Liberation Mono", monospace;
  font-size: 13.5px;
  line-height: 1.6;
  white-space: pre-wrap;
}
</style>
