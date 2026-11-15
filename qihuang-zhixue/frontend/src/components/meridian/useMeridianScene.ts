/**
 * 3D 场景的运行时容器：渲染器 / 相机 / 控制器 / 后处理 / 帧循环 / 资源释放。
 *
 * 三条硬约束（P0 验收记录 §3 踩过的）写在这里，子组件不许绕开：
 *
 * 1. **Three 对象不能进 Vue 响应式代理**。矩阵一旦被 Proxy 包住，读取时会走 getter，
 *    表现为模型闪烁 / 消失。所有持有者都必须 `shallowRef` 或 `markRaw`。
 * 2. **动画进度用真实时间**，不要累加 `dt`——累加在低帧率机器上会慢到几乎不动。
 * 3. **`renderer.info` 每帧手动 reset**。接上 EffectComposer 之后它记的是最后一个
 *    全屏 pass（`1 tris · 1 calls`），不 reset 就等于没测。
 */
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { CSS2DRenderer } from 'three/addons/renderers/CSS2DRenderer.js'
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js'
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js'
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js'
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js'
import atlasConfig from '../../config/meridian-atlas.json'

export type QualityTier = 'desktop' | 'mobile' | 'low'
export type ViewMode = 'overview' | 'chapter' | 'acupoint'
export type ViewName = 'front' | 'back' | 'side'

export interface SceneStats {
  fps: number
  worstFps: number
  calls: number
  triangles: number
}

/** 辉光链的精度档：`half-float` 最准，`rgba8` 是降级，`off` 表示整条后期链关掉。 */
export type BloomMode = 'half-float' | 'rgba8' | 'off'

export interface SceneRuntime {
  scene: THREE.Scene
  camera: THREE.PerspectiveCamera
  renderer: THREE.WebGLRenderer
  labelRenderer: CSS2DRenderer
  controls: OrbitControls
  composer: EffectComposer | null
  bloom: UnrealBloomPass | null
  /** 实际启用的辉光档位。验收脚本据此判断「本机到底跑没跑 bloom」。 */
  bloomMode: BloomMode
  quality: QualityTier
  reducedMotion: boolean
  size: () => { width: number; height: number }
  addFrame: (fn: (dt: number, now: number) => void) => () => void
  addResize: (fn: (width: number, height: number) => void) => () => void
  goView: (name: ViewName, opts?: { duration?: number }) => void
  flyTo: (
    target: THREE.Vector3,
    distance: number,
    opts?: { duration?: number; fov?: number },
  ) => void
  panBy: (dxPx: number, dyPx: number) => void
  stats: () => SceneStats
  onStats: (fn: ((stats: SceneStats) => void) | null) => void
  dispose: () => void
}

const CAM = atlasConfig.camera
const RENDER = atlasConfig.render

const prefersReducedMotion = () =>
  typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

/** 画质档位只看三件事：指针粗细、核数、内存。不猜 UA。 */
export const detectQuality = (): QualityTier => {
  if (typeof window === 'undefined') return 'desktop'
  const coarse = window.matchMedia('(pointer: coarse)').matches
  const cores = navigator.hardwareConcurrency ?? 4
  const memory = (navigator as Navigator & { deviceMemory?: number }).deviceMemory ?? 4
  if (coarse && (cores <= 4 || memory <= 2)) return 'low'
  if (coarse || cores <= 4) return 'mobile'
  return 'desktop'
}

export const qualityProfile = (tier: QualityTier) => atlasConfig.quality[tier] as {
  pixel_ratio_cap: number
  bloom: boolean
}

const easeInOutCubic = (k: number) => (k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2)

const makeBackdrop = (): THREE.Texture | THREE.Color => {
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = 512
  const ctx = canvas.getContext('2d')
  if (!ctx) return new THREE.Color(RENDER.background)
  const gradient = ctx.createRadialGradient(256, 300, 40, 256, 300, 330)
  gradient.addColorStop(0, '#163034')
  gradient.addColorStop(0.42, '#0e2226')
  gradient.addColorStop(1, '#060e10')
  ctx.fillStyle = gradient
  ctx.fillRect(0, 0, 512, 512)
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  return texture
}

