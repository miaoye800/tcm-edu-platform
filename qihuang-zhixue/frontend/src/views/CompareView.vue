<template>
  <section class="compare-view">
    <header class="compare-hero">
      <div>
        <p class="eyebrow">图谱 · 对比</p>
        <h2>两药异同</h2>
        <p>逐项结构差异 + 教材明文鉴别句。不调模型——差异是可枚举的，教材本身也写了鉴别句。</p>
      </div>
    </header>

    <GlassCard padding="lg">
      <div class="picker-row">
        <el-select
          v-model="leftId"
          class="picker"
          filterable
          remote
          clearable
          reserve-keyword
          :remote-method="(q: string) => searchSide('left', q)"
          :loading="searching.left"
          placeholder="左：药名 / 别名 / 拼音"
        >
          <el-option v-for="item in options.left" :key="item.id" :label="item.name" :value="item.id">
            <span>{{ item.name }}</span>
            <span class="option-kind">{{ kindLabel(item.kind) }}</span>
          </el-option>
        </el-select>

        <span class="vs">对比</span>

        <el-select
          v-model="rightId"
          class="picker"
          filterable
          remote
          clearable
          reserve-keyword
          :remote-method="(q: string) => searchSide('right', q)"
          :loading="searching.right"
          placeholder="右：药名 / 别名 / 拼音"
        >
          <el-option v-for="item in options.right" :key="item.id" :label="item.name" :value="item.id">
            <span>{{ item.name }}</span>
            <span class="option-kind">{{ kindLabel(item.kind) }}</span>
          </el-option>
        </el-select>

        <el-button type="primary" :loading="loading" :disabled="!leftId || !rightId" @click="run()">
          开始对比
        </el-button>
      </div>

      <div class="quick-pairs">
        <span>常用：</span>
        <button v-for="pair in QUICK_PAIRS" :key="`${pair[0]}-${pair[1]}`" @click="usePair(pair)">
          {{ pair[0] }} / {{ pair[1] }}
        </button>
      </div>
    </GlassCard>

    <el-alert v-if="error" type="warning" :title="error" show-icon :closable="false" class="compare-alert" />

    <template v-if="result">
      <!-- ok:false 不是错误：药没进图谱时只有身份没有性味功效，硬凑一张表等于编。 -->
      <el-alert
        v-if="!result.ok"
        type="info"
        :title="result.reason || '无法逐项对比'"
        show-icon
        :closable="false"
        class="compare-alert"
      />

      <GlassCard v-if="result.constraints?.length" padding="lg" class="constraint-card">
        <div class="panel-head">
          <div>
            <p class="eyebrow danger">T1 硬约束</p>
            <h3>这两味不能一起用</h3>
          </div>
          <Badge tone="coral" :label="result.constraints.length" />
        </div>
        <blockquote v-for="item in result.constraints" :key="item.edge_id">
          <b>{{ item.relation_type }}</b>
          <p>{{ item.quote }}</p>
          <small v-if="item.page">第 {{ item.page }} 页</small>
        </blockquote>
      </GlassCard>

      <GlassCard v-if="result.textbook_differentiation" padding="lg">
        <div class="panel-head">
          <div>
            <p class="eyebrow">教材原文</p>
            <h3>鉴别要点</h3>
          </div>
          <Badge tone="gold" label="原文" />
        </div>
        <blockquote class="textbook-quote">
          <p>{{ result.textbook_differentiation.quote }}</p>
          <small>{{ result.textbook_differentiation.ref }}</small>
        </blockquote>
      </GlassCard>

      <GlassCard v-if="result.differences?.length" padding="lg">
        <div class="panel-head">
          <div>
            <p class="eyebrow">Structure</p>
            <h3>逐项差异</h3>
          </div>
          <span class="head-note">{{ leftName }} · {{ rightName }}</span>
        </div>

        <div class="diff-table">
          <div class="diff-head">
            <span>维度</span>
            <span>{{ leftName }}</span>
            <span>{{ rightName }}</span>
          </div>
          <div v-for="row in visibleDifferences" :key="row.field" class="diff-row">
            <span class="diff-label">{{ row.label }}</span>
            <div class="diff-cell">
              <span
                v-for="value in row.left"
                :key="`l-${value}`"
                class="chip"
                :class="{ 'is-only': row.only_left.includes(value), 'is-shared': row.shared.includes(value) }"
              >{{ value }}</span>
              <span v-if="!row.left.length" class="chip is-empty">—</span>
            </div>
            <div class="diff-cell">
              <span
                v-for="value in row.right"
                :key="`r-${value}`"
                class="chip"
                :class="{ 'is-only': row.only_right.includes(value), 'is-shared': row.shared.includes(value) }"
              >{{ value }}</span>
              <span v-if="!row.right.length" class="chip is-empty">—</span>
            </div>
          </div>
        </div>
        <p class="legend-note">高亮 = 只有这味药有；灰底 = 两者共有。</p>
        <p v-if="result.left?.source || result.right?.source" class="source-note">
          出处：{{ leftName }} — {{ result.left?.source?.ref || '教材未收录' }}；
          {{ rightName }} — {{ result.right?.source?.ref || '教材未收录' }}
        </p>
      </GlassCard>

      <GlassCard v-if="result.shared_relations?.length" padding="lg">
        <div class="panel-head">
          <div>
            <p class="eyebrow">Shared</p>
            <h3>共同关联</h3>
          </div>
          <Badge :label="result.shared_relations.length" />
        </div>
        <p class="head-note">它们都和谁有关系，往往比「它们彼此什么关系」更能说明问题。</p>
        <div class="shared-list">
          <button
            v-for="item in result.shared_relations"
            :key="item.edge_id"
            class="shared-chip"
            @click="jumpTo(item.name)"
          >
            <b>{{ item.name }}</b>
            <small>{{ item.relation_type }}</small>
          </button>
        </div>
      </GlassCard>

      <GlassCard v-if="result.summary" padding="lg">
        <div class="panel-head">
          <div>
            <p class="eyebrow">Summary</p>
            <h3>一句话差别</h3>
          </div>
        </div>
        <p class="summary">{{ result.summary }}</p>
      </GlassCard>

      <!-- 关系链：两味药在图里到底怎么连着。3 跳以内查不到就说「教材没写这条关系」，
           不是「没关系」——两者差别很大，别混。 -->
      <GlassCard v-if="pathResult" padding="lg">
        <div class="panel-head">
          <div>
            <p class="eyebrow">Path</p>
            <h3>它们在图里怎么连着</h3>
          </div>
          <Badge v-if="pathResult.found" tone="jade" :label="`${pathResult.hops} 跳`" />
          <Badge v-else tone="muted" label="无路径" />
        </div>

        <template v-if="pathResult.found">
          <p class="path-sentence">{{ pathResult.sentence }}</p>
          <ol class="path-steps">
            <li v-for="step in pathResult.path" :key="step.edge_id">
              <span class="path-hop">
                <b>{{ step.from.name }}</b>
                <em>{{ step.relation_type }}</em>
                <b>{{ step.to.name }}</b>
              </span>
              <small v-if="step.evidence?.[0]?.quote">
                <!-- 页码可能为空（T2 边只有 100 条写了 page）：没有就不显示「第 undefined 页」。 -->
                <template v-if="step.evidence[0].page">第 {{ step.evidence[0].page }} 页 · </template>{{ step.evidence[0].quote }}
              </small>
            </li>
          </ol>
        </template>
        <p v-else class="empty-line">{{ pathResult.reason }}</p>
      </GlassCard>
    </template>

    <GlassCard v-else-if="!loading" padding="lg">
      <p class="empty-line">选两味药开始对比。教材里有鉴别句的，会直接引原文。</p>
    </GlassCard>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Badge from '../components/ui/Badge.vue'
