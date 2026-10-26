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
        <div class="stage-caption"><span class="live-indicator" />{{ selectedNode ? `正在查看「${selectedNode.name}」` : '拖动画布旋转 · 滚轮缩放 · 点击节点查看' }}</div>
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
      <footer class="graph-footer"><p>关系图例</p><div class="legend-list"><span v-for="item in legendGroups" :key="item" :class="`relation-${relationColorKey(item)}`"><i />{{ graphVisual.relation[item].group }}</span></div><span class="legend-note">节点大小与远近表现图谱层级，线条样式区分关系</span></footer>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type * as ThreeTypes from 'three'
import { graphVisual, relationGroup, relationStyle, type GraphRelationGroup } from '../../config/graph'
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
  const indications = (d.功效主治?.主治 || []).map(item => typeof item === 'string' ? item : JSON.stringify(item))
  return [
    { label: '性味归经', value: d.性味归经 },
    { label: '功效主治', value: [...formula, ...indications].join('；') },
    { label: '常用剂量与炮制', value: d.常用剂量与炮制 ? `${d.常用剂量与炮制.剂量 || ''} ${d.常用剂量与炮制.炮制 || ''}`.trim() : '' },
    { label: '使用禁忌', value: d.使用禁忌?.join('；') },
    { label: '经典出处', value: d.经典出处?.join(' · ') },
  ].filter(item => item.value) as Array<{ label: string; value: string }>
})
const visible = computed(() => {
  const nodeById = new Map(props.content.nodes.map(node => [node.id, node]))
  const edges = props.content.edges.filter(edge => relationFilters.value.has(relationGroup(edge.relation_type)) && entityFilters.value.has(nodeById.get(edge.source)?.type || 'herb') && entityFilters.value.has(nodeById.get(edge.target)?.type || 'herb'))
  const limited = props.content.nodes.length > graphVisual.maxVisibleNodes ? [...edges].sort((a, b) => (b.confidence ?? .5) - (a.confidence ?? .5)).slice(0, graphVisual.maxVisibleNodes * 2) : edges
  const used = new Set([props.content.center_id, ...limited.flatMap(edge => [edge.source, edge.target])])
  return { nodes: props.content.nodes.filter(node => used.has(node.id) && entityFilters.value.has(node.type)).slice(0, graphVisual.maxVisibleNodes), edges: limited.filter(edge => used.has(edge.source) && used.has(edge.target)) }
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

let renderer: ThreeTypes.WebGLRenderer | null = null
let scene: ThreeTypes.Scene | null = null
let camera: ThreeTypes.PerspectiveCamera | null = null
let nodeLayer: ThreeTypes.Group | null = null
let edgeLayer: ThreeTypes.Group | null = null
let starfield: ThreeTypes.Points | null = null
let nodePositionsById = new Map<string, ThreeTypes.Vector3>()
const nodeTargets = new Map<string, HTMLButtonElement>()
let resizeObserver: ResizeObserver | null = null
let frameId = 0
let lastFrame = 0
let lastInteraction = 0
let spherical: ThreeTypes.Spherical
let raycaster: ThreeTypes.Raycaster
let pointer: ThreeTypes.Vector2
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
  return value || root.getPropertyValue('--qh-graph-node-herb').trim() || 'steelblue'
}
const makeGlowTexture = () => {
  const canvas = document.createElement('canvas'); canvas.width = 128; canvas.height = 128
  const context = canvas.getContext('2d')!
  const glow = context.createRadialGradient(64, 64, 2, 64, 64, 64)
  glow.addColorStop(0, resolveColor('var(--qh-graph-canvas-halo)')); glow.addColorStop(.42, resolveColor('var(--qh-graph-canvas-halo-mid)')); glow.addColorStop(1, resolveColor('var(--qh-graph-star-opacity)'))
  context.fillStyle = glow; context.fillRect(0, 0, 128, 128)
  return new THREE!.CanvasTexture(canvas)
}
const makeLabel = (text: string, color: string, emphasized = false) => {
  const canvas = document.createElement('canvas'); const context = canvas.getContext('2d')!
  const fontSize = emphasized ? 32 : 27; context.font = `${emphasized ? 700 : 600} ${fontSize}px "Microsoft YaHei", "PingFang SC", sans-serif`
  const width = Math.ceil(context.measureText(text).width) + 34; canvas.width = width * 2; canvas.height = 82
  context.scale(2, 2); context.font = `${emphasized ? 700 : 600} ${fontSize}px "Microsoft YaHei", "PingFang SC", sans-serif`; context.textAlign = 'center'; context.textBaseline = 'middle'
  context.fillStyle = resolveColor('var(--qh-graph-label-fill)'); context.beginPath(); context.roundRect(1, 1, width - 2, 40, 13); context.fill()
  context.fillStyle = color; context.fillText(text, width / 2, 21)
  const texture = new THREE!.CanvasTexture(canvas); texture.colorSpace = THREE!.SRGBColorSpace
  const sprite = new THREE!.Sprite(new THREE!.SpriteMaterial({ map: texture, transparent: true, depthWrite: false, opacity: .98, sizeAttenuation: true }))
  sprite.scale.set(width * .5, 20.5, 1); sprite.userData.kind = 'label'; sprite.renderOrder = 2
  return sprite
}
const nodePositions = (nodes: GraphNode[], edges: GraphEdge[]) => {
  if (!THREE) return new Map<string, ThreeTypes.Vector3>()
  const centerId = props.content.center_id
  const adjacency = new Map(nodes.map(node => [node.id, [] as string[]]))
  edges.forEach(edge => { adjacency.get(edge.source)?.push(edge.target); adjacency.get(edge.target)?.push(edge.source) })
  const levels = new Map<string, number>([[centerId, 0]]); const queue = [centerId]
  for (let i = 0; i < queue.length; i++) for (const neighbor of adjacency.get(queue[i]) || []) if (!levels.has(neighbor)) { levels.set(neighbor, levels.get(queue[i])! + 1); queue.push(neighbor) }
  const orphans = nodes.filter(node => !levels.has(node.id)); orphans.forEach((node, index) => levels.set(node.id, Math.max(1, 1 + Math.floor(index / 12))))
  const positions = new Map<string, ThreeTypes.Vector3>(); const grouped = new Map<number, GraphNode[]>()
  nodes.filter(node => node.id !== centerId).forEach(node => { const level = levels.get(node.id) || 1; const group = grouped.get(level) || []; group.push(node); grouped.set(level, group) })
  positions.set(centerId, new THREE!.Vector3())
  const golden = Math.PI * (3 - Math.sqrt(5))
  grouped.forEach((group, level) => {
    group.forEach((node, index) => {
      const y = 1 - 2 * (index + .5) / group.length; const ring = Math.sqrt(Math.max(0, 1 - y * y)); const angle = golden * index + (node.type === 'formula' ? 1.7 : node.type === 'syndrome' ? .85 : node.type === 'category' ? 2.55 : 0)
      const distance = Math.min(graphVisual.space.sphereRadius * .92, 125 + (level - 1) * 95 + Math.sqrt(group.length) * 17)
      positions.set(node.id, new THREE!.Vector3(Math.cos(angle) * ring * distance, y * distance, Math.sin(angle) * ring * distance))
    })
  })
  return positions
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
const buildSceneGraph = () => {
  if (!THREE || !scene || !nodeLayer || !edgeLayer) return
  disposeLayer(nodeLayer); disposeLayer(edgeLayer)
  const nodeById = new Map(visible.value.nodes.map(node => [node.id, node])); const positions = nodePositions(visible.value.nodes, visible.value.edges)
  nodePositionsById = positions
  const glowMap = new Map<GraphEntityType, ThreeTypes.Texture>()
  visible.value.nodes.forEach((node, index) => {
    const color = resolveColor(graphVisual.entity[node.type].color); const center = node.id === props.content.center_id; const position = positions.get(node.id) || new THREE!.Vector3((index - visible.value.nodes.length / 2) * 32, 0, -50)
    let glowTexture = glowMap.get(node.type); if (!glowTexture) { glowTexture = makeGlowTexture(); glowMap.set(node.type, glowTexture) }
    const halo = new THREE!.Sprite(new THREE!.SpriteMaterial({ map: glowTexture, transparent: true, depthWrite: false, opacity: node.id === selectedId.value ? .9 : .5, color }))
    halo.position.copy(position); halo.scale.set(center ? 104 : 78, center ? 104 : 78, 1); halo.userData.nodeId = node.id; nodeLayer!.add(halo)
    const radius = center ? 17 : node.type === 'formula' ? 12.5 : 10.5
    const sphere = new THREE!.Mesh(new THREE!.SphereGeometry(radius, 26, 20), new THREE!.MeshStandardMaterial({ color, roughness: .32, metalness: .06, emissive: color, emissiveIntensity: center ? .15 : .09, transparent: true, opacity: selectedId.value && selectedId.value !== node.id && !connectedNodes.value.some(item => item.node.id === node.id) ? .26 : 1 }))
    sphere.position.copy(position); sphere.userData.nodeId = node.id; sphere.userData.kind = 'node'; sphere.userData.baseScale = 1; nodeLayer!.add(sphere)
    const label = makeLabel(node.name, resolveColor(graphVisual.canvas.labelFill), center); label.position.copy(position).add(new THREE!.Vector3(0, -radius - 15, 0)); label.userData.nodeId = node.id; nodeLayer!.add(label)
    if (node.id === selectedId.value || node.id === hoveredId) {
      const ring = new THREE!.Mesh(new THREE!.TorusGeometry(radius + 5.2, 1.2, 8, 48), new THREE!.MeshBasicMaterial({ color, transparent: true, opacity: .62 }))
      ring.position.copy(position); ring.quaternion.copy(camera?.quaternion || new THREE!.Quaternion()); ring.userData.nodeId = node.id; nodeLayer!.add(ring)
    }
  })
  const multiplicity = new Map<string, number>()
  visible.value.edges.forEach((edge, index) => {
    const source = positions.get(edge.source); const target = positions.get(edge.target); if (!source || !target) return
    const style = relationStyle(edge.relation_type); const color = resolveColor(style.color); const key = [edge.source, edge.target].sort().join('|'); const parallel = multiplicity.get(key) || 0; multiplicity.set(key, parallel + 1)
    const direction = new THREE!.Vector3().subVectors(target, source); const perpendicular = new THREE!.Vector3().crossVectors(direction, new THREE!.Vector3(0, 1, 0)).normalize(); if (perpendicular.lengthSq() < .05) perpendicular.set(1, 0, 0)
    const bend = perpendicular.multiplyScalar((parallel - .5) * 25 + 10); const control = new THREE!.Vector3().addVectors(source, target).multiplyScalar(.5).add(bend)
    const curve = new THREE!.QuadraticBezierCurve3(source, control, target); const focused = !selectedId.value || edge.source === selectedId.value || edge.target === selectedId.value
    const material = new THREE!.LineBasicMaterial({ color, transparent: true, opacity: focused ? (selectedEdge.value?.edge === edge ? .92 : .62) : .11, depthWrite: false, linewidth: 1 })
    const geometry = new THREE!.BufferGeometry().setFromPoints(curve.getPoints(32)); const line = new THREE!.Line(geometry, material); line.userData.edgeIndex = index; line.userData.kind = 'edge'; edgeLayer!.add(line)
    if (style.dash !== 'solid') {
      const dashedMaterial = style.dash === 'dot' ? new THREE!.LineDashedMaterial({ color, transparent: true, opacity: focused ? .58 : .1, dashSize: 3, gapSize: 5, depthWrite: false }) : new THREE!.LineDashedMaterial({ color, transparent: true, opacity: focused ? .58 : .1, dashSize: 8, gapSize: 5, depthWrite: false })
      line.material = dashedMaterial; line.computeLineDistances()
    }
    if (style.dash === 'arrow') {
      const point = curve.getPoint(.89); const tangent = curve.getTangent(.89).normalize(); const arrow = new THREE!.Mesh(new THREE!.ConeGeometry(4.5, 13, 10), new THREE!.MeshBasicMaterial({ color, transparent: true, opacity: focused ? .78 : .12, depthWrite: false }))
      arrow.position.copy(point); arrow.quaternion.setFromUnitVectors(new THREE!.Vector3(0, 1, 0), tangent); edgeLayer!.add(arrow)
    }
    line.userData.relationGroup = relationGroup(edge.relation_type); line.userData.edge = edge
    if (!nodeById.has(edge.source) || !nodeById.has(edge.target)) line.visible = false
  })
}
const syncCamera = () => { if (!camera) return; camera.position.setFromSpherical(spherical); camera.lookAt(0, 0, 0); camera.updateMatrixWorld() }
const animate = (time: number) => {
  frameId = requestAnimationFrame(animate)
  if (!renderer || !scene || !camera) return
  const delta = Math.min(48, time - (lastFrame || time)); lastFrame = time
  if (autoRotate.value && !dragStart && time - lastInteraction > 1100 && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) { spherical.theta -= graphVisual.space.autoRotateSpeed * delta / 16.67; syncCamera() }
  const pulse = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : Math.sin(time / graphVisual.motion.pulseMs)
  nodeLayer?.children.forEach(object => { if (object.userData.kind === 'node' && object.userData.nodeId === props.content.center_id) object.scale.setScalar(1 + pulse * .035) })
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
  return raycaster.intersectObjects([...nodeLayer.children.filter(item => item.userData.kind === 'node'), ...edgeLayer.children], false)
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
    const hit = intersections(event).find(item => item.object.userData.kind === 'node'); const nextId = String(hit?.object.userData.nodeId || '')
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
    const nodeHit = tap.find(item => item.object.userData.kind === 'node')
    if (nodeHit) { const node = visible.value.nodes.find(item => item.id === nodeHit.object.userData.nodeId); if (node) selectNode(node) }
    else { const edgeHit = tap.find(item => item.object.userData.kind === 'edge'); if (edgeHit) { const edge = visible.value.edges[edgeHit.object.userData.edgeIndex]; if (edge) selectEdge(edge) } }
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
    raycaster = new THREE!.Raycaster(); pointer = new THREE!.Vector2(); spherical = new THREE!.Spherical(graphVisual.space.cameraDistance, Math.PI * .37, Math.PI * .5)
    renderer = new THREE!.WebGLRenderer({ alpha: true, antialias: true, powerPreference: 'high-performance' })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.8)); renderer.setClearColor(0x000000, 0); renderer.outputColorSpace = THREE!.SRGBColorSpace; renderer.toneMapping = THREE!.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.12
    renderer.domElement.setAttribute('aria-hidden', 'true'); renderer.domElement.style.touchAction = 'none'; renderer.domElement.style.cursor = 'grab'; canvasRef.value.appendChild(renderer.domElement)
    scene = new THREE!.Scene(); scene.fog = new THREE!.FogExp2(resolveColor('var(--qh-graph-stage)'), .0007)
    camera = new THREE!.PerspectiveCamera(43, canvasRef.value.clientWidth / Math.max(canvasRef.value.clientHeight, 1), .1, 2400); spherical = new THREE!.Spherical(graphVisual.space.cameraDistance, Math.PI * .37, Math.PI * .5); syncCamera()
    scene.add(new THREE!.AmbientLight(0xffffff, 2.25)); const keyLight = new THREE!.DirectionalLight(0xffffff, 2.0); keyLight.position.set(-150, 260, 320); scene.add(keyLight)
    const fillLight = new THREE!.PointLight(resolveColor('var(--qh-graph-node-formula)'), 260, 950); fillLight.position.set(180, -120, -200); scene.add(fillLight)
    nodeLayer = new THREE!.Group(); edgeLayer = new THREE!.Group(); scene.add(edgeLayer, nodeLayer)
    const starPositions = new Float32Array(720 * 3); let seed = 8173; const random = () => { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646 }
    for (let i = 0; i < starPositions.length; i += 3) { starPositions[i] = (random() - .5) * 1320; starPositions[i + 1] = (random() - .5) * 960; starPositions[i + 2] = (random() - .5) * 1080 }
    const starGeometry = new THREE!.BufferGeometry(); starGeometry.setAttribute('position', new THREE!.BufferAttribute(starPositions, 3)); starfield = new THREE!.Points(starGeometry, new THREE!.PointsMaterial({ color: resolveColor('var(--qh-graph-muted)'), size: 1.45, transparent: true, opacity: .28, depthWrite: false, sizeAttenuation: true })); scene.add(starfield)
    buildSceneGraph(); onResize(); resizeObserver = new ResizeObserver(onResize); resizeObserver.observe(canvasRef.value)
    renderer.domElement.addEventListener('pointerdown', onPointerDown); renderer.domElement.addEventListener('pointermove', onPointerMove); renderer.domElement.addEventListener('pointerup', onPointerUp); renderer.domElement.addEventListener('pointercancel', event => onPointerUp(event, true)); renderer.domElement.addEventListener('wheel', onWheel, { passive: false })
    frameId = requestAnimationFrame(animate)
  } catch (error) { console.warn('Three-dimensional knowledge map could not start', error); clearScene(); webglError.value = true }
}
const zoom = (delta: number) => { spherical.radius = THREE!.MathUtils.clamp(spherical.radius + delta, graphVisual.space.minCameraDistance, graphVisual.space.maxCameraDistance); syncCamera(); lastInteraction = performance.now() }
const resetCamera = () => { if (!THREE) return; spherical = new THREE!.Spherical(graphVisual.space.cameraDistance, Math.PI * .37, Math.PI * .5); syncCamera(); selectedEdge.value = null; lastInteraction = performance.now() }
const selectNode = (node: GraphNode) => { selectedEdge.value = null; selectedId.value = node.id; graphStore.detail = null; void graphStore.loadDetail(node.id); emit('node-select', node); buildSceneGraph() }
const selectEdge = (edge: GraphEdge) => { selectedEdge.value = { edge, source: props.content.sources[edge.source_ref] }; emit('edge-select', edge); buildSceneGraph() }
const exitFocus = () => { selectedId.value = ''; selectedEdge.value = null; graphStore.detail = null; buildSceneGraph() }
const onCanvasDoubleClick = () => { if (selectedNode.value) emit('expand', selectedNode.value) }
const clearScene = () => {
  cancelAnimationFrame(frameId); resizeObserver?.disconnect(); resizeObserver = null
  if (renderer) {
    renderer.domElement.removeEventListener('pointerdown', onPointerDown); renderer.domElement.removeEventListener('pointermove', onPointerMove); renderer.domElement.removeEventListener('pointerup', onPointerUp); renderer.domElement.removeEventListener('pointercancel', onPointerUp); renderer.domElement.removeEventListener('wheel', onWheel)
  }
  disposeLayer(nodeLayer); disposeLayer(edgeLayer); starfield?.geometry.dispose(); (starfield?.material as ThreeTypes.Material | undefined)?.dispose(); renderer?.dispose(); renderer?.domElement.remove(); renderer = null; scene = null; camera = null; nodeLayer = null; edgeLayer = null; starfield = null; nodePositionsById.clear(); nodeTargets.clear()
}
onMounted(() => { void createScene(); if (props.initialFocusId) { const node = props.content.nodes.find(item => item.id === props.initialFocusId); if (node) selectNode(node) } })
onBeforeUnmount(clearScene)
watch(visible, () => { if (renderer) buildSceneGraph() }, { deep: true })
watch(selectedId, () => { if (renderer) buildSceneGraph() })
watch(() => props.initialFocusId, id => { const node = props.content.nodes.find(item => item.id === id); if (node) selectNode(node) })
</script>

