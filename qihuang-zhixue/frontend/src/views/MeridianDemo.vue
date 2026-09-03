<template>
  <main class="meridian-demo" :class="`theme-${activeVariant}`" :style="cssVariables">
    <header class="demo-header">
      <div class="title-lockup">
        <div class="seal" aria-label="岐黄印"><span>岐</span><span>黄</span></div>
        <div>
          <p class="eyebrow">MERIDIAN LEARNING MAP · VISUAL STUDY</p>
          <h1>经络式学习脉络</h1>
          <p class="intro">让知识的先后关系，像气血一样看得见。</p>
        </div>
      </div>
      <div class="variant-switch" aria-label="样张方向">
        <el-radio-group v-model="activeVariant" size="small">
          <el-radio-button label="ink">A 淡雅水墨</el-radio-button>
          <el-radio-button label="dark">B 暗色气韵</el-radio-button>
        </el-radio-group>
        <small>{{ activeVariant === 'ink' ? '药典批注页：留白、墨线、朱砂为锚。' : '夜行经脉图：气场、游光、纵深为锚。' }}</small>
      </div>
    </header>

    <section ref="stageRef" class="demo-stage" aria-label="经络式学习脉络图">
      <svg class="paper-texture" aria-hidden="true" preserveAspectRatio="none"><filter id="paper"><feTurbulence type="fractalNoise" baseFrequency="0.68" numOctaves="3" stitchTiles="stitch" /><feColorMatrix type="saturate" values="0" /></filter><rect width="100%" height="100%" filter="url(#paper)" /></svg>
      <svg class="background-ink" viewBox="0 0 1160 580" aria-hidden="true"><path d="M-20 450 C160 365 228 432 390 382 S666 434 812 343 S1032 388 1190 292" /><path d="M-30 498 C175 425 303 482 477 426 S814 487 1170 376" /><path d="M86 65 C222 128 332 82 466 124 S757 88 924 150 S1062 132 1190 166" /></svg>
      <svg class="meridian-map" viewBox="0 0 1160 580" role="img" aria-label="中药与方剂两条学习经络主干和八个节点">
        <defs>
          <filter id="ink-soft"><feGaussianBlur stdDeviation="2.2" /></filter>
          <linearGradient id="trunk-jade" x1="0" y1="1" x2=".66" y2="0"><stop stop-color="var(--meridian-jade-d)" /><stop offset=".54" stop-color="var(--meridian-jade)" /><stop offset="1" stop-color="var(--meridian-jade-d)" /></linearGradient>
          <linearGradient id="mastered-flow" x1="0" y1="1" x2=".25" y2="0"><stop stop-color="var(--meridian-jade)" /><stop offset="1" stop-color="var(--meridian-gold)" /></linearGradient>
          <filter id="orb-glow"><feGaussianBlur stdDeviation="2.6" result="blur" /><feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
        </defs>

        <path class="trunk-aura" :style="{ strokeWidth: `${config.trunkWidth + config.auraWidth}px` }" d="M 284 530 C 255 437, 340 386, 326 300 C 313 221, 370 159, 352 58" />
        <path class="trunk-aura" :style="{ strokeWidth: `${config.trunkWidth + config.auraWidth}px` }" d="M 778 536 C 813 442, 740 381, 770 293 C 800 204, 739 145, 776 50" />
        <path class="meridian-ink trunk-draw" :style="{ strokeWidth: `${config.trunkWidth + 4}px` }" d="M 284 530 C 255 437, 340 386, 326 300 C 313 221, 370 159, 352 58" />
        <path class="meridian-ink trunk-draw" :style="{ strokeWidth: `${config.trunkWidth + 4}px` }" d="M 778 536 C 813 442, 740 381, 770 293 C 800 204, 739 145, 776 50" />
        <path class="meridian-trunk trunk-draw" :style="{ strokeWidth: `${config.trunkWidth}px` }" d="M 284 530 C 255 437, 340 386, 326 300 C 313 221, 370 159, 352 58" />
        <path class="meridian-trunk trunk-draw" :style="{ strokeWidth: `${config.trunkWidth}px` }" d="M 778 536 C 813 442, 740 381, 770 293 C 800 204, 739 145, 776 50" />
        <path id="mastered-left" class="mastered-segment trunk-draw" :style="{ strokeWidth: `${config.trunkWidth + 1}px` }" d="M 284 530 C 255 437, 340 386, 326 300" />
        <path id="mastered-right" class="mastered-segment trunk-draw" :style="{ strokeWidth: `${config.trunkWidth + 1}px` }" d="M 778 536 C 813 442, 740 381, 770 293" />
        <path class="meridian-branch trunk-draw" :style="{ strokeWidth: `${Math.max(1, config.trunkWidth - 1)}px` }" d="M 326 300 C 411 285, 463 237, 497 181" />
        <path class="meridian-branch trunk-draw" :style="{ strokeWidth: `${Math.max(1, config.trunkWidth - 1)}px` }" d="M 770 293 C 696 287, 641 242, 602 185" />

        <g v-for="orb in flowOrbs" :key="orb.id" class="flow-orb" :data-path="orb.path">
          <circle class="orb-tail" r="9" /><circle class="orb-core" r="3.4" filter="url(#orb-glow)" />
        </g>

        <text class="direction-index" x="218" y="92">I</text><text class="direction-title" x="242" y="90">中药学主干</text><text class="direction-subtitle" x="242" y="110">性味归经 · 功效群</text>
        <text class="direction-index" x="707" y="84">II</text><text class="direction-title" x="734" y="82">方剂学主干</text><text class="direction-subtitle" x="734" y="102">治法组方 · 证候配伍</text>
        <text class="segment-label" x="178" y="452">药性基础</text><text class="segment-label" x="360" y="244">功效辨析</text><text class="segment-label" x="820" y="453">组方思维</text><text class="segment-label" x="690" y="244">证候应用</text>
        <text class="ink-note" x="102" y="285">先辨气味，后察升降</text><text class="ink-note" x="406" y="335">性味相合，则功效可循</text><text class="ink-note" x="854" y="200">方以法立，法随证变</text><text class="ink-note" x="622" y="506">配伍之要，在于君臣</text>
        <MeridianNode v-for="node in displayPoints" :key="node.id" :node="node" :radius="config.nodeRadius" @select="node => bloomNode(node.id)" />
        <path ref="bloomRef" class="unlock-bloom" d="M 0 0" />
        <path class="unlock-bloom-target" d="M -18 0 C -8 -5 -5 -8 0 -18 C 5 -8 8 -5 18 0 C 8 5 5 8 0 18 C -5 8 -8 5 -18 0 Z" />
      </svg>
      <aside class="legend-panel" aria-label="节点状态图例"><p>穴位状态</p><div v-for="(style, key) in meridianStatusStyles" :key="key" class="legend-item"><i :class="`is-${key}`" /><span>{{ style.label }}</span></div></aside>
    </section>

    <section class="control-strip" aria-label="视觉参数调节">
      <div class="control-title"><span>评审微调</span><small>变体共享同一套坐标与状态映射</small></div>
      <label>主干线宽 <output>{{ config.trunkWidth }} px</output><el-slider v-model="config.trunkWidth" :min="2" :max="10" :show-tooltip="false" /></label>
      <label>节点间距 <output>{{ config.nodeSpacing.toFixed(2) }} x</output><el-slider v-model="config.nodeSpacing" :min="0.9" :max="1.1" :step="0.02" :show-tooltip="false" /></label>
      <label>呼吸强度 <output>{{ config.glowStrength }}%</output><el-slider v-model="config.glowStrength" :min="20" :max="100" :show-tooltip="false" /></label>
      <label>流光速度 <output>{{ config.flowSpeed.toFixed(1) }} s</output><el-slider v-model="config.flowSpeed" :min="1.2" :max="5" :step="0.2" :show-tooltip="false" /></label>
    </section>
  </main>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { gsap } from 'gsap'
