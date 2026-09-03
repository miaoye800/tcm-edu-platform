<template>
  <main class="mindmap-themes" :class="`theme-${activeTheme.id}`" :style="activeTheme.cssVars">
    <header class="library-header">
      <div>
        <p class="kicker">MINDMAP TEMPLATE LIBRARY / 一药八卷</p>
        <h1>麻黄，八种读法</h1>
        <p class="library-intro">同一份知识内容，以不同的阅读秩序呈现。选择一卷，查看其信息性格。</p>
      </div>
      <div class="scene-note"><span>适用</span><strong>{{ activeTheme.scene }}</strong></div>
    </header>

    <nav class="theme-picker" aria-label="思维导图模板选择">
      <button v-for="theme in mindmapThemes" :key="theme.id" class="theme-option" :class="{ active: theme.id === activeTheme.id }" type="button" @click="selectTheme(theme.id)">
        <i class="swatch" :style="{ '--c1': theme.palette[0], '--c2': theme.palette[1], '--c3': theme.palette[2] }" />
        <span>{{ theme.name }}</span>
      </button>
    </nav>

    <section class="workbench" :class="{ fading: isTransitioning }" aria-live="polite">
      <aside class="template-brief">
        <p class="brief-label">卷册立场</p>
        <p class="stance">{{ activeTheme.stance }}</p>
        <dl>
          <div><dt>布局</dt><dd>{{ layoutLabel }}</dd></div>
          <div><dt>节点</dt><dd>{{ activeTheme.nodeStyle.shape }} / {{ activeTheme.nodeStyle.density }}</dd></div>
          <div><dt>线条</dt><dd>{{ activeTheme.lineStyle.kind }} / {{ activeTheme.lineStyle.width }}</dd></div>
        </dl>
      </aside>

      <div class="map-stage" :class="`layout-${activeTheme.layout}`">
        <template v-if="activeTheme.layout === 'tree'">
          <div ref="elixirRef" class="elixir-host" />
          <svg v-if="activeTheme.id === 'herb-garden'" class="leaf-endpoints" viewBox="0 0 1000 680" aria-hidden="true">
            <path v-for="point in leafPoints" :key="`${point.x}-${point.y}`" :transform="`translate(${point.x} ${point.y}) rotate(${point.r})`" d="M0 0C8-10 18-10 22 0C16 8 7 8 0 0Z" />
          </svg>
        </template>

        <div v-else-if="activeTheme.layout === 'fishbone'" class="fishbone-map" role="img" aria-label="麻黄鱼骨对比导图">
          <div class="spine" /><div class="fish-root">麻黄</div>
          <article v-for="(branch, index) in branches" :key="branch.title" class="fish-branch" :class="index % 2 ? 'coral' : 'jade'" :style="{ '--i': index }">
            <strong>{{ branch.title }}</strong><span>{{ branch.children[0] }}</span><small>{{ branch.children[1] }}</small>
          </article>
        </div>

        <div v-else-if="activeTheme.layout === 'timeline'" class="timeline-map" role="img" aria-label="麻黄时间线流程导图">
          <div class="timeline-line" />
          <article v-for="(branch, index) in branches" :key="branch.title" class="timeline-item" :class="index % 2 ? 'below' : 'above'" :style="{ '--i': index }">
            <i>{{ index + 1 }}</i><strong>{{ branch.title }}</strong><span>{{ branch.children[0] }}</span><small>{{ branch.children[1] }}</small>
          </article>
          <div class="timeline-root">麻黄<span>知识展开</span></div>
        </div>

        <div v-else class="radial-map" role="img" aria-label="麻黄放射星图导图">
          <svg viewBox="0 0 1000 600" aria-hidden="true"><line v-for="(_, index) in branches" :key="index" x1="500" y1="300" :x2="radialPoints[index].x" :y2="radialPoints[index].y" /></svg>
          <div class="radial-root">麻黄<span>Ma Huang</span></div>
          <article v-for="(branch, index) in branches" :key="branch.title" class="star-node" :style="{ '--x': `${radialPoints[index].x / 10}%`, '--y': `${radialPoints[index].y / 6}%` }"><strong>{{ branch.title }}</strong><span>{{ branch.children[0] }}</span><small>{{ branch.children[1] }}</small></article>
        </div>
      </div>
    </section>
  </main>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import MindElixir, { SIDE, type MindElixirData, type MindElixirInstance, type Theme } from 'mind-elixir'