import GlassCard from '../components/ui/GlassCard.vue'
import { compareHerbs, findKgPath, searchKgEntities } from '../services/kgApi'
import type { CompareResult, KgEntityKind, KgPathResult, KgSearchHit } from '../types/kg'

const route = useRoute()
const router = useRouter()

/** 常用药对只是**入口快捷方式**，不参与判定——判定全走接口。 */
const QUICK_PAIRS: [string, string][] = [
  ['麻黄', '桂枝'],
  ['人参', '党参'],
  ['石膏', '知母'],
  ['黄芪', '白术'],
]

const leftId = ref('')
const rightId = ref('')
const loading = ref(false)
const error = ref('')
const result = ref<CompareResult | null>(null)
/** 关系链是**补充信息**：查不到（或超时）就不显示这一块，不影响对比表本身。 */
const pathResult = ref<KgPathResult | null>(null)
const searching = reactive({ left: false, right: false })
const options = reactive<{ left: KgSearchHit[]; right: KgSearchHit[] }>({ left: [], right: [] })

const kindLabel = (kind: KgEntityKind) => ({ herb: '药', formula: '方', syndrome: '证' })[kind] ?? kind

const leftName = computed(() => nameOf(leftId.value, 'left'))
const rightName = computed(() => nameOf(rightId.value, 'right'))

