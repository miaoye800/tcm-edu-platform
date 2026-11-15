<template>
  <section ref="shellRef" class="meridian-scene" :class="`is-${view}`">
    <div ref="hostRef" class="canvas-host" />

    <div ref="labelHostRef" class="label-host" />

    <template v-if="runtime && atlas">
      <BodyMesh
        :runtime="runtime"
        :assets="atlas.assets"
        :acupoints="atlas.acupoints"
        @status="loadStatus = $event"
        @surface="surfaceOffsets = $event"
      />
      <MeridianCurves
        :runtime="runtime"
        :channels="atlas.channels"
        :paths="paths"
        :acupoints="atlas.acupoints"
        :presence="presence"
        :focus="focus"
      />
      <AcupointNodes
        ref="nodesRef"
        :runtime="runtime"
        :acupoints="atlas.acupoints"
        :presence="presence"
        :show-all="showAll"
        :selected="selectedId"
        :offsets="surfaceOffsets"
      />
      <AcupointLabels
        :runtime="runtime"
        :acupoints="atlas.acupoints"
        :presence="presence"
        :focus="focus"
        :visible="labelsVisible"
      />
    </template>

    <div v-if="!atlas" class="scene-loading">
      <p class="loading-title">脉</p>
      <p class="loading-text">{{ loadStatus || '正在读取经脉目录…' }}</p>
    </div>

    <header class="hud hud-top">
      <div class="hud-left">
        <p class="hud-kicker">MERIDIAN ATLAS</p>
        <p class="hud-title">{{ layerTitle }}</p>
        <p class="hud-count">
          {{ subjectSections.length }} 节 · {{ atlas?.acupoints.length ?? 0 }} 穴 ·
          <b>{{ litCount }}</b> 已点亮
        </p>
      </div>
      <div class="hud-actions">
        <button
          v-for="item in viewButtons"
          :key="item.key"
          type="button"
          :class="{ 'is-on': item.on }"
          @click="item.run()"
        >
          {{ item.label }}
        </button>
      </div>
    </header>

    <LayerSwitcher
      class="hud-layers"
      :layers="atlas?.layers ?? []"
      :active="layer"
      :summary="layerSummary"
      @select="(id) => emit('update:layer', id)"
    />

    <div class="hud-toggles">
      <label>
        <input v-model="showAll" type="checkbox" />
        显示未挂章节的穴
      </label>
      <label>
        <input v-model="showLabels" type="checkbox" :disabled="view === 'overview'" />
        穴名<span class="toggle-hint">近景</span>
      </label>
    </div>

    <aside class="hud-legend" aria-label="状态图例">
      <p>穴位状态</p>
      <span v-for="item in legend" :key="item.key"><i :class="`is-${item.key}`" />{{ item.label }}</span>
    </aside>

    <div class="hud-right">
      <TickBand
        v-if="view === 'chapter' && activeAcupoint"
        :sections="activeSections"
        :active="activeSectionId"
        :chapter-label="activeChapterLabel"
        @select="selectSection"
      />
      <TickPoint
        v-if="view === 'acupoint' && activeSection"
        :section="activeSection"
        :states="states"
        :active-tick="activeTickId"
        @open="selectTick"
      />
      <AcupointSheet
        :acupoint="activeAcupoint"
        :presence="activePresence"
        :sections="activeSections"
        :states="states"
        :tick-id="activeTickId"
        :channel-title="activeChannelTitle"
        @close="back"
        @complete="complete"
        @defer="defer"
        @ask="ask"
      />
    </div>

    <!--
      路线条回答三个问题，缺一个用户就只能在 293 个穴位里自己找：
        ① 我该从哪开始（当前第几步、哪一节）
        ② 顺序是什么（一条刻度尺，一格一步，章的边界留缺口）
        ③ 学完这一步之后去哪（下一步）
      以前这里只有一排章级芯片：它既说不出"第几步"，芯片还按 domain_id 跳穴位，
      而一章现在会拆成 1–7 步，点上去只会得到"还没有挂到穴位上"。
    -->
    <div v-if="routeSteps.length" class="route-strip">
      <div class="strip-now">
        <p class="strip-step">
          <b>{{ currentStep ? `第 ${currentStep.seq} 步` : '这条路走完了' }}</b>
          <span>共 {{ progress?.total ?? routeSteps.length }} 步 · 已完成 {{ percentLabel }}</span>
        </p>
        <p class="strip-line">
          <em>从这一步开始</em>
          <button v-if="currentStep" type="button" class="strip-link" @click="focusStep(currentStep)">
            {{ currentStep.title }}
          </button>
          <span v-else class="strip-none">没有待学的步骤了</span>
          <span v-if="currentStep" class="strip-meta">{{ stepMeta(currentStep) }}</span>
        </p>
        <p class="strip-line">
          <em>之后</em>
          <button v-if="nextStep" type="button" class="strip-link is-next" @click="focusStep(nextStep)">
            {{ nextStep.title }}
          </button>
          <span v-else class="strip-none">已是最后一步</span>
          <span v-if="nextStep" class="strip-meta">{{ stepMeta(nextStep) }}</span>
        </p>
      </div>
      <ol ref="rulerRef" class="strip-ruler" aria-label="学习顺序">
        <li v-for="step in routeSteps" :key="`${step.stage_id || step.seq}-${step.seq}`">
          <button
            type="button"
            class="ruler-tick"
            :class="[`is-${step.state}`, { 'is-chapter-start': step.newChapter }]"
            :title="tickTitle(step)"
            :aria-label="tickTitle(step)"
            :aria-current="step.state === 'current' ? 'step' : undefined"
            @click="focusStep(step)"
          />
        </li>
      </ol>
      <p class="strip-hint">一格一步 · 点刻度可跳</p>
    </div>

    <A11yMirror
      :acupoints="mirrorAcupoints"
      :presence="presence"
      :selected="selectedId"
      :channel-title="mirrorChannelTitle"
      :section-titles="sectionTitles"
      @select="selectAcupoint"
      @back="back"
    />

    <p class="hud-precision">{{ atlas?.precision.statement ?? '定位依据 GB/T 12346-2021 · 坐标经几何吸附' }}</p>

    <div v-if="perfVisible" class="hud-perf" aria-hidden="true">
      {{ perf.fps.toFixed(0) }} fps · 最差帧 {{ perf.worstFps.toFixed(0) }} fps ·
      {{ perf.calls }} draw calls · {{ (perf.triangles / 1000).toFixed(0) }}k 面 ·
      {{ runtime?.quality }}
    </div>
  </section>
