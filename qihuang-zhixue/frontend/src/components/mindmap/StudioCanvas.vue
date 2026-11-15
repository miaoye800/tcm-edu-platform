<template>
  <div class="canvas" :style="cssVars" :class="{ 'is-live': live }">
    <div class="canvas-grid" aria-hidden="true" />
    <div ref="host" class="canvas-host" />
    <div class="canvas-corner" aria-hidden="true" />
    <div v-if="notice" class="canvas-notice">{{ notice }}</div>
    <div v-if="zoomHint" class="canvas-hint">{{ zoomHint }}</div>
  </div>
</template>

<script setup lang="ts">
/**
 * 画布适配层：simple-mind-map → 工坊。
 *
 * 对外只暴露「树 + 外观 + 视图」三类操作，组件内不出现库的术语，
 * 这样以后换渲染内核时，改动只落在这一个文件。
 *
 * 三处必须按库的脾气来写（P0 已实测）：
 *   1. 数据必须是 wrapper 树、文字字段叫 text —— 转换集中在 utils/mindmapTree.ts。
 *   2. 组织结构/鱼骨的布局异步算完，fit 要分三轮重试。
 *   3. 逐节点样式覆盖优先于主题，四档层级靠它补齐（见 toSimpleTree 的 fourTier）。
 */
import MindMap from 'simple-mind-map'
// 基础入口（index.js）不带 Drag：节点只能看不能拖。拖拽换父级是编辑的基本操作，
// 所以显式注册这一个插件，而不是整包换 full.js（那会把导出 PDF 等一堆依赖拖进来）。
import Drag from 'simple-mind-map/src/plugins/Drag.js'
import 'simple-mind-map/dist/simpleMindMap.esm.css'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  buildSimpleTheme,
  getThemePreset,
  tagsColorMap,
  themeCssVars,
} from '../../config/mindmapThemes'
import { getStructure } from '../../config/mindmapStructures'
import { GROW_BATCH, GROW_MS, NODE_INTERVAL_MS, QUEUE_LIMIT } from '../../config/mindmapStudio'
import type { ResourceMindmapNode } from '../../types/resource'
import {
  anchorsOf,
  countFolded,
  countNodes,
  depthOf,
  fromSimpleTree,
  toSimpleTree,
  type MindmapTreeNode,
  type SimpleTreeOptions,
} from '../../utils/mindmapTree'

const props = defineProps<{
  themeId: string
  structureId: string
  editing: boolean
  live: boolean
}>()

// 插件是类级别的，注册一次即可。重复注册会让同一个事件挂两遍监听。
MindMap.usePlugin(Drag)

const emit = defineEmits<{
  dirty: []
  stats: [value: { nodes: number; depth: number; textbook: number; graph: number; model: number; folded: number }]
  finish: []
  /** 计划快照的叶子被点中：带 node_id 回地图。没有 node_id 的节点不触发。 */
  nodeSelect: [nodeId: string]
}>()

const host = ref<HTMLElement | null>(null)
const notice = ref('')
const zoomHint = ref('')
const cssVars = computed(() => themeCssVars(getThemePreset(props.themeId)))

/** 库的类型声明很宽，这里统一收口到 any，避免模板/事件被 TS 噪音淹没。 */
let mm: any = null
let disposed = false
let timer = 0
let draining = false
let pendingDone = false
/** setData 重建期间要压住 dirty：库会在 addHistory 时派发 data_change。 */
let suppressDirty = false
/**
 * 生长期间「树的所有权」归内存树，不归渲染库。
 *
 * `setData` 派发的 `data_change` 是**异步**的：等它到达时 `suppressDirty` 早已
 * 落回 false，而库里的 `renderTree` 还是上一版快照。此时若按库回写 `tree`，
 * 内存树会被自己两批之前的模样覆盖；更要命的是 `index` 仍指向被覆盖掉的那批
 * 对象，后续节点全部挂到一棵已经没人读的树上——队列看着在正常消耗、孤儿数为 0，
 * 画布和保存却只剩最初几个节点（实测 25 个节点只剩 5 个）。
 *
 * 所以生长一开始就上锁，直到布局真正渲染完（`node_tree_render_end`）才交还。
 */