import { DrawSVGPlugin } from 'gsap/DrawSVGPlugin'
import { MotionPathPlugin } from 'gsap/MotionPathPlugin'
import { MorphSVGPlugin } from 'gsap/MorphSVGPlugin'
import MeridianNode from '../components/meridian/MeridianNode.vue'
import { meridianStatusStyles, type MeridianPoint } from '../components/meridian/meridianStatus'

gsap.registerPlugin(DrawSVGPlugin, MotionPathPlugin, MorphSVGPlugin)

type Variant = 'ink' | 'dark'
const activeVariant = ref<Variant>(new URLSearchParams(window.location.search).get('variant') === 'ink' ? 'ink' : 'dark')
const isCapture = new URLSearchParams(window.location.search).has('capture')
const stageRef = ref<HTMLElement>()
const bloomRef = ref<SVGPathElement>()
const config = reactive({ trunkWidth: 6, auraWidth: 10, nodeRadius: 13, nodeSpacing: 1, glowStrength: 72, flowSpeed: 2.8 })
const variants = {
  ink: { jade: '#5f8f77', jadeDark: '#405d4e', gold: '#b68b3a', cream: '#f0e6cb', nodeFill: '#f7efd9' },
  dark: { jade: 'var(--qh-color-jade)', jadeDark: 'var(--qh-color-jade-d)', gold: 'var(--qh-color-gold)', cream: 'var(--qh-color-cream)', nodeFill: 'rgba(18, 36, 33, .94)' },
} as const
const cssVariables = computed(() => {
  const palette = variants[activeVariant.value]
  return { '--demo-glow': `${config.glowStrength / 100}`, '--meridian-jade': palette.jade, '--meridian-jade-d': palette.jadeDark, '--meridian-gold': palette.gold, '--meridian-cream': palette.cream, '--meridian-node-fill': palette.nodeFill }
})

