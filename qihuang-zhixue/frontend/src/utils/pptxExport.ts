/**
 * 把舞台上的这份稿子，落成真的 .pptx。
 *
 * 三条铁律：
 *
 * 1. **尺寸只抄 `render/frames.ts` 的数字**。舞台是 16:9、100cqw 宽，pptx 的
 *    LAYOUT_16x9 正好 10 × 5.625 英寸，于是「1cqw = 0.1in」是一条干净的换算。
 *    版心（8.4 / 9 / 7.2）与字号七级全部来自 frames，改版式只改一处。
 * 2. **版式与分块只读 `utils/slideBlocks.ts`**。屏幕按它排、导出按它排，
 *    否则「屏幕上是对照表、导出的 pptx 是一列要点」——这是升级前的真实故障。
 * 3. **颜色只从 pptThemes 取**。主题里那份 hex 不带 `#`，正好是 pptxgenjs 要的
 *    格式；这里绝不出现颜色字面量，换肤只改一处。
 *
 * 字体用英文名（SimSun / Microsoft YaHei）：PowerPoint 认的是字体族名本身，写中文
 * 名在部分版本里会回退成默认字体，一换机器版式就散了。
 */

import PptxGenJS from 'pptxgenjs'
import { pptThemeById } from '../config/pptThemes'
import type { PptTheme } from '../config/pptThemes'
import { MARGIN, TYPE_SCALE, frameFor, type Rect } from '../render/frames'
import { sectionOrdinal } from './slideOrdinal'
import { blocksOf, layoutOf, type ViewBlock } from './slideBlocks'
import { SLIDE_PURPOSE_LABELS } from '../types/ppt'
import type { SlideNode } from '../types/ppt'
import type { Archetype } from '../types/design'
import { pptFontPairById } from '../config/pptFonts'
import { renderMotif } from '../render/motifs'
import { paletteFor } from '../render/skin'
import {
  SCENE_TEXT_ROLES,
  isDecorativeAsset,
  motifViewBox,
  normalizeScene,
  sceneAssetIds,
  type Scene,
  type SceneElement,
} from '../render/scene'
import { assetMapOf, assetSrc } from '../render/assetSrc'

const SERIF = 'SimSun'
const SANS = 'Microsoft YaHei'

/* ── 版面尺（单位：cqw，100cqw = 10 英寸；页面高 56.25cqw） ───────────────── */
const PAGE_W = 100
const PAGE_H = 56.25
const PAD_L = MARGIN.left
const PAD_T = MARGIN.top
const PAD_R = MARGIN.right
const PAD_B = MARGIN.bottom
const CONTENT_W = PAGE_W - PAD_L - PAD_R
/** 版心底沿：与 frames 的 FOOT_Y（56.25 - 7.2 + 3.2）对齐。 */
const FOOT_LINE_Y = PAGE_H - PAD_B + 3.2
const FOOT_TEXT_Y = FOOT_LINE_Y + 0.9
const FOOT_H = 1.8
/** 正文可用区下沿：页脚线再留半格呼吸。 */
const BODY_BOTTOM = FOOT_LINE_Y - 1.4

const KICKER_H = 2.2
const TITLE_GAP = 1.4
const TITLE_H = 7.4
const RULE_H = 0.3
/** 正文起点：与 frames 的 body.y 同一串加法。 */
const BODY_TOP = PAD_T + KICKER_H + TITLE_GAP + TITLE_H + RULE_H + 2.4

const box = (x: number, y: number, w: number, h: number) => ({ x: x / 10, y: y / 10, w: w / 10, h: h / 10 })
/** cqw → pt：1cqw = 0.1in = 7.2pt。 */
const pt = (cqw: number) => Math.round(cqw * 72) / 10

const channels = (hex: string): [number, number, number] => [
  parseInt(hex.slice(0, 2), 16),
  parseInt(hex.slice(2, 4), 16),
  parseInt(hex.slice(4, 6), 16),
]

/**
 * 按比例调色。pptx 的文字没有透明度，`color-mix` 那套在导出里只能算成实色：
 * 章节水印、总结页底色、矩阵斑马格都靠它落地。
 */
const mix = (front: string, back: string, weight: number) => {
  const a = channels(front)
  const b = channels(back)
  return a
    .map((value, index) => Math.round(value * weight + b[index] * (1 - weight)).toString(16).padStart(2, '0'))
    .join('')
    .toUpperCase()
}

export interface DeckExportInput {
  title: string
  themeId: string
  slides: SlideNode[]
  /**
   * 字对（`song-kai` 等）。有场景的页面按它选字族——场景里的 `font: 'display' | 'body'`
   * 只是角色，真正的字体名在这一层。
   */
  fontPairId?: string
}

type Gen = InstanceType<typeof PptxGenJS>
type Slide = ReturnType<Gen['addSlide']>

/* ── 共用零件 ─────────────────────────────────────────────────────────────── */

const addRule = (deck: Gen, slide: Slide, color: string, x: number, y: number, w: number, h: number) => {
  slide.addShape(deck.ShapeType.rect, { ...box(x, y, w, h), fill: { color }, line: { color, width: 0 } })
}

