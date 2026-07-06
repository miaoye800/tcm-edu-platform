<template>
  <section v-loading="learning.loadingPath" class="path-view">
    <header class="path-hero">
      <div class="path-title">
        <p class="eyebrow">闯关地图</p>
        <h2>中药学技能树</h2>
        <p>{{ progressText }}</p>
      </div>
      <div class="path-controls">
        <el-input
          v-model="searchKeyword"
          clearable
          :prefix-icon="Search"
          placeholder="搜索药材或关卡，如 麻黄、桂枝"
          @keyup.enter="revealSearchMatch(searchKeyword)"
        />
        <div class="button-row">
          <el-button :icon="Expand" @click="expandAll">全部展开</el-button>
          <el-button :icon="Fold" @click="collapseAll">全部折叠</el-button>
          <el-button type="primary" :icon="Refresh" @click="refreshPath">刷新路径</el-button>
        </div>
      </div>
    </header>

    <el-alert
      v-if="learning.error"
      type="warning"
      :title="learning.error"
      show-icon
      :closable="false"
      class="path-alert"
    />

    <section class="overview-row">
      <div class="overview-item">
        <strong>{{ pathStats.mastered }}</strong>
        <span>已掌握</span>
      </div>
      <div class="overview-item">
        <strong>{{ pathStats.total }}</strong>
        <span>总关卡</span>
      </div>
      <div class="overview-item">
        <strong>{{ pathStats.unlocked + pathStats.inProgress }}</strong>
        <span>可挑战</span>
      </div>
      <el-progress
        class="overview-progress"
        :percentage="masteredPercent"
        :stroke-width="10"
        :show-text="false"
      />
    </section>

    <section class="legend-row">
      <span v-for="item in legend" :key="item.status" :class="`is-${item.status}`">{{ item.label }}</span>
    </section>

    <section v-if="learning.streaming || learning.hasResources" class="generation-strip">
      <span class="live-dot" />
      <div>
        <strong>{{ learning.activeTopic }}</strong>
        <p>{{ learning.streaming ? `小岐正在生成：${learning.nodeLabel}` : '资源已生成，可继续查看学习页' }}</p>
      </div>
      <el-button v-if="learning.hasResources" text type="primary" @click="goLearn">查看资源</el-button>
    </section>

    <section v-if="topCategories.length" class="category-board" aria-label="中药学闯关树">
      <div v-for="category in topCategories" :key="category.node.id" class="category-tree">
        <GlassCard
          :id="domId(category.node.id)"
          tag="article"
          padding="md"
          compact
          class="skill-node category-card"
          :class="[statusClass(category.node), kindClass(category.node), focusClass(category.node), { 'is-expanded': isExpanded(category.node.id) }]"
          @click="toggleNode(category.node.id)"
        >
          <div class="skill-top">
            <div class="node-icons">
              <el-icon class="toggle-icon" :class="{ 'is-open': isExpanded(category.node.id) }">
                <ArrowRight />
              </el-icon>
              <el-icon class="status-icon">
                <Lock v-if="category.node.status === 'locked'" />
                <CircleCheck v-else-if="category.node.status === 'mastered'" />
                <Unlock v-else />
              </el-icon>
            </div>
            <Badge :tone="statusTone(category.node.status)" :label="statusLabel(category.node.status)" />
          </div>
          <div class="node-content">
            <p class="kind-label">大类 · {{ category.subcategories.length }} 个小类</p>
            <h3>{{ category.node.title }}</h3>
            <p>{{ category.node.reason }}</p>
          </div>
          <div class="skill-footer">
            <div class="skill-meta">
              <span>掌握 {{ percent(category.node.mastery) }}%</span>
              <span>置信 {{ percent(category.node.confidence) }}%</span>
            </div>
            <el-button
              class="resource-button"
              size="small"
              :icon="MagicStick"
              :loading="learning.streaming && learning.activeTopic === category.node.title"
              @click.stop="handleResourceClick(category.node)"
            >
              生成资源
            </el-button>
          </div>
        </GlassCard>

        <Transition name="tree-expand">
          <div v-if="isExpanded(category.node.id)" class="category-children">
            <div v-for="subcategory in category.subcategories" :key="subcategory.node.id" class="subcategory-block">
              <article
                :id="domId(subcategory.node.id)"
                class="skill-node subcategory-node"
                :class="[statusClass(subcategory.node), kindClass(subcategory.node), focusClass(subcategory.node), { 'is-expanded': isExpanded(subcategory.node.id) }]"
                @click="toggleNode(subcategory.node.id)"
              >
                <div class="skill-top">
                  <div class="node-icons">
                    <el-icon class="toggle-icon" :class="{ 'is-open': isExpanded(subcategory.node.id) }">
                      <ArrowRight />
                    </el-icon>
                    <el-icon class="status-icon">
                      <Lock v-if="subcategory.node.status === 'locked'" />
                      <CircleCheck v-else-if="subcategory.node.status === 'mastered'" />
                      <Unlock v-else />
                    </el-icon>
                  </div>
                  <Badge :tone="statusTone(subcategory.node.status)" :label="statusLabel(subcategory.node.status)" />
                </div>
                <div class="node-content">
                  <p class="kind-label">
                    小类 · {{ subcategory.herbs.length }} 味药材 · {{ subcategory.diffs.length }} 个鉴别
                  </p>
                  <h3>{{ subcategory.node.title }}</h3>
                  <p>{{ subcategory.node.reason }}</p>
                </div>
                <div class="skill-footer">
                  <div class="skill-meta">
                    <span>掌握 {{ percent(subcategory.node.mastery) }}%</span>
                    <span>置信 {{ percent(subcategory.node.confidence) }}%</span>
                  </div>
                  <el-button
                    class="resource-button"
                    size="small"
                    :icon="MagicStick"
                    :loading="learning.streaming && learning.activeTopic === subcategory.node.title"
                    @click.stop="handleResourceClick(subcategory.node)"
                  >
                    生成资源
                  </el-button>
                </div>
              </article>

              <Transition name="tree-expand">
                <div v-if="isExpanded(subcategory.node.id)" class="subcategory-children">
                  <section v-if="subcategory.herbs.length" class="list-section">
                    <div class="section-head">
                      <div>
                        <p class="eyebrow">Herbs</p>
                        <h4>药材关卡</h4>
                      </div>
                      <span>{{ visibleChildren(subcategory.herbs, herbsLimitKey(subcategory.node.id)).length }}/{{ subcategory.herbs.length }}</span>
                    </div>
                    <div class="card-grid">
                      <article
                        v-for="herb in visibleChildren(subcategory.herbs, herbsLimitKey(subcategory.node.id))"
                        :id="domId(herb.id)"
                        :key="herb.id"
                        class="skill-node leaf-node"
                        :class="[statusClass(herb), kindClass(herb), focusClass(herb)]"
                        @click="handleResourceClick(herb)"
                      >
                        <div class="skill-top">
                          <el-icon class="status-icon">
                            <Lock v-if="herb.status === 'locked'" />
                            <CircleCheck v-else-if="herb.status === 'mastered'" />
                            <Unlock v-else />
                          </el-icon>
                          <Badge :tone="statusTone(herb.status)" :label="statusLabel(herb.status)" />
                        </div>
                        <div class="node-content">
                          <p class="kind-label">药材</p>
                          <h3>{{ herb.title }}</h3>
                          <p>{{ herb.reason }}</p>
                        </div>
                        <div class="skill-meta">
                          <span>掌握 {{ percent(herb.mastery) }}%</span>
                          <span>置信 {{ percent(herb.confidence) }}%</span>
                        </div>
                      </article>
                    </div>
                    <el-button
                      v-if="hasMore(subcategory.herbs, herbsLimitKey(subcategory.node.id))"
                      class="more-button"
                      text
                      type="primary"
                      @click="showMore(subcategory.herbs, herbsLimitKey(subcategory.node.id))"
                    >
                      展开更多药材
                    </el-button>
                  </section>

                  <section v-if="subcategory.diffs.length" class="diff-section">
                    <button
                      class="diff-toggle"
                      type="button"
                      :aria-expanded="isExpanded(diffGroupKey(subcategory.node.id))"
                      @click="toggleNode(diffGroupKey(subcategory.node.id))"
                    >
                      <span>
                        <el-icon class="toggle-icon" :class="{ 'is-open': isExpanded(diffGroupKey(subcategory.node.id)) }">
                          <ArrowRight />
                        </el-icon>
                        相似药鉴别 ({{ subcategory.diffs.length }})
                      </span>
                      <Badge tone="blue" :label="`${visibleChildren(subcategory.diffs, diffsLimitKey(subcategory.node.id)).length}/${subcategory.diffs.length}`" />
                    </button>

                    <Transition name="tree-expand">
                      <div v-if="isExpanded(diffGroupKey(subcategory.node.id))" class="diff-list">
                        <div class="card-grid">
                          <article
                            v-for="diff in visibleChildren(subcategory.diffs, diffsLimitKey(subcategory.node.id))"
                            :id="domId(diff.id)"
                            :key="diff.id"
                            class="skill-node leaf-node diff-node"
                            :class="[statusClass(diff), kindClass(diff), focusClass(diff)]"
                            @click="handleResourceClick(diff)"
                          >
                            <div class="skill-top">
                              <el-icon class="status-icon">
                                <Lock v-if="diff.status === 'locked'" />
                                <CircleCheck v-else-if="diff.status === 'mastered'" />
                                <Unlock v-else />
                              </el-icon>
                              <Badge :tone="statusTone(diff.status)" :label="statusLabel(diff.status)" />
                            </div>
                            <div class="node-content">
                              <p class="kind-label">鉴别</p>
                              <h3>{{ diff.title }}</h3>
                              <p>{{ diff.reason }}</p>
                            </div>
                            <div class="skill-meta">
                              <span>掌握 {{ percent(diff.mastery) }}%</span>
                              <span>置信 {{ percent(diff.confidence) }}%</span>
                            </div>
                          </article>
                        </div>
                        <el-button
                          v-if="hasMore(subcategory.diffs, diffsLimitKey(subcategory.node.id))"
                          class="more-button"
                          text
                          type="primary"
                          @click="showMore(subcategory.diffs, diffsLimitKey(subcategory.node.id))"
                        >
                          展开更多鉴别
                        </el-button>
                      </div>
                    </Transition>
                  </section>
                </div>
              </Transition>
            </div>
          </div>
        </Transition>
      </div>
    </section>

    <el-empty v-else-if="!learning.loadingPath" description="暂无路径数据" />
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowRight, CircleCheck, Expand, Fold, Lock, MagicStick, Refresh, Search, Unlock } from '@element-plus/icons-vue'
import Badge from '../components/ui/Badge.vue'
import GlassCard from '../components/ui/GlassCard.vue'
import { useLearningStore } from '../stores/learning'
import type { ResourceType, SkillNode, SkillStatus } from '../types/learning'

