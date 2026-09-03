<template>
  <main class="meridian-map-view" :style="stageVars" v-loading="path.loading">
    <header class="map-header">
      <div class="title-group">
        <p class="eyebrow">MERIDIAN LEARNING MAP</p>
        <h1>学习脉络图</h1>
        <p class="map-intro">沿着可见的主干，逐点打通你的中药学习经脉。</p>
      </div>
      <div class="header-actions">
        <el-button :icon="Plus" @click="customVisible = true">添加自定义节点</el-button>
        <el-button v-if="path.usingMock" text>示例路线</el-button>
      </div>
    </header>

    <el-alert v-if="path.error" :title="path.fallback ? '先按标准路线走，随时可以重新规划' : path.error" type="warning" :closable="false" show-icon />

    <section v-if="path.hasPlan" ref="stageRef" class="map-stage" aria-label="学习脉络图">
      <div class="stellar-bloom bloom-jade" aria-hidden="true" />
      <div class="stellar-bloom bloom-blue" aria-hidden="true" />
      <div class="stellar-bloom bloom-gold" aria-hidden="true" />
      <div class="rise-particles" aria-hidden="true">
        <i v-for="([left, bottom, opacity, delay], index) in meridianVisual.particles" :key="index" :style="particleStyle(left, bottom, opacity, delay)" />
      </div>
      <svg class="paper-texture" aria-hidden="true" preserveAspectRatio="none"><filter id="meridian-paper"><feTurbulence type="fractalNoise" baseFrequency=".68" numOctaves="3" /></filter><rect width="100%" height="100%" filter="url(#meridian-paper)" /></svg>

      <svg ref="mapRef" class="meridian-map" :viewBox="layout.viewBox" role="img" aria-label="中药学习脉络图">
        <defs>
          <filter id="map-soft"><feGaussianBlur stdDeviation="2.2" /></filter>
          <filter id="flow-glow"><feGaussianBlur stdDeviation="2.4" result="blur" /><feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
          <linearGradient id="map-jade-blue" x1="0" y1="1" x2=".72" y2="0"><stop stop-color="var(--qh-color-jade-d)"/><stop offset=".58" stop-color="var(--qh-color-jade)"/><stop offset="1" stop-color="var(--qh-color-blue)"/></linearGradient>
          <linearGradient id="map-flow" x1="0" y1="1" x2="0" y2="0"><stop stop-color="var(--qh-color-cream)" stop-opacity="0"/><stop offset=".36" stop-color="var(--qh-color-cream)"/><stop offset=".72" stop-color="var(--qh-color-gold)"/><stop offset="1" stop-color="var(--qh-color-gold)" stop-opacity="0"/></linearGradient>
        </defs>
        <g v-for="direction in layout.directions" :key="direction.id" :class="{ 'is-foggy': isFoggy(direction.id), 'is-active': isActivated(direction.id) }">
          <path class="trunk-aura" :d="direction.path" />
          <path class="trunk-shadow" :d="direction.path" />
          <path class="trunk" :id="`trunk-${direction.id}`" :d="direction.path" />
          <path class="trunk-flow" :d="direction.path" />
          <path class="trunk-branch" :d="branchPath(direction.x, direction.y)" />
          <text class="direction-title" :x="direction.x - 56" y="38">{{ direction.label }}</text>
          <text class="segment-label" :x="direction.x - 38" y="610">{{ direction.segment }}</text>
        </g>
        <MeridianNode
          v-for="node in path.nodes"
          :key="node.id"
          :node="node"
          :radius="meridianVisual.layout.nodeRadius"
          :visual-state="visualStateFor(node)"
          :class="{ 'is-new': newNodeId === node.id }"
          @select="(node, event) => openNode(node as MapNode, event)"
          @defer="node => openDefer(node as MapNode)"
          @dragstart="node => startDrag(node as MapNode)"
          @dragover="() => undefined"
          @drop="node => dropNode(node as MapNode)"
        />
        <g ref="flowRef" class="flow-orb" aria-hidden="true">
          <circle class="flow-tail" r="12" /><circle class="flow-core" r="4" filter="url(#flow-glow)" />
        </g>
      </svg>

      <aside class="progress-card" aria-label="学习进度">
        <p class="card-kicker">行进进度</p>
        <strong>{{ masteredCount }}<small>/ {{ path.nodes.length }}</small></strong>
        <p>已点亮节点</p>
        <div class="progress-track" aria-hidden="true"><i :style="{ width: `${progressPercent}%` }" /></div>
        <span>{{ currentNode?.label || '等待下一处星穴' }}</span>
      </aside>
      <aside class="legend" aria-label="节点状态图例">
        <p>星穴状态</p>
        <span v-for="item in legend" :key="item.key"><i :class="`is-${item.key}`" />{{ item.label }}</span>
      </aside>
    </section>

    <section v-else-if="!path.loading" class="empty-plan">
      <div class="empty-seal">岐黄</div><h2>先把你的学习主干定下来</h2><p>小岐会按基础依赖、章节顺序和当前目标排好起步路线。</p><el-button type="primary" :loading="path.planning" @click="openPlan">生成我的路线</el-button>
    </section>

    <el-dialog v-model="planVisible" title="小岐的路线建议" width="460px" :close-on-click-modal="false"><p class="dialog-copy">先从总论建立共同语言，再进入可自由调整的章节组。每一步都有前置依据，也保留复习节奏。</p><ol class="plan-order"><li v-for="direction in layout.directions" :key="direction.id">{{ direction.label }}</li></ol><template #footer><el-button @click="planVisible = false">稍后再说</el-button><el-button type="primary" :loading="path.planning" @click="confirmPlan">确认这个顺序</el-button></template></el-dialog>
    <ImmersiveLayer />
    <el-dialog v-model="customVisible" title="添加自定义节点" width="420px"><el-input v-model="customText" maxlength="500" show-word-limit placeholder="例如：比较麻黄与桂枝的发汗差异"/><template #footer><el-button @click="customVisible = false">取消</el-button><el-button type="primary" :disabled="!customText.trim()" @click="addCustom">加入路线</el-button></template></el-dialog>
    <el-dialog v-model="deferVisible" title="暂缓这个节点" width="420px"><el-radio-group v-model="deferReason" class="reason-list"><el-radio label="太难">太难</el-radio><el-radio label="暂时不感兴趣">暂时不感兴趣</el-radio><el-radio label="以后再学">以后再学</el-radio><el-radio label="其他">其他</el-radio></el-radio-group><el-input v-if="deferReason === '其他'" v-model="deferText" placeholder="说明原因"/><template #footer><el-button @click="deferVisible = false">取消</el-button><el-button type="primary" @click="confirmDefer">暂缓</el-button></template></el-dialog>
  </main>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { gsap } from 'gsap'
