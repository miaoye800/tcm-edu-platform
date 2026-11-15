<template>
  <span class="meridian-points" aria-hidden="true" />
</template>

<script setup lang="ts">
/**
 * 362 穴 → 670 个光点。**必须一次画完**：逐穴建 mesh 会产生 670 个 draw call，
 * 光提交批次就把移动端拖死。所以按视觉态分桶，每桶一个 `InstancedMesh`。
 *
 * 左右两侧共用一份进度（槽位按穴位建，不按左右实例建），右侧只是镜像出来的影像——
 * 否则会出现"左边学了右边没学"。
 */
import { onBeforeUnmount, onMounted, watch } from 'vue'
import * as THREE from 'three'
import { gsap } from 'gsap'
import atlasConfig from '../../config/meridian-atlas.json'
import type { MeridianAcupoint } from '../../services/meridianApi'
import type { AcupointPresence, Presence } from './meridianPresence'
import type { SceneRuntime } from './useMeridianScene'

const props = defineProps<{
  runtime: SceneRuntime
  acupoints: MeridianAcupoint[]
  presence: Map<string, AcupointPresence>
  showAll: boolean
  selected: string | null
  /** 中性化体表后需要改位的穴位（只有会阴）。为空时全部用导出的原始坐标。 */
  offsets?: Map<string, THREE.Vector3>
}>()

const RADIUS = atlasConfig.points.radius_m

interface Style {
  core: number
  coreOpacity: number
  coreScale: number
  ring: number | null
  ringOpacity: number
  ringScale: number
  arc?: boolean
  halo?: boolean
}

/** 视觉态 → 形状与颜色。和计划 §10.5 的表一一对应。 */
const STYLE: Record<Presence, Style> = {
  planned: { core: 0x7fa3a9, coreOpacity: 0.26, coreScale: 1, ring: 0x7fa3a9, ringOpacity: 0.38, ringScale: 1 },
  active: { core: 0x3fbfa0, coreOpacity: 0.62, coreScale: 1.1, ring: 0x3fbfa0, ringOpacity: 0.5, ringScale: 1 },
  current: { core: 0x8ff5dd, coreOpacity: 0.96, coreScale: 1.5, ring: 0x62e8c4, ringOpacity: 0.8, ringScale: 1.25, halo: true },
  lit: { core: 0xffd79a, coreOpacity: 1, coreScale: 1.55, ring: 0xe8b76a, ringOpacity: 1, ringScale: 1.2 },
  deferred: { core: 0x8b9aa0, coreOpacity: 0.2, coreScale: 1, ring: 0x8b9aa0, ringOpacity: 0.3, ringScale: 1 },
  // split：一穴挂多节、只点亮一部分。半环，不撒谎说"全绿"也不抹掉已有进度。
  split: { core: 0xc9a06a, coreOpacity: 0.62, coreScale: 1.2, ring: 0xe8b76a, ringOpacity: 0.75, ringScale: 1.1, arc: true },
  dormant: { core: 0x4a5a5f, coreOpacity: 0.18, coreScale: 1, ring: null, ringOpacity: 0, ringScale: 1 },
}

interface Instance {
  position: THREE.Vector3
  normal: THREE.Vector3
  id: string
}

const root = new THREE.Group()
root.name = 'acupoint-nodes'
const meshes: THREE.InstancedMesh[] = []
let pickables: Instance[] = []
let halo: THREE.Mesh | null = null
let selection: THREE.Mesh | null = null
let frameOff: (() => void) | undefined
let pulse = 0

const dummy = new THREE.Object3D()
const forward = new THREE.Vector3(0, 0, 1)

/** 体表法线：从身体中轴向外的方向。中线穴（x≈z≈0）直接朝前。 */
const surfaceNormal = (x: number, y: number, z: number) => {
  const vector = new THREE.Vector3(x, 0, z)
  if (vector.lengthSq() < 1e-8) return new THREE.Vector3(0, 0, 1)
  return vector.normalize()
}

const bucketOf = () => {
  const buckets = new Map<Presence, Instance[]>()
  for (const point of props.acupoints) {
    const presence = props.presence.get(point.id)?.presence ?? 'dormant'
    if (presence === 'dormant' && !props.showAll) continue
    // 绝大多数穴位直接用导出坐标；只有被中性化改动到的那几个（会阴）走 offsets，
    // 否则点会浮在抹平后的皮外或陷进体内。
    const moved = props.offsets?.get(point.id)
    const x = moved?.x ?? point.x3d
    const y = moved?.y ?? point.y3d
    const z = moved?.z ?? point.z3d
    const normal = surfaceNormal(x, y, z)
    const list = buckets.get(presence) ?? []
    list.push({ position: new THREE.Vector3(x, y, z), normal, id: point.id })
    if (point.side !== 'M') {
      list.push({
        position: new THREE.Vector3(-x, y, z),
        normal: new THREE.Vector3(-normal.x, normal.y, normal.z),
        id: point.id,
      })
    }
    buckets.set(presence, list)
  }
  return buckets
}

const makeCore = (instances: Instance[], style: Style) => {
  const geometry = new THREE.SphereGeometry(RADIUS, 8, 6)
  const material = new THREE.MeshBasicMaterial({
    color: style.core,
    transparent: true,
    opacity: style.coreOpacity,
    depthWrite: false,
  })
  const mesh = new THREE.InstancedMesh(geometry, material, instances.length)
  instances.forEach((item, index) => {
    dummy.position.copy(item.position)
    dummy.quaternion.identity()
    dummy.scale.setScalar(style.coreScale)
    dummy.updateMatrix()
    mesh.setMatrixAt(index, dummy.matrix)
  })
  mesh.instanceMatrix.needsUpdate = true
  mesh.frustumCulled = false
  mesh.renderOrder = 11
  root.add(mesh)
  meshes.push(mesh)
}