type PathKind = SkillNode['kind'] | 'diff'
type PathSkillNode = Omit<SkillNode, 'kind'> & { kind: PathKind }

type BranchStats = {
  total: number
  mastered: number
  unlocked: number
  inProgress: number
  locked: number
}

type SubcategoryBranch = {
  node: PathSkillNode
  herbs: PathSkillNode[]
  diffs: PathSkillNode[]
  stats: BranchStats
}

type CategoryBranch = {
  node: PathSkillNode
  subcategories: SubcategoryBranch[]
  stats: BranchStats
}

const EXPANDED_STORAGE_KEY = 'qihuang:path-expanded:v1'
const PAGE_SIZE = 50
const fallbackResourceTypes: ResourceType[] = ['explain', 'graph', 'mindmap', 'quiz', 'reading']

const router = useRouter()
const learning = useLearningStore()
const expanded = ref<Record<string, boolean>>({})
const pageLimits = ref<Record<string, number>>({})
const searchKeyword = ref('')
const focusedNodeId = ref('')
let searchTimer: number | undefined
let focusTimer: number | undefined

const legend: Array<{ status: SkillStatus; label: string }> = [
  { status: 'locked', label: '锁定' },
  { status: 'unlocked', label: '已解锁' },
  { status: 'in_progress', label: '进行中' },
  { status: 'mastered', label: '已掌握' },
]

