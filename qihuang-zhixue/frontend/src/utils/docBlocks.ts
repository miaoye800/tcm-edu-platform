import type { BlockType, DocBlock, DocBlockProps, LegacyDocBlock } from '../types/doc'
import { HEADING_TYPES, isHeading } from '../types/doc'
import { parseMarkdownTable } from '../types/resource'

let counter = 0

/** 稳定块 id。同一块在编辑、拖拽、撤销过程中必须保持不变。 */
export const newBlockId = (): string => {
  counter = (counter + 1) % 1_000_000
  return `blk_${Date.now().toString(36)}${counter.toString(36)}${Math.random().toString(36).slice(2, 5)}`
}

export const createBlock = (type: BlockType, init: Partial<DocBlock> = {}): DocBlock => ({
  id: init.id || newBlockId(),
  type,
  text: init.text ?? '',
  props: { ...defaultPropsFor(type), ...(init.props || {}) },
  ...(init.children ? { children: init.children } : {}),
  ...(init.meta ? { meta: init.meta } : {}),
})

export const defaultPropsFor = (type: BlockType): DocBlockProps => {
  switch (type) {
    case 'heading1':
    case 'heading2':
    case 'heading3':
      return { collapsed: false }
    case 'todo':
      return { checked: false }
    case 'toggle':
      return { collapsed: true }
    case 'callout':
      return { tone: 'info', icon: '💡' }
    case 'code':
      return { lang: '' }
    case 'table':
      return { columns: ['列一', '列二'], rows: [['', '']], header: true }
    case 'columns':
      return { widths: [1, 1] }
    default:
      return {}
  }
}

export const cloneBlock = (block: DocBlock, withChildren = true): DocBlock => ({
  ...block,
  id: newBlockId(),
  props: { ...(block.props || {}) },
  ...(withChildren && block.children ? { children: block.children.map(child => cloneBlock(child)) } : {}),
})

const LEVEL_TO_TYPE: Record<number, BlockType> = { 1: 'heading1', 2: 'heading2', 3: 'heading3' }

const stripHeadingPrefix = (text: string): { text: string; level: number } => {
  const match = /^(#{1,6})\s+/.exec(text)
  if (!match) return { text, level: 0 }
  return { text: text.slice(match[0].length), level: Math.min(3, match[1].length) }
}

const looksLikeTable = (text: string): boolean =>
  /\r?\n/.test(text) && /^\s*\|/.test(text)

/**
 * 旧格式归一为 Notion 块。
 *
 * 后端在改造前只下发 { type, text }，且把结构信息编码进 text：
 *  - 标题层级 → `## 前缀`
 *  - 多行列表 → 换行分隔（展开成多个块，Notion 里每个列表项就是一个块）
 *  - 表格    → 整段 Markdown 管道表（解析成 columns/rows）
 * 这里同时接受新格式（带 id/props/children），保证新旧资源都能打开。
 */
export const normalizeBlock = (raw: LegacyDocBlock | DocBlock | null | undefined): DocBlock | null => {
  if (!raw || typeof raw !== 'object') return null
  const type = String(raw.type || 'paragraph')
  const text = typeof raw.text === 'string' ? raw.text : ''
  const props: DocBlockProps = { ...(raw.props || {}) }
  const id = raw.id || newBlockId()

  // 已是新格式：仅补 id 与默认 props
  const known: BlockType[] = [
    'paragraph', 'heading1', 'heading2', 'heading3', 'bulleted', 'numbered', 'todo', 'toggle',
    'quote', 'callout', 'divider', 'code', 'table', 'image', 'columns', 'page',
  ]
  if (known.includes(type as BlockType)) {
    const block: DocBlock = { id, type: type as BlockType, text, props }
    if (Array.isArray(raw.children) && raw.children.length) {
      block.children = raw.children.map(child => normalizeBlock(child)).filter(Boolean) as DocBlock[]
    }
    if (raw.meta) block.meta = raw.meta as DocBlock['meta']
    if (type === 'table' && !props.rows) {
      const cells = parseMarkdownTable(text)
      if (cells.length) {
        props.columns = cells[0]
        props.rows = cells.slice(1)
        props.header = true
      }
    }
    return block
  }

  // 旧格式
  if (type === 'heading') {
    const parsed = stripHeadingPrefix(text)
    const level = Number(props.level) || parsed.level || 2
    return { id, type: LEVEL_TO_TYPE[level] || 'heading2', text: parsed.text || text, props }
  }
  if (type === 'bullet') {
    const lines = text.split(/\r?\n/).map(line => line.replace(/^[-*]\s*/, '').trim()).filter(Boolean)
    if (lines.length <= 1) return { id, type: 'bulleted', text: lines[0] || text, props }
    // 多行列表展开成多个块：只保留第一个沿用 id，其余发新号
    return { id, type: 'bulleted', text: lines[0], props, meta: { sourceIndex: 0 } }
  }
  if (type === 'table') {
    const cells = parseMarkdownTable(text)
    return {
      id,
      type: 'table',
      text,
      props: cells.length ? { ...props, columns: cells[0], rows: cells.slice(1), header: true } : props,
    }
  }
  if (type === 'callout') return { id, type: 'callout', text, props: { tone: 'info', icon: '注', ...props } }
  if (type === 'quote') return { id, type: 'quote', text, props }
  return { id, type: 'paragraph', text, props }
}

/** 批量归一。旧格式的多行 bullet 会在这里展开成多个块。 */
export const normalizeBlocks = (raw: unknown): DocBlock[] => {
  if (!Array.isArray(raw)) return []
  const out: DocBlock[] = []
  for (const item of raw) {
    const block = normalizeBlock(item as LegacyDocBlock)
    if (!block) continue
    if (block.type === 'bulleted' && typeof (item as LegacyDocBlock)?.text === 'string') {
      const lines = String((item as LegacyDocBlock).text).split(/\r?\n/).map(l => l.replace(/^[-*]\s*/, '').trim()).filter(Boolean)
      if (lines.length > 1) {
        lines.forEach((line, index) => {
          out.push({ ...createBlock('bulleted', { text: line }), id: index === 0 ? block.id : newBlockId() })
        })
        continue
      }
    }
    out.push(block)
  }
  return out
}

/** 纯文本（用于字数统计、目录、搜索），去掉内联标记与引文角标 */
export const plainText = (block: DocBlock): string => {
  const text = block.text || ''
  return text
    .replace(/\[(\d+)\]/g, '')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/~~([^~]+)~~/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\*([^*]+)\*/g, '$1')
    .trim()
}