<style scoped lang="scss">
.graph-view{--graph-surface:var(--qh-graph-panel);position:relative;isolation:isolate;overflow:hidden;border:1px solid var(--qh-graph-border);border-radius:var(--qh-radius-card);background:linear-gradient(153deg,var(--qh-graph-stage-glow) 0%,var(--qh-graph-stage) 54%,var(--qh-graph-stage-deep) 100%);box-shadow:0 22px 70px var(--qh-graph-shadow),inset 0 1px 0 var(--qh-graph-surface-clear);color:var(--qh-graph-ink);font-family:var(--qh-font-body)}
.graph-view:before,.graph-view:after{position:absolute;z-index:-1;pointer-events:none;content:''}
.graph-view:before{inset:0;background:radial-gradient(ellipse at 49% 54%,var(--qh-graph-surface-clear),transparent 41%),radial-gradient(ellipse at 82% 12%,var(--qh-graph-node-halo),transparent 33%),radial-gradient(ellipse at 5% 85%,var(--qh-graph-node-halo),transparent 33%)}
.graph-view:after{top:30%;left:25%;width:50%;height:40%;border-radius:50%;background:var(--qh-graph-node-halo);filter:blur(82px)}
.graph-head{position:relative;z-index:2;display:grid;grid-template-columns:minmax(230px,1fr) auto;gap:20px 28px;padding:25px 30px 0}
.title-block .eyebrow{display:flex;align-items:center;gap:7px;margin:0 0 8px;color:var(--qh-graph-node-herb);font-size:11px;font-weight:800;letter-spacing:1.5px}.eyebrow-mark{font-size:15px}
.title-block h2{margin:0;color:var(--qh-graph-ink);font-family:var(--qh-font-display);font-size:25px;font-weight:800;letter-spacing:.015em}.title-block .subtitle{margin:6px 0 0;color:var(--qh-graph-muted);font-size:12px;letter-spacing:.025em}
.graph-counts{display:flex;align-items:center;gap:13px;align-self:center;padding:9px 14px;border:1px solid var(--qh-graph-border-light);border-radius:var(--qh-radius-pill);background:var(--qh-graph-surface-wash);color:var(--qh-graph-muted);font-size:11px}.graph-counts b{margin-right:3px;color:var(--qh-graph-ink);font-size:14px}.graph-counts i{width:1px;height:13px;background:var(--qh-graph-border)}
.filters{grid-column:1/-1;display:grid;gap:9px;padding:12px 14px;border:1px solid var(--qh-graph-border-light);border-radius:var(--qh-radius-card-sm);background:var(--qh-graph-surface-wash);backdrop-filter:blur(16px)}
.filter-row{display:flex;flex-wrap:wrap;align-items:center;gap:6px}.filter-label{width:35px;color:var(--qh-graph-muted);font-size:10px;font-weight:700;letter-spacing:.08em}
.filter-row button{display:inline-flex;align-items:center;gap:5px;min-height:27px;padding:4px 9px;border:1px solid transparent;border-radius:var(--qh-radius-pill);background:var(--qh-graph-surface-wash);color:var(--qh-graph-muted);font:inherit;font-size:10px;cursor:pointer;transition:background var(--qh-duration-fast) ease,border-color var(--qh-duration-fast) ease,color var(--qh-duration-fast) ease,opacity var(--qh-duration-fast) ease}
.filter-row button i{width:6px;height:6px;border:1.5px solid currentColor;border-radius:50%;background:transparent}.filter-row button.active{border-color:var(--qh-graph-border-hover);background:var(--qh-graph-surface-clear);color:var(--qh-graph-ink);box-shadow:0 2px 8px var(--qh-graph-shadow-soft)}.filter-row button.active i{background:currentColor;box-shadow:0 0 8px currentColor}.filter-row button:hover{border-color:var(--qh-graph-border)}
.relation-compatible{--relation-color:var(--qh-graph-edge-compatible)}.relation-restraint{--relation-color:var(--qh-graph-edge-restraint)}.relation-forbidden{--relation-color:var(--qh-graph-edge-forbidden)}.relation-benefit{--relation-color:var(--qh-graph-edge-benefit)}.relation-evidence{--relation-color:var(--qh-graph-edge-evidence)}.relation-composition{--relation-color:var(--qh-graph-edge-composition)}.relation-contrast{--relation-color:var(--qh-graph-edge-contrast)}.relation-cluster{--relation-color:var(--qh-graph-edge-cluster)}
.relation-filters button.active{color:var(--relation-color)}.entity-herb{--entity-color:var(--qh-graph-node-herb)}.entity-syndrome{--entity-color:var(--qh-graph-node-syndrome)}.entity-formula{--entity-color:var(--qh-graph-node-formula)}.entity-category{--entity-color:var(--qh-graph-node-category)}.entity-filters button.active{color:var(--entity-color)}
.graph-stage{position:relative;min-height:635px;margin-top:2px;overflow:hidden}.graph-canvas{position:absolute;inset:0;width:100%;height:100%;min-height:635px;outline:none}.graph-canvas :deep(canvas){display:block;width:100%;height:100%;outline:none}
.graph-hit-layer{position:absolute;z-index:1;inset:0;pointer-events:none}.node-hit-target{position:absolute;width:46px;height:46px;transform:translate(-50%,-50%);border:0;border-radius:50%;background:transparent;pointer-events:auto;cursor:pointer}.node-hit-target:focus-visible{outline:2px solid var(--qh-graph-node-herb);outline-offset:3px}
.stage-caption{position:absolute;z-index:1;bottom:19px;left:50%;display:flex;align-items:center;gap:8px;max-width:calc(100% - 170px);padding:8px 13px;transform:translateX(-50%);border:1px solid var(--qh-graph-border-mid);border-radius:var(--qh-radius-pill);background:var(--qh-graph-surface-glass);box-shadow:0 4px 15px var(--qh-graph-shadow-soft);color:var(--qh-graph-muted);font-size:11px;white-space:nowrap;backdrop-filter:blur(14px)}.live-indicator{width:6px;height:6px;flex:0 0 6px;border-radius:50%;background:var(--qh-graph-node-herb);box-shadow:0 0 0 4px var(--qh-graph-ink-halo)}
.camera-controls{position:absolute;z-index:2;right:21px;bottom:18px;display:flex;align-items:center;overflow:hidden;border:1px solid var(--qh-graph-border-mid);border-radius:var(--qh-radius-pill);background:var(--qh-graph-surface-soft);box-shadow:0 5px 17px var(--qh-graph-shadow-controls);backdrop-filter:blur(15px)}.camera-controls button{display:grid;width:36px;height:36px;place-items:center;border:0;background:transparent;color:var(--qh-graph-ink);font-family:var(--qh-font-body);font-size:17px;cursor:pointer;transition:background var(--qh-duration-fast) ease,color var(--qh-duration-fast) ease}.camera-controls button:hover,.camera-controls button.active{background:var(--qh-graph-ink-wash);color:var(--qh-graph-node-herb)}.camera-controls span{width:1px;height:18px;background:var(--qh-graph-border)}
.canvas-fallback{position:absolute;inset:15% 10%;z-index:2;display:grid;align-content:center;justify-items:center;gap:8px;border:1px solid var(--qh-graph-border);border-radius:var(--qh-radius-card);background:var(--qh-graph-surface-selection);color:var(--qh-graph-muted);text-align:center}.canvas-fallback p{margin:0;color:var(--qh-graph-ink);font-weight:800}.canvas-fallback div{display:flex;flex-wrap:wrap;justify-content:center;gap:7px;margin-top:12px}.canvas-fallback button{padding:7px 12px;border:1px solid var(--qh-graph-border);border-radius:var(--qh-radius-pill);background:var(--qh-graph-white);color:var(--qh-graph-ink);cursor:pointer}
.graph-footer{position:relative;z-index:1;display:flex;flex-wrap:wrap;align-items:center;gap:8px 18px;min-height:51px;padding:11px 24px;border-top:1px solid var(--qh-graph-line-soft);background:var(--qh-graph-surface-footer)}.graph-footer>p{margin:0;color:var(--qh-graph-ink);font-size:10px;font-weight:800}.legend-list{display:flex;flex-wrap:wrap;gap:7px 16px}.legend-list span{display:inline-flex;align-items:center;gap:6px;color:var(--qh-graph-muted);font-size:10px}.legend-list i{width:14px;border-top:1.5px solid var(--relation-color)}.legend-list .relation-restraint i,.legend-list .relation-forbidden i{border-top-style:dashed}.legend-list .relation-contrast i,.legend-list .relation-cluster i{border-top-style:dotted}.legend-note{margin-left:auto;color:var(--qh-graph-muted);font-size:9px}
.detail-panel,.edge-card{position:absolute;z-index:4;top:15px;right:16px;bottom:66px;width:min(325px,calc(100% - 32px));overflow:auto;padding:19px;border:1px solid var(--qh-graph-border-panel);border-radius:var(--qh-radius-card);background:var(--qh-graph-surface-panel);box-shadow:0 15px 48px var(--qh-graph-shadow-panel);backdrop-filter:blur(23px);scrollbar-color:var(--qh-graph-edge-compatible) transparent;scrollbar-width:thin}.detail-head{display:flex;align-items:center;gap:11px;padding-bottom:15px;border-bottom:1px solid var(--qh-graph-line-clear)}.detail-symbol{display:grid;width:42px;height:42px;flex:0 0 42px;place-items:center;border-radius:15px;background:var(--qh-graph-node-symbol);color:var(--entity-color);font-family:var(--qh-font-reading);font-size:20px;font-weight:700}.detail-title{min-width:0;flex:1}.detail-title>span,.card-kicker{color:var(--qh-graph-muted);font-size:10px}.detail-title h3{margin:2px 0 0;color:var(--qh-graph-ink);font-size:24px;font-weight:800}.card-close{display:grid;width:29px;height:29px;flex:0 0 29px;place-items:center;border:0;border-radius:50%;background:var(--qh-graph-ink-wash);color:var(--qh-graph-muted);font-size:20px;line-height:1;cursor:pointer}.card-close:hover{background:var(--qh-graph-ink-hover);color:var(--qh-graph-ink)}
.node-brief{margin:13px 0;color:var(--qh-graph-ink);font-size:12px;line-height:1.8}.detail-section{padding:10px 0;border-top:1px solid var(--qh-graph-line-soft)}.detail-section h4,.connections h4{margin:0;color:var(--qh-graph-node-herb);font-size:10px;font-weight:800;letter-spacing:.07em}.detail-section p{margin:5px 0 0;color:var(--qh-graph-muted);font-size:11px;line-height:1.8}.frequency{display:block;margin-top:5px;color:var(--qh-graph-edge-benefit);font-size:10px}.detail-loading{color:var(--qh-graph-muted);font-size:11px}.connections{padding-top:12px;border-top:1px solid var(--qh-graph-line-soft)}.connections h4{display:flex;align-items:center;justify-content:space-between;margin-bottom:6px}.connections h4 span,.source-details summary span{display:grid;min-width:19px;height:19px;place-items:center;border-radius:50%;background:var(--qh-graph-ink-wash);color:var(--qh-graph-node-herb);font-size:9px}
.connection-row{display:flex;width:100%;align-items:center;gap:9px;padding:8px 2px;border:0;border-bottom:1px solid var(--qh-graph-line-faint);background:transparent;text-align:left;cursor:pointer}.connection-row>i{width:7px;height:7px;flex:0 0 7px;border-radius:50%;background:var(--relation-color)}.connection-copy{display:grid;min-width:0;flex:1;gap:2px}.connection-copy b{color:var(--qh-graph-ink);font-size:11px}.connection-copy small{overflow:hidden;color:var(--qh-graph-muted);font-size:9px;text-overflow:ellipsis;white-space:nowrap}.connection-arrow{color:var(--qh-graph-muted);font-size:18px}.connection-row:hover .connection-copy b{color:var(--qh-graph-node-herb)}
.source-details{margin-top:12px;border-top:1px solid var(--qh-graph-line-soft);padding-top:10px}.source-details summary{display:flex;align-items:center;justify-content:space-between;color:var(--qh-graph-node-herb);font-size:10px;font-weight:700;cursor:pointer;list-style:none}.source-details summary::-webkit-details-marker{display:none}.source-details blockquote,.edge-card blockquote{margin:10px 0 0;padding-left:11px;border-left:2px solid var(--qh-graph-edge-evidence);color:var(--qh-graph-muted);font-size:10px;line-height:1.7}.source-details blockquote b,.edge-card blockquote b{display:block;color:var(--qh-graph-ink)}.source-details blockquote p,.edge-card blockquote p{margin:4px 0}.source-details blockquote small,.edge-card blockquote small{color:var(--qh-graph-node-herb)}
.expand-button{display:flex;width:100%;align-items:center;justify-content:space-between;margin-top:14px;padding:11px 13px;border:1px solid var(--qh-graph-border-active);border-radius:var(--qh-radius-card-sm);background:var(--qh-graph-ink-mid);color:var(--qh-graph-ink);font:inherit;font-size:10px;font-weight:700;cursor:pointer}.expand-button:hover{background:var(--qh-graph-ink-hover)}.expand-button span{font-size:15px}
.edge-card{top:auto;right:auto;bottom:74px;left:18px;width:min(300px,calc(100% - 36px));padding:16px}.edge-card h3{margin:5px 30px 6px 0;color:var(--qh-graph-ink);font-size:17px}.edge-relation-tag{display:inline-flex;padding:4px 8px;border-radius:var(--qh-radius-pill);background:var(--qh-graph-ink-wash);color:var(--qh-graph-node-herb);font-size:10px}.edge-card .card-close{position:absolute;top:12px;right:12px}
.graph-state{min-height:635px}.detail-enter-active,.detail-leave-active{transition:transform var(--qh-duration-md) ease,opacity var(--qh-duration-md) ease}.detail-enter-from,.detail-leave-to{transform:translateX(20px);opacity:0}.source-enter-active,.source-leave-active{transition:opacity var(--qh-duration-fast) ease}.source-enter-from,.source-leave-to{opacity:0}
@media(max-width:850px){.graph-head{gap:14px;padding:20px 20px 0}.graph-stage,.graph-canvas,.graph-state{min-height:600px}.title-block h2{font-size:22px}.title-block .subtitle{max-width:390px;line-height:1.6}.filters{gap:7px;padding:10px}.filter-row{gap:4px}.filter-row button{padding:4px 7px;font-size:9px}.legend-note{width:100%;margin-left:53px}.detail-panel{right:10px;bottom:60px;width:min(310px,calc(100% - 20px))}.stage-caption{bottom:70px;left:12px;max-width:calc(100% - 142px);transform:none;font-size:9px}.camera-controls{right:12px;bottom:14px}}
@media(max-width:560px){.graph-view{border-radius:var(--qh-radius-card-sm)}.graph-head{grid-template-columns:1fr auto;padding:17px 14px 0}.graph-counts{gap:7px;padding:7px 9px;font-size:9px}.graph-counts b{font-size:12px}.title-block .eyebrow{margin-bottom:5px;font-size:9px}.title-block h2{font-size:19px}.title-block .subtitle{grid-column:1/-1;max-width:300px;font-size:10px}.filters{grid-column:1/-1;margin-inline:-3px}.filter-label{width:29px;font-size:9px}.filter-row button{gap:4px;min-height:25px;padding:3px 6px;font-size:8px}.filter-row button i{width:5px;height:5px}.graph-stage,.graph-canvas,.graph-state{min-height:530px}.graph-footer{gap:8px;padding:10px 13px}.legend-list{gap:7px 11px}.legend-list span{font-size:9px}.legend-note{margin-left:0;font-size:8px}.camera-controls button{width:32px;height:33px}.stage-caption{bottom:64px;left:10px;max-width:calc(100% - 129px);padding:7px 9px;font-size:8px}.detail-panel{top:10px;right:8px;bottom:54px;width:min(300px,calc(100% - 16px));padding:15px}.detail-title h3{font-size:21px}.edge-card{bottom:60px;left:10px}.canvas-fallback{inset:16% 4%}}
@media(prefers-reduced-motion:reduce){.detail-enter-active,.detail-leave-active,.source-enter-active,.source-leave-active{transition:none}.filter-row button,.camera-controls button{transition:none}}
</style>
