/**
 * 幻灯皮肤。
 *
 * 一套皮肤同时供两处使用，必须**同源**：
 *
 * - DOM：写成 CSS 变量（`--ppt-bg` / `--ppt-fg` …），舞台内所有颜色只读这些变量。
 *   舞台是「一块纸」，不跟随页面主题；颜色读 `--qh-*` 会在深色纸面上隐形
 *   （导图那轮踩过这个坑：比例尺用页面令牌，压到深色图纸上就看不见了）。
 * - 导出：写成不带 `#` 的 6 位 hex，pptxgenjs 只认这种格式。
 *
 * 所以这里是唯一的事实来源，改一处两边同时生效。
 */

export interface PptThemeHex {
  bg: string
  fg: string
  muted: string
  accent: string
  line: string
}

export interface PptTheme {
  id: string
  label: string
  /** 一句话说明适用场景，显示在工具条的 title 上。 */
  note: string
  hex: PptThemeHex
  /** 工具条上的双色小样。 */
  palette: [string, string]
}

export const pptThemes: PptTheme[] = [
  {
    id: 'paper',
    label: '宣纸',
    note: '默认。米白底、墨字、朱砂点睛，投影与打印都稳。',
    hex: { bg: 'FAF7F0', fg: '1F1B16', muted: '6B6259', accent: 'B5443A', line: 'DFD8CA' },
    palette: ['#FAF7F0', '#B5443A'],
  },
  {
    id: 'ink',
    label: '墨夜',
    note: '深色底，适合阶梯教室与录屏。',
    hex: { bg: '151719', fg: 'F2EFE8', muted: 'A9A49A', accent: 'C98A5B', line: '3A3D40' },
    palette: ['#151719', '#C98A5B'],
  },
  {
    id: 'celadon',
    label: '青瓷',
    note: '青绿冷调，适合学术汇报与病案讨论。',
    hex: { bg: 'F2F6F3', fg: '173B30', muted: '5C7A6E', accent: '3B7A69', line: 'D3E0D8' },
    palette: ['#F2F6F3', '#3B7A69'],
  },
  {
    id: 'cinnabar',
    label: '朱墨',
    note: '暖底强对比，适合强调禁忌与警示信息。',
    hex: { bg: 'FBF2ED', fg: '2A1815', muted: '7A5E56', accent: 'A8433A', line: 'E8D5CC' },
    palette: ['#FBF2ED', '#A8433A'],
  },
]

export const DEFAULT_THEME_ID = 'paper'

export const pptThemeById = (id: string | undefined | null): PptTheme =>
  pptThemes.find((item) => item.id === id) ?? pptThemes[0]

/** 舞台根节点上的内联变量。所有舞台内部样式都只读这几个值。 */
export const pptThemeVars = (id: string | undefined | null): Record<string, string> => {
  const theme = pptThemeById(id)
  return {
    '--ppt-bg': `#${theme.hex.bg}`,
    '--ppt-fg': `#${theme.hex.fg}`,
    '--ppt-muted': `#${theme.hex.muted}`,
    '--ppt-accent': `#${theme.hex.accent}`,
    '--ppt-line': `#${theme.hex.line}`,
  }
}
