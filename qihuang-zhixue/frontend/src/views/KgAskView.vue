<template>
  <section class="ask-view">
    <header class="ask-hero">
      <div>
        <p class="eyebrow">图谱 · 推理</p>
        <h2>图谱问答</h2>
        <p>
          普通检索找「相似的文本」，这里找「相连的事实」：先认出问题里的药材/方剂/证候，
          再沿图谱走一跳，把关系本身和教材原文一起取回来。默认不调大模型。
        </p>
      </div>
    </header>

    <GlassCard padding="lg">
      <div class="ask-row">
        <el-input
          v-model="question"
          class="ask-input"
          placeholder="例：麻黄和桂枝为什么常一起用"
          clearable
          @keyup.enter="run()"
        />
        <el-button type="primary" :loading="loading" :disabled="!question.trim()" @click="run()">
          提问
        </el-button>
      </div>

      <div class="ask-controls">
        <div class="quick-asks">
          <span>试试：</span>
          <button v-for="item in EXAMPLES" :key="item" @click="useExample(item)">{{ item }}</button>
        </div>
        <label class="expand-switch">
          <el-switch v-model="expand" />
          <span>图扩展</span>
          <em>{{ expand ? '沿关系补一跳' : '只认实体、不扩展（对照组）' }}</em>
        </label>
      </div>
    </GlassCard>

    <GlassCard padding="lg">
      <div class="panel-head">
        <div>
          <p class="eyebrow">图谱 · 反查</p>
          <h3>从一个症状往回找</h3>
        </div>
        <span class="stat">症 → 证 → 方 → 药</span>
      </div>
      <p class="panel-note">
        上面那栏是「认出了哪味药，然后看它连着什么」；这里反过来：给一个症状或证候，
        先查教材词表命中哪个证，再沿「证方链」找常用方、沿「药证链」和「组成」找药。
        全程查表，一步不生成——所以查不到时会明说查不到，而不是给一批看着像的证名。
      </p>

      <div class="ask-row">
        <el-input
          v-model="symptom"
          class="ask-input"
          placeholder="例：咽喉肿痛 / 气虚欲脱 / 食少便溏"
          clearable
          @keyup.enter="runReverse()"
        />
        <el-button :loading="reverseLoading" :disabled="!symptom.trim()" @click="runReverse()">
          反查
        </el-button>
      </div>

      <div class="quick-asks">
        <span>试试：</span>
        <button v-for="item in SYMPTOM_EXAMPLES" :key="item" @click="useSymptomExample(item)">{{ item }}</button>
      </div>

      <el-alert
        v-if="reverseError"
        type="warning"
        :title="reverseError"
        show-icon
        :closable="false"
        class="ask-alert"
      />

      <template v-if="reverse">
        <p v-if="!reverse.ok" class="panel-note warn">
          {{ reverse.reason }}<span v-if="reverse.hint"> {{ reverse.hint }}</span>
        </p>
        <ul v-else class="reverse-list">
          <li v-for="item in reverse.syndromes" :key="item.id">
            <div class="relation-head">
              <span class="tier">{{ layerLabel(item.layer) }}</span>
              <strong>{{ item.name }}</strong>
              <span class="arrow">证</span>
              <em v-if="!item.in_graph">未进图谱</em>
              <em v-else-if="!item.complete">路径不全</em>
            </div>
            <p v-if="item.note" class="panel-note warn">{{ item.note }}</p>
            <template v-else>
              <div class="hop-row">
                <b>药</b>
                <span v-for="hop in item.herbs" :key="`h-${hop.edge_id}`" :title="hop.evidence[0]?.quote || ''">
                  {{ hop.name }}
                </span>
                <em v-if="!item.herbs?.length">没有药证链</em>
                <small v-else-if="(item.herbs_total || 0) > (item.herbs?.length || 0)">
                  等 {{ item.herbs_total }} 味
                </small>
              </div>
              <div class="hop-row">
                <b>方</b>
                <span v-for="hop in item.formulas" :key="`f-${hop.edge_id}`">
                  {{ hop.name }}
                  <small v-if="hop.composition?.length">
                    （{{ hop.composition.map(pill => pill.name).join('、') }}）
                  </small>
                </span>
                <em v-if="!item.formulas?.length">没有证方链</em>
              </div>
              <p class="hop-note">
                命中依据：{{ item.matched_terms.join('、') || '—' }}
              </p>
            </template>
          </li>
        </ul>
      </template>
    </GlassCard>

    <el-alert v-if="error" type="warning" :title="error" show-icon :closable="false" class="ask-alert" />

    <template v-if="result">
      <GlassCard padding="lg">
        <div class="panel-head">
          <h3>识别到的起点</h3>
          <span class="stat">
            {{ result.graph.nodes.length }} 个节点 · {{ result.graph.edges.length }} 条关系 ·
            {{ result.citations.length }} 条原文
          </span>
        </div>
        <p v-if="!expand" class="panel-note warn">
          对照模式：只做了实体识别，没有沿图走一跳。所以下面没有关系、没有原文——
          这正是「普通检索」的处境：知道在说哪味药，但拿不出「它和谁、为什么」的依据。
        </p>
        <p v-if="!result.seeds.length" class="panel-note warn">
          问题里没有认出任何实体。图谱只能从实体出发——认不出起点就查不到东西，
          这时它说「没找到起点」，而不是编一段像模像样的分析。
        </p>
        <div v-else class="seed-row">
          <span v-for="seed in result.seeds" :key="seed.id" class="seed">
            {{ seed.name }}
            <em>{{ kindLabel(seed.kind) }} · {{ seed.matched_by }}</em>
          </span>
        </div>
        <p class="answer">{{ result.answer }}</p>
      </GlassCard>

      <GlassCard v-if="result.context.relations.length" padding="lg">
        <div class="panel-head">
          <h3>图里查到的事实</h3>
          <span class="stat">T1 硬约束 {{ result.context.constraints.length }} 条</span>
        </div>
        <p class="panel-note">
          每条都带教材出处。T1 是「不能一起用」的硬约束，T2 是配伍、方剂组成、药证链这类事实关系；
          T3（同功效/同归经的相似边）永远不会出现在这里——它回答不了「为什么」。
        </p>
        <ul class="relation-list">
          <li v-for="(item, index) in visibleRelations" :key="`${item.edge_id}-${index}`" :class="{ hard: item.tier === 1 }">
            <div class="relation-head">
              <span class="tier">{{ item.tier === 1 ? 'T1' : 'T2' }}</span>
              <strong>{{ item.source.name }}</strong>
              <span class="arrow">—{{ item.relation_type }}→</span>
              <strong>{{ item.target.name }}</strong>
              <em v-if="item.derived">推导</em>
            </div>
            <blockquote v-if="item.evidence[0]?.quote">
              {{ item.evidence[0].quote }}
              <footer>
                <span v-if="item.evidence[0].page">p{{ item.evidence[0].page }}</span>
                <span>{{ shortRef(item.evidence[0].ref) }}</span>
              </footer>
            </blockquote>
          </li>
        </ul>
        <p v-if="result.context.relations.length > VISIBLE_LIMIT" class="panel-note">
          只列出前 {{ VISIBLE_LIMIT }} 条，共 {{ result.context.relations.length }} 条。
        </p>
      </GlassCard>

      <GlassCard v-if="result.citations.length" padding="lg">
        <div class="panel-head">
          <h3>教材引用</h3>
          <span class="stat">去重后 {{ result.citations.length }} 条</span>
        </div>
        <p class="panel-note">
          每条引用都能按边 id 追回图里那条边，这也是 GraphRAG 和「让模型自由发挥」的分界线。
        </p>
        <ul class="citation-list">
          <li v-for="(item, index) in result.citations.slice(0, 12)" :key="`${item.edge_id}-${index}`">
            <div class="citation-head">
              <span class="tier">{{ item.tier === 1 ? 'T1' : 'T2' }}</span>
              <strong>{{ item.relation_type }}</strong>
              <span v-if="item.page" class="page">p{{ item.page }}</span>
              <code v-if="item.edge_id">#{item.edge_id}</code>
            </div>
            <p>{{ item.quote }}</p>
          </li>
        </ul>
      </GlassCard>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import GlassCard from '../components/ui/GlassCard.vue'
