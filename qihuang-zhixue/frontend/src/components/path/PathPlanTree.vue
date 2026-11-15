<template>
  <div class="plan-tree">
    <div ref="host" class="tree-host" />
    <p class="tree-legend">
      点一个<b>节</b>就去掉它，再点一下加回来；点<b>学科</b>或<b>章名</b>展开、收起。
      滚轮平移，<b>Ctrl+滚轮</b>放大。
      <span class="tree-count">{{ includedCount }} / {{ totalCount }} 节在路线里</span>
    </p>
  </div>
</template>

<script setup lang="ts">
/**
 * 「定取舍」那一步的思维导图：学科 → 章 → 节，点一下就砍掉或加回。
 *
 * **为什么用导图而不是清单**：取舍要看的是"这一章里还留着哪几节"。
 * 一张 291 行的列表把这个信息摊平成 291 个同等权重的条目——用户既看不出层级，
 * 也看不出自己砍掉的到底是哪一块。导图上一章一个小分支，砍掉的节变灰留在原位，
 * 结构关系一眼可见。
 *
 * **为什么不复用 `StudioCanvas`**：那个是工坊的画布（生长队列、脏标记、保存协议），
 * 这里只要"读一棵树 + 点一下回调"。硬套会为了一个回调拖进整套状态机。
 * 但渲染内核仍是同一个 `simple-mind-map`，转换也只走 `utils/mindmapTree` 的约定
 * （wrapper 树 + `text` 字段）——两条都踩错会得到一屏没有文字的空节点。
 *
 * **删除不是真删**：点掉一节是发一个 `remove` 草稿操作，后端那一行仍在
 * （`status='draft'`），所以点回来是精确还原，不会丢内容、不会改写进度。
 */
import MindMap from 'simple-mind-map'
import 'simple-mind-map/dist/simpleMindMap.esm.css'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { buildSimpleTheme, getThemePreset, tagsColorMap } from '../../config/mindmapThemes'
import { getStructure } from '../../config/mindmapStructures'
import type { DraftExcludedRow, DraftStageRow } from '../../types/path'

const props = withDefaults(
  defineProps<{
    stages: DraftStageRow[]
    excluded: DraftExcludedRow[]
    busy?: boolean
    themeId?: string
    structureId?: string
  }>(),
  { busy: false, themeId: 'indigo', structureId: 'logic-right' },
)

const emit = defineEmits<{
  (event: 'toggle', payload: { stageId: string; domainId: string; include: boolean }): void
}>()

const host = ref<HTMLElement | null>(null)
let mm: any = null
/** 用户展开过的章/学科。重建数据时要还原，否则点一下去一节，整棵树又收回去。 */
const expanded = new Set<string>()
/**
 * 用户**主动收起**过的节点。
 *
 * 与 `expanded` 分开记：一门课时会默认展开（见 ``buildTree`` 末尾），
 * 用户点了收起之后如果不留痕，下一次重建又会自己弹开——点了没反应，最招人烦。
 */
const collapsed = new Set<string>()

const includedCount = computed(() => props.stages.length)
const totalCount = computed(() => props.stages.length + props.excluded.length)

interface SimpleNode {
  data: { id: string; text: string; expand?: boolean; meta?: Record<string, unknown>; fontColor?: string }
  children: SimpleNode[]
}

const unitKey = (row: DraftStageRow | DraftExcludedRow) => String(row.stage_id || row.domain_id)
const subjectKey = (subjectId: string) => `subject:${subjectId || 'other'}`
const chapterKey = (subjectId: string, chapter: string) => `chapter:${subjectId || 'other'}:${chapter || '未分章'}`

/**
 * 把扁平的行折成 学科 → 章 → 节。
 *
 * 取舍行与被砍掉的行**放在同一棵树里**：砍掉的节留原位、变灰、标"不学"。
 * 只画留下的那些，用户就看不到自己砍掉了什么，也就没法判断该不该加回来。
 * 折法是"相邻同类归组"而不是按 id 归类——顺序本身就是安排表的内容。
 */
