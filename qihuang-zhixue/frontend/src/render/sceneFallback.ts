/**
 * 兜底场景（前端）。
 *
 * 与 `backend/app/ppt/scene_fallback.py` 同构：模型没给 `scene` 时，前端自己拼一份，
 * 保证「旧稿也能用新渲染器画」。两份实现的坐标系与构造顺序必须一致，否则同一份
 * 稿件在导出与预览里会不一样——这种漂移比「没有兜底」更难查。
 *
 * 只保留一条底线：纸 + 墨 + 一条朱砂。兜底不该比模型设计的更花。
 */

import {
  SCENE_TEXT_ROLES,
  normalizeScene,
  type Scene,
  type SceneElement,
  type SceneText,
  type TextRole,
} from './scene'

const MARGIN = { top: 8.4, right: 9, bottom: 7.2, left: 9 }
const CANVAS_W = 100
const CANVAS_H = 56.25
const CONTENT_W = CANVAS_W - MARGIN.left - MARGIN.right
const FOOT_Y = CANVAS_H - MARGIN.bottom + 1.2

const DENSITY: Record<string, [number, number]> = {
  airy: [3.2, 2.2],
  normal: [2.0, 2.2],
  dense: [1.2, 1.8],
}

const textElement = (
  content: string,
  box: [number, number, number, number],
  options: Partial<SceneText> & { z?: number } = {},
): SceneElement => {
  const role = (options.role ?? 'body') as TextRole
  const spec = SCENE_TEXT_ROLES[role]
  return {
    type: 'text',
    box,
    z: options.z ?? 20,
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
    fill: null,
    fill2: null,
    gradient_dir: 'none',
    stroke: null,
    stroke_width: 0,
    dash: 'solid',
    orient: 'h',
    weight: 0.18,
    content: '',
    shape: 'square',
    items: [],
    text: {
      content,
      role,
      size: options.size ?? spec.size,
      font: options.font ?? 'body',
      color: options.color ?? 'fg',
      write: options.write ?? 'h',
      align: options.align ?? 'left',
      weight: options.weight ?? spec.weight,
      line_height: options.line_height ?? 1.6,
      tracking: options.tracking ?? 0,
      max_lines: options.max_lines ?? 4,
    },
  }
}

const ruleElement = (
  box: [number, number, number, number],
  color: SceneElement['fill'] = 'accent',
  weight = 0.3,
  z = 10,
): SceneElement => ({
  type: 'rule',
  box,
  z,
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
  fill: color,
  fill2: null,
  gradient_dir: 'none',
  stroke: null,
  stroke_width: 0,
  dash: 'solid',
  orient: box[3] > box[2] ? 'v' : 'h',
  weight,
  content: '',
  shape: 'square',
  items: [],
  text: null,
})

/** 四角角花：画一份，镜像三份。与后端 `_corners` 同一套坐标。 */
const corners = (motif: string, size: number): SceneElement[] => {
  if (!motif) return []
  const make = (box: [number, number, number, number], flip: SceneElement['flip']): SceneElement => ({
    ...ruleElement(box, 'fg', 0.1, 8),
    type: 'motif',
    motif,
    fill: null,
    flip,
  })
  return [
    make([0, 0, size, size], 'none'),
    make([CANVAS_W - size, 0, size, size], 'x'),
    make([0, CANVAS_H - size, size, size], 'y'),
    make([CANVAS_W - size, CANVAS_H - size, size, size], 'xy'),
  ]
}

/** 图位：素材走 asset，参数化母题走 motif。混用会被后端命名规则拒掉。 */
const figure = (
  id: string,
  box: [number, number, number, number],
  input: FallbackInput,
): SceneElement =>
  input.figureKind === 'motif'
    ? { ...ruleElement(box, 'fg', 0.1, 15), type: 'motif', motif: id, fill: null }
    : { ...ruleElement(box, 'accent', 0.2, 15), type: 'asset', asset: id, fit: 'cover', caption: input.figureCaption ?? '' }

