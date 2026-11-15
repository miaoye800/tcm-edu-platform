/**
 * 场景 DSL 的前端镜像。
 *
 * 与 `backend/app/ppt/scene.py` **逐字段对应**，两边不能分叉：后端用它校验与落库，
 * 前端用它渲染。字段全部带默认值，旧资源没有 `scene` 时由 `sceneFallback.ts` 拼一份。
 *
 * 关键约定：
 *
 * - `box` 是 `(x, y, w, h)`，单位 cqw，画布 100 × 56.25。允许负值（出血）。
 * - 颜色只认角色名，不认 hex——同一份场景要能在 12 套配色下都成立。
 * - 元素不做「标题块 / 图位」这类复合，只给九个基元。复合即模板。
 */

import { MOTIFS } from './motifs.catalog'

/** 画布（cqw）。16:9 固定。 */
export const CANVAS = { w: 100, h: 56.25 } as const

/** 出血上限（cqw）。超过就不是出血，是画到纸外面。 */
export const BLEED_LIMIT = 25

/** 一页最多元素数（group 展开后）。 */
export const MAX_ELEMENTS = 24

export type ColorRole = 'fg' | 'muted' | 'accent' | 'line' | 'bg'

export type ElementType = 'asset' | 'motif' | 'rect' | 'rule' | 'circle' | 'text' | 'seal' | 'group'

export type TextRole = 'title' | 'subtitle' | 'kicker' | 'body' | 'caption' | 'note' | 'numeral'

/** 角色 → 默认字号（cqw）/ 字重。与后端 `TEXT_ROLE_SPEC` 同源。 */
export const SCENE_TEXT_ROLES: Record<TextRole, { size: number; weight: number; maxChars: number }> = {
  title: { size: 4.6, weight: 600, maxChars: 40 },
  subtitle: { size: 2.2, weight: 400, maxChars: 80 },
  kicker: { size: 1.3, weight: 400, maxChars: 16 },
  body: { size: 2.2, weight: 400, maxChars: 200 },
  caption: { size: 1.3, weight: 400, maxChars: 60 },
  note: { size: 1.3, weight: 400, maxChars: 80 },
  numeral: { size: 7.6, weight: 600, maxChars: 8 },
}

export interface SceneVeil {
  color: ColorRole
  opacity: number
  direction: 'flat' | 'from_left' | 'from_right' | 'from_bottom' | 'from_top'
}

export interface SceneBackground {
  kind: 'motif' | 'asset' | 'solid'
  motif: string
  params?: Record<string, number | string>
  asset: string
  fit: 'cover' | 'contain'
  veil?: SceneVeil | null
  tone: ColorRole
}

export interface SceneText {
  content: string
  role: TextRole
  size?: number | null
  font: 'display' | 'body'
  color: ColorRole
  write: 'h' | 'v'
  align: 'left' | 'center' | 'right' | 'justify'
  weight?: number | null
  line_height: number
  tracking: number
  max_lines: number
}

export interface SceneElement {
  type: ElementType
  box: [number, number, number, number]
  z: number
  opacity: number
  rotate: number
  /** 镜像。角花四面对称，画一份镜像四处比存四张图合理。 */
  flip: 'none' | 'x' | 'y' | 'xy'
  asset: string
  fit: 'cover' | 'contain'
  radius: number
  caption: string
  /** 润色（母版路线 Phase 4，additive）：旧场景无这些字段 = 原样。 */
  shadow: number
  frame: 'none' | 'hairline' | 'mat'
  img_treatment: 'none' | 'toned' | 'duotone'
  motif: string
  params?: Record<string, number | string>
  fill: ColorRole | null
  /** rect 双色渐变：fill → fill2（方向 h/v）。null = 纯色。 */
  fill2: ColorRole | null
  gradient_dir: 'h' | 'v' | 'none'
  stroke: ColorRole | null
  stroke_width: number
  dash: 'solid' | 'dash' | 'dot'
  orient: 'h' | 'v'
  weight: number
  text: SceneText | null
  content: string
  shape: 'square' | 'round'
  items: SceneElement[]
}

export interface Scene {
  version: number
  note: string
  background: SceneBackground
  elements: SceneElement[]
}

/** 九个基元的默认值。模型只写要用的字段，其余由此补齐。 */
export const elementDefaults = (): SceneElement => ({
  type: 'rect',
  box: [0, 0, 10, 10],
  z: 10,
  opacity: 1,
  rotate: 0,
  flip: 'none',
  asset: '',
  fit: 'contain',
  radius: 0,
  caption: '',
  shadow: 0,
  frame: 'none',
  img_treatment: 'none',
  motif: '',
  params: {},
  fill: 'accent',
  fill2: null,
  gradient_dir: 'none',
  stroke: null,
  stroke_width: 0,
  dash: 'solid',
  orient: 'h',
  weight: 0.18,
  text: null,
  content: '',
  shape: 'square',
  items: [],
})

export const textDefaults = (): SceneText => ({
  content: '',
  role: 'body',
  size: null,
  font: 'body',
  color: 'fg',
  write: 'h',
  align: 'left',
  weight: null,
  line_height: 1.5,
  tracking: 0,
  max_lines: 4,
})