/** 选中项的显示名。从已加载的选项里找，找不到就退回 id（不猜名字）。 */
function nameOf(id: string, side: 'left' | 'right'): string {
  return options[side].find((item) => item.id === id)?.name || id
}

/**
 * 只把「有差异」的维度留在主表里。
 *
 * 性味归经全同的两味药极少见，但一旦出现，七行「完全一致」会把真正的差别埋掉。
 * 毒性、用量这类**即使相同也必须看**的维度例外——它们影响能不能开方。
 */
const ALWAYS_SHOW = new Set(['toxicity', 'dosage'])
const visibleDifferences = computed(() =>
  (result.value?.differences ?? []).filter(
    (row) => row.only_left.length || row.only_right.length || ALWAYS_SHOW.has(row.field),
  ),
)

async function searchSide(side: 'left' | 'right', query: string) {
  const q = query.trim()
  if (!q) {
    options[side] = []
    return
  }
  searching[side] = true
  try {
    const payload = await searchKgEntities(q)
    options[side] = payload.items
  } catch (err) {
    error.value = err instanceof Error ? err.message : '搜索失败'
  } finally {
    searching[side] = false
  }
}

async function run(pair?: [string, string]) {
  const left = pair ? pair[0] : leftId.value
  const right = pair ? pair[1] : rightId.value
  if (!left || !right) return
  if (left === right) {
    error.value = '选了两味一样的药——对比不出差异。'
    result.value = null
    pathResult.value = null
    return
  }
  loading.value = true
  error.value = ''
  pathResult.value = null
  try {
    const payload = await compareHerbs(left, right)
    result.value = payload
    // 地址栏带上参数，方便把一次对比直接分享出去/刷新后还在。
    void router.replace({ query: { left, right } })
    void loadPath(payload)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '对比失败'
    result.value = null
  } finally {
    loading.value = false
  }
}

/**
 * 查两实体之间的最短关系链（≤3 跳，与方案 §P6 一致）。
 *
 * 走 ``/api/kg/path``：纯图算法、不调模型、毫秒级。查不到是**正常结果**
 * （教材没写这条关系），要把 ``reason`` 显示出来，而不是吞掉。
 */
async function loadPath(payload: CompareResult) {
  if (!payload.ok || !payload.left || !payload.right) return
  try {
    pathResult.value = await findKgPath(payload.left.id, payload.right.id, 3)
  } catch {
    // 关系链失败不影响已经拿到的对比结果，静默跳过。
    pathResult.value = null
  }
}

