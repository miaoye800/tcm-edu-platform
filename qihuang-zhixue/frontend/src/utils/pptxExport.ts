/**
 * 把舞台上的这份稿子，落成真的 .pptx。
 *
 * 两条铁律：
 *
 * 1. **尺寸只抄 CSS 的 cqw 数字**。舞台是 16:9、100cqw 宽，pptx 的 LAYOUT_16x9 正好
 *    10 × 5.625 英寸，于是「1cqw = 0.1 英寸」是一条干净的换算。下面所有坐标都直接
 *    写 CSS 里那个数字，再交给 `box()` 除 10——改版式时两边一起改，不会各走各的。
 * 2. **颜色只从 pptThemes 取**。主题里那份 hex 是不带 `#` 的，正好是 pptxgenjs 要的
 *    格式；这里绝不出现颜色字面量，换肤只改一处。
 *
 * 字体用英文名（SimSun / Microsoft YaHei）：PowerPoint 认的是字体族名本身，写中文
 * 名在部分版本里会回退成默认字体，一换机器版式就散了。
 */

import PptxGenJS from 'pptxgenjs'
import { pptThemeById } from '../config/pptThemes'
import type { PptTheme } from '../config/pptThemes'
import { sectionOrdinal } from './slideOrdinal'
import type { SlideNode } from '../types/ppt'

const SERIF = 'SimSun'
const SANS = 'Microsoft YaHei'

/* ── 版面尺（单位：cqw，100cqw = 10 英寸；页面高 56.25cqw） ───────────────── */
const PAGE_W = 100
const PAGE_H = 56.25
const PAD_L = 6
const PAD_T = 5.2
const PAD_R = 6
const PAD_B = 4.2
const CONTENT_W = PAGE_W - PAD_L - PAD_R
const FOOT_LINE_Y = 48.4
const FOOT_TEXT_Y = 49.5
const FOOT_H = 2.6
/** 正文可用区下沿：页脚线再留半格呼吸。 */
const BODY_BOTTOM = FOOT_LINE_Y - 1

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
 * 章节水印、总结页底色都靠它落地。
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
}

type Gen = InstanceType<typeof PptxGenJS>
type Slide = ReturnType<Gen['addSlide']>

/* ── 共用零件 ─────────────────────────────────────────────────────────────── */

const addRule = (deck: Gen, slide: Slide, color: string, x: number, y: number, w: number, h: number) => {
  slide.addShape(deck.ShapeType.rect, { ...box(x, y, w, h), fill: { color }, line: { color, width: 0 } })
}

const addPageFoot = (deck: Gen, slide: Slide, theme: PptTheme, deckTitle: string, index: number, total: number) => {
  const line = { color: theme.hex.line, width: 1 }
  slide.addShape(deck.ShapeType.line, {
    ...box(PAD_L, FOOT_LINE_Y, CONTENT_W, 0),
    line,
  })
  if (deckTitle) {
    slide.addText(deckTitle, {
      ...box(PAD_L, FOOT_TEXT_Y, 68, FOOT_H),
      fontSize: pt(1.25),
      color: theme.hex.muted,
      fontFace: SANS,
      fit: 'shrink',
      valign: 'middle',
    })
  }
  slide.addText(`${String(index + 1).padStart(2, '0')} / ${String(total).padStart(2, '0')}`, {
    ...box(PAD_L + 68, FOOT_TEXT_Y, CONTENT_W - 68, FOOT_H),
    fontSize: pt(1.25),
    color: theme.hex.muted,
    fontFace: SANS,
    align: 'right',
    valign: 'middle',
  })
}

const addHead = (deck: Gen, slide: Slide, theme: PptTheme, kicker: string, title: string) => {
  slide.addText(kicker, {
    ...box(PAD_L, PAD_T, 60, 2.6),
    fontSize: pt(1.4),
    color: theme.hex.accent,
    fontFace: SANS,
    charSpacing: pt(0.42),
    valign: 'middle',
  })
  slide.addText(title, {
    ...box(PAD_L, PAD_T + 3.2, CONTENT_W, 8),
    fontSize: pt(3.4),
    color: theme.hex.fg,
    fontFace: SERIF,
    bold: true,
    fit: 'shrink',
    lineSpacingMultiple: 1.3,
    valign: 'top',
  })
  addRule(deck, slide, theme.hex.accent, PAD_L, PAD_T + 12, 8, 0.3)
}

