import MarkdownIt from 'markdown-it'
import type Token from 'markdown-it/lib/token.mjs'
import type { BlockType, CalloutTone, DocBlock } from '../types/doc'
import { createBlock, newBlockId, normalizeBlocks } from './docBlocks'

/**
 * Markdown → Notion 块。
 *
 * 走 markdown-it 的 `parse()`（token 流），不走 `render()`（HTML 字符串）：
 * 我们要的是**结构**，不是一段 HTML。这样同一套解析既能用于「Markdown 呈现为页面」，
 * 也能用于粘贴导入与导出往返。
 *
 * 已知限制：不做块级引用（blockquote 内嵌列表）；嵌套列表映射为 toggle（Notion 导出语义）。
 */
const md = new MarkdownIt({ html: false, linkify: true, breaks: false })

/** callout 图标 → 底色语气。首字符命中即生效，与后端导出 `> icon text` 约定配套。 */
export const ICON_TONE: Array<[RegExp, CalloutTone]> = [
  [/^[⚠❗‼🚨⛔]/u, 'warn'],
  [/^[✦✨⭐✅🎯💡🔥📌🌟]/u, 'tip'],
]

export const toneOfIcon = (icon: string): CalloutTone =>
  ICON_TONE.find(([pattern]) => pattern.test(icon))?.[1] || 'info'

/**
 * 取文本开头的 emoji 图标；不是 emoji 返回空串。
 * ⚠️ 是 U+26A0 + U+FE0F 两个码点，必须吸收变体选择符，否则半个字符会漏进正文。
 */
export const extractLeadingEmoji = (text: string): string => {
  const trimmed = text.trim()
  if (!trimmed) return ''
  const codepoints = Array.from(trimmed)
  const first = codepoints[0] || ''
  if (!/\p{Extended_Pictographic}/u.test(first)) return ''
  let icon = first
  for (let index = 1; index < Math.min(codepoints.length, 4); index += 1) {
    const cp = codepoints[index]
    if (cp === '\uFE0E' || cp === '\uFE0F') { icon += cp; continue }
    if (cp === '\u200D') { icon += cp; continue }
    break
  }
  return icon
}

const ASIDE_OPEN = /^<aside\b[^>]*>\s*$/i
const ASIDE_CLOSE = /^<\/aside>\s*$/i

/**
 * Notion 导出的提示块是 `<aside>` 原始 HTML（emoji 图标独占一行 + 正文若干行）。
 * markdown-it 的 `html:false` 会把它打成转义文本段落，呈现极差。
 * 这里在 parse 前把它改写成 `> icon text` 的 callout 约定行，与 blockquote→callout 分支共用一条出口。
 * 只处理行级独立的 aside；嵌在列表/表格里的 aside 仍走 html_block 段落兜底（保内容不保样式）。
 */
export const rewriteNotionAsides = (source: string): string => {
  if (!/<aside/i.test(source)) return source
  const lines = source.split(/\r?\n/)
  const out: string[] = []
  let buffer: string[] | null = null
  let icon = ''
  for (const line of lines) {
    if (buffer === null) {
      if (ASIDE_OPEN.test(line.trim())) { buffer = []; icon = '' } else out.push(line)
      continue
    }
    if (ASIDE_CLOSE.test(line.trim())) {
      const body = buffer.map(item => item.trim()).filter(Boolean)
      if (body.length) {
        out.push(`> ${icon ? `${icon} ` : ''}${body[0]}`)
        for (const extra of body.slice(1)) out.push(`> ${extra}`)
      }
      buffer = null
      icon = ''
      continue
    }
    if (line.trim() === '') continue
    // aside 内首个非空行：若整行就是一个 emoji（Notion 的图标行），记为 icon
    if (buffer.length === 0 && !icon) {
      const trimmed = line.trim()
      const candidate = extractLeadingEmoji(trimmed)
      if (candidate && candidate === trimmed) { icon = candidate; continue }
    }
    buffer.push(line)
  }
  // 未闭合的 aside：宁可降级为引用行也不丢内容
  if (buffer) for (const item of buffer) out.push(`> ${item.trim()}`)
  return out.join('\n')
}