</template>

<script setup lang="ts">
/**
 * 经脉 3D 场景的编排层。
 *
 * 三级 LOD 的分工（计划 §10.1）：
 *   L0 全身   —— 只有光点与经脉，**永不画刻度**
 *   L1 章节   —— 相机推到该穴，右侧 2D 刻度带列出它挂的所有小节
 *   L2 穴位   —— 相机再推近，右侧逐点刻度可点，点了读正文
 *
 * 所有 Three 对象都装在不带响应式的容器里（`shallowRef` + 普通对象）。
 * Vue 的 Proxy 一旦包住 Object3D，矩阵读取会走 getter，表现是模型闪烁或凭空消失。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Vector3 } from 'three'
import atlasConfig from '../../config/meridian-atlas.json'
import { fetchAtlas, fetchPaths, type MeridianAtlas, type MeridianPathData } from '../../services/meridianApi'
import { usePathStore } from '../../stores/path'
import type { BackendPathState, PathStep } from '../../types/path'
import AcupointLabels from './AcupointLabels.vue'
import AcupointNodes from './AcupointNodes.vue'
import AcupointSheet from './AcupointSheet.vue'
import A11yMirror from './A11yMirror.vue'
import BodyMesh from './BodyMesh.vue'
import LayerSwitcher from './LayerSwitcher.vue'
import MeridianCurves from './MeridianCurves.vue'
import TickBand from './TickBand.vue'
import TickPoint from './TickPoint.vue'
import {
  buildPresence,
  buildSectionProgress,
  presenceLabel,
  type AcupointPresence,
  type Presence,
  type SectionProgress,
} from './meridianPresence'
import { pickAcupoint } from './pickAcupoint'
import { createMeridianScene, type SceneRuntime, type ViewMode } from './useMeridianScene'

const props = defineProps<{ layer: string }>()
const emit = defineEmits<{
  (event: 'update:layer', value: string): void
  (event: 'ask-assistant', payload: { id: string; name: string }): void
  (event: 'focus-node', nodeId: string): void
}>()

const path = usePathStore()
const shellRef = ref<HTMLElement>()
const hostRef = ref<HTMLElement>()
const labelHostRef = ref<HTMLElement>()
const nodesRef = ref<InstanceType<typeof AcupointNodes>>()

// shallowRef：只监听"换了一个 runtime"，不深代理里面的 Three 对象
const runtime = shallowRef<SceneRuntime>()
const atlas = shallowRef<MeridianAtlas>()
const paths = shallowRef<MeridianPathData[]>([])

const view = ref<ViewMode>('overview')
const selectedId = ref<string | null>(null)
const activeSectionId = ref<string | null>(null)
const activeTickId = ref<string | null>(null)
const showAll = ref(false)
// 中性化体表后需要改位的穴位（只有会阴）。BodyMesh 算完经 `surface` 事件送上来，
// 穴位层据此把点贴到中性化后的曲面上；为空时全部用导出的原始坐标。
const surfaceOffsets = shallowRef<Map<string, Vector3>>()
// 全身态（L0）不挂穴名：362 个穴名在 3 m 视距下必然在头面挤成一团，
// 标签避让也救不了——避让只能把它们推得更远，救不了密度。穴名只在
// 章节态 / 穴位态出现（那时只挂当前经，几百个变几十个）。
const showLabels = ref(true)
const labelsVisible = computed(() => showLabels.value && view.value !== 'overview')
const loadStatus = ref('')
const perfVisible = ref(new URLSearchParams(window.location.search).get('perf') === '1')
const perf = ref({ fps: 0, worstFps: 0, calls: 0, triangles: 0 })

const states = computed(() => {
  const map = new Map<string, BackendPathState>()
  for (const item of path.current?.states ?? []) map.set(item.node_id, item.status)
  return map
})

/**
 * 全部小节（293）与「当前筛选出的学科子集」。
 *
 * `sections` 永远是全量：一穴一节之后每个穴位只属于一个小节，按学科裁掉会让
 * 深链 `?focus=` 指到"当前学科里不存在"的点上。筛选只影响高亮与计数。
 */
const sections = computed(() => atlas.value?.layer?.sections ?? [])
const subjectSections = computed(() =>
  props.layer === 'all' ? sections.value : sections.value.filter((item) => item.layer_id === props.layer),
)

const presence = computed(() =>
  atlas.value
    ? buildPresence(atlas.value, states.value, subjectSections.value)
    : new Map<string, AcupointPresence>(),
)
const sectionProgress = computed(() =>
  atlas.value ? buildSectionProgress(atlas.value, states.value) : new Map(),
)
const litCount = computed(() => [...presence.value.values()].filter((item) => item.presence === 'lit').length)