function usePair(pair: [string, string]) {
  leftId.value = pair[0]
  rightId.value = pair[1]
  options.left = [{ id: pair[0], kind: 'herb', name: pair[0], status: 'approved', matched_by: 'name' }]
  options.right = [{ id: pair[1], kind: 'herb', name: pair[1], status: 'approved', matched_by: 'name' }]
  void run(pair)
}

/** 共同关联点进去，以它为左、当前左为右再比一次——顺着关系走，不用重新输入。 */
function jumpTo(name: string) {
  leftId.value = name
  options.left = [{ id: name, kind: 'herb', name, status: 'approved', matched_by: 'name' }]
  void run([name, rightName.value])
}

onMounted(() => {
  const { left, right } = route.query
  if (typeof left === 'string' && left) leftId.value = left
  if (typeof right === 'string' && right) rightId.value = right
  if (leftId.value && rightId.value) {
    // 深链进来时选项还没加载，先把显示名补上，避免下拉框里显示 id。
    options.left = [{ id: leftId.value, kind: 'herb', name: leftId.value, status: 'approved', matched_by: 'name' }]
    options.right = [{ id: rightId.value, kind: 'herb', name: rightId.value, status: 'approved', matched_by: 'name' }]
    void run()
  }
})
</script>

<style scoped lang="scss">
@use "../styles/tokens.scss" as *;

.compare-view {
  display: grid;
  gap: 18px;
  width: min(1320px, 100%);
  margin: 0 auto;
}

.compare-hero {
  @include glass-panel;
  padding: 24px;
}

.eyebrow {
  margin: 0 0 8px;
  color: var(--qh-color-jade);
  font-size: 13px;
  font-weight: 900;

  &.danger {
    color: var(--qh-accent);
  }
}

h2,
h3 {
  margin: 0;
  font-family: var(--qh-font-display);
  font-weight: 900;
}

h2 {
  @include text-sheen;
  font-size: clamp(34px, 4.6vw, 56px);
}

h3 {
  font-size: 22px;
}

.compare-hero p {
  margin: 10px 0 0;
  color: var(--qh-text-muted);
  line-height: 1.7;
}

.compare-alert {
  border-radius: 16px;
}

.picker-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr) auto;
  gap: 12px;
  align-items: center;
}

.picker {
  width: 100%;
}

.option-kind {
  float: right;
  color: var(--qh-text-muted);
  font-size: 12px;
}

.vs {
  color: var(--qh-text-muted);
  font-size: 13px;
  font-weight: 900;
}

.quick-pairs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-top: 14px;
  color: var(--qh-text-muted);
  font-size: 13px;

  button {
    padding: 5px 11px;
    border: 1px solid var(--qh-line);
    border-radius: var(--qh-radius-pill);
    background: transparent;
    color: var(--qh-fg-soft);
    font-size: 13px;
    cursor: pointer;

    &:hover {
      border-color: var(--qh-color-jade);
    }
  }
}

.panel-head {
  display: flex;
  justify-content: space-between;
  /* 同 MistakesView：不加 align-items，Badge 会被拉成和标题块等高。 */
  align-items: center;
  gap: 14px;
  margin-bottom: 14px;
}

.head-note {
  color: var(--qh-text-muted);
  font-size: 13px;
}

.constraint-card blockquote {
  margin: 0 0 10px;
  padding: 14px;
  border-left: 3px solid var(--qh-accent);
  border-radius: 0 14px 14px 0;
  background: var(--qh-accent-soft);

  b {
    color: var(--qh-accent);
    font-size: 13px;
    font-weight: 900;
  }

  p {
    margin: 8px 0 0;
    color: var(--qh-text);
    line-height: 1.75;
  }

  small {
    display: block;
    margin-top: 8px;
    color: var(--qh-text-muted);
  }
}

