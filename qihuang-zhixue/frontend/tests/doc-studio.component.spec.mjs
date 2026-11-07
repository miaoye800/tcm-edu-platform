import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = path => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')

const markdown = read('utils/markdownToBlocks.ts')
const docBlocks = read('utils/docBlocks.ts')
const router = read('router/index.ts')
const menu = read('config/menu.ts')
const studioView = read('views/DocStudioView.vue')
const topicPanel = read('components/doc/DocTopicPanel.vue')
const useStudio = read('composables/useDocStudio.ts')
const config = read('config/docStudio.ts')

// 1) Notion 导出格式的解析能力：aside 改写、callout 图标表、嵌套列表→toggle
assert.match(markdown, /rewriteNotionAsides/, 'markdownToBlocks 缺少 aside 预处理')
assert.match(markdown, /ICON_TONE/, 'markdownToBlocks 缺少 callout 图标→语气表')
assert.match(markdown, /extractLeadingEmoji/, 'markdownToBlocks 缺少 emoji 图标提取')
assert.match(markdown, /createBlock\('toggle'/, '嵌套列表必须映射为 toggle 折叠块')
assert.match(markdown, /createBlock\('callout'/, 'blockquote 前缀图标必须映射为 callout')
// 保持 token 流解析契约：不开 HTML 直通、不渲染 HTML 字符串
assert.ok(!/html: true/.test(markdown), 'markdownToBlocks 不允许开启 html:true（会污染所有输入的 token 流）')
assert.doesNotMatch(markdown, /md\.render\(/)
// ⚠️ 是 U+26A0+FE0F 两个码点，必须吸收变体选择符
assert.match(markdown, /\\uFE0F/, 'emoji 提取必须处理变体选择符 U+FE0F')

// 2) resolveDocBlocks 抽出复用：详情页与工坊共用，不复制逻辑
assert.match(markdown, /export const resolveDocBlocks/, 'markdownToBlocks 应导出 resolveDocBlocks')
assert.match(docBlocks.includes('resolveDocBlocks') ? docBlocks : markdown, /normalizeBlocks/)

// 3) 路由与侧边栏：文档工坊单占一个创作组入口
assert.match(router, /path: 'doc-studio'/)
assert.match(router, /name: 'doc-studio'/)
assert.match(router, /title: '文档工坊'/)
assert.match(router, /group: '创作'/)
assert.match(menu, /label: '文档工坊'/)
assert.match(menu, /route: '\/doc-studio'/)

// 4) 工坊页：只读复用块渲染器 + 生成流 + 导入流 + 旧稿打开
assert.match(studioView, /data-qh-theme="light"/)
assert.match(studioView, /DocBlockView/)
assert.match(studioView, /:editable="false"/, '工坊预览必须只读，编辑在资源详情页完成')
assert.match(studioView, /openFullPage|resource-detail/)
assert.match(studioView, /exportMarkdown|toMarkdown/)
assert.match(studioView, /route\.query\.resource/)
assert.match(topicPanel, /importMarkdown/)
assert.match(topicPanel, /generate/, '面板必须把生成动作接到 studio.generate')

// 5) 生成流契约：doc 任务 + SSE 流 + block 事件必须归一后上屏
assert.match(useStudio, /createResourceTask\(/)
assert.match(useStudio, /'doc'/)
assert.match(useStudio, /streamResourceTask\(/)
assert.match(useStudio, /normalizeBlock\(/, 'block 事件必须过 normalizeBlock（后端发旧六值 type）')
assert.match(useStudio, /markdownToEditableBlocks\(/)
assert.match(useStudio, /resolveDocBlocks\(/)
assert.match(useStudio, /FREE_TOPIC_NODE_ID/)

// 6) 自由主题挂真实节点 + 五步工序与后端 ALCHEMY_STEPS['doc'] 对应
assert.match(config, /FREE_TOPIC_NODE_ID = 'general'/)
for (const step of ['source', 'relate', 'distill', 'verify', 'layout']) {
  assert.ok(config.includes(`id: '${step}'`), `docStudio.ts 缺少工序 ${step}`)
}

console.log('doc studio contract checks passed')
