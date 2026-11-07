<template>
  <main class="lab">
    <header class="lab-head">
      <div>
        <p class="kicker">MINDMAP LAYOUT LAB / 版式验证</p>
        <h1>四结构 × 四主题</h1>
        <p class="intro">
          用真实的「解表药」知识树验证两件事：四种结构能不能画对，四套配色是不是同一族。
          仅开发环境可见，不打包进生产。
        </p>
      </div>
      <dl class="meta">
        <div><dt>节点</dt><dd>{{ fixtureCount }}</dd></div>
        <div><dt>层数</dt><dd>{{ fixtureDepth }}</dd></div>
      </dl>
    </header>

    <nav class="controls" aria-label="验证参数">
      <div class="seg">
        <button type="button" :class="{ on: mode === 'theme' }" @click="mode = 'theme'">按主题对比</button>
        <button type="button" :class="{ on: mode === 'structure' }" @click="mode = 'structure'">按结构对比</button>
      </div>

      <label class="field">
        <span>数据密度</span>
        <select v-model="density">
          <option value="normal">常规（{{ counts.normal }} 节点）</option>
          <option value="dense">压力（{{ counts.dense }} 节点）</option>
        </select>
      </label>

      <label v-if="mode === 'theme'" class="field">
        <span>固定结构</span>
        <select v-model="fixedStructure">
          <option v-for="item in mindmapStructures" :key="item.id" :value="item.id">{{ item.name }}</option>
        </select>
      </label>

      <label v-else class="field">
        <span>固定配色</span>
        <select v-model="fixedTheme">
          <option v-for="item in mindmapThemes" :key="item.id" :value="item.id">{{ item.name }}</option>
        </select>
      </label>

      <div class="seg">
        <button type="button" :class="{ on: viewMode === 'fit' }" @click="viewMode = 'fit'">全局</button>
        <button type="button" :class="{ on: viewMode === 'detail' }" @click="viewMode = 'detail'">细节 100%</button>
      </div>
    </nav>

    <section class="rows">
      <article v-for="row in rows" :key="row.key" class="row">
        <header class="row-head">
          <div class="row-title">
            <i class="dot" :style="{ background: row.accent }" aria-hidden="true" />
            <strong>{{ row.title }}</strong>
            <span class="row-sub">{{ row.subtitle }}</span>
          </div>
          <span class="row-note">{{ row.note }}</span>
        </header>
        <div :ref="el => setHost(row.key, el)" class="row-canvas" />
      </article>
    </section>
  </main>
</template>

<script setup lang="ts">
import MindMap from 'simple-mind-map'
import 'simple-mind-map/dist/simpleMindMap.esm.css'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  buildSimpleTheme,
  defaultThemeId,
  getThemePreset,
  mindmapThemes,
} from '../config/mindmapThemes'
import { defaultStructureId, getStructure, mindmapStructures } from '../config/mindmapStructures'
import normalFixture from '../config/mindmap-fixtures/jiebiao.json'
import denseFixture from '../config/mindmap-fixtures/jiebiao-dense.json'
import { toSimpleTree } from '../utils/mindmapTree'

type Mode = 'theme' | 'structure'

const mode = ref<Mode>('theme')
const density = ref<'normal' | 'dense'>('normal')
const fixedStructure = ref(defaultStructureId)
const fixedTheme = ref(defaultThemeId)
/** 全局看轮廓，细节 100% 看字号/边框/连线质感——一张图装不下这两件事。 */
const viewMode = ref<'fit' | 'detail'>('fit')

const fixture = computed(() => (density.value === 'dense' ? denseFixture : normalFixture))

const countNodes = (node: any): number => 1 + (node?.children ?? []).reduce((sum: number, child: any) => sum + countNodes(child), 0)
const depthOf = (node: any): number => 1 + Math.max(0, ...(node?.children ?? []).map((child: any) => depthOf(child)))

const counts = {
  normal: countNodes(normalFixture as any),
  dense: countNodes(denseFixture as any),
}
const fixtureCount = computed(() => countNodes(fixture.value as any))
const fixtureDepth = computed(() => depthOf(fixture.value as any))

interface Row {
  key: string
  title: string
  subtitle: string
  note: string
  accent: string
  structureId: string
  themeId: string
}

const rows = computed<Row[]>(() => {
  if (mode.value === 'theme') {
    const structure = getStructure(fixedStructure.value)
    return mindmapThemes.map(preset => ({
      key: `theme-${preset.id}`,
      title: preset.name,
      subtitle: preset.vibe,
      note: `${structure.name} · ${structure.layout}`,
      accent: preset.swatches[0],
      structureId: structure.id,
      themeId: preset.id,
    }))
  }
  const preset = getThemePreset(fixedTheme.value)
  return mindmapStructures.map(structure => ({
    key: `structure-${structure.id}`,
    title: structure.name,
    subtitle: structure.when,
    note: `${structure.layout} · 最多 ${structure.maxDepth} 层`,
    accent: preset.swatches[0],
    structureId: structure.id,
    themeId: preset.id,
  }))
})