let growing = false
/** 已请求交还所有权，等下一次渲染结束事件落地。 */
let handoverArmed = false
/** 有一次 fit 待补：等库把布局算完（node_tree_render_end）再对镜头。 */
let pendingFit = false

const queue: ResourceMindmapNode[] = []
const deferred: ResourceMindmapNode[] = []
const seen = new Set<string>()
/** 收到但父级始终没到的节点，收卷时提示一次。 */
let orphanCount = 0
let tree: MindmapTreeNode | null = null
const index = new Map<string, MindmapTreeNode>()
/** 挂在「这一次渲染结束」上的等待者。生长必须一批画完再交下一批。 */
const renderWaiters: Array<() => void> = []

/**
 * 等库把这一版布局画完。
 *
 * 生长期间每批都调一次 `setData`，而库的渲染是异步的：不等它画完就交下一批，
 * 两次渲染会叠在一起跑（`isRendering` 只挡住并发，挡不住「上上版」的节点
 * 因为清理时机错位而留在 DOM 里）。实测到的现象是整棵树多出一层影子——
 * 数据 18 个节点，画布上看起来 36 个，文字互相压着。
 */
const afterRender = (timeout = 420) =>
  new Promise<void>(resolve => {
    if (!mm) {
      resolve()
      return
    }
    let settled = false
    const fire = () => {
      if (settled) return
      settled = true
      resolve()
    }
    renderWaiters.push(fire)
    // 布局没变化时库可能不派发事件，留一个兜底，别把生长卡死。
    window.setTimeout(fire, timeout)
  })

/** 渲染格式的选项：四档层级 + 按当前结构折叠超层。结构与配色一样是纯视图属性。 */
const simpleOptions = (): SimpleTreeOptions => ({
  fourTier: true,
  maxDepth: getStructure(props.structureId).maxDepth,
})

const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
const wait = (ms: number) => new Promise(resolve => window.setTimeout(resolve, reducedMotion() ? 0 : ms))

const reportStats = () => {
  if (!tree) {
    emit('stats', { nodes: 0, depth: 0, textbook: 0, graph: 0, model: 0, folded: 0 })
    return
  }
  // 三档分开数：底栏要显示「教材 / 图谱 / 推断」各占多少，合并成一个数就说不清了。
  let textbook = 0
  let graph = 0
  let model = 0
  const stack: MindmapTreeNode[] = [tree]
  while (stack.length) {
    const node = stack.pop() as MindmapTreeNode
    if (node.evidence === 'model') model += 1
    else if (node.evidence === 'graph') graph += 1
    else if (node.evidence === 'textbook') textbook += 1
    stack.push(...(node.children ?? []))
  }
  // folded：当前结构装不下的层被折起来多少个。状态栏要说得出这个数，
  // 否则「点了鱼骨图，图变少了」就成了一个没法解释的现象。
  const foldLimit = getStructure(props.structureId).maxDepth
  emit('stats', {
    nodes: countNodes(tree),
    depth: depthOf(tree),
    textbook,
    graph,
    model,
    folded: countFolded(tree, foldLimit),
  })
}

const buildOption = (data: any) => {
  const structure = getStructure(props.structureId)
  return {
    el: host.value,
    // 0.14 的入口数据是 wrapper 根节点 { data: {...}, children: [...] }。
    data,
    layout: structure.layout,
    themeConfig: buildSimpleTheme(getThemePreset(props.themeId), structure),
    readonly: !props.editing,
    // 中文字典：新增节点的默认名不能是英文，否则会原样存进库里。
    defaultInsertSecondLevelNodeText: '新分支',
    defaultInsertBelowSecondLevelNodeText: '新要点',
    maxTag: 1,
    tagsColorMap,
    // 折叠数字比一个光秃秃的三角更能说明「这里还有东西」。
    isShowExpandNum: true,
    mousewheelAction: 'zoom',
    minZoomRatio: 20,
    maxZoomRatio: 400,
    scaleRatio: 0.15,
    enableDblclickBackToRootNode: false,
    // 工具条是浮在图纸上的一支笔（top:16px + 高 40px），留白少于它就会把
    // 最上面那一行叶子压住——看着像被裁掉了，其实是躲到浮层底下。
    fitPadding: 72,
  }
}

