<template>
  <section class="graph-view" :class="{ 'has-selection': Boolean(selectedId) }">
    <header class="graph-head">
      <div class="title-block">
        <p class="eyebrow"><span class="eyebrow-mark">✳</span> 本草关系图谱</p>
        <h2>{{ title }}</h2>
        <p class="subtitle">沿着药性、证候与方剂的联系，发现一味药的完整脉络</p>
      </div>
      <div class="graph-counts" aria-label="图谱规模">
        <span><b>{{ visible.nodes.length }}</b>个节点</span><i />
        <span><b>{{ visible.edges.length }}</b>条关系</span>
      </div>
      <div class="filters">
        <div class="filter-row relation-filters" aria-label="筛选关系">
          <span class="filter-label">关系</span>
          <button v-for="item in relationGroups" :key="item" :class="[{ active: relationFilters.has(item) }, `relation-${relationColorKey(item)}`]" :aria-pressed="relationFilters.has(item)" @click="toggleRelation(item)"><i />{{ graphVisual.relation[item].group }}</button>
        </div>
        <div class="filter-row entity-filters" aria-label="筛选节点类型">
          <span class="filter-label">节点</span>
          <button v-for="item in entityTypes" :key="item" :class="[{ active: entityFilters.has(item) }, `entity-${item}`]" :aria-pressed="entityFilters.has(item)" @click="toggleEntity(item)"><i />{{ graphVisual.entity[item].label }}</button>
        </div>
        <div class="filter-row tier-filters" aria-label="按可信层筛选关系">
          <span class="filter-label">层级</span>
          <button v-for="item in tierOptions" :key="item.tier" :class="{ active: tierFilters.has(item.tier) }" :aria-pressed="tierFilters.has(item.tier)" :title="item.description" @click="toggleTier(item.tier)"><i />{{ item.label }}<small>{{ tierCounts[item.tier] || 0 }}</small></button>
          <button class="tier-preset" :class="{ active: constraintOnly }" :aria-pressed="constraintOnly" title="只留十八反、十九畏这类硬约束；再点一次恢复两层" @click="toggleConstraintOnly">只看禁忌<small>{{ tierCounts[1] || 0 }}</small></button>
        </div>
      </div>
    </header>

    <StateLoading v-if="loading" class="graph-state" size="lg" title="正在整理本草脉络" description="马上为你展开药材、证候与方剂之间的联系。" />
    <StateError v-else-if="error" class="graph-state" size="lg" title="暂时无法载入图谱" description="请检查网络连接后重试。" @action="emit('retry')" />
    <StateEmpty v-else-if="!content.nodes.length" class="graph-state" size="lg" title="这里还没有图谱内容" description="添加本草知识后，这里会呈现它与证候、方剂之间的关联。" />
    <template v-else>
      <div class="graph-stage">
        <div ref="canvasRef" class="graph-canvas" role="img" aria-label="可拖动旋转的三维本草知识图谱" @dblclick="onCanvasDoubleClick" />
        <div class="graph-hit-layer" aria-label="图谱节点" @pointerdown="onPointerDown" @pointermove="onPointerMove" @pointerup="onPointerUp" @pointercancel="onPointerCancel" @wheel="onWheel">
          <button v-for="node in visible.nodes" :key="node.id" :ref="element => setNodeTarget(node.id, element)" class="node-hit-target" type="button" :data-node-id="node.id" :aria-label="`选择节点 ${node.name}`" :title="node.name" @click.stop="onNodeClick(node)" />
        </div>
        <div v-if="webglError" class="canvas-fallback" role="status">
          <p>此浏览器暂不支持三维画布</p><span>仍可从下方节点清单查看本草内容</span>
          <div><button v-for="node in visible.nodes" :key="node.id" @click="selectNode(node)">{{ node.name }}</button></div>
        </div>
        <div class="stage-caption"><span class="live-indicator" />{{ selectedNode ? `正在查看「${selectedNode.name}」` : '拖动画布旋转 · 滚轮缩放 · 点击节点查看' }}<span v-if="hiddenCount > 0" class="hidden-note">未显示 {{ hiddenCount }} 条</span></div>
        <nav class="camera-controls" aria-label="图谱视角控制">
          <button title="放大" aria-label="放大图谱" @click="zoom(-70)">＋</button>
          <button title="缩小" aria-label="缩小图谱" @click="zoom(70)">−</button>
          <span />
          <button title="恢复初始视角" aria-label="恢复初始视角" @click="resetCamera">⌖</button>
          <button :class="{ active: autoRotate }" :aria-pressed="autoRotate" :title="autoRotate ? '暂停自动旋转' : '开启自动旋转'" aria-label="切换自动旋转" @click="autoRotate = !autoRotate">⟳</button>
        </nav>
        <Transition name="source">
          <aside v-if="selectedEdge" class="edge-card" aria-live="polite">
            <button class="card-close" aria-label="关闭关系依据" @click="selectedEdge = null">×</button>
            <span class="card-kicker">关系依据</span><h3>{{ selectedEdge.edge.label }}</h3><span class="edge-relation-tag">{{ selectedEdge.edge.relation_type }}</span>
            <blockquote v-if="selectedEdge.source"><b>{{ selectedEdge.source.出处 }}</b><p>{{ selectedEdge.source.原文摘录 }}</p><small>{{ selectedEdge.source.定位 }}</small></blockquote>
          </aside>
        </Transition>
        <Transition name="detail">
          <aside v-if="selectedNode" class="detail-panel" aria-live="polite">
            <header class="detail-head">
              <div class="detail-symbol" :class="`entity-${selectedNode.type}`">{{ entitySymbol[selectedNode.type] }}</div>
              <div class="detail-title"><span>{{ graphVisual.entity[selectedNode.type].label }}</span><h3>{{ selectedNode.name }}</h3></div>
              <button class="card-close" title="关闭详情" aria-label="关闭详情" @click="exitFocus">×</button>
            </header>
            <p class="node-brief">{{ selectedNode.brief }}</p>
            <p v-if="detailLoading" class="detail-loading">正在查阅本草资料…</p>
            <template v-else>
              <section v-for="item in detailRows" :key="item.label" class="detail-section"><h4>{{ item.label }}</h4><p>{{ item.value }}</p></section>
              <section v-if="detail?.关联考点" class="detail-section"><h4>关联考点</h4><p>{{ detail.关联考点.说明 }}<b class="frequency">高频度 {{ Math.round((detail.关联考点.exam_frequency || 0) * 100) }}%</b></p></section>
              <section v-if="connectedNodes.length" class="connections"><h4>图谱关系 <span>{{ connectedNodes.length }}</span></h4>
                <button v-for="item in connectedNodes" :key="`${item.edge.source}-${item.edge.target}-${item.edge.relation_type}`" class="connection-row" @click="selectNode(item.node)">
                  <i :class="`relation-${relationColorKey(relationGroup(item.edge.relation_type))}`" /><span class="connection-copy"><b>{{ item.node.name }}</b><small>{{ item.edge.label }}</small></span><span class="connection-arrow">›</span>
                </button>
              </section>
              <details v-if="detail?.sources?.length" class="source-details"><summary>查看典籍出处 <span>{{ detail.sources.length }}</span></summary><blockquote v-for="(source, index) in detail.sources" :key="`${source.出处}-${index}`"><b>{{ source.出处 }}</b><p>{{ source.原文摘录 }}</p><small>{{ source.定位 }}</small></blockquote></details>
            </template>
            <button class="expand-button" @click="emit('expand', selectedNode)">以「{{ selectedNode.name }}」展开相关图谱 <span>↗</span></button>
          </aside>
        </Transition>
      </div>
      <footer class="graph-footer"><p>关系图例</p><div class="legend-list"><span v-for="item in legendGroups" :key="item" :class="`relation-${relationColorKey(item)}`"><i />{{ graphVisual.relation[item].group }}</span></div><span class="legend-note">字号与明度表现层级，线条只做暗示 · 选中节点时关系才显色</span></footer>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type * as ThreeTypes from 'three'
import { clampLevel, graphVisual, levelStyle, relationGroup, relationStyle, type GraphLevel, type GraphRelationGroup } from '../../config/graph'
import { StateEmpty, StateError, StateLoading } from '../state'
import { useGraphStore } from '../../stores/graph'
import type { GraphContent, GraphEdge, GraphEntityType, GraphNode } from '../../types/graph'

const props = withDefaults(defineProps<{ content: GraphContent; title?: string; loading?: boolean; error?: boolean; initialFocusId?: string }>(), { title: '本草知识图谱', loading: false, error: false, initialFocusId: '' })
const emit = defineEmits<{ (e: 'node-select', node: GraphNode): void; (e: 'edge-select', edge: GraphEdge): void; (e: 'expand', node: GraphNode): void; (e: 'retry'): void }>()
const graphStore = useGraphStore()
let THREE: typeof import('three') | null = null
const canvasRef = ref<HTMLDivElement>()
const selectedId = ref('')
const selectedEdge = ref<{ edge: GraphEdge; source: GraphContent['sources'][number] | undefined } | null>(null)
const relationGroups = Object.keys(graphVisual.relation) as GraphRelationGroup[]
const relationFilters = ref(new Set<GraphRelationGroup>(relationGroups))
const entityTypes: GraphEntityType[] = ['herb', 'syndrome', 'formula', 'category']
const entityFilters = ref(new Set<GraphEntityType>(entityTypes))
/**
 * 按可信层筛选（P8）。三层不是「置信度高低的排序」，而是三种不同性质的知识：
 *
 * - T1 硬约束：十八反、十九畏。错一次就是教学事故，只能人工校订，不进模型候选；
 * - T2 教材事实：配伍、方剂组成、药证链，每条都能翻到教材某一句；
 * - T3 相似聚类：按属性算出来的「同归经/同功效」，按设计不落库，所以这里只有两项。
 *
 * 默认两层都开。为什么保留开关：评审时要能一眼看出来「这条线是禁忌还是配伍」，
 * 关掉 T2 只剩 T1，画布上剩的就是那 80 条不能碰的组合——这比在图例里写一行字有力。
 */
