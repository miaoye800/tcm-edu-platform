<template>
  <main class="harness">
    <header class="head">
      <div>
        <p class="kicker">MINDMAP CANVAS HARNESS / 画布联调</p>
        <h1>StudioCanvas 真实组件验证</h1>
        <p class="intro">
          这里挂的是工坊里那个 <code>StudioCanvas.vue</code>，不是复刻版：用真实的解表药树驱动
          生长、切结构、换配色、编辑、导出，验证适配层而不是验证库。
        </p>
      </div>
      <dl class="stats">
        <div><dt>节点</dt><dd>{{ stats.nodes }}</dd></div>
        <div><dt>层数</dt><dd>{{ stats.depth }}</dd></div>
        <div><dt>教材</dt><dd>{{ stats.textbook }}</dd></div>
        <div><dt>推断</dt><dd>{{ stats.model }}</dd></div>
      </dl>
    </header>

    <nav class="controls">
      <div class="seg">
        <button type="button" :class="{ on: density === 'normal' }" @click="load('normal')">常规 {{ counts.normal }}</button>
        <button type="button" :class="{ on: density === 'dense' }" @click="load('dense')">压力 {{ counts.dense }}</button>
      </div>
      <div class="seg">
        <button type="button" :class="{ on: mode === 'settled' }" @click="settle">直接落图</button>
        <button type="button" :class="{ on: mode === 'grow' }" @click="grow">逐批生长</button>
      </div>
      <div class="seg">
        <button type="button" :class="{ on: editing }" @click="editing = !editing">{{ editing ? '编辑中' : '只读' }}</button>
        <button type="button" @click="canvas?.addChild()">加子节点</button>
        <button type="button" @click="canvas?.removeSelected()">删选中</button>
        <button type="button" @click="dump">打印树</button>
      </div>
      <span class="note">当前：{{ structureName }} · {{ themeName }}</span>
    </nav>

    <section class="stage">
      <div class="toolbar-slot">
        <StudioToolbar
          :editing="editing"
          :theme-id="themeId"
          :structure-id="structureId"
          :save-status="'clean'"
          save-message="未藏"
          @add-child="canvas?.addChild()"
          @add-sibling="canvas?.addSibling()"
          @remove="canvas?.removeSelected()"
          @zoom-in="canvas?.zoom(1.15)"
          @zoom-out="canvas?.zoom(0.87)"
          @fit="canvas?.fit()"
          @structure="structureId = $event"
          @theme="themeId = $event"
          @export-svg="exportSvg"
          @export-png="exportPng"
        />
      </div>
      <StudioCanvas
        ref="canvasRef"
        :theme-id="themeId"
        :structure-id="structureId"
        :editing="editing"
        :live="mode === 'grow' && growing"
        @stats="stats = $event"
        @dirty="dirtyCount += 1"
        @finish="growing = false"
      />
      <p v-if="dirtyCount" class="dirty">dirty 事件 {{ dirtyCount }} 次</p>
    </section>
  </main>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import StudioCanvas from '../components/mindmap/StudioCanvas.vue'
import StudioToolbar from '../components/mindmap/StudioToolbar.vue'
import { defaultThemeId, getThemePreset } from '../config/mindmapThemes'
import { defaultStructureId, getStructure, type StructureId } from '../config/mindmapStructures'
import normalFixture from '../config/mindmap-fixtures/jiebiao.json'
import denseFixture from '../config/mindmap-fixtures/jiebiao-dense.json'
import type { ResourceMindmapNode } from '../types/resource'
import type { MindmapTreeNode } from '../utils/mindmapTree'

interface CanvasApi {
  reset: () => void
  pushNode: (node: ResourceMindmapNode) => void
  finish: () => void
  setTree: (data: unknown) => void
  getTree: () => MindmapTreeNode | null
  zoom: (factor: number) => void
  fit: () => void
  addChild: () => void
  addSibling: () => void
  removeSelected: () => void
  exportSvg: () => Blob | undefined
  exportPng: () => Promise<Blob | null> | undefined
}

const canvasRef = ref<CanvasApi | null>(null)
const canvas = computed(() => canvasRef.value)
const themeId = ref(defaultThemeId)
const structureId = ref<StructureId>(defaultStructureId)
const editing = ref(false)
const density = ref<'normal' | 'dense'>('normal')
const mode = ref<'settled' | 'grow'>('settled')
const growing = ref(false)
const dirtyCount = ref(0)
const stats = ref({ nodes: 0, depth: 0, textbook: 0, graph: 0, model: 0 })

const countNodes = (node: any): number => 1 + (node?.children ?? []).reduce((sum: number, child: any) => sum + countNodes(child), 0)
const counts = { normal: countNodes(normalFixture as any), dense: countNodes(denseFixture as any) }