const skillNodes = computed<PathSkillNode[]>(() =>
  learning.skillTree.map((node) => ({
    ...node,
    kind: node.id.startsWith('diff:') ? 'differentiation' : node.kind,
  })),
)

const nodeById = computed(() => new Map(skillNodes.value.map((node) => [node.id, node])))

const treeData = computed<CategoryBranch[]>(() => {
  const byId = nodeById.value
  const categories = skillNodes.value.filter((node) => node.kind === 'category')
  const subcategories = skillNodes.value.filter((node) => node.kind === 'subcategory')
  const herbs = skillNodes.value.filter((node) => node.kind === 'herb')
  const diffs = skillNodes.value.filter(isDiffNode)
  const categoryBranches = new Map<string, CategoryBranch>()
  const subcategoryBranches = new Map<string, SubcategoryBranch>()
  const herbToSubcategory = new Map<string, string>()

  for (const category of categories) {
    categoryBranches.set(category.id, {
      node: category,
      subcategories: [],
      stats: emptyStats(),
    })
  }

  for (const subcategory of subcategories) {
    const categoryId = resolveCategoryId(subcategory, byId)
    const category = categoryId ? categoryBranches.get(categoryId) : undefined
    if (!category) continue

    const branch: SubcategoryBranch = {
      node: subcategory,
      herbs: [],
      diffs: [],
      stats: emptyStats(),
    }
    subcategoryBranches.set(subcategory.id, branch)
    category.subcategories.push(branch)
  }

  for (const herb of herbs) {
    const subcategoryId = resolveSubcategoryId(herb, byId)
    if (!subcategoryId) continue
    herbToSubcategory.set(herb.id, subcategoryId)
    subcategoryBranches.get(subcategoryId)?.herbs.push(herb)
  }

  for (const diff of diffs) {
    const subcategoryId = resolveDiffSubcategoryId(diff, byId, herbToSubcategory)
    if (!subcategoryId) continue
    subcategoryBranches.get(subcategoryId)?.diffs.push(diff)
  }

  for (const subcategory of subcategoryBranches.values()) {
    subcategory.stats = buildStats([subcategory.node, ...subcategory.herbs, ...subcategory.diffs])
  }

  for (const category of categoryBranches.values()) {
    const nodes = category.subcategories.flatMap((subcategory) => [subcategory.node, ...subcategory.herbs, ...subcategory.diffs])
    category.stats = buildStats([category.node, ...nodes])
  }

  return categories.flatMap((category) => {
    const branch = categoryBranches.get(category.id)
    return branch ? [branch] : []
  })
})