const tierOptions = [
  { tier: 1 as const, label: 'T1 硬约束', description: '十八反、十九畏：配伍禁忌，只能人工校订' },
  { tier: 2 as const, label: 'T2 教材事实', description: '配伍、方剂组成、药证链：每条都能翻到教材原文' },
]
const tierFilters = ref(new Set<number>([1, 2]))
const tierCounts = computed(() => {
  const counts: Record<number, number> = {}
  // 早期数据没有 tier 字段（build_graph 时代写进去的），按 T2 计入：
  // 那些边都是教材明文关系，归到 T1 会把禁忌列表污染成假阳性。
  for (const edge of props.content.edges) {
    const tier = edge.tier ?? 2
    counts[tier] = (counts[tier] || 0) + 1
  }
  return counts
})
const autoRotate = ref(true)
const webglError = ref(false)
const entitySymbol: Record<GraphEntityType, string> = { herb: '草', syndrome: '证', formula: '方', category: '类' }
const selectedNode = computed(() => props.content.nodes.find(node => node.id === selectedId.value))
const detail = computed(() => graphStore.detail)
const detailLoading = computed(() => graphStore.detailLoading)
const detailRows = computed(() => {
  if (!detail.value) return []
  const d = detail.value
  const formula = d.功效主治?.功效 || []
  // 主治有两种形态：教材抽取的纯字符串，和带原文段落的 {syndrome, description}。
  // 直接 JSON.stringify 会把一坨原始 JSON 糊到面板上，所以按字段名渲染。
  const indications = (d.功效主治?.主治 || []).map(item => {
    if (typeof item === 'string') return item
    if (item && typeof item === 'object' && 'syndrome' in item) {
      const row = item as { syndrome: string; description?: string }
      return row.description ? `${row.syndrome}：${row.description}` : row.syndrome
    }
    return ''
  }).filter(Boolean)
  // 剂量是 {text, min_g, max_g, usage_notes}，不是字符串——直接插值会渲染成 [object Object]。
  const dose = d.常用剂量与炮制?.剂量
  const doseText = typeof dose === 'string' ? dose : dose?.text || ''
  const notes = typeof dose === 'object' && dose ? dose.usage_notes || '' : ''
  // 「知识库暂未覆盖」是抽取管线写的占位值，不是真内容，别当炮制方法展示。
  const prep = d.常用剂量与炮制?.炮制 || ''
  const prepText = prep === '知识库暂未覆盖' ? '' : prep
  return [
    { label: '性味归经', value: d.性味归经 },
    { label: '功效主治', value: [...formula, ...indications].join('；') },
    { label: '常用剂量与炮制', value: [doseText, notes, prepText].filter(Boolean).join('；') },
    { label: '使用禁忌', value: d.使用禁忌?.join('；') },
    { label: '经典出处', value: d.经典出处?.join(' · ') },
  ].filter(item => item.value) as Array<{ label: string; value: string }>
})
const visible = computed(() => {
  const nodeById = new Map(props.content.nodes.map(node => [node.id, node]))
  const edges = props.content.edges.filter(edge => tierFilters.value.has(edge.tier ?? 2) && relationFilters.value.has(relationGroup(edge.relation_type)) && entityFilters.value.has(nodeById.get(edge.source)?.type || 'herb') && entityFilters.value.has(nodeById.get(edge.target)?.type || 'herb'))
  const limited = props.content.nodes.length > graphVisual.maxVisibleNodes ? [...edges].sort((a, b) => (b.confidence ?? .5) - (a.confidence ?? .5)).slice(0, graphVisual.maxVisibleNodes * 2) : edges
  const used = new Set([props.content.center_id, ...limited.flatMap(edge => [edge.source, edge.target])])
  // totalEdges 是「筛完但裁之前」的条数，供「已隐藏 N 条」算前后差；不单独再算一遍
  // 是为了避免过滤条件写两处、改一处忘一处。
  return { nodes: props.content.nodes.filter(node => used.has(node.id) && entityFilters.value.has(node.type)).slice(0, graphVisual.maxVisibleNodes), edges: limited.filter(edge => used.has(edge.source) && used.has(edge.target)), totalEdges: edges.length }
})
/**
 * 「已隐藏 N 条」必须真能算出来，两个来源分开加：
 *
 * - 后端截断：`meta.total_nodes - nodes.length`。这是真被切掉的**节点**数。
 *   不传 limit 时它永远是 0（后端默认只给 40 个），所以 store 里把 limit
 *   显式传成 60——否则这个提示永远不出现，看着像「什么都没被隐藏」。
 * - 前端截断：`visible.totalEdges - visible.edges.length`，是筛完再按
 *   `maxVisibleNodes` 裁掉的边数。用户自己关掉一层也会让它变大，所以措辞
 *   用「未显示」而不是「被系统隐藏」。
 */
const hiddenCount = computed(() => {
  const total = props.content.meta?.total_nodes ?? props.content.nodes.length
  const backendHidden = Math.max(0, total - props.content.nodes.length)
  const frontendHidden = Math.max(0, visible.value.totalEdges - visible.value.edges.length)
  return backendHidden + frontendHidden
})
const connectedNodes = computed(() => {
  if (!selectedId.value) return []
  const byId = new Map(props.content.nodes.map(node => [node.id, node]))
  return visible.value.edges.filter(edge => edge.source === selectedId.value || edge.target === selectedId.value).map(edge => ({ edge, node: byId.get(edge.source === selectedId.value ? edge.target : edge.source) })).filter((item): item is { edge: GraphEdge; node: GraphNode } => Boolean(item.node))
})
const legendGroups = computed(() => relationGroups.filter(item => visible.value.edges.some(edge => relationGroup(edge.relation_type) === item)))
const relationColorKey = (group: GraphRelationGroup) => ({ 配伍协同: 'compatible', 配伍制约: 'restraint', 配伍禁忌: 'forbidden', 功效互参: 'benefit', 方药组成: 'composition', 药证方链: 'evidence', 鉴别对照: 'contrast', 归属聚类: 'cluster' })[group]
const toggleRelation = (item: GraphRelationGroup) => { const next = new Set(relationFilters.value); next.has(item) ? next.delete(item) : next.add(item); relationFilters.value = next }
const toggleEntity = (item: GraphEntityType) => { const next = new Set(entityFilters.value); next.has(item) ? next.delete(item) : next.add(item); entityFilters.value = next }
const toggleTier = (tier: number) => { const next = new Set(tierFilters.value); next.has(tier) ? next.delete(tier) : next.add(tier); tierFilters.value = next }
/**
 * 「只看禁忌」是一个**预设**，不是一个默认值。
 *
 * 默认仍是 `[1,2]`：T1 全库只有 80 条，麻黄一条都没有，默认只开 T1 会让首屏
 * 变空图，进而触发「教材未收录」的假结论。所以「干净」必须由用户主动选，
 * 而不是替他决定。再点一次退回两层，避免进了出不来。
 */
const constraintOnly = computed(() => tierFilters.value.size === 1 && tierFilters.value.has(1))
const toggleConstraintOnly = () => { tierFilters.value = constraintOnly.value ? new Set<number>([1, 2]) : new Set<number>([1]) }

let renderer: ThreeTypes.WebGLRenderer | null = null
let scene: ThreeTypes.Scene | null = null
let camera: ThreeTypes.PerspectiveCamera | null = null
let nodeLayer: ThreeTypes.Group | null = null
let edgeLayer: ThreeTypes.Group | null = null
let nodePositionsById = new Map<string, ThreeTypes.Vector3>()
let nodeLevelsById = new Map<string, GraphLevel>()
const nodeTargets = new Map<string, HTMLButtonElement>()
let resizeObserver: ResizeObserver | null = null
let frameId = 0
let lastFrame = 0
let lastInteraction = 0
let spherical: ThreeTypes.Spherical
let raycaster: ThreeTypes.Raycaster
let pointer: ThreeTypes.Vector2
let tmpForward: ThreeTypes.Vector3 | null = null
let tmpToCam: ThreeTypes.Vector3 | null = null
/** 景深缓存：每帧先算所有节点的相机深度，再按 min/max 归一化 */
const depthItems: { sprite: ThreeTypes.Sprite; level: GraphLevel; kind: string; base: number; depth: number; nodeId: string }[] = []
/** 标签去重叠用的临时缓存 */
const labelBoxes: { sprite: ThreeTypes.Sprite; level: GraphLevel; priority: number; depth: number; x: number; y: number; halfW: number; halfH: number }[] = []
let tmpProject: ThreeTypes.Vector3 | null = null
const pointers = new Map<number, ThreeTypes.Vector2>()
let pinchDistance = 0
let dragStart: ThreeTypes.Vector2 | null = null
let dragMoved = false
let hoveredId = ''
let pressedNodeId = ''
let suppressedClickNodeId = ''
let suppressedClickAt = 0
const onNodeClick = (node: GraphNode) => {
  if (suppressedClickNodeId === node.id && performance.now() - suppressedClickAt < 500) { suppressedClickNodeId = ''; return }
  selectNode(node)
}
const setNodeTarget = (id: string, element: unknown) => {
  if (element instanceof HTMLButtonElement) nodeTargets.set(id, element)
  else nodeTargets.delete(id)
}

