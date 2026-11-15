/**
 * 方案协商的组件契约（三种工坊共用一套）。
 *
 * 这里只钉「结构性事实」：先出方案、方案能改能推翻、没确认不许生成、
 * 依据三档说得清、刷新回来不丢方案。真实交互由人工评审与截图验，
 * 这些断言负责防「改着改着把确认这道闸门拆了」。
 */

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = path => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')

const review = read('components/resources/PlanReview.vue')
const types = read('types/resource.ts')
const api = read('services/resourceApi.ts')
const pptStudio = read('composables/usePptStudio.ts')
const docStudio = read('composables/useDocStudio.ts')
const mapStudio = read('composables/useMindmapStudio.ts')
const pptView = read('views/PptStudioView.vue')
const docView = read('views/DocStudioView.vue')
const mapView = read('views/MindmapStudioView.vue')

// —— 方案面板：模态、可编辑、可退出 ——
assert.match(review, /role="dialog"/, '方案面板是对话框')
assert.match(review, /aria-modal="true"/, '面板模态：后面那层不能点')
assert.match(review, /暂不生成/, '必须留一条「不生成」的出路')
assert.match(review, /确认方案并生成/, '确认按钮说清「确认之后才生成」')
assert.match(review, /确认之后才会开始生成/, '把「确认是闸门」讲给用户听')

// 目标 / 受众 / 组织 / 呈现都要能改：方案是商量出来的，不是发下来的通知。
for (const field of ['goal', 'audience', 'organization', 'presentation']) {
  assert.match(review, new RegExp(`v-model="draft\\.${field}"`), `方案字段 ${field} 可编辑`)
}

// —— 单元（页 / 节 / 支）：增删改排序 + 依据档位 ——
assert.match(review, /addUnit/, '可以加一项')
assert.match(review, /draft\.units\.splice/, '可以删项、可以调顺序')
assert.match(review, /v-model="unit\.label"/, '标题可改')
assert.match(review, /v-model="unit\.intent"/, '这一项要达成什么可改')
assert.match(review, /v-model="unit\.source"/, '依据档位可改')
assert.match(review, /unit\.evidence/, '有依据就摆出依据')
assert.match(review, /unit\.gap/, '没依据就承认缺口，不装作有')
assert.match(review, /draft\.gaps/, '整份的资料缺口要显示')
assert.match(review, /draft\.clarification/, '只在关键歧义时追问')
assert.match(review, /draft\.notice/, '修订没生效要如实说，不能假装改过')

// —— 三档依据 ——
for (const tier of ['knowledge_base', 'user_material', 'model']) {
  assert.match(review, new RegExp(`value: '${tier}'`), `依据档位 ${tier} 在面板里`)
}
assert.match(review, /模型补充/, '模型补充必须自成一档，不能伪装成教材事实')

