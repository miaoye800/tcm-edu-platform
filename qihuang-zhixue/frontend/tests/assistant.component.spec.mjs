/**
 * AI 服务助手的组件契约。
 *
 * 只钉结构性事实，不测像素。三条最容易被改坏的：
 *
 * 1. 它必须是个**页面**，不是一个空壳路由或抽屉开关；
 * 2. 生成类工具只拿到 202 就返回，方案必须是**确认之后**才续跑；
 * 3. 导航按钮由后端给，模型不参与——这条靠「没有 navigate 工具」来守。
 */

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = (path) => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')

const router = read('router/index.ts')
const menu = read('config/menu.ts')
const store = read('stores/assistant.ts')
const view = read('views/AssistantView.vue')
const api = read('services/assistantApi.ts')
const types = read('types/assistant.ts')
const planCard = read('components/assistant/PlanCard.vue')
const composer = read('components/assistant/ComposerBar.vue')
const messageItem = read('components/assistant/MessageItem.vue')
const empty = read('components/assistant/AssistantEmpty.vue')

// —— 入口：真实页面，不是 redirect ——
assert.match(router, /component: AssistantView/, '/assistant 必须挂真实组件')
assert.match(router, /path: 'assistant'/, '路由存在')
assert.doesNotMatch(router, /path: 'assistant',\s*redirect/, '不能再 redirect 到仪表盘')
assert.match(router, /title: 'AI 服务助手'/, '页面标题是 AI 服务助手')

assert.match(menu, /label: 'AI 服务助手'/, '侧栏叫 AI 服务助手')
assert.match(menu, /route: '\/assistant'/, '侧栏指向助手页')
assert.doesNotMatch(menu, /assistantTrigger/, '不再用「开抽屉」的入口')
assert.match(store, /router\.push\('\/assistant'\)/, '入口跳页面')

