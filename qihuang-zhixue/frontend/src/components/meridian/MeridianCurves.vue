<template>
  <span class="meridian-curves" aria-hidden="true" />
</template>

<script setup lang="ts">
/**
 * 经脉折线 + 已通段 + 气血流光。
 *
 * 三条 P0 结论直接落在这里：
 *
 * 1. **已通段不能按"路径采样点索引差 ≤ 3"判连续**。相邻两穴之间可能隔几十个采样点，
 *    这样判的结果是每段只剩一个点、`learnedSegments` 常年为 0。正确做法是把该经
 *    **所有穴位**映射到路径索引上排序，再看穴序列里相邻两个是否都已学。
 * 2. **相邻已通段必须先拼成一条连续点列再建曲线**，否则光球在每两个穴位之间顿一下。
 * 3. **线要抬离皮肤**（沿法向 0.3% 体高），否则和肌肉层 z-fighting，看起来"埋进肉里"。
 */
import { onBeforeUnmount, onMounted, watch } from 'vue'
import * as THREE from 'three'
import { Line2 } from 'three/addons/lines/Line2.js'
import { LineGeometry } from 'three/addons/lines/LineGeometry.js'
import { LineMaterial } from 'three/addons/lines/LineMaterial.js'
import atlasConfig from '../../config/meridian-atlas.json'
import type {
  MeridianAcupoint,
  MeridianChannel,
  MeridianPathData,
} from '../../services/meridianApi'
import type { AcupointPresence } from './meridianPresence'
import type { SceneRuntime } from './useMeridianScene'

const props = defineProps<{
  runtime: SceneRuntime
  channels: MeridianChannel[]
  paths: MeridianPathData[]
  acupoints: MeridianAcupoint[]
  presence: Map<string, AcupointPresence>
  focus: string | null
}>()

const LINE = atlasConfig.line
/** 沿经相邻两穴的直线间距超过这个值，说明是经脉的解剖跳跃（如委中→附分），
 *  分段时照样画（沿经折线是真的），但**不拼进同一条流光曲线**，免得光球横穿身体。 */
const JUMP_M = 0.25
const LIFT_M = 0.005

interface ChannelLine {
  channel: string
  line: Line2
  glow: Line2
  material: LineMaterial
  glowMaterial: LineMaterial
}

const root = new THREE.Group()
const litGroup = new THREE.Group()
root.name = 'meridian-curves'
litGroup.name = 'meridian-lit'

const lines: ChannelLine[] = []
let flow: THREE.Group | null = null
let learnedRuns: Run[] = []
let curve: THREE.CatmullRomCurve3 | null = null
let flowLength = 0
let flowT = 0
let frameOff: (() => void) | undefined
let resizeOff: (() => void) | undefined
let disposed = false

const byChannel = () => {
  const map = new Map<string, MeridianAcupoint[]>()
  for (const point of props.acupoints) {
    const list = map.get(point.channel_id)
    if (list) list.push(point)
    else map.set(point.channel_id, [point])
  }
  for (const list of map.values()) list.sort((a, b) => a.t - b.t)
  return map
}

const buildLines = (sign: 1 | -1, channelList: MeridianChannel[]) => {
  const { width, height } = props.runtime.size()
  const pathById = new Map(props.paths.map((item) => [item.id, item]))

  for (const channel of channelList) {
    const data = pathById.get(channel.id)
    if (!data || data.points.length < 2) continue
    const positions: number[] = []
    for (const [x, y, z] of data.points) positions.push(x * sign, y, z)

    const geometry = new LineGeometry()
    geometry.setPositions(positions)

    const material = new LineMaterial({
      color: 0x2a6a60,
      linewidth: LINE.idle_width_px,
      transparent: true,
      opacity: 0.26,
      worldUnits: false,
      alphaToCoverage: true,
    })
    material.resolution.set(width, height)

    const line = new Line2(geometry, material)
    line.computeLineDistances()
    line.frustumCulled = false
    line.renderOrder = 8
    line.name = 'is-unlearned'
    line.userData.channel = channel.id
    line.userData.sign = sign

    const glowMaterial = new LineMaterial({
      color: 0x62e8c4,
      linewidth: LINE.focus_glow_px,
      transparent: true,
      opacity: 0.07,
      worldUnits: false,
      depthWrite: false,
    })
    glowMaterial.resolution.set(width, height)
    const glow = new Line2(geometry, glowMaterial)
    glow.computeLineDistances()
    glow.frustumCulled = false
    glow.renderOrder = 7
    glow.visible = false
    glow.name = 'meridian-glow'

    root.add(glow)
    root.add(line)
    lines.push({ channel: channel.id, line, glow, material, glowMaterial })
  }
}