import 'mind-elixir/style.css'
import { mindmapThemes, type MindmapTheme } from '../config/mindmapThemes'

type Branch = { title: string; children: [string, string] }
const branches: Branch[] = [
  { title: '性味归经', children: ['辛、微苦，温', '归肺、膀胱经'] },
  { title: '功效', children: ['发汗解表', '宣肺平喘、利水消肿'] },
  { title: '主治', children: ['风寒表实证', '咳喘与风水浮肿'] },
  { title: '七情配伍', children: ['配桂枝：发汗力增', '配杏仁：宣降相因'] },
  { title: '使用禁忌', children: ['表虚自汗、阴虚盗汗者慎用', '高血压及失眠者须遵医嘱，避免自行久服或超量使用'] },
  { title: '易混鉴别', children: ['麻黄：发汗平喘', '桂枝：解肌温通'] },
]
const radialPoints = [{ x: 205, y: 110 }, { x: 510, y: 64 }, { x: 810, y: 126 }, { x: 845, y: 410 }, { x: 490, y: 520 }, { x: 170, y: 422 }]
const leafPoints = [{ x: 710, y: 182, r: 20 }, { x: 910, y: 225, r: -15 }, { x: 735, y: 372, r: 20 }, { x: 920, y: 440, r: -20 }, { x: 725, y: 512, r: 18 }, { x: 910, y: 563, r: -12 }]
const queryTheme = new URLSearchParams(window.location.search).get('theme')
const activeTheme = ref(mindmapThemes.find((theme) => theme.id === queryTheme) ?? mindmapThemes[0])
const elixirRef = ref<HTMLElement>()
const isTransitioning = ref(false)
let elixir: MindElixirInstance | undefined

const layoutLabel = computed(() => ({ tree: '树状脉络', fishbone: '鱼骨对照', timeline: '横向流程', radial: '中心放射' })[activeTheme.value.layout])

const buildTheme = (theme: MindmapTheme): Theme => ({
  name: theme.id,
  type: theme.id === 'warm-gold' || theme.id === 'neon-night' || theme.id === 'star-chart' ? 'dark' : 'light',
  palette: theme.palette,
  cssVar: {
    '--node-gap-x': theme.nodeStyle.density === 'compact' ? '8px' : '17px',
    '--node-gap-y': theme.nodeStyle.density === 'compact' ? '6px' : '13px',
    '--main-gap-x': '24px', '--main-gap-y': '20px', '--main-color': theme.cssVars['--mm-primary'],
    '--main-bgcolor': theme.cssVars['--mm-surface'], '--main-bgcolor-transparent': theme.cssVars['--mm-surface'],
    '--main-border': `2px solid ${theme.cssVars['--mm-node-border']}`, '--color': theme.cssVars['--mm-text'],
    '--bgcolor': theme.cssVars['--mm-bg'], '--selected': theme.cssVars['--mm-secondary'], '--accent-color': theme.cssVars['--mm-accent'],
    '--root-color': theme.cssVars['--mm-text'], '--root-bgcolor': theme.cssVars['--mm-surface'], '--root-border-color': theme.cssVars['--mm-primary'],
    '--root-radius': theme.nodeStyle.radius, '--main-radius': theme.nodeStyle.radius, '--topic-padding': '8px 12px',
    '--panel-color': theme.cssVars['--mm-text'], '--panel-bgcolor': theme.cssVars['--mm-surface'], '--panel-border-color': theme.cssVars['--mm-node-border'], '--map-padding': '80px',
  },
})

const treeData = (): MindElixirData => ({ nodeData: { id: 'mahuang', topic: '麻黄', children: branches.map((branch, index) => ({ id: `branch-${index}`, topic: branch.title, direction: (index % 2 ? 0 : 1) as 0 | 1, branchColor: activeTheme.value.palette[index % activeTheme.value.palette.length], children: branch.children.map((topic, childIndex) => ({ id: `branch-${index}-${childIndex}`, topic })) })) } })