const resolveColor = (token: string): string => {
  const root = getComputedStyle(document.documentElement)
  let value = token.trim()
  for (let i = 0; i < 3 && value.startsWith('var('); i++) {
    const name = value.match(/--[\w-]+/)?.[0]
    if (!name) break
    value = root.getPropertyValue(name).trim()
  }
  return value || root.getPropertyValue('--qh-graph-l1').trim() || 'steelblue'
}

/** 实心圆点纹理，按颜色缓存 */
const makeDotTexture = (color: string) => {
  const canvas = document.createElement('canvas'); canvas.width = 64; canvas.height = 64
  const context = canvas.getContext('2d')!
  context.fillStyle = color
  context.beginPath(); context.arc(32, 32, 30, 0, Math.PI * 2); context.fill()
  const texture = new THREE!.CanvasTexture(canvas)
  texture.colorSpace = THREE!.SRGBColorSpace
  texture.generateMipmaps = false
  texture.minFilter = THREE!.LinearFilter
  return texture
}

/**
 * 文字标签：只有文字，没有白底圆角块（参考图的关键特征之一）。
 * canvas 内部用固定基准字号绘制，再按 levelStyle.font 换算成世界尺寸，
 * 这样任何字号下文字都保持清晰（不依赖浏览器缩放 canvas）。
 */
const makeLabel = (text: string, level: GraphLevel, scale: number) => {
  const style = levelStyle(level)
  const color = resolveColor(style.color)
  const BASE = 96
  const canvas = document.createElement('canvas')
  const probe = canvas.getContext('2d')!
  const fontSpec = `${style.weight} ${BASE}px "Microsoft YaHei", "PingFang SC", "Noto Sans SC", sans-serif`
  probe.font = fontSpec
  const metrics = probe.measureText(text)
  // 用实际墨迹包围盒裁画布，而不是固定 BASE*1.4 再加 textBaseline=middle。
  // 后者居中的是 em 框，中文字形整体偏上，实测中心词比几何中心高 43px。
  // pad 只留 0.08em：内边距越大，去重叠时越容易把本可以并排的标签判成冲突。
  const pad = BASE * 0.08
  const inkAscent = metrics.actualBoundingBoxAscent || BASE * 0.72
  const inkDescent = metrics.actualBoundingBoxDescent || 0
  const margin = pad * 0.25
  const width = Math.ceil(metrics.width) + pad * 2
  const height = Math.ceil(inkAscent + inkDescent + margin * 2)
  const dpr = Math.min(2, Math.max(1, window.devicePixelRatio || 1))
  canvas.width = Math.ceil(width * dpr); canvas.height = Math.ceil(height * dpr)
  const context = canvas.getContext('2d')!
  context.scale(dpr, dpr)
  context.font = fontSpec
  context.textAlign = 'center'; context.textBaseline = 'alphabetic'
  context.fillStyle = color
  context.fillText(text, width / 2, margin + inkAscent)
  const texture = new THREE!.CanvasTexture(canvas)
  texture.colorSpace = THREE!.SRGBColorSpace
  texture.generateMipmaps = false
  texture.minFilter = THREE!.LinearFilter
  const sprite = new THREE!.Sprite(new THREE!.SpriteMaterial({ map: texture, transparent: true, depthWrite: false, depthTest: false, opacity: 1 }))
  // 1 个 BASE px 对应 1 个 style.font 世界单位 → 「font」就是实际字高，可直接对照规格表
  const worldPerPixel = (style.font * scale) / BASE
  sprite.scale.set(width * worldPerPixel, height * worldPerPixel, 1)
  sprite.userData.kind = 'label'
  return sprite
}

/** 字号缩放：节点越多字越小，避免 60 节点时叠字 */
const labelScale = () => Math.min(1, Math.max(graphVisual.density.min, Math.sqrt(graphVisual.density.fullAt / Math.max(1, visible.value.nodes.length))))
/** 是否显字：节点多时只让中心与一跳显字，其余只留圆点 */
const shouldShowLabel = (level: GraphLevel) => {
  if (level === 0) return true
  if (visible.value.nodes.length <= graphVisual.density.allLabelsUpTo) return true
  return level <= 1
}

/** 确定性伪随机：同一个 index 永远得到同一个值，刷新不跳位 */
const pseudoRandom = (seed: number) => {
  const value = Math.sin(seed * 12.9898 + 78.233) * 43758.5453
  return value - Math.floor(value)
}

/**
 * 椭球面排斥：同层节点在归一化空间里互相推开，推完再拉回壳层。
 *
 * 三个轴都要参与。之前只在 x/y 上排斥、z 只做抖动，等价于一张扁平圆盘，
 * 相机绕 y 转到侧面时 x 变成景深方向，整张盘直接塌成一条竖线。
 */
const relaxShell = (points: ThreeTypes.Vector3[], shell: number, count: number) => {
  if (!THREE || count < 2) return
  const { squashY, squashZ } = graphVisual.space
  const squash = squashY || 1
  const flatten = squashZ || 1
  // 在「椭球归一化空间」里算距离：y 除以 squashY、z 除以 squashZ。
  // 不归一化的话，各向同性排斥会把椭球推回球，压扁就白做了。
  const ideal = 2 * shell * Math.sqrt(Math.PI / count) * 0.62
  for (let step = 0; step < 36; step++) {
    for (let i = 0; i < count; i++) {
      for (let j = i + 1; j < count; j++) {
        const a = points[i]; const b = points[j]
        const dx = b.x - a.x
        const dy = b.y / squash - a.y / squash
        const dz = b.z / flatten - a.z / flatten
        const distance = Math.hypot(dx, dy, dz)
        if (distance < 0.001 || distance >= ideal) continue
        const push = (ideal - distance) * 0.25
        const nx = dx / distance; const ny = dy / distance; const nz = dz / distance
        a.x -= nx * push; a.y -= ny * push * squash; a.z -= nz * push * flatten
        b.x += nx * push; b.y += ny * push * squash; b.z += nz * push * flatten
      }
    }
  }
  // 拉回壳层带宽，保持「同层同一壳」的层级语义
  points.forEach(point => {
    const length = Math.hypot(point.x, point.y / squash, point.z / flatten)
    if (length < 1e-4) return
    const ratio = length / shell
    const k = ratio > 1.08 ? 1.08 / ratio : ratio < 0.86 ? 0.86 / ratio : 1
    point.x *= k; point.y *= k; point.z *= k
  })
}

/**
 * 布局归一化：按**真实投影**把节点云缩放到目标填充率。
 *
 * 为什么不能直接按世界坐标算：相机 phi=72°（俯视 18°），世界 y 到屏幕 y
 * 不是线性映射，越靠上越被拉大、越靠下越被压小，两边还不对称。
 * 所以这里直接拿 project() 的 NDC 结果反复校正。
 *
 * 为什么要在**多个方位角**上取并集：布局是三维椭球，转 90° 后
 * 屏幕水平方向从世界 x 变成世界 z、纵向投影也随之变化。
 * 只按正面算的话，正面刚好，一转就顶出画布（实测背面「细辛」压到提示条上）。
 */
