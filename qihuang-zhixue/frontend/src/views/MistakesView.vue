<template>
  <section v-loading="learning.loadingPath" class="mistakes-view">
    <div class="mistakes-hero">
      <div>
        <p class="eyebrow">错题本</p>
        <h2>薄弱回炉</h2>
        <p>先看图谱能不能说清「为什么错」，再看 BKT 掌握度与 FSRS 到期项。</p>
      </div>
      <el-button type="primary" :icon="Refresh" @click="refreshAll">刷新错题</el-button>
    </div>

    <el-alert
      v-if="learning.error || attributionError"
      class="mistakes-alert"
      type="warning"
      :title="learning.error || attributionError"
      show-icon
      :closable="false"
    />

    <!-- 归因：每条错题落成图上的一条事实，或者老实说归不了。 -->
    <section class="attribution-section">
      <GlassCard padding="lg">
        <div class="panel-head">
          <div>
            <p class="eyebrow">Attribution</p>
            <h3>错因归因</h3>
          </div>
          <Badge :tone="attribution?.count ? 'jade' : 'muted'" :label="attribution?.count ?? 0" />
        </div>

        <p v-if="attributionLoading" class="empty-line">正在对照图谱核查错因…</p>

        <template v-else-if="attribution?.count">
          <div class="stat-row">
            <div v-for="stat in stats" :key="stat.key" class="stat-card" :class="`is-${stat.tone}`">
              <span>{{ stat.label }}</span>
              <b>{{ stat.value }}</b>
              <small>{{ stat.hint }}</small>
            </div>
          </div>

          <div v-if="domains.length" class="domain-row">
            <span class="domain-label">错题集中章节</span>
            <span v-for="item in domains" :key="item.name" class="domain-chip">
              {{ item.name }}<b>{{ item.count }}</b>
            </span>
          </div>

          <div class="cause-list">
            <article v-for="item in attribution.items" :key="item.mistake_id" class="cause-item">
              <header>
                <div>
                  <strong>{{ item.exam_point.name || item.mistake_id }}</strong>
                  <small v-if="item.entity">图谱实体 · {{ item.entity.name }}</small>
                  <small v-else>未进入图谱</small>
                </div>
                <Badge v-for="cause in item.causes" :key="cause.cause_type" :tone="toneOf(cause.cause_type)" :label="cause.label" />
              </header>

              <p v-for="(cause, index) in item.causes" :key="`${cause.cause_type}-${index}`" class="cause-copy">
                {{ cause.explanation }}
              </p>

              <blockquote v-if="item.causes[0]?.quote" class="cause-quote">
                <p>{{ item.causes[0].quote }}</p>
                <small>{{ item.causes[0].ref || item.causes[0].relation_type }}</small>
              </blockquote>

              <div v-if="item.remediation?.node_path.length" class="node-path">
                <span v-for="(node, index) in item.remediation.node_path" :key="node.id">
                  <i v-if="index">›</i>{{ node.name }}
                </span>
              </div>

              <div class="cause-actions">
                <el-button
                  v-if="confusedWith(item)"
                  size="small"
                  :icon="Connection"
                  @click="openCompare(item)"
                >
                  和「{{ confusedWith(item) }}」对比
                </el-button>
                <el-button
                  v-if="item.remediation?.focus_node_id"
                  size="small"
                  :icon="Share"
                  @click="openAtlas(item)"
                >
                  在图谱里回看
                </el-button>
              </div>

              <el-alert
                v-for="note in item.notes"
                :key="note"
                type="info"
                :title="note"
                show-icon
                :closable="false"
                class="note-alert"
              />
            </article>
          </div>

          <p v-if="attribution.summary.unattributed" class="empty-line">
            另有 {{ attribution.summary.unattributed }} 题图谱无法归因——这些不会被算进任何章节，
            免得「归因不了」被读成「这章掌握得还行」。
          </p>
        </template>

        <p v-else class="empty-line">还没有错题记录。做完测评后，这里会逐题告诉你错在哪、依据教材哪一句。</p>
      </GlassCard>
    </section>

    <section class="mistakes-grid">
      <GlassCard padding="lg">
        <div class="panel-head">
          <div>
            <p class="eyebrow">Weak Points</p>
            <h3>薄弱知识点</h3>
          </div>
          <Badge tone="coral" :label="weakNodes.length" />
        </div>

        <div class="mistake-list">
          <article v-for="node in weakNodes" :key="node.id">
            <div>
              <strong>{{ node.title }}</strong>
              <p>{{ node.reason }}</p>
              <span>掌握 {{ Math.round(node.mastery * 100) }}% · 置信 {{ Math.round(node.confidence * 100) }}%</span>
            </div>
            <el-button size="small" :icon="MagicStick" @click="goLearn(node.title)">重练</el-button>
          </article>
          <p v-if="!weakNodes.length" class="empty-line">暂无明确错题，完成测评后会自动沉淀。</p>
        </div>
      </GlassCard>

      <GlassCard padding="lg">
        <div class="panel-head">
          <div>
            <p class="eyebrow">Due Again</p>
            <h3>需复现题</h3>
          </div>
          <Badge tone="gold" :label="dueItems.length" />
        </div>

        <div class="due-list">
          <article v-for="item in dueItems" :key="item.kc_id">
            <strong>{{ item.kc_id }}</strong>
            <p>上次评级：{{ item.last_rating }} · 紧迫度 {{ Math.round(item.urgency * 100) }}%</p>
          </article>
          <p v-if="!dueItems.length" class="empty-line">今天没有需要复现的错题。</p>
        </div>
      </GlassCard>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Connection, MagicStick, Refresh, Share } from '@element-plus/icons-vue'
