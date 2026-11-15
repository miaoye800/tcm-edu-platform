<template>
  <main class="doc-page" :class="{ 'is-wide': fullWidth }" data-qh-theme="light" :style="documentStyle">
    <header class="doc-toolbar">
      <button title="返回资源库" @click="router.push({ name: 'resources' })">←</button>
      <span class="toolbar-title">{{ kindLabel }}</span>
      <div class="toolbar-right">
        <span v-if="editable" class="save-state" :class="`is-${status}`">{{ saveLabel }}</span>
        <button title="撤销 (Ctrl+Z)" :disabled="!canUndo || !editable" @click="undo">↶</button>
        <button title="重做 (Ctrl+Shift+Z)" :disabled="!canRedo || !editable" @click="redo">↷</button>
        <button :class="{ active: fullWidth }" title="全宽显示" @click="setMeta({ fullWidth: !fullWidth })">⤢</button>
        <button title="导出 Markdown" :disabled="!resource" @click="exportMarkdown">⇩</button>
        <button :class="{ active: resource?.favorited }" title="收藏" :disabled="!resource" @click="favorite">☆</button>
        <button title="重炼一份" :disabled="!resource || reforging" @click="reforge">↻</button>
      </div>
    </header>

    <section class="document-shell">
      <article class="document" @click="onDocumentClick">
        <DocPageHeader
          :title="resource?.title || '正在整理讲解'"
          :icon="String(meta.icon || '')"
          :cover="String(meta.cover || '')"
          :created-at="createdAt"
          :breadcrumb="breadcrumb"
          :kind-label="kindLabel"
          :word-count="wordCount"
          :citation-count="citationTotal"
          @update-icon="value => setMeta({ icon: value })"
          @add-cover="setMeta({ cover: nextCover() })"
          @remove-cover="setMeta({ cover: '' })"
        />

        <p v-if="streaming" class="generation-state">正逐段入卷</p>

        <div v-if="status === 'conflict'" class="conflict-banner">
          {{ errorMessage }}
          <button type="button" @click="reload">重新加载</button>
        </div>

        <div class="block-list">
          <section
            v-for="(block, index) in blocks"
            :id="isHeading(block.type) ? headingId(block.id) : undefined"
            :key="block.id"
            class="block"
            :class="[`block-${block.type}`, { 'is-revealing': animated.has(index) }]"
            :style="{ animationDelay: `${Math.min(index, 6) * stage4Visual.document.blockStaggerMs}ms` }"
            @animationend="finishReveal(index)"
            @dragover="onDragOver($event, block)"
            @drop.prevent="onDrop"
          >
            <DocBlockView
              :block="block"
              :index="listOrdinal(index)"
              :editable="editable"
              :selected="selectedId === block.id"
              :dragging="dragId === block.id"
              :drop-hint="dropTarget && dropTarget.id === block.id ? dropTarget.hint : null"
              @update="onUpdate"
              @enter="payload => onEnter(block.id, payload)"
              @backspace-empty="onBackspaceEmpty(block.id)"
              @select="id => (selectedId = id)"
              @menu="openMenu"
              @slash="openSlash"
              @insert-after="insertAfter"
              @drag-start="onDragStart"
              @drag-end="onDragEnd"
            />
            <!-- 计划快照：每个知识点都能回到地图上那个穴位。只对带 node_id 的块出现。 -->
            <button v-if="mapNodeOf(block)" class="block-map-jump" type="button" @click="openOnMap(block)">
              在地图上看
            </button>
          </section>

          <button v-if="editable" class="block-tail" type="button" @click="appendBlock">+ 继续输入</button>
        </div>

        <details v-if="resource?.notes" class="alchemy-notes">
          <summary>炼丹手记</summary>
          <div><i class="pin" /><i class="seal">录</i>{{ resource.notes }}</div>
        </details>

        <footer class="doc-footer">
          <span>{{ kindLabel }}</span>
          <span>{{ createdAt }}</span>
          <span v-if="wordCount">{{ wordCount }} 字</span>
          <span v-if="citationTotal">{{ citationTotal }} 处引文</span>
        </footer>
      </article>

      <DocToc :items="toc" :active-id="activeId" @jump="scrollTo" />
    </section>

    <nav class="study-actions" aria-label="学完这篇">
      <span>学完这篇</span>
      <button @click="router.push({ name: 'quiz' })">去练一练</button>
      <button @click="router.push({ name: 'atlas' })">看图谱</button>
      <button @click="router.push({ name: 'path' })">回脉络图</button>
    </nav>

    <Transition name="citation">
      <aside v-if="selectedSource" class="citation-panel">
        <button title="关闭引用" @click="selectedSource = null">×</button>
        <p>出处</p>
        <h2>{{ sourceValue(selectedSource, ['出处', 'source', 'title']) || '未标注出处' }}</h2>
        <p>原文摘录</p>
        <blockquote><small>教材摘录</small>{{ sourceValue(selectedSource, ['原文摘录', 'quote']) || '暂无原文摘录' }}</blockquote>
        <p>定位</p>
        <strong>{{ sourceValue(selectedSource, ['定位', 'ref']) || '暂无定位信息' }}</strong>
      </aside>
    </Transition>

    <DocBlockMenu
      :open="menu.open"
      :x="menu.x"
      :y="menu.y"
      :block="menuBlock"
      :can-move-up="menuIndex > 0"
      :can-move-down="menuIndex >= 0 && menuIndex < blocks.length - 1"
      @close="menu.open = false"
      @turn-into="onTurnInto"
      @duplicate="onDuplicate"
      @remove="onRemove"
      @move-up="onMove(-1)"
      @move-down="onMove(1)"
    />

    <DocSlashMenu :open="slash.open" :x="slash.x" :y="slash.y" @close="slash.open = false" @select="onSlashSelect" />
  </main>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { stage4Visual } from '../config/stage4Visual'