import { MotionPathPlugin } from 'gsap/MotionPathPlugin'
import layout from '../config/meridian-layout.json'
import { assessmentVisual } from '../config/assessment'
import { meridianVisual } from '../config/stage4Visual'
import { usePathStore } from '../stores/path'
import { useImmersiveStore } from '../stores/immersive'
import ImmersiveLayer from '../components/immersive/ImmersiveLayer.vue'
import MeridianNode from '../components/meridian/MeridianNode.vue'
import type { MapNode } from '../types/path'

gsap.registerPlugin(MotionPathPlugin)

type VisualState = keyof typeof meridianVisual.states
type Direction = typeof layout.directions[number]
const route = useRoute()
const path = usePathStore()
const immersive = useImmersiveStore()
const stageRef = ref<HTMLElement>()
const mapRef = ref<SVGSVGElement>()
const flowRef = ref<SVGGElement>()
const selected = ref<MapNode>()
const planVisible = ref(false)
const customVisible = ref(false)
const deferVisible = ref(false)
const customText = ref('')
const deferReason = ref('太难')
const deferText = ref('')
const newNodeId = ref('')
const draggedId = ref('')
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)')
let gsapContext: gsap.Context | undefined
let flowQueue = Promise.resolve()

const stageVars = computed(() => ({
  '--meridian-stage-height': `${meridianVisual.layout.stageHeight}px`,
  '--meridian-trunk-width': `${meridianVisual.layout.trunkWidth}px`,
  '--meridian-branch-width': `${meridianVisual.layout.branchWidth}px`,
  '--meridian-pulse-seconds': `${meridianVisual.motion.currentPulseSeconds}s`,
  '--meridian-flow-sweep-seconds': `${meridianVisual.motion.flowSweepSeconds}s`,
  '--meridian-node-hover-seconds': `${meridianVisual.motion.nodeHoverSeconds}s`,
}))
const legend = computed(() => (Object.entries(meridianVisual.states) as [VisualState, typeof meridianVisual.states[VisualState]][]).map(([key, state]) => ({ key, label: state.label })))
const masteredCount = computed(() => path.nodes.filter((node) => node.status === 'mastered').length)
const progressPercent = computed(() => path.nodes.length ? Math.round((masteredCount.value / path.nodes.length) * 100) : 0)
const currentNode = computed(() => path.nodes.find((node) => node.status === 'current'))

