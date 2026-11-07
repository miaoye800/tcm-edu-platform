<template>
  <button class="page-link" type="button" @click="open">
    <span class="page-icon" aria-hidden="true">{{ icon }}</span>
    <span class="page-title">{{ block.text || '未命名子页面' }}</span>
    <span class="page-arrow" aria-hidden="true">↗</span>
  </button>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import type { DocBlock } from '../../../types/doc'

const props = defineProps<{ block: DocBlock; editable: boolean }>()
const router = useRouter()

const icon = computed(() => String(props.block.props?.icon || '📄'))
const nodeId = computed(() => String(props.block.props?.nodeId || ''))

/** 知识树就是页面树：子页面点击回到该知识点的资源库 */
const open = () => {
  if (!nodeId.value) return
  void router.push({ name: 'resources', query: { node: nodeId.value } })
}
</script>

<style scoped lang="scss">
.page-link {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  margin: 3px 0;
  padding: 4px 6px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--qh-fg);
  text-align: left;
  cursor: pointer;

  &:hover {
    background: color-mix(in srgb, var(--qh-fg) 5%, transparent);

    .page-title {
      text-decoration: underline;
      text-underline-offset: 3px;
    }
  }
}

.page-icon {
  font-size: 15px;
}

.page-title {
  font-size: 15px;
  font-weight: 500;
}

.page-arrow {
  margin-left: auto;
  color: var(--qh-fg-faint);
  font-size: 12px;
}
</style>