const fitStage = (positions: Map<string, ThreeTypes.Vector3>, nodes: GraphNode[], levels: Map<string, GraphLevel>) => {
  if (!THREE || !camera || !positions.size) return
  const { fillX, fillY, cameraDistance, fov } = graphVisual.space
  const canvasWidth = canvasRef.value?.clientWidth || 1
  const canvasHeight = canvasRef.value?.clientHeight || 1
  const pixelsPerWorld = (canvasHeight / 2) / (cameraDistance * Math.tan((fov * Math.PI) / 360))
  // 标签是屏幕空间贴图，比节点本身宽得多。不把它算进边界的话，
  // 「发散风寒药」这种 5 字标签在边缘一定被裁（实测左右各溢出约 40px）。
  const scale = labelScale()
  let padX = 0
  let padY = 0
  for (const node of nodes) {
    const style = levelStyle(levels.get(node.id) ?? 1)
    if (!style.font) continue
    // 与 makeLabel 的度量保持一致：字高≈1em，宽度≈字数×1em 再各留 0.08em 内边距
    const worldHeight = style.font * scale * 1.04
    const worldWidth = style.font * scale * (Math.max(1, node.name.length) + 0.16)
    padX = Math.max(padX, (worldWidth * pixelsPerWorld) / canvasWidth)
    padY = Math.max(padY, (worldHeight * pixelsPerWorld) / canvasHeight)
  }
  const probe = new THREE!.Vector3()
  // 闭包里 camera 的 null 收窄会失效，先取出来
  const activeCamera = camera
  const savedTheta = spherical.theta
  const azimuths = [0, Math.PI / 4, Math.PI / 2, (Math.PI * 3) / 4]
  for (let iteration = 0; iteration < 4; iteration++) {
    let minX = Infinity; let maxX = -Infinity; let minY = Infinity; let maxY = -Infinity
    for (const offset of azimuths) {
      spherical.theta = savedTheta + offset
      syncCamera()
      positions.forEach(point => {
        probe.copy(point).project(activeCamera)
        if (probe.x < minX) minX = probe.x
        if (probe.x > maxX) maxX = probe.x
        if (probe.y < minY) minY = probe.y
        if (probe.y > maxY) maxY = probe.y
      })
    }
    spherical.theta = savedTheta
    syncCamera()
    // 取「单侧最大」做基准：保证任何一边都不会溢出画布，
    // 俯视造成的上下不对称就自然留在另一侧，不会把节点顶出可视区。
    const reachX = Math.max(Math.abs(minX), Math.abs(maxX)) + padX
    const reachY = Math.max(Math.abs(minY), Math.abs(maxY)) + padY
    if (reachX < 1e-4 || reachY < 1e-4) return
    // 必须是**等比缩放**，三个轴同一个 k。
    // 各轴独立缩放会破坏椭球比例：实测正面 coverY 被压到 0.415、
    // 而侧面 0.603 —— 正面变成一条扁带，转起来每个角度形状都不一样。
    // 等比缩放本身与方位角无关，是这里唯一能保证「各角度一致」的做法。
    const k = Math.min(fillX / reachX, fillY / reachY)
    if (Math.abs(k - 1) < 0.005) return
    positions.forEach(point => { point.x *= k; point.y *= k; point.z *= k })
  }
}

/**
 * 默认视角优化：整体绕 y 轴转一个角度，让「离中心最近的非中心节点」尽量远。
 *
 * 三维椭球上总会有节点落在相机轴线上，投影到画面正中、贴着中心词
 * （实测「小青龙汤」正好压在「麻黄」正下方 30px 处）。
 * 试 24 个方位角挑最优的一个，默认视角就不会有节点怼在中心词上。
 */
const rotateForBestFrontView = (positions: Map<string, ThreeTypes.Vector3>, centerId: string) => {
  if (!THREE || positions.size < 2) return
  let bestAngle = 0
  let bestClearance = -1
  for (let step = 0; step < 24; step++) {
    const angle = (step / 24) * Math.PI * 2
    const cos = Math.cos(angle); const sin = Math.sin(angle)
    let clearance = Infinity
    positions.forEach((point, id) => {
      if (id === centerId) return
      const x = point.x * cos - point.z * sin
      clearance = Math.min(clearance, Math.hypot(x, point.y))
    })
    if (clearance > bestClearance) { bestClearance = clearance; bestAngle = angle }
  }
  const cos = Math.cos(bestAngle); const sin = Math.sin(bestAngle)
  positions.forEach(point => {
    const x = point.x * cos - point.z * sin
    point.z = point.x * sin + point.z * cos
    point.x = x
  })
}

const nodePositions = (nodes: GraphNode[], edges: GraphEdge[]) => {
  const empty = { positions: new Map<string, ThreeTypes.Vector3>(), levels: new Map<string, GraphLevel>() }
  if (!THREE) return empty
  const centerId = props.content.center_id
  const adjacency = new Map(nodes.map(node => [node.id, [] as string[]]))
  edges.forEach(edge => { adjacency.get(edge.source)?.push(edge.target); adjacency.get(edge.target)?.push(edge.source) })
  const rawLevels = new Map<string, number>([[centerId, 0]]); const queue = [centerId]
  for (let i = 0; i < queue.length; i++) for (const neighbor of adjacency.get(queue[i]) || []) if (!rawLevels.has(neighbor)) { rawLevels.set(neighbor, rawLevels.get(queue[i])! + 1); queue.push(neighbor) }
  nodes.filter(node => !rawLevels.has(node.id)).forEach((node, index) => rawLevels.set(node.id, Math.max(1, 1 + Math.floor(index / 12))))
  const levels = new Map<string, GraphLevel>(); rawLevels.forEach((value, key) => levels.set(key, clampLevel(value)))

  const { squashY, squashZ } = graphVisual.space
  const positions = new Map<string, ThreeTypes.Vector3>()
  positions.set(centerId, new THREE!.Vector3())
  const grouped = new Map<number, GraphNode[]>()
  nodes.filter(node => node.id !== centerId).forEach(node => { const level = rawLevels.get(node.id) || 1; const group = grouped.get(level) || []; group.push(node); grouped.set(level, group) })

  grouped.forEach((group, level) => {
    const shell = levelStyle(level).shell
    if (!shell) return
    const count = group.length
    const points = group.map((node, index) => {
      // 黄金角球面撒点（Fibonacci sphere）：任意视角下都铺得开。
      // 旧实现只在 x/y 平面撒点、z 只做 ±0.28 抖动，等价于一张扁盘，
      // 转到侧面 x 变成景深方向，整盘塌成一条竖线（用户截图就是这个现象）。
      const unitY = 1 - 2 * ((index + 0.5) / count)
      const ring = Math.sqrt(Math.max(0, 1 - unitY * unitY))
      const angle = index * 2.39996 + level * 1.13 + (pseudoRandom(index + level * 37) - 0.5) * 0.5
      // 半径抖动，避免所有点严格落在同一球面上
      const radius = shell * (0.92 + pseudoRandom(index * 7.3 + level) * 0.16)
      return new THREE!.Vector3(
        Math.cos(angle) * ring * radius,
        unitY * radius * squashY,
        Math.sin(angle) * ring * radius * squashZ,
      )
    })
    relaxShell(points, shell, count)
    group.forEach((node, index) => {
      const point = points[index]
      if (!Number.isFinite(point.x) || !Number.isFinite(point.y) || !Number.isFinite(point.z)) {
        console.warn('[graph] non-finite position', node.id, 'level', level, 'shell', shell, point)
      }
      positions.set(node.id, point)
    })
  })
  rotateForBestFrontView(positions, centerId)
  fitStage(positions, nodes, levels)
  return { positions, levels }
}

const disposeLayer = (layer: ThreeTypes.Group | null) => {
  if (!layer || !THREE) return
  layer.traverse(object => {
    const mesh = object as ThreeTypes.Mesh
    if (mesh.geometry) mesh.geometry.dispose()
    const disposeMaterial = (material: ThreeTypes.Material) => { const texture = 'map' in material ? material.map : undefined; if (texture instanceof THREE!.Texture) texture.dispose(); material.dispose() }
    const material = mesh.material
    if (Array.isArray(material)) material.forEach(disposeMaterial)
    else if (material) disposeMaterial(material)
  })
  layer.clear()
}

/** 边：一条 LineSegments 装全部边，vertexColors 实现「默认单色 + 选中时关系色」 */
const buildEdges = (positions: Map<string, ThreeTypes.Vector3>) => {
  if (!THREE || !edgeLayer) return
  const baseColor = new THREE!.Color(resolveColor(graphVisual.edge.line))
  const base: number[] = []; const baseColors: number[] = []
  const focus: number[] = []; const focusColors: number[] = []
  visible.value.edges.forEach(edge => {
    const source = positions.get(edge.source); const target = positions.get(edge.target)
    if (!source || !target) return
    const connected = Boolean(selectedId.value) && (edge.source === selectedId.value || edge.target === selectedId.value)
    const color = connected ? new THREE!.Color(resolveColor(relationStyle(edge.relation_type).color)) : baseColor
    base.push(source.x, source.y, source.z, target.x, target.y, target.z)
    baseColors.push(color.r, color.g, color.b, color.r, color.g, color.b)
    if (connected) {
      focus.push(source.x, source.y, source.z, target.x, target.y, target.z)
      focusColors.push(color.r, color.g, color.b, color.r, color.g, color.b)
    }
  })
  if (base.length) {
    const geometry = new THREE!.BufferGeometry()
    geometry.setAttribute('position', new THREE!.Float32BufferAttribute(base, 3))
    geometry.setAttribute('color', new THREE!.Float32BufferAttribute(baseColors, 3))
    const material = new THREE!.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: selectedId.value ? graphVisual.edge.opacityBack : graphVisual.edge.opacity, depthWrite: false })
    const lines = new THREE!.LineSegments(geometry, material)
    lines.userData.kind = 'edge'
    edgeLayer!.add(lines)
  }
  if (focus.length) {
    const geometry = new THREE!.BufferGeometry()
    geometry.setAttribute('position', new THREE!.Float32BufferAttribute(focus, 3))
    geometry.setAttribute('color', new THREE!.Float32BufferAttribute(focusColors, 3))
    const material = new THREE!.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: graphVisual.edge.focusOpacity, depthWrite: false })
    const lines = new THREE!.LineSegments(geometry, material)
    lines.userData.kind = 'edge-focus'
    edgeLayer!.add(lines)
  }
}