const visualStateFor = (node: MapNode): VisualState => {
  if (node.state === 'locked') return 'locked'
  return meridianVisual.statusMap[node.status] as VisualState
}
const isActivated = (domainId: string) => path.nodes.some((node) => node.domainId === domainId && ['current', 'mastered'].includes(node.status))
const isFoggy = (domainId: string) => !path.expandedDomains.includes(domainId) && domainId !== 'general' && !isActivated(domainId)
const branchPath = (x: number, y: number) => `M ${x} ${y} C ${x + 22} ${y - 8}, ${x + 38} ${y - 22}, ${x + 55} ${y - 17}`
const particleStyle = (left: number, bottom: number, opacity: number, delay: number) => ({ left: `${left}%`, bottom: `${bottom}%`, opacity: String(opacity), animationDelay: `${delay}s` })

onMounted(async () => {
  await path.load()
  await nextTick()
  if (!reducedMotion.matches) {
    gsapContext = gsap.context(() => {
      const nodes = stageRef.value?.querySelectorAll<SVGGElement>('.meridian-node')
      if (stageRef.value) gsap.from(stageRef.value, { autoAlpha: 0, y: 10, duration: meridianVisual.motion.unlockSeconds, ease: 'power2.out' })
      if (nodes?.length) gsap.from(nodes, { autoAlpha: 0, scale: 0.88, duration: meridianVisual.motion.unlockSeconds, stagger: 0.045, ease: 'power2.out', transformOrigin: '50% 50%' })
    }, stageRef.value)
  }
  const passed = route.query[assessmentVisual.passReturnQuery]
  if (typeof passed === 'string') void celebratePassed(passed)
})
onBeforeUnmount(() => gsapContext?.revert())

const celebratePassed = async (nodeId: string) => {
  await nextTick()
  const target = stageRef.value?.querySelector<SVGGElement>(`[data-node-id="${nodeId}"]`)
  if (!target || reducedMotion.matches) return
  gsap.fromTo(target, { scale: 0.58, opacity: 0.45 }, { scale: 1, opacity: 1, duration: meridianVisual.motion.unlockSeconds, ease: 'back.out(1.55)' })
  const next = path.nodes.find((node) => node.status === 'current')
  if (next) await queueQiFlow(next.domainId, target, stageRef.value?.querySelector(`[data-node-id="${next.id}"]`) as SVGGElement | undefined)
}

const queueQiFlow = (domainId: string, source?: SVGGElement, target?: SVGGElement) => {
  flowQueue = flowQueue.then(async () => {
    const flowPath = mapRef.value?.querySelector<SVGPathElement>(`#trunk-${domainId}`)
    const flowOrb = flowRef.value
    if (!flowOrb || !flowPath || reducedMotion.matches) return
    await new Promise<void>((resolve) => {
      const timeline = gsap.timeline({ onComplete: resolve })
      if (source) timeline.to(source, { scale: 1.08, duration: 0.16, yoyo: true, repeat: 1, ease: 'power1.out', transformOrigin: '50% 50%' })
      timeline.set(flowOrb, { opacity: 1, x: 0, y: 0 })
      timeline.to(flowOrb, { duration: meridianVisual.motion.qiFlowSeconds, motionPath: { path: flowPath, align: flowPath, alignOrigin: [0.5, 0.5] }, ease: meridianVisual.motion.qiFlowEase })
      timeline.set(flowOrb, { opacity: 0 })
      if (target) timeline.fromTo(target, { scale: 0.72 }, { scale: 1, duration: 0.3, ease: 'back.out(1.45)', transformOrigin: '50% 50%' }, '-=0.18')
    })
  })
  return flowQueue
}