const destroy = () => {
  if (!mm) return
  try {
    mm.destroy()
  } catch {
    /* 卸载时库可能已自毁 */
  }
  mm = null
  zoomHint.value = ''
}

/**
 * 看不清的界线是 0.5，不是 0.36。
 *
 * 正文 13px，缩到 50% 就只剩 6.5px——已经不是「有点小」而是「读不出字」。
 * 原先卡在 0.36，正好漏掉实测 0.376 的向下分支：图明显糊了，界面却一声不吭。
 */
const READABLE_SCALE = 0.5

/** 缩到看不清时把话说明白——比让人盯着一团灰线猜要好。 */
const syncScale = () => {
  const scale = Number(mm?.view?.scale ?? 1)
  zoomHint.value = scale < READABLE_SCALE ? `全图缩至 ${Math.round(scale * 100)}%，滚轮可放大细看` : ''
}

const attach = (data: any): boolean => {
  const el = host.value
  if (!el) return false
  destroy()
  el.replaceChildren()
  mm = new MindMap(buildOption(data) as any)
  mm.on('data_change', () => {
    // 生成期每轮 setData 也会触发 data_change，而那一刻库的 renderTree 还是**上一版**。
    // 两个都必须挡住：suppressDirty 只覆盖 setData 的那一拍（它是同步的），
    // growing 覆盖整段生长期（异步派发的 data_change 全落在后面）。
    if (suppressDirty || growing) return
    // 用户改完树后画布内存树要跟着更新，否则底栏统计会停在生成时那一刻。
    try {
      tree = fromSimpleTree(mm.getData())
      reportStats()
    } catch {
      /* 库正在重绘，下一轮再算 */
    }
    if (!props.editing) return
    emit('dirty')
  })
  // 计划快照：叶子带 node_id，点一下应该能跳回地图。**不自动跳**——
  // 用户可能只是想展开看一眼，把他拽走等于替他决定。
  // 库的事件联合类型里没有 node_click，但它确实会派发（见 src/event 的 node_click）。
  const onNodeClick = (node: any) => {
    const data = node?.nodeData?.data ?? node?.getData?.()?.data ?? {}
    const nodeId = String(data?.meta?.node_id ?? data?.node_id ?? '')
    if (nodeId) emit('nodeSelect', nodeId)
  }
  mm.on('node_click', onNodeClick)
  mm.on('scale', syncScale)
  // 布局真正算完时会派发这个事件。定时器是猜，这个才是准信：
  // 组织结构与鱼骨的包围盒在 700ms 后仍可能变，靠猜就会裁掉右边一截（实测）。
  mm.on('node_tree_render_end', () => {
    // 等在这一次渲染上的生长批次可以继续了（见 afterRender）。
    const waiters = renderWaiters.splice(0)
    for (const resolve of waiters) resolve()
    // 生长收尾：这一版的布局已经画完，可以把树的所有权交回给库了。
    if (handoverArmed) {
      handoverArmed = false
      growing = false
    }
    if (!pendingFit) return
    pendingFit = false
    try {
      fitOnce()
    } catch {
      /* 事件里抛错会打断库自己的渲染栈，宁可放过这一刀 */
    }
  })
  // 仅开发期：给 /_lab/canvas 与截图脚本一个抓手，生产包里不存在。
  if (import.meta.env.DEV) (window as any).__mm = mm
  return true
}

/**
 * 生长动画。
 *
 * 库每次 setData 都会重建 DOM，所以只能按库内部的 uid 找刚出现的元素：
 * 先用 id → uid 映射定位，再逐个加类。标过的记在 animated 里，不重复播。
 */
const animated = new Set<string>()
const animate = (ids: string[]) => {
  if (reducedMotion() || !ids.length) return
  const renderTree = mm?.renderer?.renderTree
  if (!renderTree) return
  const uidById = new Map<string, string>()
  const walk = (node: any) => {
    if (node?.data?.id && node?.data?.uid) uidById.set(String(node.data.id), String(node.data.uid))
    for (const child of node?.children ?? []) walk(child)
  }
  walk(renderTree)
  for (const id of ids) {
    if (animated.has(id)) continue
    const uid = uidById.get(id)
    if (!uid) continue
    animated.add(id)
    const node = mm.renderer.findNodeByUid(uid)
    const el = node?.group?.node as HTMLElement | undefined
    if (!el?.classList) continue
    el.classList.add('mm-grow')
    window.setTimeout(() => el.classList.remove('mm-grow'), GROW_MS + 80)
  }
}

