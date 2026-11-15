import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')

const view = read('../src/views/PptStudioView.vue')
const surface = read('../src/components/ppt/SlideSurface.vue')
const stage = read('../src/components/ppt/DeckStage.vue')
const strip = read('../src/components/ppt/SlideFilmstrip.vue')
const toolbar = read('../src/components/ppt/PptToolbar.vue')
const panel = read('../src/components/ppt/PptTopicPanel.vue')
const outlineConfirm = read('../src/components/ppt/OutlineConfirm.vue')
const composable = read('../src/composables/usePptStudio.ts')
const studioConfig = read('../src/config/pptStudio.ts')
const themes = read('../src/config/pptThemes.ts')
const exportUtil = read('../src/utils/pptxExport.ts')
const router = read('../src/router/index.ts')
const menu = read('../src/config/menu.ts')
const resourceApi = read('../src/services/resourceApi.ts')
const resourceTypes = read('../src/types/resource.ts')
const pptTypes = read('../src/types/ppt.ts')
const immersive = read('../src/config/immersive.ts')
const resourcesView = read('../src/views/MyResourcesView.vue')
const resourceCard = read('../src/components/resources/ResourceCard.vue')
const detailView = read('../src/views/ResourceDetailView.vue')

// —— 入口：路由 + 侧栏「创作」组，两者是独立配置，必须同时存在 ——
assert.match(router, /path: 'ppt'/, '幻灯工坊路由已注册')
assert.match(router, /name: 'ppt-studio'/, '路由名固定，供 push({ name }) 使用')
assert.match(router, /title: '幻灯工坊'/, '顶栏标题来自 meta.title')
assert.match(router, /group: '创作'/, '路由 meta.group 与侧栏分组一致')
assert.match(menu, /label: '幻灯工坊'/, '侧栏有入口')
assert.match(menu, /route: '\/ppt'/, '菜单指向 /ppt')
// 分组顺序：图谱之后、互动之前，创作自成一类。
assert.ok(menu.indexOf("group: '创作'") > menu.indexOf("group: '图谱'"), '「创作」排在图谱之后')
assert.ok(menu.indexOf("group: '创作'") < menu.indexOf("group: '互动'"), '「创作」排在互动之前')