import Badge from '../components/ui/Badge.vue'
import GlassCard from '../components/ui/GlassCard.vue'
import { getMyAttribution } from '../services/kgApi'
import { useLearningStore } from '../stores/learning'
import type { AttributionResponse, CauseType, MistakeAttribution } from '../types/kg'

const router = useRouter()
const learning = useLearningStore()

const attribution = ref<AttributionResponse | null>(null)
const attributionLoading = ref(false)
const attributionError = ref('')

const weakNodes = computed(() =>
  learning.skillTree
    .filter((node) => node.status !== 'mastered' && node.mastery < 0.55)
    .slice(0, 12),
)
const dueItems = computed(() => learning.reviews?.items ?? [])

const domains = computed(() =>
  Object.entries(attribution.value?.summary.by_domain ?? {}).map(([name, count]) => ({ name, count })),
)

const stats = computed(() => {
  const summary = attribution.value?.summary
  if (!summary) return []
  const byCause = summary.by_cause ?? {}
  return [
    { key: 'attributed', label: '可归因', value: summary.attributed, hint: '图谱能给依据', tone: 'jade' },
    { key: 'unattributed', label: '无法归因', value: summary.unattributed, hint: '不摊进章节', tone: 'muted' },
    { key: 'confused', label: '概念混淆', value: byCause.confused_with ?? 0, hint: '教材写了鉴别点', tone: 'coral' },
    { key: 'wrongHerb', label: '选错药', value: byCause.wrong_herb ?? 0, hint: '按功效对照', tone: 'blue' },
    { key: 'forbidden', label: '禁忌错认', value: byCause.forbidden_pair ?? 0, hint: '十八反/十九畏', tone: 'gold' },
  ]
})

const toneOf = (cause: CauseType) =>
  ({
    confused_with: 'coral',
    forbidden_pair: 'gold',
    wrong_herb: 'blue',
    answer_incomplete: 'jade',
    unknown: 'muted',
  })[cause] as 'coral' | 'gold' | 'blue' | 'jade' | 'muted'

/** 混淆对象：指向另一味具体药的归因才有——概念混淆、禁忌错认、选错药。 */
const confusedWith = (item: MistakeAttribution) => {
  const cause = item.causes.find(
    (entry) =>
      (entry.cause_type === 'confused_with' ||
        entry.cause_type === 'forbidden_pair' ||
        entry.cause_type === 'wrong_herb') &&
      entry.entity_name,
  )
  return cause?.entity_name ?? ''
}

const goLearn = (topic: string) => {
  void router.push({ name: 'learn', query: { topic } })
}

const openCompare = (item: MistakeAttribution) => {
  void router.push({
    name: 'compare',
    query: { left: item.exam_point.name || item.entity?.name || '', right: confusedWith(item) },
  })
}

const openAtlas = (item: MistakeAttribution) => {
  void router.push({ name: 'atlas', query: { focus: item.remediation?.focus_node_id ?? '' } })
}

async function loadAttribution() {
  attributionLoading.value = true
  attributionError.value = ''
  try {
    attribution.value = await getMyAttribution(20)
  } catch (error) {
    // 归因接口挂了不该连带把整页的薄弱知识点也弄成报错，单独提示。
    attributionError.value = error instanceof Error ? error.message : '错因归因加载失败'
    attribution.value = null
  } finally {
    attributionLoading.value = false
  }
}

function refreshAll() {
  void learning.loadPath()
  void loadAttribution()
}

onMounted(() => {
  void learning.loadPath()
  void loadAttribution()
})
</script>

<style scoped lang="scss">
@use "../styles/tokens.scss" as *;

.mistakes-view {
  display: grid;
  gap: 18px;
  width: min(1320px, 100%);
  margin: 0 auto;
}