const activeAcupoint = computed(
  () => atlas.value?.acupoints.find((item) => item.id === selectedId.value) ?? null,
)
const activePresence = computed(() => (selectedId.value ? presence.value.get(selectedId.value) ?? null : null))
const activeSections = computed<SectionProgress[]>(() => {
  const ids = activePresence.value?.section_ids ?? []
  return ids
    .map((id) => sectionProgress.value.get(id))
    .filter((item): item is SectionProgress => Boolean(item))
})
const activeSection = computed(() =>
  activeSectionId.value ? sectionProgress.value.get(activeSectionId.value) ?? null : null,
)
/**
 * 刻度带的表头 = **章**；下面的每一行只是节/小节名，一个字都不带章名。
 *
 * 以前的写法是 `${part} 第${no}章`，于是总论那段读出"总论 第1章"——把篇名摆在
 * 章号前面，看起来像一个叫"总论"的章。现在只写章本身：
 *   · 教材给这一章编了号 → `第3章 · 中药的配伍`
 *   · 教材不编号（绪论 / 总论，`book_chapter_no = 0`）→ 只写章名，不写"第0章"
 *   · 这一章在教材里本来就不分节、拿整章当一个单元 → 只写章名（行内也是它）
 */
const activeChapterLabel = computed(() => {
  const first = activeSections.value[0]?.section
  if (!first) return '本章'
  const title = (first.chapter_title || '').trim()
  const numbered = first.book_chapter_no !== 0
  if (!numbered) return title || `第${first.chapter_no}章`
  const head = `第${first.book_chapter_no}章`
  return !title || title === first.title ? head : `${head} · ${title}`
})
const channelTitle = (id?: string) =>
  atlas.value?.channels.find((item) => item.id === id)?.title ?? ''
const activeChannelTitle = computed(() => channelTitle(activeAcupoint.value?.channel_id))
const focus = computed(() => activeAcupoint.value?.channel_id ?? null)

const sectionTitles = computed(() => {
  const map = new Map<string, string[]>()
  for (const section of sections.value) {
    const list = map.get(section.acupoint_id) ?? []
    list.push(section.title)
    map.set(section.acupoint_id, list)
  }
  return map
})

const mirrorChannel = computed(() => focus.value ?? 'LU')
const mirrorAcupoints = computed(() =>
  (atlas.value?.acupoints ?? []).filter((item) => item.channel_id === mirrorChannel.value),
)
const mirrorChannelTitle = computed(() => channelTitle(mirrorChannel.value) || '手太阴肺经')

const layerTitle = computed(() => {
  if (props.layer === 'all') return '一具身体 · 全部学科'
  return atlas.value?.layers.find((item) => item.id === props.layer)?.title ?? '中药学'
})
const layerSummary = computed(() => {
  const chapters = new Set(subjectSections.value.map((item) => item.chapter_key)).size
  if (props.layer === 'all') {
    const subjects = new Set(sections.value.map((item) => item.layer_id)).size
    return `${subjects} 科 / ${chapters} 章 / ${sections.value.length} 节`
  }
  return `${chapters} 章 / ${subjectSections.value.length} 节`
})

const legend = computed(() =>
  (['lit', 'current', 'split', 'active', 'planned', 'deferred'] as Presence[]).map((key) => ({
    key,
    label: presenceLabel[key],
  })),
)

/**
 * 已确认路线的步骤（后端 `plan.progress`）。
 *
 * 位置信息只能由后端给：一步 = 一个教材单元，完成度要按**该单元的点集**数，
 * 前端手里只有一张扁平的 `states`，数不出单元边界（一个穴位可能只挂着半节）。
 * `newChapter` 是纯显示用的派生值——刻度尺上章与章之间留一个缺口，
 * 同一章的各节才会连成一段路，而不是 284 个等距的孤立格子。
 */
type RouteStep = PathStep & { newChapter: boolean }

const progress = computed(() => path.current?.plan.progress ?? null)
const routeSteps = computed<RouteStep[]>(() => {
  const steps = progress.value?.steps ?? []
  return steps.map((step, index) => {
    const previous = index ? steps[index - 1] : null
    return {
      ...step,
      newChapter:
        !previous ||
        previous.subject_id !== step.subject_id ||
        previous.chapter_title !== step.chapter_title,
    }
  })
})
const currentStep = computed(() => progress.value?.current ?? null)
const nextStep = computed(() => progress.value?.next ?? null)
const percentLabel = computed(() => {
  if (!progress.value) return '—'
  return `${Math.round((progress.value.percent ?? 0) * 100)}%`
})

/** 一行小字：谁的点、多少知识点、大约多久。**不写章名**——节名自己够用。 */
const stepMeta = (step: PathStep) => {
  const bits: string[] = []
  if (step.subject_name) bits.push(step.subject_name)
  bits.push(`${step.point_count} 个知识点`)
  if (step.minutes) bits.push(`约 ${step.minutes} 分钟`)
  return bits.join(' · ')
}

const tickTitle = (step: RouteStep) => {
  const kind = step.unit_kind === 'chapter' ? '（整章，教材不分节）' : ''
  return `第 ${step.seq} 步 · ${step.title}${kind}`
}

const rulerRef = ref<HTMLElement>()

/**
 * 把当前步滚到刻度尺中间。
 *
 * 不用 `scrollIntoView`：它会把整页一起滚，地图下面还有内容时画面会往上跳一格。
 * `offsetLeft` 需要一个定位过的祖先（`.strip-ruler` 自己就是 relative）。
 */
