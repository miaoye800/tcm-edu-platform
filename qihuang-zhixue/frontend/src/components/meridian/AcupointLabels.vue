<template>
  <span class="meridian-labels" aria-hidden="true" />
</template>

<script setup lang="ts">
/**
 * 穴名标签（CSS2DObject）。
 *
 * 两个坑都不能绕：
 * 1. **CSS2DObject 不参与深度测试**。转到背面时，正面的穴名会"透过身体"显示出来，
 *    比不显示更糟。所以每个标签都记住穴位体表法线，背向相机就隐藏。
 * 2. **3D 里没有布局可以依赖**。670 个穴名全挂必然糊成一团，只能挂当前经 + 屏幕空间
 *    纵向推开（`avoidOverlap`）。这是 3D 特有的账，2D 里靠排版就解决了。
 */
import { onBeforeUnmount, onMounted, watch } from 'vue'
import * as THREE from 'three'
import { CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js'
import atlasConfig from '../../config/meridian-atlas.json'
import type { MeridianAcupoint } from '../../services/meridianApi'
import type { AcupointPresence } from './meridianPresence'
import type { SceneRuntime } from './useMeridianScene'

const props = defineProps<{
  runtime: SceneRuntime
  acupoints: MeridianAcupoint[]
  presence: Map<string, AcupointPresence>
  focus: string | null
  visible: boolean
}>()

const LABEL = atlasConfig.points.label
const labels: Array<{ object: CSS2DObject; element: HTMLElement; normal: THREE.Vector3 }> = []
let frameOff: (() => void) | undefined

const projected = new THREE.Vector3()
const toCamera = new THREE.Vector3()

const clear = () => {
  for (const item of labels) {
    item.object.removeFromParent()
    item.element.remove()
  }
  labels.splice(0, labels.length)
}

const build = () => {
  clear()
  if (!props.visible) return
  const pool = props.focus
    ? props.acupoints.filter((point) => point.channel_id === props.focus)
    : [...props.acupoints]
  // 只挂一侧：左右同名标签会重叠成双影
  const list = pool.filter((point) => point.side !== 'R').slice(0, LABEL.max)

  for (const point of list) {
    const lit = props.presence.get(point.id)?.presence === 'lit'
    const element = document.createElement('div')
    element.className = `meridian-label${lit ? ' is-lit' : ''}`
    element.textContent = point.name
    const code = document.createElement('small')
    code.textContent = point.code
    element.appendChild(code)

    const object = new CSS2DObject(element)
    object.position.set(point.x3d, point.y3d + LABEL.offset_y_m, point.z3d)
    const normal = new THREE.Vector3(point.x3d, 0, point.z3d)
    if (normal.lengthSq() < 1e-8) normal.set(0, 0, 1)
    normal.normalize()
    props.runtime.scene.add(object)
    labels.push({ object, element, normal })
  }
}

/**
 * 屏幕空间避让：按屏幕 y 排序后逐个下推。24px 的命中容差、19px 的行距是 P0 实拍值——
 * 更小会叠字，更大会把一整条经的标签拖到画面外。
 */
const avoidOverlap = () => {
  const { width, height } = props.runtime.size()
  const camera = props.runtime.camera
  toCamera.subVectors(camera.position, props.runtime.controls.target).normalize()
  const live: Array<{ element: HTMLElement; x: number; y: number }> = []

  for (const item of labels) {
    // 背向相机的标签直接隐身，否则会"透过身体"浮在正面
    if (item.normal.dot(toCamera) < LABEL.normal_cull) {
      item.element.style.opacity = '0'
      continue
    }
    item.object.getWorldPosition(projected).project(camera)
    if (projected.z > 1) {
      item.element.style.opacity = '0'
      continue
    }
    live.push({
      element: item.element,
      x: (projected.x * 0.5 + 0.5) * width,
      y: (-projected.y * 0.5 + 0.5) * height,
    })
  }

  live.sort((a, b) => a.y - b.y)
  const placed: Array<{ x: number; y: number }> = []
  for (const item of live) {
    let y = item.y
    for (let guard = 0; guard < 6; guard += 1) {
      const hit = placed.find(
        (other) => Math.abs(other.x - item.x) < LABEL.avoid_px && Math.abs(other.y - y) < LABEL.row_px,
      )
      if (!hit) break
      y = hit.y + LABEL.row_px
    }
    placed.push({ x: item.x, y })
    item.element.style.opacity = ''
    item.element.style.transform = `translate(-50%,-50%) translate(${item.x.toFixed(1)}px, ${y.toFixed(1)}px)`
  }
}

onMounted(() => {
  build()
  frameOff = props.runtime.addFrame(() => {
    if (labels.length) avoidOverlap()
  })
})

watch(
  () => [props.focus, props.visible, props.acupoints.length],
  () => build(),
)

onBeforeUnmount(() => {
  frameOff?.()
  clear()
})
</script>

<style scoped lang="scss">
.meridian-labels {
  display: none;
}

:global(.meridian-label) {
  position: absolute;
  padding: 1px 5px;
  border: 1px solid color-mix(in srgb, var(--qh-color-jade) 16%, transparent);
  border-radius: 3px;
  background: rgb(8 18 20 / 42%);
  color: rgb(226 222 210 / 72%);
  font-size: 10px;
  letter-spacing: 0.04em;
  white-space: nowrap;
  text-shadow: 0 1px 3px rgb(0 0 0 / 90%);
  transform: translate(-50%, -50%);
  transition: opacity 0.2s;

  small {
    margin-left: 3px;
    font-size: 9px;
    opacity: 0.55;
  }

  &.is-lit {
    border-color: rgb(232 181 90 / 50%);
    background: rgb(38 20 8 / 62%);
    color: #ffd9a0;
  }
}
</style>
