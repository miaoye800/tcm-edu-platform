import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = path => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')

const types = read('types/doc.ts')
const registry = read('config/docEditor.ts')
const blocks = read('utils/docBlocks.ts')
const markdown = read('utils/markdownToBlocks.ts')
const document = read('composables/useDocDocument.ts')
const view = read('views/DocResourceView.vue')
const shell = read('components/doc/DocBlockView.vue')
const editable = read('components/doc/EditableText.vue')

// 1) 16 种块类型在契约、注册表、渲染器三处必须对齐，少一处就会渲染成空白
const BLOCK_TYPES = [
  'paragraph', 'heading1', 'heading2', 'heading3', 'bulleted', 'numbered', 'todo', 'toggle',
  'quote', 'callout', 'divider', 'code', 'table', 'image', 'columns', 'page',
]
for (const type of BLOCK_TYPES) {
  assert.ok(types.includes(`'${type}'`), `types/doc.ts 缺少块类型 ${type}`)
  assert.match(registry, new RegExp(`\\b${type}: \\{`), `config/docEditor.ts 未注册 ${type}`)
  assert.ok(shell.includes(`case '${type}'`) || shell.includes(`${type}: `), `DocBlockView 未映射 ${type}`)
}

// 2) Markdown 必须走 token 流解析，不能渲染成 HTML 字符串
assert.match(markdown, /md\.parse\(/)
assert.doesNotMatch(markdown, /md\.render\(/)

// 2b) Notion 导出格式的复刻能力（文档工坊依赖这三条）
assert.match(markdown, /rewriteNotionAsides/, 'Notion <aside> 提示块必须在 parse 前改写为 callout 行')
assert.match(markdown, /ICON_TONE/, 'blockquote 首字符图标 → callout 语气表')
assert.match(markdown, /createBlock\('toggle'/, '带子列表的条目必须映射为 toggle 折叠块')
assert.ok(!/html: true/.test(markdown), '不开 HTML 直通：aside 走预处理而不是污染 token 流')

// 3) 旧格式归一：标题前缀、多行列表、管道表都要能被识别
assert.match(blocks, /stripHeadingPrefix/)
assert.match(blocks, /normalizeBlocks/)
assert.match(blocks, /LEVEL_TO_TYPE/)

// 4) 「转成」只改 type，props 必须原样带回（Notion 的不丢数据机制）
assert.match(document, /\{ \.\.\.block, type \}/)
assert.doesNotMatch(document, /props: \{\}/)

// 5) 中文输入法保护：composition 期间不回写、不触发快捷键
assert.match(editable, /compositionstart/)
assert.match(editable, /compositionend/)
assert.match(editable, /if \(composing\.value\) return/)

// 6) 页面必须真正切到米白纸主题，并挂上新的渲染/交互组件
assert.match(view, /data-qh-theme="light"/)
assert.match(view, /DocBlockView/)
assert.match(view, /DocBlockMenu/)
assert.match(view, /DocSlashMenu/)
assert.match(view, /DocPageHeader/)
assert.match(view, /DocToc/)
assert.match(view, /document\.md|exportMarkdown/)

console.log('notion block contract checks passed')