const treeLookup = computed(() => {
  const categoryBySubcategory = new Map<string, string>()
  const subcategoryByHerb = new Map<string, string>()
  const subcategoryByDiff = new Map<string, string>()

  for (const category of treeData.value) {
    for (const subcategory of category.subcategories) {
      categoryBySubcategory.set(subcategory.node.id, category.node.id)
      for (const herb of subcategory.herbs) {
        subcategoryByHerb.set(herb.id, subcategory.node.id)
      }
      for (const diff of subcategory.diffs) {
        subcategoryByDiff.set(diff.id, subcategory.node.id)
      }
    }
  }

  return {
    categoryBySubcategory,
    subcategoryByHerb,
    subcategoryByDiff,
  }
})

const topCategories = computed(() => treeData.value.slice(0, 3))
const pathStats = computed(() => buildStats(skillNodes.value))
const progressText = computed(() => {
  const total = pathStats.value.total
  const mastered = pathStats.value.mastered
  const challengeable = pathStats.value.unlocked + pathStats.value.inProgress
  return `已掌握 ${mastered}/${total} · 当前可挑战 ${challengeable} 个关卡`
})
const masteredPercent = computed(() => {
  if (!pathStats.value.total) return 0
  return Math.round((pathStats.value.mastered / pathStats.value.total) * 100)
})

const refreshPath = () => {
  void learning.loadPath()
}

const isExpanded = (key: string) => Boolean(expanded.value[key])

const toggleNode = (key: string) => {
  expanded.value = {
    ...expanded.value,
    [key]: !isExpanded(key),
  }
}

const setExpanded = (key: string, value = true) => {
  expanded.value = {
    ...expanded.value,
    [key]: value,
  }
}

const expandAll = () => {
  const nextExpanded: Record<string, boolean> = {}
  for (const category of treeData.value) {
    nextExpanded[category.node.id] = true
    for (const subcategory of category.subcategories) {
      nextExpanded[subcategory.node.id] = true
      if (subcategory.diffs.length) {
        nextExpanded[diffGroupKey(subcategory.node.id)] = true
      }
    }
  }
  expanded.value = nextExpanded
}