const centerRuler = () => {
  const host = rulerRef.value
  if (!host) return
  const tick =
    host.querySelector<HTMLElement>('.ruler-tick.is-current') ??
    host.querySelector<HTMLElement>('.ruler-tick.is-next') ??
    host.querySelector<HTMLElement>('.ruler-tick.is-pending')
  if (!tick) return
  host.scrollLeft = Math.max(0, tick.offsetLeft - host.clientWidth / 2 + tick.offsetWidth / 2)
}

const viewButtons = computed(() => [
  { key: 'overview', label: '全身', on: view.value === 'overview', run: back },
  {
    key: 'front',
    label: '正面',
    on: false,
    run: () => {
      runtime.value?.goView('front', { duration: 1000 })
      view.value = 'overview'
    },
  },
  {
    key: 'back',
    label: '背面',
    on: false,
    run: () => {
      runtime.value?.goView('back', { duration: 1000 })
      view.value = 'overview'
    },
  },
  {
    key: 'side',
    label: '侧面',
    on: false,
    run: () => {
      runtime.value?.goView('side', { duration: 1000 })
      view.value = 'overview'
    },
  },
])

/* ------------------------------------------------------------------ 交互 */
const selectAcupoint = (id: string) => {
  const target = atlas.value?.acupoints.find((item) => item.id === id)
  if (!target || !runtime.value) return
  selectedId.value = id
  activeSectionId.value = activePresenceById(id)?.section_ids[0] ?? null
  activeTickId.value = null
  view.value = 'chapter'
  runtime.value.flyTo(
    new Vector3(target.x3d, target.y3d, target.z3d),
    atlasConfig.camera.distance.chapter,
    { fov: atlasConfig.camera.fov.chapter },
  )
}

const activePresenceById = (id: string) => presence.value.get(id)

/** L1 → L2：选中小节，相机再推近一档，右侧换成逐点刻度。 */
const selectSection = (id: string) => {
  activeSectionId.value = id
  activeTickId.value = null
  view.value = 'acupoint'
  const target = activeAcupoint.value
  if (!target || !runtime.value) return
  runtime.value.flyTo(
    new Vector3(target.x3d, target.y3d, target.z3d),
    atlasConfig.camera.distance.acupoint,
    { fov: atlasConfig.camera.fov.acupoint },
  )
}

/**
 * L2 内点刻度：只换选中的那一条，相机不动。
 *
 * 五十个刻度挨个点过去是 L2 的常态操作，每点一次都重新飞行的话台面会一直抖；
 * 用户此刻要的是"换一条正文"，不是"换一个机位"。但地址栏必须跟着走——
 * `?focus=<node_id>` 是这个位置唯一能被分享、能被刷新还原的表达。
 */
const selectTick = (nodeId: string) => {
  activeTickId.value = nodeId
  view.value = 'acupoint'
  emit('focus-node', nodeId)
}

/** 逐级退回，而不是一步跳回全身：L2 退到 L1、L1 才退到 L0。 */
const back = () => {
  if (view.value === 'acupoint' && activeAcupoint.value) {
    view.value = 'chapter'
    activeTickId.value = null
    runtime.value?.flyTo(
      new Vector3(activeAcupoint.value.x3d, activeAcupoint.value.y3d, activeAcupoint.value.z3d),
      atlasConfig.camera.distance.chapter,
      { fov: atlasConfig.camera.fov.chapter },
    )
    return
  }
  view.value = 'overview'
  selectedId.value = null
  activeSectionId.value = null
  activeTickId.value = null
  runtime.value?.flyTo(new Vector3(0, 0.87, 0), atlasConfig.camera.distance.overview, {
    fov: atlasConfig.camera.fov.overview,
  })
}

const complete = async (nodeId: string) => {
  const result = await path.complete(nodeId)
  ElMessage.success('已记录这次学习')
  if (result.diff?.inserted_nodes?.length) ElMessage.info(`小岐补入一关：${result.diff.reason}`)
}

const defer = async (nodeId: string) => {
  await path.defer(nodeId, '暂时不感兴趣')
  ElMessage.success('已暂缓，路线里还留着这一关')
}

const ask = (name: string) => {
  // 优先用当前刻度：小岐拿到的必须是一个能进知识树的 node_id，
  // 只传"合谷"两个字它查不到任何正文。
  const tick =
    activeTickId.value ??
    activeSection.value?.section.points[0]?.id ??
    activeAcupoint.value?.id ??
    name
  emit('ask-assistant', { id: tick, name })
}

/**
 * 点一步 → 落到那一节的第一个**还没学**的点上。
 *
 * 不能拿 `stage_id` 或 `unit_id` 去跳：地图认的是知识点 id（`d01.s2.p3`），
 * 单元 id 只在路线里有效。取点集里第一个未完成的点，用户看到的正好是他该动的那一条。
 */
const focusStep = (step: PathStep) => {
  const ids = step.point_ids ?? []
  const target = ids.find((id) => states.value.get(id) !== 'completed') ?? ids[0]
  if (!target) {
    ElMessage.info(`「${step.title}」还没挂到穴位上`)
    return
  }
  selectByNode(target)
  emit('focus-node', target)
}

/* ------------------------------------------------------------------ 拾取 */
let pointerDown: { x: number; y: number } | null = null
const onPointerDown = (event: PointerEvent) => {
  pointerDown = { x: event.clientX, y: event.clientY }
}
const onPointerUp = (event: PointerEvent) => {
  const start = pointerDown
  pointerDown = null
  if (!start || event.button !== 0 || !runtime.value) return
  if (Math.hypot(event.clientX - start.x, event.clientY - start.y) > 6) return // 拖动不算点击
  const rect = hostRef.value?.getBoundingClientRect()
  if (!rect) return
  const candidates = nodesRef.value?.pickables() ?? []
  const id = pickAcupoint({
    camera: runtime.value.camera,
    target: runtime.value.controls.target,
    width: rect.width,
    height: rect.height,
    candidates,
    x: event.clientX - rect.left,
    y: event.clientY - rect.top,
  })
  if (id) selectAcupoint(id)
}