const buildSceneGraph = () => {
  if (!THREE || !scene || !nodeLayer || !edgeLayer) return
  disposeLayer(nodeLayer); disposeLayer(edgeLayer)
  const { positions, levels } = nodePositions(visible.value.nodes, visible.value.edges)
  nodePositionsById = positions; nodeLevelsById = levels
  const scale = labelScale()
  const dotTextures = new Map<string, ThreeTypes.Texture>()
  // 远的先加、近的后加，配合 depthTest:false 让近的盖住远的
  const ordered = [...visible.value.nodes].sort((a, b) => (positions.get(a.id)?.z || 0) - (positions.get(b.id)?.z || 0))

  ordered.forEach(node => {
    const position = positions.get(node.id)
    if (!position) return
    const level = levels.get(node.id) ?? 1
    const style = levelStyle(level)
    const color = resolveColor(style.color)
    const isSelected = node.id === selectedId.value
    const isConnected = connectedNodes.value.some(item => item.node.id === node.id)
    const dimmed = Boolean(selectedId.value) && !isSelected && !isConnected

    // 圆点（中心节点无圆点，用不可见命中体）
    const dotRadius = style.dot > 0 ? style.dot : style.font * 0.3
    let texture = dotTextures.get(style.color)
    if (!texture) { texture = makeDotTexture(color); dotTextures.set(style.color, texture) }
    const grow = isSelected || node.id === hoveredId ? 1.5 : 1
    const dot = new THREE!.Sprite(new THREE!.SpriteMaterial({ map: texture, transparent: true, depthWrite: false, opacity: style.dot > 0 ? (dimmed ? 0.2 : 1) : 0 }))
    dot.position.copy(position)
    dot.scale.set(dotRadius * 2 * grow, dotRadius * 2 * grow, 1)
    dot.renderOrder = 500
    dot.userData = { kind: 'dot', nodeId: node.id, baseOpacity: style.dot > 0 ? (dimmed ? 0.2 : 1) : 0, level }
    nodeLayer!.add(dot)

    if (shouldShowLabel(level)) {
      const label = makeLabel(node.name, level, scale)
      // 有圆点的层级：文字整体浮在圆点上方，留一点间隙（圆点像图钉）。
      // 中心节点没有圆点，就不该抬起 —— 否则中心词会比几何中心高 27px。
      const lift = style.dot > 0 ? dotRadius + style.font * scale * 0.66 : 0
      label.position.copy(position).add(new THREE!.Vector3(0, lift, 0))
      label.renderOrder = 1000 + Math.round(position.z * 0.05)
      label.userData = { kind: 'label', nodeId: node.id, baseOpacity: dimmed ? 0.16 : 1, level }
      nodeLayer!.add(label)
    }
  })
  buildEdges(positions)
}

const syncCamera = () => { if (!camera) return; camera.position.setFromSpherical(spherical); camera.lookAt(0, 0, 0); camera.updateMatrixWorld() }

/**
 * 深度衰减：近浓远淡 + 背面标签淡化。
 * 这是 3D 里保持「层级可读」的关键 —— 背面节点只留淡淡的存在感。
 */
const applyDepth = () => {
  if (!nodeLayer || !camera || !tmpForward || !tmpToCam || !tmpProject) return
  const { minOpacity, l1Floor, backLabelFactor } = graphVisual.depth
  // 深度必须沿「相机朝向」量，不能用径向法线点乘视线。
  // 布局是压扁的椭球（z 很薄），径向法线几乎躺在屏幕平面里，
  // 点乘结果恒 ≈ 0，所有节点会被压到同一个中间透明度，四档层级就全糊了。
  camera.getWorldDirection(tmpForward)
  depthItems.length = 0
  let minDepth = Infinity
  let maxDepth = -Infinity
  for (const child of nodeLayer.children) {
    const data = child.userData as { kind?: string; level?: GraphLevel; baseOpacity?: number }
    // 注意用 === undefined 而不是 !data.level：level 0 是中心节点，
    // 写成 falsy 判断会把它整个排除在景深和去重叠之外，别的标签就能压到它身上。
    if (!data.kind || data.level === undefined) continue
    const depth = tmpToCam.copy(child.position).sub(camera.position).dot(tmpForward)
    depthItems.push({
      sprite: child as ThreeTypes.Sprite,
      level: data.level,
      kind: data.kind,
      base: data.baseOpacity ?? 1,
      depth,
      nodeId: String((data as { nodeId?: string }).nodeId || ''),
    })
    if (depth < minDepth) minDepth = depth
    if (depth > maxDepth) maxDepth = depth
  }
  const span = Math.max(1e-3, maxDepth - minDepth)
  for (const item of depthItems) {
    const material = item.sprite.material as ThreeTypes.SpriteMaterial
    if (!material) continue
    // 离相机越近，t 越接近 1（越实、越浓）
    const t = 1 - (item.depth - minDepth) / span
    let opacity: number
    if (item.level === 0) {
      // 中心节点是视觉锚点，不吃景深，否则实测颜色永远到不了 #22459c。
      // 但仍要乘 base：中心节点的圆点 base 为 0（不可见），
      // 聚焦别的节点时 base 为 0.16，也要跟着淡下去。
      opacity = item.base
    } else {
      // 一跳是主体信息，给一个较高的地板，保证同层明度一致；二跳起才拉开景深
      const floor = item.level === 1 ? l1Floor : minOpacity
      opacity = (floor + (1 - floor) * t) * item.base
      if (item.kind === 'label') {
        if (item.level === 3 && t < 0.5) opacity = 0
        else if (t < 0.5) opacity *= item.level === 1 ? 0.9 : backLabelFactor
      }
    }
    material.opacity = opacity
  }
  cullOverlappingLabels()
}

/**
 * 屏幕空间标签去重叠。
 *
 * 三维布局再均匀，投影到屏幕也一定会有两个节点前后重叠的时候 ——
 * 转到侧面时尤其严重（实测侧视图里「风寒表实/杏仁/肺经/外寒内饮」整片压在一起）。
 * 这里按「层级优先、近的优先」贪心保留，被压住的标签只隐藏文字、保留圆点，
 * 节点仍然可点、可悬停。
 */
const cullOverlappingLabels = () => {
  if (!camera || !canvasRef.value || !tmpProject) return
  const project = tmpProject
  const { labelGap } = graphVisual.density
  const width = canvasRef.value.clientWidth
  const height = canvasRef.value.clientHeight
  if (!width || !height) return
  const halfHeight = graphVisual.space.cameraDistance * Math.tan((graphVisual.space.fov * Math.PI) / 360)
  labelBoxes.length = 0
  for (const item of depthItems) {
    if (item.kind !== 'label') continue
    const material = item.sprite.material as ThreeTypes.SpriteMaterial
    if (!material || material.opacity <= 0.02) continue
    project.copy(item.sprite.position).project(camera)
    if (project.z < -1 || project.z > 1) { material.opacity = 0; continue }
    // 世界尺寸 → 屏幕像素：透视除法后每世界单位占多少像素
    const perWorld = (height / 2) / Math.max(1, item.depth * Math.tan((graphVisual.space.fov * Math.PI) / 360))
    labelBoxes.push({
      sprite: item.sprite,
      level: item.level,
      depth: item.depth,
      x: (project.x + 1) * width / 2,
      y: (1 - project.y) * height / 2,
      halfW: item.sprite.scale.x * perWorld / 2,
      halfH: item.sprite.scale.y * perWorld / 2,
      priority: item.nodeId && item.nodeId === selectedId.value ? -2 : item.level,
    })
  }
  // 选中的节点最优先，其次中心，再按层级（一跳 → 二跳 → 三跳），同层里近的先占。
  // 不把选中节点放最前的话，聚焦一个二跳节点时，它可能被一个更高层级但已淡化的标签挤掉。
  labelBoxes.sort((a, b) => a.priority - b.priority || a.depth - b.depth)
  const kept: typeof labelBoxes = []
  for (const box of labelBoxes) {
    let blocked = false
    for (const other of kept) {
      if (Math.abs(box.x - other.x) < box.halfW + other.halfW + labelGap
        && Math.abs(box.y - other.y) < box.halfH + other.halfH + labelGap) { blocked = true; break }
    }
    if (blocked) (box.sprite.material as ThreeTypes.SpriteMaterial).opacity = 0
    else kept.push(box)
  }
  // 调试用：脚本要能量化「转到某个角度还剩几个标签可见」
  if (import.meta.env.DEV) (window as unknown as { __graphLabels?: { visible: number; total: number } }).__graphLabels = { visible: kept.length, total: labelBoxes.length }
}

