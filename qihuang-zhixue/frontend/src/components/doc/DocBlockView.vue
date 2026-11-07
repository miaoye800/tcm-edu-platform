<template>
  <div
    class="doc-block"
    :class="[`type-${block.type}`, { 'is-selected': selected, 'is-dragging': dragging, 'drop-before': dropHint === 'before', 'drop-after': dropHint === 'after' }]"
    :data-block-id="block.id"
    @mouseenter="hovered = true"
    @mouseleave="hovered = false"
  >
    <!-- 左侧 gutter：Notion 的 + 与 ⠿ 就悬浮在这里，不占正文宽度 -->
    <div v-if="editable" class="block-gutter" :class="{ visible: hovered || selected }">
      <button class="gutter-btn" type="button" title="在下方插入" @mousedown.prevent="emit('insert-after', block.id)">
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3v10M3 8h10" /></svg>
      </button>
      <button
        class="gutter-btn gutter-handle"
        type="button"
        title="拖动或打开菜单"
        draggable="true"
        @mousedown="onHandleMouseDown"
        @click="emit('menu', { id: block.id, x: $event.clientX, y: $event.clientY })"
        @dragstart="onDragStart"
        @dragend="onDragEnd"
      >
        <svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="6" cy="4" r="1.1" /><circle cx="10" cy="4" r="1.1" /><circle cx="6" cy="8" r="1.1" /><circle cx="10" cy="8" r="1.1" /><circle cx="6" cy="12" r="1.1" /><circle cx="10" cy="12" r="1.1" /></svg>
      </button>
    </div>

    <div class="block-body" @click="emit('select', block.id)">
      <component
        :is="contentComponent"
        :block="block"
        :editable="editable"
        :index="index"
        @update="handleUpdate"
        @enter="handleEnter"
        @backspace-empty="emit('backspace-empty')"
        @slash="emit('slash', { id: block.id })"
      >
        <template v-if="block.type === 'columns'">
          <div v-for="(child, childIndex) in block.children || []" :key="child.id" class="column-cell">
            <DocBlockView
              :block="child"
              :index="childIndex"
              :editable="editable"
              @update="value => updateChild(value)"
              @enter="payload => emit('column-enter', { parent: block.id, child: child.id, payload })"
              @backspace-empty="emit('column-backspace', { parent: block.id, child: child.id })"
              @slash="emit('slash', { id: child.id })"
              @insert-after="emit('column-insert-after', { parent: block.id, child: child.id })"
            />
          </div>
        </template>
      </component>
    </div>

    <!-- 容器块：children 由自身递归渲染，折叠态也只是隐藏，不销毁数据 -->
    <div v-if="block.type === 'toggle' && !collapsed && (block.children || []).length" class="block-children">
      <DocBlockView
        v-for="(child, childIndex) in block.children"
        :key="child.id"
        :block="child"
        :index="childIndex"
        :editable="editable"
        @update="value => updateChild(value)"
        @enter="payload => emit('child-enter', { parent: block.id, child: child.id, payload })"
        @backspace-empty="emit('child-backspace', { parent: block.id, child: child.id })"
        @slash="emit('slash', { id: child.id })"
        @insert-after="emit('child-insert-after', { parent: block.id, child: child.id })"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import type { BlockType, DocBlock } from '../../types/doc'
import ParagraphBlock from './blocks/ParagraphBlock.vue'
import HeadingBlock from './blocks/HeadingBlock.vue'
import ListItemBlock from './blocks/ListItemBlock.vue'
import ToggleBlock from './blocks/ToggleBlock.vue'
import QuoteBlock from './blocks/QuoteBlock.vue'
import CalloutBlock from './blocks/CalloutBlock.vue'
import DividerBlock from './blocks/DividerBlock.vue'
import CodeBlock from './blocks/CodeBlock.vue'
import TableBlock from './blocks/TableBlock.vue'
import ImageBlock from './blocks/ImageBlock.vue'
import ColumnsBlock from './blocks/ColumnsBlock.vue'
import PageLinkBlock from './blocks/PageLinkBlock.vue'

/**
 * 单块渲染器。
 *
 * 用注册表把 type 映射到组件，而不是一长串 v-if —— 这对应 Notion 的
 * 「块类型 → 渲染组件」映射：新增一种块只改映射表。
 * 列表容器仍在 DocResourceView 里（逐块淡入动效与既有测试依赖它），
 * 因此这里只负责「一个块」以及它自己的子块递归。
 */
const props = defineProps<{
  block: DocBlock
  index: number
  editable: boolean
  selected?: boolean
  dragging?: boolean
  dropHint?: 'before' | 'after' | null
}>()