const buildTree = (): SimpleNode => {
  const root: SimpleNode = { data: { id: 'root', text: '我的路线', expand: true }, children: [] }
  const subjects = new Map<string, { node: SimpleNode; chapters: Map<string, SimpleNode> }>()

  const ensureSubject = (subjectId: string, name: string) => {
    const key = subjectKey(subjectId)
    let entry = subjects.get(key)
    if (!entry) {
      const node: SimpleNode = {
        data: {
          id: key,
          text: name || '其他',
          // **默认全收起**。四门课全展开 = 96 个章竖成一列，一屏装不下，
          // 只能缩到 20% 上下，字是一条线；只展开一门又会让另外三门藏在屏外，
          // 第一眼看不出"一共几门课"。收起时节点上带着"里面有多少节"的数字，
          // 一句话就能说清全貌，用户想看哪门再点开。
          expand: expanded.has(key),
          meta: { kind: 'subject' },
        },
        children: [],
      }
      root.children.push(node)
      entry = { node, chapters: new Map() }
      subjects.set(key, entry)
    }
    return entry
  }

  const ensureChapter = (subjectId: string, chapter: string, name: string) => {
    const entry = ensureSubject(subjectId, name)
    const key = chapterKey(subjectId, chapter)
    let node = entry.chapters.get(key)
    if (!node) {
      node = {
        data: {
          id: key,
          text: chapter || '未分章',
          // 默认收起：28 章全展开会把一屏挤满 200 多个节，反而看不见结构
          expand: expanded.has(key),
          meta: { kind: 'chapter' },
        },
        children: [],
      }
      entry.node.children.push(node)
      entry.chapters.set(key, node)
    }
    return node
  }

  for (const row of props.stages) {
    const chapter = ensureChapter(row.subject_id || '', row.chapter_title || '', row.subject_name || '')
    chapter.children.push({
      data: {
        id: `unit:${unitKey(row)}`,
        text: row.unit_title || row.name,
        meta: { kind: 'unit', stage_id: unitKey(row), domain_id: row.domain_id, included: true },
      },
      children: [],
    })
  }
  for (const row of props.excluded) {
    const chapter = ensureChapter(row.subject_id || '', row.chapter_title || '', row.subject_name || '')
    chapter.children.push({
      data: {
        id: `unit:${unitKey(row)}`,
        // 灰字 + 后缀，而不是只靠颜色：打印和截图里颜色会丢
        text: `${row.unit_title || row.name}（不学）`,
        fontColor: 'var(--qh-text-muted, #98a2b3)',
        meta: { kind: 'unit', stage_id: unitKey(row), domain_id: row.domain_id, included: false },
      },
      children: [],
    })
  }
  // 只有一门课时自动展开它。
  //
  // 范围生效后（"就想考中药学期末" → 只排中药学）树里只剩一个学科节点：全收起
  // 时屏幕上是一个"中药学 65"的圆角框，用户要砍的是某一节，却连章都看不见，
  // 只能凭"再点两下试试"。一门课没有"看不清全貌"的问题，直接摊开章。
  // 两门以上仍全收起：六个学科 × 二十几章展开 = 一屏放不下，字缩成一条线。
  if (root.children.length === 1) {
    const only = root.children[0]
    if (!collapsed.has(only.data.id)) only.data.expand = true
  }
  return root
}

const structure = computed(() => getStructure(props.structureId))

const buildOption = (data: SimpleNode) => ({
  el: host.value,
  data,
  layout: structure.value.layout,
  themeConfig: buildSimpleTheme(getThemePreset(props.themeId), structure.value),
  readonly: true,
  tagsColorMap,
  // 折叠时显示"还有几个"：光一个三角看不出这里藏了多少节
  isShowExpandNum: true,
  // 滚轮平移、Ctrl+滚轮缩放：293 个节点一屏装不下，平移是高频动作，
  // 而"滚一下缩一下"会让人在 20%–300% 之间来回找刚才看的那个节点。
  mousewheelAction: 'move',
  minZoomRatio: 20,
  maxZoomRatio: 300,
  scaleRatio: 0.12,
  enableDblclickBackToRootNode: false,
  fitPadding: 24,
})

