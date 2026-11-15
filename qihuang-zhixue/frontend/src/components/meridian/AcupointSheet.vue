<template>
  <aside class="acu-sheet" :class="{ 'is-open': !!acupoint }" aria-label="穴位详情">
    <template v-if="acupoint">
      <header class="sheet-head">
        <div>
          <p class="sheet-code">{{ acupoint.code }}</p>
          <h3 class="sheet-name">{{ acupoint.name }}</h3>
          <p class="sheet-pinyin">{{ acupoint.pinyin }} · {{ channelTitle }}</p>
        </div>
        <button type="button" class="sheet-close" aria-label="关闭详情" @click="emit('close')">✕</button>
      </header>

      <p class="sheet-state">
        <span class="state-pill" :class="`is-${presenceOf.presence}`">{{ stateLabel }}</span>
        <span v-if="presenceOf.total" class="state-count">已点亮 {{ presenceOf.completed }}/{{ presenceOf.total }}</span>
      </p>

      <!-- 精度可见是硬要求：362 穴没有一个经过人工校核，界面必须说出来 -->
      <p class="sheet-prec" :class="acupoint.verified ? 'is-verified' : 'is-community'">
        {{ acupoint.verified ? '已人工校核' : 'community 坐标 · 几何吸附，未经临床校核' }}
      </p>

      <p v-if="sections.length" class="sheet-sections">
        <span v-for="item in sections" :key="item.section.id" class="section-chip">
          {{ item.section.title }} · {{ item.completed }}/{{ item.total }}
        </span>
      </p>

      <div v-if="detail" class="sheet-detail">
        <p class="detail-title">{{ detail.name }}</p>
        <p v-for="[key, value] in entries" :key="key" class="detail-row">
          <b>{{ key }}</b>{{ value }}
        </p>
        <p v-if="!entries.length" class="detail-empty">{{ detail.description || '这一条还没有正文。' }}</p>
        <p v-if="detail.content_source && Object.keys(detail.content_source).length" class="detail-source">
          正文来源：{{ sourceLabel }}
        </p>
      </div>
      <p v-else-if="loading" class="sheet-hint">正在读取正文…</p>
      <p v-else class="sheet-hint">{{ hint }}</p>

      <div v-if="tickId" class="sheet-actions">
        <el-button v-if="inPlan" size="small" @click="emit('defer', tickId)">暂缓</el-button>
        <el-button v-if="inPlan" size="small" type="primary" plain @click="emit('complete', tickId)">标记已学</el-button>
        <el-button size="small" text @click="emit('ask', acupoint.name)">问问小岐</el-button>
      </div>
    </template>
  </aside>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { BackendPathState } from '../../types/path'
import { fetchPointDetail, type MeridianAcupoint, type MeridianPointDetail } from '../../services/meridianApi'
import { presenceLabel, type AcupointPresence, type SectionProgress } from './meridianPresence'

const props = defineProps<{
  acupoint: MeridianAcupoint | null
  presence: AcupointPresence | null
  sections: SectionProgress[]
  states: Map<string, BackendPathState>
  tickId: string | null
  channelTitle: string
}>()

const emit = defineEmits<{
  (event: 'close'): void
  (event: 'complete', id: string): void
  (event: 'defer', id: string): void
  (event: 'ask', name: string): void
}>()

const detail = ref<MeridianPointDetail | null>(null)
const loading = ref(false)
const hint = ref('点右侧刻度查看知识点正文。')

const presenceOf = computed(() => props.presence ?? { presence: 'dormant' as const, completed: 0, total: 0 })
const stateLabel = computed(() => presenceLabel[presenceOf.value.presence])
const inPlan = computed(() => (props.tickId ? props.states.has(props.tickId) : false))

const entries = computed<Array<[string, string]>>(() => {
  const content = detail.value?.content ?? {}
  return Object.entries(content)
    .filter(([, value]) => typeof value === 'string')
    .map(([key, value]) => [key, String(value)])
})

const sourceLabel = computed(() => {
  const source = detail.value?.content_source ?? {}
  const page = source.page ?? source.pages
  const book = source.book ?? source.source
  return [book, page ? `第 ${String(page)} 页` : ''].filter(Boolean).join(' · ') || '教材条目'
})

