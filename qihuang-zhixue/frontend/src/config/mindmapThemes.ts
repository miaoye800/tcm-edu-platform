/**
 * 四套导图配色（按参考图取色）。
 *
 * 四套共用同一套排版语法，只换颜色：
 *   root 20/600 实色块 · 一级 15/600 药牌 · 二级 14/500 纯文字 · 三级 13/400 白底细框
 *   主枝 1.6 · 大量留白
 *
 * 之所以不再一套一个字号梯度：旧版 8 套主题的差异主要在色相，
 * 本质是「一个模板换八次颜色」——那正是模板感的来源。
 *
 * 层级与 simple-mind-map 的映射（已核实 `src/core/render/node/Style.js:86`）：
 *   layerIndex 0 → themeConfig.root
 *   layerIndex 1 → themeConfig.second   ← 本文件的 branch
 *   layerIndex ≥2 → themeConfig.node    ← 本文件的 leaf
 * 库只有这三档，第四档（二级纯文字）靠逐节点样式覆盖，
 * 见 `utils/mindmapTree.ts:toSimpleTree` 的 fourTier 选项。
 */

export interface ThemeLevelStyle {
  fillColor: string
  color: string
  borderColor: string
  borderWidth: number
  borderRadius: number
  fontSize: number
  fontWeight: string
}

export interface MindmapThemePreset {
  id: string
  name: string
  vibe: string
  /** 用于选择器色卡：根色 / 一级色 / 线色 */
  swatches: [string, string, string]
  backgroundColor: string
  /** 画布内文字基色，供网格、比例尺等画布外元素取色用 */
  textColor: string
  lineColor: string
  lineWidth: number
  root: ThemeLevelStyle
  branch: ThemeLevelStyle
  leaf: ThemeLevelStyle
}

/** 四套主题只允许改色值；字号/圆角/间距由 buildSimpleTheme 统一注入。 */
export const mindmapThemes: MindmapThemePreset[] = [
  {
    id: 'indigo',
    name: '靛蓝',
    vibe: '清晰，课堂投影最稳',
    swatches: ['#2E6BE6', '#F1F3F6', '#3B7BEA'],
    backgroundColor: '#FFFFFF',
    textColor: '#1D2129',
    lineColor: '#3B7BEA',
    lineWidth: 1.6,
    root: { fillColor: '#2E6BE6', color: '#FFFFFF', borderColor: 'transparent', borderWidth: 0, borderRadius: 8, fontSize: 20, fontWeight: '600' },
    branch: { fillColor: '#F1F3F6', color: '#1D2129', borderColor: '#E4E7EC', borderWidth: 1, borderRadius: 6, fontSize: 15, fontWeight: '600' },
    leaf: { fillColor: '#FFFFFF', color: '#1D2129', borderColor: '#BFD2F2', borderWidth: 1, borderRadius: 4, fontSize: 13, fontWeight: '400' },
  },
  {
    id: 'verdant',
    name: '青绿',
    vibe: '有性格，青绿撞深紫',
    swatches: ['#ABD24E', '#3A2D63', '#8FBE3F'],
    backgroundColor: '#FFFFFF',
    textColor: '#1D2129',
    lineColor: '#8FBE3F',
    lineWidth: 1.6,
    root: { fillColor: '#ABD24E', color: '#17200A', borderColor: 'transparent', borderWidth: 0, borderRadius: 8, fontSize: 20, fontWeight: '600' },
    branch: { fillColor: '#3A2D63', color: '#FFFFFF', borderColor: 'transparent', borderWidth: 0, borderRadius: 6, fontSize: 15, fontWeight: '600' },
    leaf: { fillColor: '#FFFFFF', color: '#1D2129', borderColor: '#B7D672', borderWidth: 1, borderRadius: 4, fontSize: 13, fontWeight: '400' },
  },
  {
    id: 'violet',
    name: '紫罗兰',
    vibe: '稳重，适合重点章节',
    swatches: ['#4A2A8C', '#3D5AFE', '#7C5CD8'],
    backgroundColor: '#FFFFFF',
    textColor: '#1D2129',
    lineColor: '#7C5CD8',
    lineWidth: 1.6,
    root: { fillColor: '#4A2A8C', color: '#FFFFFF', borderColor: 'transparent', borderWidth: 0, borderRadius: 8, fontSize: 20, fontWeight: '600' },
    branch: { fillColor: '#3D5AFE', color: '#FFFFFF', borderColor: 'transparent', borderWidth: 0, borderRadius: 6, fontSize: 15, fontWeight: '600' },
    leaf: { fillColor: '#FFFFFF', color: '#1D2129', borderColor: '#B9A9E6', borderWidth: 1, borderRadius: 4, fontSize: 13, fontWeight: '400' },
  },
  {
    id: 'inkCream',
    name: '藏青米',
    vibe: '沉、雅，接近宣纸墨色',
    swatches: ['#16324E', '#EFE4CC', '#CE8C7C'],
    backgroundColor: '#FFFFFF',
    textColor: '#1D2129',
    lineColor: '#CE8C7C',
    lineWidth: 1.6,
    root: { fillColor: '#16324E', color: '#FFFFFF', borderColor: 'transparent', borderWidth: 0, borderRadius: 8, fontSize: 20, fontWeight: '600' },
    branch: { fillColor: '#EFE4CC', color: '#3A2F1E', borderColor: 'transparent', borderWidth: 0, borderRadius: 6, fontSize: 15, fontWeight: '600' },
    leaf: { fillColor: '#FFFFFF', color: '#1D2129', borderColor: '#D6C7A9', borderWidth: 1, borderRadius: 4, fontSize: 13, fontWeight: '400' },
  },
]

