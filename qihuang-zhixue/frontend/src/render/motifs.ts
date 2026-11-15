/**
 * 参数化母题：底纹、远山、角饰、界格、印章、水纹，全部由代码现场生成。
 *
 * 为什么不用手画 SVG：`public/story/herbs/*.svg` 那四个 777 字节占位卡已经
 * 证明过一次——手工资产会腐化，参数化函数不会。母题随主题换色、无限多样、
 * 零许可、零体积。
 *
 * 坐标系与 `frames.ts` 一致：100 × 56.25（16:9 的 cqw）。角饰函数画在 0..20 的
 * 小方格内，由调用方平移与缩放——这样同一个母题能放左上，也能放右下。
 *
 * 目录（有哪些母题、参数范围）在后端 `app/ppt/motifs.py`，这里是**渲染**。
 * 两边的连接点是构建期生成的 `motifs.catalog.ts`。
 */

import type { MotifSpec } from './motifs.catalog'

export interface MotifPalette {
  bg: string
  fg: string
  muted: string
  accent: string
  line: string
}

export interface MotifContext {
  palette: MotifPalette
  params?: Record<string, number>
  /** 让同一页的底纹稳定可复现：同一 seed 永远同一张图。 */
  seed?: number
}

type MotifRenderer = (ctx: MotifContext) => string

/** 线性同余，确定性伪随机。用 Math.random 会让截图基线每次都变。 */
const random = (seed: number) => {
  let state = (seed % 2147483647) || 7
  return () => {
    state = (state * 1103515245 + 12345) % 2147483648
    return state / 2147483648
  }
}