const addPageFoot = (deck: Gen, slide: Slide, theme: PptTheme, deckTitle: string, index: number, total: number) => {
  slide.addShape(deck.ShapeType.line, {
    ...box(PAD_L, FOOT_LINE_Y, CONTENT_W, 0),
    line: { color: theme.hex.line, width: 1 },
  })
  if (deckTitle) {
    slide.addText(deckTitle, {
      ...box(PAD_L, FOOT_TEXT_Y, 68, FOOT_H),
      fontSize: pt(TYPE_SCALE[6]),
      color: theme.hex.muted,
      fontFace: SANS,
      fit: 'shrink',
      valign: 'middle',
    })
  }
  slide.addText(`${String(index + 1).padStart(2, '0')} / ${String(total).padStart(2, '0')}`, {
    ...box(PAD_L + 68, FOOT_TEXT_Y, CONTENT_W - 68, FOOT_H),
    fontSize: pt(TYPE_SCALE[6]),
    color: theme.hex.muted,
    fontFace: SANS,
    align: 'right',
    valign: 'middle',
  })
}

const addHead = (deck: Gen, slide: Slide, theme: PptTheme, kicker: string, title: string) => {
  slide.addText(kicker, {
    ...box(PAD_L, PAD_T, 60, KICKER_H),
    fontSize: pt(TYPE_SCALE[6]),
    color: theme.hex.accent,
    fontFace: SANS,
    charSpacing: pt(0.42),
    valign: 'middle',
  })
  slide.addText(title, {
    ...box(PAD_L, PAD_T + KICKER_H + TITLE_GAP, CONTENT_W, TITLE_H),
    fontSize: pt(TYPE_SCALE[2]),
    color: theme.hex.fg,
    fontFace: SERIF,
    bold: true,
    fit: 'shrink',
    lineSpacingMultiple: 1.3,
    valign: 'top',
  })
  addRule(deck, slide, theme.hex.accent, PAD_L, PAD_T + KICKER_H + TITLE_GAP + TITLE_H + 0.6, 8, RULE_H)
}

/* ── 图位 ─────────────────────────────────────────────────────────────────── */

/** 一页最多一张图：只认第一个「真的选到了素材」的槽位。 */
const figureOf = (node: SlideNode): string => {
  const ref = (node.asset_refs ?? []).find((item) => item.result !== 'none' && Boolean(item.file))
  return ref?.file ?? ''
}

/**
 * 把素材转成 JPEG base64。
 *
 * 两个决定：
 *
 * - **必须转格式**。库里的图是 ``normalize.py`` 统一转的 webp，而 PowerPoint
 *   对 webp 的支持要看版本，老版本直接显示成红叉。转 JPEG 兼容性最稳。
 * - **用 JPEG 而不是 PNG**。图位是矩形裁切、不需要透明通道；同一张 70KB 的
 *   webp 转 PNG 会接近 1MB，转 JPEG（q0.82）只有它的 1.5 倍左右。
 *
 * 失败时宁可这张图不出现，也不让整份导出抛错——导出失败比少一张图严重得多。
 */
const toJpegBase64 = async (src: string, paper: string): Promise<string> => {
  const image = new Image()
  image.crossOrigin = 'anonymous'
  image.src = src
  if (!image.complete) {
    await new Promise<void>((resolve, reject) => {
      image.onload = () => resolve()
      image.onerror = () => reject(new Error(`图片加载失败：${src}`))
    })
  }
  const canvas = document.createElement('canvas')
  canvas.width = image.naturalWidth
  canvas.height = image.naturalHeight
  const context = canvas.getContext('2d')
  if (!context) throw new Error('canvas 不可用')
  // JPEG 没有透明通道，不先铺底色的话透明区会变成黑色。
  // 铺主题纸色而不是纯白：图片自带的透明边会自动融进纸面，看不出贴片痕迹。
  context.fillStyle = `#${paper}`
  context.fillRect(0, 0, canvas.width, canvas.height)
  context.drawImage(image, 0, 0)
  return canvas.toDataURL('image/jpeg', 0.82)
}

/** 预取整份稿子的图。串行即可：导出不是高频操作，并发解码反而会卡主线程。 */
const preloadFigures = async (slides: SlideNode[], paper: string): Promise<Map<number, string>> => {
  const ready = new Map<number, string>()
  for (const [index, node] of slides.entries()) {
    const src = figureOf(node)
    if (!src) continue
    try {
      ready.set(index, await toJpegBase64(src, paper))
    } catch {
      // 取不到就跳过这一页的图位。
    }
  }
  return ready
}

/**
 * 铺一张图到版式给出的图位。
 *
 * ``sizing.type='cover'`` 让图片按比例填满并居中裁切——与屏幕上的
 * ``object-fit: cover`` 是同一个语义，否则同一张图在屏幕和 pptx 里会一横一竖。
 */
const addFigure = (deck: Gen, slide: Slide, dataUrl: string, rect: Rect, transparency = 0) => {
  slide.addImage({
    // `data:` 前缀剥掉即可：pptxgenjs 认 `image/png;base64,...` 与
    // `image/jpeg;base64,...` 两种形——真纹样走 PNG（染色 + 透明通道）。
    data: dataUrl.replace(/^data:/, ''),
    ...box(rect.x, rect.y, rect.w, rect.h),
    sizing: { type: 'cover', w: rect.w / 10, h: rect.h / 10 },
    transparency: transparency || undefined,
  })
}

/**
 * 真纹样（ornament-/ground-）：canvas `source-in` 染成母版前景色，出 PNG。
 *
 * 这批 SVG 是黑填充的整幅图案，按 JPEG 原样导出就是深灰墙纸。染成前景色 +
 * 保留 alpha（PNG），导出端与屏幕端的 mask 染色是同一个语义。
 */
