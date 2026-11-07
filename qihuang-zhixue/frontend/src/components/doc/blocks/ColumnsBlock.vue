<template>
  <div
    class="columns-grid"
    :style="{ gridTemplateColumns: widths.map(width => `minmax(0, ${width}fr)`).join(' ') }"
  >
    <slot />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { DocBlock } from '../../../types/doc'

const props = defineProps<{ block: DocBlock; editable: boolean }>()

const widths = computed<number[]>(() => {
  const raw = props.block.props?.widths as number[] | undefined
  if (Array.isArray(raw) && raw.length) return raw
  const count = Math.max(2, props.block.children?.length || 2)
  return Array.from({ length: count }, () => 1)
})
</script>

<style scoped lang="scss">
.columns-grid {
  display: grid;
  gap: 18px;
  margin: 8px 0;
}
</style>
