<template>
  <span class="meridian-body" aria-hidden="true" />
</template>

<script setup lang="ts">
/**
 * 人体层：**只有体表**。
 *
 * P10 之前这里还有肌肉与骨骼两层。下架的理由不是"不好看"，是**几何本身不成立**：
 * 肌肉层是 `voxel_cluster(7.5mm)` 的简化产物，薄肌腹被磨穿、肌群之间没有筋膜填充，
 * 凑到 1.25 m 的章节视距就是一块块断开的红肉（"稀疏、撕裂"）。补面补不回来——
 * 缺的是几何，不是材质。而 362 个穴位坐标是吸附在皮肤（FJ2810）三角形上的，
 * 经脉折线也走体表，**皮肤才是穴位这一概念的真实载体**，肌肉是画在里面一层。
 *
 * 留一条注释而不是把文件删掉：`app/meridian/asset.py` 仍会打包 muscle/skeleton，
 * 将来若要做"刺入深度"这类教学动画，重新接进来只要加一个 mesh。
 */
import { onBeforeUnmount, onMounted } from 'vue'
import * as THREE from 'three'
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js'
import atlasConfig from '../../config/meridian-atlas.json'
import type { MeridianAcupoint, MeridianAsset } from '../../services/meridianApi'
import { neutralSurface } from './neutralize'
import type { SceneRuntime } from './useMeridianScene'

const props = defineProps<{
  runtime: SceneRuntime
  assets: MeridianAsset[]
  acupoints: MeridianAcupoint[]
}>()

const emit = defineEmits<{
  (event: 'status', value: string): void
  /** 中性化后需要改位的穴位（只有会阴会被改到），交给 AcupointNodes 用 */
  (event: 'surface', value: Map<string, THREE.Vector3>): void
}>()

const ANATOMY = atlasConfig.anatomy
const meshes: Record<string, THREE.Mesh | undefined> = {}
const pending = new Set<string>()
let frameOff: (() => void) | undefined
let fillLight: THREE.DirectionalLight | undefined
let disposed = false

const hex = (value: string) => new THREE.Color(value)

/**
 * 三维值噪声。皮肤没有 UV（`atlas.json` 里只有 position / normal / index），
 * 斑驳只能 triplanar 取——频率高了就是砂纸，所以皮肤用低两个数量级的 scale。
 */
const NOISE_GLSL = `
        float h31(vec3 p){
          p = fract(p * 0.3183099 + vec3(0.11, 0.17, 0.13));
          p *= 17.0;
          return fract(p.x * p.y * p.z * (p.x + p.y + p.z));
        }
        float vn(vec3 x){
          vec3 i = floor(x), f = fract(x);
          f = f * f * (3.0 - 2.0 * f);
          float n000 = h31(i);
          float n100 = h31(i + vec3(1,0,0));
          float n010 = h31(i + vec3(0,1,0));
          float n110 = h31(i + vec3(1,1,0));
          float n001 = h31(i + vec3(0,0,1));
          float n101 = h31(i + vec3(1,0,1));
          float n011 = h31(i + vec3(0,1,1));
          float n111 = h31(i + vec3(1,1,1));
          return mix(mix(mix(n000,n100,f.x), mix(n010,n110,f.x), f.y),
                     mix(mix(n001,n101,f.x), mix(n011,n111,f.x), f.y), f.z);
        }`

/**
 * 皮肤材质。**这是默认的那具身体**，也是唯一一层。
 *
 * 深度冲突：经脉线与穴位点都贴在皮肤表面上，同深度会 z-fighting 被切成虚线。
 * 用 `polygonOffset` 把皮肤往深度里推一格，贴在表面上的线就稳定压在皮上。
 */