/* ── 各版式 ───────────────────────────────────────────────────────────────── */

const drawCover = (deck: Gen, slide: Slide, theme: PptTheme, node: SlideNode) => {
  addRule(deck, slide, theme.hex.accent, 0, 0, 0.55, PAGE_H)

  const left = PAD_L + 1.6
  slide.addText('中医药学 · 讲稿', {
    ...box(left, 17.5, 60, 2.6),
    fontSize: pt(1.5),
    color: theme.hex.accent,
    fontFace: SANS,
    charSpacing: pt(0.5),
    valign: 'middle',
  })
  slide.addText(node.title, {
    ...box(left, 20.8, CONTENT_W - 1.6 - 2, 14),
    fontSize: pt(6.2),
    color: theme.hex.fg,
    fontFace: SERIF,
    bold: true,
    fit: 'shrink',
    lineSpacingMultiple: 1.22,
    valign: 'top',
  })
  if (node.subtitle) {
    slide.addText(node.subtitle, {
      ...box(left, 36, CONTENT_W - 1.6 - 2, 4),
      fontSize: pt(2.2),
      color: theme.hex.muted,
      fontFace: SANS,
      charSpacing: pt(0.18),
      fit: 'shrink',
      valign: 'top',
    })
  }
  addRule(deck, slide, theme.hex.accent, left, 42.4, 12, 0.32)
}

