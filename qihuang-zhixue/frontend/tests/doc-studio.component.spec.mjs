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
const listItem = read('components/doc/blocks/ListItemBlock.vue')

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

// 7) 方案协商：先出方案、确认之后才落笔
assert.match(studioView, /phase === 'awaiting-plan' && plan/, '只在拿到方案时开面板')
assert.match(studioView, /@revise="revisePlan"/, '面板可带反馈修订')
assert.match(studioView, /@confirm="confirmPlan"/, '面板可确认')
assert.match(studioView, /@cancel="cancelPlan"/, '面板可退出（暂不生成）')
assert.match(useStudio, /case 'plan':/, '消费方案事件')
assert.match(useStudio, /await_plan/, '识别「等确认方案」的暂停')
assert.match(useStudio, /confirmResourcePlan/, '确认走方案接口')
assert.match(studioView, /route\.query\.task/, '刷新回来先认任务，不重开一份')

// 8) 逐节依据：整篇一个「有依据」等于没标
assert.match(useStudio, /case 'doc_verify':/, '消费逐节核对事件')
assert.match(useStudio, /sectionSources/, '逐节依据留在前端')
assert.match(studioView, /教材依据/, '教材依据与模型补充分开写')
assert.match(studioView, /docCheck/, '核对结论（未落章节 / 无效引文）要显示')

// 9) 复选框：预览只读 ≠ 清单不能勾；勾了必须存回资源，不能只改本地
assert.doesNotMatch(
  listItem,
  /if \(!props\.editable\) return/,
  '复选框不得因为正文只读就点不动——用户会以为整篇文档都是死的',
)
assert.match(studioView, /@update="toggleCheck"/, '工坊要把待办勾选接住')
assert.match(useStudio, /const toggleCheck/, '工坊要能勾选待办')
assert.match(useStudio, /saveDocument\(/, '勾选要存回资源（只改本地等于下次打开又变没勾）')
assert.match(useStudio, /doc_version/, '存回要带版本号，否则撞版本冲突')
assert.match(useStudio, /replaceInTree/, '折叠块里的待办要按树改，不能只替换顶层')

console.log('doc studio contract checks passed')
