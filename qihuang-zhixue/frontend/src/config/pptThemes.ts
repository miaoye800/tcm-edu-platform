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

/**
 * 十二套配色。
 *
 * 每套五个槽位（bg / fg / muted / accent / line），**不含字体**——字族在
 * `pptFonts.ts` 里是一个独立维度。配色与字对正交，才能 12 × 8 自由组合；
 * 绑在一起就是 12 套模板，正是要避免的东西。
 *
 * 全部满足 `fg × bg ≥ 7:1`、`accent × bg ≥ 3:1`（契约测试守着）。深色底只留一套
 * `ink`：投影环境光不确定，多数人的教室是亮的，深底反而看不清。
 */
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
  {
    id: 'indigo',
    label: '靛蓝',
    note: '冷蓝底。适合科研汇报与方剂数据页，比青瓷更沉。',
    hex: { bg: 'F4F5F9', fg: '1B2137', muted: '5C6482', accent: '2E4A86', line: 'D9DDEA' },
    palette: ['#F4F5F9', '#2E4A86'],
  },
  {
    id: 'ochre',
    label: '赭石',
    note: '土黄底。药材、产地、炮制这类「实物」主题最贴。',
    hex: { bg: 'F8F4E9', fg: '2E2418', muted: '6D5D45', accent: '96602A', line: 'E4DAC4' },
    palette: ['#F8F4E9', '#96602A'],
  },
  {
    id: 'bamboo',
    label: '苍绿',
    note: '淡绿底。适合生长、栽培、药源植物这类话题。',
    hex: { bg: 'F3F6EE', fg: '1D2A1C', muted: '5F6F55', accent: '4A7A3F', line: 'D9E2D0' },
    palette: ['#F3F6EE', '#4A7A3F'],
  },
  {
    id: 'plum',
    label: '绛紫',
    note: '冷紫调。适合经典条文、医案品读这类偏文的内容。',
    hex: { bg: 'F7F1F4', fg: '2A1622', muted: '6E5563', accent: '8C3A5E', line: 'E4D3DB' },
    palette: ['#F7F1F4', '#8C3A5E'],
  },
  {
    id: 'navy',
    label: '藏青',
    note: '蓝灰底。正式的答辩与评审场合，比靛蓝更克制。',
    hex: { bg: 'F2F5F8', fg: '13283A', muted: '54708A', accent: '1F5C8C', line: 'CFDBE5' },
    palette: ['#F2F5F8', '#1F5C8C'],
  },
  {
    id: 'moon',
    label: '月白',
    note: '近白灰底。图多文少时最不抢画面，适合满版图的稿子。',
    hex: { bg: 'F6F7F8', fg: '232A30', muted: '63707A', accent: '4A6E8A', line: 'DCE2E6' },
    palette: ['#F6F7F8', '#4A6E8A'],
  },
  {
    id: 'graphite',
    label: '黛灰',
    note: '中性灰。不确定该用什么色时选它，最不挑内容。',
    hex: { bg: 'F5F4F2', fg: '22201F', muted: '6B6561', accent: '5B5754', line: 'E0DCD7' },
    palette: ['#F5F4F2', '#5B5754'],
  },
  {
    id: 'apricot',
    label: '杏黄',
    note: '暖黄底。欢快但不轻浮，适合科普与进校园的场合。',
    hex: { bg: 'FCF6E8', fg: '2B2318', muted: '76664B', accent: 'B06F14', line: 'EFE0BF' },
    palette: ['#FCF6E8', '#B06F14'],
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
