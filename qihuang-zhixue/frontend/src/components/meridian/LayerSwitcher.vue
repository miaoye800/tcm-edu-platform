<template>
  <nav class="layer-switcher" aria-label="学科筛选">
    <button
      type="button"
      class="layer"
      :class="{ 'is-active': active === 'all' }"
      :aria-pressed="active === 'all'"
      @click="emit('select', 'all')"
    >
      <span class="layer-name">全部</span>
      <span v-if="active === 'all' && summary" class="layer-count">{{ summary }}</span>
    </button>
    <button
      v-for="layer in layers"
      :key="layer.id"
      type="button"
      class="layer"
      :class="{ 'is-active': layer.id === active }"
      :aria-pressed="layer.id === active"
      @click="emit('select', layer.id)"
    >
      <span class="layer-name">{{ layer.title }}</span>
      <span v-if="layer.id === active && summary" class="layer-count">{{ summary }}</span>
    </button>
  </nav>
</template>

<script setup lang="ts">
/**
 * 学科 = **筛选**，不是图层，也不是身体分区。
 *
 * 一具身体上只有 362 个穴位，六门课一共 293 个小节，一穴一节全部铺满——
 * 所以每个穴位只属于**一个**小节、**一门**课，不存在"一个穴位在中药学里是
 * 辛温解表药、在方剂学里是辛温解表剂"这回事（早期版本那样做，切换学科实际是在
 * 换一整套映射，用户看到的就是"同一个点代表不同学科"）。
 *
 * 这里的按钮只做高亮筛选：选全部 = 六门课同时亮；选某一科 = 只看这一科。
 */
import type { MeridianLayer } from '../../services/meridianApi'

defineProps<{
  layers: Array<Pick<MeridianLayer, 'id' | 'title'>>
  active: string
  summary: string
}>()

const emit = defineEmits<{ (event: 'select', id: string): void }>()
</script>

<style scoped lang="scss">
.layer-switcher {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.layer {
  display: grid;
  gap: 1px;
  padding: 5px 11px;
  border: 1px solid color-mix(in srgb, var(--qh-color-jade) 20%, transparent);
  border-radius: var(--qh-radius-pill);
  background: color-mix(in srgb, var(--qh-color-night-soft) 66%, transparent);
  color: var(--qh-text-muted);
  cursor: pointer;
  transition: 0.16s;

  &:hover,
  &:focus-visible {
    border-color: color-mix(in srgb, var(--qh-color-jade) 55%, transparent);
    color: var(--qh-color-cream);
    outline: none;
  }

  &.is-active {
    border-color: var(--qh-color-jade);
    background: color-mix(in srgb, var(--qh-color-jade) 16%, transparent);
    color: var(--qh-color-cream);
  }
}

.layer-name {
  font-size: 11.5px;
  letter-spacing: 0.02em;
}

.layer-count {
  color: var(--qh-color-jade);
  font-size: 9px;
  font-variant-numeric: tabular-nums;
}
</style>
