<template>
  <div class="toolbar" role="toolbar" aria-label="幻灯工具栏">
    <button class="tool tool-icon" type="button" title="加一页" aria-label="加一页" :disabled="!editable" @click="$emit('add')">
      <el-icon><Plus /></el-icon>
    </button>
    <button
      class="tool tool-icon"
      type="button"
      title="删掉这一页"
      aria-label="删掉这一页"
      :disabled="!editable || slideCount <= 1"
      @click="$emit('remove')"
    >
      <el-icon><Delete /></el-icon>
    </button>

    <span class="rule" aria-hidden="true" />

    <div class="swatches" role="group" aria-label="幻灯皮肤">
      <button
        v-for="item in pptThemes"
        :key="item.id"
        class="swatch"
        type="button"
        :class="{ active: item.id === themeId }"
        :style="{ '--a': item.palette[0], '--b': item.palette[1] }"
        :title="`${item.label}：${item.note}`"
        :aria-label="item.label"
        @click="$emit('theme', item.id)"
      />
    </div>

    <span class="rule" aria-hidden="true" />

    <button class="tool tool-text" type="button" title="导出 PowerPoint（.pptx）" :disabled="!slideCount || exporting" @click="$emit('export')">
      {{ exporting ? '导出中' : 'PPTX' }}
    </button>

    <span class="rule" aria-hidden="true" />

    <PptSaveSeal :status="saveStatus" :message="saveMessage" />
  </div>
</template>

<script setup lang="ts">
import { Delete, Plus } from '@element-plus/icons-vue'
import { pptThemes } from '../../config/pptThemes'
import type { SaveStatus } from '../../composables/usePptStudio'
import PptSaveSeal from './PptSaveSeal.vue'

defineProps<{
  editable: boolean
  slideCount: number
  themeId: string
  saveStatus: SaveStatus
  saveMessage: string
  exporting: boolean
}>()

defineEmits<{
  add: []
  remove: []
  theme: [id: string]
  export: []
}>()
</script>

<style scoped lang="scss">
/* 压在舞台上的一支笔：悬浮、通体一致、没有卡片阴影堆叠。 */
.toolbar {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  height: 40px;
  padding: 0 10px;
  border: 1px solid var(--qh-line);
  border-radius: var(--qh-radius-pill);
  background: var(--qh-surface-raised);
  box-shadow: var(--qh-shadow-1), var(--qh-highlight);
}

.tool {
  display: grid;
  place-items: center;
  /* 被舞台宽度约束时 flex 子项默认会被压窄，中文按钮会折成两行。 */
  flex: none;
  white-space: nowrap;
  height: 32px;
  border: 0;
  border-radius: var(--qh-radius-pill);
  color: var(--qh-fg-muted);
  background: transparent;
  font-size: 14px;
  cursor: pointer;
  transition: color var(--qh-duration-fast) var(--qh-ease), background-color var(--qh-duration-fast) var(--qh-ease);
}

.tool-icon {
  width: 32px;
}

.tool-text {
  padding: 0 10px;
  font-size: var(--qh-text-footnote);
  letter-spacing: 0.5px;
}

.tool:hover:not(:disabled) {
  color: var(--qh-fg);
  background: var(--qh-surface-hover);
}

.tool:disabled {
  color: var(--qh-fg-faint);
  cursor: not-allowed;
}

.tool:focus-visible {
  outline: 2px solid var(--qh-accent-line);
  outline-offset: 1px;
}

.rule {
  width: 1px;
  height: 18px;
  margin: 0 6px;
  background: var(--qh-line);
}

.swatches {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

/* 双色小样：一眼看出这套纸的底色与主色。 */
.swatch {
  width: 22px;
  height: 14px;
  padding: 0;
  border: 1px solid var(--qh-line-strong);
  border-radius: 2px;
  background: linear-gradient(135deg, var(--a) 50%, var(--b) 50%);
  cursor: pointer;
  transition: transform var(--qh-duration-fast) var(--qh-ease), box-shadow var(--qh-duration-fast) var(--qh-ease);
}

.swatch:hover {
  transform: translateY(-1px);
}

.swatch.active {
  box-shadow: 0 0 0 2px var(--qh-surface-raised), 0 0 0 3px var(--qh-accent);
}

.swatch:focus-visible {
  outline: 2px solid var(--qh-accent-line);
  outline-offset: 2px;
}

@media (max-width: 1020px) {
  .toolbar {
    flex-wrap: wrap;
    height: auto;
    padding: 6px 10px;
    border-radius: var(--qh-radius-card-sm);
  }

  .rule {
    display: none;
  }
}
</style>