/**
 * 宽松入参。
 *
 * 后端的 `extra="forbid"` 只在写入时生效，读回来的是全量字段；但**手写场景**（调试页、
 * 将来的模型产出）不会把 17 个字段都写一遍。所以归一化的入参必须是「想写多少写多少」，
 * 而出参必须是全量——渲染层不该判 `undefined`。
 */
export type RawElement = Partial<SceneElement> & {
  box?: [number, number, number, number]
  text?: Partial<SceneText> | null
  items?: RawElement[]
}

export type RawScene = {
  version?: number
  note?: string
  background?: Partial<SceneBackground>
  elements?: RawElement[]
}

/** 把后端的宽松 JSON 补成完整的 `SceneElement`。渲染层不判 undefined。 */
export const normalizeElement = (raw: RawElement): SceneElement => {
  const base = elementDefaults()
  const merged: SceneElement = { ...base, ...raw } as SceneElement
  merged.box = (raw.box ?? base.box) as [number, number, number, number]
  merged.items = (raw.items ?? []).map(normalizeElement)
  if (merged.type === 'text') {
    merged.text = { ...textDefaults(), ...(raw.text ?? {}) }
  }
  return merged
}

export const normalizeScene = (raw: RawScene | null | undefined): Scene => ({
  version: raw?.version ?? 1,
  note: raw?.note ?? '',
  background: {
    kind: 'motif',
    motif: 'paper-plain',
    params: {},
    asset: '',
    fit: 'cover',
    veil: null,
    tone: 'bg',
    ...(raw?.background ?? {}),
  },
  elements: (raw?.elements ?? []).map(normalizeElement),
})

export const EMPTY_SCENE: Scene = normalizeScene(null)

/** 展开 group 后的元素总数。装饰预算按它算。 */
export const countElements = (scene: Scene): number =>
  scene.elements.reduce((sum, element) => sum + 1 + element.items.length, 0)

/**
 * 前端侧校验。后端 `validate_scene` 是权威，这份只用于**编辑期即时提示**——
 * 用户改一个坐标不该等一次网络往返才知道超了出血。
 *
 * 返回的是 warning 不是 error：排版是手艺，不是法律，能画就画出来给人看。
 */
export const lintScene = (scene: Scene): string[] => {
  const out: string[] = []
  const total = countElements(scene)
  if (total > MAX_ELEMENTS) out.push(`元素 ${total} 个，超过 ${MAX_ELEMENTS} 个预算`)

  for (const [index, element] of scene.elements.entries()) {
    const [x, y, w, h] = element.box
    if (x < -BLEED_LIMIT || y < -BLEED_LIMIT || x + w > CANVAS.w + BLEED_LIMIT || y + h > CANVAS.h + BLEED_LIMIT) {
      out.push(`元素 ${index} 出血过远`)
    }
    if (element.type !== 'text' || !element.text) continue
    const size = element.text.size ?? SCENE_TEXT_ROLES[element.text.role].size
    const perLine = Math.max(1, w / Math.max(size, 0.1))
    const lines = Math.max(1, element.text.content.length / perLine)
    if (element.text.write === 'h' && lines > element.text.max_lines) {
      out.push(`元素 ${index} 需要 ${lines.toFixed(1)} 行，上限 ${element.text.max_lines}`)
    }
  }
  return out
}

/** 场景里所有引用的素材 id（去重）。用于导出前预取与许可核对。 */
export const sceneAssetIds = (scene: Scene): string[] => {
  const ids = new Set<string>()
  const walk = (elements: SceneElement[]) => {
    for (const element of elements) {
      if (element.asset) ids.add(element.asset)
      if (element.items.length) walk(element.items)
    }
  }
  walk(scene.elements)
  if (scene.background.kind === 'asset' && scene.background.asset) ids.add(scene.background.asset)
  return [...ids]
}

/**
 * 真纹样资产（装饰素材包，Phase 1 引入）。
 *
 * 这批 SVG 是黑填充的**整幅图案**（Inkscape/SVG Repo 下载），不是透明线稿：
 * 直接按照片画，纸上就是深灰墙纸和黑色角块。屏幕端走 CSS mask 单色重着色，
 * 导出端走 canvas `source-in` 染色 + PNG——两条路都认这一个前缀，别各写一份。
 */
export const DECOR_ASSET_PREFIXES = ['ornament-', 'ground-']

export const isDecorativeAsset = (id: string | undefined | null): boolean =>
  Boolean(id && DECOR_ASSET_PREFIXES.some((prefix) => id.startsWith(prefix)))

/**
 * 母题自己的坐标系。
 *
 * 这件事必须由这里回答，不能让调用方猜：角饰画在 0..20 的方格内（`SlideSurface` 里
 * 用 `viewBox="0 0 20 20"`），纸底与水纹画在 100×56.25 的整页坐标里。用同一个
 * viewBox 画两者，角花会缩到只剩原大五分之一，看起来像「没画」。
 *
 * 按 `family` 推断而不是硬编码 id：以后加角饰母题不必回来改这张表。
 */
export const motifViewBox = (id: string): string => {
  const spec = MOTIFS.find((item) => item.id === id)
  if (spec?.family === 'ornament') return '0 0 20 20'
  if (spec?.family === 'seal') return '0 0 100 100'
  return '0 0 100 56.25'
}