const emit = defineEmits<{
  (e: 'update', block: DocBlock): void
  (e: 'enter', payload: { before: string; after: string }): void
  (e: 'backspace-empty'): void
  (e: 'select', id: string): void
  (e: 'menu', payload: { id: string; x: number; y: number }): void
  (e: 'slash', payload: { id: string }): void
  (e: 'insert-after', id: string): void
  (e: 'drag-start', id: string): void
  (e: 'drag-end'): void
  (e: 'child-enter', payload: { parent: string; child: string; payload: { before: string; after: string } }): void
  (e: 'child-backspace', payload: { parent: string; child: string }): void
  (e: 'child-insert-after', payload: { parent: string; child: string }): void
  (e: 'column-enter', payload: { parent: string; child: string; payload: { before: string; after: string } }): void
  (e: 'column-backspace', payload: { parent: string; child: string }): void
  (e: 'column-insert-after', payload: { parent: string; child: string }): void
}>()

const hovered = ref(false)

/** 动态组件（<component :is>）无法给模板内联箭头函数推导参数类型，这里显式收口 */
const handleUpdate = (value: DocBlock) => emit('update', value)
const handleEnter = (payload: { before: string; after: string }) => emit('enter', payload)

const COMPONENTS: Record<BlockType, unknown> = {
  paragraph: ParagraphBlock,
  heading1: HeadingBlock,
  heading2: HeadingBlock,
  heading3: HeadingBlock,
  bulleted: ListItemBlock,
  numbered: ListItemBlock,
  todo: ListItemBlock,
  toggle: ToggleBlock,
  quote: QuoteBlock,
  callout: CalloutBlock,
  divider: DividerBlock,
  code: CodeBlock,
  table: TableBlock,
  image: ImageBlock,
  columns: ColumnsBlock,
  page: PageLinkBlock,
}

const contentComponent = computed(() => COMPONENTS[props.block.type] || ParagraphBlock)
const collapsed = computed(() => props.block.props?.collapsed !== false)

const updateChild = (child: DocBlock) => {
  const children = (props.block.children || []).map(item => (item.id === child.id ? child : item))
  emit('update', { ...props.block, children })
}

const onHandleMouseDown = () => {
  // 只有从手柄按下才允许拖动：否则正文里选词会误触发整块拖拽
  document.body.classList.add('doc-dragging')
}

const onDragStart = (event: DragEvent) => {
  event.dataTransfer?.setData('text/plain', props.block.id)
  if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move'
  emit('drag-start', props.block.id)
}

const onDragEnd = () => {
  document.body.classList.remove('doc-dragging')
  emit('drag-end')
}
</script>

<style scoped lang="scss">
.doc-block {
  position: relative;
  padding: 1px 0;
}

.doc-block.is-selected {
  border-radius: 3px;
  background: color-mix(in srgb, var(--qh-slate-400) 8%, transparent);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--qh-slate-400) 55%, transparent);
}

.doc-block.is-dragging {
  opacity: 0.35;
}

.doc-block.drop-before::before,
.doc-block.drop-after::after {
  position: absolute;
  right: 0;
  left: 0;
  height: 2px;
  border-radius: 2px;
  background: var(--qh-slate-400);
  content: '';
}

.doc-block.drop-before::before { top: -1px; }
.doc-block.drop-after::after { bottom: -1px; }

/* gutter 绝对定位在正文左侧：手柄出现时正文不位移，避免整页抖动 */
.block-gutter {
  position: absolute;
  top: 0;
  left: -46px;
  display: flex;
  gap: 1px;
  align-items: flex-start;
  padding-top: 1px;
  opacity: 0;
  transition: opacity 100ms ease;
}

.block-gutter.visible {
  opacity: 1;
}

.gutter-btn {
  display: grid;
  width: 20px;
  height: 26px;
  padding: 0;
  place-items: center;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--qh-fg-faint);
  cursor: pointer;

  svg {
    width: 14px;
    height: 14px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.4;
    stroke-linecap: round;
  }

  &:hover {
    color: var(--qh-fg-muted);
    background: color-mix(in srgb, var(--qh-fg) 7%, transparent);
  }
}

.gutter-handle {
  cursor: grab;

  svg {
    fill: currentColor;
    stroke: none;
  }

  &:active {
    cursor: grabbing;
  }
}

.block-body {
  min-width: 0;
}

.block-children {
  margin-left: 22px;
  padding-left: 2px;
}

.column-cell {
  min-width: 0;
}
</style>