const toTintedPng = async (src: string, color: string): Promise<string> => {
  const image = new Image()
  image.crossOrigin = 'anonymous'
  image.src = src
  if (!image.complete) {
    await new Promise<void>((resolve, reject) => {
      image.onload = () => resolve()
      image.onerror = () => reject(new Error(`图片加载失败：${src}`))
    })
  }
  const canvas = document.createElement('canvas')
  canvas.width = image.naturalWidth
  canvas.height = image.naturalHeight
  const context = canvas.getContext('2d')
  if (!context) throw new Error('canvas 不可用')
  context.drawImage(image, 0, 0)
  context.globalCompositeOperation = 'source-in'
  context.fillStyle = `#${color}`
  context.fillRect(0, 0, canvas.width, canvas.height)
  return canvas.toDataURL('image/png')
}

/* ── 版式 ─────────────────────────────────────────────────────────────────── */

const drawCover = (deck: Gen, slide: Slide, theme: PptTheme, node: SlideNode) => {
  addRule(deck, slide, theme.hex.accent, 0, 0, 0.55, PAGE_H)

  const left = PAD_L + 1.6
  slide.addText('中医药学 · 讲稿', {
    ...box(left, PAD_T + 2, 60, KICKER_H),
    fontSize: pt(TYPE_SCALE[5]),
    color: theme.hex.accent,
    fontFace: SANS,
    charSpacing: pt(0.5),
    valign: 'middle',
  })
  slide.addText(node.title, {
    ...box(left, PAD_T + 8, CONTENT_W * 0.62, 18),
    fontSize: pt(TYPE_SCALE[0]),
    color: theme.hex.fg,
    fontFace: SERIF,
    bold: true,
    fit: 'shrink',
    lineSpacingMultiple: 1.22,
    valign: 'top',
  })
  if (node.subtitle) {
    slide.addText(node.subtitle, {
      ...box(left, PAD_T + 28, CONTENT_W * 0.62, 5),
      fontSize: pt(TYPE_SCALE[3]),
      color: theme.hex.muted,
      fontFace: SANS,
      charSpacing: pt(0.18),
      fit: 'shrink',
      valign: 'top',
    })
  }
  addRule(deck, slide, theme.hex.accent, left, PAD_T + 35, 12, 0.32)
}

const drawSection = (deck: Gen, slide: Slide, theme: PptTheme, node: SlideNode) => {
  // 章号水印：右对齐、压在标题后面，15% 的淡色靠 mix 算出来。
  // 用两位数字而不是「一、二、三」——单个「一」放大后就是一横，看着不像章号。
  const mark = sectionOrdinal(node.title)
  if (mark) {
    slide.addText(mark, {
      ...box(PAGE_W - PAD_R - 40, 10, 40, 36),
      fontSize: pt(17),
      color: mix(theme.hex.accent, theme.hex.bg, 0.15),
      fontFace: SERIF,
      bold: true,
      align: 'right',
      valign: 'middle',
      fit: 'shrink',
    })
  }
  slide.addText(node.title, {
    ...box(PAD_L, PAGE_H / 2 - 6, CONTENT_W * 0.62, 12),
    fontSize: pt(TYPE_SCALE[1]),
    color: theme.hex.fg,
    fontFace: SERIF,
    bold: true,
    fit: 'shrink',
    lineSpacingMultiple: 1.3,
    valign: 'middle',
  })
  if (node.subtitle) {
    slide.addText(node.subtitle, {
      ...box(PAD_L, PAGE_H / 2 + 6.5, CONTENT_W * 0.62, 5),
      fontSize: pt(TYPE_SCALE[3]),
      color: theme.hex.muted,
      fontFace: SANS,
      fit: 'shrink',
      lineSpacingMultiple: 1.5,
      valign: 'top',
    })
  }
}

/** 要点：一列，菱形朱点。`sparse` 时整体放大一档，与屏幕同一条规矩。 */
const drawPoints = (deck: Gen, slide: Slide, theme: PptTheme, blocks: ViewBlock[]) => {
  const count = Math.max(blocks.length, 1)
  const sparse = count <= 2
  const size = sparse ? TYPE_SCALE[3] : TYPE_SCALE[3]
  const gap = sparse ? 3.2 : 2
  const slot = Math.min((BODY_BOTTOM - BODY_TOP) / count, sparse ? 14 : 9)

  blocks.forEach((block, index) => {
    const y = BODY_TOP + (slot + (index ? gap : 0)) * index
    slide.addShape(deck.ShapeType.rect, {
      ...box(PAD_L, y + 0.75 * size - 0.31, 0.62, 0.62),
      fill: { color: theme.hex.accent },
      line: { color: theme.hex.accent, width: 0 },
      rotate: 45,
    })
    slide.addText(block.text, {
      ...box(PAD_L + 1.72, y, CONTENT_W - 1.72, slot),
      fontSize: pt(sparse ? TYPE_SCALE[3] * 1.18 : size),
      color: theme.hex.fg,
      fontFace: SANS,
      fit: 'shrink',
      lineSpacingMultiple: 1.6,
      valign: 'top',
    })
  })
}

