/**
 * 一页 → 「按什么版式、有哪几块」。
 *
 * 屏幕（`SlideSurface.vue`）与导出（`utils/pptxExport.ts`）**必须**读同一份结果：
 * 升级前两边各写一套 `kind === 'compare'` 分支，靠注释约定「一起改」，
 * 结果就是「屏幕上是对照表、导出的 pptx 是一列要点」。
 *
 * 这里只做结构换算，不碰样式：字号、坐标、颜色各自在自己的层里定。
 */

import type { Archetype, BlockType } from '../types/design'
import type { SlideColumn, SlideKind, SlideNode } from '../types/ppt'

/** 老稿只有 `kind`（六种），没有 `purpose`（十种）：先映射再决定版式。 */
export const KIND_TO_LAYOUT: Record<SlideKind, Archetype> = {
  cover: 'cover',
  agenda: 'agenda',
  section: 'section',
  bullets: 'points',
  compare: 'compare',
  summary: 'summary',
}

/** 这一页实际按什么版式排。设计单说了算，没有设计单时按 `kind` 回落。 */
export const layoutOf = (slide: SlideNode): Archetype => slide.purpose ?? KIND_TO_LAYOUT[slide.kind] ?? 'points'

/**
 * 一个待渲染的块。
 *
 * `proxy` 表示这块是**从旧字段摊出来的**（没有 `blocks` 的老稿、或用户手加的新页）：
 * 编辑时要写回 `bullets` / `columns`，否则一保存就丢。
 */
export interface ViewBlock {
  type: BlockType
  text: string
  heading: string
  items: string[]
  columns: SlideColumn[]
  value: string
  source: string
  /** 真实块 = `blocks` 下标；代理块 = 回写位置（-1 表示整块对整列）。 */
  origin: number
  proxy: boolean
}

const empty = {
  text: '',
  heading: '',
  items: [] as string[],
  columns: [] as SlideColumn[],
  value: '',
  source: '',
  proxy: true,
}

/**
 * 取这一页的块。
 *
 * 有 `blocks` 就用；没有就按版式把 `bullets` / `columns` 摊开——这样旧稿不需要
 * 迁移也能吃到新版式（对照页有两条以上要点时按两栏对切，警示页一句一条）。
 */
export const blocksOf = (slide: SlideNode): ViewBlock[] => {
  const raw = slide.blocks ?? []
  if (raw.length) {
    return raw.map((block, index) => ({
      type: block.type,
      text: block.text ?? '',
      heading: block.heading ?? '',
      items: [...(block.items ?? [])],
      columns: (block.columns ?? []).map((column) => ({ heading: column.heading, items: [...column.items] })),
      value: block.value ?? '',
      source: block.source ?? '',
      origin: index,
      proxy: false,
    }))
  }

  const layout = layoutOf(slide)
  const columns = slide.columns
  // 对照与矩阵都吃 `columns`：两栏是对照、三栏是矩阵，同一份数据换个版式就换个读法。
  if ((layout === 'compare' || layout === 'matrix') && columns.length >= 2) {
    return [
      {
        ...empty,
        type: 'matrix',
        origin: -1,
        columns: columns.map((column) => ({ heading: column.heading, items: [...column.items] })),
      },
    ]
  }
  if ((layout === 'agenda' || layout === 'process') && slide.bullets.length) {
    return [{ ...empty, type: 'steps', origin: -1, items: [...slide.bullets] }]
  }
  if (layout === 'warning') {
    return slide.bullets.map((text, index) => ({ ...empty, type: 'alert', text, origin: index }))
  }
  return slide.bullets.map((text, index) => ({ ...empty, type: 'point', text, origin: index }))
}

/** 一页的正文量。密度、字号放大、导出行高都按它算，避免三处各数一遍。 */
export const blockCount = (slide: SlideNode): number => blocksOf(slide).length

/**
 * 有没有**画得出来**的图位。
 *
 * `result !== 'none'` 还不够：库里命中但 `file` 为空、或母题 id 没有渲染实现时，
 * 画出来就是个空灰块——比不画更难看。所以这里要求「有文件」或「有母题」。
 */
export const hasFigure = (slide: SlideNode): boolean =>
  (slide.asset_refs ?? []).some((ref) => ref.result !== 'none' && (Boolean(ref.file) || ref.result === 'parametric'))