const collapseAll = () => {
  expanded.value = {}
}

const handleResourceClick = async (node: PathSkillNode) => {
  if (node.status === 'locked') {
    showLockedMessage(node)
    return
  }
  if (learning.streaming) {
    ElMessage.info('小岐正在生成上一份资源，请稍候')
    return
  }

  const types = node.recommended_resources.length ? node.recommended_resources : fallbackResourceTypes
  ElMessage.success(`小岐开始生成「${node.title}」资源`)
  await learning.generateResources(node.title, types)
}

const showLockedMessage = (node: PathSkillNode) => {
  const prerequisites = node.prerequisites.map((id) => nodeById.value.get(id)?.title ?? id)
  ElMessage.warning(prerequisites.length ? `需先完成前置：${prerequisites.join('、')}` : '该关卡暂未解锁')
}

const goLearn = () => {
  void router.push({ name: 'learn', query: { topic: learning.activeTopic } })
}

const revealSearchMatch = async (keyword: string, notify = true) => {
  const query = keyword.trim()
  if (!query) return

  const match = findBestMatch(query)
  if (!match) {
    if (notify) ElMessage.warning(`未找到「${query}」`)
    return
  }

  expandToNode(match)
  ensurePagedNodeVisible(match)
  focusedNodeId.value = match.id
  await nextTick()
  document.getElementById(domId(match.id))?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  clearFocusTimer()
  focusTimer = window.setTimeout(() => {
    if (focusedNodeId.value === match.id) focusedNodeId.value = ''
  }, 2400)
}

const findBestMatch = (query: string) => {
  const normalizedQuery = query.toLowerCase()
  return [...skillNodes.value]
    .filter((node) => node.title.toLowerCase().includes(normalizedQuery) || node.id.toLowerCase().includes(normalizedQuery))
    .sort((left, right) => searchRank(left, normalizedQuery) - searchRank(right, normalizedQuery))[0]
}

const searchRank = (node: PathSkillNode, query: string) => {
  const title = node.title.toLowerCase()
  if (node.kind === 'herb' && title === query) return 0
  if (title === query) return 1
  if (node.kind === 'herb' && title.includes(query)) return 2
  if (title.includes(query)) return 3
  return 4
}

const expandToNode = (node: PathSkillNode) => {
  if (node.kind === 'category') {
    setExpanded(node.id)
    return
  }

  const lookup = treeLookup.value
  const subcategoryId =
    node.kind === 'subcategory'
      ? node.id
      : node.kind === 'herb'
        ? lookup.subcategoryByHerb.get(node.id)
        : lookup.subcategoryByDiff.get(node.id)
  const categoryId = subcategoryId ? lookup.categoryBySubcategory.get(subcategoryId) : undefined

  if (categoryId) setExpanded(categoryId)
  if (subcategoryId) setExpanded(subcategoryId)
  if (subcategoryId && isDiffNode(node)) setExpanded(diffGroupKey(subcategoryId))
}

const ensurePagedNodeVisible = (node: PathSkillNode) => {
  const lookup = treeLookup.value
  const subcategoryId = node.kind === 'herb' ? lookup.subcategoryByHerb.get(node.id) : lookup.subcategoryByDiff.get(node.id)
  if (!subcategoryId) return

  const subcategory = treeData.value
    .flatMap((category) => category.subcategories)
    .find((item) => item.node.id === subcategoryId)
  if (!subcategory) return

  if (node.kind === 'herb') {
    ensureLimit(herbsLimitKey(subcategoryId), subcategory.herbs.findIndex((item) => item.id === node.id) + 1)
  } else if (isDiffNode(node)) {
    ensureLimit(diffsLimitKey(subcategoryId), subcategory.diffs.findIndex((item) => item.id === node.id) + 1)
  }
}

const ensureLimit = (key: string, minimum: number) => {
  if (minimum <= 0) return
  pageLimits.value = {
    ...pageLimits.value,
    [key]: Math.max(pageLimits.value[key] ?? PAGE_SIZE, minimum),
  }
}

const visibleChildren = <T,>(items: T[], key: string) => items.slice(0, pageLimits.value[key] ?? PAGE_SIZE)

const hasMore = <T,>(items: T[], key: string) => visibleChildren(items, key).length < items.length

