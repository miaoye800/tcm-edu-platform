<template>
  <div class="divider-row" role="separator"><span /></div>
</template>

<script setup lang="ts">
import type { DocBlock } from '../../../types/doc'

defineProps<{ block: DocBlock; editable: boolean }>()
</script>

<style scoped lang="scss">
.divider-row {
  padding: 10px 0;
  cursor: default;

  span {
    display: block;
    height: 1px;
    background: var(--qh-line-strong);
  }
}
</style>
