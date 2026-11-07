<template>
  <figure class="image-block">
    <img v-if="src" :src="src" :alt="alt" :style="{ aspectRatio: ratio }" loading="lazy" />
    <div v-else class="image-placeholder" :style="{ aspectRatio: ratio }">
      <span aria-hidden="true">🖼</span>
      <p>图片占位</p>
      <small>本期未接图像模型，接入后由 assetId 直接渲染</small>
    </div>
    <figcaption v-if="caption || editable" class="image-caption">
      <EditableText
        :model-value="caption"
        :editable="editable"
        placeholder="添加图片说明"
        @update:model-value="value => emit('update', { ...block, props: { ...(block.props || {}), caption: value } })"
      />
    </figcaption>
  </figure>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import EditableText from '../EditableText.vue'
import type { DocBlock } from '../../../types/doc'

const props = defineProps<{ block: DocBlock; editable: boolean }>()
const emit = defineEmits<{ (e: 'update', block: DocBlock): void }>()

const src = computed(() => String(props.block.props?.assetId || ''))
const alt = computed(() => String(props.block.props?.alt || ''))
const ratio = computed(() => String(props.block.props?.ratio || '16 / 9'))
const caption = computed(() => String(props.block.props?.caption || ''))
</script>

<style scoped lang="scss">
.image-block {
  margin: 10px 0;
}

img {
  display: block;
  width: 100%;
  border-radius: 4px;
  object-fit: cover;
}

.image-placeholder {
  display: grid;
  place-content: center;
  gap: 2px;
  width: 100%;
  border: 1px dashed var(--qh-line-strong);
  border-radius: 4px;
  background: var(--qh-surface-sunken);
  color: var(--qh-fg-muted);
  text-align: center;

  span {
    font-size: 22px;
    opacity: 0.55;
  }

  p {
    margin: 0;
    font-size: 13px;
  }

  small {
    color: var(--qh-fg-faint);
    font-size: 11px;
  }
}

.image-caption {
  margin-top: 6px;
}

:deep(.doc-editable) {
  font-size: 13px;
  line-height: 1.5;
  color: var(--qh-fg-muted);
}
</style>