const animate = (time: number) => {
  frameId = requestAnimationFrame(animate)
  if (!renderer || !scene || !camera) return
  const delta = Math.min(48, time - (lastFrame || time)); lastFrame = time
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  if (autoRotate.value && !dragStart && time - lastInteraction > graphVisual.space.autoRotateResumeMs && !reduced) {
    spherical.theta -= graphVisual.space.autoRotateSpeed * delta / 16.67
    syncCamera()
  }
  applyDepth()
  for (const [id, target] of nodeTargets) {
    const position = nodePositionsById.get(id)
    if (!position || !camera || !canvasRef.value) { target.hidden = true; continue }
    const projected = position.clone().project(camera)
    target.hidden = projected.z < -1 || projected.z > 1
    if (!target.hidden) { target.style.left = `${(projected.x + 1) * canvasRef.value.clientWidth / 2}px`; target.style.top = `${(1 - projected.y) * canvasRef.value.clientHeight / 2}px` }
  }
  renderer.render(scene, camera)
}
const onResize = () => {
  if (!canvasRef.value || !renderer || !camera) return
  const { clientWidth: width, clientHeight: height } = canvasRef.value; if (!width || !height) return
  renderer.setSize(width, height, false); camera.aspect = width / height; camera.updateProjectionMatrix()
}
const intersections = (event: PointerEvent) => {
  if (!renderer || !camera || !nodeLayer || !edgeLayer || !canvasRef.value) return [] as ThreeTypes.Intersection[]
  const bounds = canvasRef.value.getBoundingClientRect(); pointer.set((event.clientX - bounds.left) / bounds.width * 2 - 1, -((event.clientY - bounds.top) / bounds.height) * 2 + 1)
  raycaster.setFromCamera(pointer, camera); raycaster.params.Line!.threshold = 5
  return raycaster.intersectObjects([...nodeLayer.children.filter(item => item.userData.kind === 'dot'), ...edgeLayer.children], false)
}
const onPointerDown = (event: PointerEvent) => {
  if (!canvasRef.value) return
  // Capture on the element receiving the pointer sequence so rotation continues
  // smoothly when a drag starts over a node's accessible hit target.
  ;(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId); pressedNodeId = (event.target as HTMLElement).closest<HTMLButtonElement>('.node-hit-target')?.dataset.nodeId || ''; pointers.set(event.pointerId, new THREE!.Vector2(event.clientX, event.clientY)); lastInteraction = performance.now()
  if (pointers.size === 1) { dragStart = new THREE!.Vector2(event.clientX, event.clientY); dragMoved = false }
  else if (pointers.size === 2) { const points = [...pointers.values()]; pinchDistance = points[0].distanceTo(points[1]); dragStart = null }
}
const onPointerMove = (event: PointerEvent) => {
  const previous = pointers.get(event.pointerId)
  if (previous && canvasRef.value) {
    const current = new THREE!.Vector2(event.clientX, event.clientY); pointers.set(event.pointerId, current)
    if (pointers.size === 1 && dragStart && camera) {
      const dx = current.x - previous.x; const dy = current.y - previous.y
      if (Math.abs(current.x - dragStart.x) + Math.abs(current.y - dragStart.y) > 5) dragMoved = true
      spherical.theta -= dx * .006; spherical.phi = THREE!.MathUtils.clamp(spherical.phi - dy * .006, .18, Math.PI - .18); syncCamera(); lastInteraction = performance.now()
    } else if (pointers.size === 2 && camera) {
      const points = [...pointers.values()]; const nextDistance = points[0].distanceTo(points[1]); if (pinchDistance) zoom((pinchDistance - nextDistance) * 1.5); pinchDistance = nextDistance
    }
  } else if (!pointers.size) {
    const hit = intersections(event).find(item => item.object.userData.kind === 'dot'); const nextId = String(hit?.object.userData.nodeId || '')
    if (nextId !== hoveredId) { hoveredId = nextId; if (canvasRef.value) canvasRef.value.style.cursor = nextId ? 'pointer' : 'grab'; buildSceneGraph() }
  }
}
const onPointerUp = (event: PointerEvent, cancelled = false) => {
  if (cancelled) { pressedNodeId = ''; pointers.delete(event.pointerId); if (!pointers.size) { dragStart = null; pinchDistance = 0; dragMoved = false }; return }
  const hadDrag = dragMoved; const tap = intersections(event)
  const hitTarget = (event.target as HTMLElement | null)?.closest<HTMLButtonElement>('.node-hit-target')
  if (!hadDrag && pressedNodeId) {
    const node = visible.value.nodes.find(item => item.id === pressedNodeId)
    if (node) { selectNode(node); suppressedClickNodeId = node.id; suppressedClickAt = performance.now() }
  } else if (!hadDrag && !hitTarget) {
    const nodeHit = tap.find(item => item.object.userData.kind === 'dot')
    if (nodeHit) { const node = visible.value.nodes.find(item => item.id === nodeHit.object.userData.nodeId); if (node) selectNode(node) }
    else {
      const edgeHit = tap.find(item => item.object.userData.kind === 'edge' || item.object.userData.kind === 'edge-focus')
      if (edgeHit) { const edge = visible.value.edges[Math.floor((edgeHit.index ?? 0) / 2)]; if (edge) selectEdge(edge) }
    }
  }
  if (hadDrag && pressedNodeId) { suppressedClickNodeId = pressedNodeId; suppressedClickAt = performance.now() }
  pressedNodeId = ''
  const target = event.currentTarget as HTMLElement
  if (target.hasPointerCapture(event.pointerId)) target.releasePointerCapture(event.pointerId)
  pointers.delete(event.pointerId); if (!pointers.size) { dragStart = null; pinchDistance = 0; dragMoved = false } else if (pointers.size === 1) { dragStart = [...pointers.values()][0].clone(); dragMoved = true }
}
const onPointerCancel = (event: PointerEvent) => onPointerUp(event, true)
const onWheel = (event: WheelEvent) => { event.preventDefault(); zoom(event.deltaY * .7) }
const createScene = async () => {
  if (!canvasRef.value || renderer) return
  try {
    THREE = await import('three')
    if (!canvasRef.value || renderer) return
    raycaster = new THREE!.Raycaster(); pointer = new THREE!.Vector2()
    tmpForward = new THREE!.Vector3(); tmpToCam = new THREE!.Vector3(); tmpProject = new THREE!.Vector3()
    const { cameraDistance, fov } = graphVisual.space
    // theta=0 → 相机在 +z 轴，屏幕水平 = 世界 x、垂直 = 世界 y。
    // phi 必须接近 π/2（水平视角）：一旦俯视，世界 z 会被投影到屏幕纵向，
    // 椭球的 x/z 对称被破坏，正面铺不宽、各方位角形状还不一致。
    // 水平视角 + x/z 等长的椭球 = 投影与方位角无关，转到哪都同一个形状。
    spherical = new THREE!.Spherical(cameraDistance, Math.PI * graphVisual.space.elevation, 0)
    // 关掉色调映射：这是一张「UI 平涂图」不是写实渲染，
    // ACESFilmic 会把 token 颜色整体推偏（实测 #22459c 被渲染成 #003da9），
    // 而验收基准是 token 原色，必须逐位对齐。
    renderer = new THREE!.WebGLRenderer({ alpha: true, antialias: true, powerPreference: 'high-performance' })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2)); renderer.setClearColor(0x000000, 0); renderer.outputColorSpace = THREE!.SRGBColorSpace; renderer.toneMapping = THREE!.NoToneMapping
    renderer.domElement.setAttribute('aria-hidden', 'true'); renderer.domElement.style.touchAction = 'none'; renderer.domElement.style.cursor = 'grab'; canvasRef.value.appendChild(renderer.domElement)
    // 场景透明，背景交给 CSS 渐变 + 云雾光斑；不要 FogExp2（会把文字也雾掉）
    scene = new THREE!.Scene()
    camera = new THREE!.PerspectiveCamera(fov, canvasRef.value.clientWidth / Math.max(canvasRef.value.clientHeight, 1), 1, 6000); syncCamera()
    nodeLayer = new THREE!.Group(); edgeLayer = new THREE!.Group(); scene.add(edgeLayer, nodeLayer)
    buildSceneGraph(); onResize(); resizeObserver = new ResizeObserver(onResize); resizeObserver.observe(canvasRef.value)
    renderer.domElement.addEventListener('pointerdown', onPointerDown); renderer.domElement.addEventListener('pointermove', onPointerMove); renderer.domElement.addEventListener('pointerup', onPointerUp); renderer.domElement.addEventListener('pointercancel', event => onPointerUp(event, true)); renderer.domElement.addEventListener('wheel', onWheel, { passive: false })
    frameId = requestAnimationFrame(animate)
  } catch (error) { console.warn('Three-dimensional knowledge map could not start', error); clearScene(); webglError.value = true }
}
const zoom = (delta: number) => { spherical.radius = THREE!.MathUtils.clamp(spherical.radius + delta * 1.6, graphVisual.space.minCameraDistance, graphVisual.space.maxCameraDistance); syncCamera(); lastInteraction = performance.now() }
const resetCamera = () => { if (!THREE) return; spherical = new THREE!.Spherical(graphVisual.space.cameraDistance, Math.PI * graphVisual.space.elevation, 0); syncCamera(); selectedEdge.value = null; lastInteraction = performance.now() }
const selectNode = (node: GraphNode) => { selectedEdge.value = null; selectedId.value = node.id; graphStore.detail = null; void graphStore.loadDetail(node.id); emit('node-select', node); buildSceneGraph() }
const selectEdge = (edge: GraphEdge) => { selectedEdge.value = { edge, source: props.content.sources[edge.source_ref] }; emit('edge-select', edge); buildSceneGraph() }
const exitFocus = () => { selectedId.value = ''; selectedEdge.value = null; graphStore.detail = null; buildSceneGraph() }
const onCanvasDoubleClick = () => { if (selectedNode.value) emit('expand', selectedNode.value) }
const clearScene = () => {
  cancelAnimationFrame(frameId); resizeObserver?.disconnect(); resizeObserver = null
  if (renderer) {
    renderer.domElement.removeEventListener('pointerdown', onPointerDown); renderer.domElement.removeEventListener('pointermove', onPointerMove); renderer.domElement.removeEventListener('pointerup', onPointerUp); renderer.domElement.removeEventListener('pointercancel', onPointerUp); renderer.domElement.removeEventListener('wheel', onWheel)
  }
  disposeLayer(nodeLayer); disposeLayer(edgeLayer); renderer?.dispose(); renderer?.domElement.remove(); renderer = null; scene = null; camera = null; nodeLayer = null; edgeLayer = null; nodePositionsById.clear(); nodeLevelsById.clear(); nodeTargets.clear()
}
onMounted(() => { void createScene(); if (props.initialFocusId) { const node = props.content.nodes.find(item => item.id === props.initialFocusId); if (node) selectNode(node) } })
onBeforeUnmount(clearScene)
// 画布容器在 v-if 链里：加载中/空态时它不存在，数据到了才被创建出来。
// 打开 /atlas 时 loading 必然先为 true，于是 onMounted 那一刻 canvasRef 还是
// undefined，createScene 直接早退——之后再没人调用它，用户看到的就是「28 个节点 /
// 34 条关系」的统计数字配一张空画布。所以必须盯着 canvasRef：容器一出现就补初始化。
// 另外容器被销毁重建（重新加载、切空态再回来）时，旧 renderer 的 canvas 已经跟着
// 旧容器一起被摘出文档，只把 renderer 当「已初始化」会让画布永远空着，要重新挂回去。
watch(canvasRef, (element) => {
  if (!element) return
  if (!renderer) { void createScene(); return }
  if (renderer.domElement.parentElement !== element) {
    element.appendChild(renderer.domElement)
    onResize()
    buildSceneGraph()
  }
}, { flush: 'post' })
watch(visible, () => { if (renderer) buildSceneGraph() }, { deep: true })
watch(selectedId, () => { if (renderer) buildSceneGraph() })
watch(() => props.initialFocusId, id => { const node = props.content.nodes.find(item => item.id === id); if (node) selectNode(node) })
</script>