/** 把节点挂进内存树。父级未到返回 false，交给下一轮重试。 */
const mount = (node: ResourceMindmapNode): boolean => {
  const item: MindmapTreeNode = {
    id: node.id,
    topic: node.topic,
    ...(node.evidence ? { evidence: node.evidence } : {}),
    meta: { source_indexes: [...(node.source_indexes ?? [])] },
    children: [],
  }
  if (!node.parent_id) {
    tree = item
    index.set(node.id, item)
    return true
  }
  const parent = index.get(node.parent_id)
  if (!parent) return false
  parent.children = [...(parent.children ?? []), item]
  index.set(node.id, item)
  return true
}

const flush = async () => {
  timer = 0
  if (draining || disposed) return
  draining = true
  try {
  while (queue.length && !disposed) {
    const batch: string[] = []
    for (let i = 0; i < GROW_BATCH && queue.length; i += 1) {
      const node = queue.shift() as ResourceMindmapNode
      if (mount(node)) batch.push(node.id)
      else deferred.push(node)
    }
    if (!batch.length) {
      // 这一轮一个都没挂上：父级确实不会再来了，记成孤儿等收卷提示。
      orphanCount += deferred.length
      deferred.length = 0
      if (!queue.length) break
      continue
    }
    if (!mm) {
      if (!tree) break
      attach(toSimpleTree(tree, simpleOptions()))
      // 根刚立起来时把镜头对准它，否则 100 节点的图会长到画布外面。
      mm?.view?.reset?.()
    } else {
      suppressDirty = true
      // 生成期间一律只读：编辑态下 setData 会走 addHistory，进而派发一串 data_change，
      // 一次生长能打出几十次「已修改」。
      mm.setMode('readonly')
      mm.setData(toSimpleTree(tree as MindmapTreeNode, simpleOptions()))
      suppressDirty = false
    }
    // 等这一版真的画完再交下一批：两批渲染并发时，上一版的节点会留在 DOM 里，
    // 最终整棵树看起来多出一层影子（数据 18 个，肉眼 36 个）。
    await afterRender()
    animate(batch)
    reportStats()
    // 上一轮卡住的节点，这一轮父级可能已经到了。
    if (deferred.length) {
      queue.push(...deferred)
      deferred.length = 0
    }
    if (queue.length) await wait(NODE_INTERVAL_MS)
  }
  } catch {
    // 渲染中断不能让 draining 永远为 true——那会让后续节点全被吞掉。
    notice.value = '部分枝节渲染中断，已保留可见内容'
  } finally {
    draining = false
  }
  reportStats()
  if (pendingDone && !queue.length && !disposed) finish()
}

const schedule = () => {
  if (timer || disposed) return
  timer = window.setTimeout(() => void flush(), 0)
}

const pushNode = (node: ResourceMindmapNode) => {
  if (!node?.id || seen.has(node.id)) return
  seen.add(node.id)
  if (queue.length >= QUEUE_LIMIT) return
  // 上锁：从这里到 finish() 之前，树的所有权不给渲染库（见 growing 的说明）。
  growing = true
  queue.push(node)
  schedule()
}

const reset = () => {
  queue.length = 0
  deferred.length = 0
  seen.clear()
  animated.clear()
  index.clear()
  orphanCount = 0
  pendingDone = false
  growing = false
  handoverArmed = false
  renderWaiters.splice(0).forEach(resolve => resolve())
  tree = null
  notice.value = ''
  if (timer) {
    window.clearTimeout(timer)
    timer = 0
  }
  destroy()
  host.value?.replaceChildren()
  emit('stats', { nodes: 0, depth: 0, textbook: 0, graph: 0, model: 0, folded: 0 })
}