import { createResourceTask, downloadDocumentMarkdown, getResource, streamResourceTask, toggleResourceFavorite } from '../services/resourceApi'
import { type ResourceItem, type ResourceSource } from '../types/resource'
import { type BlockType, type Crumb, type DocBlock, type LegacyDocBlock, type TocItem, headingLevel, isHeading } from '../types/doc'
import { countCitations, countWords, createBlock, continuesOnEnter, normalizeBlock, plainText } from '../utils/docBlocks'
import { resolveDocBlocks } from '../utils/markdownToBlocks'
import { useDocDocument } from '../composables/useDocDocument'
import DocBlockView from '../components/doc/DocBlockView.vue'
import DocPageHeader from '../components/doc/DocPageHeader.vue'
import DocToc from '../components/doc/DocToc.vue'
import DocBlockMenu from '../components/doc/DocBlockMenu.vue'
import DocSlashMenu from '../components/doc/DocSlashMenu.vue'

const route = useRoute()
const router = useRouter()

const resource = ref<ResourceItem>()
const selectedSource = ref<ResourceSource | null>(null)
const activeId = ref('')
const streaming = ref(false)
const reforging = ref(false)
const animated = ref(new Set<number>())
const observer = ref<IntersectionObserver>()
const dragId = ref('')
const dropTarget = ref<{ id: string; hint: 'before' | 'after' } | null>(null)
const menu = ref({ open: false, x: 0, y: 0, id: '' })
const slash = ref({ open: false, x: 0, y: 0, id: '' })
let controller: AbortController | undefined
let userScrolled = false

const editable = computed(() => !streaming.value && resource.value?.status === 'ready' && resource.value?.id !== 'mock-doc')

const {
  blocks, meta, version, selectedId, status, errorMessage, canUndo, canRedo,
  update, insertAfter: insertBlockAfter, append, remove, move, turnInto, duplicate,
  undo, redo, setMeta, setBlocksSilently, save,
} = useDocDocument({
  resourceId: () => String(route.params.id),
  canEdit: () => editable.value,
})

