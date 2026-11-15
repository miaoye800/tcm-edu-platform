/**
 * 导图工坊的组件契约。
 *
 * 这些断言检查的是「这一版必须成立的结构性事实」：入口在哪、内核是谁、
 * 四结构四主题都还在、逐节点生长不漏不重、三档依据能不能被数出来、
 * 问法信息有没有真的接到界面上。
 *
 * 只读源码不跑浏览器 —— 真正的视觉与交互由 scripts/shot-studio.mjs 截图验，
 * 这里负责防「改着改着把某个环节改没了」。
 */

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8')

const view = read('../src/views/MindmapStudioView.vue')
const canvas = read('../src/components/mindmap/StudioCanvas.vue')
const chatPanel = read('../src/components/mindmap/StudioChatPanel.vue')
const card = read('../src/components/mindmap/ChatCompleteCard.vue')
const toolbar = read('../src/components/mindmap/StudioToolbar.vue')
const studio = read('../src/composables/useMindmapStudio.ts')
const chat = read('../src/composables/useMindmapChat.ts')
const treeUtils = read('../src/utils/mindmapTree.ts')
const structures = read('../src/config/mindmapStructures.ts')
const themes = read('../src/config/mindmapThemes.ts')
const studioConfig = read('../src/config/mindmapStudio.ts')
const resourceApi = read('../src/services/resourceApi.ts')
const capabilityApi = read('../src/services/capabilityApi.ts')
const capabilityTypes = read('../src/types/capability.ts')
const resourceTypes = read('../src/types/resource.ts')
const router = read('../src/router/index.ts')
const menu = read('../src/config/menu.ts')

// —— 入口：路由 + 侧栏是两份独立配置，缺一个就点不进来 ——
assert.match(router, /path: 'mindmap'/, '导图工坊路由已注册')
assert.match(router, /name: 'mindmap-studio'/, '路由名固定，供 push({ name }) 使用')
assert.match(menu, /route: '\/mindmap'/, '侧栏菜单指向 /mindmap')

// —— 页面形态：左对话栏 + 右画布 + 底部状态栏 ——
assert.match(view, /<StudioChatPanel/, '左侧是对话栏，不是参数表单')
assert.match(view, /<StudioCanvas/, '右侧是画布')
assert.match(view, /class="status"/, '底部有状态栏')
assert.match(view, /data-qh-theme="light"/, '页面固定米白纸主题')
assert.match(view, /chat-collapsed/, '对话栏可收起，收起后画布拿回宽度')
assert.match(chatPanel, /PLACEHOLDER/, '输入框有提示字段')
assert.match(chatPanel, /CHAT_SUGGESTIONS/, '冷启动给四条建议')
assert.match(view, /prefers-reduced-motion|StateLoading/, '加载态与动效都有兜底')

// —— 渲染内核：simple-mind-map（mind-elixir 只支持左右树，已换掉） ——
assert.match(canvas, /from 'simple-mind-map'/, '画布用 simple-mind-map')
// 注释里提到 old 内核没问题，不能再有真的 import。
assert.doesNotMatch(canvas, /from 'mind-elixir'/, '不再 import mind-elixir')
assert.doesNotMatch(view, /from 'mind-elixir'/, '页面不再 import mind-elixir')

// —— 四结构 / 四主题 ——
for (const id of ['logic-right', 'mind-both', 'org-down', 'fishbone']) {
  assert.match(structures, new RegExp(`id: '${id}'`), `结构 ${id} 仍在`)
}
for (const id of ['indigo', 'verdant', 'violet', 'inkCream']) {
  assert.match(themes, new RegExp(`id: '${id}'`), `主题 ${id} 仍在`)
}
assert.match(toolbar, /StructureSwitch/, '工具栏能换结构')
assert.match(toolbar, /ThemeSwitch/, '工具栏能换配色')
assert.match(card, /mindmapStructures/, '完成卡也能就地换结构')
assert.match(card, /mindmapThemes/, '完成卡也能就地换配色')