const finish = () => {
  if (draining || queue.length) {
    pendingDone = true
    return
  }
  pendingDone = false
  if (orphanCount) {
    notice.value = `有 ${orphanCount} 个枝节未落位，已保留可见部分`
    orphanCount = 0
  }
  if (mm) {
    // 生长期间被压成了只读，收卷时按真实编辑态恢复。
    mm.setMode(props.editing ? 'edit' : 'readonly')
    requestFit()
    // 交还树的所有权：等库把最后这一版布局画完（node_tree_render_end）。
    // 直接放手不行——那一拍迟到的 data_change 还带着上一版快照。
    handoverArmed = true
    window.setTimeout(() => {
      // 布局没变化时库可能不派发渲染结束事件，留一个兜底，别把锁焊死。
      if (!handoverArmed) return
      handoverArmed = false
      growing = false
    }, GROW_MS + 400)
    // 收卷后核一遍：数据与画面不一致就重建。这不是常态，是「保险丝」——
    // 一旦库在并发渲染里留下影子，用户看到的是一张重影的图，
    // 重建一次（约百毫秒）比事后解释便宜得多。
    window.setTimeout(verifyRender, GROW_MS + 600)
  }
  reportStats()
  emit('finish')
}

/** 画布上的节点数 vs 树里的节点数（折起来的不算）。不一致就整张重建。 */
const verifyRender = () => {
  if (!mm || !tree || disposed) return
  const el = host.value
  if (!el) return
  const foldLimit = getStructure(props.structureId).maxDepth
  const expected = countNodes(tree) - countFolded(tree, foldLimit)
  if (el.querySelectorAll('.smm-node').length === expected) return
  rebuild()
}

/**
 * 组织结构和鱼骨的布局是异步算完的，一次 fit 会拿到空包围盒（P0 实测）。
 *
 * fit 不做下限截断：一张 100 节点的图必然要缩到 20% 才装得下，
 * 强行停在 42% 只会让人看到「中间一段被裁掉的图」，那比小更糟。
 * 缩得太小时用一句提示把事实说清，让人自己决定是放大还是拖。
 */
const fitOnce = () => {
  mm?.view?.fit?.()
  syncScale()
}

/**
 * 请求一次「对准全图」。
 *
 * 两条路一起上：0/260/700ms 三轮（猜时间，覆盖大多数情况），
 * 以及 node_tree_render_end（准信，布局真算完时才到）。fit 很便宜，多对几次没有代价。
 */
const requestFit = () => {
  pendingFit = true
  void fitWithRetry()
}

const fitWithRetry = async () => {
  for (const delay of [0, 260, 700]) {
    if (delay) await wait(delay)
    try {
      fitOnce()
    } catch {
      /* 布局未就绪，交给下一轮 */
    }
  }
  pendingFit = false
}

const normalizeTree = (data: unknown): MindmapTreeNode | null => {
  if (!data || typeof data !== 'object') return null
  const raw = data as Record<string, any>
  // 后端存的是 mind-elixir 时代的 { nodeData: {...} }；也兼容直接给根节点。
  const root = raw.nodeData ?? raw
  if (!root) return null
  const walk = (node: any): MindmapTreeNode => {
    const evidence = node?.evidence ?? node?.meta?.evidence ?? node?.data?.meta?.evidence
    const meta = node?.meta ?? node?.data?.meta
    return {
      id: String(node?.id ?? node?.data?.id ?? ''),
      topic: String(node?.topic ?? node?.data?.text ?? ''),
      ...(evidence ? { evidence } : {}),
      // 锚点（node_id / acupoint / branch_id）必须原样带过来：丢了它，
      // 叶子就只是"写着知识点名的方块"，点了回不到地图。
      meta: { source_indexes: [...(meta?.source_indexes ?? [])], ...anchorsOf(meta) },
      children: (node?.children ?? []).map(walk),
    }
  }
  const flat = walk(root)
  return flat.id || flat.topic ? flat : null
}

const indexTree = (root: MindmapTreeNode) => {
  index.clear()
  const stack: MindmapTreeNode[] = [root]
  while (stack.length) {
    const node = stack.pop() as MindmapTreeNode
    if (node.id) index.set(node.id, node)
    stack.push(...(node.children ?? []))
  }
}

const setTree = (data: unknown) => {
  reset()
  const flat = normalizeTree(data)
  if (!flat) return
  tree = flat
  indexTree(flat)
  if (!attach(toSimpleTree(flat, simpleOptions()))) return
  if (props.editing) mm?.setMode('edit')
  requestFit()
  reportStats()
}