const structureName = computed(() => getStructure(structureId.value).name)
const themeName = computed(() => getThemePreset(themeId.value).name)

/** 把扁平 fixture 摊成后端推流那种逐节点事件序列（父先于子）。 */
const toEvents = (root: any): ResourceMindmapNode[] => {
  const rows: ResourceMindmapNode[] = []
  let total = countNodes(root)
  const walk = (node: any, parentId: string | null, depth: number, order: number) => {
    rows.push({
      id: String(node.id),
      parent_id: parentId,
      topic: String(node.topic),
      depth,
      order,
      direction: order % 2,
      source_indexes: depth <= 1 ? [0] : [],
      node_total: total,
      // 刻意给最深一层打「推断」标，验证角标随节点走。
      ...(depth >= 3 ? { evidence: 'model' as const } : { evidence: 'textbook' as const }),
    })
    ;(node.children ?? []).forEach((child: any, index: number) => walk(child, String(node.id), depth + 1, index))
  }
  walk(root, null, 0, 0)
  return rows
}

const load = (next: 'normal' | 'dense') => {
  density.value = next
  if (mode.value === 'settled') settle()
  else grow()
}

const settle = () => {
  mode.value = 'settled'
  growing.value = false
  const fixture = density.value === 'dense' ? denseFixture : normalFixture
  canvas.value?.setTree(JSON.parse(JSON.stringify(fixture)))
}

const grow = () => {
  mode.value = 'grow'
  growing.value = true
  dirtyCount.value = 0
  canvas.value?.reset()
  const fixture = density.value === 'dense' ? denseFixture : normalFixture
  const events = toEvents(JSON.parse(JSON.stringify(fixture)))
  events.forEach(event => canvas.value?.pushNode(event))
  // 队列排空后 canvas 会自己 finish；这里只兜底，防止中途异常卡住。
  window.setTimeout(() => { canvas.value?.finish(); growing.value = false }, 9000)
}

const dump = () => {
  const tree = canvas.value?.getTree()
  // eslint-disable-next-line no-console
  console.log('[harness] tree', tree && countNodes(tree), tree)
}

const download = (blob: Blob, extension: string) => {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `harness.${extension}`
  anchor.click()
  URL.revokeObjectURL(url)
}
const exportSvg = () => {
  const blob = canvas.value?.exportSvg()
  if (blob) download(blob, 'svg')
}
const exportPng = async () => {
  const blob = await canvas.value?.exportPng()
  if (blob) download(blob, 'png')
}

onMounted(() => settle())
</script>

<style scoped lang="scss">
.harness {
  min-height: 100vh;
  padding: 32px 40px 48px;
  background: var(--qh-bg);
  color: var(--qh-fg);
}

.head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 16px;

  h1 {
    margin: 4px 0 8px;
    font-family: var(--qh-font-display);
    font-size: 26px;
    font-weight: 600;
  }
}

.kicker {
  margin: 0;
  color: var(--qh-fg-muted);
  font-size: 11px;
  letter-spacing: 2px;
}

.intro {
  max-width: 760px;
  margin: 0;
  color: var(--qh-fg-soft);
  font-size: 13px;
  line-height: 1.7;

  code {
    padding: 1px 5px;
    border-radius: 3px;
    background: var(--qh-surface-sunken);
    font-size: 12px;
  }
}

.stats {
  display: flex;
  gap: 18px;
  margin: 0;

  div { text-align: right; }
  dt { color: var(--qh-fg-muted); font-size: 11px; }
  dd { margin: 0; font-family: var(--qh-font-mono, monospace); font-size: 18px; }
}

.controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
}

.seg {
  display: flex;
  padding: 2px;
  border: 1px solid var(--qh-line);
  border-radius: 8px;
  background: var(--qh-surface-sunken);

  button {
    padding: 5px 11px;
    border: 0;
    border-radius: 6px;
    background: transparent;
    color: var(--qh-fg-soft);
    font-size: 12px;
    cursor: pointer;

    &.on {
      background: var(--qh-surface-raised);
      color: var(--qh-fg);
      box-shadow: var(--qh-shadow-1);
    }
  }
}

.note {
  color: var(--qh-fg-muted);
  font-size: 12px;
}

.stage {
  position: relative;
  height: calc(100vh - 260px);
  min-height: 480px;
  border: 1px solid var(--qh-line);
  border-radius: var(--qh-radius-panel);
  background: var(--qh-surface-sunken);
}

.toolbar-slot {
  position: absolute;
  top: 14px;
  left: 50%;
  z-index: 4;
  max-width: calc(100% - 20px);
  transform: translateX(-50%);
}

.dirty {
  position: absolute;
  right: 16px;
  bottom: 12px;
  margin: 0;
  color: var(--qh-fg-muted);
  font-size: 11px;
}
</style>