const onNodeClick = (node: any) => {
  if (props.busy) return
  const data = node?.nodeData?.data ?? node?.getData?.() ?? {}
  const meta = (data.meta ?? {}) as Record<string, unknown>
  if (meta.kind === 'unit') {
    emit('toggle', {
      stageId: String(meta.stage_id ?? ''),
      domainId: String(meta.domain_id ?? ''),
      include: meta.included !== true,
    })
    return
  }
  const id = String(data.id ?? '')
  if (!id.startsWith('chapter:') && !id.startsWith('subject:')) return
  // 展开状态自己记：重建数据时要还原，不然每次砍一节，树就全收回去。
  const next = !(node.getData?.('expand') ?? false)
  if (next) {
    expanded.add(id)
    collapsed.delete(id)
  } else {
    expanded.delete(id)
    collapsed.add(id)
  }
  mm?.execCommand?.('SET_NODE_EXPAND', node, next)
}

/**
 * 首屏怎么摆：**交给 `fit()`，只做这一件事**。
 *
 * 试过的三条路都走不通，记在这里免得下次再试：
 *   · `view.reset()` —— 把根节点摆到画面正中。根在整棵树最左边，于是左半屏空着、
 *     右半屏被切掉，第一眼像"图只剩一门课"。
 *   · 自己按 `draw.rbox()` 算比例与平移 —— `rbox()` 量到的**不是内容包围盒**
 *     （实测内容 447 宽、根节点在宿主内 26px，rbox 却报 465），拿它算 translate
 *     会把图推出画布；`setScale()` 之后同一拍再量还是旧坐标，得等一帧。两次掉进
 *     同一类坑后就不再手工摆机位了。
 *   · `setScale()` 单位是**倍数**不是百分比（传 50 = 50 倍），这个坑踩过一次。
 *
 * `fit()` 保证整棵树在框内、留 padding、居中——缩到多小由内容决定。二十几章时
 * 字确实会小，用户 Ctrl+滚轮放大、滚轮平移即可（`mousewheelAction: 'move'` 已配）。
 * 「默认只展开需要看的那一层」才是控制规模的正道，不是事后硬改镜头。
 */
const frame = () => {
  const view = mm?.view
  if (!view) return
  view.fit?.()
  // 只 fit，**不在这里改 scale / translate**。
  //
  // fit 之后 `setScale(1)` 呢？实测内容是整体往右下扩散：根节点掉到画布底边、
  // 右半列章名出界——缩放基准点不在左上，引擎内部那套 translate 只对"它自己算的
  // 比例"成立。改一处就得连 translate 一起重算，而 `draw.rbox()` 量到的又不是
  // 内容包围盒（实测内容 447 宽、根在宿主内 26px，rbox 报 465）。三次都栽在
  // 同一个坐标系上，就不再手工摆机位了。
  //
  // 代价是二十几章时会缩到 0.5 上下、字偏小；图例里明写 Ctrl+滚轮放大。
  // 要真正解决规模问题得少显示一层节点，那是数据的事，不是镜头的事。
}

const render = () => {
  if (!mm) return
  mm.setData(buildTree() as any)
  // 布局是异步算的；这一拍 fit 会把镜头对到上一版的包围盒上。
  window.setTimeout(frame, 120)
}

onMounted(() => {
  if (!host.value) return
  mm = new MindMap(buildOption(buildTree()) as any)
  mm.on('node_click', onNodeClick)
  window.setTimeout(frame, 260)
})

watch(
  () => [props.stages, props.excluded],
  () => render(),
)

onBeforeUnmount(() => {
  try {
    mm?.destroy?.()
  } catch {
    /* 卸载时库可能已自毁 */
  }
  mm = null
})
</script>

<style scoped lang="scss">
.plan-tree {
  display: grid;
  gap: 6px;
}

.tree-host {
  /* 一门课 25 章竖排约 900px，520px 的窗口 fit 后要缩到 0.55 上下——字偏小但
     结构看得全。再高会顶掉下面的汇总与按钮；要放大看就用 Ctrl+滚轮。 */
  height: min(58vh, 560px);
  border: 1px solid var(--qh-line);
  border-radius: var(--qh-radius-sm);
  background: color-mix(in srgb, var(--qh-surface) 70%, transparent);
  overflow: hidden;
}

.tree-legend {
  margin: 0;
  color: var(--qh-text-muted);
  font-size: 11.5px;
  line-height: 1.6;

  b {
    color: var(--qh-text);
    font-weight: 600;
  }
}

.tree-count {
  margin-left: 6px;
  color: var(--qh-color-jade);
}
</style>