const makeSkinMaterial = () => {
  const spec = ANATOMY.layers.skin
  const texture = spec.texture
  const material = new THREE.MeshStandardMaterial({
    color: hex(spec.color),
    roughness: spec.roughness,
    metalness: spec.metalness,
    // 让皮肤在深度上退一格：经脉线 / 穴位环都画在同一张表面上
    polygonOffset: true,
    polygonOffsetFactor: 1,
    polygonOffsetUnits: 1,
    side: THREE.FrontSide,
  })

  material.onBeforeCompile = (shader) => {
    shader.uniforms.uScale = { value: texture.scale }
    shader.uniforms.uBump = { value: texture.bump }

    shader.vertexShader = shader.vertexShader
      .replace(
        '#include <common>',
        `#include <common>
        varying vec3 vWPos;`,
      )
      .replace(
        '#include <begin_vertex>',
        `#include <begin_vertex>
        vWPos = (modelMatrix * vec4(transformed, 1.0)).xyz;`,
      )

    shader.fragmentShader = shader.fragmentShader
      .replace(
        '#include <common>',
        `#include <common>
        varying vec3 vWPos;
        uniform float uScale, uBump;
        ${NOISE_GLSL}`,
      )
      .replace(
        '#include <color_fragment>',
        `#include <color_fragment>
        {
          // 低频斑驳：皮肤没有 UV，靠世界坐标取噪声；频率高了就是砂纸。
          float mottle = vn(vWPos * uScale) * 0.6 + vn(vWPos * uScale * 3.1 + 7.0) * 0.4;
          diffuseColor.rgb *= mix(0.92, 1.06, mottle);
          // 腋下/腹股沟这类凹陷处自然压暗一点，避免整具身体像塑料模特。
          float cavity = smoothstep(0.05, 0.55, vWPos.y);
          diffuseColor.rgb *= mix(0.94, 1.0, cavity);
        }`,
      )
      .replace(
        '#include <normal_fragment_maps>',
        `#include <normal_fragment_maps>
        {
          vec3 p = vWPos * uScale * 4.0;
          float e = 0.08;
          float f0 = vn(p);
          vec3 g = vec3(vn(p + vec3(e,0,0)) - f0,
                        vn(p + vec3(0,e,0)) - f0,
                        vn(p + vec3(0,0,e)) - f0);
          vec3 tangential = g - normal * dot(g, normal);
          vec3 perturbed = normal - tangential * uBump * 3.0;
          float pl = length(perturbed);
          normal = pl > 1e-5 ? perturbed / pl : normal;
        }`,
      )
  }
  return material
}
const addLights = () => {
  const { lights } = ANATOMY
  const hemisphere = new THREE.HemisphereLight(
    hex(lights.hemisphere.sky),
    hex(lights.hemisphere.ground),
    lights.hemisphere.intensity,
  )
  props.runtime.scene.add(hemisphere)

  for (const key of ['key', 'fill', 'rim', 'up'] as const) {
    const spec = lights[key]
    const light = new THREE.DirectionalLight(hex(spec.color), spec.intensity)
    light.position.set(...(spec.position as [number, number, number]))
    props.runtime.scene.add(light)
  }

  // 跟随相机的补光：转到背面/侧面时 key 光被身体挡住，整块会黑。
  // 强度压低，只负责"看得见"，造型仍归固定的三点光。
  fillLight = new THREE.DirectionalLight(hex(lights.camera_fill.color), lights.camera_fill.intensity)
  props.runtime.scene.add(fillLight)
  props.runtime.scene.add(fillLight.target)
  frameOff = props.runtime.addFrame(() => {
    if (!fillLight) return
    fillLight.position.copy(props.runtime.camera.position)
    fillLight.target.position.copy(props.runtime.controls.target)
  })
}

const buildSkin = async () => {
  if (meshes.skin || pending.has('skin') || disposed) return
  const asset = props.assets.find((item) => item.name === 'skin')
  if (!asset) return
  pending.add('skin')
  emit('status', '铺设体表…')
  try {
    // 中性化（抹掉下体）与皮肤解析都在 neutralize 里做完，这里只负责挂上去；
    // AcupointNodes 等的是同一个 Promise，不会重复解析与变形。
    const surface = await neutralSurface(asset, props.acupoints)
    if (disposed) return
    // 资产在后端已经按系统合并过一次；这里保留 merge 是为了"按块流式加载"那条后路
    // （皮肤拆 part 后仍旧一次 draw call），单块时直接透传，不多花一次拷贝。
    //
    // 注意 `surface.geometry` 是 neutralize 的缓存，**不能在这里 dispose**：
    // 组件重新挂载会拿到同一份，dispose 掉的是别人还要用的缓冲。
    const merged = mergeGeometries([surface.geometry], false) ?? surface.geometry.clone()

    const mesh = new THREE.Mesh(merged, makeSkinMaterial())
    mesh.frustumCulled = false
    mesh.name = 'body:skin'
    mesh.renderOrder = 6
    props.runtime.scene.add(mesh)
    meshes.skin = mesh
    emit('surface', surface.offsets)
  } finally {
    pending.delete('skin')
  }
}

onMounted(async () => {
  addLights()
  await buildSkin()
})

onBeforeUnmount(() => {
  disposed = true
  frameOff?.()
  for (const [name, mesh] of Object.entries(meshes)) {
    mesh?.parent?.remove(mesh)
    mesh?.geometry.dispose()
    const material = mesh?.material
    if (Array.isArray(material)) material.forEach((item) => item.dispose())
    else material?.dispose()
    delete meshes[name]
  }
})
</script>

<style scoped lang="scss">
.meridian-body {
  display: none;
}
</style>