const HEADING_TAGS: Record<string, BlockType> = { h1: 'heading1', h2: 'heading2', h3: 'heading3', h4: 'heading3', h5: 'heading3', h6: 'heading3' }

const isOpen = (token: Token): boolean => token.type.endsWith('_open')
const closeName = (type: string): string => type.replace(/_open$/, '_close')

/** 找到与 start 处开标签配对的闭标签下标 */
const matchClose = (tokens: Token[], start: number): number => {
  const close = closeName(tokens[start].type)
  let depth = 0
  for (let i = start; i < tokens.length; i += 1) {
    if (tokens[i].type === tokens[start].type) depth += 1
    else if (tokens[i].type === close) {
      depth -= 1
      if (depth === 0) return i
    }
  }
  return tokens.length - 1
}

const inlineOf = (tokens: Token[], from: number, to: number): string => {
  for (let i = from; i < to; i += 1) {
    if (tokens[i].type === 'inline') return tokens[i].content
  }
  return ''
}

const imageOf = (tokens: Token[], from: number, to: number): { src: string; alt: string } | null => {
  for (let i = from; i < to; i += 1) {
    if (tokens[i].type !== 'inline') continue
    const children = tokens[i].children || []
    const images = children.filter(child => child.type === 'image')
    if (images.length === 1) {
      const others = children.filter(child => child.type !== 'image' && child.content.trim() !== '')
      if (!others.length) return { src: images[0].attrGet('src') || '', alt: images[0].content || '' }
    }
  }
  return null
}

const parseTable = (tokens: Token[], start: number, end: number): DocBlock => {
  const columns: string[] = []
  const rows: string[][] = []
  let inHead = false
  let current: string[] | null = null
  for (let i = start; i < end; i += 1) {
    const token = tokens[i]
    if (token.type === 'thead_open') inHead = true
    if (token.type === 'thead_close') inHead = false
    if (token.type === 'tr_open') current = []
    if (token.type === 'tr_close' && current) {
      if (inHead && !columns.length) columns.push(...current)
      else rows.push(current)
      current = null
    }
    if ((token.type === 'th_open' || token.type === 'td_open') && current) {
      current.push(inlineOf(tokens, i, matchClose(tokens, i)))
    }
  }
  if (!columns.length && rows.length) columns.push(...rows.shift() as string[])
  return createBlock('table', { props: { columns, rows, header: true }, text: '' })
}

/** 拍平嵌套列表，但保留缩进语义的先后顺序；带子列表的条目映射为 toggle（Notion 导出语义） */
const parseList = (tokens: Token[], start: number, end: number, type: BlockType): DocBlock[] => {
  const out: DocBlock[] = []
  let i = start + 1
  while (i < end) {
    const token = tokens[i]
    if (token.type === 'list_item_open') {
      const itemEnd = matchClose(tokens, i)
      const text = inlineOf(tokens, i, itemEnd)
      const nested = tokens.slice(i, itemEnd).find(t => t.type === 'bullet_list_open' || t.type === 'ordered_list_open')
      if (nested) {
        const nestedIndex = tokens.indexOf(nested, i)
        const nestedEnd = matchClose(tokens, nestedIndex)
        const nestedType: BlockType = nested.type === 'ordered_list_open' ? 'numbered' : 'bulleted'
        out.push(createBlock('toggle', { text, children: parseList(tokens, nestedIndex, nestedEnd, nestedType) }))
      } else if (text.trim()) {
        out.push(createBlock(type, { text }))
      }
      i = itemEnd + 1
      continue
    }
    i += 1
  }
  return out
}