const documentStyle = computed(() => ({
  '--doc-line-height': String(stage4Visual.document.readingLineHeight),
  '--doc-measure': stage4Visual.document.measure,
  '--doc-paragraph-gap': stage4Visual.document.paragraphGap,
  '--doc-reveal-duration': `${stage4Visual.document.blockRevealMs}ms`,
  '--doc-reveal-distance': stage4Visual.document.blockRevealDistance,
  '--doc-citation-duration': `${stage4Visual.document.citationTransitionMs}ms`,
  '--doc-action-blur': stage4Visual.document.actionBarBlur,
}))

const createdAt = computed(() => resource.value?.created_at
  ? new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'short', day: 'numeric' }).format(new Date(resource.value.created_at))
  : '生成中')

/** 文稿体裁由生成时判定（学习计划 / 对比分析 / 讲解…），旧资源没有这个字段。 */
const kindLabel = computed(() => {
  const content = resource.value?.content as { doc_kind?: unknown; source_mode?: unknown } | undefined
  const kind = typeof content?.doc_kind === 'string' && content.doc_kind.trim() ? content.doc_kind : '讲解文档'
  // 没命中教材时标明「通识整理」：读者要知道这份稿子有没有教材依据。
  return content?.source_mode === 'general' ? `${kind} · 通识整理` : kind
})
const fullWidth = computed(() => meta.value.fullWidth === true)
const wordCount = computed(() => countWords(blocks.value))
const citationTotal = computed(() => countCitations(blocks.value))

const breadcrumb = computed<Crumb[]>(() => {
  const items: Crumb[] = [{ label: '我的资源库', to: '/resources' }]
  if (resource.value?.node_name) items.push({ label: resource.value.node_name })
  items.push({ label: kindLabel.value })
  return items
})

const saveLabel = computed(() => ({
  idle: '已保存', dirty: '未保存', saving: '保存中…', saved: '已保存', conflict: '版本冲突', error: '保存失败',
} as Record<string, string>)[status.value] || '已保存')

const toc = computed<TocItem[]>(() => blocks.value
  .filter(block => isHeading(block.type))
  .map(block => ({ id: headingId(block.id), text: plainText(block), level: headingLevel(block.type) || 2 })))

const headingId = (blockId: string) => `doc-heading-${blockId}`

/** 计划快照的块带 node_id；普通文稿没有，也就不会出现「在地图上看」。 */
const mapNodeOf = (block: DocBlock) => String(block.meta?.node_id || '')

const openOnMap = (block: DocBlock) => {
  const nodeId = mapNodeOf(block)
  if (!nodeId) return
  void router.push(`/path?focus=${encodeURIComponent(nodeId)}`)
}

/**
 * 有序列表的序号必须按「连续同类块」重新起算，不能用全局下标：
 * 否则一篇文档里第二个列表会从 7. 开始编号（已实测出现）。
 */
const listOrdinal = (index: number): number => {
  const current = blocks.value[index]
  if (!current) return index
  if (current.type !== 'numbered') return index
  let ordinal = 0
  for (let i = index; i >= 0; i -= 1) {
    if (blocks.value[i].type !== 'numbered') break
    ordinal += 1
  }
  return ordinal - 1
}

const sourceValue = (source: ResourceSource, keys: string[]) => keys.map(key => source[key]).find(Boolean)

const openCitation = (index: number) => { selectedSource.value = resource.value?.sources?.[index] ?? null }

/** 引文角标渲染在各块内部，用事件委托统一处理，避免给每个块传回调 */
const onDocumentClick = (event: MouseEvent) => {
  const target = (event.target as HTMLElement)?.closest?.('.doc-citation') as HTMLElement | null
  if (!target) return
  const raw = target.dataset.citation
  if (raw === undefined) return
  openCitation(Number(raw))
}

const favorite = async () => { if (resource.value) resource.value = await toggleResourceFavorite(resource.value.id) }