const points: MeridianPoint[] = [
  { id: 'herb-1', label: '四气五味', detail: '根基', x: 284, y: 500, status: 'mastered', labelSide: 'left' },
  { id: 'herb-2', label: '解表药', detail: '推荐起点', x: 318, y: 380, status: 'recommended', labelSide: 'right' },
  { id: 'herb-3', label: '清热药', detail: '自由选修', x: 326, y: 300, status: 'free', labelSide: 'left' },
  { id: 'herb-4', label: '归经定位', detail: '暂缓', x: 352, y: 128, status: 'deferred', labelSide: 'right' },
  { id: 'branch-1', label: '药性配伍', detail: '自定义', x: 497, y: 181, status: 'custom', color: '#bc5d58', labelSide: 'right' },
  { id: 'formula-1', label: '君臣佐使', detail: '已达标', x: 778, y: 506, status: 'mastered', labelSide: 'right' },
  { id: 'formula-2', label: '解表剂', detail: '当前学习', x: 758, y: 380, status: 'current', labelSide: 'left' },
  { id: 'formula-3', label: '方证相应', detail: '考研高频', x: 770, y: 293, status: 'exam_hot', labelSide: 'right' },
]
const displayPoints = computed(() => points.map((point) => ({ ...point, y: 290 + (point.y - 290) * config.nodeSpacing })))
const flowOrbs = [{ id: 'left-a', path: '#mastered-left' }, { id: 'left-b', path: '#mastered-left' }, { id: 'right-a', path: '#mastered-right' }]
let animationContext: gsap.Context | undefined

const bloomNode = async (id: string) => {
  await nextTick()
  const target = stageRef.value?.querySelector<SVGGElement>(`[transform*="translate(${points.find((point) => point.id === id)?.x}"]`)
  if (!target || !bloomRef.value) return
  const matrix = target.getCTM()
  if (!matrix) return
  gsap.set(bloomRef.value, { x: matrix.e, y: matrix.f, transformOrigin: '50% 50%' })
  gsap.fromTo(bloomRef.value, { opacity: 0, scale: 0.25, morphSVG: 'M 0 0' }, { opacity: 1, scale: 1, morphSVG: '.unlock-bloom-target', duration: 0.32, ease: 'power2.out', yoyo: true, repeat: 1 })
}

const runEntrance = () => {
  animationContext?.revert()
  animationContext = gsap.context(() => {
    const trunks = gsap.utils.toArray<SVGPathElement>('.trunk-draw')
    const nodes = gsap.utils.toArray<SVGGElement>('.meridian-node')
    if (isCapture) {
      gsap.set(trunks, { drawSVG: '100%' })
      gsap.set(nodes, { autoAlpha: 1, scale: 1 })
      return
    }
    gsap.set(trunks, { drawSVG: '0%' })
    gsap.set(nodes, { autoAlpha: 0, scale: 0.48, transformOrigin: '50% 50%' })
    gsap.timeline().to(trunks, { drawSVG: '100%', duration: 1.5, stagger: 0.08, ease: 'power2.out' }).to(nodes, { autoAlpha: 1, scale: 1, duration: 0.3, stagger: 0.11, ease: 'back.out(1.8)' }, '-=1.04')
    flowOrbs.forEach((orb, index) => {
      gsap.to(`.flow-orb[data-path="${orb.path}"]`, { duration: config.flowSpeed, motionPath: { path: orb.path, align: orb.path, alignOrigin: [0.5, 0.5] }, repeat: -1, ease: 'none', delay: index * 0.7 })
    })
  }, stageRef.value)
}

onMounted(runEntrance)
watch(() => config.flowSpeed, runEntrance)
onBeforeUnmount(() => animationContext?.revert())
</script>