const getTree = (): MindmapTreeNode | null => {
  if (!mm) return tree
  try {
    return fromSimpleTree(mm.getData())
  } catch {
    return tree
  }
}

/**
 * 换结构 / 换配色。
 *
 * 不做增量 setThemeConfig：库的 themeConfig 只在 initTheme 时与内置默认主题
 * 合并，运行期改它容易留下上一套的间距（P0 见过残留）。
 * 重建一次不贵（100 节点约百毫秒）。
 *
 * 重建必须重新过一遍 toSimpleTree：结构变了，层数上限也跟着变。
 * 直接把上一版的渲染数据喂回去，鱼骨图会照样画出第 4 层（踩过一次：
 * 状态栏诚实地写着「已折叠 8 个」，画布上一个没折）。
 */
const rebuild = () => {
  if (!mm || props.live) return
  let snapshot: any = null
  try {
    snapshot = mm.getData()
  } catch {
    return
  }
  if (!snapshot) return
  tree = fromSimpleTree(snapshot)
  indexTree(tree)
  const editing = props.editing
  if (!attach(toSimpleTree(tree, simpleOptions()))) return
  if (editing) mm?.setMode('edit')
  requestFit()
  reportStats()
}

const zoom = (factor: number) => {
  if (!mm) return
  if (factor > 1) mm.view.enlarge()
  else mm.view.narrow()
}
const fit = () => requestFit()

const addChild = () => {
  if (!props.editing) return
  mm?.execCommand('INSERT_CHILD_NODE')
  emit('dirty')
}
const addSibling = () => {
  if (!props.editing) return
  mm?.execCommand('INSERT_NODE')
  emit('dirty')
}
const removeSelected = () => {
  if (!props.editing) return
  // 根节点不可删——删了整张图没有锚点，后端也会拒。
  mm?.execCommand('REMOVE_NODE')
  emit('dirty')
}

const svgElement = () => host.value?.querySelector('svg') as SVGSVGElement | null
const exportSvg = (): Blob | undefined => {
  const svg = svgElement()
  if (!svg) return undefined
  return new Blob([new XMLSerializer().serializeToString(svg)], { type: 'image/svg+xml' })
}
const exportPng = async (): Promise<Blob | null> => {
  const svg = svgElement()
  if (!svg) return null
  const raw = new Blob([new XMLSerializer().serializeToString(svg)], { type: 'image/svg+xml' })
  const url = URL.createObjectURL(raw)
  try {
    const image = await new Promise<HTMLImageElement>((resolve, reject) => {
      const img = new Image()
      img.onload = () => resolve(img)
      img.onerror = () => reject(new Error('png export failed'))
      img.src = url
    })
    const canvas = document.createElement('canvas')
    canvas.width = image.width || 1600
    canvas.height = image.height || 900
    canvas.getContext('2d')?.drawImage(image, 0, 0)
    return await new Promise<Blob | null>(resolve => canvas.toBlob(resolve, 'image/png'))
  } catch {
    return null
  } finally {
    URL.revokeObjectURL(url)
  }
}

watch(() => props.editing, value => {
  if (!mm) return
  mm.setMode(value ? 'edit' : 'readonly')
})

// 结构与配色是视图属性：切换只重建渲染，不动数据，也不调后端。
// 用拼接字符串而不是数组：数组每次都是新对象，watch 会误判成「变了」。
watch(() => `${props.structureId}|${props.themeId}`, rebuild)

/**
 * 容器尺寸变了要重新对焦。
 *
 * 对话栏收起时画布宽了 312px，但窗口没变，库自己的 window resize 监听不会触发——
 * 结果就是「一收起对话栏，图还挤在左边一半」。用 ResizeObserver 补这一刀。
 */
let resizeTimer = 0
let resizeObserver: ResizeObserver | null = null

onMounted(() => {
  const el = host.value
  if (!el || typeof ResizeObserver === 'undefined') return
  resizeObserver = new ResizeObserver(() => {
    if (!mm || props.live) return
    try {
      mm.resize()
    } catch {
      /* 库正在重建，下一轮再说 */
    }
    if (resizeTimer) window.clearTimeout(resizeTimer)
    // 300ms 是折叠动画（200ms）的余量：动画中途量到的包围盒是错的。
    resizeTimer = window.setTimeout(() => {
      resizeTimer = 0
      if (mm && !props.live) requestFit()
    }, 300)
  })
  resizeObserver.observe(el)
})