const load = async (id: string | null) => {
  detail.value = null
  if (!id) return
  loading.value = true
  try {
    detail.value = await fetchPointDetail(id)
  } catch (error) {
    const payload = error instanceof Error ? error.message : ''
    hint.value = payload.includes('软引用') ? '这一条还没进知识树，暂时只有名字。' : '正文读取失败，稍后再试。'
  } finally {
    loading.value = false
  }
}

watch(() => props.tickId, (id) => void load(id), { immediate: true })
</script>

<style scoped lang="scss">
.acu-sheet {
  display: none;
  width: 100%;
  max-width: 300px;
  /* 放在 .hud-right 这个有界 flex 列里：允许收缩、自己内部滚动。
     写死 58vh 会让"刻度带 + 抽屉"总高超过容器，内容被裁在舞台下缘。 */
  max-height: none;
  flex: 0 1 auto;
  min-height: 0;
  padding: var(--qh-space-4);
  overflow: auto;
  overscroll-behavior: contain;
  border: 1px solid color-mix(in srgb, var(--qh-color-gold) 30%, transparent);
  border-radius: var(--qh-radius-stage);
  background: color-mix(in srgb, var(--qh-color-night-soft) 90%, transparent);
  box-shadow: var(--qh-shadow-deep);
  backdrop-filter: blur(18px);

  &.is-open {
    display: block;
  }
}

.sheet-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}

.sheet-code {
  margin: 0;
  color: var(--qh-color-jade);
  font-size: 10px;
  letter-spacing: 0.16em;
}

.sheet-name {
  margin: 2px 0 0;
  color: var(--qh-color-cream);
  font-family: var(--qh-font-display);
  font-size: 20px;
  letter-spacing: 0.04em;
}

.sheet-pinyin {
  margin: 2px 0 0;
  color: var(--qh-text-muted);
  font-size: 10px;
}

.sheet-close {
  padding: 2px 6px;
  border: none;
  background: none;
  color: var(--qh-text-muted);
  font-size: 12px;
  cursor: pointer;

  &:hover {
    color: var(--qh-color-cream);
  }
}

.sheet-state {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 10px 0 0;
}

.state-pill {
  padding: 1px 7px;
  border: 1px solid color-mix(in srgb, var(--qh-text) 22%, transparent);
  border-radius: var(--qh-radius-pill);
  font-size: 10px;

  &.is-lit {
    border-color: var(--qh-color-gold);
    color: var(--qh-color-gold);
  }

  &.is-current {
    border-color: var(--qh-color-jade);
    color: var(--qh-color-jade);
  }

  &.is-split {
    border-color: color-mix(in srgb, var(--qh-color-gold) 60%, transparent);
    border-style: dashed;
    color: var(--qh-color-gold);
  }
}

.state-count {
  color: var(--qh-text-muted);
  font-size: 10px;
}

.sheet-prec {
  margin: 8px 0 0;
  font-size: 9.5px;
  line-height: 1.6;

  &.is-community {
    color: color-mix(in srgb, var(--qh-color-coral) 82%, var(--qh-text-muted));
  }

  &.is-verified {
    color: var(--qh-color-jade);
  }
}

.sheet-sections {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin: 10px 0 0;
}

.section-chip {
  padding: 2px 7px;
  border: 1px solid color-mix(in srgb, var(--qh-color-jade) 24%, transparent);
  border-radius: var(--qh-radius-pill);
  color: var(--qh-text-muted);
  font-size: 9.5px;
}

.sheet-detail {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid color-mix(in srgb, var(--qh-color-gold) 18%, transparent);
}

.detail-title {
  margin: 0 0 6px;
  color: var(--qh-color-gold);
  font-size: 12px;
}

.detail-row {
  margin: 0 0 7px;
  color: var(--qh-text);
  font-size: 11.5px;
  line-height: 1.75;

  b {
    margin-right: 6px;
    color: var(--qh-color-cream);
    font-weight: 600;
  }
}

.detail-empty,
.detail-source,
.sheet-hint {
  margin: 6px 0 0;
  color: var(--qh-text-muted);
  font-size: 10.5px;
  line-height: 1.6;
}

.sheet-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 12px;
}
</style>