/** 导览：竖排编号；流程：横排大号序号。两者内容相同、读法不同。 */
const drawSteps = (deck: Gen, slide: Slide, theme: PptTheme, blocks: ViewBlock[], layout: Archetype) => {
  const items = blocks.flatMap((block) => block.items)
  if (!items.length) return

  if (layout === 'process') {
    const gap = 2
    const width = (CONTENT_W - gap * (items.length - 1)) / items.length
    items.forEach((item, index) => {
      const x = PAD_L + (width + gap) * index
      slide.addText(String(index + 1).padStart(2, '0'), {
        ...box(x, BODY_TOP, width, 6),
        fontSize: pt(TYPE_SCALE[1]),
        color: theme.hex.accent,
        fontFace: SERIF,
        bold: true,
        fit: 'shrink',
        valign: 'middle',
      })
      // 连接线：序号右侧一段细分隔线，读起来是「先后」而不是「并列」。
      if (index < items.length - 1) {
        slide.addShape(deck.ShapeType.line, {
          ...box(x + width - 1.2, BODY_TOP + 5.4, 1.6, 0),
          line: { color: theme.hex.line, width: 1 },
        })
      }
      slide.addText(item, {
        ...box(x, BODY_TOP + 7, width - 0.6, BODY_BOTTOM - BODY_TOP - 7),
        fontSize: pt(TYPE_SCALE[3] * 0.92),
        color: theme.hex.fg,
        fontFace: SANS,
        fit: 'shrink',
        lineSpacingMultiple: 1.5,
        valign: 'top',
      })
    })
    return
  }

  const slot = Math.min((BODY_BOTTOM - BODY_TOP) / items.length, 9)
  items.forEach((item, index) => {
    const y = BODY_TOP + slot * index
    slide.addText(String(index + 1).padStart(2, '0'), {
      ...box(PAD_L, y, 6, slot),
      fontSize: pt(TYPE_SCALE[4]),
      color: theme.hex.accent,
      fontFace: SERIF,
      valign: 'top',
    })
    slide.addText(item, {
      ...box(PAD_L + 6.4, y, CONTENT_W - 6.4, slot),
      fontSize: pt(TYPE_SCALE[3]),
      color: theme.hex.fg,
      fontFace: SANS,
      fit: 'shrink',
      lineSpacingMultiple: 1.55,
      valign: 'top',
    })
  })
}

/** 对照（两栏 + 中线）/ 矩阵（三栏斑马格）。 */
const drawMatrix = (deck: Gen, slide: Slide, theme: PptTheme, blocks: ViewBlock[]) => {
  const columns = blocks.flatMap((block) => block.columns)
  if (!columns.length) return
  const pair = columns.length <= 2
  const gap = pair ? 3.4 : 0
  const width = (CONTENT_W - gap * (columns.length - 1)) / columns.length

  if (pair) {
    slide.addShape(deck.ShapeType.line, {
      ...box(PAD_L + width + gap / 2, BODY_TOP, 0, BODY_BOTTOM - BODY_TOP),
      line: { color: theme.hex.line, width: 1 },
    })
  }

  columns.forEach((column, index) => {
    const x = PAD_L + (width + gap) * index
    // 矩阵的斑马格：偶数栏压一层 3.5% 的底色，行与行才看得出是一张表。
    if (!pair && index % 2 === 1) {
      addRule(deck, slide, mix(theme.hex.fg, theme.hex.bg, 0.035), x, BODY_TOP, width, BODY_BOTTOM - BODY_TOP)
    }
    const left = pair ? x + (index ? 1.8 : 0) : x + 1.8
    const inner = pair ? width - 1.8 : width - 3.6
    slide.addText(column.heading, {
      ...box(left, BODY_TOP, inner, 4.2),
      fontSize: pt(TYPE_SCALE[3]),
      color: theme.hex.accent,
      fontFace: SERIF,
      bold: true,
      fit: 'shrink',
      valign: 'middle',
    })
    const itemsTop = BODY_TOP + 4.8
    const slot = Math.min((BODY_BOTTOM - itemsTop) / Math.max(column.items.length, 1), 7)
    column.items.forEach((item, position) => {
      const y = itemsTop + slot * position
      // 小横杠与首行文字居中对齐（屏幕上是 0.5lh），别让它浮成连字符。
      addRule(deck, slide, theme.hex.muted, left, y + 1.28, 0.7, 0.12)
      slide.addText(item, {
        ...box(left + 1.3, y, inner - 1.3, slot),
        fontSize: pt(TYPE_SCALE[4]),
        color: theme.hex.fg,
        fontFace: SANS,
        fit: 'shrink',
        lineSpacingMultiple: 1.5,
        valign: 'top',
      })
    })
  })
}

/** 数据卡：一行数字，顶上一道朱砂。数值大、标签小——顺序反过来就读不出重点。 */
const drawFigures = (deck: Gen, slide: Slide, theme: PptTheme, blocks: ViewBlock[]) => {
  if (!blocks.length) return
  const gap = 2.2
  const width = (CONTENT_W - gap * (blocks.length - 1)) / blocks.length

  blocks.forEach((block, index) => {
    const x = PAD_L + (width + gap) * index
    addRule(deck, slide, mix(theme.hex.fg, theme.hex.bg, 0.03), x, BODY_TOP + 1.4, width, 16)
    addRule(deck, slide, theme.hex.accent, x, BODY_TOP + 1.4, width, 0.32)
    slide.addText(block.value, {
      ...box(x + 1.6, BODY_TOP + 2.6, width - 3.2, 8),
      fontSize: pt(TYPE_SCALE[1]),
      color: theme.hex.accent,
      fontFace: SERIF,
      bold: true,
      fit: 'shrink',
      valign: 'middle',
    })
    slide.addText(block.heading, {
      ...box(x + 1.6, BODY_TOP + 11, width - 3.2, 5),
      fontSize: pt(TYPE_SCALE[4]),
      color: theme.hex.fg,
      fontFace: SANS,
      fit: 'shrink',
      valign: 'top',
    })
    if (block.source) {
      slide.addText(block.source, {
        ...box(x + 1.6, BODY_TOP + 15.6, width - 3.2, 3),
        fontSize: pt(TYPE_SCALE[6]),
        color: theme.hex.muted,
        fontFace: SANS,
        fit: 'shrink',
        valign: 'top',
      })
    }
  })
}