const openPlan = () => { planVisible.value = true }
const confirmPlan = async () => { await path.plan(); planVisible.value = false }
const openNode = async (node: MapNode, event: MouseEvent | KeyboardEvent) => {
  const element = event.currentTarget as SVGElement | null
  const rect = element?.getBoundingClientRect()
  const origin = rect ? { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 } : { x: window.innerWidth / 2, y: window.innerHeight / 2 }
  if (!reducedMotion.matches && stageRef.value) {
    const stageRect = stageRef.value.getBoundingClientRect()
    await gsap.to(stageRef.value, { scale: 1.035, x: (stageRect.width / 2 - (origin.x - stageRect.left)) * 0.035, y: (stageRect.height / 2 - (origin.y - stageRect.top)) * 0.035, duration: meridianVisual.motion.transitionSeconds, ease: meridianVisual.motion.transitionEase }).then()
  }
  await immersive.begin({ id: node.id, label: node.label, detail: node.detail, stage: layout.directions.findIndex((item) => item.id === node.domainId) + 1 }, origin)
  if (stageRef.value) gsap.set(stageRef.value, { clearProps: 'transform' })
  if (!node.expanded) await path.expand(node.domainId)
}
const openDefer = (node: MapNode) => { selected.value = node; deferVisible.value = true }
const startDrag = (node: MapNode) => { if (node.free) draggedId.value = node.id }
const dropNode = async (target: MapNode) => {
  if (!draggedId.value || draggedId.value === target.id || !target.free) return
  const ids = path.nodes.filter((node) => node.free).map((node) => node.id)
  const from = ids.indexOf(draggedId.value)
  const to = ids.indexOf(target.id)
  ids.splice(from, 1); ids.splice(to, 0, draggedId.value)
  try { await path.reorder(ids); ElMessage.success('自由组顺序已更新') } catch (error) { ElMessage.warning(error instanceof Error ? error.message : '此顺序不满足前置关系') } finally { draggedId.value = '' }
}
const confirmDefer = async () => {
  if (!selected.value) return
  await path.defer(selected.value.id, deferReason.value, deferText.value)
  deferVisible.value = false
  ElMessage.success('已暂缓，路线会保留这个节点。')
}
// Kept as the completion bridge for the existing immersive/assessment flow.
// The visual work is queued so rapid state changes never overlap their qi flow.
const completeSelected = async () => {
  if (!selected.value) return
  const sourceId = selected.value.id
  const result = await path.complete(selected.value.id)
  await nextTick()
  const next = result.next_node
  const source = stageRef.value?.querySelector<SVGGElement>(`[data-node-id="${sourceId}"]`)
  const target = next ? stageRef.value?.querySelector<SVGGElement>(`[data-node-id="${next}"]`) : undefined
  const domainId = next ? path.nodes.find((node) => node.id === next)?.domainId : selected.value.domainId
  if (domainId) void queueQiFlow(domainId, source ?? undefined, target ?? undefined)
  if (result.diff.inserted_nodes.length) ElMessage.info(`小岐补入一关：${result.diff.reason}`)
}
const addCustom = async () => {
  const result = await path.addCustom(customText.value.trim())
  if (!result.accepted) { ElMessage.warning(result.suggestion || '暂时无法加入路线'); return }
  customVisible.value = false; customText.value = ''; newNodeId.value = result.node?.node_id || ''
  await nextTick()
  const target = stageRef.value?.querySelector<SVGGElement>(`[data-node-id="${newNodeId.value}"]`)
  if (target && !reducedMotion.matches) gsap.fromTo(target, { scale: 0.35, opacity: 0 }, { scale: 1, opacity: 1, duration: meridianVisual.motion.unlockSeconds, ease: 'back.out(1.55)' })
  ElMessage.success('已加入路线')
}
</script>

<style scoped lang="scss">
@use "../styles/tokens.scss" as *;