const showMore = <T,>(items: T[], key: string) => {
  pageLimits.value = {
    ...pageLimits.value,
    [key]: Math.min(items.length, (pageLimits.value[key] ?? PAGE_SIZE) + PAGE_SIZE),
  }
}

const herbsLimitKey = (subcategoryId: string) => `herbs:${subcategoryId}`

const diffsLimitKey = (subcategoryId: string) => `diffs:${subcategoryId}`

const diffGroupKey = (subcategoryId: string) => `diffs-group:${subcategoryId}`

const percent = (value: number) => Math.round(value * 100)

const statusLabel = (status: SkillStatus) =>
  ({ locked: '锁定', unlocked: '解锁', in_progress: '进行', mastered: '掌握' })[status]

const statusTone = (status: SkillStatus) =>
  ({ locked: 'muted', unlocked: 'blue', in_progress: 'gold', mastered: 'jade' })[status] as 'muted' | 'blue' | 'gold' | 'jade'

const statusClass = (node: PathSkillNode) => `is-${node.status}`

const kindClass = (node: PathSkillNode) => (isDiffNode(node) ? 'kind-differentiation' : `kind-${node.kind}`)

const focusClass = (node: PathSkillNode) => ({ 'is-focused': focusedNodeId.value === node.id })

const domId = (id: string) => `path-node-${encodeURIComponent(id).replace(/%/g, '_')}`

function isDiffNode(node: PathSkillNode): boolean {
  return node.kind === 'differentiation' || node.kind === 'diff' || node.id.startsWith('diff:')
}

function resolveCategoryId(node: PathSkillNode, byId: Map<string, PathSkillNode>): string | undefined {
  const prerequisite = node.prerequisites.find((id) => byId.get(id)?.kind === 'category')
  if (prerequisite) return prerequisite

  if (node.id.startsWith('subcategory:')) {
    const [, category] = node.id.split(':')
    return category ? `category:${category}` : undefined
  }
  return undefined
}

function resolveSubcategoryId(node: PathSkillNode, byId: Map<string, PathSkillNode>): string | undefined {
  return node.prerequisites.find((id) => byId.get(id)?.kind === 'subcategory')
}

function resolveDiffSubcategoryId(
  node: PathSkillNode,
  byId: Map<string, PathSkillNode>,
  herbToSubcategory: Map<string, string>,
): string | undefined {
  const prerequisiteHerbs = node.prerequisites.filter((id) => byId.get(id)?.kind === 'herb' || id.startsWith('herb_'))
  const idHerbs = node.id.startsWith('diff:') ? node.id.split(':').slice(1) : []
  return [...prerequisiteHerbs, ...idHerbs].flatMap((id) => herbToSubcategory.get(id) ?? [])[0]
}

function buildStats(nodes: PathSkillNode[]): BranchStats {
  return nodes.reduce(
    (stats, node) => {
      stats.total += 1
      if (node.status === 'mastered') stats.mastered += 1
      if (node.status === 'unlocked') stats.unlocked += 1
      if (node.status === 'in_progress') stats.inProgress += 1
      if (node.status === 'locked') stats.locked += 1
      return stats
    },
    emptyStats(),
  )
}

function emptyStats(): BranchStats {
  return {
    total: 0,
    mastered: 0,
    unlocked: 0,
    inProgress: 0,
    locked: 0,
  }
}

const loadExpandedState = () => {
  const raw = window.localStorage.getItem(EXPANDED_STORAGE_KEY)
  if (!raw) return
  try {
    const parsed = JSON.parse(raw) as Record<string, boolean>
    expanded.value = parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    expanded.value = {}
  }
}

const clearSearchTimer = () => {
  if (searchTimer) {
    window.clearTimeout(searchTimer)
    searchTimer = undefined
  }
}

const clearFocusTimer = () => {
  if (focusTimer) {
    window.clearTimeout(focusTimer)
    focusTimer = undefined
  }
}

watch(
  expanded,
  (value) => {
    window.localStorage.setItem(EXPANDED_STORAGE_KEY, JSON.stringify(value))
  },
  { deep: true },
)

watch(searchKeyword, (value) => {
  clearSearchTimer()
  if (!value.trim()) return
  searchTimer = window.setTimeout(() => {
    void revealSearchMatch(value, false)
  }, 320)
})