import { askKgGraph, reverseLookup } from '../services/kgApi'
import type { KgAskResult, KgEntityKind, KgReverseResult } from '../types/kg'

/** 示例只作入口，不参与判定。前三题是配伍理由，后两题是方剂与禁忌。 */
const EXAMPLES = ['麻黄和桂枝为什么常一起用', '麻黄汤由哪些药组成', '人参和藜芦能一起用吗', '黄芪能治什么']

/**
 * 反查示例挑的是**教材原话**，不是口语。
 *
 * 口语（「嗓子疼」「没劲儿」）不是不支持，而是它走的是别名层，命中层级会降一级；
 * 示例用教材词，是为了让人第一眼看到的是「这套东西能准确命中」，
 * 而不是先撞一次「查不到」再怀疑功能坏了。
 */
const SYMPTOM_EXAMPLES = ['气虚欲脱', '咽喉肿痛', '食少便溏', '恶寒发热']

/** 关系列表默认只铺前 25 条：一次扩展能带出上百条边，全铺开会把关键的那条埋掉。 */
const VISIBLE_LIMIT = 25

const question = ref(EXAMPLES[0])
const expand = ref(true)
const loading = ref(false)
const error = ref('')
const result = ref<KgAskResult | null>(null)

const symptom = ref(SYMPTOM_EXAMPLES[0])
const reverse = ref<KgReverseResult | null>(null)
const reverseLoading = ref(false)
const reverseError = ref('')

