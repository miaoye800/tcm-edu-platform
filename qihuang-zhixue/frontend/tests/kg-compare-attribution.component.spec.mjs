import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')

/** 剥掉注释再做「不许出现某词」的断言，否则解释性注释会把断言弄成假阳性。 */
const stripComments = (source) =>
  source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const compare = read('../src/views/CompareView.vue')
const mistakes = read('../src/views/MistakesView.vue')
const api = read('../src/services/kgApi.ts')
const types = read('../src/types/kg.ts')
const menu = read('../src/config/menu.ts')
const router = read('../src/router/index.ts')

// --- 对比页：教材原文优先，T1 约束必须排在差异表前面 -------------------------

assert.match(compare, /compareHerbs/)
assert.match(compare, /textbook_differentiation/)
assert.match(compare, /教材原文/)
// 「能不能一起用」比「差在哪」更急：约束卡必须先于逐项差异渲染。
assert.ok(
  compare.indexOf('constraint-card') < compare.indexOf('逐项差异'),
  'T1 约束卡必须排在逐项差异之前',
)
assert.match(compare, /result\.constraints/)
assert.match(compare, /!result\.ok/)
// 对比不调模型：页面里不许出现任何生成式调用。
assert.doesNotMatch(stripComments(compare.slice(0, compare.indexOf('<script'))), /chat|llm|generate/i)
assert.doesNotMatch(stripComments(compare.slice(compare.indexOf('<script'))), /chat|llm|generate/i)
// 模板里不能残留 markdown 星号——Vue 不会渲染它们，会原样显示。
const compareTemplate = compare.slice(0, compare.indexOf('<script'))
assert.doesNotMatch(compareTemplate, /\*\*/, '模板里残留了 markdown 星号')

// --- 对比页：关系链必须真的查（P6 第 2 项不能只有接口没有入口） -------------

assert.match(compare, /findKgPath/, '对比页没有调用 /api/kg/path，关系链在界面上看不到')
assert.match(compare, /pathResult/, '对比页缺少关系链状态')
assert.match(compare, /它们在图里怎么连着/)
// 查不到也要把原因显示出来：「3 跳内没有路径」不等于「没关系」。
assert.match(compare, /pathResult\.reason/)
assert.match(compare, /step\.relation_type/)

// --- 归因：unknown 是一等公民，不许被折叠进章节 -----------------------------

// 归因类型是闭集：后端给几种，前端就得认几种。锁**成员**不锁字面量格式——
// 上一版锁了完整联合类型的字符串，加一个类型就要改测试，测试反而挡路。
for (const cause of ['confused_with', 'forbidden_pair', 'answer_incomplete', 'wrong_herb', 'unknown']) {
  assert.match(types, new RegExp(`'${cause}'`), `前端 CauseType 缺 ${cause}`)
  assert.match(mistakes, new RegExp(`${cause}:`), `前端色调映射缺 ${cause}`)
}
assert.match(mistakes, /getMyAttribution/)
assert.match(mistakes, /summary\.unattributed/)
// 归因不了的错题单独提示，不能被算进 by_domain。
assert.match(mistakes, /不会被算进任何章节/)
assert.match(mistakes, /item\.causes\[0\]\?\.quote/)
assert.match(mistakes, /notes/)
// 混淆对象要能一键跳到对比页——这是归因结果的唯一可操作出口。
assert.match(mistakes, /name: 'compare'/)
assert.match(mistakes, /focus_node_id/)

// --- 服务层：这些端点都要登录，不许静默跳登录 -------------------------------

for (const endpoint of ['/api/kg/search', '/api/kg/compare', '/api/kg/path', '/api/mistakes/attribution']) {
  assert.ok(api.includes(endpoint), `missing endpoint ${endpoint}`)
}
assert.doesNotMatch(stripComments(api), /skipAuthRedirect/, '图谱/归因端点要登录，不能跳过 401 处理')

// --- 路由与菜单：对比页必须可达 ---------------------------------------------

assert.match(router, /name: 'compare'/)
assert.match(menu, /route: '\/compare'/)

// --- 归因提示语不许写成 markdown --------------------------------------------

assert.doesNotMatch(
  mistakes.slice(0, mistakes.indexOf('<script')),
  /\*\*/,
  '模板里残留了 markdown 星号',
)

console.log('kg compare and attribution contract checks passed')