onMounted(() => {
  loadExpandedState()
  void learning.loadPath()
})

onBeforeUnmount(() => {
  clearSearchTimer()
  clearFocusTimer()
})
</script>

<style scoped lang="scss">
@use "../styles/tokens.scss" as *;

.path-view {
  display: grid;
  gap: 18px;
  width: min(1480px, 100%);
  margin: 0 auto;
}

.path-hero {
  @include glass-panel(26px);
  display: grid;
  grid-template-columns: minmax(320px, 0.92fr) minmax(420px, 1.08fr);
  gap: 18px;
  align-items: center;
  padding: 24px;
}

.path-title {
  min-width: 0;
}

.path-controls {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) auto;
  gap: 10px;
  align-items: center;
  min-width: 0;
}

.button-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: flex-end;
}

.eyebrow {
  margin: 0 0 8px;
  color: var(--qh-color-jade);
  font-size: 13px;
  font-weight: 900;
}

h2,
h3,
h4 {
  margin: 0;
  font-family: var(--qh-font-display);
  font-weight: 900;
}

h2 {
  @include text-sheen;
  font-size: clamp(34px, 5vw, 58px);
  line-height: 1.08;
}

h3 {
  color: var(--qh-text);
  font-size: 19px;
  line-height: 1.2;
}

h4 {
  color: var(--qh-text);
  font-size: 18px;
}

.path-title p:last-child {
  margin: 10px 0 0;
  color: var(--qh-text-muted);
}

.path-alert {
  border-radius: 16px;
}

.overview-row {
  display: grid;
  grid-template-columns: repeat(3, minmax(120px, auto)) minmax(180px, 1fr);
  gap: 10px;
  align-items: center;
}

.overview-item {
  @include glass-panel(18px);
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-height: 58px;
  padding: 12px 14px;
}

.overview-item strong {
  color: var(--qh-color-gold);
  font-family: var(--qh-font-display);
  font-size: 26px;
  font-weight: 900;
}

.overview-item span {
  color: var(--qh-text-muted);
  font-size: 13px;
  font-weight: 800;
}

.overview-progress {
  min-width: 180px;
}

.legend-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.legend-row span {
  min-height: 28px;
  padding: 6px 10px;
  border-radius: 999px;
  color: var(--qh-text-muted);
  background: rgba(238, 245, 239, 0.055);
  font-size: 12px;
  font-weight: 900;
}

.legend-row .is-unlocked {
  color: var(--qh-color-blue);
}

.legend-row .is-in_progress {
  color: var(--qh-color-gold);
}

.legend-row .is-mastered {
  color: var(--qh-color-jade);
}

.generation-strip {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 12px;
  align-items: center;
  min-height: 58px;
  padding: 12px 16px;
  border: 1px solid rgba(143, 208, 173, 0.16);
  border-radius: var(--qh-radius-card-sm);
  background: rgba(238, 245, 239, 0.05);
}

.generation-strip strong {
  color: var(--qh-text);
  font-family: var(--qh-font-display);
  font-weight: 900;
}

.generation-strip p {
  margin: 4px 0 0;
  color: var(--qh-text-muted);
  font-size: 13px;
}

.live-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--qh-color-jade);
  animation: live 1.8s ease-in-out infinite;
}

.category-board {
  display: grid;
  gap: 18px;
}

.category-tree {
  display: grid;
  gap: 14px;
}

.category-children,
.subcategory-children,
.diff-list {
  display: grid;
  gap: 14px;
}

.category-children {
  padding-left: clamp(12px, 3vw, 34px);
  border-left: 1px solid rgba(143, 208, 173, 0.18);
}

.subcategory-block {
  display: grid;
  gap: 12px;
}

.subcategory-children {
  padding-left: clamp(10px, 2.4vw, 28px);
  border-left: 1px dashed rgba(159, 182, 214, 0.24);
}

.skill-node {
  position: relative;
  min-height: 166px;
  padding: 16px;
  border: 1px solid rgba(143, 208, 173, 0.14);
  border-radius: var(--qh-radius-card-sm);
  background: var(--qh-card-fill);
  box-shadow: var(--qh-inner-highlight);
  cursor: pointer;
  transition:
    transform 180ms ease,
    border-color 180ms ease,
    background 180ms ease,
    opacity 180ms ease;
}

