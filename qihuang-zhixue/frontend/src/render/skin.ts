/**
 * 纸面装帧：设计单上的「纸」「角饰」落到具体母题上。
 *
 * 设计单存的是**处方名**（`xuan-plain` / `ink-night` / `vine-a`），不是母题 id。
 * 处方与母题之间必须有一层映射：母题是渲染实现，处方是设计语义。
 * 换个主题只想换母题，不想改设计单——两层分开就改得动。
 *
 * 放在这里而不是组件里，是因为导出（pptx）与线框也要同一套映射；
 * 三处各写一份 `if (paper === 'xuan-plain')` 必然漂移。
 */

import { pptThemeById } from '../config/pptThemes'
import { renderMotif, type MotifPalette } from './motifs'

/** 纸面处方 → 底纹母题。缺省给最安静的那张。 */
const PAPER_MOTIFS: Record<string, string> = {
  'xuan-plain': 'paper-plain',
  'xuan-chain': 'paper-chain',
  'xuan-mottle': 'paper-mottle',
  'ink-night': 'paper-mottle',
  'celadon-mist': 'paper-chain',
  'cinnabar-warm': 'paper-mottle',
}

/** 角饰处方 → 角饰母题。`none` 表示不画（装饰预算里省下来的一格）。 */
const ORNAMENT_MOTIFS: Record<string, string> = {
  'vine-a': 'corner-vine',
  'branch-b': 'corner-branch',
  'bamboo-d': 'corner-bamboo',
  'fret-e': 'corner-fret',
  'scroll-c': 'corner-scroll',
  none: '',
}

/**
 * 纸面：既认处方名（`xuan-plain`），也认母题 id（`paper-chain`）。
 *
 * 设计单从 v2 起直接存母题 id（母题目录是唯一的候选来源，模型选的就是它），
 * 但旧稿存的是处方名，两个都得能读——少认一边，旧稿就退回「同一张素纸」。
 */
export const paperMotif = (paper: string | undefined): string => {
  if (!paper) return 'paper-plain'
  if (paper.startsWith('paper-')) return paper
  return PAPER_MOTIFS[paper] ?? 'paper-plain'
}

/**
 * 角饰：母题 id 直用，处方名走映射，`none` / 空 = 不画。
 *
 * 修的是「每份稿子四角都开着同一朵卷草」：以前调用方传旧字段 `ornament_set`
 * （缺省 vine-a），方案里选了什么完全没机会进来。`none` 必须真的是一个答案，
 * 而不是「没填，所以给默认值」。
 */
export const ornamentMotif = (set: string | undefined): string => {
  if (!set || set === 'none') return ''
  if (set.startsWith('corner-')) return set
  return ORNAMENT_MOTIFS[set] ?? ''
}

/** 主题 hex → 母题调色板。母题画在 DOM 里，用带 `#` 的 CSS 颜色。 */
export const paletteFor = (themeId: string): MotifPalette => {
  const { hex } = pptThemeById(themeId)
  return {
    bg: `#${hex.bg}`,
    fg: `#${hex.fg}`,
    muted: `#${hex.muted}`,
    accent: `#${hex.accent}`,
    line: `#${hex.line}`,
  }
}

/** 底纹 SVG 内容。`seed` 让同一页每次渲出来一模一样（截图基线才可比）。 */
export const groundMarkup = (paper: string | undefined, themeId: string, seed = 1): string =>
  renderMotif(paperMotif(paper), { palette: paletteFor(themeId), seed })

/** 角饰 SVG 内容。画在 0..20 的方格内，由调用方平移/镜像。 */
export const ornamentMarkup = (set: string | undefined, themeId: string, params?: Record<string, number>): string =>
  renderMotif(ornamentMotif(set), { palette: paletteFor(themeId), params })