/* ------------------------------------------------------------------ 键盘 */
const held = new Set<string>()
let frameOff: (() => void) | undefined
const onKeyDown = (event: KeyboardEvent) => {
  const target = event.target as HTMLElement | null
  if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) return
  if (event.key === 'Escape') {
    back()
    return
  }
  if (event.key === '1' || event.key === '2' || event.key === '3') {
    runtime.value?.goView(event.key === '1' ? 'front' : event.key === '2' ? 'back' : 'side', { duration: 900 })
    view.value = 'overview'
    return
  }
  if (event.key.startsWith('Arrow')) {
    held.add(event.key)
    event.preventDefault()
  }
}
const onKeyUp = (event: KeyboardEvent) => held.delete(event.key)
const onBlur = () => held.clear()

/* ------------------------------------------------------------------ 加载 */
/**
 * 深链进来时地图可能还没加载完。
 *
 * `/path?focus=…` 是视图在 `onMounted` 里推过来的，而经脉目录是异步拉的
 * （几百 KB JSON）。目录没到，`sections` 是空的，`selectByNode` 找不到小节就直接
 * 返回——表现是「点了在地图上看，页面停了全身态」。所以先记下来，目录到位再落。
 */
let pendingFocus = ''

/**
 * 目录只拉一次，永远是「全部学科」这一份。
 *
 * 一穴一节之后，六门课共用同一套穴位——按学科去查接口只会拿到同一批穴位里的
 * 一个子集，切换学科还得重拉一遍。所以数据层不再有 `layer` 这个概念，
 * 学科退化成每节自带的一个 `layer_id` 字段，筛选在前端做。
 */
const loadAtlas = async () => {
  loadStatus.value = '正在读取经脉目录…'
  try {
    atlas.value = await fetchAtlas('all')
  } catch (error) {
    loadStatus.value = error instanceof Error ? error.message : '经脉目录读取失败'
    ElMessage.error('经脉目录读取失败，先确认后端已启动')
    return
  }
  if (pendingFocus) {
    const nodeId = pendingFocus
    pendingFocus = ''
    selectByNode(nodeId)
  }
  // 目录晚到一步时 `selectByNode` 只是排了待办，`loadAtlas` 消费完再补一次
  // 地址栏同步——否则刷新 `?focus=` 深链后，右侧选中的是 A，地址栏却还是空。
  if (view.value === 'acupoint' && activeTickId.value) emit('focus-node', activeTickId.value)
}

onMounted(async () => {
  if (!hostRef.value || !labelHostRef.value) return
  const scene = createMeridianScene(hostRef.value, labelHostRef.value)
  runtime.value = scene
  scene.onStats((stats) => {
    perf.value = stats
  })

  await path.load()
  await nextTick()
  centerRuler()
  await Promise.all([loadAtlas(), fetchPaths().then((data) => (paths.value = data))])

  hostRef.value.addEventListener('pointerdown', onPointerDown)
  hostRef.value.addEventListener('pointerup', onPointerUp)
  window.addEventListener('keydown', onKeyDown)
  window.addEventListener('keyup', onKeyUp)
  window.addEventListener('blur', onBlur)

  frameOff = scene.addFrame((dt) => {
    if (!held.size) return
    // 按"像素/秒"走，不是按帧：低帧率机器上按帧算会慢到挪不动
    const speed = 420 * dt
    let dx = 0
    let dy = 0
    if (held.has('ArrowLeft')) dx += speed
    if (held.has('ArrowRight')) dx -= speed
    if (held.has('ArrowUp')) dy += speed
    if (held.has('ArrowDown')) dy -= speed
    if (dx || dy) scene.panBy(dx, dy)
  })
})

// 切学科不再重拉目录（数据是同一份），只重置选中态——否则用户会以为"图变了"，
// 其实只是高亮变了，而右下的详情还停在上一门课的穴位上。
watch(
  () => props.layer,
  () => {
    selectedId.value = null
    view.value = 'overview'
  },
)

// 学完一步之后后端会把"当前步"往前推，刻度尺要跟着把新的一步滚到中间；
// 否则用户点完"已学"看到的仍是原来那一段，会以为没生效。
watch(
  () => progress.value?.current?.seq,
  () => {
    void nextTick(centerRuler)
  },
)

onBeforeUnmount(() => {
  frameOff?.()
  hostRef.value?.removeEventListener('pointerdown', onPointerDown)
  hostRef.value?.removeEventListener('pointerup', onPointerUp)
  window.removeEventListener('keydown', onKeyDown)
  window.removeEventListener('keyup', onKeyUp)
  window.removeEventListener('blur', onBlur)
  runtime.value?.dispose()
  runtime.value = undefined
})

/**
 * 按 node_id 深链：测评回跳、对话里的「在地图上看」，都走这里。
 *
 * **要落到刻度，不能只推到穴位**。一个穴位挂 2–3 节，只飞到特写的话
 * 用户还得在右侧列表里自己找那一条——深链的意义就是替他把这一步做掉。
 */