const distance = (a: MeridianAcupoint, b: MeridianAcupoint) =>
  Math.hypot(a.x3d - b.x3d, a.y3d - b.y3d, a.z3d - b.z3d)

/** 穴序列 → 路径索引。用最近采样点，阈值 7.7 cm（超过说明这一穴不在这条折线上）。 */
const indexOnPath = (data: MeridianPathData, point: MeridianAcupoint) => {
  let best = -1
  let bestD = 0.006
  data.points.forEach(([x, y, z], index) => {
    const d = (x - point.x3d) ** 2 + (y - point.y3d) ** 2 + (z - point.z3d) ** 2
    if (d < bestD) {
      bestD = d
      best = index
    }
  })
  return best
}

interface Run {
  channel: string
  points: number[]
}

/** 把已点亮穴位的相邻区间切出来，并按"共享端点"拼成连续点列。 */
const buildLearned = () => {
  litGroup.clear()
  const pathById = new Map(props.paths.map((item) => [item.id, item]))
  const grouped = byChannel()
  const runs: Run[] = []

  for (const [channelId, list] of grouped) {
    const data = pathById.get(channelId)
    if (!data || data.points.length < 2) continue
    const indexed = list
      .map((point) => ({ point, index: indexOnPath(data, point) }))
      .filter((item) => item.index >= 0)
      .sort((a, b) => a.index - b.index)
    if (indexed.length < 2) continue

    for (let i = 0; i + 1 < indexed.length; i += 1) {
      const a = indexed[i]
      const b = indexed[i + 1]
      if (props.presence.get(a.point.id)?.presence !== 'lit') continue
      if (props.presence.get(b.point.id)?.presence !== 'lit') continue
      if (b.index - a.index < 1) continue

      const slice: number[] = []
      for (let k = a.index; k <= b.index; k += 1) {
        const [x, y, z] = data.points[k]
        // 抬离皮肤：不然线会被肌肉层吃掉，看起来像埋在肉里
        slice.push(x, y + LIFT_M, z)
      }

      const last = runs[runs.length - 1]
      const continuous =
        last &&
        last.channel === channelId &&
        distance(a.point, b.point) <= JUMP_M &&
        last.points.length >= 3 &&
        Math.abs(last.points[last.points.length - 3] - slice[0]) < 1e-6 &&
        Math.abs(last.points[last.points.length - 2] - slice[1]) < 1e-6
      if (continuous) last.points.push(...slice.slice(3))
      else runs.push({ channel: channelId, points: slice })
    }
  }

  for (const run of runs) {
    for (const sign of [1, -1] as const) {
      const positions = run.points.map((value, index) =>
        index % 3 === 0 ? value * sign : value,
      )
      const geometry = new LineGeometry()
      geometry.setPositions(positions)
      const material = new LineMaterial({
        color: 0xe8b76e,
        linewidth: LINE.lit_width_px,
        transparent: true,
        opacity: 0.8,
        worldUnits: false,
      })
      const { width, height } = props.runtime.size()
      material.resolution.set(width, height)
      const line = new Line2(geometry, material)
      line.computeLineDistances()
      line.frustumCulled = false
      line.renderOrder = 10
      line.name = 'meridian-lit'
      litGroup.add(line)
    }
  }
  learnedRuns = runs
  return runs
}

const disposeGroup = (group: THREE.Group) => {
  group.traverse((object) => {
    const line = object as Line2
    line.geometry?.dispose()
    const material = line.material as THREE.Material | THREE.Material[] | undefined
    if (Array.isArray(material)) material.forEach((item) => item.dispose())
    else material?.dispose()
  })
  group.clear()
}

/**
 * 流光：把已通段里**最长的一条连续点列**建成 CatmullRomCurve3。
 * `flowSamples` 是曲线的控制点上限（不是粒子数）：12 个点足够顺，多了只是白算。
 */
