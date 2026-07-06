<template>
  <section v-loading="learning.loadingPath" class="mistakes-view">
    <div class="mistakes-hero">
      <div>
        <p class="eyebrow">错题本</p>
        <h2>薄弱回炉</h2>
        <p>依据 BKT 掌握度与 FSRS 到期项汇总，优先处理近期答错和反复到期知识点。</p>
      </div>
      <el-button type="primary" :icon="Refresh" @click="learning.loadPath()">刷新错题</el-button>
    </div>

    <el-alert
      v-if="learning.error"
      class="mistakes-alert"
      type="warning"
      :title="learning.error"
      show-icon
      :closable="false"
    />

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
import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { MagicStick, Refresh } from '@element-plus/icons-vue'
import Badge from '../components/ui/Badge.vue'
import GlassCard from '../components/ui/GlassCard.vue'
import { useLearningStore } from '../stores/learning'

const router = useRouter()
const learning = useLearningStore()

const weakNodes = computed(() =>
  learning.skillTree
    .filter((node) => node.status !== 'mastered' && node.mastery < 0.55)
    .slice(0, 12),
)
const dueItems = computed(() => learning.reviews?.items ?? [])

const goLearn = (topic: string) => {
  void router.push({ name: 'learn', query: { topic } })
}

onMounted(() => {
  void learning.loadPath()
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
  @include glass-panel(26px);
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

.mistakes-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.12fr) minmax(340px, 0.88fr);
  gap: 18px;
}

.panel-head {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  margin-bottom: 16px;
}

.mistake-list,
.due-list {
  display: grid;
  gap: 12px;
}

.mistake-list article,
.due-list article {
  padding: 14px;
  border: 1px solid rgba(143, 208, 173, 0.12);
  border-radius: 16px;
  background: rgba(238, 245, 239, 0.045);
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
  .mistakes-grid {
    display: grid;
    grid-template-columns: 1fr;
  }
}
</style>