<style scoped lang="scss">
/* 无容器：去边框、去圆角卡、去阴影、去网格。图谱直接漂浮在云雾渐变上。 */
.graph-view{--graph-surface:transparent;position:relative;isolation:isolate;overflow:hidden;border-radius:var(--qh-radius-card);background:linear-gradient(180deg,var(--qh-graph-stage-glow) 0%,var(--qh-graph-stage) 45%,var(--qh-graph-stage-deep) 100%);color:var(--qh-graph-ink);font-family:var(--qh-font-body)}
.graph-view:before,.graph-view:after{position:absolute;z-index:-1;pointer-events:none;content:''}
.graph-view:before{inset:0;background:radial-gradient(ellipse 62% 48% at 22% 16%,var(--qh-graph-cloud),transparent 70%),radial-gradient(ellipse 54% 42% at 82% 10%,var(--qh-graph-cloud-soft),transparent 72%),radial-gradient(ellipse 70% 46% at 50% 78%,var(--qh-graph-cloud-soft),transparent 74%)}
.graph-view:after{top:28%;left:22%;width:56%;height:44%;border-radius:50%;background:var(--qh-graph-cloud);filter:blur(92px)}
.graph-head{position:relative;z-index:2;display:grid;grid-template-columns:minmax(230px,1fr) auto;gap:20px 28px;padding:25px 30px 0}
.title-block .eyebrow{display:flex;align-items:center;gap:7px;margin:0 0 8px;color:var(--qh-graph-l1);font-size:11px;font-weight:800;letter-spacing:1.5px}.eyebrow-mark{font-size:15px}
.title-block h2{margin:0;color:var(--qh-graph-l0);font-family:var(--qh-font-display);font-size:25px;font-weight:800;letter-spacing:.015em}.title-block .subtitle{margin:6px 0 0;color:var(--qh-graph-l2);font-size:12px;letter-spacing:.025em}
.graph-counts{display:flex;align-items:center;gap:13px;align-self:center;padding:9px 14px;border-radius:var(--qh-radius-pill);background:var(--qh-graph-cloud-soft);color:var(--qh-graph-l2);font-size:11px}.graph-counts b{margin-right:3px;color:var(--qh-graph-l0);font-size:14px}.graph-counts i{width:1px;height:13px;background:var(--qh-graph-edge-line)}
/* 筛选器弱化成透明行，不要卡片感 */
.filters{grid-column:1/-1;display:grid;gap:9px;padding:10px 2px}
.filter-row{display:flex;flex-wrap:wrap;align-items:center;gap:6px}.filter-label{width:35px;color:var(--qh-graph-l3);font-size:10px;font-weight:700;letter-spacing:.08em}
.filter-row button{display:inline-flex;align-items:center;gap:5px;min-height:27px;padding:4px 9px;border:1px solid transparent;border-radius:var(--qh-radius-pill);background:transparent;color:var(--qh-graph-l2);font:inherit;font-size:10px;cursor:pointer;transition:background var(--qh-duration-fast) ease,border-color var(--qh-duration-fast) ease,color var(--qh-duration-fast) ease,opacity var(--qh-duration-fast) ease}
.filter-row button i{width:6px;height:6px;border:1.5px solid currentColor;border-radius:50%;background:transparent}.filter-row button.active{border-color:var(--qh-graph-edge-line);background:var(--qh-graph-cloud);color:var(--qh-graph-l0)}.filter-row button.active i{background:currentColor}.filter-row button:hover{border-color:var(--qh-graph-edge-line)}
.relation-compatible{--relation-color:var(--qh-graph-edge-compatible)}.relation-restraint{--relation-color:var(--qh-graph-edge-restraint)}.relation-forbidden{--relation-color:var(--qh-graph-edge-forbidden)}.relation-benefit{--relation-color:var(--qh-graph-edge-benefit)}.relation-evidence{--relation-color:var(--qh-graph-edge-evidence)}.relation-composition{--relation-color:var(--qh-graph-edge-composition)}.relation-contrast{--relation-color:var(--qh-graph-edge-contrast)}.relation-cluster{--relation-color:var(--qh-graph-edge-cluster)}
.relation-filters button.active{color:var(--relation-color)}.entity-herb{--entity-color:var(--qh-graph-node-herb)}.entity-syndrome{--entity-color:var(--qh-graph-node-syndrome)}.entity-formula{--entity-color:var(--qh-graph-node-formula)}.entity-category{--entity-color:var(--qh-graph-node-category)}.entity-filters button.active{color:var(--entity-color)}
/* 层级筛选：T1 用禁忌色，提醒它不是「更可信」而是「更危险」；角标给的是边数，不让人盲点 */
.tier-filters button small{margin-left:4px;color:var(--qh-graph-l3);font-size:9px;font-variant-numeric:tabular-nums}
.tier-filters button.active small{color:currentColor;opacity:.72}
.tier-filters button:first-of-type.active{color:var(--qh-graph-edge-forbidden)}
/* 「只看禁忌」是预设不是层级：虚线边框 + 前置分隔，避免被读成第三个 tier */
.tier-filters .tier-preset{margin-left:6px;border-style:dashed;color:var(--qh-graph-l3)}
.tier-filters .tier-preset.active{border-style:solid;border-color:var(--qh-graph-edge-forbidden);background:transparent;color:var(--qh-graph-edge-forbidden)}
/* 高度用 clamp 跟随视口：固定 min-height 会让 canvas 溢出被裁，节点跑出可视区 */
.graph-stage{position:relative;height:clamp(430px,54vh,660px);margin-top:2px;overflow:hidden}.graph-canvas{position:absolute;inset:0;width:100%;height:100%;outline:none}.graph-canvas :deep(canvas){display:block;width:100%;height:100%;outline:none}
.graph-hit-layer{position:absolute;z-index:1;inset:0;pointer-events:none}.node-hit-target{position:absolute;width:46px;height:46px;transform:translate(-50%,-50%);border:0;border-radius:50%;background:transparent;pointer-events:auto;cursor:pointer}.node-hit-target:focus-visible{outline:2px solid var(--qh-graph-l1);outline-offset:3px}
.stage-caption{position:absolute;z-index:1;bottom:19px;left:50%;display:flex;align-items:center;gap:8px;max-width:calc(100% - 170px);padding:8px 13px;transform:translateX(-50%);border-radius:var(--qh-radius-pill);background:var(--qh-graph-cloud);color:var(--qh-graph-l2);font-size:11px;white-space:nowrap;backdrop-filter:blur(14px)}.live-indicator{width:6px;height:6px;flex:0 0 6px;border-radius:50%;background:var(--qh-graph-l1)}
/* 「未显示 N 条」是提示不是警告：用次级文字色，避免和禁忌红抢注意力 */
.stage-caption .hidden-note{padding-left:8px;border-left:1px solid var(--qh-graph-edge-line);color:var(--qh-graph-l3);font-variant-numeric:tabular-nums}
.camera-controls{position:absolute;z-index:2;right:21px;bottom:18px;display:flex;align-items:center;overflow:hidden;border-radius:var(--qh-radius-pill);background:var(--qh-graph-cloud);backdrop-filter:blur(15px)}.camera-controls button{display:grid;width:36px;height:36px;place-items:center;border:0;background:transparent;color:var(--qh-graph-l1);font-family:var(--qh-font-body);font-size:17px;cursor:pointer;transition:background var(--qh-duration-fast) ease,color var(--qh-duration-fast) ease}.camera-controls button:hover,.camera-controls button.active{background:var(--qh-graph-cloud-soft);color:var(--qh-graph-l0)}.camera-controls span{width:1px;height:18px;background:var(--qh-graph-edge-line)}
.canvas-fallback{position:absolute;inset:15% 10%;z-index:2;display:grid;align-content:center;justify-items:center;gap:8px;border-radius:var(--qh-radius-card);background:var(--qh-graph-cloud);color:var(--qh-graph-l2);text-align:center}.canvas-fallback p{margin:0;color:var(--qh-graph-l0);font-weight:800}.canvas-fallback div{display:flex;flex-wrap:wrap;justify-content:center;gap:7px;margin-top:12px}.canvas-fallback button{padding:7px 12px;border:1px solid var(--qh-graph-edge-line);border-radius:var(--qh-radius-pill);background:var(--qh-graph-white);color:var(--qh-graph-l1);cursor:pointer}
/* 图例：去掉分隔线和背景，让它像注释而不像工具栏 */
.graph-footer{position:relative;z-index:1;display:flex;flex-wrap:wrap;align-items:center;gap:8px 18px;min-height:51px;padding:11px 24px}.graph-footer>p{margin:0;color:var(--qh-graph-l2);font-size:10px;font-weight:800}.legend-list{display:flex;flex-wrap:wrap;gap:7px 16px}.legend-list span{display:inline-flex;align-items:center;gap:6px;color:var(--qh-graph-l3);font-size:10px}.legend-list i{width:14px;border-top:1.5px solid var(--relation-color)}.legend-note{margin-left:auto;color:var(--qh-graph-l3);font-size:9px}
.detail-panel,.edge-card{position:absolute;z-index:4;top:15px;right:16px;bottom:66px;width:min(325px,calc(100% - 32px));overflow:auto;padding:19px;border:1px solid var(--qh-graph-edge-line);border-radius:var(--qh-radius-card);background:var(--qh-graph-surface-panel);box-shadow:0 15px 48px var(--qh-graph-shadow-panel);backdrop-filter:blur(23px);scrollbar-color:var(--qh-graph-l2) transparent;scrollbar-width:thin}.detail-head{display:flex;align-items:center;gap:11px;padding-bottom:15px;border-bottom:1px solid var(--qh-graph-line-clear)}.detail-symbol{display:grid;width:42px;height:42px;flex:0 0 42px;place-items:center;border-radius:15px;background:var(--qh-graph-ink-wash);color:var(--entity-color);font-family:var(--qh-font-reading);font-size:20px;font-weight:700}.detail-title{min-width:0;flex:1}.detail-title>span,.card-kicker{color:var(--qh-graph-l2);font-size:10px}.detail-title h3{margin:2px 0 0;color:var(--qh-graph-l0);font-size:24px;font-weight:800}.card-close{display:grid;width:29px;height:29px;flex:0 0 29px;place-items:center;border:0;border-radius:50%;background:var(--qh-graph-ink-wash);color:var(--qh-graph-l2);font-size:20px;line-height:1;cursor:pointer}.card-close:hover{background:var(--qh-graph-ink-hover);color:var(--qh-graph-l0)}
.node-brief{margin:13px 0;color:var(--qh-graph-l1);font-size:12px;line-height:1.8}.detail-section{padding:10px 0;border-top:1px solid var(--qh-graph-line-soft)}.detail-section h4,.connections h4{margin:0;color:var(--qh-graph-l1);font-size:10px;font-weight:800;letter-spacing:.07em}.detail-section p{margin:5px 0 0;color:var(--qh-graph-l2);font-size:11px;line-height:1.8}.frequency{display:block;margin-top:5px;color:var(--qh-graph-edge-benefit);font-size:10px}.detail-loading{color:var(--qh-graph-l2);font-size:11px}.connections{padding-top:12px;border-top:1px solid var(--qh-graph-line-soft)}.connections h4{display:flex;align-items:center;justify-content:space-between;margin-bottom:6px}.connections h4 span,.source-details summary span{display:grid;min-width:19px;height:19px;place-items:center;border-radius:50%;background:var(--qh-graph-ink-wash);color:var(--qh-graph-l1);font-size:9px}
.connection-row{display:flex;width:100%;align-items:center;gap:9px;padding:8px 2px;border:0;border-bottom:1px solid var(--qh-graph-line-faint);background:transparent;text-align:left;cursor:pointer}.connection-row>i{width:7px;height:7px;flex:0 0 7px;border-radius:50%;background:var(--relation-color)}.connection-copy{display:grid;min-width:0;flex:1;gap:2px}.connection-copy b{color:var(--qh-graph-l1);font-size:11px}.connection-copy small{overflow:hidden;color:var(--qh-graph-l3);font-size:9px;text-overflow:ellipsis;white-space:nowrap}.connection-arrow{color:var(--qh-graph-l3);font-size:18px}.connection-row:hover .connection-copy b{color:var(--qh-graph-l0)}
.source-details{margin-top:12px;border-top:1px solid var(--qh-graph-line-soft);padding-top:10px}.source-details summary{display:flex;align-items:center;justify-content:space-between;color:var(--qh-graph-l1);font-size:10px;font-weight:700;cursor:pointer;list-style:none}.source-details summary::-webkit-details-marker{display:none}.source-details blockquote,.edge-card blockquote{margin:10px 0 0;padding-left:11px;border-left:2px solid var(--qh-graph-edge-evidence);color:var(--qh-graph-l2);font-size:10px;line-height:1.7}.source-details blockquote b,.edge-card blockquote b{display:block;color:var(--qh-graph-l0)}.source-details blockquote p,.edge-card blockquote p{margin:4px 0}.source-details blockquote small,.edge-card blockquote small{color:var(--qh-graph-l1)}
.expand-button{display:flex;width:100%;align-items:center;justify-content:space-between;margin-top:14px;padding:11px 13px;border:1px solid var(--qh-graph-edge-line);border-radius:var(--qh-radius-card-sm);background:var(--qh-graph-cloud-soft);color:var(--qh-graph-l0);font:inherit;font-size:10px;font-weight:700;cursor:pointer}.expand-button:hover{background:var(--qh-graph-cloud)}.expand-button span{font-size:15px}
.edge-card{top:auto;right:auto;bottom:74px;left:18px;width:min(300px,calc(100% - 36px));padding:16px}.edge-card h3{margin:5px 30px 6px 0;color:var(--qh-graph-l0);font-size:17px}.edge-relation-tag{display:inline-flex;padding:4px 8px;border-radius:var(--qh-radius-pill);background:var(--qh-graph-ink-wash);color:var(--qh-graph-l1);font-size:10px}.edge-card .card-close{position:absolute;top:12px;right:12px}
.graph-state{height:clamp(430px,54vh,660px)}.detail-enter-active,.detail-leave-active{transition:transform var(--qh-duration-md) ease,opacity var(--qh-duration-md) ease}.detail-enter-from,.detail-leave-to{transform:translateX(20px);opacity:0}.source-enter-active,.source-leave-active{transition:opacity var(--qh-duration-fast) ease}.source-enter-from,.source-leave-to{opacity:0}
@media(max-width:850px){.graph-head{gap:14px;padding:20px 20px 0}.graph-stage,.graph-state{height:clamp(420px,52vh,600px)}.title-block h2{font-size:22px}.title-block .subtitle{max-width:390px;line-height:1.6}.filters{gap:7px;padding:8px 2px}.filter-row{gap:4px}.filter-row button{padding:4px 7px;font-size:9px}.legend-note{width:100%;margin-left:53px}.detail-panel{right:10px;bottom:60px;width:min(310px,calc(100% - 20px))}.stage-caption{bottom:70px;left:12px;max-width:calc(100% - 142px);transform:none;font-size:9px}.camera-controls{right:12px;bottom:14px}}
@media(max-width:560px){.graph-view{border-radius:var(--qh-radius-card-sm)}.graph-head{grid-template-columns:1fr auto;padding:17px 14px 0}.graph-counts{gap:7px;padding:7px 9px;font-size:9px}.graph-counts b{font-size:12px}.title-block .eyebrow{margin-bottom:5px;font-size:9px}.title-block h2{font-size:19px}.title-block .subtitle{grid-column:1/-1;max-width:300px;font-size:10px}.filters{grid-column:1/-1;margin-inline:-3px}.filter-label{width:29px;font-size:9px}.filter-row button{gap:4px;min-height:25px;padding:3px 6px;font-size:8px}.filter-row button i{width:5px;height:5px}.graph-stage,.graph-state{height:clamp(380px,50vh,530px)}.graph-footer{gap:8px;padding:10px 13px}.legend-list{gap:7px 11px}.legend-list span{font-size:9px}.legend-note{margin-left:0;font-size:8px}.camera-controls button{width:32px;height:33px}.stage-caption{bottom:64px;left:10px;max-width:calc(100% - 129px);padding:7px 9px;font-size:8px}.detail-panel{top:10px;right:8px;bottom:54px;width:min(300px,calc(100% - 16px));padding:15px}.detail-title h3{font-size:21px}.edge-card{bottom:60px;left:10px}.canvas-fallback{inset:16% 4%}}
@media(prefers-reduced-motion:reduce){.detail-enter-active,.detail-leave-active,.source-enter-active,.source-leave-active{transition:none}.filter-row button,.camera-controls button{transition:none}}
</style>