const stack = (
  rows: string[],
  box: [number, number, number, number],
  { size = 2.2, gap = 2, numbered = false } = {},
): SceneElement[] => {
  const items = rows.filter(Boolean)
  if (!items.length) return []
  const [x, y, w, h] = box
  const rowH = Math.min((h - gap * (items.length - 1)) / items.length, size * 2.6)
  const out: SceneElement[] = []
  items.forEach((row, index) => {
    const top = y + index * (rowH + gap)
    if (numbered) {
      out.push(textElement(String(index + 1).padStart(2, '0'), [x, top, 5, rowH], {
        role: 'numeral',
        size: Math.min(2.6, rowH * 0.8),
        color: 'accent',
        font: 'display',
        line_height: 1.2,
      }))
      out.push(textElement(row, [x + 6.2, top, w - 6.2, rowH], { size, line_height: 1.5 }))
    } else {
      out.push(textElement(row, [x + 1.6, top, w - 1.6, rowH], { size, line_height: 1.5 }))
      out.push({
        ...ruleElement([x + 0.1, top + size * 0.62, 0.55, 0.55], 'accent', 0.2, 19),
        type: 'rect',
        rotate: 45,
      })
    }
  })
  return out
}

export interface FallbackInput {
  archetype: string
  title: string
  subtitle?: string
  /** 已归一的内容块（与后端 `SlideBlock` 同形）。 */
  blocks?: Array<{ type: string; text?: string; heading?: string; items?: string[]; columns?: Array<{ heading: string; items: string[] }>; value?: string; source?: string }>
  bullets?: string[]
  /** 旧稿的两栏：只有 `columns` 没有 `blocks` 的页面靠它兜住，否则对照页只剩标题。 */
  columns?: Array<{ heading: string; items: string[] }>
  density?: string
  figureId?: string
  figureCaption?: string
  /** 图位的种类：库内素材走 asset，参数化母题走 motif（id 不同域，不能混）。 */
  figureKind?: 'asset' | 'motif'
  /** 纸面母题 id（`paper-plain` 等），来自设计单的 paper 处方。 */
  paperMotif?: string
  /** 角饰母题 id（`corner-vine` 等），空串表示不画。 */
  ornamentMotif?: string
}