// —— 逐节点生长：去重 / 父先于子 / done 早到 ——
assert.match(canvas, /seen\.has\(node\.id\)/, '按 id 去重，重连重推不会长两遍')
assert.match(canvas, /index\.get\(node\.parent_id\)/, '父节点用 id 定位，不靠到达顺序')
assert.match(canvas, /deferred\.push\(node\)/, '父节点未到时先挂起，下一轮重排而非丢弃')
assert.match(canvas, /pendingDone/, 'done 早于队空时先挂起')
assert.match(studioConfig, /NODE_INTERVAL_MS/, '生长节流常量集中定义')
assert.match(canvas, /on\('data_change'/, '用户改动走库的 data_change 事件')
assert.match(canvas, /suppressDirty/, '生长期间压住 setData 自己派发的 data_change')

// —— 结构切换：折叠是真折，不是只改个名字 ——
assert.match(canvas, /toSimpleTree\(/, '重建时统一过一遍 toSimpleTree')
assert.match(treeUtils, /expand: false/, '超出结构层数的节点折叠（保留 children，切回来不丢）')
assert.match(treeUtils, /countFolded/, '折了多少个能数出来')
assert.match(view, /describeOverflow/, '状态栏说明「折了 N 个」')
assert.match(canvas, /node_tree_render_end/, '布局真算完才 fit，避免空包围盒')
assert.match(canvas, /ResizeObserver/, '收起对话栏后画布重新适配')

// —— 三档依据：教材 / 图谱 / 推断 ——
assert.match(resourceTypes, /MindmapEvidenceCounts/, '三档计数有类型')
assert.match(studio, /textbook: 0, graph: 0, model: 0/, '底栏三档分开计数')
assert.match(studio, /payload\.evidence/, '校验步的三档计数被消费')
assert.match(view, /教材 \{\{ evidence\.textbook \}\}%/, '底栏显示教材占比')
assert.match(view, /推断 \{\{ evidence\.model \}\}%/, '底栏显示推断占比')
assert.match(view, /evidence\.warn/, '推断偏多时给提示')

// —— 问法：按什么组织，界面要说出来 ——
assert.match(resourceTypes, /intent_note/, '事件载荷带后端写好的那句话')
assert.match(studio, /intentNote/, '问法说明进状态')
assert.match(studio, /degraded/, '域外标记进状态')
assert.match(card, /note/, '完成卡渲染这句说明')
assert.match(view, /主题不在本地教材内/, '底栏对域外主题明确降级')
assert.match(studio, /structureTouched/, '用户亲手选过结构才锁定结构')
assert.match(capabilityTypes, /client_invocation_id/, '契约类型里有幂等键')

// —— 生成：一次调用一张图，重试不重复入库 ——
assert.match(resourceApi, /createResourceTask = \(nodeId: string, resourceType: ResourceType, params: Record<string, unknown> = \{\}\)/, '建任务接口仍保留给其它资源类型')
assert.match(capabilityApi, /'\/api\/capabilities\/mindmap\.generate'/, '导图生成走能力契约')
assert.match(capabilityApi, /body\.code/, '契约层拒绝时按 code 抛出，不只给一句话')
assert.match(studio, /generateMindmap\(\{/, '左栏是能力契约的第一个真实调用方')
assert.match(studio, /node_id: selectedNode\.value\?\.id \?\? FREE_TOPIC_NODE_ID/, '自由主题落到 general 节点')
assert.match(studio, /structure: structureTouched\.value \? structureId\.value : 'auto'/, '没选过结构时交后端按问法建议')
assert.match(studio, /client_invocation_id: invocationId/, '带幂等键，重试不出两张图')
assert.match(chat, /makeInvocationId/, '对话侧生成幂等键')
assert.match(studio, /case 'node'/, '消费逐节点事件')
assert.match(studio, /node_total/, '节点总数来自后端首条事件')
assert.match(studio, /mindmap_verify/, '校验统计事件被消费')

// —— 保存：防抖 + 409 双出口 ——
assert.match(studio, /saveMindmapTree\(/, '整树保存走独立接口')
assert.match(studio, /SAVE_DEBOUNCE_MS/, '自动保存有防抖')
assert.match(studio, /pendingSave/, '保存中再编辑会续排')
assert.match(studio, /error\.status === 409/, '409 单独识别')
assert.match(studio, /reloadFromServer/, '冲突可重新载入')
assert.match(studio, /keepMine/, '冲突可保留本地版本')
assert.match(view, /saveStatus === 'conflict'/, '冲突提示条渲染在画布上方')
assert.match(studio, /Number\.isFinite\(result\.node_count\)/, '回执没有数字时不把底栏清零')

// —— 空 / 加载 / 错误三态走标准组件 ——
assert.match(view, /StateEmpty/, '空态用标准组件')
assert.match(view, /StateLoading/, '加载态用标准组件')
assert.match(view, /StateError/, '错误态用标准组件')

// —— 收卷：完成卡上的数字要跟着实时统计走 ——
assert.match(chat, /watch\(\(\) => studio\.stats\.value, patchResult/, '完成卡跟随实时统计，不是快照')

// —— 方案协商：先出方案、确认之后才铺枝 ——
assert.match(view, /phase === 'awaiting-plan' && plan/, '只在拿到方案时开面板')
assert.match(view, /@revise="revisePlan"/, '面板可带反馈修订')
assert.match(view, /@confirm="confirmPlan"/, '面板可确认')
assert.match(view, /@cancel="cancelPlan"/, '面板可退出（暂不生成）')
assert.match(studio, /case 'plan':/, '消费方案事件')
assert.match(studio, /await_plan/, '识别「等确认方案」的暂停')
assert.match(studio, /confirmResourcePlan/, '确认走方案接口')
assert.match(view, /route\.query\.task/, '刷新回来先认任务，不重开一张')

// —— 方案分支落地率：确认过的结构不能悄悄少一支 ——
assert.match(studio, /planCoverage/, '记住方案分支覆盖率')
assert.match(studio, /payload\.plan_coverage/, '读后端给的覆盖率')
assert.match(view, /planCoverage\.missing/, '没落地的分支要说出来')

console.log('mindmap studio component contract checks passed')