.textbook-quote {
  margin: 0;
  padding: 16px;
  border: 1px solid var(--qh-line);
  border-radius: 16px;
  background: var(--qh-line);

  p {
    margin: 0;
    color: var(--qh-text);
    font-size: 16px;
    line-height: 1.85;
  }

  small {
    display: block;
    margin-top: 10px;
    color: var(--qh-text-muted);
    font-size: 12px;
  }
}

.diff-table {
  display: grid;
  gap: 2px;
}

.diff-head,
.diff-row {
  display: grid;
  grid-template-columns: 96px minmax(0, 1fr) minmax(0, 1fr);
  gap: 12px;
  align-items: start;
}

.diff-head {
  padding-bottom: 8px;
  color: var(--qh-text-muted);
  font-size: 12px;
  font-weight: 900;
}

.diff-row {
  padding: 12px 0;
  border-top: 1px solid var(--qh-line);
}

.diff-label {
  color: var(--qh-fg-soft);
  font-size: 13px;
  font-weight: 900;
}

.diff-cell {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.chip {
  padding: 3px 9px;
  border: 1px solid var(--qh-line);
  border-radius: var(--qh-radius-pill);
  color: var(--qh-text-muted);
  font-size: 13px;

  &.is-only {
    border-color: var(--qh-color-jade-line);
    background: var(--qh-color-jade-wash);
    color: var(--qh-fg);
    font-weight: 900;
  }

  /* 共有项压暗。原来是 0.72——实测在浅色主题下和「独有」几乎分不出来，
     而这张表的全部意义就是看差异，所以再压一档。 */
  &.is-shared {
    opacity: 0.58;
  }

  &.is-empty {
    border-style: dashed;
    opacity: 0.5;
  }
}

.legend-note,
.empty-line {
  margin: 12px 0 0;
  color: var(--qh-text-muted);
  font-size: 13px;
}

/* 出处一行要能一眼看出「这张表不是我编的」，但不能抢差异表的注意力。 */
.source-note {
  margin: 8px 0 0;
  color: var(--qh-text-muted);
  font-size: 12px;
  line-height: 1.8;
  opacity: 0.85;
}

.shared-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.shared-chip {
  display: grid;
  gap: 2px;
  padding: 8px 12px;
  border: 1px solid var(--qh-line);
  border-radius: 14px;
  background: transparent;
  color: var(--qh-text);
  text-align: left;
  cursor: pointer;

  b {
    font-weight: 900;
  }

  small {
    color: var(--qh-text-muted);
    font-size: 12px;
  }

  &:hover {
    border-color: var(--qh-color-jade);
  }
}

.summary {
  margin: 0;
  color: var(--qh-text);
  font-size: 15px;
  line-height: 1.85;
}

/* 关系链：一行一步，箭头方向就是图谱里边的方向（药→方、方→证不混）。 */
.path-sentence {
  margin: 0 0 12px;
  padding: 12px 14px;
  border: 1px solid var(--qh-line);
  border-radius: 14px;
  background: var(--qh-line);
  color: var(--qh-text);
  font-size: 14px;
  line-height: 1.8;
  word-break: break-all;
}

.path-steps {
  display: grid;
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;

  li {
    display: grid;
    gap: 4px;
    padding-left: 12px;
    border-left: 2px solid var(--qh-color-jade-line);
  }

  small {
    color: var(--qh-text-muted);
    font-size: 12px;
    line-height: 1.7;
  }
}

.path-hop {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  color: var(--qh-text);
  font-size: 14px;

  b {
    font-weight: 900;
  }

  em {
    padding: 2px 8px;
    border-radius: var(--qh-radius-pill);
    background: var(--qh-color-jade-wash);
    color: var(--qh-color-jade);
    font-size: 12px;
    font-style: normal;
    font-weight: 900;
  }
}

@media (max-width: 960px) {
  .picker-row,
  .diff-head,
  .diff-row {
    grid-template-columns: 1fr;
  }
}
</style>