const kindLabel = (kind: KgEntityKind) => ({ herb: '药', formula: '方', syndrome: '证' })[kind] ?? kind

/** T1 排前面：能不能一起用比「配起来治什么」更要紧。 */
const visibleRelations = computed(() =>
  [...(result.value?.context.relations ?? [])]
    .sort((left, right) => left.tier - right.tier || (right.confidence ?? 0) - (left.confidence ?? 0))
    .slice(0, VISIBLE_LIMIT),
)

/** 出处字符串很长（书名+版次+页码+章节），列表里只留书名和章节。 */
function shortRef(ref?: string): string {
  if (!ref) return ''
  const parts = ref.split(/[·（(]/).filter(Boolean)
  return parts.slice(0, 2).join(' · ')
}

function useExample(item: string) {
  question.value = item
  run()
}

/**
 * 命中层级要显示出来，不能藏。
 *
 * 四级可靠性差得很远：``name`` 是证候名原样出现，``alias`` 是教材并列单项或
 * 口语别名，``phrase`` 是症状短语倒排，``fuzzy`` 只是字符二元组重叠的兜底。
 * 全写成「匹配成功」，用户会把最弱的那一级当成最强的那一级。
 */
function layerLabel(layer: string): string {
  return ({ name: '证名命中', alias: '别名', phrase: '症状短语', fuzzy: '字面近似' } as Record<string, string>)[layer] ?? layer
}

function useSymptomExample(item: string) {
  symptom.value = item
  runReverse()
}

async function runReverse() {
  const text = symptom.value.trim()
  if (!text) return
  reverseLoading.value = true
  reverseError.value = ''
  reverse.value = null
  try {
    reverse.value = await reverseLookup(text)
  } catch (err) {
    reverseError.value = err instanceof Error ? err.message : '反查失败'
  } finally {
    reverseLoading.value = false
  }
}

async function run() {
  const text = question.value.trim()
  if (!text) return
  loading.value = true
  error.value = ''
  result.value = null
  try {
    // 开关只影响「扩不扩展」，问题识别那一段两边完全一样——
    // 这样对比出来的差异才只归因于图扩展本身。
    result.value = await askKgGraph(text, { expand: expand.value })
  } catch (err) {
    error.value = err instanceof Error ? err.message : '请求失败'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped lang="scss">
@use "../styles/tokens.scss" as *;

.ask-view {
  display: grid;
  gap: 18px;
  width: min(1180px, 100%);
  margin: 0 auto;
}

.ask-hero {
  @include glass-panel;
  padding: 24px;
}

.eyebrow {
  margin: 0 0 8px;
  color: var(--qh-color-jade);
  font-size: 13px;
  font-weight: 900;
}

h2 {
  @include text-sheen;
  margin: 0;
  font-family: var(--qh-font-display);
  font-size: clamp(34px, 4.6vw, 56px);
  font-weight: 900;
}

h3 {
  margin: 0;
  font-family: var(--qh-font-display);
  font-size: 20px;
  font-weight: 900;
}

.ask-hero p {
  margin: 10px 0 0;
  color: var(--qh-text-muted);
  line-height: 1.7;
}

.ask-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 12px;
  align-items: center;
}

.ask-input {
  width: 100%;
}

.ask-controls {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  margin-top: 14px;
}

.quick-asks {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
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

.expand-switch {
  display: flex;
  gap: 8px;
  align-items: center;
  color: var(--qh-fg-soft);
  font-size: 13px;

  em {
    color: var(--qh-text-muted);
    font-style: normal;
  }
}

.ask-alert {
  border-radius: 16px;
}

.panel-head {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: baseline;
  justify-content: space-between;
}

.stat {
  color: var(--qh-text-muted);
  font-size: 13px;
}

.panel-note {
  margin: 10px 0 0;
  color: var(--qh-text-muted);
  font-size: 13px;
  line-height: 1.7;

  &.warn {
    color: var(--qh-accent);
  }
}

.hit-list,
.relation-list,
.citation-list {
  display: grid;
  gap: 10px;
  margin: 14px 0 0;
  padding: 0;
  list-style: none;
}

.hit-list li {
  display: flex;
  gap: 10px;
  align-items: baseline;
  padding: 10px 12px;
  border: 1px solid var(--qh-line);
  border-radius: 12px;

  code,
  em {
    color: var(--qh-text-muted);
    font-size: 12px;
    font-style: normal;
  }

  .kind {
    color: var(--qh-color-jade);
    font-size: 12px;
    font-weight: 900;
  }
}

.seed-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.seed {
  padding: 6px 12px;
  border: 1px solid var(--qh-line);
  border-radius: var(--qh-radius-pill);
  font-weight: 700;

  em {
    margin-left: 6px;
    color: var(--qh-text-muted);
    font-size: 12px;
    font-style: normal;
    font-weight: 400;
  }
}

.answer {
  margin: 14px 0 0;
  padding: 14px 16px;
  border-left: 3px solid var(--qh-color-jade);
  border-radius: 0 12px 12px 0;
  background: color-mix(in srgb, var(--qh-color-jade) 8%, transparent);
  line-height: 1.85;
}

.relation-list li {
  padding: 12px 14px;
  border: 1px solid var(--qh-line);
  border-radius: 12px;

  &.hard {
    border-color: color-mix(in srgb, var(--qh-accent) 55%, transparent);  }
}

.relation-head {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: baseline;

  em {
    color: var(--qh-text-muted);
    font-size: 12px;
    font-style: normal;
  }
}

.tier {
  padding: 1px 6px;
  border-radius: 6px;
  background: var(--qh-line);
  color: var(--qh-fg-soft);
  font-size: 11px;
  font-weight: 900;
}

.arrow {
  color: var(--qh-color-jade);
  font-weight: 700;
}

blockquote {
  margin: 8px 0 0;
  padding-left: 10px;
  border-left: 2px solid var(--qh-line);
  color: var(--qh-fg-soft);
  font-size: 13px;
  line-height: 1.7;

  footer {
    display: flex;
    gap: 8px;
    margin-top: 4px;
    color: var(--qh-text-muted);
    font-size: 12px;
  }
}

.citation-list li {
  padding: 10px 12px;
  border: 1px solid var(--qh-line);
  border-radius: 12px;

  p {
    margin: 6px 0 0;
    color: var(--qh-fg-soft);
    font-size: 13px;
    line-height: 1.7;
  }
}

.citation-head {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: baseline;

  code,
  .page {
    color: var(--qh-text-muted);
    font-size: 12px;
  }
}

/* 反查结果：一条「证」，下面两行「药 / 方」。 */
.reverse-list {
  display: grid;
  gap: 10px;
  margin: 14px 0 0;
  padding: 0;
  list-style: none;

  > li {
    display: grid;
    gap: 8px;
    padding: 12px 14px;
    border: 1px solid var(--qh-line);
    border-radius: 12px;
  }
}

.hop-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  color: var(--qh-text-muted);
  font-size: 13px;

  b {
    min-width: 16px;
    color: var(--qh-fg-soft);
    font-size: 12px;
    font-weight: 600;
  }

  span {
    padding: 1px 8px;
    border-radius: 999px;
    background: color-mix(in srgb, var(--qh-fg) 6%, transparent);
    color: var(--qh-fg);
    font-size: 13px;
  }

  em {
    color: var(--qh-text-muted);
    font-size: 12px;
    font-style: normal;
  }

  small {
    color: var(--qh-text-muted);
    font-size: 12px;
  }
}

.hop-note {
  margin: 0;
  color: var(--qh-text-muted);
  font-size: 12px;
}
</style>
