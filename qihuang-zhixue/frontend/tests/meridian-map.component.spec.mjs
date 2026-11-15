import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = (path) => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')

const store = read('stores/path.ts')
const scene = read('components/meridian/MeridianScene.vue')
const runtime = read('components/meridian/useMeridianScene.ts')
const curves = read('components/meridian/MeridianCurves.vue')
const nodes = read('components/meridian/AcupointNodes.vue')
const labels = read('components/meridian/AcupointLabels.vue')
const body = read('components/meridian/BodyMesh.vue')
const neutral = read('components/meridian/neutralize.ts')
const sheet = read('components/meridian/AcupointSheet.vue')
const mirror = read('components/meridian/A11yMirror.vue')
const presence = read('components/meridian/meridianPresence.ts')
const tickBand = read('components/meridian/TickBand.vue')
const config = read('config/meridian-atlas.json')

// ── 路线语义不变：七个状态与三条映射仍然是契约 ─────────────────────────────
for (const status of ['recommended', 'current', 'mastered', 'free', 'deferred', 'exam_hot', 'custom']) {
  assert.match(store, new RegExp(`'${status}'`))
}
assert.match(store, /state === 'completed'.*'mastered'/)
assert.match(store, /state === 'paused'.*'deferred'/)

// ── 渲染层新增 7 个 presence，其中 split（一穴多节、只学了一半）必须有 ──────
for (const item of ['planned', 'active', 'current', 'lit', 'deferred', 'split', 'dormant']) {
  assert.match(presence, new RegExp(`'${item}'`), `presence 缺 ${item}`)
}
assert.doesNotMatch(presence, /completed:\s*'lit'.*locked/s, 'presence 不该直接等于后端状态')