const makeRing = (instances: Instance[], style: Style) => {
  if (style.ring == null) return
  const inner = RADIUS * 1.9
  const outer = RADIUS * (style.arc ? 3.0 : 2.6)
  const geometry = new THREE.RingGeometry(
    inner,
    outer,
    24,
    1,
    0,
    style.arc ? Math.PI : Math.PI * 2,
  )
  const material = new THREE.MeshBasicMaterial({
    color: style.ring,
    transparent: true,
    opacity: style.ringOpacity,
    side: THREE.DoubleSide,
    depthWrite: false,
    blending: style.arc ? THREE.NormalBlending : THREE.AdditiveBlending,
  })
  const mesh = new THREE.InstancedMesh(geometry, material, instances.length)
  instances.forEach((item, index) => {
    dummy.position.copy(item.position).addScaledVector(item.normal, 0.0015)
    dummy.quaternion.setFromUnitVectors(forward, item.normal)
    dummy.scale.setScalar(style.ringScale)
    dummy.updateMatrix()
    mesh.setMatrixAt(index, dummy.matrix)
  })
  mesh.instanceMatrix.needsUpdate = true
  mesh.frustumCulled = false
  mesh.renderOrder = 10
  root.add(mesh)
  meshes.push(mesh)
}

const teardown = () => {
  for (const mesh of meshes) {
    root.remove(mesh)
    mesh.geometry.dispose()
    const material = mesh.material as THREE.Material | THREE.Material[]
    if (Array.isArray(material)) material.forEach((item) => item.dispose())
    else material.dispose()
  }
  meshes.splice(0, meshes.length)
  if (halo) {
    root.remove(halo)
    halo.geometry.dispose()
    ;(halo.material as THREE.Material).dispose()
    halo = null
  }
  if (selection) {
    root.remove(selection)
    selection.geometry.dispose()
    ;(selection.material as THREE.Material).dispose()
    selection = null
  }
}

const build = () => {
  teardown()
  const buckets = bucketOf()
  pickables = []
  for (const [presence, instances] of buckets) {
    if (!instances.length) continue
    makeCore(instances, STYLE[presence])
    makeRing(instances, STYLE[presence])
    pickables.push(...instances)
  }

  const current = buckets.get('current') ?? []
  if (current.length) {
    const first = current[0]
    // 当前穴的呼吸光环：单独一个 mesh 才能整体缩放（实例化网格改不了一个实例的 scale 动画）
    halo = new THREE.Mesh(
      new THREE.SphereGeometry(RADIUS * 3.4, 12, 10),
      new THREE.MeshBasicMaterial({
        color: 0x62e8c4,
        transparent: true,
        opacity: 0.3,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      }),
    )
    halo.position.copy(first.position)
    halo.renderOrder = 12
    root.add(halo)
  }
  updateSelection()
}

/** 选中环：金色双圈。选中的穴位可能已经点亮，没有这层就和 `lit` 分不出来。 */
const updateSelection = () => {
  if (selection) {
    root.remove(selection)
    selection.geometry.dispose()
    ;(selection.material as THREE.Material).dispose()
    selection = null
  }
  if (!props.selected) return
  const target = pickables.find((item) => item.id === props.selected)
  if (!target) return
  const mesh = new THREE.Mesh(
    new THREE.RingGeometry(RADIUS * 3.4, RADIUS * 4.4, 32),
    new THREE.MeshBasicMaterial({
      color: 0xffd79a,
      transparent: true,
      opacity: 0.85,
      side: THREE.DoubleSide,
      depthWrite: false,
    }),
  )
  mesh.position.copy(target.position).addScaledVector(target.normal, 0.002)
  mesh.quaternion.setFromUnitVectors(forward, target.normal)
  mesh.renderOrder = 13
  root.add(mesh)
  selection = mesh
  // 点亮回弹只跑一次（0.34s），不是每帧 tween：每帧驱动补间是 3D 掉帧最典型的来源。
  if (!props.runtime.reducedMotion) {
    gsap.fromTo(
      mesh.scale,
      { x: 0.55, y: 0.55, z: 0.55 },
      { x: 1, y: 1, z: 1, duration: 0.34, ease: 'back.out(1.7)' },
    )
  }
}

onMounted(() => {
  props.runtime.scene.add(root)
  build()
  frameOff = props.runtime.addFrame((dt) => {
    if (!halo) return
    pulse = (pulse + dt * 1.6) % (Math.PI * 2)
    const k = 1 + Math.sin(pulse) * 0.18
    halo.scale.setScalar(k)
    ;(halo.material as THREE.MeshBasicMaterial).opacity = 0.3 - Math.sin(pulse) * 0.1
  })
})

watch(
  () => [props.presence, props.showAll, props.offsets],
  () => build(),
)
watch(() => props.selected, () => updateSelection())

onBeforeUnmount(() => {
  frameOff?.()
  teardown()
  root.parent?.remove(root)
})

defineExpose({
  /** 屏幕空间拾取用的候选集合（含镜像实例）。 */
  pickables: () => pickables,
  count: () => pickables.length,
})
</script>

<style scoped lang="scss">
.meridian-points {
  display: none;
}
</style>