const drawSection = (deck: Gen, slide: Slide, theme: PptTheme, node: SlideNode) => {
  // 章号水印：右对齐、压在标题后面，15% 的淡色靠 mix 算出来。
  // 用两位数字而不是「一、二、三」——单个「一」放大后就是一横，看着不像章号。
  const mark = sectionOrdinal(node.title)
  if (mark) {
    slide.addText(mark, {
      ...box(30, 10, 64, 36),
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
    ...box(PAD_L, 19, CONTENT_W * 0.72, 10),
    fontSize: pt(4.6),
    color: theme.hex.fg,
    fontFace: SERIF,
    bold: true,
    fit: 'shrink',
    lineSpacingMultiple: 1.3,
    valign: 'middle',
  })
  if (node.subtitle) {
    slide.addText(node.subtitle, {
      ...box(PAD_L, 29.5, CONTENT_W * 0.72, 5),
      fontSize: pt(2),
      color: theme.hex.muted,
      fontFace: SANS,
      fit: 'shrink',
      lineSpacingMultiple: 1.5,
      valign: 'top',
    })
  }
}

const drawBullets = (deck: Gen, slide: Slide, theme: PptTheme, node: SlideNode) => {
  const top = PAD_T + 15.5
  const count = Math.max(node.bullets.length, 1)
  // 只有一两条要点的页放大一档，和屏幕上的 .items.sparse 同一套规矩。
  const bodySize = node.bullets.length <= 2 ? 2.6 : 2.05
  const slot = Math.min((BODY_BOTTOM - top) / count, node.bullets.length <= 2 ? 12 : 9)

  node.bullets.forEach((bullet, index) => {
    const y = top + slot * index
    if (node.kind === 'agenda') {
      slide.addText(String(index + 1).padStart(2, '0'), {
        ...box(PAD_L, y, 6, slot),
        fontSize: pt(1.9),
        color: theme.hex.accent,
        fontFace: SERIF,
        valign: 'top',
      })
      slide.addText(bullet, {
        ...box(PAD_L + 6.4, y, CONTENT_W - 6.4, slot),
        fontSize: pt(2.05),
        color: theme.hex.fg,
        fontFace: SANS,
        fit: 'shrink',
        lineSpacingMultiple: 1.5,
        valign: 'top',
      })
      return
    }
    // 菱形点：DOM 里是 45° 旋转的小方块，这里照做；纵向跟着首行中线走。
    slide.addShape(deck.ShapeType.rect, {
      ...box(PAD_L, y + 0.75 * bodySize - 0.31, 0.62, 0.62),
      fill: { color: theme.hex.accent },
      line: { color: theme.hex.accent, width: 0 },
      rotate: 45,
    })
    slide.addText(bullet, {
      ...box(PAD_L + 1.72, y, CONTENT_W - 1.72, slot),
      fontSize: pt(bodySize),
      color: theme.hex.fg,
      fontFace: SANS,
      fit: 'shrink',
      lineSpacingMultiple: 1.5,
      valign: 'top',
    })
  })
}

const drawColumns = (deck: Gen, slide: Slide, theme: PptTheme, node: SlideNode) => {
  const top = PAD_T + 15.5
  const gap = 3.4
  const width = (CONTENT_W - gap) / 2

  slide.addShape(deck.ShapeType.line, {
    ...box(PAD_L + width + gap / 2, top, 0, BODY_BOTTOM - top),
    line: { color: theme.hex.line, width: 1 },
  })

  node.columns.slice(0, 2).forEach((column, index) => {
    const x = PAD_L + (width + gap) * index
    slide.addText(column.heading, {
      ...box(x + 1.6, top, width - 1.6, 4),
      fontSize: pt(2.4),
      color: theme.hex.accent,
      fontFace: SERIF,
      bold: true,
      fit: 'shrink',
      valign: 'middle',
    })
    const itemsTop = top + 4.6
    const slot = Math.min((BODY_BOTTOM - itemsTop) / Math.max(column.items.length, 1), 7)
    column.items.forEach((item, position) => {
      const y = itemsTop + slot * position
      // 小横杠与首行文字居中对齐（屏幕上是 0.5lh），别让它浮成连字符。
      addRule(deck, slide, theme.hex.muted, x + 1.6, y + 1.28, 0.7, 0.12)
      slide.addText(item, {
        ...box(x + 2.9, y, width - 2.9, slot),
        fontSize: pt(1.85),
        color: theme.hex.fg,
        fontFace: SANS,
        fit: 'shrink',
        lineSpacingMultiple: 1.5,
        valign: 'top',
      })
    })
  })
}

const KICKERS: Record<SlideNode['kind'], string> = {
  cover: '讲稿',
  agenda: '导览',
  section: '章节',
  bullets: '正文',
  compare: '对照',
  summary: '收束',
}

/* ── 出口 ─────────────────────────────────────────────────────────────────── */

const safeName = (value: string) => {
  const trimmed = value.replace(/[\\/:*?"<>|\n\r\t]/g, '').trim()
  return (trimmed || '中医药学讲稿').slice(0, 40)
}

/**
 * 导出并触发下载，返回实际落盘的文件名（供调用方提示）。
 *
 * 只导「你看得见的东西」：编辑态的输入框框线、胶片条页码都不进 pptx，
 * 讲者备注单独写进 notes，放映时在演讲者视图里能看到。
 */
export const exportDeckToPptx = async (input: DeckExportInput): Promise<string> => {
  const theme = pptThemeById(input.themeId)
  const total = input.slides.length
  const deckTitle = input.title

  const deck = new PptxGenJS()
  deck.layout = 'LAYOUT_16x9'
  deck.title = input.title
  deck.subject = '中医药学讲稿'
  deck.author = '岐黄智学'
  deck.company = '岐黄智学'

  input.slides.forEach((node, index) => {
    const slide = deck.addSlide()
    const tinted = node.kind === 'summary'
    slide.background = { color: tinted ? mix(theme.hex.accent, theme.hex.bg, 0.07) : theme.hex.bg }

    if (node.kind === 'cover') {
      drawCover(deck, slide, theme, node)
    } else if (node.kind === 'section') {
      drawSection(deck, slide, theme, node)
    } else {
      // 与屏幕上的版式同一条规矩：标题里已经含这个词时不再加眉标，
      // 否则导出的「本讲导览」上方会多出一枚「导览」。
      const label = KICKERS[node.kind] ?? '正文'
      addHead(deck, slide, theme, node.title.includes(label) ? '' : label, node.title)
      if (node.kind === 'compare' && node.columns.length >= 2) drawColumns(deck, slide, theme, node)
      else drawBullets(deck, slide, theme, node)
    }

    addPageFoot(deck, slide, theme, node.kind === 'cover' ? '' : deckTitle, index, total)
    if (node.note) slide.addNotes(node.note)
  })

  const fileName = `${safeName(input.title)}-${new Date().toISOString().slice(0, 10)}.pptx`
  await deck.writeFile({ fileName })
  return fileName
}