// —— 对话流：POST + Bearer + 六种事件 ——
assert.match(api, /'\/api\/assistant\/chat\/stream'/, '对话走 POST 流')
assert.match(api, /authHeaders\(\{ Accept: 'text\/event-stream'/, 'SSE 也要带 Bearer（EventSource 做不到）')
assert.match(api, /runs\/\$\{encodeURIComponent\(runId\)\}\/stream/, '断线能重连')
assert.match(api, /artifacts\/\$\{encodeURIComponent\(taskId\)\}\/plan\/confirm/, '确认走助手接口')
assert.match(api, /artifacts\/\$\{encodeURIComponent\(taskId\)\}\/stream/, '确认后有续流')

for (const event of ['message_start', 'token', 'tool_start', 'tool_progress', 'tool_result', 'plan_card', 'artifact', 'done', 'error']) {
  assert.match(view, new RegExp(`case '${event}'`), `视图处理 ${event} 事件`)
}

// —— 确认是闸门：先确认，再续流 ——
const confirmAt = view.indexOf('await confirmAssistantPlan(')
const resumeAt = view.indexOf('await streamAssistantArtifact(')
assert.ok(confirmAt > 0, '确认要真的发出去')
assert.ok(resumeAt > confirmAt, '必须先确认再订阅续流，顺序反了等于自动确认')

assert.match(planCard, /确认，按这份方案生成/, '确认按钮说清后果')
assert.match(planCard, /不确认不会生成/, '把闸门讲给用户听')
assert.match(planCard, /v-model="unit\.label"/, '方案单元标题可改')
assert.match(planCard, /emit\('confirm'/, '确认把改过的方案带出去')
assert.match(planCard, /clone\(props\.plan\)/, '改的是草稿，不直接改父级对象')

// —— 工具行：要能展开看参数与失败原因 ——
assert.match(messageItem, /<ToolRow/, '工具调用在消息里可见')
assert.match(messageItem, /confirm-plan/, '方案卡从消息里发出确认')

// —— 输入区 ——
assert.match(composer, /@keydown="onKeydown"/, 'Enter 发送')
assert.match(composer, /event\.shiftKey/, 'Shift+Enter 换行')
assert.match(composer, /emit\('stop'\)/, '生成中可以停')
assert.match(composer, /shiftKey[\s\S]*isComposing|isComposing/, '输入法组字时不能误发')

// —— 空态必须给入口，不是一句「开始对话吧」 ——
assert.match(empty, /emit\('ask'/, '空态卡片点了就能问')
assert.match(empty, /做一张解表药的思维导图/, '示例问法要落在语料范围内')

// —— 消息类型契约 ——
for (const kind of ['text', 'tool', 'plan', 'artifact', 'error']) {
  assert.match(types, new RegExp(`type: '${kind}'`), `消息类型 ${kind} 在契约里`)
}

// —— 导航不由模型决定 ——
assert.doesNotMatch(view, /navigate/i, '视图不认模型给的导航指令')

// —— 滚动：方案卡要顶到顶部，不能滚到底 ——
// 方案卡能有一屏高。滚到底的话用户看见的是第 5、6 单元和「确认」，卡头写着
// 「这份方案讲什么」反而在屏幕外，确认就成了盲签。这条是实拍里发现的。
assert.match(view, /const scrollToItem/, '需要按消息定位的滚动')
assert.match(view, /data-item-id/, '消息要带定位用的 id')
assert.match(view, /pinTo = `plan_\$\{taskId\}`/, '方案卡出现时定位到卡本身')
assert.match(view, /if \(pinTo\) void scrollToItem\(pinTo\)\s*\n\s*else void scrollToBottom\(\)/, '有要定位的消息就不要滚到底')

// —— 右栏：没产物不占位 ——
// 空着一条 196px 的「还没有产物」比没有右栏更碍事；有第一份产物才让出来。
assert.match(view, /const railOpen = ref\(false\)/, '右栏默认收起')
assert.match(view, /if \(!railTouched\.value\) railOpen\.value = true/, '有产物时自动展开')

// —— 会话列表：第一句话之后要长出这一条 ——
// 后端是收到第一句话才建会话的。不刷新列表，右边聊了三轮，左边还写着「还没有对话」。
const startBranch = view.slice(view.indexOf("case 'message_start'"), view.indexOf("case 'token'"))
assert.match(startBranch, /refreshConversations\(\)/, '新会话开始后刷新左栏')

console.log('assistant component contract checks passed')

// —— 新增产物：试卷与漫剧走同一条渲染路径 ——
// 不要求前端为每种产物写一个组件，只要求标签认得它；认不出来会显示「产物」。
const artifactCard = read('components/assistant/ArtifactCard.vue')
assert.match(artifactCard, /story: '漫剧'/, '产物卡认得漫剧')
assert.match(artifactCard, /quiz: '试卷'/, '产物卡认得试卷')
assert.match(view, /story: '漫剧'/, '右栏也认得漫剧')
assert.match(types, /kind: string/, '产物类型是开放字符串，不是联合类型')

// —— 依据要写在产物卡上 ——
assert.match(messageItem, /:verify="item\.content\.verify"/, '产物卡拿到校验结果')
assert.match(artifactCard, /data-grounded/, '依据不足要能看出来')

// —— 失败可重试 ——
assert.match(messageItem, /emit\('retry'\)/, '错误消息里有重试')
assert.match(view, /const retry = \(\) =>/, '重试把上一次的问题重发')
assert.match(view, /@retry="retry"/, '重试接到视图上')
// 重试必须是一次新的轮次：复用 client_turn_id 只会把失败原样回放一遍。
const retryBlock = view.slice(view.indexOf('const retry = () =>'), view.indexOf('const syncAfterTurn'))
assert.doesNotMatch(retryBlock, /client_turn_id/, '重试不复用幂等键')

// —— @ 引用既有资源 ——
assert.match(composer, /emit\('lookResources'\)/, '打 @ 时才拉资源清单')
assert.match(composer, /emit\('send', text, selected\.value\.map/, '发送时把引用 id 一起带上')
assert.match(composer, /lastIndexOf\('@'\)/, '只认光标前那个 @')
assert.match(view, /client_turn_id: uid\(\), refs/, '引用进了请求体')

console.log('assistant extension contract checks passed')
