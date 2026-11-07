/**
 * 内联 Markdown 解析。
 *
 * 块正文用「内联 Markdown 字符串」存储（`**粗**`、`*斜*`、`` `码` ``、`~~删~~`、`[n]` 引文），
 * 而不是 Notion 的 interval 样式数组——LLM 天然产出 Markdown，且存量数据已经是这个形态。
 *
 * 只解析行内语法，不处理块级结构：块级由 markdownToBlocks 负责。
 */

export type InlineKind = 'text' | 'strong' | 'em' | 'del' | 'code' | 'citation' | 'link'

export interface InlineSegment {
  kind: InlineKind
  text: string
  /** link 才有 */
  href?: string
  /** citation 才有：sources 的零基下标 */
  index?: number
}

const PATTERN = new RegExp(
  [
    /(\*\*[^*]+\*\*)/.source,       // **粗**
    /(~~[^~]+~~)/.source,           // ~~删除~~
    /(`[^`]+`)/.source,             // `码`
    /(\[\d+\])/.source,             // [0] 引文（必须早于链接，否则被当成链接）
    /(\[[^\]]+\]\([^)]+\))/.source, // [文字](链接)
    /(\*[^*]+\*)/.source,           // *斜*
  ].join('|'),
  'g',
)

export const parseInline = (text: string): InlineSegment[] => {
  const source = text || ''
  if (!source) return []
  const segments: InlineSegment[] = []
  let last = 0
  let match: RegExpExecArray | null
  PATTERN.lastIndex = 0

  while ((match = PATTERN.exec(source)) !== null) {
    if (match.index > last) segments.push({ kind: 'text', text: source.slice(last, match.index) })
    const token = match[0]

    if (token.startsWith('**')) {
      segments.push({ kind: 'strong', text: token.slice(2, -2) })
    } else if (token.startsWith('~~')) {
      segments.push({ kind: 'del', text: token.slice(2, -2) })
    } else if (token.startsWith('`')) {
      segments.push({ kind: 'code', text: token.slice(1, -1) })
    } else if (/^\[\d+\]$/.test(token)) {
      segments.push({ kind: 'citation', text: token.slice(1, -1), index: Number(token.slice(1, -1)) })
    } else if (token.startsWith('[')) {
      const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(token)
      if (link) segments.push({ kind: 'link', text: link[1], href: link[2] })
      else segments.push({ kind: 'text', text: token })
    } else {
      segments.push({ kind: 'em', text: token.slice(1, -1) })
    }
    last = match.index + token.length
  }

  if (last < source.length) segments.push({ kind: 'text', text: source.slice(last) })
  return segments
}

/** 编辑态：把纯文本包成一层最小标记，避免直接把 DOM 文本塞回 model 时丢掉已有格式 */
export const uniformText = (text: string): string => (text || '').replace(/\u00a0/g, ' ')
