import type { BlockType } from '../types/doc'

/**
 * 块类型注册表 —— 对齐 Notion 的做法：新增一种块只加一条注册项，
 * 渲染器、斜杠菜单、「转成」菜单三处自动生效，不改任何分支代码。
 */
export interface BlockSpec {
  type: BlockType
  /** 块菜单与斜杠菜单里显示的名字 */
  label: string
  /** 斜杠菜单里的示意图标（用文字符号，避免引入图标库） */
  icon: string
  group: 'basic' | 'list' | 'media' | 'advanced'
  /** 斜杠检索关键词：中文 + 拼音首字母，中文用户两种都会打 */
  keywords: string[]
  /** 是否显示描述（斜杠菜单里） */
  hint: string
  /** 正文是否可直接编辑 */
  editable: boolean
}

export const BLOCK_SPECS: Record<BlockType, BlockSpec> = {
  paragraph: { type: 'paragraph', label: '正文', icon: 'T', group: 'basic', keywords: ['正文', '文本', '段落', 'wenben', 'duanluo', 'text', 'p'], hint: '普通文字段落', editable: true },
  heading1: { type: 'heading1', label: '一级标题', icon: 'H1', group: 'basic', keywords: ['标题', '一级', '大标题', 'biaoti', 'h1'], hint: '页面内最大的一级标题', editable: true },
  heading2: { type: 'heading2', label: '二级标题', icon: 'H2', group: 'basic', keywords: ['标题', '二级', 'biaoti', 'h2'], hint: '章节标题', editable: true },
  heading3: { type: 'heading3', label: '三级标题', icon: 'H3', group: 'basic', keywords: ['标题', '三级', '小标题', 'biaoti', 'h3'], hint: '小节标题', editable: true },
  bulleted: { type: 'bulleted', label: '无序列表', icon: '•', group: 'list', keywords: ['列表', '无序', '项目符号', 'liebiao', 'wuxu', 'bullet', 'ul'], hint: '圆点项目列表', editable: true },
  numbered: { type: 'numbered', label: '有序列表', icon: '1.', group: 'list', keywords: ['列表', '有序', '编号', 'liebiao', 'youxu', 'number', 'ol'], hint: '带序号的列表', editable: true },
  todo: { type: 'todo', label: '待办', icon: '☑', group: 'list', keywords: ['待办', '任务', '清单', '复选框', 'daiban', 'renwu', 'todo', 'check'], hint: '可勾选的任务项', editable: true },
  toggle: { type: 'toggle', label: '折叠块', icon: '▸', group: 'advanced', keywords: ['折叠', '收起', '展开', 'zhedie', 'shouqi', 'toggle'], hint: '可展开收起的内容块', editable: true },
  quote: { type: 'quote', label: '引用', icon: '❝', group: 'basic', keywords: ['引用', '摘录', '原文', 'yinyong', 'zhailu', 'quote'], hint: '引用一段原文', editable: true },
  callout: { type: 'callout', label: '提示框', icon: '💡', group: 'advanced', keywords: ['提示', '注意', '高亮', 'tishi', 'zhuyi', 'callout'], hint: '带图标的强调框', editable: true },
  divider: { type: 'divider', label: '分割线', icon: '—', group: 'basic', keywords: ['分割线', '分隔', '横线', 'fengexian', 'divider', 'hr'], hint: '一条水平分割线', editable: false },
  code: { type: 'code', label: '代码块', icon: '</>', group: 'advanced', keywords: ['代码', 'code', 'daima'], hint: '等宽字体代码段', editable: true },
  table: { type: 'table', label: '表格', icon: '▦', group: 'media', keywords: ['表格', 'biaoge', 'table'], hint: '带表头的表格', editable: false },
  image: { type: 'image', label: '图片', icon: '🖼', group: 'media', keywords: ['图片', '插图', 'photo', 'tupian', 'image'], hint: '插入一张图', editable: false },
  columns: { type: 'columns', label: '分栏', icon: '◫', group: 'advanced', keywords: ['分栏', '两栏', 'fenlan', 'columns'], hint: '左右并排的内容', editable: false },
  page: { type: 'page', label: '子页面', icon: '📄', group: 'advanced', keywords: ['页面', '子页面', '链接', 'yemian', 'ziyemian', 'page'], hint: '跳转到知识点子页面', editable: false },
}

export const BLOCK_ORDER: BlockType[] = [
  'paragraph', 'heading1', 'heading2', 'heading3',
  'bulleted', 'numbered', 'todo', 'toggle',
  'quote', 'callout', 'divider', 'code',
  'table', 'image', 'columns', 'page',
]

export const BLOCK_GROUP_LABEL: Record<BlockSpec['group'], string> = {
  basic: '基础块',
  list: '列表',
  media: '媒体',
  advanced: '高级',
}

/** 「转成」菜单里不提供的目标：分栏与子页面有结构副作用，不参与类型转换 */
export const TURN_INTO_TYPES: BlockType[] = BLOCK_ORDER.filter(type => type !== 'columns' && type !== 'page')

export const specOf = (type: BlockType): BlockSpec => BLOCK_SPECS[type] || BLOCK_SPECS.paragraph

/** 斜杠菜单检索：空查询返回全部，否则按 label / keywords 模糊匹配 */
export const matchSlash = (query: string): BlockSpec[] => {
  const q = query.trim().toLowerCase()
  if (!q) return BLOCK_ORDER.map(specOf)
  return BLOCK_ORDER.map(specOf).filter(spec => {
    if (spec.label.toLowerCase().includes(q)) return true
    if (spec.type.includes(q)) return true
    return spec.keywords.some(keyword => keyword.toLowerCase().includes(q) || q.includes(keyword.toLowerCase()))
  })
}