.mistakes-hero {
  @include glass-panel;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 24px;
}

.eyebrow {
  margin: 0 0 8px;
  color: var(--qh-color-jade);
  font-size: 13px;
  font-weight: 900;
}

h2,
h3 {
  margin: 0;
  font-family: var(--qh-font-display);
  font-weight: 900;
}

h2 {
  @include text-sheen;
  font-size: clamp(36px, 5vw, 62px);
}

h3 {
  font-size: 22px;
}

.mistakes-hero p,
.empty-line {
  margin: 10px 0 0;
  color: var(--qh-text-muted);
  line-height: 1.7;
}

.mistakes-alert {
  border-radius: 16px;
}

.attribution-section {
  display: grid;
}

.panel-head {
  display: flex;
  justify-content: space-between;
  /* 不加这一行 Badge 会被 flex 拉成和标题块一样高（实测 59px），
     渲染成一根竖着的长药丸。Badge 是 inline-flex 且没有固定高度。 */
  align-items: center;
  gap: 14px;
  margin-bottom: 16px;
}

.stat-row {
  display: grid;
  /* 五张卡：auto-fit 让窄屏自动折行，不用为「多了一类」再改栅格。 */
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 12px;
}

.stat-card {
  display: grid;
  gap: 4px;
  padding: 14px;
  border: 1px solid var(--qh-line);
  border-radius: 16px;
  background: var(--qh-line);

  span {
    color: var(--qh-text-muted);
    font-size: 12px;
    font-weight: 900;
  }

  b {
    font-family: var(--qh-font-display);
    font-size: 28px;
    font-weight: 900;
  }

  small {
    color: var(--qh-text-muted);
    font-size: 12px;
  }

  &.is-jade b {
    color: var(--qh-fg-soft);
  }

  &.is-coral b {
    color: var(--qh-accent);
  }

  &.is-gold b {
    color: var(--qh-color-gold);
  }

  &.is-blue b {
    color: var(--qh-slate-300);
  }

  &.is-muted b {
    color: var(--qh-text-muted);
  }
}

.domain-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-top: 16px;
}

.domain-label {
  color: var(--qh-text-muted);
  font-size: 12px;
  font-weight: 900;
}

.domain-chip {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  padding: 4px 11px;
  border: 1px solid var(--qh-line);
  border-radius: var(--qh-radius-pill);
  color: var(--qh-fg-soft);
  font-size: 13px;

  b {
    color: var(--qh-color-jade);
  }
}

.cause-list {
  display: grid;
  gap: 12px;
  margin-top: 16px;
}

.cause-item {
  display: grid;
  gap: 10px;
  padding: 16px;
  border: 1px solid var(--qh-line);
  border-radius: 16px;

  header {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
    justify-content: space-between;
  }

  header > div {
    display: grid;
    gap: 3px;
  }

  strong {
    color: var(--qh-text);
    font-size: 16px;
    font-weight: 900;
  }

  header small {
    color: var(--qh-text-muted);
    font-size: 12px;
  }
}

.cause-copy {
  margin: 0;
  color: var(--qh-text);
  font-size: 14px;
  line-height: 1.75;
}

.cause-quote {
  margin: 0;
  padding: 12px 14px;
  border-left: 3px solid var(--qh-color-jade);
  border-radius: 0 14px 14px 0;
  background: var(--qh-line);

  p {
    margin: 0;
    color: var(--qh-fg-soft);
    font-size: 14px;
    line-height: 1.75;
  }

  small {
    display: block;
    margin-top: 8px;
    color: var(--qh-text-muted);
    font-size: 12px;
  }
}

.node-path {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  color: var(--qh-text-muted);
  font-size: 13px;

  i {
    margin-right: 6px;
    font-style: normal;
    opacity: 0.6;
  }
}

.cause-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.note-alert {
  border-radius: 14px;
}

.mistakes-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.12fr) minmax(340px, 0.88fr);
  gap: 18px;
}

.mistake-list,
.due-list {
  display: grid;
  gap: 12px;
}

.mistake-list article,
.due-list article {
  padding: 14px;
  border: 1px solid var(--qh-line);
  border-radius: 16px;
  background: var(--qh-line);
}

.mistake-list article {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.mistake-list strong,
.due-list strong {
  color: var(--qh-text);
  font-weight: 900;
}

.mistake-list p,
.mistake-list span,
.due-list p {
  display: block;
  margin: 7px 0 0;
  color: var(--qh-text-muted);
  font-size: 13px;
  line-height: 1.65;
}

@media (max-width: 960px) {
  .mistakes-hero,
  .mistakes-grid,
  .stat-row {
    display: grid;
    grid-template-columns: 1fr;
  }
}
</style>