.meridian-map-view { color: var(--qh-text); }
.map-header { display: flex; justify-content: space-between; align-items: end; gap: var(--qh-space-6); margin: var(--qh-space-1) var(--qh-space-1) var(--qh-space-5); }
.title-group { min-width: 0; }
.eyebrow { margin: 0 0 7px; color: var(--qh-color-jade); font-size: 10px; font-weight: 800; letter-spacing: 1.7px; }
.map-header h1 { margin: 0; color: var(--qh-text); font-family: var(--qh-font-display); font-size: 30px; letter-spacing: 0; }
.map-intro { margin: 7px 0 0; color: var(--qh-text-muted); font-size: 13px; }
.header-actions { display: flex; gap: var(--qh-space-2); }

.map-stage { position: relative; min-height: var(--meridian-stage-height); overflow: hidden; border: 1px solid var(--qh-border); border-radius: var(--qh-radius-stage); background: linear-gradient(142deg, var(--qh-color-night-soft), var(--qh-color-night) 58%, var(--qh-color-ink)); box-shadow: var(--qh-inner-highlight), var(--qh-shadow-deep); isolation: isolate; }
.stellar-bloom { position: absolute; z-index: -1; width: 46%; aspect-ratio: 1; border-radius: 50%; filter: blur(18px); opacity: 0.28; pointer-events: none; }
.bloom-jade { top: -27%; left: -13%; background: color-mix(in srgb, var(--qh-color-jade) 42%, transparent); }
.bloom-blue { top: 7%; right: -21%; background: color-mix(in srgb, var(--qh-color-blue) 32%, transparent); }
.bloom-gold { right: 3%; bottom: -36%; background: color-mix(in srgb, var(--qh-color-gold) 23%, transparent); }
.paper-texture { position: absolute; inset: 0; width: 100%; height: 100%; opacity: 0.04; pointer-events: none; }
.rise-particles { position: absolute; inset: 0; overflow: hidden; pointer-events: none; }
.rise-particles i { position: absolute; width: 2px; height: 2px; border-radius: 50%; background: var(--qh-color-cream); animation: meridian-rise 8.8s ease-in infinite; }
.meridian-map { position: relative; display: block; width: 100%; height: var(--meridian-stage-height); overflow: visible; }
.trunk-aura, .trunk-shadow, .trunk, .trunk-flow, .trunk-branch { fill: none; stroke-linecap: round; }
.trunk-aura { stroke: var(--qh-color-jade); stroke-width: calc(var(--meridian-trunk-width) + 12px); opacity: 0.12; filter: url(#map-soft); }
.trunk-shadow { stroke: var(--qh-color-ink); stroke-width: calc(var(--meridian-trunk-width) + 4px); opacity: 0.72; filter: url(#map-soft); }
.trunk { stroke: url(#map-jade-blue); stroke-width: var(--meridian-trunk-width); }
.trunk-flow { stroke: url(#map-flow); stroke-width: calc(var(--meridian-trunk-width) - 2px); stroke-dasharray: 24 440; stroke-dashoffset: 0; opacity: 0; }
.is-active .trunk-flow { opacity: 0.48; animation: trunk-sweep var(--meridian-flow-sweep-seconds) linear infinite; }
.trunk-branch { stroke: var(--qh-color-jade); stroke-width: var(--meridian-branch-width); opacity: 0.46; }
.is-foggy .trunk, .is-foggy .trunk-branch { stroke-dasharray: 3 9; opacity: 0.34; }
.is-foggy .trunk-aura, .is-foggy .trunk-shadow, .is-foggy .trunk-flow { opacity: 0.08; }
.direction-title { fill: var(--qh-color-cream); font-family: var(--qh-font-display); font-size: 17px; font-weight: 800; }
.segment-label { fill: var(--qh-color-jade); font-size: 11px; font-weight: 700; letter-spacing: 1.3px; }
.flow-orb { opacity: 0; pointer-events: none; }
.flow-tail { fill: var(--qh-color-gold); opacity: 0.18; filter: blur(4px); }
.flow-core { fill: var(--qh-color-cream); }

.progress-card, .legend { position: absolute; border: 1px solid color-mix(in srgb, var(--qh-color-jade) 22%, transparent); background: color-mix(in srgb, var(--qh-color-night-soft) 75%, transparent); box-shadow: var(--qh-inner-highlight), var(--qh-shadow-soft); backdrop-filter: blur(16px); }
.progress-card { top: var(--qh-space-4); left: var(--qh-space-4); display: grid; width: 150px; gap: 3px; padding: var(--qh-space-3) var(--qh-space-4); border-radius: var(--qh-radius-stage); }
.card-kicker, .legend > p { margin: 0; color: var(--qh-text-muted); font-size: 10px; font-weight: 800; letter-spacing: 1.2px; }
.progress-card strong { color: var(--qh-color-cream); font-family: var(--qh-font-display); font-size: 27px; line-height: 1; }
.progress-card strong small { color: var(--qh-text-muted); font-family: var(--qh-font-body); font-size: 12px; font-weight: 500; }
.progress-card > p:not(.card-kicker) { margin: 0; color: var(--qh-text-muted); font-size: 11px; }
.progress-card > span { overflow: hidden; color: var(--qh-color-jade); font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.progress-track { height: 3px; overflow: hidden; border-radius: var(--qh-radius-pill); background: color-mix(in srgb, var(--qh-text) 14%, transparent); }
.progress-track i { display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg, var(--qh-color-jade), var(--qh-color-gold)); transition: width 260ms ease-out; }
.legend { right: var(--qh-space-4); bottom: calc(var(--qh-space-4) + 70px); display: grid; grid-template-columns: repeat(2, auto); gap: 8px 16px; padding: var(--qh-space-3) var(--qh-space-4); border-radius: var(--qh-radius-stage); font-size: 11px; }
.legend > p { grid-column: 1 / -1; margin-bottom: 1px; }
.legend span { display: flex; align-items: center; gap: 7px; color: var(--qh-text); white-space: nowrap; }
.legend i { width: 11px; height: 11px; border: 2px solid var(--qh-color-jade); border-radius: 50%; }
.legend .is-locked { border-color: var(--qh-color-jade-d); opacity: 0.5; }.legend .is-available { box-shadow: 0 0 7px color-mix(in srgb, var(--qh-color-jade) 35%, transparent); }.legend .is-current { box-shadow: 0 0 10px var(--qh-color-jade); }.legend .is-lit { border-color: var(--qh-color-gold); background: var(--qh-color-gold); }.legend .is-deferred { border-color: var(--qh-color-mist); border-style: dashed; opacity: 0.58; }.legend .is-priority { border-color: var(--qh-color-gold); box-shadow: 0 0 0 2px color-mix(in srgb, var(--qh-color-gold) 35%, transparent); }

.empty-plan { display: grid; min-height: 520px; place-items: center; align-content: center; border: 1px solid var(--qh-border); border-radius: var(--qh-radius-stage); background: var(--qh-card-fill); text-align: center; }
.empty-seal { display: grid; width: 48px; height: 48px; place-items: center; border: 2px solid var(--qh-color-coral); color: var(--qh-color-coral); font-family: serif; font-weight: 800; transform: rotate(-4deg); }.empty-plan h2 { margin: 18px 0 8px; font-family: var(--qh-font-display); font-size: 22px; }.empty-plan p, .dialog-copy { color: var(--qh-text-muted); font-size: 13px; }.plan-order { margin: 20px 0; padding-left: 22px; color: var(--qh-text); line-height: 2; }.reason-list { display: grid; gap: 10px; margin-bottom: 16px; }

@keyframes trunk-sweep { to { stroke-dashoffset: -464; } }
@keyframes meridian-rise { 0% { transform: translate3d(0, 20px, 0); opacity: 0; } 18% { opacity: inherit; } 100% { transform: translate3d(14px, -94px, 0); opacity: 0; } }
@media (prefers-reduced-motion: reduce) { .rise-particles { display: none; }.trunk-flow { animation: none; }.is-active .trunk-flow { opacity: 0.2; }.progress-track i { transition: none; } }
@media (max-width: 760px) { .map-header { align-items: flex-start; flex-direction: column; }.map-stage { overflow-x: auto; }.meridian-map { width: 1000px; }.progress-card { left: var(--qh-space-3); }.legend { right: auto; left: var(--qh-space-3); }.header-actions { width: 100%; } }
</style>
