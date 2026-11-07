import type { ResourceSource } from './resource'

/** 渲染层认识的六种版式，与后端 `app/ppt/schemas.py` 的 SlideKind 一一对应。 */
export type SlideKind = 'cover' | 'agenda' | 'section' | 'bullets' | 'compare' | 'summary'

export interface SlideColumn {
  heading: string
  items: string[]
}

/**
 * 一页幻灯。
 *
 * 字段上限与后端严格模型保持一致（标题 40 / 要点 60 / 备注 200），
 * 前端输入框也按这个卡，避免「编辑能打进去、保存被 422 打回来」。
 */
export interface SlideNode {
  id: string
  kind: SlideKind
  title: string
  subtitle: string
  bullets: string[]
  columns: SlideColumn[]
  note: string
  source_indexes: number[]
}

export interface DeckContent {
  theme_id: string
  slides: SlideNode[]
  sources?: ResourceSource[]
}

/** 大纲页：谋篇的产物，也是「先定大纲」模式下用户唯一能改的东西。 */
export interface OutlineSlide {
  id: string
  title: string
  points: string[]
  source_indexes: number[]
}

export type PptMode = 'one_shot' | 'outline'

export interface PptSaveResult {
  version: number
  slide_count: number
}

export interface PptStats {
  slides: number
  bullets: number
  grounded: number
}

export const SLIDE_LIMITS = {
  title: 40,
  subtitle: 40,
  bullet: 60,
  columnHeading: 24,
  columnItem: 30,
  note: 200,
  slides: 20,
} as const

/** 版式的中文名，用于左栏「当前页」与胶片条的辅助说明。 */
export const SLIDE_KIND_LABELS: Record<SlideKind, string> = {
  cover: '封面',
  agenda: '导览',
  section: '章节',
  bullets: '要点',
  compare: '对比',
  summary: '总结',
}