const exportMarkdown = async () => {
  if (!resource.value || resource.value.id === 'mock-doc') return
  try {
    const blob = await downloadDocumentMarkdown(resource.value.id)
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${resource.value.title || 'document'}.md`
    anchor.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

const reforge = async () => {
  if (!resource.value || resource.value.id === 'mock-doc') return
  reforging.value = true
  try {
    const task = await createResourceTask(resource.value.node_id, resource.value.type)
    router.push({ name: 'resource-detail', params: { id: resource.value.id }, query: { task: task.task_id } })
  } finally {
    reforging.value = false
  }
}

const reveal = async (block: DocBlock) => {
  blocks.value.push(block)
  const index = blocks.value.length - 1
  animated.value = new Set([...animated.value, index])
  await nextTick()
  if (!userScrolled) window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'smooth' })
}

const finishReveal = (index: number) => {
  if (!animated.value.has(index)) return
  const next = new Set(animated.value)
  next.delete(index)
  animated.value = next
}

const finishStreaming = () => window.setTimeout(() => {
  animated.value = new Set()
  streaming.value = false
}, stage4Visual.document.blockRevealMs + stage4Visual.document.blockStaggerMs * 6)

const resolveBlocks = (item: ResourceItem | undefined): DocBlock[] => resolveDocBlocks(item?.content)

const load = async () => {
  controller?.abort()
  setBlocksSilently([])
  selectedId.value = ''
  animated.value = new Set()
  userScrolled = false
  const id = String(route.params.id)
  const task = typeof route.query.task === 'string' ? route.query.task : ''
  try {
    resource.value = id === 'mock-doc' ? mockResource : await getResource(id)
  } catch {
    resource.value = mockResource
  }
  meta.value = { ...(resource.value?.meta || {}) }
  version.value = Number(resource.value?.meta?.doc_version || 1)

  if (task && id === 'mock-doc') {
    streaming.value = true
    for (const block of resolveBlocks(mockResource)) {
      await new Promise(resolve => window.setTimeout(resolve, 260))
      await reveal(block)
    }
    finishStreaming()
    return
  }

  if (task) {
    streaming.value = true
    controller = new AbortController()
    await streamResourceTask(task, event => {
      if (event.event === 'block' && event.payload.block) {
        const block = normalizeBlock(event.payload.block as LegacyDocBlock)
        if (block) void reveal(block)
      }
      if (event.event === 'done') finishStreaming()
      if (event.event === 'error') streaming.value = false
    }, { signal: controller.signal, onConnection: () => undefined })
    if (!blocks.value.length) setBlocksSilently(resolveBlocks(resource.value))
    return
  }

  setBlocksSilently(resolveBlocks(resource.value))
}

const reload = () => { void load() }

const scrollTo = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })

const focusBlock = async (id: string, position: 'start' | 'end' = 'end') => {
  await nextTick()
  const host = document.querySelector(`[data-block-id="${id}"] .doc-editable`) as HTMLElement | null
  if (!host) return
  host.focus()
  const range = document.createRange()
  range.selectNodeContents(host)
  range.collapse(position === 'start')
  const selection = window.getSelection()
  selection?.removeAllRanges()
  selection?.addRange(range)
}

const onUpdate = (block: DocBlock) => update(block)

const onEnter = (id: string, payload: { before: string; after: string }) => {
  const block = blocks.value.find(item => item.id === id)
  if (!block) return
  if (block.text !== payload.before) update({ ...block, text: payload.before })
  const nextType: BlockType = continuesOnEnter(block.type) ? block.type : 'paragraph'
  const created = insertBlockAfter(id, createBlock(nextType, { text: payload.after }))
  void focusBlock(created.id, 'start')
}

const onBackspaceEmpty = (id: string) => {
  const index = blocks.value.findIndex(item => item.id === id)
  if (index <= 0) return
  const previous = blocks.value[index - 1]
  remove(id)
  void focusBlock(previous.id, 'end')
}

const insertAfter = (id: string) => {
  const created = insertBlockAfter(id)
  void focusBlock(created.id, 'start')
}

const appendBlock = () => {
  const created = append()
  void focusBlock(created.id, 'end')
}

const openMenu = (payload: { id: string; x: number; y: number }) => {
  selectedId.value = payload.id
  menu.value = { open: true, x: payload.x, y: payload.y, id: payload.id }
}

const menuBlock = computed(() => blocks.value.find(block => block.id === menu.value.id) || null)
const menuIndex = computed(() => blocks.value.findIndex(block => block.id === menu.value.id))

const onTurnInto = (type: BlockType) => {
  turnInto(menu.value.id, type)
  menu.value.open = false
}

const onDuplicate = () => { duplicate(menu.value.id); menu.value.open = false }

const onRemove = () => {
  const id = menu.value.id
  menu.value.open = false
  remove(id)
}

const onMove = (direction: -1 | 1) => {
  const index = menuIndex.value
  const target = blocks.value[index + direction]
  if (!target) return
  move(menu.value.id, target.id, direction === -1 ? 'before' : 'after')
  menu.value.open = false
}

const openSlash = (payload: { id: string }) => {
  const host = document.querySelector(`[data-block-id="${payload.id}"]`) as HTMLElement | null
  const rect = host?.getBoundingClientRect()
  slash.value = {
    open: true,
    x: rect ? Math.min(rect.left + 24, window.innerWidth - 340) : 200,
    y: rect ? Math.min(rect.bottom + 4, window.innerHeight - 400) : 200,
    id: payload.id,
  }
}

const onSlashSelect = (type: BlockType) => {
  const id = slash.value.id
  slash.value.open = false
  turnInto(id, type)
  void focusBlock(id, 'end')
}

const onDragStart = (id: string) => { dragId.value = id }

const onDragOver = (event: DragEvent, block: DocBlock) => {
  if (!dragId.value || dragId.value === block.id) { dropTarget.value = null; return }
  event.preventDefault()
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  dropTarget.value = { id: block.id, hint: event.clientY < rect.top + rect.height / 2 ? 'before' : 'after' }
}

const onDrop = () => {
  if (dragId.value && dropTarget.value) move(dragId.value, dropTarget.value.id, dropTarget.value.hint)
  dragId.value = ''
  dropTarget.value = null
}

const onDragEnd = () => {
  dragId.value = ''
  dropTarget.value = null
  document.body.classList.remove('doc-dragging')
}

const COVERS = ['jade', 'ochre', 'slate', 'ink', 'sunset', 'night']
const nextCover = () => {
  const current = String(meta.value.cover || '')
  const index = COVERS.indexOf(current)
  return COVERS[(index + 1) % COVERS.length]
}

const onKeydown = (event: KeyboardEvent) => {
  const mod = event.metaKey || event.ctrlKey
  if (!mod) return
  const key = event.key.toLowerCase()
  if (key === 'z') {
    if (!editable.value) return
    event.preventDefault()
    if (event.shiftKey) redo(); else undo()
    return
  }
  if (key === 's') {
    event.preventDefault()
    void save(true)
  }
}

const mockResource: ResourceItem = {
  id: 'mock-doc',
  node_id: 'd01',
  type: 'doc',
  title: '麻黄 · 性味归经与功效',
  status: 'ready',
  created_at: new Date().toISOString(),
  favorited: false,
  node_name: '解表药 · 麻黄',
  sources: [{ '出处': '《本草纲目》', '原文摘录': '麻黄，发汗解表。', '定位': '草部' }],
  notes: '依次完成本草检索、结构编排与溯源校验后封存。',
  meta: { icon: '🌿', cover: 'jade', doc_version: 1 },
  content: {
    blocks: buildMockBlocks(),
  },
}

function buildMockBlocks(): DocBlock[] {
  return [
    createBlock('heading2', { text: '核心定位' }),
    createBlock('paragraph', { text: '麻黄以**发汗解表**、宣肺平喘、利水消肿见长 [0]，为解表散寒之要药。' }),
    createBlock('callout', { text: '表实无汗是辨识使用情境的关键；表虚自汗者忌用。', props: { tone: 'warn', icon: '⚠' } }),
    createBlock('heading3', { text: '性味归经' }),
    createBlock('bulleted', { text: '辛、微苦，**温**' }),
    createBlock('bulleted', { text: '归肺、膀胱经' }),
    createBlock('numbered', { text: '发汗解表' }),
    createBlock('numbered', { text: '宣肺平喘' }),
    createBlock('todo', { text: '记住「麻黄汤」四味组成', props: { checked: true } }),
    createBlock('todo', { text: '对比桂枝与麻黄的发汗强弱' }),
    createBlock('toggle', {
      text: '与桂枝的鉴别要点',
      props: { collapsed: false },
      children: [
        createBlock('paragraph', { text: '麻黄发汗力强，走表实无汗；桂枝解肌和营，走表虚有汗。' }),
      ],
    }),
    createBlock('table', {
      props: {
        columns: ['维度', '麻黄', '桂枝'],
        rows: [['性味', '辛、微苦，温', '辛、甘，温'], ['发汗', '强', '缓'], ['主治', '风寒表实证', '风寒表虚证']],
        header: true,
      },
    }),
    createBlock('quote', { text: '麻黄，发汗解表，宣肺平喘，利水消肿。', props: { source: { '出处': '《本草纲目》', '原文摘录': '麻黄，发汗解表。' } } }),
    createBlock('code', { text: '麻黄汤：麻黄 9g、桂枝 6g、杏仁 9g、炙甘草 3g', props: { lang: 'text' } }),
    createBlock('divider' ),
    createBlock('heading3', { text: '用量与使用注意' }),
    createBlock('paragraph', { text: '煎服 2–10g；发汗解表宜生用，止咳平喘多蜜炙。' }),
    createBlock('image', { props: { caption: '麻黄饮片（示例占位）', ratio: '16 / 9' } }),
    createBlock('page', { text: '桂枝 · 同类药对比', props: { nodeId: 'd01.s2.p2', icon: '🌿' } }),
  ]
}

onMounted(() => {
  window.addEventListener('wheel', () => { userScrolled = true }, { passive: true })
  window.addEventListener('keydown', onKeydown)
  observer.value = new IntersectionObserver(entries => {
    const visible = entries.filter(entry => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0]
    if (visible) activeId.value = visible.target.id
  }, { rootMargin: '-18% 0px -68%' })
  void load()
})

watch(blocks, async () => {
  await nextTick()
  document.querySelectorAll<HTMLElement>('[id^="doc-heading-"]').forEach(element => observer.value?.observe(element))
}, { deep: true })

watch(() => route.fullPath, () => { void load() })
onBeforeUnmount(() => {
  controller?.abort()
  window.removeEventListener('keydown', onKeydown)
})
</script>

<style scoped lang="scss">
/* 页面整体。米白纸来自 [data-qh-theme='light'] 的令牌覆盖，
   只作用于本页根节点，全站其他页面仍是暗色。 */
.doc-page {
  min-height: 100vh;
  padding-bottom: 120px;
  background: var(--qh-bg);
  color: var(--qh-fg);
  font-family: var(--qh-font-body);
}

.doc-toolbar {
  position: sticky;
  top: 0;
  z-index: 30;
  display: flex;
  gap: 8px;
  align-items: center;
  height: 46px;
  padding: 0 18px;
  border-bottom: 1px solid var(--qh-line);
  background: color-mix(in srgb, var(--qh-bg) 86%, transparent);
  backdrop-filter: blur(var(--doc-action-blur));
}

.doc-toolbar > button {
  width: 28px;
  height: 28px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--qh-fg-muted);
  font-size: 16px;
  cursor: pointer;

  &:hover { background: color-mix(in srgb, var(--qh-fg) 7%, transparent); color: var(--qh-fg); }
}

.toolbar-title {
  min-width: 0;
  color: var(--qh-fg-muted);
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.toolbar-right {
  display: flex;
  gap: 2px;
  align-items: center;
  margin-left: auto;
}

.toolbar-right button {
  width: 28px;
  height: 28px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--qh-fg-muted);
  font-size: 15px;
  cursor: pointer;

  &:hover:not(:disabled) { background: color-mix(in srgb, var(--qh-fg) 7%, transparent); color: var(--qh-fg); }
  &:disabled { opacity: 0.32; cursor: default; }
  &.active { color: var(--qh-accent); }
}

.save-state {
  margin-right: 8px;
  color: var(--qh-fg-faint);
  font-size: 11.5px;

  &.is-saving { color: var(--qh-fg-muted); }
  &.is-conflict,
  &.is-error { color: var(--qh-accent); }
}

/* 正文与右侧目录同一栅格：目录跟随内容走，不再用 fixed 定位去猜侧栏宽度 */
.document-shell {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, var(--doc-measure)) 188px;
  gap: 30px;
  justify-content: center;
  width: min(calc(var(--doc-measure) + 218px), calc(100% - 40px));
  margin: 0 auto;
  padding: 28px 0 120px;
}

.document {
  margin: auto;
}

.generation-state {
  margin: 10px 0 0;
  color: var(--qh-accent);
  font-size: 12px;
  letter-spacing: 0.06em;
}

.conflict-banner {
  display: flex;
  gap: 10px;
  align-items: center;
  margin: 14px 0 4px;
  padding: 9px 12px;
  border: 1px solid color-mix(in srgb, var(--qh-accent) 32%, transparent);
  border-radius: 4px;
  background: color-mix(in srgb, var(--qh-accent) 6%, transparent);
  color: var(--qh-fg);
  font-size: 12.5px;

  button {
    margin-left: auto;
    padding: 3px 9px;
    border: 1px solid var(--qh-line-strong);
    border-radius: 4px;
    background: var(--qh-surface-raised);
    color: var(--qh-fg);
    font-size: 12px;
    cursor: pointer;
  }
}

.block-map-jump {
  margin: 4px 0 0 2px;
  padding: 2px 8px;
  border: 1px solid color-mix(in srgb, var(--qh-color-jade) 40%, transparent);
  border-radius: 999px;
  background: transparent;
  color: var(--qh-color-jade);
  font-size: 11px;
  cursor: pointer;

  &:hover,
  &:focus-visible {
    background: color-mix(in srgb, var(--qh-color-jade) 14%, transparent);
    outline: none;
  }
}

.block-list {
  padding-top: 14px;
}

.block {
  position: relative;
}

.block.is-revealing {
  animation: block-in var(--doc-reveal-duration) ease-out both;
}

/* 块左侧留出 46px，手柄悬浮其上 —— 与 Notion 一致，正文宽度不因手柄变化 */
.block-list :deep(.doc-block) {
  padding-left: 2px;
}

.block-tail {
  display: block;
  width: 100%;
  margin-top: 6px;
  padding: 7px 2px;
  border: 0;
  border-radius: 3px;
  background: transparent;
  color: var(--qh-fg-faint);
  font-size: 13px;
  text-align: left;
  cursor: text;

  &:hover { background: color-mix(in srgb, var(--qh-fg) 4%, transparent); color: var(--qh-fg-muted); }
}

/* 炼丹手记：保留类名与伪元素选择器（既有视觉测试依赖），观感改成 Notion 灰色 callout */
.alchemy-notes {
  margin-top: 34px;
  padding: 10px 12px;
  border-radius: 4px;
  background: color-mix(in srgb, var(--qh-fg) 4%, transparent);
}

.alchemy-notes summary {
  color: var(--qh-fg-muted);
  font-size: 12.5px;
  cursor: pointer;
}

.alchemy-notes > div {
  position: relative;
  margin-top: 8px;
  color: var(--qh-fg-soft);
  font-size: 13px;
  line-height: 1.7;
}

.alchemy-notes>div:before {
  display: none;
  content: '';
}

.alchemy-notes .pin,
.alchemy-notes .seal {
  display: none;
}

.doc-footer {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  margin-top: 40px;
  padding-top: 14px;
  border-top: 1px solid var(--qh-line);
  color: var(--qh-fg-faint);
  font-size: 11.5px;
}

.study-actions {
  position: fixed;
  right: 50%;
  bottom: 22px;
  z-index: 20;
  display: flex;
  align-items: center;
  gap: 6px;
  width: max-content;
  max-width: calc(100% - 32px);
  padding: 5px;
  border: 1px solid var(--qh-line-strong);
  border-radius: 8px;
  background: color-mix(in srgb, var(--qh-surface-raised) 92%, transparent);
  box-shadow: var(--qh-shadow-2);
  backdrop-filter: blur(var(--doc-action-blur));
  transform: translateX(50%);

  span {
    padding: 0 8px;
    color: var(--qh-fg-faint);
    font-size: 12px;
  }

  button {
    padding: 6px 11px;
    border: 1px solid transparent;
    border-radius: 5px;
    background: transparent;
    color: var(--qh-fg);
    font-size: 13px;
    cursor: pointer;

    &:hover { background: color-mix(in srgb, var(--qh-fg) 7%, transparent); }
  }
}

.citation-panel {
  position: fixed;
  top: 58px;
  right: 20px;
  z-index: 40;
  width: min(340px, calc(100vw - 40px));
  padding: 20px;
  border: 1px solid var(--qh-line-strong);
  border-radius: 8px;
  background: var(--qh-surface-raised);
  box-shadow: var(--qh-shadow-3);
  color: var(--qh-fg);

  > button {
    position: absolute;
    top: 8px;
    right: 10px;
    border: 0;
    background: transparent;
    color: var(--qh-fg-muted);
    font-size: 19px;
    cursor: pointer;
  }

  p {
    margin: 0 0 4px;
    color: var(--qh-fg-faint);
    font-size: 11px;
    letter-spacing: 0.06em;
  }

  h2 {
    margin: 0 0 16px;
    font-family: var(--qh-font-body);
    font-size: 17px;
    font-weight: 600;
  }

  blockquote {
    margin: 0 0 16px;
    padding-left: 10px;
    border-left: 2px solid var(--qh-line-strong);
    color: var(--qh-fg-soft);
    font-size: 14px;
    line-height: 1.7;

    small {
      display: block;
      margin-bottom: 4px;
      color: var(--qh-fg-faint);
      font-size: 11px;
    }
  }

  strong {
    color: var(--qh-fg);
    font-size: 13px;
    font-weight: 500;
  }
}

.citation-enter-active,
.citation-leave-active {
  transition: opacity var(--doc-citation-duration), transform var(--doc-citation-duration);
}

.citation-enter-from,
.citation-leave-to {
  opacity: 0;
  transform: translateX(12px);
}

:global(body.doc-dragging) {
  cursor: grabbing;
  user-select: none;
}

@keyframes block-in {
  from { opacity: 0; transform: translateY(var(--doc-reveal-distance)); }
  to { opacity: 1; transform: translateY(0); }
}

/* 全宽：正文吃掉剩余宽度，目录仍占独立一列 */
.doc-page.is-wide .document-shell {
  grid-template-columns: minmax(0, 1fr) 188px;
  width: calc(100% - 40px);
  max-width: 1180px;
}

@media (max-width: 1240px) {
  .document-shell {
    grid-template-columns: minmax(0, var(--doc-measure));
    width: min(var(--doc-measure), calc(100% - 40px));
  }
}

@media (max-width: 720px) {
  .document-shell { width: calc(100% - 32px); }
  .study-actions span { display: none; }
}

@media (prefers-reduced-motion: reduce) {
  .block.is-revealing { animation: none; }
}
</style>