export const countWords = (blocks: DocBlock[]): number => {
  let total = 0
  const walk = (list: DocBlock[]) => {
    for (const block of list) {
      total += plainText(block).replace(/\s/g, '').length
      if (block.children?.length) walk(block.children)
    }
  }
  walk(blocks)
  return total
}

export const countCitations = (blocks: DocBlock[]): number => {
  let total = 0
  const walk = (list: DocBlock[]) => {
    for (const block of list) {
      total += (block.text?.match(/\[(\d+)\]/g) || []).length
      if (block.children?.length) walk(block.children)
    }
  }
  walk(blocks)
  return total
}

/** 递归查找块与其所在数组，供移动/删除使用 */
export const locateBlock = (
  blocks: DocBlock[],
  id: string,
): { block: DocBlock; siblings: DocBlock[]; index: number; parent: DocBlock | null } | null => {
  const walk = (list: DocBlock[], parent: DocBlock | null): ReturnType<typeof locateBlock> => {
    for (let index = 0; index < list.length; index += 1) {
      const block = list[index]
      if (block.id === id) return { block, siblings: list, index, parent }
      if (block.children?.length) {
        const found = walk(block.children, block)
        if (found) return found
      }
    }
    return null
  }
  return walk(blocks, null)
}

/** 导出为 Markdown，与 markdownToBlocks 构成往返 */
export const toMarkdown = (blocks: DocBlock[], depth = 0): string => {
  const pad = '  '.repeat(depth)
  const lines: string[] = []
  for (const block of blocks) {
    const text = block.text || ''
    switch (block.type) {
      case 'heading1': lines.push(`# ${text}`); break
      case 'heading2': lines.push(`## ${text}`); break
      case 'heading3': lines.push(`### ${text}`); break
      case 'bulleted': lines.push(`${pad}- ${text}`); break
      case 'numbered': lines.push(`${pad}1. ${text}`); break
      case 'todo': lines.push(`${pad}- [${block.props?.checked ? 'x' : ' '}] ${text}`); break
      case 'toggle':
        lines.push(`${pad}- ${text}`)
        if (block.children?.length) lines.push(toMarkdown(block.children, depth + 1))
        break
      case 'quote': lines.push(`> ${text.replace(/\n/g, '\n> ')}`); break
      case 'callout': lines.push(`> ${block.props?.icon ? `${block.props.icon} ` : ''}${text}`); break
      case 'divider': lines.push('---'); break
      case 'code': lines.push(`\`\`\`${block.props?.lang || ''}\n${text}\n\`\`\``); break
      case 'table': {
        const columns = (block.props?.columns as string[]) || []
        const rows = (block.props?.rows as string[][]) || []
        if (!columns.length) { lines.push(text); break }
        // 管道表必须整段一次性入行（内部 \n 连接）：行间若被 \n\n 打断，
        // 再导入时会被 markdown-it 拆成多个段落而不是表格（与后端 _table 同规则）。
        lines.push([
          `| ${columns.join(' | ')} |`,
          `| ${columns.map(() => '---').join(' | ')} |`,
          ...rows.map(row => `| ${row.join(' | ')} |`),
        ].join('\n'))
        break
      }
      case 'image': lines.push(`![${block.props?.alt || ''}](${block.props?.assetId || ''})`); break
      case 'columns':
        (block.children || []).forEach(child => lines.push(toMarkdown([child], depth)))
        break
      case 'page': lines.push(`${pad}- 📄 ${text}`); break
      default: lines.push(text)
    }
  }
  return lines.filter(line => line !== '').join('\n\n')
}

/** 只有标题、列表、待办会延续类型；与 Notion 的 Enter 行为一致 */
export const continuesOnEnter = (type: BlockType): boolean =>
  type === 'bulleted' || type === 'numbered' || type === 'todo'

export const isHeadingType = (type: BlockType): boolean => isHeading(type) || HEADING_TYPES.includes(type)