// —— 协商：改、推翻、退出 ——
assert.match(review, /emit\('revise', feedback\.value\.trim\(\), '', currentPlan\(\)\)/, '写反馈 → 带反馈和当前手改稿修订')
assert.match(review, /emit\('revise', feedback\.value\.trim\(\), goalDraft\.value\.trim\(\), currentPlan\(\)\)/, '重述目标 → 按新目标重拟')
assert.match(review, /const currentPlan = \(\): ResourcePlan/, '手改过的那一版要能被取出来')
// 确认传的是面板上这一版，且设计单同批带出去：只交方案等于让服务端再猜一遍版式。
assert.match(review, /emit\('confirm', currentPlan\(\), designDraft\.value/, '确认传的就是面板上这一版（方案 + 设计单）')
assert.match(review, /emit\('cancel'\)/, '可以主动退出协商')
assert.match(review, /一次写清即可/, '反馈合并处理，不让用户逐条排队')
// 空标题的单元不能带进生成：确认前先过滤掉。
assert.match(review, /filter\(\(unit: ResourcePlanUnit\) => unit\.label\.trim\(\)\)/, '空标题单元在确认前被剔除')
assert.match(review, /!draft\.goal\.trim\(\) \|\| !draft\.units\.length/, '没目标或没单元时不给确认')

// —— 类型契约 ——
assert.match(types, /export type PlanSource = 'knowledge_base' \| 'user_material' \| 'model'/, '依据只有三档')
assert.match(types, /export interface ResourcePlanUnit/, '方案单元是结构化契约，不是字符串列表')
assert.match(types, /export interface ResourceTaskSnapshot/, '任务快照供刷新后恢复')
assert.match(types, /awaiting_plan: boolean/, '快照要能回答「还在等确认吗」')
assert.match(types, /'plan'/, '任务事件含 plan')
assert.match(types, /'doc_verify'/, '任务事件含 doc_verify')
assert.match(types, /await_plan/, '暂停原因 await_plan 写进注释与契约')
assert.match(types, /export interface DeckPageCheck/, '逐页核对结果有类型')
assert.match(types, /export interface SectionSource/, '逐节依据有类型')
assert.match(types, /export interface DeckVerifyStats/, '逐页核对统计有类型')
assert.match(types, /pages\?: DeckPageCheck\[\]/, 'payload 带逐页结果')
assert.match(types, /source_map\?: SectionSource\[\]/, 'payload 带逐节依据')
assert.match(types, /plan_coverage\?:/, 'payload 带方案分支覆盖率')

// —— API：修订要带「当前那一版」，确认要能拿回落地后的方案 ——
assert.match(api, /getResourceTask/, '有任务快照接口')
assert.match(api, /plan\/revise/, '修订走方案接口')
assert.match(api, /plan\/confirm/, '确认走方案接口')
assert.match(api, /plan: options\.plan/, '修订带上面板里手改过的那一版')
assert.match(api, /goal: options\.goal \|\| ''/, '只写反馈时 goal 传空串，不误触推翻重建')
assert.match(api, /current_step: number; plan: ResourcePlan/, '确认后拿回服务端校验过的方案')

// —— 三个工坊都必须过同一道闸 ——
const studios = [
  { name: 'ppt', src: pptStudio, view: pptView, task: 'taskId' },
  { name: 'doc', src: docStudio, view: docView, task: 'taskId' },
  { name: 'mindmap', src: mapStudio, view: mapView, task: 'activeTaskId' },
]

for (const studio of studios) {
  const { name, src, view } = studio
  assert.match(src, /case 'plan':/, `${name}：消费方案事件`)
  assert.match(src, /await_plan/, `${name}：识别「等确认方案」的暂停原因`)
  assert.match(src, /phase\.value = 'awaiting-plan'/, `${name}：暂停后停在待确认态`)
  assert.match(src, /confirmResourcePlan/, `${name}：确认走方案确认接口`)
  assert.match(src, /revisePlan = async \(feedback: string, goal = '', draft\?: ResourcePlan\)/, `${name}：修订能带新目标与手改稿`)
  assert.match(src, /plan: draft \?\? plan\.value \?\? undefined, goal/, `${name}：修订优先用面板里那一版`)
  assert.match(src, /const cancelPlan = \(\)/, `${name}：可以退出协商`)
  assert.match(src, /const restoreTask = async \(id: string\)/, `${name}：刷新后能回到同一任务`)
  assert.match(src, /awaiting_plan && snapshot\.plan/, `${name}：恢复时优先还原待确认方案`)
  assert.match(src, /getResourceTask/, `${name}：恢复走任务快照`)
  assert.match(view, /@cancel="cancelPlan"/, `${name}：视图接上「暂不生成」`)
  assert.match(view, /@revise="revisePlan"/, `${name}：视图接上修订`)
  assert.match(view, /@confirm="confirmPlan"/, `${name}：视图接上确认`)
  assert.match(view, /phase === 'awaiting-plan' && plan/, `${name}：只有拿到方案才开面板`)
  assert.match(view, /route\.query\.task/, `${name}：带 ?task= 回到未完成的任务`)
}

// 确认之后才续流：先 confirm 再 stream，顺序反了就等于绕过确认。
for (const { name, src } of studios) {
  const start = src.indexOf('const confirmPlan')
  const next = src.indexOf('\n  const ', start + 1)
  const body = src.slice(start, next > start ? next : start + 900)
  const confirmAt = body.indexOf('confirmResourcePlan')
  const streamAt = body.search(/stream(?:ResourceTask)?\(/)
  assert.ok(confirmAt >= 0, `${name}：confirmPlan 先确认`)
  assert.ok(streamAt > confirmAt, `${name}：确认之后才续流`)
}

// —— 依据展示：整篇一个「有依据」等于没标 ——
assert.match(docStudio, /sectionSources/, '文稿记住逐节依据')
assert.match(docStudio, /case 'doc_verify':/, '文稿消费逐节核对事件')
assert.match(docView, /sectionSources/, '文稿把逐节依据画出来')
assert.match(docView, /教材依据/, '教材依据与模型补充要分开写')
assert.match(pptStudio, /deckCheck/, '幻灯记住逐页核对结果')
assert.match(pptView, /deckCheck\.missing/, '没落地的方案页要说出来')
assert.match(mapStudio, /planCoverage/, '导图记住方案分支覆盖率')
assert.match(mapView, /planCoverage\.missing/, '没落地的分支要说出来')

// —— 三步向导：说目标 / 定取舍 / 确认 ——
{
  const wizard = read('components/path/PathPlanWizard.vue')
  const mapView = read('views/MeridianMapView.vue')
  // 编辑走 PATCH 草稿，绝不走 reorder/defer（那三个直接写 user_path_state）
  assert.match(wizard, /patchPlanDraft/)
  assert.doesNotMatch(wizard, /reorderPath|deferPathNode/, '草稿阶段不许直接改进度')
  assert.match(wizard, /notice\.value = message/, '409 要把服务端那句话原样给用户')

  // 三步，不是四步：独立的「看细节 + 调掌握度」已经并进定取舍。
  // 把 0.70~0.95 的滑杆摆给还没开始学的人，他能做的判断只有"这俩数差不多"。
  assert.doesNotMatch(wizard, /el-input-number/, '掌握度滑杆不该再出现在向导里')
  assert.match(wizard, /<el-step title="说目标" \/>/)
  assert.match(wizard, /<el-step title="定取舍" \/>/)
  assert.match(wizard, /<el-step title="确认" \/>/)
  assert.doesNotMatch(wizard, /title="看细节"/, '看细节这一步已取消')
  assert.match(wizard, /if \(step\.value === 2\) return '路线已生效'/)

  // 一周转内的取舍是一张可点的导图，不是 291 行清单
  assert.match(wizard, /PathPlanTree/, '定取舍用导图')
  assert.match(wizard, /@toggle="toggleUnit"/, '点节点就能增删')
  assert.match(wizard, /op: 'remove'/, '点掉一节发 remove 草稿操作')
  assert.match(wizard, /op: 'include'/, '加回来走 include，回到原来的章里')

  // 缺信息先问一轮，并把"不填的默认值"摆出来
  assert.match(wizard, /clarifyPlan/, '排之前先追问')
  assert.match(wizard, /clarify\.assumptions/, '没答的项要用默认值说清楚')

  // 一步定稿：不要再让用户点两次确认
  assert.match(wizard, /finalizePlanDraft/, '确认取舍即定稿')

  // 确认回调里关窗 = 用户看不到「已生效」那一屏
  const start = mapView.indexOf('const onConfirmed')
  const body = mapView.slice(start, start + 400)
  assert.ok(start >= 0, '地图视图要接 @confirmed')
  assert.doesNotMatch(body, /planVisible\.value = false/, '关窗交给向导，回调里关掉就看不到结果')
}

// —— 确认之后不再推销任何"材料"：排完就去看图 ——
{
  const wizard = read('components/path/PathPlanWizard.vue')
  const api = read('services/pathApi.ts')

  // 用户原话："生成完就行了，不需要这些内容，直接让用户看图就行"。
  // 这一步曾经摆四张卡片（导图 / 说明书 / 任务卡 / 对照表），正文是模板生成的
  // 固定段落，对"我要考这学期期末"的人没有一分信息量，还要再点一次才看得到东西。
  assert.equal(wizard.match(/presentPlan\s*\(/g)?.length ?? 0, 0, '确认后不该再导材料')
  assert.doesNotMatch(wizard, /presentation_options/, '材料卡片流已删除')
  assert.match(wizard, /去地图上看/, '确认后的唯一动作是去地图')
  // 能力本身保留：对话里用户主动要一份材料时还得能出
  assert.match(api, /\/api\/capabilities\/plan\.present/, '能力接口别跟着界面一起删掉')
}

// —— 追问要按他的话问：题面与示例都来自后端 ——
{
  const wizard = read('components/path/PathPlanWizard.vue')
  const types = read('types/path.ts')

  // 三题共用一个"例如明年 3 月"是写死的 bug：问到"什么水平"时那个例子是错的
  assert.match(wizard, /question\.placeholder/, '输入框示例必须逐题来，不能在前端写死')
  assert.doesNotMatch(wizard, /例如「明年 3 月」/, '写死的示例已删除')
  assert.match(types, /placeholder\?:\s*string/, '类型里要声明每题自己的示例')

  // 范围要回显：用户说过"就想考中药学期末"，就得看见"本次只排 中药学"，
  // 而不是从 265 节里自己推断范围到底有没有生效
  assert.match(wizard, /scope_subjects/, '范围回显读后端声明的学科清单')
  assert.match(types, /scope_subjects\?:/, '类型里要声明范围字段')
}

console.log('plan negotiation contract checks passed')