const selectByNode = (nodeId: string) => {
  const section = sections.value.find((item) => item.points.some((tick) => tick.id === nodeId))
  if (!section) {
    // 目录还没到（或这一层没有这个点）：记下来，等 `loadAtlas` 收尾再试一次。
    pendingFocus = nodeId
    return
  }
  // 已经落在这一点上就别再飞：点刻度会 emit 出去同步地址栏，父级的 watch 又把
  // focus 传回来，少这道闸门就是"点一下抖两下"。
  if (activeTickId.value === nodeId && view.value === 'acupoint') return
  // **不能调 `selectAcupoint`**：那把 activeTickId 清成 null，深层链接落进 L2 时
  // 目标刻度没被选中，右侧一列 16 条长得一模一样，用户还得自己找那一条。
  // 这里只补相机，刻度归属由下面三行写死。
  selectedId.value = section.acupoint_id
  activeSectionId.value = section.id
  activeTickId.value = nodeId
  view.value = 'acupoint'
  const target = atlas.value?.acupoints.find((item) => item.id === section.acupoint_id)
  if (target && runtime.value) {
    runtime.value.flyTo(
      new Vector3(target.x3d, target.y3d, target.z3d),
      atlasConfig.camera.distance.acupoint,
      { fov: atlasConfig.camera.fov.acupoint },
    )
  }
}

defineExpose({ selectAcupoint, selectSection, selectTick, selectByNode, back, view })
</script>

<style scoped lang="scss">
.meridian-scene {
  position: relative;
  overflow: hidden;
  min-height: 640px;
  border: 1px solid var(--qh-border);
  border-radius: var(--qh-radius-stage);
  background: radial-gradient(120% 90% at 50% 40%, #163034 0%, #0e2226 46%, #060e10 100%);
  box-shadow: var(--qh-inner-highlight), var(--qh-shadow-deep);
  isolation: isolate;
  /* 舞台是一个**恒定的深色岛**：3D 底图永远是墨绿黑，不跟主题走。
     所以要在这里把「深色侧」那几支变量钉死，HUD 子组件才不用各写一套配色。
     注意**必须钉别名本身**（--qh-color-night / -night-soft），不能只钉底层的
     --qh-bg / --qh-surface：`--qh-color-night: var(--qh-bg)` 是在 :root 上求值的，
     在舞台子树里改 --qh-bg 一点用都没有。实测踩过：面板仍是米白、字已变浅色，
     结果是一排"看不见字的白条"。 */
  --qh-text: rgb(232 238 230 / 94%);
  --qh-text-muted: rgb(196 212 206 / 66%);
  --qh-fg-muted: rgb(196 212 206 / 66%);
  --qh-color-night: #061012;
  --qh-color-night-soft: #0a1a1d;
  --qh-border: color-mix(in srgb, var(--qh-color-jade) 20%, transparent);
  --stage-text: var(--qh-text);
  --stage-dim: var(--qh-text-muted);
  --stage-panel: rgb(6 18 20 / 74%);
  --stage-line: color-mix(in srgb, var(--qh-color-jade) 26%, transparent);
}

.canvas-host,
.label-host {
  position: absolute;
  inset: 0;
}

.canvas-host :deep(canvas) {
  display: block;
  width: 100%;
  height: 100%;
  touch-action: none;
}

.label-host {
  pointer-events: none;
}

.scene-loading {
  position: absolute;
  inset: 0;
  display: grid;
  place-content: center;
  gap: 8px;
  text-align: center;
}

.loading-title {
  margin: 0;
  color: color-mix(in srgb, var(--qh-color-cream) 70%, transparent);
  font-family: var(--qh-font-display);
  font-size: 26px;
  letter-spacing: 0.3em;
}

.loading-text {
  margin: 0;
  color: var(--qh-text-muted);
  font-size: 11px;
  letter-spacing: 0.16em;
}

.hud {
  position: absolute;
  z-index: 3;
}

.hud-top {
  top: var(--qh-space-4);
  left: var(--qh-space-4);
  display: flex;
  align-items: flex-start;
  gap: var(--qh-space-5);
}

.hud-kicker {
  margin: 0 0 4px;
  color: var(--qh-color-jade);
  font-size: 9px;
  font-weight: 800;
  letter-spacing: 0.22em;
}

.hud-title {
  margin: 0;
  color: var(--qh-color-cream);
  font-family: var(--qh-font-display);
  font-size: 18px;
  letter-spacing: 0.04em;
}

.hud-count {
  margin: 4px 0 0;
  color: var(--stage-dim);
  font-size: 10.5px;

  b {
    color: var(--qh-color-gold);
    font-weight: 600;
  }
}

.hud-actions {
  display: flex;
  gap: 5px;
  padding-top: 4px;

  button {
    padding: 4px 9px;
    border: 1px solid var(--stage-line);
    border-radius: var(--qh-radius-pill);
    background: var(--stage-panel);
    color: var(--stage-dim);
    font-size: 10.5px;
    cursor: pointer;

    &:hover,
    &:focus-visible {
      border-color: var(--qh-color-jade);
      color: var(--stage-text);
      outline: none;
    }

    &.is-on {
      border-color: var(--qh-color-jade);
      color: var(--stage-text);
      background: color-mix(in srgb, var(--qh-color-jade) 22%, var(--stage-panel));
    }
  }
}

.hud-layers {
  position: absolute;
  z-index: 3;
  top: var(--qh-space-4);
  right: var(--qh-space-4);
  justify-content: flex-end;
  max-width: 300px;
}

.hud-toggles {
  position: absolute;
  z-index: 3;
  top: 76px;
  left: var(--qh-space-4);
  display: grid;
  gap: 2px;
  padding: 7px 10px;
  border: 1px solid var(--stage-line);
  border-radius: var(--qh-radius-stage);
  background: var(--stage-panel);
  backdrop-filter: blur(8px);
  color: var(--stage-dim);
  font-size: 10.5px;

  label {
    display: flex;
    align-items: center;
    gap: 6px;
    cursor: pointer;
  }

  input {
    width: 12px;
    height: 12px;
    accent-color: #3fbfa0;

    &:disabled {
      opacity: 0.45;
    }
  }

  .toggle-hint {
    margin-left: 2px;
    color: color-mix(in srgb, var(--stage-dim) 60%, transparent);
    font-size: 9px;
  }
}

.hud-legend {
  position: absolute;
  z-index: 3;
  /* 底部路线条（.route-strip）常驻在 20px 处、实测高 68px（三行字 + 34px 刻度尺），
     图例必须让开它：78px 时实测被压 10px，条上"第 N 步"那行正好被盖住。
     102px = 26px 边距 + 68px 条高 + 8px 空隙；改条内行高时记得同步这个数。 */
  bottom: 102px;
  left: var(--qh-space-4);
  display: grid;
  grid-template-columns: repeat(2, auto);
  gap: 4px 14px;
  padding: 8px 10px;
  border: 1px solid var(--stage-line);
  border-radius: var(--qh-radius-stage);
  background: var(--stage-panel);
  backdrop-filter: blur(8px);
  font-size: 10px;

  p {
    grid-column: 1 / -1;
    margin: 0 0 2px;
    color: var(--stage-dim);
    font-size: 9px;
    font-weight: 800;
    letter-spacing: 0.12em;
  }

  span {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--stage-text);
    white-space: nowrap;
  }

  i {
    width: 9px;
    height: 9px;
    border: 1px solid var(--qh-color-jade);
    border-radius: 50%;

    &.is-lit {
      border-color: var(--qh-color-gold);
      background: var(--qh-color-gold);
    }

    &.is-current {
      box-shadow: 0 0 8px var(--qh-color-jade);
    }

    &.is-split {
      border-color: var(--qh-color-gold);
      border-style: dashed;
    }

    &.is-planned {
      opacity: 0.45;
    }

    &.is-deferred {
      border-color: var(--qh-color-mist);
      opacity: 0.4;
    }
  }
}