/** 十原型共用：一个头 + 一片内容区。不写十套几何。 */
export const fallbackScene = (input: FallbackInput): Scene => {
  const { archetype, title, subtitle = '', blocks = [], bullets = [], density = 'normal' } = input
  const [gap, bodySize] = DENSITY[density] ?? DENSITY.normal
  const background = { kind: 'motif' as const, motif: input.paperMotif || 'paper-plain', params: {}, asset: '', fit: 'cover' as const, veil: null, tone: 'bg' as const }
  const elements: SceneElement[] = []

  if (archetype === 'cover') {
    const hasFigure = Boolean(input.figureId)
    const textW = CONTENT_W * (hasFigure ? 0.56 : 0.86)
    elements.push(
      textElement(title, [MARGIN.left, 16, textW, 20], {
        role: 'title',
        size: 7.6,
        font: 'display',
        line_height: 1.22,
        tracking: 0.08,
        z: 22,
      }),
    )
    if (subtitle) {
      elements.push(textElement(subtitle, [MARGIN.left, 38, textW, 6], { role: 'subtitle', color: 'muted' }))
    }
    elements.push(ruleElement([MARGIN.left, 46, 12, 0.32]))
    if (hasFigure) {
      elements.push(figure(input.figureId!, [CANVAS_W - 34, MARGIN.top, 34, CANVAS_H - MARGIN.top - MARGIN.bottom], input))
    }
    elements.push(...corners(input.ornamentMotif ?? '', 14))
    return normalizeScene({ note: '兜底封面', background, elements })
  }

  if (archetype === 'section') {
    elements.push(textElement(title, [MARGIN.left, 21, CONTENT_W * 0.7, 12], { role: 'title', size: 4.6, font: 'display', line_height: 1.3 }))
    if (subtitle) {
      elements.push(textElement(subtitle, [MARGIN.left, 34, CONTENT_W * 0.66, 5], { role: 'subtitle', color: 'muted' }))
    }
    elements.push(...corners(input.ornamentMotif ?? '', 14))
    return normalizeScene({ note: '兜底章节页', background, elements })
  }

  // 内页：头固定，后面按块类型分派。
  elements.push(textElement(title, [MARGIN.left, MARGIN.top + 3.4, CONTENT_W, 7.6], { role: 'title', size: 3.2, font: 'display', line_height: 1.32, z: 22 }))
  elements.push(ruleElement([MARGIN.left, MARGIN.top + 11.6, 8, 0.3]))

  const contentTop = MARGIN.top + 15.2
  const contentH = FOOT_Y - 2 - contentTop
  let bodyBox: [number, number, number, number] = [MARGIN.left, contentTop, CONTENT_W, contentH]

  if (input.figureId && ['points', 'summary', 'data'].includes(archetype)) {
    const figW = CONTENT_W * 0.32
    bodyBox = [MARGIN.left, contentTop, CONTENT_W - figW - 3, contentH]
    elements.push(figure(input.figureId, [MARGIN.left + CONTENT_W - figW, contentTop, figW, contentH], input))
  }

  const matrix =
    blocks.find((block) => block.type === 'matrix' && block.columns?.length) ??
    (input.columns?.length ? { type: 'matrix', columns: input.columns } : undefined)
  const figures = blocks.filter((block) => block.type === 'figures' && block.value)
  const steps = blocks.find((block) => block.type === 'steps')
  const plain = blocks.filter((block) => ['point', 'alert', 'quote', 'def'].includes(block.type))

  if (matrix?.columns) {
    const columns = matrix.columns.filter((column) => column.items.length)
    const columnGap = 2.4
    const columnW = (bodyBox[2] - columnGap * (columns.length - 1)) / Math.max(columns.length, 1)
    columns.forEach((column, index) => {
      const left = bodyBox[0] + index * (columnW + columnGap)
      // 用 muted 不用 line：line 是页脚那种"几乎没有"的分隔线，栏间用它等于没画。
      if (index) elements.push(ruleElement([left - columnGap / 2, bodyBox[1], 0.06, bodyBox[3]], 'muted', 0.08, 12))
      if (column.heading) {
        elements.push(textElement(column.heading, [left, bodyBox[1], columnW, 3], { role: 'subtitle', size: bodySize * 1.05, color: 'accent', font: 'display', line_height: 1.3 }))
      }
      elements.push(...stack(column.items, [left, bodyBox[1] + 4, columnW, bodyBox[3] - 4], { size: bodySize * 0.94, gap: 1.2 }))
    })
  } else if (figures.length) {
    const cardGap = 2.2
    const cardW = (bodyBox[2] - cardGap * (figures.length - 1)) / figures.length
    figures.forEach((card, index) => {
      const left = bodyBox[0] + index * (cardW + cardGap)
      elements.push(ruleElement([left, bodyBox[1], cardW, 0.18], 'accent', 0.18))
      elements.push(textElement(card.value ?? '', [left, bodyBox[1] + 1.2, cardW, 7], { role: 'numeral', size: 4.6, color: 'accent', font: 'display', line_height: 1.1 }))
      elements.push(textElement(card.heading ?? '', [left, bodyBox[1] + 8.8, cardW, 4], { size: 2, line_height: 1.4 }))
      if (card.source) {
        elements.push(textElement(card.source, [left, bodyBox[1] + 13.2, cardW, 2.4], { role: 'caption', color: 'muted' }))
      }
    })
  } else if (steps?.items?.length) {
    elements.push(...stack(steps.items, bodyBox, { size: bodySize, gap, numbered: true }))
  } else if (plain.length) {
    const texts = plain.map((block) =>
      block.type === 'def' && block.heading ? `${block.heading}：${block.text ?? ''}` : String(block.text ?? ''),
    )
    elements.push(...stack(texts, bodyBox, { size: bodySize, gap }))
  } else if (bullets.length) {
    elements.push(...stack(bullets, bodyBox, { size: bodySize, gap }))
  }

  if (archetype === 'summary') {
    elements.push(ruleElement([0, 0, CANVAS_W, 0.5], 'accent', 0.5))
  }

  elements.push(...corners(input.ornamentMotif ?? '', 9.5))
  return normalizeScene({ note: `兜底 ${archetype}`, background, elements })
}