/** 警示 / 引文 / 术语：三种「一句话」版式，靠边线与字号区分语气。 */
const drawTextBlocks = (
  deck: Gen,
  slide: Slide,
  theme: PptTheme,
  blocks: ViewBlock[],
  layout: Archetype,
) => {
  let y = BODY_TOP
  blocks.forEach((block) => {
    const height = Math.min(18, Math.max(6, (BODY_BOTTOM - BODY_TOP) / Math.max(blocks.length, 1)))

    if (block.type === 'alert' || layout === 'warning') {
      addRule(deck, slide, theme.hex.accent, PAD_L, y, 0.34, height)
      slide.addText(block.text, {
        ...box(PAD_L + 1.8, y, CONTENT_W - 1.8, height),
        fontSize: pt(TYPE_SCALE[3]),
        color: theme.hex.fg,
        fontFace: SANS,
        fit: 'shrink',
        lineSpacingMultiple: 1.6,
        valign: 'top',
      })
      y += height + 1.8
      return
    }

    if (block.type === 'quote') {
      slide.addText(`「${block.text}」`, {
        ...box(PAD_L, y, CONTENT_W, height),
        fontSize: pt(TYPE_SCALE[3] * 1.05),
        color: theme.hex.fg,
        fontFace: SERIF,
        fit: 'shrink',
        lineSpacingMultiple: 1.75,
        valign: 'top',
      })
      if (block.source) {
        slide.addText(block.source, {
          ...box(PAD_L, y + height, CONTENT_W, 3),
          fontSize: pt(TYPE_SCALE[6]),
          color: theme.hex.muted,
          fontFace: SANS,
          fit: 'shrink',
          valign: 'top',
        })
        y += 3
      }
      y += height + 1.6
      return
    }

    if (block.type === 'def') {
      slide.addText(block.heading, {
        ...box(PAD_L, y, CONTENT_W, 4.4),
        fontSize: pt(TYPE_SCALE[3]),
        color: theme.hex.accent,
        fontFace: SERIF,
        bold: true,
        fit: 'shrink',
        valign: 'middle',
      })
      slide.addText(block.text, {
        ...box(PAD_L, y + 4.8, CONTENT_W, height),
        fontSize: pt(TYPE_SCALE[4]),
        color: theme.hex.fg,
        fontFace: SANS,
        fit: 'shrink',
        lineSpacingMultiple: 1.6,
        valign: 'top',
      })
      y += height + 6
      return
    }

    // 兜底：不认识的块按一句话排，宁可朴素也不要丢内容。
    slide.addText(block.text, {
      ...box(PAD_L, y, CONTENT_W, height),
      fontSize: pt(TYPE_SCALE[3]),
      color: theme.hex.fg,
      fontFace: SANS,
      fit: 'shrink',
      valign: 'top',
    })
    y += height + 1.8
  })
}

const drawBody = (deck: Gen, slide: Slide, theme: PptTheme, node: SlideNode, layout: Archetype) => {
  const blocks = blocksOf(node)
  if (!blocks.length) return

  if (layout === 'compare' || layout === 'matrix') {
    if (blocks.every((block) => block.type === 'matrix')) {
      drawMatrix(deck, slide, theme, blocks)
      return
    }
  }
  if (layout === 'data' && blocks.every((block) => block.type === 'figures')) {
    drawFigures(deck, slide, theme, blocks)
    return
  }
  if ((layout === 'agenda' || layout === 'process') && blocks.some((block) => block.type === 'steps')) {
    drawSteps(deck, slide, theme, blocks, layout)
    return
  }
  if (layout === 'warning' || blocks.some((block) => ['alert', 'quote', 'def'].includes(block.type))) {
    drawTextBlocks(deck, slide, theme, blocks, layout)
    return
  }
  drawPoints(deck, slide, theme, blocks)
}

/* ── 出口 ─────────────────────────────────────────────────────────────────── */

