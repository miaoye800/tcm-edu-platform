<template>
  <div class="toolbar" role="toolbar" aria-label="导图工具栏">
    <template v-if="editing">
      <button class="tool" type="button" title="加子节点" aria-label="加子节点" @click="$emit('add-child')">
        <el-icon><Plus /></el-icon>
      </button>
      <button class="tool" type="button" title="加同级节点" aria-label="加同级节点" @click="$emit('add-sibling')">
        <el-icon><Right /></el-icon>
      </button>
      <button class="tool" type="button" title="删除选中节点" aria-label="删除选中节点" @click="$emit('remove')">
        <el-icon><Delete /></el-icon>
      </button>
      <span class="rule" aria-hidden="true" />
    </template>

    <button class="tool tool-text" type="button" title="缩小" @click="$emit('zoom-out')">−</button>
    <button class="tool tool-text" type="button" title="适应画布" @click="$emit('fit')">适应</button>
    <button class="tool tool-text" type="button" title="放大" @click="$emit('zoom-in')">＋</button>

    <span class="rule" aria-hidden="true" />

    <StructureSwitch :structure-id="structureId" @change="$emit('structure', $event)" />

    <span class="rule" aria-hidden="true" />

    <ThemeSwitch :theme-id="themeId" @change="$emit('theme', $event)" />

    <span class="rule" aria-hidden="true" />

    <button class="tool tool-text" type="button" title="导出 SVG" @click="$emit('export-svg')">SVG</button>
    <button class="tool tool-text" type="button" title="导出 PNG" @click="$emit('export-png')">PNG</button>

    <span class="rule" aria-hidden="true" />

    <StudioSaveSeal :status="saveStatus" :message="saveMessage" />
  </div>
</template>

<script setup lang="ts">
import { Delete, Plus, Right } from '@element-plus/icons-vue'
import type { SaveStatus } from '../../composables/useMindmapStudio'
import type { StructureId } from '../../config/mindmapStructures'
import StructureSwitch from './StructureSwitch.vue'
import StudioSaveSeal from './StudioSaveSeal.vue'
import ThemeSwitch from './ThemeSwitch.vue'

defineProps<{ editing: boolean; themeId: string; structureId: string; saveStatus: SaveStatus; saveMessage: string }>()

defineEmits<{
  'add-child': []
  'add-sibling': []
  remove: []
  'zoom-in': []
  'zoom-out': []
  fit: []
  structure: [id: StructureId]
  theme: [id: string]
  'export-svg': []
  'export-png': []
}>()
</script>

<style scoped lang="scss">
/* 压在图纸上的一支笔：悬浮、通体一致、没有卡片阴影堆叠。 */
.toolbar {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  /* 用 min-height 而不是 height：内部有 32px 按钮 + 1px 描边，
     定高会在 1.25 倍行高下把内容挤出圆角。 */
  min-height: 40px;
  padding: 4px 10px;
  border: 1px solid var(--qh-line);
  border-radius: var(--qh-radius-pill);
  background: var(--qh-surface-raised);
  box-shadow: var(--qh-shadow-1), var(--qh-highlight);
}

.tool {
  display: grid;
  place-items: center;
  /* 工具条被画布宽度约束时，flex 子项默认会被压窄，「适应」会折成上下两行。 */
  flex: none;
  white-space: nowrap;
  width: 32px;
  height: 32px;
  border: 0;
  border-radius: var(--qh-radius-pill);
  color: var(--qh-fg-muted);
  background: transparent;
  font-size: 14px;
  cursor: pointer;
  transition: color var(--qh-duration-fast) var(--qh-ease), background-color var(--qh-duration-fast) var(--qh-ease);
}

.tool:hover {
  color: var(--qh-fg);
  background: var(--qh-surface-hover);
}

.tool:focus-visible {
  outline: 2px solid var(--qh-accent-line);
  outline-offset: 1px;
}

.tool-text {
  width: auto;
  min-width: 30px;
  padding: 0 8px;
  font-size: var(--qh-text-footnote);
  letter-spacing: 0.5px;
}

.rule {
  width: 1px;
  height: 18px;
  margin: 0 5px;
  background: var(--qh-line);
}

@media (max-width: 1240px) {
  /* 1180 上下这一档最尴尬：栏宽 800 左右，塞不下「编辑 + 缩放 + 结构 + 配色 + 导出 + 印章」。
     不换行就会溢出到画布外面，最后一个色卡和「已藏」印章被裁掉一半。 */
  .toolbar {
    flex-wrap: wrap;
    justify-content: center;
    max-width: 100%;
    height: auto;
    padding: 6px 10px;
    border-radius: var(--qh-radius-card-sm);
    row-gap: 4px;
  }

  .rule {
    display: none;
  }
}
</style>
