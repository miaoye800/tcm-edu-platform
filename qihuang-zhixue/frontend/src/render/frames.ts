/**
 * 版式几何：**唯一的事实来源**。
 *
 * 为什么必须是数据而不是 `v-if`：确认单里的线框（0.3 保真）与成品（1.0）要由
 * 同一套几何渲出来，否则「确认过的样子」和「拿到的样子」必然对不上——
 * 升级前 `SlideSurface.vue` 用 `kind === 'cover'` 之类的分支画版式，
 * 导出又另写一份 pt/英寸坐标，两边靠注释约定「一起改」，这就是漂移的源头。
 *
 * 坐标系：以舞台宽度为 100 单位的 cqw。16:9 画布 = 100 × 56.25。
 * 所有数值来自装帧规格（`docs/studios/ppt.md` §3.3）：
 *
 * - 版心：左右 9、天头 8.4、地脚 7.2；
 * - 字号七级：7.6 / 4.6 / 3.2 / 2.2 / 1.8 / 1.5 / 1.3；
 * - 每页装饰预算 ≤2。
 */

import type { Archetype, Density } from '../types/design'

export interface Rect {
  x: number
  y: number
  w: number
  h: number
}

export interface Frame {
  archetype: Archetype
  /** 版心。内容只在版心内，装饰可以出血。 */
  margin: Rect
  kicker: Rect | null
  title: Rect
  rule: Rect | null
  body: Rect
  /** 分栏数。1 = 单栏，2 = 对照，3 = 矩阵。 */
  columns: number
  /** 行块数。议程/流程用；普通页为 1。 */
  rows: number
  figure: Rect | null
  ornament: Rect | null
  foot: { y: number; left: Rect; right: Rect }
  capacity: { maxBlocks: number; itemChars: number }
  scale: { title: number; body: number; kicker: number }
}

/** 画布：16:9，单位 cqw。 */
export const CANVAS = { w: 100, h: 56.25 } as const

/** 版心四边。天头比地脚宽，是纸面装帧的常识——上下等宽会显得「顶头」。 */
export const MARGIN = { top: 8.4, right: 9, bottom: 7.2, left: 9 } as const

/** 字号七级（cqw）。 */
export const TYPE_SCALE = [7.6, 4.6, 3.2, 2.2, 1.8, 1.5, 1.3] as const

/** 每页装饰预算。超了就是「花」，不是「雅」。 */
export const DECOR_BUDGET = 2

const CONTENT_TOP = MARGIN.top
const CONTENT_LEFT = MARGIN.left
const CONTENT_W = CANVAS.w - MARGIN.left - MARGIN.right
const CONTENT_H = CANVAS.h - MARGIN.top - MARGIN.bottom
const FOOT_Y = CANVAS.h - MARGIN.bottom + 3.2

/** 各版式的容量。与后端 `ARCHETYPES` 同数——超限由校验拦，不靠自觉。 */
export const FRAME_CAPACITY: Record<Archetype, { maxBlocks: number; itemChars: number }> = {
  cover: { maxBlocks: 1, itemChars: 24 },
  agenda: { maxBlocks: 6, itemChars: 14 },
  section: { maxBlocks: 1, itemChars: 16 },
  points: { maxBlocks: 5, itemChars: 40 },
  compare: { maxBlocks: 2, itemChars: 30 },
  matrix: { maxBlocks: 3, itemChars: 8 },
  process: { maxBlocks: 4, itemChars: 12 },
  data: { maxBlocks: 3, itemChars: 6 },
  warning: { maxBlocks: 2, itemChars: 36 },
  summary: { maxBlocks: 4, itemChars: 40 },
}

const KICKER_H = 2.2
const RULE_H = 0.3
const TITLE_GAP = 1.4
const FOOT_H = 1.6