const rebuildFlow = () => {
  if (flow) {
    disposeGroup(flow)
    flow.parent?.remove(flow)
    flow = null
  }
  curve = null
  if (props.runtime.reducedMotion) return

  // **流光只走已通段**。以前取"第一个已亮穴到最后一个已亮穴"的整段，
  // 中间没学的穴位也会被光球穿过——用户看到的是"我已经连到这里了"，
  // 而地图上那段明明是暗的。现在复用 buildLearned 的连续段（相邻两穴都学过）。
  const runs = learnedRuns
  if (!runs.length) return

  const longest = runs.reduce((best, run) => (run.points.length > best.points.length ? run : best))
  const step = Math.max(1, Math.ceil(longest.points.length / 3 / LINE.flow_samples))
  const controls: THREE.Vector3[] = []
  for (let i = 0; i < longest.points.length; i += step * 3) {
    const [x, y, z] = longest.points.slice(i, i + 3)
    controls.push(new THREE.Vector3(x, y, z))
  }
  if (controls.length < 2) return
  curve = new THREE.CatmullRomCurve3(controls, false, 'catmullrom', 0.5)
  flowLength = curve.getLength()

  const group = new THREE.Group()
  group.name = 'meridian-flow'
  // 核心 + 光晕两层：单独一颗小球会被 2.6px 金线完全吃掉，等于看不见；
  // 光晕是 additive + bloom 双重放大，尺寸和浓度都要压着，否则烫出个白团。
  const core = new THREE.Mesh(
    new THREE.SphereGeometry(0.007, 12, 10),
    new THREE.MeshBasicMaterial({ color: 0xffeec2 }),
  )
  const halo = new THREE.Mesh(
    new THREE.SphereGeometry(0.022, 12, 10),
    new THREE.MeshBasicMaterial({
      color: 0xe8a94e,
      transparent: true,
      opacity: 0.2,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    }),
  )
  core.renderOrder = 14
  halo.renderOrder = 13
  group.add(core, halo)
  props.runtime.scene.add(group)
  flow = group
}

const applyFocus = () => {
  for (const item of lines) {
    const isFocus = props.focus != null && item.channel === props.focus
    const all = props.focus == null
    item.material.color.set(all ? 0x24625a : isFocus ? 0x3fbfa0 : 0x2a6a60)
    item.material.linewidth = all ? 1.05 : isFocus ? LINE.focus_width_px : LINE.idle_width_px
    item.material.opacity = all ? 0.6 : isFocus ? 0.95 : 0.24
    item.line.name = isFocus || all ? 'meridian-line' : 'is-unlearned'
    item.glow.visible = isFocus
  }
}

onMounted(() => {
  props.runtime.scene.add(root)
  props.runtime.scene.add(litGroup)
  // 中线（任/督）镜像会得到两条重合线，直接跳过：x 全为 0 的经不需要右侧副本
  const midline = new Set(
    props.paths
      .filter((item) => item.points.every((point) => Math.abs(point[0]) < 0.01))
      .map((item) => item.id),
  )
  buildLines(1, props.channels)
  buildLines(-1, props.channels.filter((channel) => !midline.has(channel.id)))

  buildLearned()
  rebuildFlow()
  applyFocus()

  frameOff = props.runtime.addFrame((dt) => {
    if (!curve || !flow) return
    // 速度按弧长算，不按 t：不同经的长度差 6 倍（任脉 0.8 m / 膀胱经 4.33 m），
    // 按 t 走会让长经的光球飞、短经的爬。
    flowT = (flowT + (dt * 0.32) / Math.max(flowLength, 0.2)) % 1
    flow.position.copy(curve.getPointAt(flowT))
    const pulse = 1 + Math.sin(flowT * Math.PI * 2 * 12) * 0.16
    flow.scale.setScalar(pulse)
  })

  resizeOff = props.runtime.addResize((width, height) => {
    for (const item of lines) {
      item.material.resolution.set(width, height)
      item.glowMaterial.resolution.set(width, height)
    }
    litGroup.traverse((object) => {
      const material = (object as Line2).material as LineMaterial | undefined
      material?.resolution?.set(width, height)
    })
  })
})

watch(
  () => [props.focus, props.channels.length],
  () => applyFocus(),
)
watch(
  () => props.presence,
  () => {
    buildLearned()
    rebuildFlow()
  },
)

onBeforeUnmount(() => {
  disposed = true
  frameOff?.()
  resizeOff?.()
  if (flow) {
    disposeGroup(flow)
    flow.parent?.remove(flow)
    flow = null
  }
  disposeGroup(root)
  disposeGroup(litGroup)
  root.parent?.remove(root)
  litGroup.parent?.remove(litGroup)
})
defineExpose({ lineCount: () => lines.length, disposed: () => disposed })
</script>

<style scoped lang="scss">
.meridian-curves {
  display: none;
}
</style>