const renderElixir = async () => {
  if (activeTheme.value.layout !== 'tree') return
  await nextTick()
  if (!elixirRef.value) return
  elixir?.destroy()
  elixirRef.value.replaceChildren()
  elixir = new MindElixir({ el: elixirRef.value, direction: SIDE, editable: false, contextMenu: false, toolBar: false, keypress: false, overflowHidden: true, theme: buildTheme(activeTheme.value), scaleMin: 0.45, scaleMax: 1.5 })
  elixir.init(treeData())
  window.setTimeout(() => elixir?.toCenter(), 0)
}

const selectTheme = async (id: string) => {
  if (id === activeTheme.value.id) return
  isTransitioning.value = true
  await new Promise((resolve) => window.setTimeout(resolve, 150))
  activeTheme.value = mindmapThemes.find((theme) => theme.id === id) ?? mindmapThemes[0]
  await renderElixir()
  await new Promise((resolve) => window.setTimeout(resolve, 150))
  isTransitioning.value = false
}

onMounted(renderElixir)
onBeforeUnmount(() => elixir?.destroy())
</script>

<style scoped lang="scss">
.mindmap-themes { min-height:100vh; padding:34px clamp(18px,4vw,64px) 40px; color:var(--mm-text); background:var(--mm-bg); font-family:var(--qh-font-body); transition:background .3s ease,color .3s ease; }
.library-header { display:flex; align-items:end; justify-content:space-between; gap:28px; max-width:1440px; margin:0 auto 22px; }.kicker,.brief-label { margin:0 0 8px; color:var(--mm-accent); font-size:11px; font-weight:900; letter-spacing:1.6px; }.library-header h1 { margin:0; font-family:var(--qh-font-display); font-size:34px; line-height:1.12; letter-spacing:0; }.library-intro { margin:8px 0 0; color:var(--mm-muted); font-size:14px; }.scene-note { width:min(360px,30%); padding:11px 0 11px 16px; border-left:2px solid var(--mm-secondary); display:grid; gap:5px; }.scene-note span { color:var(--mm-muted); font-size:11px; font-weight:800; }.scene-note strong { font-size:13px; line-height:1.55; }
.theme-picker { display:flex; gap:8px; max-width:1440px; margin:0 auto 20px; overflow-x:auto; padding:4px 0 10px; }.theme-option { flex:0 0 auto; display:flex; align-items:center; gap:8px; min-height:39px; padding:7px 10px; border:1px solid color-mix(in srgb,var(--mm-text) 16%,transparent); border-radius:4px; color:var(--mm-muted); background:color-mix(in srgb,var(--mm-surface) 66%,transparent); font:700 12px var(--qh-font-body); cursor:pointer; transition:border-color .2s ease,color .2s ease,transform .2s ease; }.theme-option:hover { transform:translateY(-1px); color:var(--mm-text); }.theme-option:focus-visible { outline:2px solid var(--mm-accent); outline-offset:2px; }.theme-option.active { border-color:var(--mm-primary); color:var(--mm-text); box-shadow:0 0 0 1px var(--mm-glow); }.swatch { width:22px; height:16px; border:1px solid color-mix(in srgb,var(--c1) 72%,transparent); background:linear-gradient(135deg,var(--c1) 0 38%,var(--c2) 38% 68%,var(--c3) 68%); }
.workbench { display:grid; grid-template-columns:230px minmax(0,1fr); max-width:1440px; min-height:680px; margin:0 auto; border:1px solid color-mix(in srgb,var(--mm-text) 18%,transparent); border-radius:7px; overflow:hidden; background:var(--mm-surface); box-shadow:0 18px 44px color-mix(in srgb,var(--mm-text) 11%,transparent); transition:opacity .15s ease; }.workbench.fading { opacity:0; }.template-brief { padding:28px 22px; border-right:1px solid color-mix(in srgb,var(--mm-text) 15%,transparent); background:color-mix(in srgb,var(--mm-bg) 58%,var(--mm-surface)); }.stance { margin:0; font-family:var(--qh-font-display); font-size:16px; font-weight:700; line-height:1.75; }.template-brief dl { display:grid; gap:12px; margin:28px 0 0; }.template-brief dl div { display:grid; gap:3px; padding-top:10px; border-top:1px solid color-mix(in srgb,var(--mm-text) 13%,transparent); }.template-brief dt { color:var(--mm-muted); font-size:10px; font-weight:800; letter-spacing:1px; }.template-brief dd { margin:0; font-size:12px; font-weight:700; }
.map-stage { position:relative; min-width:0; min-height:680px; overflow:hidden; background:var(--mm-bg); }.elixir-host { width:100%; height:680px; }.elixir-host :deep(.map-container) { font-family:var(--qh-font-body); background:var(--mm-bg); }.elixir-host :deep(me-tpc) { max-width:230px; white-space:normal; line-height:1.45; }.elixir-host :deep(me-parent me-tpc) { border:1px solid transparent; background:var(--mm-surface); box-shadow:0 3px 0 color-mix(in srgb,var(--mm-primary) 12%,transparent); }.elixir-host :deep(me-root me-tpc) { font-family:var(--qh-font-display); font-weight:900; box-shadow:0 0 0 6px var(--mm-glow); }.theme-ink-scroll .elixir-host :deep(.lines path) { stroke-linecap:round; }.theme-ink-scroll .elixir-host :deep(.lines path:nth-child(3n)) { stroke-width:4px; }.theme-ink-scroll .elixir-host :deep(.lines path:nth-child(3n + 1)) { stroke-width:1.5px; }.theme-ink-scroll .elixir-host :deep(me-parent me-tpc) { border-color:var(--mm-node-border); }.theme-warm-gold .elixir-host :deep(me-parent me-tpc) { border:1px solid var(--mm-node-border); }.theme-warm-gold .elixir-host :deep(me-main > me-wrapper > me-parent me-tpc)::after { content:'重点'; position:absolute; top:-10px; right:7px; padding:2px 5px; color:#2a2113; background:var(--mm-primary); font-size:9px; font-weight:900; }.theme-neon-night .elixir-host :deep(me-parent me-tpc) { border-color:var(--mm-node-border); box-shadow:0 0 0 1px rgba(120,174,251,.24),0 0 2px var(--mm-glow); }.theme-neon-night .elixir-host :deep(.lines path) { filter:drop-shadow(0 0 1px var(--mm-accent)); }.theme-herb-garden .elixir-host :deep(me-parent me-tpc) { border-radius:999px; border:1px solid var(--mm-node-border); background:color-mix(in srgb,var(--mm-surface) 82%,var(--mm-accent)); }.theme-herb-garden .elixir-host :deep(.lines path) { stroke-linecap:round; }.theme-hand-scroll .map-stage { background:linear-gradient(90deg,rgba(113,78,42,.1),transparent 4%,transparent 96%,rgba(113,78,42,.1)),var(--mm-bg); }.theme-hand-scroll .elixir-host :deep(me-parent me-tpc) { border:1px solid var(--mm-node-border); transform:rotate(-.4deg); box-shadow:2px 3px 0 rgba(92,78,49,.1); }.theme-hand-scroll .elixir-host :deep(.lines path) { stroke-dasharray:5 4; }
.leaf-endpoints { position:absolute; inset:0; z-index:12; width:100%; height:100%; fill:var(--mm-accent); stroke:var(--mm-primary); stroke-width:1.5; pointer-events:none; }
.fishbone-map,.timeline-map,.radial-map { position:relative; width:100%; height:680px; }.spine { position:absolute; top:50%; left:7%; right:7%; height:4px; background:var(--mm-line); }.fish-root { position:absolute; top:calc(50% - 30px); left:calc(50% - 50px); width:100px; padding:18px 0; border:2px solid var(--mm-secondary); border-radius:50%; color:var(--mm-text); background:var(--mm-surface); text-align:center; font-weight:900; z-index:2; }.fish-branch { position:absolute; left:calc(8% + var(--i) * 15%); width:15%; min-width:105px; display:grid; gap:6px; padding:10px; border:1px solid var(--mm-primary); border-radius:5px; background:var(--mm-surface); font-size:12px; line-height:1.35; }.fish-branch::after { content:''; position:absolute; left:50%; width:1px; height:78px; background:currentColor; }.fish-branch:nth-of-type(odd) { top:14%; }.fish-branch:nth-of-type(odd)::after { top:100%; }.fish-branch:nth-of-type(even) { bottom:12%; }.fish-branch:nth-of-type(even)::after { bottom:100%; }.fish-branch.coral { color:var(--mm-accent); border-color:var(--mm-accent); }.fish-branch.jade { color:var(--mm-primary); }.fish-branch strong { color:var(--mm-text); }.fish-branch span,.fish-branch small { color:var(--mm-muted); }
.timeline-line { position:absolute; top:50%; left:7%; right:7%; height:3px; background:var(--mm-line); }.timeline-root { position:absolute; left:7%; top:calc(50% - 25px); padding:13px 14px; border:2px solid var(--mm-primary); border-radius:4px; background:var(--mm-surface); font-weight:900; }.timeline-root span { display:block; color:var(--mm-muted); font-size:10px; margin-top:3px; }.timeline-item { position:absolute; left:calc(17% + var(--i) * 13%); width:13%; min-width:116px; display:grid; gap:4px; padding:11px; border:1px solid var(--mm-node-border); border-radius:6px; background:var(--mm-surface); font-size:13px; line-height:1.38; }.timeline-item i { position:absolute; left:calc(50% - 11px); width:22px; height:22px; border-radius:50%; color:var(--mm-surface); background:var(--mm-accent); text-align:center; font-style:normal; font-size:11px; font-weight:900; line-height:22px; }.timeline-item.above { top:18%; }.timeline-item.above i { bottom:-72px; }.timeline-item.below { bottom:15%; }.timeline-item.below i { top:-72px; }.timeline-item strong { color:var(--mm-primary); }.timeline-item span,.timeline-item small { color:var(--mm-muted); }
.radial-map svg { position:absolute; inset:0; width:100%; height:100%; }.radial-map line { stroke:var(--mm-line); stroke-width:1.5; }.radial-root { position:absolute; left:calc(50% - 58px); top:calc(50% - 45px); width:116px; height:90px; display:grid; place-content:center; border:1px solid var(--mm-secondary); border-radius:50%; background:var(--mm-surface); box-shadow:0 0 20px var(--mm-glow); text-align:center; font-weight:900; }.radial-root span { color:var(--mm-muted); font-size:10px; margin-top:4px; }.star-node { position:absolute; left:calc(var(--x) - 69px); top:calc(var(--y) - 35px); width:138px; display:grid; gap:4px; padding:9px; border:1px solid var(--mm-node-border); border-radius:4px; background:var(--mm-surface); box-shadow:0 0 13px var(--mm-glow); font-size:11px; line-height:1.3; text-align:center; }.star-node::before { content:''; position:absolute; top:-7px; left:calc(50% - 3px); width:6px; height:6px; border-radius:50%; background:var(--mm-secondary); }.star-node strong { color:var(--mm-primary); }.star-node span,.star-node small { color:var(--mm-muted); }
@media (max-width:900px) { .library-header { align-items:start; flex-direction:column; }.scene-note { width:100%; }.workbench { grid-template-columns:1fr; }.template-brief { display:none; }.map-stage,.elixir-host,.fishbone-map,.timeline-map,.radial-map { min-height:590px; height:590px; }.fish-branch { transform:scale(.84); transform-origin:center; }.timeline-item { transform:scale(.82); transform-origin:center; } }
@media (max-width:620px) { .mindmap-themes { padding:24px 12px; }.library-header h1 { font-size:28px; }.map-stage,.elixir-host,.fishbone-map,.timeline-map,.radial-map { min-height:530px; height:530px; }.fishbone-map,.timeline-map { min-width:760px; }.map-stage { overflow:auto; }.star-node { transform:scale(.78); }.radial-root { transform:scale(.85); } }
</style>