.hud-right {
  position: absolute;
  z-index: 3;
  /* 78px 是学科胶囊两行的高度 + 间隙。**必须让开**：上方是 .hud-layers，
     压过来会把刻度带的表头整个盖住（实测 L2 时"L2 · 逐点刻度"上面那行被裁掉一半）。 */
  top: 112px;
  right: var(--qh-space-4);
  /* 与 .hud-legend 同一个让位值：26px 边距 + 68px 条高 + 8px 空隙 */
  bottom: 102px;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
  /* 容器自己不出滚动条：刻度带固定高度、详情抽屉内部滚动。
     否则"内容超出"会表现为"被裁在舞台边缘"，看起来像样式坏了。 */
  overflow: hidden;
}

.hud-right :deep(.tick-band),
.hud-right :deep(.tick-point) {
  /* 刻度列与详情抽屉抢同一列高度：刻度最多占 44%（内部自己滚），
     剩下全给抽屉。不给上限的话 16 条刻度会把抽屉整个顶出可视区 ——
     而抽屉才是"点开知识点要读的正文"。 */
  flex: 0 1 auto;
  max-height: 44%;
  overflow: auto;
  overscroll-behavior: contain;
}

.hud-right :deep(.acu-sheet) {
  flex: 1 1 auto;
  min-height: 0;
}

.route-strip {
  position: absolute;
  z-index: 3;
  /* 右边留出键盘浏览入口的位置，否则条尾会被它压住 */
  right: 190px;
  /* 26px 而不是 20px：底下还要留一行"定位依据…未经临床校核"的免责声明，
     贴到 20px 时那行字上缘会顶在条的下边框上，读起来像被划掉。 */
  bottom: 26px;
  left: var(--qh-space-4);
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 7px 12px;
  border: 1px solid color-mix(in srgb, var(--qh-color-jade) 16%, transparent);
  border-radius: var(--qh-radius-stage);
  background: color-mix(in srgb, var(--qh-color-night) 76%, transparent);
  backdrop-filter: blur(10px);
}

/* 左段：第几步 / 从哪开始 / 之后去哪。三行合计约 46px，与刻度尺同高。 */
.strip-now {
  display: grid;
  flex: 0 0 auto;
  gap: 1px;
  width: min(46%, 400px);
}

.strip-step {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin: 0;
  color: var(--qh-text-muted);
  font-size: 9.5px;
  letter-spacing: 0.08em;

  b {
    color: var(--qh-color-gold);
    font-family: var(--qh-font-display);
    font-size: 11px;
    letter-spacing: 0.04em;
  }

  span {
    color: color-mix(in srgb, var(--qh-text-muted) 76%, transparent);
  }
}

.strip-line {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
  margin: 0;
  font-size: 11.5px;
  line-height: 1.35;

  em {
    flex: 0 0 auto;
    color: color-mix(in srgb, var(--qh-text-muted) 58%, transparent);
    font-size: 9px;
    font-style: normal;
    letter-spacing: 0.06em;
  }
}

.strip-link {
  overflow: hidden;
  min-width: 0;
  padding: 0;
  border: none;
  background: none;
  color: var(--qh-color-cream);
  font-family: var(--qh-font-display);
  font-size: 12.5px;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;

  /* 下一步比当前步淡一档：它们是两种不同的紧迫度，同色会让人分不清该点哪个 */
  &.is-next {
    color: color-mix(in srgb, var(--qh-color-cream) 66%, transparent);
  }

  &:hover,
  &:focus-visible {
    color: var(--qh-color-gold);
    outline: none;
    text-decoration: underline;
  }
}