const hosts = new Map<string, HTMLElement>()
const instances = new Map<string, any>()
/** 每次重建自增；异步回调回来时用它判断自己是否已经过期。 */
let buildSeq = 0
const wait = (ms: number) => new Promise(resolve => window.setTimeout(resolve, ms))

const setHost = (key: string, el: unknown) => {
  if (el instanceof HTMLElement) hosts.set(key, el)
  else hosts.delete(key)
}

const destroyAll = () => {
  instances.forEach(instance => {
    try {
      instance.destroy()
    } catch {
      /* 组件卸载时库已自毁，忽略 */
    }
  })
  instances.clear()
}

const build = async () => {
  await nextTick()
  const seq = ++buildSeq
  destroyAll()
  for (const row of rows.value) {
    const el = hosts.get(row.key)
    if (!el) continue
    el.replaceChildren()
    const structure = getStructure(row.structureId)
    const preset = getThemePreset(row.themeId)
    const instance = new MindMap({
      el,
      // opt.data 是 wrapper 根节点：{ data: {...}, children: [...] }。
      // 直接传扁平节点会让库找不到根，所有子节点退化成并列的一列。
      data: toSimpleTree(JSON.parse(JSON.stringify(fixture.value)) as any, { fourTier: true }),
      layout: structure.layout,
      themeConfig: buildSimpleTheme(preset, structure),
      readonly: true,
      fit: viewMode.value === 'fit',
      // 折叠数字比一个光秃秃的三角更能说明「这里还有东西」。
      isShowExpandNum: true,
      mousewheelAction: 'move',
      disableMouseWheelZoom: true,
      enableDblclickBackToRootNode: false,
      maxTag: 0,
    } as any)
    instances.set(row.key, instance)
  }
  if (import.meta.env.DEV) (window as any).__lab = instances

  const applyView = () => {
    if (seq !== buildSeq) return
    instances.forEach(instance => {
      try {
        // reset 把缩放归 1 并把根节点摆回画布中心，相当于「看细节」
        if (viewMode.value === 'detail') instance.view?.reset?.()
        else instance.view?.fit?.()
      } catch {
        // 布局还在算的时候 fit 可能拿到空的包围盒；交给下一轮重试，
        // 不能让一个实例的异常中断后面三行。
      }
    })
  }

  await nextTick()
  // 组织结构与鱼骨的布局是异步算完的，库的 render 回调常常早于布局结束。
  // 只 fit 一次会得到「按原始尺寸溢出容器」的图，所以分三轮压过去。
  for (const delay of [0, 260, 700]) {
    if (delay) await wait(delay)
    applyView()
  }
}

watch([mode, density, fixedStructure, fixedTheme, viewMode], () => void build())
onMounted(() => void build())
onBeforeUnmount(destroyAll)
</script>

<style scoped lang="scss">
.lab {
  min-height: 100vh;
  padding: 40px 48px 64px;
  background: var(--qh-bg);
  color: var(--qh-fg);
}

.lab-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 20px;

  h1 {
    margin: 4px 0 8px;
    font-family: var(--qh-font-display);
    font-size: 30px;
    font-weight: 600;
    letter-spacing: 0.5px;
  }
}

.kicker {
  margin: 0;
  color: var(--qh-fg-muted);
  font-size: 11px;
  letter-spacing: 2px;
  text-transform: uppercase;
}

.intro {
  max-width: 720px;
  margin: 0;
  color: var(--qh-fg-soft);
  font-size: 13px;
  line-height: 1.7;
}

.meta {
  display: flex;
  gap: 20px;
  margin: 0;

  div {
    text-align: right;
  }

  dt {
    color: var(--qh-fg-muted);
    font-size: 11px;
  }

  dd {
    margin: 0;
    font-family: var(--qh-font-mono, monospace);
    font-size: 20px;
  }
}

.controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  margin-bottom: 20px;
  border: 1px solid var(--qh-line);
  border-radius: var(--qh-radius-panel);
  background: var(--qh-surface);
}

.seg {
  display: flex;
  padding: 2px;
  border: 1px solid var(--qh-line);
  border-radius: 8px;
  background: var(--qh-surface-sunken);

  button {
    padding: 6px 14px;
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

.field {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--qh-fg-muted);
  font-size: 12px;

  select {
    padding: 5px 8px;
    border: 1px solid var(--qh-line);
    border-radius: 6px;
    background: var(--qh-surface-raised);
    color: var(--qh-fg);
    font-size: 12px;
  }
}

.rows {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.row {
  overflow: hidden;
  border: 1px solid var(--qh-line);
  border-radius: var(--qh-radius-panel);
  background: var(--qh-surface-raised);
}

.row-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 16px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--qh-line);
  background: var(--qh-surface);
}

.row-title {
  display: flex;
  align-items: baseline;
  gap: 8px;

  strong {
    font-size: 14px;
    font-weight: 600;
  }
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  transform: translateY(-2px);
}

.row-sub,
.row-note {
  color: var(--qh-fg-muted);
  font-size: 11px;
}

.row-canvas {
  height: 520px;
  background: #fff;
}
</style>