export const createMeridianScene = (host: HTMLElement, labelHost: HTMLElement): SceneRuntime => {
  const quality = detectQuality()
  const profile = qualityProfile(quality)
  const reducedMotion = prefersReducedMotion()

  const renderer = new THREE.WebGLRenderer({
    antialias: quality !== 'low',
    alpha: false,
    powerPreference: 'high-performance',
  })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, profile.pixel_ratio_cap))
  renderer.outputColorSpace = THREE.SRGBColorSpace
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.toneMappingExposure = RENDER.exposure
  renderer.info.autoReset = false
  renderer.domElement.className = 'meridian-canvas'
  // 画布对读屏软件是空白，它读到的只有一堆 0。语义由 A11yMirror 提供。
  renderer.domElement.setAttribute('aria-hidden', 'true')
  host.appendChild(renderer.domElement)

  const scene = new THREE.Scene()
  scene.background = makeBackdrop()
  scene.fog = new THREE.FogExp2(new THREE.Color(RENDER.background).getHex(), RENDER.fog_density)

  const camera = new THREE.PerspectiveCamera(CAM.fov.overview, 1, 0.05, 40)
  camera.position.set(...(CAM.views.front.position as [number, number, number]))

  const controls = new OrbitControls(camera, renderer.domElement)
  controls.target.set(...(CAM.views.front.target as [number, number, number]))
  controls.enableDamping = true
  controls.dampingFactor = 0.075
  controls.minDistance = 0.42
  controls.maxDistance = 5.2
  controls.enablePan = true
  controls.screenSpacePanning = true
  controls.panSpeed = 1.25
  controls.zoomToCursor = true
  controls.mouseButtons = {
    LEFT: THREE.MOUSE.ROTATE,
    MIDDLE: THREE.MOUSE.DOLLY,
    RIGHT: THREE.MOUSE.PAN,
  }

  const labelRenderer = new CSS2DRenderer({ element: labelHost })
  labelRenderer.domElement.style.position = 'absolute'
  labelRenderer.domElement.style.inset = '0'
  labelRenderer.domElement.style.pointerEvents = 'none'

  let composer: EffectComposer | null = null
  let bloom: UnrealBloomPass | null = null

  /* ---------------------------------------------------------------- 尺寸 */
  const size = { width: 1, height: 1 }
  const resizeFns = new Set<(w: number, h: number) => void>()

  const applySize = (width: number, height: number) => {
    const nextWidth = Math.max(1, Math.round(width))
    const nextHeight = Math.max(1, Math.round(height))
    const changed = nextWidth !== size.width || nextHeight !== size.height
    size.width = nextWidth
    size.height = nextHeight
    camera.aspect = size.width / size.height
    camera.updateProjectionMatrix()
    renderer.setSize(size.width, size.height, false)
    // 只在尺寸真的变了才重建后期链。UnrealBloomPass.setSize 会重造一整组渲染目标，
    // 尺寸没变时重复调用只会平白抖一次显存，而且实测会抖出黑屏。
    if (changed) {
      composer?.setSize(size.width, size.height)
      bloom?.setSize(size.width, size.height)
    }
    labelRenderer.setSize(size.width, size.height)
    resizeFns.forEach((fn) => fn(size.width, size.height))
  }

  const measure = () => {
    const rect = host.getBoundingClientRect()
    applySize(rect.width || 1, rect.height || 1)
  }
  measure()

  /* ---------------------------------------------------------------- 后处理
     **必须在首次 measure 之后再建**。EffectComposer 在构造时按当时画布的
     drawingBuffer 尺寸分配渲染目标；先建后 resize 的话，UnrealBloomPass 的
     多级模糊链会带着创建时的尺寸，最终把整帧输出成黑色。

     另一半坑更难查：只要场景里有一处材质写进 **NaN/Inf**，半浮点的模糊链会把它
     顺着 mip 链加性地摊满整帧，最终 `OutputPass` 写出去的是 NaN —— 表现为
     **整屏透明**（读回 alpha=0），而且 `gl.getError()` 返回 0、console 一条报错都没有。
     实测触发源是肌肉层的 319 个零长度法线（`normalize` → NaN）。
     两件事都做：
       1. 把 NaN 源头掐掉（法线在打包端与前端各兜一层，见 `BodyMesh.vue`）；
       2. 这一条梯子留着兜底：半浮点 → 全 RGBA8（RGBA8 写入时会把 NaN 压成 0，
          所以它天然免疫）→ 关掉后期链。最坏只是没有辉光，绝不留黑屏。 */
  let bloomMode: BloomMode = 'off'
  let verifiedWithContent = false

  const buildPipeline = (lowPrecision: boolean) => {
    const nextComposer = new EffectComposer(renderer)
    nextComposer.addPass(new RenderPass(scene, camera))
    const nextBloom = new UnrealBloomPass(
      new THREE.Vector2(size.width, size.height),
      RENDER.bloom.strength,
      RENDER.bloom.radius,
      RENDER.bloom.threshold,
    )
    if (lowPrecision) {
      // 纹理还没上传，这个时候改 type 有效（换完再改就需要重建整组 RT 了）。
      nextBloom.renderTargetBright.texture.type = THREE.UnsignedByteType
      for (const target of [...nextBloom.renderTargetsHorizontal, ...nextBloom.renderTargetsVertical]) {
        target.texture.type = THREE.UnsignedByteType
      }
    }
    nextComposer.addPass(nextBloom)
    nextComposer.addPass(new OutputPass())
    return { nextComposer, nextBloom }
  }

  /** 刚渲完一帧后读回画布：三点全是 alpha=0，说明这一档根本没写出画面。 */
  const canvasHasContent = () => {
    const gl = renderer.getContext()
    const width = gl.drawingBufferWidth
    const height = gl.drawingBufferHeight
    const pixel = new Uint8Array(4)
    for (const ratio of [0.5, 0.72, 0.28]) {
      gl.readPixels(width >> 1, Math.round(height * ratio), 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel)
      if (pixel[3] > 0) return true
    }
    return false
  }

  const teardownPipeline = () => {
    bloom?.dispose()
    composer?.dispose()
    composer = null
    bloom = null
    bloomMode = 'off'
  }

  /** 试一档：真的渲一帧再读像素，通过了才装上。 */
  const tryPipeline = (lowPrecision: boolean) => {
    const built = buildPipeline(lowPrecision)
    if (!pipelineDrawsFrame(built.nextComposer)) {
      built.nextBloom.dispose()
      built.nextComposer.dispose()
      return false
    }
    composer = built.nextComposer
    bloom = built.nextBloom
    bloomMode = lowPrecision ? 'rgba8' : 'half-float'
    return true
  }

  function pipelineDrawsFrame(candidate: EffectComposer) {
    candidate.render()
    return canvasHasContent()
  }

  if (profile.bloom && !reducedMotion && !tryPipeline(false)) {
    tryPipeline(true)
  }

  const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(measure) : null
  observer?.observe(host)
  window.addEventListener('resize', measure)

  /* ---------------------------------------------------------------- 平移
     只给旋转 + 缩放时，从头顶想看脚只有"缩小→重新找到脚→点进去"一条路，
     那是换视角不是挪画面。临床看经脉是沿一条线上下读的，必须能顺着身体滑。 */
  const panBox = CAM.pan_box
  const right = new THREE.Vector3()
  const up = new THREE.Vector3()

  const clampPan = () => {
    const t = controls.target
    const cx = THREE.MathUtils.clamp(t.x, -panBox.x, panBox.x)
    const cy = THREE.MathUtils.clamp(t.y, panBox.y_min, panBox.y_max)
    const cz = THREE.MathUtils.clamp(t.z, -panBox.z, panBox.z)
    if (cx === t.x && cy === t.y && cz === t.z) return
    camera.position.x += cx - t.x
    camera.position.y += cy - t.y
    camera.position.z += cz - t.z
    t.set(cx, cy, cz)
  }

  const panBy = (dxPx: number, dyPx: number) => {
    const distance = camera.position.distanceTo(controls.target)
    const perPixel =
      (2 * Math.tan((camera.fov * Math.PI) / 180 / 2) * distance) / size.height
    right.set(1, 0, 0).applyQuaternion(camera.quaternion)
    up.set(0, 1, 0).applyQuaternion(camera.quaternion)
    const mx = (-dxPx * right.x + dyPx * up.x) * perPixel
    const my = (-dxPx * right.y + dyPx * up.y) * perPixel
    const mz = (-dxPx * right.z + dyPx * up.z) * perPixel
    camera.position.x += mx
    camera.position.y += my
    camera.position.z += mz
    controls.target.x += mx
    controls.target.y += my
    controls.target.z += mz
    clampPan()
  }

  /* Shift + 左键 = 平移（Blender / PS 的惯用手势）。
     一定要在**捕获阶段** stopImmediatePropagation：OrbitControls 把「PAN + shift」
     直接翻成旋转，改 mouseButtons 等于喂给它一个自相矛盾的组合。 */
  let shiftDrag: { x: number; y: number } | null = null
  const canvas = renderer.domElement
  canvas.addEventListener('contextmenu', (event) => event.preventDefault())

  const onShiftDown = (event: PointerEvent) => {
    tween = null
    if (event.button !== 0 || !event.shiftKey) return
    event.stopImmediatePropagation()
    shiftDrag = { x: event.clientX, y: event.clientY }
    canvas.setPointerCapture(event.pointerId)
    canvas.style.cursor = 'grabbing'
  }
  const onShiftMove = (event: PointerEvent) => {
    if (!shiftDrag) return
    panBy(event.clientX - shiftDrag.x, event.clientY - shiftDrag.y)
    shiftDrag = { x: event.clientX, y: event.clientY }
  }
  const endShiftDrag = () => {
    if (!shiftDrag) return
    shiftDrag = null
    canvas.style.cursor = ''
  }
  canvas.addEventListener('pointerdown', onShiftDown, true)
  canvas.addEventListener('pointermove', onShiftMove)
  canvas.addEventListener('pointerup', endShiftDrag)
  canvas.addEventListener('pointercancel', endShiftDrag)

  /* ---------------------------------------------------------------- 相机动画
     进度用 performance.now() 的绝对时间。早期写 `t += dt / dur`，实测 1.9 秒只走了
     0.4%，相机像没动——累加在低帧率下会被稀释。 */
  interface Tween {
    t0: number
    dur: number
    fromPos: THREE.Vector3
    fromTarget: THREE.Vector3
    toPos: THREE.Vector3
    toTarget: THREE.Vector3
    fromFov: number
    toFov: number
  }
  let tween: Tween | null = null

  const startTween = (
    toPos: THREE.Vector3,
    toTarget: THREE.Vector3,
    durationMs: number,
    toFov: number,
  ) => {
    if (reducedMotion) {
      camera.position.copy(toPos)
      controls.target.copy(toTarget)
      camera.fov = toFov
      camera.updateProjectionMatrix()
      tween = null
      return
    }
    tween = {
      t0: performance.now(),
      dur: durationMs,
      fromPos: camera.position.clone(),
      fromTarget: controls.target.clone(),
      toPos,
      toTarget,
      fromFov: camera.fov,
      toFov,
    }
  }

  const goView = (name: ViewName, opts?: { duration?: number }) => {
    const view = CAM.views[name]
    if (!view) return
    startTween(
      new THREE.Vector3(...(view.position as [number, number, number])),
      new THREE.Vector3(...(view.target as [number, number, number])),
      opts?.duration ?? CAM.tween_ms,
      CAM.fov.overview,
    )
  }

  const flyTo = (
    target: THREE.Vector3,
    distance: number,
    opts?: { duration?: number; fov?: number },
  ) => {
    const direction = camera.position.clone().sub(controls.target)
    if (direction.lengthSq() < 1e-8) direction.set(0, 0, 1)
    direction.normalize()
    startTween(
      target.clone().add(direction.multiplyScalar(distance)),
      target.clone(),
      opts?.duration ?? CAM.tween_ms,
      opts?.fov ?? CAM.fov.chapter,
    )
  }

  /* ---------------------------------------------------------------- 帧循环 */
  const frameFns = new Set<(dt: number, now: number) => void>()
  const statsState: SceneStats = { fps: 0, worstFps: 0, calls: 0, triangles: 0 }
  let statsSink: ((stats: SceneStats) => void) | null = null
  let frames = 0
  let worstMs = 0
  let lastFrame = performance.now()
  let windowStart = performance.now()
  let raf = 0
  let disposed = false

  const tick = () => {
    if (disposed) return
    raf = requestAnimationFrame(tick)
    const now = performance.now()
    const rawMs = now - lastFrame
    const dt = Math.min(rawMs / 1000, 0.06)
    lastFrame = now
    // 掉出 2 秒的大停顿是切标签页，不算卡顿
    if (rawMs < 2000) worstMs = Math.max(worstMs, rawMs)

    if (tween) {
      const k = Math.min((now - tween.t0) / tween.dur, 1)
      const e = easeInOutCubic(k)
      camera.position.lerpVectors(tween.fromPos, tween.toPos, e)
      controls.target.lerpVectors(tween.fromTarget, tween.toTarget, e)
      const fov = tween.fromFov + (tween.toFov - tween.fromFov) * e
      if (Math.abs(camera.fov - fov) > 0.01) {
        camera.fov = fov
        camera.updateProjectionMatrix()
      }
      if (k >= 1) tween = null
    }

    controls.update()
    clampPan()
    frameFns.forEach((fn) => fn(dt, now))

    renderer.info.reset()
    if (composer) composer.render()
    else renderer.render(scene, camera)
    labelRenderer.render(scene, camera)

    /* 开场那一刻人还没加载进来，只有背景，半浮点链是"空场景下能画出画面"的。
       真正会炸的是有材质写进 NaN（肌肉法线曾整层是零长度）。所以第一帧真内容
       渲完之后再验一次：挂了就降级，不挂就再也不用管。 */
    if (!verifiedWithContent && composer && renderer.info.render.calls > 4) {
      verifiedWithContent = true
      if (!canvasHasContent()) {
        const wasLowPrecision = bloomMode === 'rgba8'
        teardownPipeline()
        if (!wasLowPrecision) tryPipeline(true)
      }
    }

    frames += 1
    const elapsed = (now - windowStart) / 1000
    if (elapsed > 0.6) {
      statsState.fps = frames / elapsed
      statsState.worstFps = worstMs > 0 ? 1000 / worstMs : 0
      statsState.calls = renderer.info.render.calls
      statsState.triangles = renderer.info.render.triangles
      statsSink?.(statsState)
      frames = 0
      worstMs = 0
      windowStart = now
    }
  }
  raf = requestAnimationFrame(tick)

  /* ---------------------------------------------------------------- 释放
     几何 / 材质 / 纹理不手动 dispose 一定泄漏：切一次图层就再堆一份。 */
  const dispose = () => {
    if (disposed) return
    disposed = true
    cancelAnimationFrame(raf)
    observer?.disconnect()
    window.removeEventListener('resize', measure)
    canvas.removeEventListener('pointerdown', onShiftDown, true)
    canvas.removeEventListener('pointermove', onShiftMove)
    canvas.removeEventListener('pointerup', endShiftDrag)
    canvas.removeEventListener('pointercancel', endShiftDrag)
    frameFns.clear()
    resizeFns.clear()

    const seen = new Set<string>()
    scene.traverse((object) => {
      const mesh = object as THREE.Mesh
      if (mesh.geometry && !seen.has(mesh.geometry.uuid)) {
        seen.add(mesh.geometry.uuid)
        mesh.geometry.dispose()
      }
      const material = mesh.material as THREE.Material | THREE.Material[] | undefined
      const list = Array.isArray(material) ? material : material ? [material] : []
      for (const item of list) {
        if (!seen.has(item.uuid)) {
          seen.add(item.uuid)
          item.dispose()
        }
      }
    })
    scene.clear()
    if (scene.background instanceof THREE.Texture) scene.background.dispose()
    controls.dispose()
    composer?.dispose()
    renderer.dispose()
    canvas.remove()
    labelHost.replaceChildren()
    ;(window as Window & { __meridianRuntime?: unknown }).__meridianRuntime = undefined
  }

  const runtime: SceneRuntime = {
    scene,
    camera,
    renderer,
    labelRenderer,
    controls,
    composer,
    bloom,
    bloomMode,
    quality,
    reducedMotion,
    size: () => ({ ...size }),
    addFrame: (fn) => {
      frameFns.add(fn)
      return () => frameFns.delete(fn)
    },
    addResize: (fn) => {
      resizeFns.add(fn)
      fn(size.width, size.height)
      return () => resizeFns.delete(fn)
    },
    goView,
    flyTo,
    panBy,
    stats: () => ({ ...statsState }),
    onStats: (fn) => {
      statsSink = fn
    },
    dispose,
  }

  // 只读句柄，给验收脚本读帧率用；产品逻辑不读它。
  ;(window as Window & { __meridianRuntime?: unknown }).__meridianRuntime = runtime
  return runtime
}