// ── 顺序调整仍然落库，但不再挂在地图上 ────────────────────────────────────
// 地图上那排"章级芯片 + ↑↓"已经被路线条替掉了：一章现在会拆成 1–7 步，
// 按 domain_id 调序会把这一章的多个节压成一行。顺序在"定取舍"里定
// （草稿 op `reorder`，按行键），地图只负责展示已确认的顺序。
assert.match(store, /reorderPath/)
assert.doesNotMatch(scene, /path\.reorder\(/, '地图不该再按章调序')

// ── 路线条要回答三件事：当前第一步 / 顺序 / 之后去哪 ──────────────────────
{
  assert.match(scene, /path\.current\?\.plan\.progress/, '进度只能来自后端（单元完成度按点集算，前端数不出边界）')
  assert.match(scene, /第 \$\{currentStep\.seq\} 步/, '必须写出"第几步"')
  assert.match(scene, /从这一步开始/, '必须指出当前第一步')
  assert.match(scene, /<em>之后<\/em>/, '必须指出学完这一步之后去哪')
  assert.match(scene, /class="strip-ruler"/, '顺序要有一条可视的顺序尺')
  assert.match(scene, /aria-label="学习顺序"/)
  // 章的边界留缺口：同一章的各节才连得成一段路，而不是一堆孤立格子
  assert.match(scene, /is-chapter-start/)
  assert.match(scene, /scrollLeft = Math\.max\(0, tick\.offsetLeft/, '当前步必须自动滚到尺中间')
  // 点一步要落到"这一节第一个还没学的点"，不是拿单元 id 去跳
  assert.match(scene, /ids\.find\(\(id\) => states\.value\.get\(id\) !== 'completed'\)/)
  assert.match(scene, /selectByNode\(target\)/)
}

// ── 通经：3D 用 CatmullRomCurve3，不再有 GSAP 的 SVG motionPath ────────────
assert.match(curves, /CatmullRomCurve3/)
assert.doesNotMatch(curves, /motionPath/)
assert.doesNotMatch(scene, /motionPath/)
// 未学段是"线本身"的一种状态，不再靠 CSS 的 is-foggy
assert.match(curves, /is-unlearned/)
assert.doesNotMatch(scene, /is-foggy/)

// ── 流光按弧长推进（不同经长度差 6 倍，按 t 会让长经的光球飞） ─────────────
assert.match(curves, /flowLength/)
assert.match(config, /"flow_samples": 12/)

// ── Three 对象绝不能进 Vue 响应式代理（矩阵会被 getter 打断，表现为闪烁） ────
assert.match(scene, /shallowRef/)

// ── 性能红线：670 个光点必须实例化 ────────────────────────────────────────
assert.match(nodes, /new THREE\.InstancedMesh/)
assert.match(nodes, /bucketOf/)

// ── 标签：CSS2D + 屏幕空间避让 ────────────────────────────────────────────
assert.match(labels, /CSS2DRenderer/)
assert.match(labels, /avoidOverlap/)

// ── 无障碍：3D 画布 aria-hidden，语义走镜像列表 ───────────────────────────
assert.match(mirror, /role="list"/)
assert.match(mirror, /aria-live/)
assert.match(runtime, /aria-hidden/)
assert.match(runtime, /prefers-reduced-motion/)
assert.match(curves, /reducedMotion/)

// ── 资源释放：不写 dispose 必然泄漏 ───────────────────────────────────────
assert.match(runtime, /dispose/)
assert.match(scene, /runtime\.value\?\.dispose\(\)/)

// ── 资产与相机参数外置，改机位不用改代码 ──────────────────────────────────
assert.match(config, /"anatomy": \{/)
assert.match(config, /"camera": \{/)
assert.match(config, /"views": \{/)

// ── P10 只剩体表：肌肉层是 7.5 mm 体素简化的产物，薄肌腹被磨穿、肌群间无筋膜填充，
//    1.25 m 章节视距下就是一块块断开的红肉。缺的是几何不是材质，补面补不回来。
assert.match(body, /mergeGeometries/)
assert.doesNotMatch(body, /vertexColors/, '肌肉层下架后不该再有顶点色的分块肌理')
assert.doesNotMatch(body, /aPart/)
assert.doesNotMatch(body, /materialMode/)
assert.doesNotMatch(body, /transmission/)
assert.doesNotMatch(config, /"muscle"/)
assert.doesNotMatch(config, /"skeleton"/)

// ── P10 下体中性化：把窗口内的体表换成中性曲面，不是"平滑一下"，也不是"加条短裤"。
//    遮挡会盖住任脉下腹三穴（曲骨 0.879 / 中极 0.917 / 关元 0.947）；
//    拉普拉斯是扩散算子，只能把突起摊成更矮更宽的突起（实测 z 峰值 0.106→0.093 m）。
assert.match(neutral, /sanitizeNormals/)
assert.match(neutral, /neutralSurface/)
assert.match(neutral, /relaxToMembrane/)
assert.match(neutral, /reproject/)
assert.match(neutral, /onEdge/, '边界环必须固定，否则膜绷不住、整圈臀股一起缩')
assert.doesNotMatch(neutral, /ring_falloff|strength \* weight/, '拉普拉斯那套参数已经废掉')
assert.match(body, /neutralSurface\(asset, props\.acupoints\)/)
// 法线是 i16 + normalized：回写必须乘 32767 取整，否则截断成 0 → NaN → bloom 摊满整屏
assert.match(neutral, /Math\.round\(\(sum\.x \/ len\) \* 32767\)/)
// 穴位必须跟着中性化后的体表走，否则会浮在抹平的皮外（会阴实测偏 9.5 mm）
assert.match(neutral, /item\.skin_tri/)
assert.match(nodes, /props\.offsets\?\.get\(point\.id\)/)
assert.match(scene, /@surface="surfaceOffsets = \$event"/)
assert.match(scene, /:offsets="surfaceOffsets"/)
{
  const atlas = JSON.parse(config)
  const nz = atlas.anatomy.neutral_zone
  assert.ok(nz.enabled)
  assert.ok(nz.iterations >= 60, '实测 60 次已收敛到 0.55 mm，再少会留着突起')
  assert.ok(typeof nz.relax === 'number')
  assert.ok(nz.box.y_max < 0.879, '窗口必须停在曲骨（0.879）以下，不许动到任脉三穴')
  assert.ok(nz.box.x <= 0.06, '窗口横向放宽会连着腹股沟一起抹平')
  assert.ok(nz.box.z_min > 0, '只处理体表前侧，别把会阴后方一起拉平')
}

// ── 全身不画刻度（决策 25）：刻度带只在 chapter 态，逐点刻度只在 acupoint 态 ─
assert.match(scene, /v-if="view === 'chapter' && activeAcupoint"/)
assert.match(scene, /<TickBand/)
assert.match(scene, /v-if="view === 'acupoint' && activeSection"/)
assert.match(scene, /<TickPoint/)
assert.doesNotMatch(tickBand, /avoidOverlap/, '刻度带是 2D HUD，不该拿 3D 的避让逻辑')

// ── 精度可见（决策 31）：362 穴全未校核，界面必须说出来 ────────────────────
assert.match(sheet, /verified/)
assert.match(sheet, /community/)

// ── 穴位点亮要有回弹，但不是每帧 tween（每帧 tween 是 3D 掉帧的典型来源） ──
assert.match(nodes, /back\.out/)

// ── 零长度法线一个都不能进渲染管线 ────────────────────────────────────────
// (0,0,0) 法线会让 MeshStandardMaterial 的 normalize 得到 NaN；裸渲染只是那几个
// 面黑掉，接上 bloom 后 NaN 会顺着半浮点模糊链加性摊满整帧——整屏透明。
// 打包端修根（vertex_normals），前端再兜一层，着色器里还要防扰动后归一化。
assert.match(neutral, /sanitizeNormals/)
assert.match(body, /pl > 1e-5 \? perturbed \/ pl : normal/)

// ── 后期链必须是可降级的三级梯子，且**用真渲染自检** ──────────────────────
// 半浮点链在部分 GL 上会静默失败（checkFramebufferStatus 报 complete、
// getError 返回 0、console 无报错，但帧写不出来），只能读了像素才知道。
assert.match(runtime, /canvasHasContent/)
assert.match(runtime, /readPixels/)
assert.match(runtime, /'half-float' \| 'rgba8' \| 'off'/)
assert.match(runtime, /THREE\.UnsignedByteType/)
assert.match(runtime, /if \(composer\) composer\.render\(\)\s*\n\s*else renderer\.render\(scene, camera\)/)
// 空场景自检不够：人还没加载进来时半浮点链是正常的，真内容渲完要再验一次
assert.match(runtime, /verifiedWithContent/)
assert.match(runtime, /info\.render\.calls > 4/)

// ── 舞台恒定深色：主题前景色不许直接用（浅色主题下深灰字压在深色画布上隐形） ──
assert.match(scene, /--qh-color-night: #061012/)
assert.match(scene, /--qh-color-night-soft: #0a1a1d/)
assert.match(scene, /--stage-panel/)

// ── 全身态不挂穴名（362 个穴名在 3m 视距下必然在头面糊成一团） ──────────────
assert.match(scene, /const labelsVisible = computed\(\(\) => showLabels\.value && view\.value !== 'overview'\)/)
assert.match(scene, /:visible="labelsVisible"/)

// ── HUD 不许互相压：底部条要给键盘入口留位，右侧面板要让开学科胶囊 ──────────
assert.match(scene, /right: 190px/)
assert.match(scene, /top: 112px/)

// ── 三级 LOD 的机位必须拉得开：视高 = 2·d·tan(fov/2) ─────────────────────
// 原来 0.62 / 0.34 的视高是 0.33 m 与 0.14 m，屏幕上只剩一块肌腹——
// 看不出"这一段是胸前还是后背"，也看不出旁边还有云门。
{
  const atlas = JSON.parse(config)
  const { distance, fov } = atlas.camera
  const heightOf = (key) => 2 * distance[key] * Math.tan((fov[key] * Math.PI) / 360)
  assert.ok(heightOf('overview') > 1.6, '全身态视高装不下一个人')
  assert.ok(heightOf('chapter') > 0.5, `章节态视高只有 ${heightOf('chapter').toFixed(2)} m，认不出部位`)
  assert.ok(heightOf('acupoint') > 0.2, `穴位态视高只有 ${heightOf('acupoint').toFixed(2)} m，看不出相邻穴`)
  assert.ok(distance.chapter > distance.acupoint, '章节态必须比穴位态远')
}

// ── P6 流光只走已通段 ──────────────────────────────────────────────────────
// 以前取"第一个已亮穴 → 最后一个已亮穴"的整段，中间没学的穴位也会被光球穿过，
// 看起来像"这段通了"。必须复用 buildLearned 的连续段（相邻两穴都亮才算一段）。
{
  const tick = read('components/meridian/TickPoint.vue')
  assert.match(curves, /learnedRuns/, '流光必须复用已学连续段')
  assert.match(curves, /const runs = learnedRuns/, '流光不许再自己算首尾')
  // 刻度可逐个点中：点了要换选中项，并且把位置传出去同步地址栏
  assert.match(scene, /@open="selectTick"/)
  assert.match(scene, /\(event: 'focus-node', nodeId: string\): void/)
  assert.match(scene, /const selectTick = \(nodeId: string\) =>/)
  assert.match(tick, /emit\('open', tick\.id\)/)
  // 点刻度 → 父级同步 query → watch 把 focus 传回来，没有这道闸门就是"点一下抖两下"
  assert.match(scene, /if \(activeTickId\.value === nodeId && view\.value === 'acupoint'\) return/)
  // 深链落地必须**选中那一条刻度**。`selectAcupoint` 会把 activeTickId 清成 null，
  // 于是 `/path?focus=d01.s1.p1` 只飞到穴位、右侧 16 条刻度谁也看不出该点哪一条。
  const byNode = scene.slice(scene.indexOf('const selectByNode = (nodeId: string) => {'))
  const body = byNode.slice(0, byNode.indexOf('\ndefineExpose'))
  assert.doesNotMatch(body, /selectAcupoint\(/, 'selectByNode 不许调 selectAcupoint（它会清掉选中刻度）')
  assert.match(body, /selectedId\.value = section\.acupoint_id/)
  assert.match(body, /activeTickId\.value = nodeId/)
  assert.match(body, /view\.value = 'acupoint'/)
}

// ── P8 位置写回地址栏，用 replace 不用 push ────────────────────────────────
// push 会让后退键变成"退到上一条知识点"，退十几下才出得去。
{
  const view = read('views/MeridianMapView.vue')
  assert.match(view, /@focus-node="syncFocus"/)
  assert.match(view, /router\.replace\(\{ query: \{ \.\.\.route\.query, focus: nodeId \} \}\)/)
  assert.doesNotMatch(view, /router\.push\(\{ query: \{ \.\.\.route\.query, focus:/)
}

// ── 路线条芯片必须是中文名：layout.nodes 只有 segment，标签在 directions 里 ──
assert.match(store, /layout\.directions\.find\(\(item\) => item\.id === id\)\?\.label/)

// ── 穴位语义统一：一个穴位 = 一个节/小节，名字里永远不带章名 ────────────────
{
  // 刻度带表头只写章，不再拼篇名——"总论 第1章"会被读成一个叫"总论"的章
  assert.doesNotMatch(scene, /\$\{first\.part \?/, '章标签不许再拼 part')
  assert.match(scene, /const numbered = first\.book_chapter_no !== 0/, '教材不编号的章（绪论/总论）不能写成"第0章"')
  assert.match(scene, /first\.chapter_title/)
  // 刻度带每一行只写节名；该章教材本来不分节时，那一行才是章名
  assert.match(tickBand, /item\.section\.title/)
  assert.match(tickBand, /item\.section\.virtual/)
  // 步骤条上的小字是"学科 · 知识点数 · 时长"，不含章名
  const stepMetaBody = scene.slice(scene.indexOf('const stepMeta'), scene.indexOf('const tickTitle'))
  assert.doesNotMatch(stepMetaBody, /chapter_title/, '节名旁边不许再跟章名')
}

console.log('meridian-map component contract checks passed')