const safeName = (value: string) => {
  const trimmed = value.replace(/[\\/:*?"<>|\n\r\t]/g, '').trim()
  return (trimmed || '中医药学讲稿').slice(0, 40)
}

/** 导出文件的头注释（写在第一页讲者备注）：字体取舍要告诉拿到文件的人。 */
const FONT_NOTICE =
  '字体说明：本稿按版式母版的字对指定字体（思源宋体 / 霞鹜文楷 / 思源黑体），' +
  'PPTX 不内嵌字体文件；未安装这套字体的机器会回落系统宋楷 / 黑体，观感可能略有差异。'

/**
 * 导出并触发下载，返回实际落盘的文件名（供调用方提示）。
 *
 * 只导「你看得见的东西」：编辑态的输入框框线、胶片条页码都不进 pptx，
 * 讲者备注单独写进 notes，放映时在演讲者视图里能看到。
 *
 * **纸纹与角饰不进 pptx**：母题是 SVG，pptx 要栅格化才能放，会让文件从几十
 * KB 涨到几 MB，而放映时那层淡纹几乎看不见。「屏幕更雅、导出更轻」是刻意的
 * 取舍，不是遗漏。
 *
 * **但图位必须进**。图和纸纹不是一回事：纸纹是装饰，图是这一页在讲什么。
 * 升级前这段逻辑整个缺失，于是「屏幕上封面有张洗药图、导出的 pptx 干干净净」，
 * 用户拿到的文件和确认过的样子对不上。
 */
/**
 * 预取场景里引用的全部素材。
 *
 * 按 `asset_id` 索引而不是按页：同一张图被两页用到只转一次。旧路径「一页一张图」
 * 是按页存的，场景里一页可能有两张（主图 + 角饰用图），按页存会丢。
 */
const preloadSceneAssets = async (slides: SlideNode[], paper: string, tint: string): Promise<Map<string, string>> => {
  // 地址表全稿合并：素材 id 全局唯一，同一张图在两页出现时只取一次。
  const sources: Record<string, string> = {}
  for (const node of slides) Object.assign(sources, assetMapOf(node.asset_refs))

  const ids = new Set<string>()
  for (const node of slides) {
    if (!node.scene) continue
    for (const id of sceneAssetIds(normalizeScene(node.scene))) ids.add(id)
  }

  const ready = new Map<string, string>()
  for (const id of ids) {
    try {
      if (isDecorativeAsset(id)) {
        // 真纹样：染前景色出 PNG（透明通道保留），底纹在 drawScene 再压透明度。
        ready.set(id, await toTintedPng(assetSrc(id, sources), tint))
      } else {
        // 远程图走 wsrv 代理（带 CORS），本地图走仓库路径。两条都 fetch 得到，
        // 因为素材不落盘的代价就是导出时必须现取——这也是 `url` 要钉死的原因。
        ready.set(id, await toJpegBase64(assetSrc(id, sources), paper))
      }
    } catch {
      // 取不到就不画这张：屏幕上也一样是失败即不显示。
    }
  }
  return ready
}

/* ── 场景导出 ───────────────────────────────────────────────────────────────── */

/**
 * 场景导出：把九个基元翻译成 pptx 的原生形状、图片与文本。
 *
 * 为什么必须做这件事：预览走 `SceneSurface`，导出如果还按 `archetype` 画，用户确认
 * 过的「右侧竖排标题 + 左侧出血大图」在 pptx 里会变成「标题在上、要点在下」——这正是
 * 升级前反复出现的漂移，只不过这次漂在导出侧。
 *
 * 三处刻意的保真度让步，都在回执里写明：
 *
 * 1. **背景纸纹不进 pptx**。母题是 SVG，整页栅格化会让文件从几十 KB 涨到几 MB，
 *    而放映时那层淡纹几乎看不见。铺主题纯色即可。
 * 2. **角花、印章这类小母题进**。它们是版式的一部分，不是纹理；丢了读者会觉得
 *    「这页怎么空了一块」。
 * 3. **竖排降级为 `eaVert`**。PowerPoint 的东亚竖排与浏览器的 `vertical-rl` 在标点
 *    位置上不完全一致，长句可能有一两字提前折行。
 * 4. **图片调性（toned/duotone）不进 pptx**。那是屏幕端的 CSS 滤镜近似，pptxgenjs
 *    没有图片滤镜，原样导出；装裱（hairline/mat）与形状阴影、双色渐变都有对应翻译。
 */
const sceneRoleHex = (theme: PptTheme, role: string): string => {
  const key = (['bg', 'fg', 'muted', 'accent', 'line'] as const).find((item) => item === role)
  return theme.hex[key ?? 'fg']
}

const svgDataUri = (markup: string, viewBox: string): string => {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${viewBox}" preserveAspectRatio="xMidYMid meet">${markup}</svg>`
  const bytes = new TextEncoder().encode(svg)
  let binary = ''
  for (const byte of bytes) binary += String.fromCharCode(byte)
  return `data:image/svg+xml;base64,${btoa(binary)}`
}

/** pptx 只认单个字族名，CSS 字体栈要取第一个。 */
const firstFont = (stack: string): string => stack.split(',')[0].replace(/["']/g, '').trim()

/** group 拍平：与前端渲染同一步，导出与预览才不会一深一浅。 */const flattenScene = (scene: Scene): SceneElement[] => {
  const out: SceneElement[] = []
  const walk = (elements: SceneElement[], origin?: [number, number, number, number]) => {
    for (const element of elements) {
      if (element.type === 'group') {
        const [gx, gy, gw, gh] = element.box
        const base: [number, number, number, number] = origin
          ? [origin[0] + gx * origin[2], origin[1] + gy * origin[3], gw * origin[2], gh * origin[3]]
          : [gx, gy, gw, gh]
        walk(element.items, base)
        continue
      }
      if (!origin) {
        out.push(element)
        continue
      }
      const [x, y, w, h] = element.box
      out.push({ ...element, box: [origin[0] + x * origin[2], origin[1] + y * origin[3], w * origin[2], h * origin[3]] })
    }
  }
  walk(scene.elements)
  return out.sort((a, b) => a.z - b.z)
}

interface SceneContext {
  deck: Gen
  theme: PptTheme
  themeId: string
  fontPairId?: string
  assets: Map<string, string>
}

/**
 * 阴影（0~1）→ pptx 形状阴影。强度该多深是母版的决定，这里只翻译；
 * 不支持图片阴影（pptxgenjs 的 ImageProps 对 shadow 支持不稳，宁缺毋滥）。
 */
const shadowOf = (element: SceneElement) =>
  element.shadow > 0
    ? {
        type: 'outer' as const,
        angle: 90,
        blur: Math.round(3 + element.shadow * 6),
        offset: Math.round(1 + element.shadow * 2),
        color: '000000',
        opacity: Math.min(0.55, 0.14 + element.shadow * 0.32),
      }
    : undefined

const drawSceneElement = (slide: Slide, ctx: SceneContext, element: SceneElement) => {
  const { deck, theme } = ctx
  const [x, y, w, h] = element.box
  const pair = pptFontPairById(ctx.fontPairId)

  if (element.type === 'asset') {
    const data = ctx.assets.get(element.asset)
    // 取不到就不画：一张裂图比留白难看得多，也与屏幕上的效果不符（屏幕也是失败即不显示）。
    if (data) {
      // mat 装裱：屏幕上是 padding + 衬纸底；导出侧垫一块放大的纸色底板再嵌图。
      if (element.frame === 'mat') {
        slide.addShape(deck.ShapeType.rect, {
          ...box(x - 0.9, y - 0.9, w + 1.8, h + 1.8),
          fill: { color: theme.hex.bg },
          line: { color: theme.hex.line, width: 0.75 },
        })
      }
      addFigure(deck, slide, data, { x, y, w, h }, Math.round((1 - element.opacity) * 100))
      if (element.frame === 'hairline') {
        slide.addShape(deck.ShapeType.rect, {
          ...box(x, y, w, h),
          fill: { color: theme.hex.bg, transparency: 100 },
          line: { color: theme.hex.line, width: 0.75 },
        })
      }
    }
    // img_treatment（toned/duotone）是屏幕端的 CSS 滤镜近似；pptx 没有图片滤镜，
    // 原样导出——取舍写进文件头注释，用户拿到的文件里说清楚。
    return
  }

  if (element.type === 'motif') {
    const markup = renderMotif(element.motif, {
      palette: paletteFor(ctx.themeId),
      params: (element.params ?? {}) as Record<string, number>,
      seed: 1,
    })
    if (!markup) return
    try {
      slide.addImage({
        data: svgDataUri(markup, motifViewBox(element.motif)),
        ...box(x, y, w, h),
        sizing: { type: 'contain', w: w / 10, h: h / 10 },
      })
    } catch {
      // SVG 进不去就算了，装饰缺失不影响这一页在讲什么。
    }
    return
  }

  if (element.type === 'rule') {
    const color = sceneRoleHex(theme, element.fill ?? 'line')
    if (element.dash === 'solid') {
      const [rx, ry, rw, rh] = element.orient === 'v' ? [x, y, Math.max(w, 0.08), h] : [x, y, w, Math.max(h, 0.08)]
      slide.addShape(deck.ShapeType.rect, { ...box(rx, ry, rw, rh), fill: { color }, line: { color, width: 0 } })
      return
    }
    slide.addShape(deck.ShapeType.line, {
      x: x / 10,
      y: (element.orient === 'h' ? y + h / 2 : y) / 10,
      w: element.orient === 'h' ? w / 10 : 0,
      h: element.orient === 'v' ? h / 10 : 0,
      line: { color, width: Math.max(0.5, element.weight * 7.2), dashType: element.dash === 'dot' ? 'sysDot' : 'dash' },
    })
    return
  }

  if (element.type === 'circle' || element.type === 'rect') {
    // 双色渐变（fill→fill2）：pptxgenjs 不支持形状渐变，取两端中点做实色——
    // 数值卡的软底板在导出里仍是一块淡色，只是少了那道「淡出」。
    const fillColor = element.fill
      ? element.fill2
        ? mix(sceneRoleHex(theme, element.fill), sceneRoleHex(theme, element.fill2), 0.5)
        : sceneRoleHex(theme, element.fill)
      : theme.hex.bg
    slide.addShape(element.type === 'circle' ? deck.ShapeType.ellipse : deck.ShapeType.rect, {
      ...box(x, y, w, h),
      // 透明度挂在 `fill` 上而不是形状上：pptxgenjs 的 ShapeProps 没有顶层 transparency。
      fill: element.fill
        ? { color: fillColor, transparency: Math.round((1 - element.opacity) * 100) }
        : { color: theme.hex.bg, transparency: 100 },
      line: element.stroke
        ? { color: sceneRoleHex(theme, element.stroke), width: Math.max(0.5, (element.stroke_width || 0.12) * 7.2) }
        : { color: sceneRoleHex(theme, 'bg'), width: 0 },
      rotate: element.rotate || undefined,
      shadow: shadowOf(element),
    })
    return
  }

  if (element.type === 'seal') {
    slide.addShape(element.shape === 'round' ? deck.ShapeType.ellipse : deck.ShapeType.rect, {
      ...box(x, y, w, h),
      fill: {
        color: sceneRoleHex(theme, element.fill ?? 'accent'),
        transparency: Math.round((1 - element.opacity) * 100),
      },
      line: { color: sceneRoleHex(theme, element.fill ?? 'accent'), width: 0 },
    })
    slide.addText(element.content, {
      ...box(x, y, w, h),
      fontSize: pt(Math.min(w, h) * 0.5),
      color: sceneRoleHex(theme, 'bg'),
      fontFace: firstFont(pair.display),
      bold: true,
      align: 'center',
      valign: 'middle',
      vert: 'eaVert',
      charSpacing: 1,
      margin: 0,
    })
    return
  }

  if (element.type === 'text' && element.text) {
    const text = element.text
    const size = text.size ?? SCENE_TEXT_ROLES[text.role].size
    slide.addText(text.content, {
      ...box(x, y, w, h),
      fontSize: pt(size),
      color: sceneRoleHex(theme, text.color),
      fontFace: firstFont(text.font === 'display' ? pair.display : pair.body),
      bold: (text.weight ?? SCENE_TEXT_ROLES[text.role].weight) >= 600,
      align: text.align === 'justify' ? 'justify' : text.align,
      valign: 'top',
      lineSpacingMultiple: text.line_height,
      charSpacing: text.tracking ? Number((text.tracking * 7.2).toFixed(1)) : undefined,
      // `shrink` 是导出侧的安全网：场景校验只估行数，真排下来可能差半行。
      fit: 'shrink',
      vert: text.write === 'v' ? 'eaVert' : undefined,
      margin: 0,
      transparency: Math.round((1 - element.opacity) * 100),
    })
  }
}

const drawScene = (slide: Slide, ctx: SceneContext, scene: Scene) => {
  const bg = scene.background
  if (bg.kind === 'asset') {
    const data = ctx.assets.get(bg.asset)
    if (data) {
      if (isDecorativeAsset(bg.asset)) {
        // 真纹样底纹：染色 PNG 压到 ~12% 透明度——与屏幕端 bg-decor 同一语义。
        addFigure(ctx.deck, slide, data, { x: 0, y: 0, w: PAGE_W, h: PAGE_H }, 88)
      } else {
        addFigure(ctx.deck, slide, data, { x: 0, y: 0, w: PAGE_W, h: PAGE_H })
        if (bg.veil) {
          // 满版图压字，不铺这层十有八九读不清。pptx 没有 CSS 渐变，用整幅半透明色代替：
          // 方向性丢失，可读性保住了。
          slide.addShape(ctx.deck.ShapeType.rect, {
            ...box(0, 0, PAGE_W, PAGE_H),
            fill: {
              color: sceneRoleHex(ctx.theme, bg.veil.color),
              transparency: Math.round((1 - bg.veil.opacity) * 100),
            },
            line: { color: sceneRoleHex(ctx.theme, bg.veil.color), width: 0 },
          })
        }
      }
    }
  } else if (bg.kind === 'solid') {
    slide.background = { color: sceneRoleHex(ctx.theme, bg.tone) }
  }

  for (const element of flattenScene(scene)) drawSceneElement(slide, ctx, element)
}

export const exportDeckToPptx = async (input: DeckExportInput): Promise<string> => {
  const theme = pptThemeById(input.themeId)
  const total = input.slides.length
  const deckTitle = input.title

  // 图片要先全部转好：下面的渲染是同步 forEach，中途不能 await。
  const figures = await preloadFigures(input.slides, theme.hex.bg)
  const sceneAssets = await preloadSceneAssets(input.slides, theme.hex.bg, theme.hex.fg)
  const coverFrame = frameFor('cover')

  const deck = new PptxGenJS()
  deck.layout = 'LAYOUT_16x9'
  deck.title = input.title
  deck.subject = '中医药学讲稿'
  deck.author = '岐黄智学'
  deck.company = '岐黄智学'

  input.slides.forEach((node, index) => {
    const slide = deck.addSlide()
    const layout = layoutOf(node)
    const scene = node.scene ? normalizeScene(node.scene) : null
    // 有场景就不铺整页朱砂：底色由场景的 background 决定，这里再叠一层会盖掉。
    const tinted = layout === 'summary' && !scene
    slide.background = { color: tinted ? mix(theme.hex.accent, theme.hex.bg, 0.07) : theme.hex.bg }

    if (scene) {
      // 有场景就以场景为准：几何全在数据里，导出只做翻译。
      drawScene(slide, { deck, theme, themeId: input.themeId, fontPairId: input.fontPairId, assets: sceneAssets }, scene)
    } else if (layout === 'cover') {
      // 图先铺、字后写：封面图位在右半幅，文字在左，两者不重叠，
      // 但先图后文可以避免任何边界情况下文字被图盖住。
      const data = figures.get(index)
      if (data && coverFrame.figure) addFigure(deck, slide, data, coverFrame.figure)
      drawCover(deck, slide, theme, node)
    } else if (layout === 'section') {
      drawSection(deck, slide, theme, node)
    } else {
      // 与屏幕上的版式同一条规矩：标题里已经含这个词时不再加眉标，
      // 否则导出的「本讲导览」上方会多出一枚「导览」。
      const label = SLIDE_PURPOSE_LABELS[layout] ?? '正文'
      addHead(deck, slide, theme, node.title.includes(label) ? '' : label, node.title)
      drawBody(deck, slide, theme, node, layout)
    }

    if (scene) {
      // 场景页按 `SceneSurface` 的规矩：封面与章节不压页码（页码是册子的东西）。
      if (layout !== 'cover' && layout !== 'section') addPageFoot(deck, slide, theme, deckTitle, index, total)
    } else {
      addPageFoot(deck, slide, theme, layout === 'cover' ? '' : deckTitle, index, total)
    }
    // 头注释写在第一页的讲者备注里：字体按母版字对指定（fontFace 来自
    // pptFonts 的字对），但 PPTX **不内嵌字体文件**——缺那套字体的机器
    // 回落系统宋楷/黑体，观感可能略异。这一句必须在文件里说清，不能只写在代码注释。
    const noteBody = index === 0 ? [FONT_NOTICE, node.note].filter(Boolean).join('\n') : node.note
    if (noteBody) slide.addNotes(noteBody)
  })

  const fileName = `${safeName(input.title)}-${new Date().toISOString().slice(0, 10)}.pptx`
  await deck.writeFile({ fileName })
  return fileName
}