.skill-node:hover {
  transform: translateY(-2px);
  border-color: rgba(231, 194, 116, 0.38);
}

.skill-node.is-locked {
  opacity: 0.62;
}

.skill-node.is-locked:hover {
  border-color: rgba(238, 245, 239, 0.12);
}

.skill-node.is-focused {
  border-color: rgba(231, 194, 116, 0.72);
  animation: glow 1.6s ease-in-out 2;
}

.subcategory-node {
  min-height: 154px;
}

.leaf-node {
  min-height: 190px;
}

.skill-top,
.skill-footer,
.skill-meta,
.node-icons {
  display: flex;
  align-items: center;
  gap: 10px;
}

.skill-top,
.skill-footer {
  justify-content: space-between;
}

.skill-footer {
  margin-top: 14px;
}

.status-icon,
.toggle-icon {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  border-radius: 12px;
  color: var(--qh-color-jade);
  background: rgba(238, 245, 239, 0.06);
}

.toggle-icon {
  color: var(--qh-color-gold);
  transition: transform 180ms ease;
}

.toggle-icon.is-open {
  transform: rotate(90deg);
}

.node-content {
  margin-top: 14px;
}

.kind-label {
  margin: 0 0 8px;
  color: var(--qh-color-blue);
  font-size: 12px;
  font-weight: 900;
}

.node-content > p:last-child {
  display: -webkit-box;
  min-height: 42px;
  margin: 9px 0 0;
  overflow: hidden;
  color: var(--qh-text-muted);
  font-size: 13px;
  line-height: 1.65;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.skill-meta {
  flex-wrap: wrap;
  color: var(--qh-text-muted);
  font-size: 13px;
  line-height: 1.65;
}

.resource-button {
  flex: 0 0 auto;
}

.kind-category {
  border-color: rgba(231, 194, 116, 0.28);
}

.kind-subcategory {
  border-color: rgba(143, 208, 173, 0.22);
}

.kind-herb {
  border-color: rgba(143, 208, 173, 0.16);
}

.kind-differentiation {
  border-color: rgba(159, 182, 214, 0.25);
}

.is-mastered {
  border-color: rgba(143, 208, 173, 0.36);
}

.is-in_progress {
  border-color: rgba(231, 194, 116, 0.3);
}

.is-unlocked {
  border-color: rgba(159, 182, 214, 0.24);
}

.list-section,
.diff-section {
  display: grid;
  gap: 12px;
}

.section-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 12px;
}

.section-head span {
  color: var(--qh-text-muted);
  font-size: 12px;
  font-weight: 900;
}

.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
  gap: 12px;
}

.diff-toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 54px;
  width: 100%;
  padding: 10px 14px;
  border: 1px solid rgba(159, 182, 214, 0.2);
  border-radius: var(--qh-radius-card-sm);
  color: var(--qh-text);
  background: rgba(238, 245, 239, 0.045);
  cursor: pointer;
  font-family: var(--qh-font-display);
  font-size: 15px;
  font-weight: 900;
  text-align: left;
}

.diff-toggle > span {
  display: inline-flex;
  align-items: center;
  gap: 10px;
}

.more-button {
  justify-self: center;
}

.tree-expand-enter-active,
.tree-expand-leave-active {
  overflow: hidden;
  transition:
    opacity 180ms ease,
    transform 180ms ease,
    max-height 220ms ease;
}

.tree-expand-enter-from,
.tree-expand-leave-to {
  max-height: 0;
  opacity: 0;
  transform: translateY(-6px);
}

.tree-expand-enter-to,
.tree-expand-leave-from {
  max-height: 4200px;
  opacity: 1;
  transform: translateY(0);
}

@media (max-width: 1180px) {
  .path-hero,
  .path-controls,
  .overview-row {
    grid-template-columns: 1fr;
  }

  .button-row {
    justify-content: flex-start;
  }
}

@media (max-width: 760px) {
  .path-hero {
    padding: 18px;
  }

  .generation-strip,
  .skill-footer,
  .section-head {
    align-items: stretch;
    grid-template-columns: 1fr;
  }

  .skill-footer,
  .section-head {
    display: grid;
  }

  .category-children,
  .subcategory-children {
    padding-left: 10px;
  }

  .card-grid {
    grid-template-columns: 1fr;
  }
}
</style>
