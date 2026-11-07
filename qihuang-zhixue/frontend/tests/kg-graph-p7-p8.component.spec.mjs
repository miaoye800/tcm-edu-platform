import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')
const stripComments = (source) =>
  source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const api = read('../src/services/graphApi.ts')
const view = read('../src/components/graph/GraphView.vue')
const types = read('../src/types/graph.ts')
const store = read('../src/stores/graph.ts')

// --- P7：图谱查询走 /api/kg/neighbors，不再走 /api/graph/{center_id} ---------

assert.match(api, /\/api\/kg\/neighbors/, 'getGraph 没有指向 /api/kg/neighbors')
assert.doesNotMatch(
  stripComments(api),
  /\/api\/graph\/\$\{/,
  'getGraph 仍在用旧的 /api/graph/{center_id}',
)
// 中心节点必须编码：传进来的常是中文药名或带点的树 id（d17.s1.p1）。
assert.match(api, /encodeURIComponent\(centerId\)/, 'center_id 没做 URL 编码')
// 这条断言以前命中的是 graphApi.ts 里提到 tiers 的**注释**，等于什么都没查。
// 去掉注释后再看：函数签名里要有 options.tiers 这个可选参数。
assert.match(stripComments(api), /options\.tiers/, 'getGraph 没有 tiers 参数（只匹配到了注释）')
// 默认只取 T1+T2，T3 不落库。默认值不在 api 层写死（那里只负责透传），
// 而是由 store 显式给出——这样「默认看哪几层」只有一处定义。
assert.match(stripComments(api), /params\.set\('tiers', options\.tiers\)/, 'getGraph 没有把 tiers 透传出去')
assert.match(store, /tiers:\s*'1,2'/, "store 没显式指定 tiers: '1,2'")
// store 必须显式传参：不传就是吃后端默认 limit=40，前端永远看不到截断，
// 「已隐藏 N 条」也就永远显示不出来。
assert.match(store, /getGraph\(centerId,\s*\{[^}]*limit/, 'store 调 getGraph 时没显式传 limit')
assert.match(store, /getGraph\(centerId,\s*\{[^}]*tiers/, 'store 调 getGraph 时没显式传 tiers')

// P7 的验收口径是「只改一个文件」：组件与 store 不许跟着改端点。
assert.doesNotMatch(stripComments(view), /\/api\/(kg|graph)\//, 'GraphView 里出现了裸接口路径')
assert.doesNotMatch(stripComments(store), /\/api\/(kg|graph)\//, 'stores/graph.ts 里出现了裸接口路径')
// 但 store 必须仍然能识别「这是真数据」——这是 dev 面板 usingMock 的来源。
assert.match(store, /usingMock = false/)

// --- P8：按可信层筛选 -------------------------------------------------------

assert.match(types, /tier\?: 1 \| 2 \| 3/, 'GraphEdge 没有 tier 字段')
assert.match(view, /tierFilters/, 'GraphView 没有层级筛选状态')
assert.match(view, /toggleTier/, 'GraphView 缺少层级切换')
// 筛选必须真的进入 visible 计算，只加按钮不加过滤等于摆样子。
const visibleBlock = view.slice(view.indexOf('const visible = computed'), view.indexOf('const connectedNodes'))
assert.match(visibleBlock, /tierFilters\.value\.has/, 'visible 没有按 tier 过滤')
// 早期边没有 tier 字段：默认按 T2 计，归到 T1 会把禁忌列表污染成假阳性。
assert.match(visibleBlock, /edge\.tier \?\? 2/, '缺少「无 tier 视为 T2」的兜底')
// 两层都默认开启，否则首屏会少一半关系。
assert.match(view, /new Set<number>\(\[1, 2\]\)/, 'tier 默认值不是两层都开')
// 角标数字来自真实边，不是写死的。
assert.match(view, /tierCounts/, '缺少每层边数角标')

// --- P9：「已隐藏 N 条」必须真能算出来 -------------------------------------

// 后端只说 truncated 说不出「砍了几条」，前端必须同时吃 total_nodes。
assert.match(types, /total_nodes\?: number/, 'GraphContent.meta 没透出 total_nodes')
assert.match(types, /truncated\?: boolean/, 'GraphContent.meta 没透出 truncated')
const hiddenBlock = view.slice(view.indexOf('const hiddenCount'), view.indexOf('const connectedNodes'))
assert.match(hiddenBlock, /meta\?\.total_nodes/, '「已隐藏」没读后端 total_nodes，靠 limit 反推是错的')
assert.match(hiddenBlock, /totalEdges - visible\.value\.edges\.length/, '「已隐藏」没算前端裁剪掉的边')
// 只在真隐藏了才显示：常驻一个「未显示 0 条」比不显示更吵。
assert.match(view, /v-if="hiddenCount > 0"/, '隐藏提示没有 0 值保护')

// --- P10：「只看禁忌」是主动选择，不是默认值 ---------------------------------

// 默认两层都开的断言在上面（P8）。默认只开 T1 会让首屏空图（麻黄 T1 边数 = 0），
// 于是形成「教材未收录」的假结论——所以「干净」只能由用户点出来。
assert.match(view, /只看禁忌/, '缺少「只看禁忌」预设按钮')
const presetBlock = view.slice(view.indexOf('const constraintOnly'), view.indexOf('let renderer'))
assert.match(presetBlock, /size === 1 && tierFilters\.value\.has\(1\)/, '预设的激活条件不是「只剩 T1」')
assert.match(presetBlock, /new Set<number>\(\[1, 2\]\)/, '再点一次没有退回两层，进了出不来')
// 预设必须真的改 tierFilters，只加按钮不改状态等于摆样子。
assert.match(presetBlock, /tierFilters\.value =/, '预设没有写回 tierFilters')

console.log('kg graph P7 endpoint + P8 tier filter + P9 hidden-count + P10 constraint preset contract checks passed')