onBeforeUnmount(() => {
  disposed = true
  if (timer) window.clearTimeout(timer)
  if (resizeTimer) window.clearTimeout(resizeTimer)
  resizeObserver?.disconnect()
  resizeObserver = null
  destroy()
})

defineExpose({
  reset,
  pushNode,
  finish,
  setTree,
  getTree,
  zoom,
  fit,
  addChild,
  addSibling,
  removeSelected,
  exportSvg,
  exportPng,
})
</script>

<style scoped lang="scss">
.canvas {
  /* 由宿主的 .mat 撑开：画布不在文档流里，尺寸只跟随容器。 */
  position: absolute;
  inset: 0;
  overflow: hidden;
  isolation: isolate;
  border-radius: var(--qh-radius-panel);
  background: var(--mm-bg);
  transition: background var(--qh-duration-md) var(--qh-ease);
}

/* 8px 点阵 + 32px 主线 + 四周暗角：这是「制图纸」而不是「白色容器」的关键。
   只有 24px 一格时看着像表格；补一层细点阵才像坐标纸——
   节点拖到哪、隔了多远，有细线兜着才判断得出来。 */
.canvas-grid {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 0;
  background-image:
    radial-gradient(circle at 1px 1px, color-mix(in srgb, var(--mm-text) 16%, transparent) 1px, transparent 1.2px),
    repeating-linear-gradient(0deg, color-mix(in srgb, var(--mm-text) 7%, transparent) 0 1px, transparent 1px 32px),
    repeating-linear-gradient(90deg, color-mix(in srgb, var(--mm-text) 7%, transparent) 0 1px, transparent 1px 32px),
    radial-gradient(ellipse at 50% 42%, transparent 45%, color-mix(in srgb, var(--mm-bg) 65%, transparent) 100%);
  background-size: 8px 8px, auto, auto, auto;
  background-repeat: repeat, repeat, repeat, no-repeat;
}

.canvas-host {
  position: absolute;
  inset: 0;
  z-index: 1;

  /* 生长只动 transform/opacity：不参与布局坐标计算，不会挤动兄弟节点。 */
  :deep(.smm-node.mm-grow) {
    animation: mm-grow 320ms var(--qh-ease) both;
  }

  :deep(svg) {
    font-family: var(--qh-font-body);
  }
}

/* 右下角裁切角：让画布看起来是一张被压住的图纸。 */
.canvas-corner {
  position: absolute;
  right: 0;
  bottom: 0;
  z-index: 2;
  width: 26px;
  height: 26px;
  pointer-events: none;
  background: linear-gradient(135deg, transparent 50%, color-mix(in srgb, var(--mm-text) 10%, transparent) 50%);
}

.canvas-notice {
  position: absolute;
  left: 50%;
  top: 12px;
  z-index: 3;
  transform: translateX(-50%);
  padding: 6px 12px;
  border: 1px solid var(--qh-line-strong);
  border-radius: var(--qh-radius-pill);
  color: var(--qh-fg-soft);
  background: var(--qh-surface-raised);
  font-size: var(--qh-text-footnote);
  box-shadow: var(--qh-shadow-1);
}

/* 缩放提示：压在右下角，不抢镜头，也不拦截任何操作。 */
.canvas-hint {
  position: absolute;
  right: 16px;
  bottom: 14px;
  z-index: 3;
  padding: 4px 10px;
  border-radius: var(--qh-radius-pill);
  color: color-mix(in srgb, var(--mm-text) 52%, transparent);
  background: color-mix(in srgb, var(--mm-bg) 78%, transparent);
  font-size: var(--qh-text-footnote);
  letter-spacing: 0.3px;
  pointer-events: none;
}

@keyframes mm-grow {
  from {
    opacity: 0;
    transform: scale(0.86) translateY(6px);
  }
}

@media (prefers-reduced-motion: reduce) {
  .canvas-host :deep(.smm-node.mm-grow) {
    animation: none;
  }
}
</style>