// —— 视觉：纸面、舞台配比、无缩放 ——
assert.match(view, /data-qh-theme="light"/, '页面固定米白纸主题')
assert.match(view, /container-type: size/, '舞台位自己声明尺寸容器，纸才能按高反推宽')
assert.match(view, /aspect-ratio: 16 \/ 9/, '纸是 16:9')
assert.match(view, /@supports \(height: 1cqh\)/, '不支持 cqh 时退回按宽排')
assert.match(view, /prefers-reduced-motion/, '动画有减弱替代')
assert.match(view, /prefers-reduced-motion[\s\S]*?transition: none/, '减弱动效下不播过渡')
// 排版用 cqw 等比，不用 transform: scale：缩放会让文字发虚、输入框点不准。
// 字号不再写死在组件里，七级字号统一从 render/frames.ts 取，线框与成品同源。
const frames = read('../src/render/frames.ts')
assert.match(surface, /font-size: var\(--s-title\)/, '标题字号来自 frames 的字号阶梯')
assert.match(frames, /TYPE_SCALE = \[7\.6, 4\.6, 3\.2, 2\.2, 1\.8, 1\.5, 1\.3\]/, '字号七级集中定义')
assert.match(surface, /padding: 8\.4cqw 9cqw 7\.2cqw/, '版心与线框同一组数字（天头 8.4 / 左右 9 / 地脚 7.2）')
assert.doesNotMatch(surface, /transform:\s*scale\(/, '不用缩放冒充自适应')
assert.doesNotMatch(surface, /\.surface\s*\{[^}]*container-type/, '容器不能声明在纸自己身上，否则 cqw 参照错层')
assert.match(stage, /container-type: inline-size/, '舞台是纸的容器')
assert.match(strip, /container-type: inline-size/, '缩略图也是独立容器，字按 148px 等比缩')

// —— 主题：DOM 与导出同源 ——
assert.match(themes, /--ppt-bg/, '主题导出成 --ppt-* 变量')
assert.match(themes, /hex: \{/, '同时带一份无 # 的 hex 供 pptxgenjs')
assert.doesNotMatch(themes, /hex: \{[^}]*'#/, 'hex 不能带 #（pptxgenjs 只认 6 位）')
for (const id of ['paper', 'ink', 'celadon', 'cinnabar']) assert.match(themes, new RegExp(`id: '${id}'`), `缺失皮肤 ${id}`)
assert.match(surface, /var\(--ppt-fg\)/, '纸内文字只读层内变量')
assert.match(surface, /color-mix\(in srgb, var\(--ppt-accent\)/, '层次用 color-mix 调，不写死颜色')

// —— 生成：参数贯通 + 逐页生长 + 大纲暂停 ——
assert.match(composable, /createResourceTask\(selectedNode\.value\?\.id \?\? FREE_TOPIC_NODE_ID, 'ppt', params\)/, '自由讲题落到 general 节点')
assert.match(composable, /params\.topic = free\.slice\(0, 60\)/, '自由讲题截断到 60 字')
assert.match(composable, /mode: mode\.value/, '节奏参数贯通')
assert.match(composable, /page_count: pageCount\.value/, '页数参数贯通')
assert.match(resourceApi, /event\.event === 'pause'/, 'pause 是终止事件')
assert.match(composable, /case 'pause'/, '消费暂停事件')
assert.match(composable, /phase\.value = 'awaiting-outline'/, '暂停后进入待确认态')
assert.match(composable, /case 'slide'/, '消费逐页事件')
assert.match(composable, /enqueue\(/, '逐页事件进队列')
assert.match(composable, /SLIDE_INTERVAL_MS/, '放页有节拍，一次全出看不出「一页页翻开」')
assert.match(studioConfig, /SLIDE_INTERVAL_MS = 260/, '节拍常量集中定义')
// 逐页敷演时两页之间隔着一次模型调用，队列空是常态。空队列必须把节拍器复位，
// 否则第 1 页之后 draining 永远为 true：后续页全堆在队列里，done 也收不了卷。
assert.match(composable, /if \(!next\) \{\s*draining = false\s+finishGrowth\(\)/, '队列空要复位 draining，不能让节拍器卡死')
assert.match(composable, /growthDone/, 'done 早于队空时先挂起，放完才收卷')
// 流里的页没有 scene（排版是后一道工序）：收卷时要换成落库的成品页，
// 否则用户看到的一直是旧渲染器那版「01 02 03 + 半句标签」。
assert.match(composable, /pendingResourceId/, '完成时回灌带排版结果的成品页')
assert.match(composable, /apiConfirmOutline/, '确认大纲走独立接口')
// 续跑位置由服务端算（v1 的敷演是第 2 步，v3 是第 3 步）：前端写死下标，加一步就错位。
assert.match(composable, /accepted\.current_step/, '确认后从服务端给的那一步续跑')
assert.match(outlineConfirm, /确认这 \$\{outline\.length\} 页/, '确认按钮说清页数')

// —— 保存：防抖 + 版本冲突双出口 ——
assert.match(composable, /saveDeck\(/, '整份保存走独立接口')
assert.match(composable, /SAVE_DEBOUNCE_MS/, '自动保存有防抖')
assert.match(composable, /phase\.value !== 'editing'/, '生成中不抢后端正在写的行')
assert.match(composable, /status === 409/, '409 单独识别')
assert.match(composable, /reloadFromServer/, '冲突可重新载入')
assert.match(composable, /keepMine/, '冲突可保留本地版本')
assert.match(composable, /version\.value = result\.version/, '保存成功后版本号前移')
assert.match(composable, /sanitizeSlide/, '保存前清洗')
assert.match(composable, /filter\(Boolean\)/, '空要点必须过滤：后端 validator 拒绝空串')
assert.match(view, /saveStatus === 'conflict'/, '冲突提示条渲染在舞台上方')

// —— 导出：真 .pptx，且与舞台同尺寸同配色 ——
assert.match(exportUtil, /LAYOUT_16x9/, '16:9 版面与舞台一致')
assert.match(exportUtil, /pptThemeById/, '颜色取自主题，不另写一份')
assert.match(exportUtil, /1cqw = 0\.1in/, '注释钉住换算关系，避免日后各改一边')
assert.match(exportUtil, /writeFile\(\{ fileName \}\)/, '导出即下载')
assert.match(exportUtil, /addNotes/, '讲者备注写进 notes')
assert.match(exportUtil, /fit: 'shrink'/, '超长文字缩排而不是溢出')
assert.doesNotMatch(exportUtil, /#[0-9a-fA-F]{3,8}\b/, '导出层不出现颜色字面量')
assert.match(toolbar, /PPTX/, '工具条上有导出入口')
assert.match(view, /exportDeckToPptx/, '页面接上导出')

// —— 空/加载/错误三态必须走标准组件 ——
assert.match(view, /StateEmpty/, '空态用标准组件')
assert.match(view, /StateLoading/, '加载态用标准组件')
assert.match(view, /StateError/, '错误态用标准组件')
assert.match(view, /这一炉没炼成/, '失败文案与炼药口径一致')
assert.match(view, /案上还是白纸/, '空态有下一步指引')
assert.match(panel, /maxlength="60"/, '讲题输入限长')
assert.match(panel, /按名字筛选/, '节点可本地筛选（后端无搜索接口）')

// —— 类型层：新资源类型与事件必须同步 ——
assert.match(resourceTypes, /'ppt'/, 'ResourceType 含 ppt')
assert.match(resourceTypes, /'pause'/, '任务事件含 pause')
assert.match(resourceTypes, /'slide'/, '任务事件含 slide')
assert.match(pptTypes, /SLIDE_LIMITS/, '限制常量集中定义')
assert.match(immersive, /ppt: '讲稿幻灯'/, '资源库标签补齐 ppt，缺键会 KeyError')
assert.match(resourcesView, /value: 'ppt'/, '资源库可按幻灯筛选')
assert.match(resourceCard, /resource\.type === 'ppt'/, '卡片有专属图标')
assert.match(detailView, /name: 'ppt-studio'/, '从资源库点开直接进工坊')

// —— 屏幕与导出必须同一条规矩，否则「所见」和「所得」会不一样 ——
const ordinal = read('../src/utils/slideOrdinal.ts')
assert.match(ordinal, /padStart\(2, '0'\)/, '章号是两位数字：单个「一」放大成一横像版式坏了')
assert.match(surface, /sectionOrdinal/, '屏幕用同一份章号换算')
assert.match(exportUtil, /sectionOrdinal/, '导出用同一份章号换算')
assert.match(surface, /kicker\.is-blank/, '眉标留空但占位，换页时标题不跳')
assert.match(surface, /\.blocks\.as-points > \.b-point:only-of-type/, '一两条要点的页放大一档')
// 导出与屏幕必须读同一份「版式 + 分块」，否则屏幕上是对照表、导出的 pptx 是一列要点。
const slideBlocks = read('../src/utils/slideBlocks.ts')
assert.match(slideBlocks, /export const layoutOf/, '版式换算只有一处实现')
assert.match(slideBlocks, /export const blocksOf/, '分块只有一处实现')
assert.match(surface, /layoutOf\(props\.slide\)/, '屏幕读共享换算')
assert.match(exportUtil, /layoutOf\(node\)/, '导出读同一份换算')
assert.match(exportUtil, /blocksOf\(node\)/, '导出按块画，不再只认 bullets')
assert.match(exportUtil, /MARGIN, TYPE_SCALE/, '导出版心与字号抄 frames，不另写一份')
assert.match(exportUtil, /includes\(label\) \? '' : label/, '导出也压掉与标题重复的眉标')
assert.match(composable, /resource\.meta\?\.topic/, '载入旧稿时把讲题填回左栏')

// —— 方案协商：先出方案、确认之后才敷演 ——
assert.match(view, /phase === 'awaiting-plan' && plan/, '只在拿到方案时开面板')
assert.match(view, /@revise="revisePlan"/, '面板可带反馈修订')
assert.match(view, /@confirm="confirmPlan"/, '面板可确认')
assert.match(view, /@cancel="cancelPlan"/, '面板可退出（暂不生成）')
assert.match(composable, /case 'plan':/, '消费方案事件')
assert.match(composable, /await_plan/, '区分「等方案」与旧大纲确认流程')
assert.match(composable, /confirmResourcePlan/, '确认走方案接口，不另开一条路')
assert.match(view, /route\.query\.task/, '刷新回来先认任务，不重开一份')
// 逐页核对只报问题：缺页/重复/空页要说出来，而不是偷偷改。
assert.match(view, /deckCheck\.missing/, '没落地的方案页要报')
assert.match(view, /deckCheck\.duplicates/, '重复标题要报')
assert.match(composable, /deckPages/, '逐页核对结果留在前端')

// —— 设计步：版式由设计单决定，不由页序猜；内容按块排，不按一句话排 ——
const designTypes = read('../src/types/design.ts')
const wireframe = read('../src/components/ppt/WireframePreview.vue')
const planReview = read('../src/components/resources/PlanReview.vue')
const skin = read('../src/render/skin.ts')

assert.match(designTypes, /export interface DeckDesign/, '设计单有前端镜像，两端不分叉')
assert.match(slideBlocks, /slide\.purpose \?\? KIND_TO_LAYOUT/, '新稿按 purpose 排，旧稿按 kind 回落')
assert.match(surface, /data-slide-layout/, '版式落成 DOM 属性，截图基线可断言')
for (const block of ['point', 'steps', 'matrix', 'figures', 'alert', 'quote', 'def']) {
  assert.match(surface, new RegExp(`block\\.type === '${block}'`), `缺内容块 ${block}`)
}
assert.match(surface, /proxy/, '旧字段摊出来的块要能写回 bullets / columns，否则一保存就丢')
assert.match(planReview, /WireframePreview/, '确认单给线框而不是文字描述')
assert.match(planReview, /emit\('confirm', currentPlan\(\)/, '方案与设计同批确认')
assert.match(view, /:design="deckDesign"/, '舞台吃设计单的纸面与角饰')
assert.match(skin, /PAPER_MOTIFS/, '纸面处方 → 母题有映射表（处方是语义，母题是渲染）')
assert.match(surface, /paperSvg/, '底纹由母题现场生成，不靠手工资产')
assert.match(surface, /showFigure/, '图位按选材结果决定，没解析到就不画')
assert.match(composable, /case 'design':/, '消费设计事件')
assert.match(composable, /case 'slot_resolution':/, '消费选材事件')
assert.match(composable, /toSlideNode\(slide\)/, '逐页事件整页映射，新增字段不漏')
// 场景是排版层的产物：映射时丢掉它，每页都会退回旧版式（「所有 PPT 一个样」）。
assert.match(composable, /slide\.scene \?/, '页的场景必须跟着页走')
assert.match(composable, /blocks: slide\.blocks \?\? \[\]/, '保存时结构块原样回传')
assert.match(studioConfig, /id: 'design'/, '离线兜底工序表也含设计步')
assert.match(composable, /remoteSteps/, '工序表以服务端下发为准，前端不再写死步数')

// —— 方案卡：场合 / 页数 / 每页版式 / 每页素材都要能改，且改的是生成会读的那份 ——
{
  const resourceTypes = read('../src/types/resource.ts')

  assert.match(planReview, /这次这样讲/, '讲稿方案卡要有一块说清「这次怎么讲」')
  assert.match(planReview, /deckSpec/, '读施工图，而不是只读三行拼接字')
  assert.match(planReview, /DECK_GENRE_LABELS/, '场合用中文枚举，与后端 GENRE_LABEL 同源')
  assert.match(planReview, /叙事弧/, '叙事弧要摆出来，它是「这次怎么起承转合」')
  assert.match(planReview, /setDeckRole/, '逐页版式可改（版式单一不是命，是没人能改）')
  assert.match(planReview, /setDeckMaterial/, '逐页素材需求可改')
  assert.match(planReview, /page\.archetype = value/, '改版式要同步线框，否则确认的还是旧版式')
  assert.match(planReview, /writeDeckPageCount/, '页数变了「共 N 页」要跟着变，卡片不能自相矛盾')
  assert.match(resourceTypes, /export interface ResourcePlanPage/, '施工图每页有前端镜像')
  assert.match(resourceTypes, /export interface DeckVariety/, '版式多样性的核对结果有镜像')
  assert.match(resourceTypes, /export type DeckGenre/, '场合枚举有镜像')
  // 版式菜单直接取设计单那十种，别在面板里另写一份
  assert.match(planReview, /ARCHETYPE_ORDER\.map/, '版式菜单与线框同一份来源')
  assert.match(view, /varietyLabel/, '稿头常驻「几种版式」，不等到出错才说')
  assert.match(view, /role_variety/, '数字来自后端 deck_conformance，前端不自己数')
}

// —— 视觉方案：长什么样由方案说了算，素材要真的出现在页面上 ——
{
  const surface = read('../src/components/ppt/SlideSurface.vue')
  const sceneSurface = read('../src/components/ppt/SceneSurface.vue')
  const slideRenderer = read('../src/components/ppt/SlideRenderer.vue')

  // 方案卡上有「长什么样」一节，且每一项都改在生成会读的那份 spec.look 上。
  assert.match(planReview, /长什么样/, '方案卡要有一节回答「长什么样」')
  assert.match(planReview, /look-block/, '视觉方案是一块可整体辨认的区域')
  assert.match(planReview, /<option value="none">不挂角花<\/option>/, '「不要角花」是一个能选的答案，不是空缺')
  assert.match(planReview, /touchLook/, '改视觉方案先落 spec.look（补默认再改，不造半份对象）')
  assert.match(planReview, /syncDesignLook/, 'look 要同步进设计单：线框与成品读同一份，改了立即生效')
  assert.match(planReview, /setDeckComposition/, '逐页构图可改，构图决定这一页挂不挂图位')
  assert.match(planReview, /compositionOptions/, '构图菜单与后端 COMPOSITION_LABELS 同源')
  assert.match(planReview, /setDeckBackgroundKind/, '背景可改（纸纹 / 素材库实拍 / 纯色）')
  // —— Phase 3：模型定稿展示。母版名/色板/背景/页型只读摆出来，三个一键动作轮换 ——
  // 以前「素材库实拍」要用户自己填主体（「如「中药房内景」」那个 placeholder）——
  // 那是把选题的活退回给用户。现在主体由模型定稿、只读展示。
  assert.doesNotMatch(planReview, /如「中药房内景」/, '背景主体只读展示，不再让用户填必填输入')
  assert.match(planReview, /look-final/, '模型定稿展示是独立可辨认的区域')
  assert.match(planReview, /masterById|PPT_MASTERS/, '母版信息来自后端生成的 pptMasters.ts，不在前端另写一份')
  assert.match(planReview, /data-look-action="accent"/, '「换配色」一键轮换色板变体')
  assert.match(planReview, /data-look-action="decoration"/, '「换装饰」一键轮换装饰组')
  assert.match(planReview, /data-look-action="master"/, '「整体换一版」一键轮换到下一个同场合母版')
  assert.match(planReview, /cycleMaster/, '换版逻辑有具名函数，走到场合同款母版的下一个')
  assert.match(planReview, /look-advanced/, '原下拉收进「高级调整」折叠，不再铺在定稿展示前面')
  assert.match(planReview, /deckRoleSummary/, '逐页页型摘要摆出来（封面 · 要点×3 · 收束）')
  assert.match(resourceTypes, /export interface DeckLook/, '视觉方案有前端镜像')
  assert.match(resourceTypes, /goal: string/, 'goal 与 thesis 分离：目标栏不是一句演讲词')
  // 图位：远程馆藏素材不落盘，只认 file 会让「库里有图、页上没图」。
  assert.match(surface, /ref\.slot === 'figure' \|\| ref\.slot === 'evidence'/, '图位只认 figure/evidence：ground 排最前，取第一个非 none 会永远拿到纸纹')
  assert.match(surface, /figureRef\.value\?\.url \|\| figureRef\.value\?\.file/, '图片地址 url 优先，远程馆藏才有 url、file 是空串')
  // 配色：设计单的 palette 要一路透到场景渲染，不能停在舞台门口。
  assert.match(sceneSurface, /palette\?: Record<string, string>/, '场景面接受自定义配色')
  assert.match(sceneSurface, /fitTexts/, '文字装不下要在框内缩字号，不能把正文切半句')
  assert.match(slideRenderer, /:palette="design\?\.palette"/, '设计单配色透传给场景面')
  // —— Phase 4 渲染润色：scene 的 additive 字段必须双端消费，不能只进 schema ——
  assert.match(sceneSurface, /shadowFilter/, '阴影（shadow）有屏幕端翻译')
  assert.match(sceneSurface, /frameStyle/, '装裱（hairline/mat）有屏幕端翻译')
  assert.match(sceneSurface, /treatmentFilter/, '图片调性（toned/duotone）有屏幕端翻译')
  assert.match(sceneSurface, /linear-gradient/, 'rect 双色渐变（fill2）上屏')
  assert.match(exportUtil, /element\.frame === 'mat'/, 'mat 装裱在 pptx 里有对应翻译（衬板 + 嵌图）')
  assert.match(exportUtil, /element\.frame === 'hairline'/, 'hairline 装裱在 pptx 里有对应翻译')
  assert.match(exportUtil, /shadowOf/, '形状阴影在 pptx 里有对应翻译')
  assert.match(exportUtil, /element\.fill2/, '双色渐变在 pptx 里取中点实色（pptxgenjs 无形状渐变）')
  assert.match(exportUtil, /FONT_NOTICE/, '导出文件的头注释要说明「不嵌字体，缺字回落系统宋楷」')
  assert.match(read('../src/render/scene.ts'), /shadow: number/, '前端镜像带 shadow 字段（additive）')
}

// —— 字体链：webfont 要真的注册、栈要真的指到注册名、后端 id 要同源 ——
{
  const fonts = read('../src/config/pptFonts.ts')
  const fontLoader = read('../src/utils/pptFonts.ts')
  const backendPairs = read('../../backend/app/ppt/fontpairs.py')
  const fontCss = read('../public/fonts/ppt-fonts.css')
  const studio = read('../src/views/PptStudioView.vue')
  const lab = read('../src/views/PptDeckLab.vue')

  // 三族 webfont 必须真的注册过（曾经栈里写 Noto Serif SC 而 @font-face 注册的是
  // QH Serif SC，模型选什么字都静默回落系统宋楷）。
  for (const family of ['Noto Serif SC', 'Noto Sans SC', 'LXGW WenKai']) {
    assert.ok(
      fontCss.includes(`font-family: '${family}'`) || fontCss.includes(`font-family: "${family}"`),
      `ppt-fonts.css 必须注册 ${family}`,
    )
    assert.ok(fonts.includes(`"${family}"`), `字体栈必须引用 ${family}`)
  }
  // 分片是按需加载的前提：一份 css 必须切成大量 unicode-range 片。
  const faceCount = (fontCss.match(/@font-face/g) || []).length
  assert.ok(faceCount > 500, `分片 webfont 应有数百条 @font-face，实际 ${faceCount}`)

  // 后端五对与前端五对逐 id 对表（单一事实源的双侧断言）。
  const backendIds = [...backendPairs.matchAll(/"([a-z-]+)":/g)].map((m) => m[1]).filter((id) =>
    ['song-kai', 'kai-song', 'hei-song', 'serif-full', 'sans-full'].includes(id),
  )
  for (const id of ['song-kai', 'kai-song', 'hei-song', 'serif-full', 'sans-full']) {
    assert.ok(fonts.includes(`id: '${id}'`), `前端必须有字对 ${id}`)
    assert.ok(backendIds.includes(id), `后端 fontpairs 必须有 ${id}`)
  }
  assert.ok(!fonts.includes('serif-song'), '前端不再有旧 id serif-song')
  assert.ok(!planReview.includes('value="serif-song"'), '面板下拉不再给旧 id')

  // 渲染幻灯的两个视图都要装载字体；loader 必须幂等（路由往返不堆积 link）。
  assert.ok(studio.includes('ensurePptFonts()'), '工坊视图挂载时装载 webfont')
  assert.ok(lab.includes('ensurePptFonts()'), 'lab 视图挂载时装载 webfont')
  assert.match(fontLoader, /getElementById\(LINK_ID\)/, '字体 link 幂等：重复调用不堆积')
}

console.log('ppt studio component contract checks passed')