/** 版式 → 几何。`density` 只调行距与块高，不改版心。 */
export const frameFor = (archetype: Archetype, density: Density = 'normal'): Frame => {
  const margin: Rect = { x: 0, y: 0, w: CANVAS.w, h: CANVAS.h }
  const capacity = FRAME_CAPACITY[archetype] ?? FRAME_CAPACITY.points
  const foot = {
    y: FOOT_Y,
    left: { x: CONTENT_LEFT, y: FOOT_Y, w: CONTENT_W * 0.6, h: FOOT_H },
    right: { x: CONTENT_LEFT + CONTENT_W * 0.6, y: FOOT_Y, w: CONTENT_W * 0.4, h: FOOT_H },
  }

  if (archetype === 'cover') {
    const titleH = 18
    const figureW = 34
    return {
      archetype,
      margin,
      kicker: { x: CONTENT_LEFT, y: CONTENT_TOP + 2, w: 40, h: KICKER_H },
      title: { x: CONTENT_LEFT + 1.6, y: CONTENT_TOP + 8, w: CONTENT_W - figureW - 8, h: titleH },
      rule: { x: 0, y: 0, w: 0.55, h: CANVAS.h },
      body: { x: CONTENT_LEFT + 1.6, y: CONTENT_TOP + 8 + titleH + 3, w: CONTENT_W - figureW - 8, h: 6 },
      columns: 1,
      rows: 1,
      figure: { x: CANVAS.w - MARGIN.right - figureW, y: CONTENT_TOP, w: figureW, h: CONTENT_H },
      ornament: { x: 0, y: 0, w: 18, h: 18 },
      foot,
      capacity,
      scale: { title: TYPE_SCALE[0], body: TYPE_SCALE[3], kicker: TYPE_SCALE[5] },
    }
  }

  if (archetype === 'section') {
    return {
      archetype,
      margin,
      kicker: null,
      title: { x: CONTENT_LEFT, y: CANVAS.h / 2 - 6, w: CONTENT_W * 0.62, h: 12 },
      rule: null,
      body: { x: CONTENT_LEFT, y: CANVAS.h / 2 + 6.5, w: CONTENT_W * 0.62, h: 5 },
      columns: 1,
      rows: 1,
      figure: null,
      ornament: { x: CANVAS.w - 28, y: CANVAS.h / 2 - 9, w: 24, h: 18 },
      foot,
      capacity,
      scale: { title: TYPE_SCALE[1], body: TYPE_SCALE[3], kicker: TYPE_SCALE[5] },
    }
  }

  const titleH = archetype === 'summary' ? 6.2 : 7.4
  const bodyTop = CONTENT_TOP + KICKER_H + TITLE_GAP + titleH + RULE_H + 2.4
  const body: Rect = {
    x: CONTENT_LEFT,
    y: bodyTop,
    w: CONTENT_W,
    h: Math.max(4, FOOT_Y - 3.4 - bodyTop),
  }
  const columns = archetype === 'compare' ? 2 : archetype === 'matrix' ? 3 : 1
  const rows = archetype === 'data' ? 1 : archetype === 'agenda' || archetype === 'process' ? capacity.maxBlocks : 1
  const gap = density === 'dense' ? 1.2 : density === 'airy' ? 2.6 : 1.8

  return {
    archetype,
    margin,
    kicker: { x: CONTENT_LEFT, y: CONTENT_TOP, w: 44, h: KICKER_H },
    title: { x: CONTENT_LEFT, y: CONTENT_TOP + KICKER_H + TITLE_GAP, w: CONTENT_W, h: titleH },
    rule: { x: CONTENT_LEFT, y: CONTENT_TOP + KICKER_H + TITLE_GAP + titleH + 0.6, w: 8, h: RULE_H },
    body: { ...body, h: Math.max(4, body.h - gap * 0.2) },
    columns,
    rows: Math.max(1, rows),
    figure: null,
    ornament: archetype === 'summary' ? { x: CANVAS.w - 20, y: CANVAS.h - 18, w: 12, h: 12 } : null,
    foot,
    capacity,
    scale: {
      // 页标题用第三级（3.2）而不是章节级的 4.6：40 字标题在 4.6 下要折三行，
      // 版心会被正文挤掉。4.6 留给 section 的巨号标题。
      title: TYPE_SCALE[2],
      body: density === 'dense' ? TYPE_SCALE[4] : TYPE_SCALE[3],
      kicker: TYPE_SCALE[6],
    },
  }
}

/** 单栏内的行高。线框与成品都用它排「几行」，避免两套行距。 */
export const rowHeight = (density: Density): number => (density === 'dense' ? 4.2 : density === 'airy' ? 7.4 : 5.6)

/** 一页能放下的正文行数。用于线框画几行、校验估溢出。 */
export const rowsFor = (frame: Frame, density: Density): number =>
  Math.max(1, Math.floor(frame.body.h / rowHeight(density)))