const num = (ctx: MotifContext, key: string, fallback: number) => {
  const value = ctx.params?.[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

/* ── 植物纹样的零件 ─────────────────────────────────────────────────────
 *
 * 角花之所以像角花，靠三件事：**主茎有走向、笔有提按、末端会卷**。
 * 用同心圆弧是画不出这三样的——圆弧没有起点也没有终点，所以永远像占位符。
 *
 * 下面这几个函数是「笔」：给一条曲线，采样成点，再按点逐段描，每段换粗细。
 * SVG 的 stroke 没有变宽能力，只能这样分段模拟。0..20 的方格上采 24 段已经看不出接缝。
 */

type Pt = { x: number; y: number }

const cubicPoint = (p0: Pt, p1: Pt, p2: Pt, p3: Pt, t: number): Pt => {
  const u = 1 - t
  return {
    x: u * u * u * p0.x + 3 * u * u * t * p1.x + 3 * u * t * t * p2.x + t * t * t * p3.x,
    y: u * u * u * p0.y + 3 * u * u * t * p1.y + 3 * u * t * t * p2.y + t * t * t * p3.y,
  }
}

const cubicAngle = (p0: Pt, p1: Pt, p2: Pt, p3: Pt, t: number): number => {
  const u = 1 - t
  const dx = 3 * u * u * (p1.x - p0.x) + 6 * u * t * (p2.x - p1.x) + 3 * t * t * (p3.x - p2.x)
  const dy = 3 * u * u * (p1.y - p0.y) + 6 * u * t * (p2.y - p1.y) + 3 * t * t * (p3.y - p2.y)
  return Math.atan2(dy, dx)
}

/** 等距采样一条三次贝塞尔，带上每点的切线方向（叶子要顺着茎长）。 */
const sampleCubic = (p0: Pt, p1: Pt, p2: Pt, p3: Pt, steps: number) =>
  Array.from({ length: steps + 1 }, (_, index) => {
    const t = index / steps
    return { ...cubicPoint(p0, p1, p2, p3, t), angle: cubicAngle(p0, p1, p2, p3, t), t }
  })

/**
 * 一条**渐细的笔画**。从 `w0` 收到 `w1`，按点逐段描线。
 *
 * `round` 收笔用圆头：枝条末端是尖的，圆头配上接近 0 的尾宽看起来就是笔锋。
 */
const taper = (pts: { x: number; y: number }[], w0: number, w1: number, color: string, opacity: number): string => {
  if (pts.length < 2) return ''
  const segments: string[] = []
  for (let index = 0; index < pts.length - 1; index += 1) {
    const ratio = index / Math.max(1, pts.length - 2)
    const width = w0 + (w1 - w0) * ratio
    const a = pts[index]
    const b = pts[index + 1]
    segments.push(
      `<line x1="${a.x.toFixed(2)}" y1="${a.y.toFixed(2)}" x2="${b.x.toFixed(2)}" y2="${b.y.toFixed(2)}" stroke="${color}" stroke-width="${width.toFixed(3)}" stroke-linecap="round" opacity="${opacity}"/>`,
    )
  }
  return segments.join('')
}

/**
 * 一片**柳叶**：两条对称弧围成的尖叶，中间一道叶脉。
 *
 * 用椭圆当叶子是角花变丑的头号原因——椭圆没有尖端、没有方向，
 * 三五个排在一起就像气泡。柳叶一头尖一头钝，朝着阳光的方向。
 */
const leaf = (x: number, y: number, rad: number, len: number, width: number, color: string, opacity: number): string => {
  const tipX = len.toFixed(2)
  const body = `M0 0 C${(len * 0.26).toFixed(2)} ${(-width).toFixed(2)} ${(len * 0.7).toFixed(2)} ${(-width * 0.82).toFixed(2)} ${tipX} 0 C${(len * 0.7).toFixed(2)} ${(width * 0.82).toFixed(2)} ${(len * 0.26).toFixed(2)} ${width.toFixed(2)} 0 0 Z`
  const vein = `M${(len * 0.05).toFixed(2)} 0 L${(len * 0.8).toFixed(2)} 0`
  const deg = ((rad * 180) / Math.PI).toFixed(1)
  return (
    `<g transform="translate(${x.toFixed(2)} ${y.toFixed(2)}) rotate(${deg})">` +
    `<path d="${body}" fill="${color}" opacity="${opacity}"/>` +
    `<path d="${vein}" stroke="${color}" stroke-width="${(width * 0.16).toFixed(3)}" opacity="${(opacity * 1.5).toFixed(2)}"/>` +
    `</g>`
  )
}

/**
 * 一根**卷须**：阿基米德螺旋，从粗卷到细，收到中心。
 *
 * 这是卷草纹的签名笔。圆环（同心圆）是死物，螺旋是活的——它一路往回收。
 */
const tendril = (x: number, y: number, rad: number, r0: number, turns: number, color: string, opacity: number): string => {
  const steps = Math.round(turns * 16)
  const pts: Pt[] = []
  for (let index = 0; index <= steps; index += 1) {
    const t = index / steps
    const theta = rad + t * turns * Math.PI * 2
    const r = r0 * (1 - t * 0.82)
    pts.push({ x: x + Math.cos(theta) * r, y: y + Math.sin(theta) * r })
  }
  return taper(pts, 0.2, 0.05, color, opacity)
}

/**
 * 角隅外框：距边两道平行线 + 转角一枚方胜。
 *
 * 五款角饰共用一个框。这是「角」的身份——内花可以换（卷草/折枝/竹叶/回纹），
 * 框不能换；框一换，同一份稿子里四角的语言就不统一了。
 */
const cornerFrame = (ink: string): string => {
  const outer = 2.2
  const inner = 3.8
  const mid = outer + 1.45
  return (
    `<path d="M20 ${outer} L${outer + 1.6} ${outer} Q${outer} ${outer} ${outer} ${outer + 1.6} L${outer} 20" fill="none" stroke="${ink}" stroke-width="0.3" opacity="0.5"/>` +
    `<path d="M20 ${inner} L${inner + 1.3} ${inner} Q${inner} ${inner} ${inner} ${inner + 1.3} L${inner} 20" fill="none" stroke="${ink}" stroke-width="0.18" opacity="0.32"/>` +
    `<rect x="${(outer + 0.5).toFixed(2)}" y="${(outer + 0.5).toFixed(2)}" width="1.9" height="1.9" transform="rotate(45 ${mid.toFixed(2)} ${mid.toFixed(2)})" fill="${ink}" opacity="0.34"/>`
  )
}

/** 角隅内花统一从内框拐角起笔，压在内线上——茎要长在框上才有生长关系。 */
const CORNER_BASE = { x: 3.8, y: 3.8 }

/** 一朵五瓣小花。卷草的空处需要一点「实」压住，否则满篇都是线。 */
const blossom = (x: number, y: number, size: number, color: string, opacity: number): string => {
  const petals = Array.from({ length: 5 }, (_, index) => {
    const rad = (index / 5) * Math.PI * 2 - Math.PI / 2
    const px = Math.cos(rad) * size * 0.62
    const py = Math.sin(rad) * size * 0.62
    return `<ellipse cx="${px.toFixed(2)}" cy="${py.toFixed(2)}" rx="${(size * 0.46).toFixed(2)}" ry="${(size * 0.3).toFixed(2)}" transform="rotate(${((rad * 180) / Math.PI).toFixed(1)})" fill="${color}" opacity="${opacity}"/>`
  })
  return `<g transform="translate(${x.toFixed(2)} ${y.toFixed(2)})">${petals.join('')}<circle r="${(size * 0.2).toFixed(2)}" fill="${color}" opacity="${(opacity * 1.6).toFixed(2)}"/></g>`
}

/** 一层山脊。`ridge` 决定高低起伏，写成折线而不是曲线——木刻感来自折笔。 */
const ridge = (baseY: number, amplitude: number, points: number, phase: number): string => {
  const step = 100 / points
  const commands: string[] = [`M0 ${baseY.toFixed(2)}`]
  for (let index = 1; index <= points; index += 1) {
    const x = step * index
    const y = baseY - amplitude * (0.5 + 0.5 * Math.sin(index * 1.7 + phase))
    commands.push(`L${x.toFixed(2)} ${y.toFixed(2)}`)
  }
  commands.push(`L100 56.25`, `L0 56.25`, 'Z')
  return commands.join(' ')
}

const renderers: Record<string, MotifRenderer> = {
  'paper-plain': ({ palette, seed = 1 }) => {
    const next = random(seed)
    const dots = Array.from({ length: 90 }, () => {
      const x = next() * 100
      const y = next() * 56.25
      const r = 0.06 + next() * 0.12
      return `<circle cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="${r.toFixed(2)}" fill="${palette.fg}" opacity="0.05"/>`
    })
    return `<g aria-hidden="true">${dots.join('')}</g>`
  },

  'paper-chain': ({ palette, params, seed = 1 }) => {
    const gap = Math.max(6, Math.min(18, params?.gap ?? 11))
    const opacity = Math.min(0.12, Math.max(0.02, params?.opacity ?? 0.05))
    const lines = []
    for (let x = gap / 2; x < 100; x += gap) {
      lines.push(`<line x1="${x.toFixed(2)}" y1="0" x2="${x.toFixed(2)}" y2="56.25" stroke="${palette.fg}" stroke-width="0.06" opacity="${opacity}"/>`)
    }
    return `<g aria-hidden="true">${lines.join('')}</g>`
  },

  'paper-mottle': ({ palette, params, seed = 3 }) => {
    const next = random(seed)
    const count = Math.max(3, Math.min(9, params?.blots ?? 5))
    const opacity = Math.min(0.1, Math.max(0.02, params?.opacity ?? 0.04))
    const blots = Array.from({ length: count }, () => {
      const cx = next() * 100
      const cy = next() * 56.25
      const rx = 6 + next() * 10
      const ry = 3 + next() * 5
      return `<ellipse cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" rx="${rx.toFixed(1)}" ry="${ry.toFixed(1)}" fill="${palette.fg}" opacity="${opacity}"/>`
    })
    return `<g aria-hidden="true">${blots.join('')}</g>`
  },

  'mountain-layers': ({ palette, params }) => {
    const layers = Math.max(2, Math.min(4, params?.layers ?? 3))
    const height = Math.min(0.42, Math.max(0.18, params?.height ?? 0.28))
    const shapes = Array.from({ length: layers }, (_, index) => {
      const base = 56.25 - index * 3.2
      const amplitude = 56.25 * height * (1 - index * 0.18)
      const opacity = 0.06 + index * 0.03
      return `<path d="${ridge(base, amplitude, 6 + index * 2, index * 1.2)}" fill="${palette.fg}" opacity="${opacity.toFixed(3)}"/>`
    })
    return `<g aria-hidden="true">${shapes.join('')}</g>`
  },

  'mountain-single': ({ palette, params }) => {
    const height = Math.min(0.5, Math.max(0.2, params?.height ?? 0.34))
    const lean = Math.max(-0.2, Math.min(0.2, params?.lean ?? 0.06))
    const peakX = 46 + lean * 100
    const peakY = 56.25 - 56.25 * height
    return `<g aria-hidden="true"><path d="M8 56.25 Q${(peakX - 18).toFixed(1)} ${(peakY + 6).toFixed(1)} ${peakX.toFixed(1)} ${peakY.toFixed(1)} Q${(peakX + 20).toFixed(1)} ${(peakY + 9).toFixed(1)} 92 56.25 Z" fill="${palette.fg}" opacity="0.08"/></g>`
  },

  /**
   * 卷草（角隅式）：外角双线 + 内填卷草。
   *
   * 为什么不是「一根藤」：一根藤有方向、有起止，镜像到四角就是四条杂草。
   * 传统角花的结构是**外框 + 内花**——外框给「角」的身份，内花给「手」的温度。
   * 四角用同一个母题镜像，外框四段在纸边连成界栏，内花各自朝里开。
   *
   * 全墨色。朱砂留给竖线、印章与分隔线：角花上了色就变成贴纸。
   */
  'corner-vine': ({ palette, params }) => {
    const leaves = Math.max(2, Math.min(5, Math.round(params?.leaves ?? 3)))
    const scale = Math.min(1.2, Math.max(0.75, params?.scale ?? 1))
    // frame=0 只留内花：正文页的角落放不下界栏，放下去就是一道没画完的短线。
    const withFrame = (params?.frame ?? 1) > 0.5
    const ink = palette.fg
    // 外线距边 2.2，内线 3.8：两道平行线是「书口」的语言，也是整枝的骨架。
    const outer = 2.2
    const inner = 3.8
    const parts: string[] = []
    if (withFrame) parts.push(cornerFrame(ink))

    const base = CORNER_BASE
    const stem = sampleCubic(
      base,
      { x: base.x + 3.4 * scale, y: base.y + 1.6 * scale },
      { x: base.x + 5.4 * scale, y: base.y + 4.4 * scale },
      { x: base.x + 6.2 * scale, y: base.y + 8.2 * scale },
      12,
    )
    parts.push(taper(stem, 0.3 * scale, 0.08, ink, 0.5))

    for (let index = 0; index < leaves; index += 1) {
      const at = stem[Math.round(2 + (index * (stem.length - 4)) / Math.max(1, leaves))]
      if (!at) continue
      const side = index % 2 === 0 ? 1 : -1
      parts.push(leaf(at.x, at.y, at.angle + side * 1.12, (index % 2 === 0 ? 3.6 : 3.0) * scale, 1.05 * scale, ink, index % 2 === 0 ? 0.34 : 0.25))
    }

    const end = stem[stem.length - 1]
    parts.push(tendril(end.x, end.y, end.angle + 0.6, 1.9 * scale, 1.4, ink, 0.4))
    return `<g aria-hidden="true">${parts.join('')}</g>`
  },

  /** 折枝：比卷草更直更疏的一条枝，枝梢聚三片叶收笔。 */
  'corner-branch': ({ palette, params }) => {
    const leaves = Math.max(3, Math.min(7, Math.round(params?.leaves ?? 4)))
    const angle = Math.max(-20, Math.min(30, params?.angle ?? 8)) / 100
    const ink = palette.fg
    const stem = sampleCubic(
      { x: CORNER_BASE.x, y: CORNER_BASE.y },
      { x: 5.6, y: 5.0 },
      { x: 8.4, y: 7.4 },
      { x: 11.4, y: 10.6 + angle * 6 },
      14,
    )
    const parts: string[] = [(params?.frame ?? 1) > 0.5 ? cornerFrame(ink) : '', taper(stem, 0.36, 0.09, ink, 0.56)]
    for (let index = 0; index < leaves; index += 1) {
      const at = stem[Math.round(2 + (index * (stem.length - 4)) / Math.max(1, leaves))]
      if (!at) continue
      const side = index % 2 === 0 ? 1 : -1
      parts.push(leaf(at.x, at.y, at.angle + side * 1.35, index % 2 === 0 ? 4.8 : 4.0, 0.86, ink, 0.32))
    }
    // 枝梢三片叶聚成一小簇，是折枝画的收笔。
    const tip = stem[stem.length - 1]
    parts.push(leaf(tip.x, tip.y, tip.angle - 0.5, 3.0, 0.82, ink, 0.3))
    parts.push(leaf(tip.x, tip.y, tip.angle + 0.55, 2.7, 0.74, ink, 0.24))
    return `<g aria-hidden="true">${parts.join('')}</g>`
  },

  /** 缠枝：主茎沿角盘绕一圈再收回中心，压得住深色底。 */
  'corner-scroll': ({ palette, params }) => {
    const loops = Math.max(2, Math.min(4, Math.round(params?.loops ?? 3)))
    const ink = palette.fg
    const stem = sampleCubic({ x: CORNER_BASE.x, y: CORNER_BASE.y }, { x: 8.0, y: 5.2 }, { x: 10.6, y: 8.4 }, { x: 10.2, y: 12.4 }, 14)
    const parts: string[] = [(params?.frame ?? 1) > 0.5 ? cornerFrame(ink) : '', taper(stem, 0.36, 0.1, ink, 0.6)]
    for (let index = 0; index < loops + 1; index += 1) {
      const at = stem[Math.round(4 + (index * (stem.length - 7)) / Math.max(1, loops))]
      if (!at) continue
      parts.push(leaf(at.x, at.y, at.angle + (index % 2 ? -1.3 : 1.3), 3.8, 1.0, ink, 0.3))
    }
    const end = stem[stem.length - 1]
    parts.push(tendril(end.x, end.y, end.angle, 2.7, 1.6, ink, 0.42))
    return `<g aria-hidden="true">${parts.join('')}</g>`
  },

  /**
   * 竹叶：一根细枝，三撇叶。
   *
   * 与卷草同一套零件，只是去掉卷须和花——少即是多，这一款放在信息密的页上也不吵。
   */
  'corner-bamboo': ({ palette, params }) => {
    const leaves = Math.max(3, Math.min(9, Math.round(params?.leaves ?? 5)))
    const ink = palette.fg
    const stem = sampleCubic({ x: CORNER_BASE.x, y: CORNER_BASE.y }, { x: 5.4, y: 7.0 }, { x: 6.6, y: 10.6 }, { x: 7.4, y: 14.6 }, 12)
    const parts: string[] = [(params?.frame ?? 1) > 0.5 ? cornerFrame(ink) : '', taper(stem, 0.26, 0.08, ink, 0.52)]
    // 竹叶成组：每节一撇一捺，叶比枝条长得多，这是竹子的比例。
    for (let index = 0; index < leaves; index += 1) {
      const at = stem[Math.min(stem.length - 1, Math.round(2 + index * 2.2))]
      if (!at) continue
      const swing = 0.95 + (index % 3) * 0.24
      parts.push(leaf(at.x, at.y, at.angle - swing, 5.6 - (index % 3) * 0.7, 0.95, ink, 0.32))
      if (index % 2 === 0) parts.push(leaf(at.x, at.y, at.angle + swing * 0.8, 4.4, 0.82, ink, 0.26))
    }
    return `<g aria-hidden="true">${parts.join('')}</g>`
  },

  /**
   * 回纹：外框 + 内嵌三层「回」字。
   *
   * 不用曲线，靠间距的匀。信息最密的一页用它——它不抢字，只把角「钉」住。
   */
  'corner-fret': ({ palette, params }) => {
    const rings = Math.max(2, Math.min(4, Math.round(params?.rings ?? 3)))
    const ink = palette.fg
    const parts: string[] = [(params?.frame ?? 1) > 0.5 ? cornerFrame(ink) : '']
    // 同心方框：外框套内框，才是「回」。
    // 之前画的是方螺旋，逐段折进去会自交，放大看像两个错位的框。
    const o = 5.4
    const arm = Math.min(7.0, 4.2 + rings * 0.9)
    const step = 1.5
    for (let index = 0; index < Math.min(rings, 3); index += 1) {
      const inset = o + index * step
      const span = arm - index * step * 2
      if (span <= 1.2) break
      parts.push(
        `<rect x="${inset.toFixed(2)}" y="${inset.toFixed(2)}" width="${span.toFixed(2)}" height="${span.toFixed(2)}" fill="none" stroke="${ink}" stroke-width="${(0.22 - index * 0.04).toFixed(2)}" opacity="${(0.44 - index * 0.09).toFixed(2)}"/>`,
      )
    }
    return `<g aria-hidden="true">${parts.join('')}</g>`
  },

  'grid-vertical': ({ palette, params }) => {
    const columns = Math.max(3, Math.min(10, params?.columns ?? 6))
    const inset = Math.max(0.02, Math.min(0.12, params?.inset ?? 0.06)) * 100
    const step = (100 - inset * 2) / (columns - 1)
    const lines = Array.from({ length: columns }, (_, index) => {
      const x = inset + index * step
      return `<line x1="${x.toFixed(2)}" y1="${(inset * 0.6).toFixed(2)}" x2="${x.toFixed(2)}" y2="${(56.25 - inset * 0.6).toFixed(2)}" stroke="${palette.fg}" stroke-width="0.07" opacity="0.16"/>`
    })
    return `<g aria-hidden="true">${lines.join('')}</g>`
  },

  'grid-silk': ({ palette, params }) => {
    const rows = Math.max(6, Math.min(14, params?.rows ?? 9))
    const opacity = Math.min(0.18, Math.max(0.05, params?.opacity ?? 0.1))
    const step = 56.25 / rows
    const lines = Array.from({ length: rows - 1 }, (_, index) => {
      const y = step * (index + 1)
      return `<line x1="2" y1="${y.toFixed(2)}" x2="98" y2="${y.toFixed(2)}" stroke="${palette.fg}" stroke-width="0.06" opacity="${opacity}"/>`
    })
    return `<g aria-hidden="true">${lines.join('')}</g>`
  },

  'seal-square': ({ palette, params }) => {
    const rotation = Math.max(-6, Math.min(6, params?.rotation ?? -3))
    return `<g aria-hidden="true" transform="rotate(${rotation} 10 10)"><rect x="2" y="2" width="16" height="16" rx="1" fill="${palette.accent}" opacity="0.82"/><rect x="3.6" y="3.6" width="12.8" height="12.8" rx="0.6" fill="none" stroke="${palette.bg}" stroke-width="0.4" opacity="0.8"/><path d="M10 4.6 L10 15.4 M6 10 L14 10" stroke="${palette.bg}" stroke-width="0.7" opacity="0.7"/></g>`
  },

  'water-ripple': ({ palette, params }) => {
    const lines = Math.max(2, Math.min(6, params?.lines ?? 4))
    const amplitude = Math.max(0.004, Math.min(0.02, params?.amplitude ?? 0.01)) * 56.25 * 10
    const paths = Array.from({ length: lines }, (_, index) => {
      const y = 40 + index * 3.4
      return `<path d="M2 ${y.toFixed(1)} Q16 ${(y - amplitude).toFixed(1)} 30 ${y.toFixed(1)} T58 ${y.toFixed(1)} T86 ${y.toFixed(1)} T98 ${y.toFixed(1)}" fill="none" stroke="${palette.fg}" stroke-width="0.1" opacity="${(0.14 - index * 0.02).toFixed(2)}"/>`
    })
    return `<g aria-hidden="true">${paths.join('')}</g>`
  },
}

/** 渲染一个母题。未知 id 返回空串——线框少一层装饰，不会整页白屏。 */
export const renderMotif = (id: string | undefined, ctx: MotifContext): string => {
  if (!id) return ''
  const renderer = renderers[id]
  return renderer ? renderer(ctx) : ''
}

export const motifRendererIds = Object.keys(renderers)

/** 目录里的母题是否都有渲染实现。构建期断言用，避免「目录有、画不出」。 */
export const missingRenderers = (motifs: MotifSpec[]): string[] =>
  motifs.filter((motif) => !renderers[motif.id]).map((motif) => motif.id)
