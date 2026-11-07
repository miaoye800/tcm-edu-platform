<template>
  <EditableText
    :model-value="block.text || ''"
    :tag="tag"
    :editable="editable"
    :placeholder="`${label}标题`"
    @update:model-value="updateText"
    @enter="payload => emit('enter', payload)"
    @backspace-empty="emit('backspace-empty')"
    @slash="emit('slash')"
  />
</template>

<script setup lang="ts">
import { computed } from 'vue'
import EditableText from '../EditableText.vue'
import type { DocBlock } from '../../../types/doc'
import { headingLevel } from '../../../types/doc'

const props = defineProps<{ block: DocBlock; editable: boolean }>()
const emit = defineEmits<{
  (e: 'update', block: DocBlock): void
  (e: 'enter', payload: { before: string; after: string }): void
  (e: 'backspace-empty'): void
  (e: 'slash'): void
}>()

const level = computed(() => headingLevel(props.block.type) || 2)
const tag = computed(() => `h${level.value}`)
const label = computed(() => ['', '一级', '二级', '三级'][level.value] || '二级')

const updateText = (text: string) => emit('update', { ...props.block, text })
</script>

<style scoped lang="scss">
/* Notion 的标题观感来自「大字号 + 紧行高 + 上方大留白 + 下方小留白」，
   而不是加粗和颜色。这里刻意不用衬线字体，一用就变回"古卷"。 */
:deep(h1.doc-editable) {
  margin: 38px 0 4px;
  font-family: var(--qh-font-body);
  font-size: 40px;
  font-weight: 700;
  line-height: 1.2;
  letter-spacing: -0.02em;
  color: var(--qh-fg);
}

:deep(h2.doc-editable) {
  margin: 32px 0 4px;
  font-family: var(--qh-font-body);
  font-size: 30px;
  font-weight: 600;
  line-height: 1.3;
  letter-spacing: -0.01em;
  color: var(--qh-fg);
}

:deep(h3.doc-editable) {
  margin: 26px 0 2px;
  font-family: var(--qh-font-body);
  font-size: 24px;
  font-weight: 600;
  line-height: 1.35;
  color: var(--qh-fg);
}
</style>
