<template>
  <div class="callout" :class="`tone-${tone}`">
    <span class="callout-icon" aria-hidden="true">{{ icon }}</span>
    <EditableText
      :model-value="block.text || ''"
      :editable="editable"
      placeholder="提示内容"
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
import type { CalloutTone, DocBlock } from '../../../types/doc'

const props = defineProps<{ block: DocBlock; editable: boolean }>()
const emit = defineEmits<{
  (e: 'update', block: DocBlock): void
  (e: 'enter', payload: { before: string; after: string }): void
  (e: 'backspace-empty'): void
  (e: 'slash'): void
}>()

const tone = computed<CalloutTone>(() => (props.block.props?.tone as CalloutTone) || 'info')
const icon = computed(() => (props.block.props?.icon as string) || (tone.value === 'warn' ? '⚠' : tone.value === 'tip' ? '✦' : '💡'))

const updateText = (text: string) => emit('update', { ...props.block, text })
</script>

<style scoped lang="scss">
.callout {
  display: grid;
  grid-template-columns: 22px 1fr;
  gap: 10px;
  margin: 6px 0;
  padding: 14px 16px;
  border-radius: 4px;
  background: color-mix(in srgb, var(--qh-slate-400) 10%, transparent);
}

.callout.tone-warn {
  background: color-mix(in srgb, var(--qh-ochre-400) 14%, transparent);
}

.callout.tone-tip {
  background: color-mix(in srgb, var(--qh-celadon-400) 14%, transparent);
}

.callout-icon {
  font-size: 16px;
  line-height: 1.75;
}

:deep(.doc-editable) {
  font-size: 15px;
  line-height: 1.7;
  color: var(--qh-fg);
}
</style>