export const markdownToBlocks = (source: string): DocBlock[] => {
  const text = (source || '').trim()
  if (!text) return []
  const tokens = md.parse(rewriteNotionAsides(text), {})
  const blocks: DocBlock[] = []
  let i = 0

  while (i < tokens.length) {
    const token = tokens[i]

    if (token.type === 'heading_open') {
      const type = HEADING_TAGS[token.tag] || 'heading3'
      blocks.push(createBlock(type, { text: inlineOf(tokens, i, matchClose(tokens, i)) }))
      i = matchClose(tokens, i) + 1
      continue
    }

    if (token.type === 'paragraph_open') {
      const end = matchClose(tokens, i)
      const image = imageOf(tokens, i, end)
      if (image) {
        blocks.push(createBlock('image', { props: { assetId: image.src, alt: image.alt, caption: '' }, text: '' }))
      } else {
        const content = inlineOf(tokens, i, end)
        if (content.trim()) blocks.push(createBlock('paragraph', { text: content }))
      }
      i = end + 1
      continue
    }

    if (token.type === 'fence' || token.type === 'code_block') {
      blocks.push(createBlock('code', { text: token.content.replace(/\n$/, ''), props: { lang: token.info?.trim() || '' } }))
      i += 1
      continue
    }

    if (token.type === 'bullet_list_open' || token.type === 'ordered_list_open') {
      const end = matchClose(tokens, i)
      blocks.push(...parseList(tokens, i, end, token.type === 'bullet_list_open' ? 'bulleted' : 'numbered'))
      i = end + 1
      continue
    }

    if (token.type === 'blockquote_open') {
      const end = matchClose(tokens, i)
      const lines: string[] = []
      for (let j = i + 1; j < end; j += 1) {
        if (tokens[j].type === 'inline' && tokens[j].content.trim()) lines.push(tokens[j].content)
      }
      if (lines.length) {
        // `> icon text`（后端 callout 导出约定 / Notion aside 改写产物）→ callout 块
        const icon = extractLeadingEmoji(lines[0])
        if (icon) {
          const rest = lines[0].trim().slice(icon.length).trim()
          const body = [rest, ...lines.slice(1)].filter(line => line.trim() !== '').join('\n')
          blocks.push(createBlock('callout', { text: body, props: { tone: toneOfIcon(icon), icon } }))
        } else {
          blocks.push(createBlock('quote', { text: lines.join('\n') }))
        }
      }
      i = end + 1
      continue
    }

    if (token.type === 'table_open') {
      const end = matchClose(tokens, i)
      blocks.push(parseTable(tokens, i, end))
      i = end + 1
      continue
    }

    if (token.type === 'hr') {
      blocks.push(createBlock('divider'))
      i += 1
      continue
    }

    // 未识别块（html_block / math 等）退化为段落，宁可显示也不能丢内容
    if (token.type === 'html_block' && token.content.trim()) {
      blocks.push(createBlock('paragraph', { text: token.content.replace(/<[^>]+>/g, '').trim() }))
      i += 1
      continue
    }

    i += 1
  }

  return blocks
}

/** 供「粘贴 Markdown」与「导入」复用；空结果时返回一个空段落，避免页面无块可编辑 */
export const markdownToEditableBlocks = (source: string): DocBlock[] => {
  const blocks = markdownToBlocks(source)
  return blocks.length ? blocks : [{ ...createBlock('paragraph'), id: newBlockId() }]
}

/**
 * 资源 content → 页面块。
 *
 * 新资源存结构化 blocks；旧资源只有 content.markdown，走 markdown 解析兜底。
 * 抽出来供 DocResourceView 与文档工坊共用（原 DocResourceView.resolveBlocks 的等价搬运，
 * 放在 markdownToBlocks 侧以避免 docBlocks ↔ 本文件的循环引用）。
 */
export const resolveDocBlocks = (content: unknown): DocBlock[] => {
  const source = (content ?? {}) as { blocks?: unknown; markdown?: unknown }
  if (Array.isArray(source.blocks) && source.blocks.length) return normalizeBlocks(source.blocks)
  const markdown = typeof source.markdown === 'string' ? source.markdown : ''
  return markdown ? markdownToBlocks(markdown) : []
}