<style scoped lang="scss">
@use "../styles/tokens.scss" as *;
.meridian-demo { width:min(1480px,100%); margin:0 auto; color:var(--qh-text); }.demo-header { display:flex; justify-content:space-between; gap:26px; align-items:flex-end; padding:8px 4px 18px; }.title-lockup { display:flex; align-items:center; gap:14px; }.seal { display:grid; grid-template-columns:1fr 1fr; width:39px; height:39px; padding:3px; border:2px solid #b84d43; color:#b84d43; font-family:serif; font-size:16px; font-weight:800; line-height:1; transform:rotate(-4deg); }.seal span { display:grid; place-items:center; border:1px solid rgba(184,77,67,.6); }.eyebrow { margin:0 0 7px; color:var(--meridian-jade); font-size:10px; font-weight:800; letter-spacing:1.7px; }h1 { margin:0; color:var(--meridian-cream); font-family:var(--qh-font-display); font-size:34px; line-height:1.1; font-weight:900; letter-spacing:0; }.intro { margin:7px 0 0; color:var(--qh-text-muted); font-size:13px; }.variant-switch { display:grid; justify-items:end; gap:7px; }.variant-switch small { color:var(--qh-text-muted); font-size:11px; }.variant-switch :deep(.el-radio-button__inner) { border-color:rgba(143,208,173,.25); color:var(--qh-text-muted); background:rgba(15,31,29,.36); box-shadow:none; }.variant-switch :deep(.el-radio-button__original-radio:checked + .el-radio-button__inner) { color:var(--meridian-cream); background:var(--meridian-jade-d); border-color:var(--meridian-jade-d); box-shadow:none; }
.demo-stage { position:relative; min-height:590px; overflow:hidden; border:1px solid rgba(143,208,173,.22); border-radius:8px; background:radial-gradient(ellipse at 50% 48%, rgba(85,151,119,.16), transparent 42%), radial-gradient(ellipse at 2% 0%, rgba(143,208,173,.13), transparent 31%), radial-gradient(ellipse at 100% 105%, rgba(231,194,116,.12), transparent 35%), linear-gradient(130deg,#203a32,#111f21 62%,#0e181c); box-shadow:var(--qh-inner-highlight),var(--qh-shadow-deep); }.paper-texture { position:absolute; inset:0; width:100%; height:100%; opacity:.065; pointer-events:none; }.background-ink { position:absolute; inset:0; width:100%; height:100%; fill:none; stroke:var(--meridian-cream); stroke-width:1.3; opacity:.045; }.meridian-map { position:relative; display:block; width:100%; height:590px; }.trunk-aura { fill:none; stroke:var(--meridian-jade); stroke-linecap:round; opacity:.15; filter:url(#ink-soft); }.meridian-ink { fill:none; stroke:rgba(3,11,10,.54); filter:url(#ink-soft); stroke-linecap:round; }.meridian-trunk { fill:none; stroke:url(#trunk-jade); stroke-linecap:round; }.mastered-segment { fill:none; stroke:url(#mastered-flow); stroke-linecap:round; }.meridian-branch { fill:none; stroke:var(--meridian-jade); stroke-dasharray:3 7; stroke-linecap:round; opacity:.58; }.orb-tail { fill:var(--meridian-jade); opacity:.2; filter:blur(3px); }.orb-core { fill:var(--meridian-cream); }.direction-index { fill:var(--meridian-gold); font-family:Georgia,serif; font-size:24px; font-style:italic; }.direction-title { fill:var(--meridian-cream); font-family:var(--qh-font-display); font-size:19px; font-weight:900; letter-spacing:.6px; }.direction-subtitle { fill:var(--qh-text-muted); font-size:11px; letter-spacing:1px; }.segment-label { fill:var(--meridian-jade); font-size:11px; font-weight:700; letter-spacing:1.6px; }.ink-note { fill:var(--meridian-cream); font-family:serif; font-size:12px; font-style:italic; letter-spacing:.8px; opacity:.42; }.unlock-bloom { fill:var(--meridian-gold); opacity:0; pointer-events:none; }.unlock-bloom-target { display:none; }
.legend-panel { position:absolute; right:20px; bottom:18px; display:grid; grid-template-columns:repeat(2,auto); gap:8px 16px; min-width:230px; padding:13px 15px; border:1px solid rgba(143,208,173,.16); border-radius:7px; background:rgba(11,27,24,.52); backdrop-filter:blur(8px); }.legend-panel p { grid-column:1 / -1; margin:0 0 1px; color:var(--qh-text-muted); font-size:10px; font-weight:800; letter-spacing:1.3px; }.legend-item { display:flex; align-items:center; gap:7px; color:rgba(238,245,239,.84); font-size:11px; white-space:nowrap; }.legend-item i { width:13px; height:13px; border:2px solid var(--meridian-jade); border-radius:50%; }.legend-item i.is-current { box-shadow:0 0 12px var(--meridian-jade); }.legend-item i.is-mastered { background:var(--meridian-jade); }.legend-item i.is-free { border-color:var(--qh-color-blue); border-style:dashed; }.legend-item i.is-deferred { border-color:rgba(190,200,194,.45); opacity:.55; }.legend-item i.is-exam_hot { border-color:var(--meridian-gold); }.legend-item i.is-custom { border-color:#bc5d58; }
.control-strip { display:grid; grid-template-columns:1.1fr repeat(4,minmax(150px,1fr)); gap:20px; align-items:center; margin-top:14px; padding:14px 16px; border:1px solid rgba(143,208,173,.16); border-radius:8px; background:rgba(15,31,29,.44); }.control-title { display:grid; gap:3px; }.control-title span { color:var(--meridian-cream); font-family:var(--qh-font-display); font-size:14px; font-weight:800; }.control-title small { color:var(--qh-text-muted); font-size:11px; }.control-strip label { display:grid; grid-template-columns:1fr auto; gap:5px 10px; color:var(--qh-text-muted); font-size:11px; }.control-strip output { color:var(--meridian-gold); font-variant-numeric:tabular-nums; }.control-strip :deep(.el-slider) { grid-column:1 / -1; }.control-strip :deep(.el-slider__runway) { margin:2px 0; background:rgba(238,245,239,.12); }.control-strip :deep(.el-slider__bar) { background:var(--meridian-jade-d); }.control-strip :deep(.el-slider__button) { width:13px; height:13px; border-color:var(--meridian-cream); background:var(--meridian-jade); }
.theme-ink { --qh-text:#25332d; --qh-text-muted:rgba(48,64,56,.64); position:relative; isolation:isolate; }.theme-ink::before { position:fixed; z-index:-1; inset:0; background:#e7dcc4; content:""; }.theme-ink h1,.theme-ink .direction-title { color:#2f4238; }.theme-ink .direction-subtitle,.theme-ink .ink-note { fill:#53645a; opacity:.78; }.theme-ink .demo-stage { border-color:rgba(77,87,70,.3); background:radial-gradient(ellipse at 47% 45%,rgba(95,143,119,.13),transparent 40%),radial-gradient(ellipse at 0% 100%,rgba(182,139,58,.1),transparent 34%),linear-gradient(135deg,#f5efdf,#e7dcc4 74%,#ddd0b7); box-shadow:0 14px 34px rgba(44,48,35,.2),inset 0 1px rgba(255,255,255,.7); }.theme-ink .paper-texture { opacity:.12; }.theme-ink .background-ink { stroke:#3d4a43; opacity:.055; }.theme-ink .meridian-ink { stroke:rgba(37,47,41,.5); }.theme-ink .legend-panel { border-color:rgba(63,81,70,.2); background:rgba(249,244,229,.68); }.theme-ink .legend-item,.theme-ink .control-strip label,.theme-ink .control-title small { color:#34423a; }.theme-ink .control-title span,.theme-ink .control-strip output { color:#415b4d; }.theme-ink .control-strip { background:rgba(246,240,223,.82); border-color:rgba(71,88,75,.2); }.theme-ink .variant-switch :deep(.el-radio-button__inner) { background:rgba(246,240,223,.7); color:#506157; }.theme-ink .orb-core { fill:#fff9e7; }
.theme-ink .direction-title { fill:#2f4238; }
.demo-stage, .meridian-map { min-height:550px; height:550px; }
@media (max-width:1020px) { .control-strip { grid-template-columns:repeat(2,1fr); }.control-title { grid-column:1 / -1; }.legend-panel { right:12px; bottom:12px; } }@media (max-width:720px) { .demo-header { align-items:flex-start; flex-direction:column; }.variant-switch { justify-items:start; }.demo-stage,.meridian-map { min-height:510px; height:510px; }.meridian-map { width:900px; transform:translateX(-90px); }.demo-stage { overflow:auto; }.legend-panel { position:sticky; left:12px; bottom:12px; width:max-content; }.control-strip { grid-template-columns:1fr; } }
</style>