export const defaultThemeId = 'indigo'

export const getThemePreset = (id?: string | null): MindmapThemePreset =>
  mindmapThemes.find(item => item.id === id) ?? mindmapThemes[0]

/** 存量的旧主题 id（8 套时代的英文名）统一落到默认配色，避免打开就「选中态空白」。 */
export const resolveThemeId = (id?: string | null): string => getThemePreset(id).id

/** 画布外元素（网格底纹、比例尺）取色用。 */
export const themeCssVars = (preset: MindmapThemePreset) => ({
  '--mm-bg': preset.backgroundColor,
  '--mm-text': preset.textColor,
  '--mm-accent': preset.lineColor,
})

/** 「模型推断」角标底色：参照朱砂，但压到最淡——不当徽章看，只当提示看。 */
export const INFERRED_TAG = '推断'
export const tagsColorMap: Record<string, string> = { [INFERRED_TAG]: '#C4866A' }

interface StructureGaps {
  lineStyle: 'curve' | 'straight' | 'direct'
  gaps: { parentX: number; parentY: number; childX: number; childY: number }
}

/**
 * 翻译成 simple-mind-map 的 themeConfig。
 *
 * 该库会把它与内置 default 主题 deepmerge（`index.js:370`），
 * 所以这里只写「要覆盖的键」，不必重复 fontFamily 之类的默认值。
 * 但字体必须显式给：库默认是「微软雅黑」，与项目字体栈不一致。
 */
export const buildSimpleTheme = (preset: MindmapThemePreset, structure: StructureGaps) => {
  const fontFamily = "'Noto Sans SC', 'PingFang SC', 'Microsoft YaHei', sans-serif"
  const level = (style: ThemeLevelStyle) => ({
    shape: 'rectangle',
    fillColor: style.fillColor,
    color: style.color,
    borderColor: style.borderColor,
    borderWidth: style.borderWidth,
    borderRadius: style.borderRadius,
    fontSize: style.fontSize,
    fontWeight: style.fontWeight,
    fontFamily,
    textDecoration: 'none',
    gradientStyle: false,
  })

  return {
    backgroundColor: preset.backgroundColor,
    lineColor: preset.lineColor,
    lineWidth: preset.lineWidth,
    lineStyle: structure.lineStyle,
    lineDasharray: 'none',
    showLineMarker: false,
    // 直角折线（direct）时也不至于变成生硬的 90°。
    lineRadius: 6,
    paddingX: 10,
    paddingY: 5,
    root: level(preset.root),
    second: { ...level(preset.branch), marginX: structure.gaps.parentX, marginY: structure.gaps.parentY },
    node: { ...level(preset.leaf), marginX: structure.gaps.childX, marginY: structure.gaps.childY },
    generalization: level(preset.branch),
  }
}