.strip-meta {
  flex: 0 1 auto;
  overflow: hidden;
  color: var(--qh-text-muted);
  font-size: 9.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.strip-none {
  color: color-mix(in srgb, var(--qh-text-muted) 70%, transparent);
  font-size: 11px;
}

/* 刻度尺：一格一步。284 步装不进一屏，所以它自己横向滚、并自动把当前步居中。 */
.strip-ruler {
  position: relative;
  display: flex;
  flex: 1 1 auto;
  align-items: stretch;
  gap: 1px;
  min-width: 120px;
  height: 34px;
  margin: 0;
  padding: 0;
  overflow-x: auto;
  overflow-y: hidden;
  scrollbar-width: none;
  list-style: none;

  &::-webkit-scrollbar {
    display: none;
  }

  li {
    display: flex;
    flex: 0 0 auto;
  }
}

.ruler-tick {
  width: 3px;
  padding: 0;
  border: none;
  border-radius: 1px;
  background: color-mix(in srgb, var(--qh-text) 15%, transparent);
  cursor: pointer;
  transition: background 0.15s ease;

  /* 章的边界留一个缺口：同一章的各节连成一段，而不是 284 个等距格子 */
  &.is-chapter-start {
    margin-left: 4px;
  }

  &.is-done {
    background: color-mix(in srgb, var(--qh-color-gold) 58%, transparent);
  }

  &.is-current {
    width: 5px;
    background: var(--qh-color-jade);
    box-shadow: 0 0 8px color-mix(in srgb, var(--qh-color-jade) 70%, transparent);
  }

  &.is-next {
    background: color-mix(in srgb, var(--qh-color-jade) 52%, transparent);
  }

  /* 当前步之前还没学完的（跳着学的）：标成"待补"，不装成已完成 */
  &.is-pending {
    background: color-mix(in srgb, var(--qh-color-gold) 28%, transparent);
  }

  &:hover,
  &:focus-visible {
    background: var(--qh-color-cream);
    outline: none;
  }
}

.strip-hint {
  flex: 0 0 auto;
  margin: 0;
  color: color-mix(in srgb, var(--qh-text-muted) 60%, transparent);
  font-size: 9px;
  line-height: 1.4;
  text-align: right;
  /* 一行说完：折成三行时这条会从 52px 涨到 68px，把刻度尺挤窄、还压住图例 */
  white-space: nowrap;
}

.hud-precision {
  position: absolute;
  z-index: 3;
  /* 舞台卡片 overflow:hidden，2px 贴底时字的下缘会被切掉半个像素级像素 */
  bottom: 6px;
  left: 50%;
  margin: 0;
  color: color-mix(in srgb, var(--qh-text-muted) 55%, transparent);
  font-size: 9px;
  letter-spacing: 0.04em;
  transform: translateX(-50%);
  white-space: nowrap;
}

.hud-perf {
  position: absolute;
  z-index: 4;
  top: 4px;
  left: 50%;
  color: color-mix(in srgb, var(--qh-text-muted) 70%, transparent);
  font-size: 9.5px;
  letter-spacing: 0.08em;
  transform: translateX(-50%);
  white-space: nowrap;
}

@media (max-width: 900px) {
  .meridian-scene {
    min-height: 560px;
  }

  .hud-layers {
    max-width: 200px;
  }

  .hud-legend,
  .route-strip,
  .hud-precision {
    display: none;
  }

  .hud-right {
    top: auto;
    bottom: 52px;
    height: 42vh;
  }
}

/* 手机：舞台只剩三百来像素宽，桌面的"四角各一块面板"必然糊在人体上。
   这里改成"顶部一行 + 底部抽屉"：学科胶囊横滑一行，图层开关与键盘入口压到底边，
   刻度带与详情走全宽抽屉、占下半屏。 */
@media (max-width: 640px) {
  .meridian-scene {
    min-height: 520px;
  }

  .hud-top {
    top: 8px;
    right: 8px;
    left: 8px;
    gap: 8px;
    flex-wrap: wrap;
  }

  .hud-kicker,
  .hud-count {
    display: none;
  }

  .hud-title {
    font-size: 15px;
  }

  .hud-actions {
    padding-top: 0;

    button {
      padding: 3px 7px;
      font-size: 10px;
    }
  }

  .hud-layers {
    top: 50px;
    right: 8px;
    left: 8px;
    max-width: none;
    flex-wrap: nowrap;
    justify-content: flex-start;
    padding-bottom: 2px;
    overflow-x: auto;
    scrollbar-width: none;
  }

  /* 横滑行里的胶囊不许被压缩：`display:grid` + 默认允许收缩会把
     "中医基础理论" 压成一列竖排字（实测过）。 */
  .hud-layers :deep(.layer) {
    flex: 0 0 auto;
    white-space: nowrap;
  }

  .hud-toggles {
    top: auto;
    bottom: 46px;
    left: 8px;
    grid-template-columns: repeat(3, auto);
    gap: 0 10px;
    padding: 5px 8px;
    font-size: 10px;
  }

  /* 手机上只留三个图层开关：多一行就把脚上的穴盖住了，而脚背正好是
     至阴、太冲这些常查的穴。其余开关（未挂章节的穴 / 穴名）到近景再说。 */
  .hud-toggles label:nth-child(n + 4) {
    display: none;
  }

  .hud-right {
    top: auto;
    right: 8px;
    bottom: 46px;
    left: 8px;
    max-height: 46vh;
    height: auto;
  }

  .a11y-mirror {
    right: 8px;
    bottom: 8px;
  }

  .hud-perf {
    display: none;
  }
}
</style>
