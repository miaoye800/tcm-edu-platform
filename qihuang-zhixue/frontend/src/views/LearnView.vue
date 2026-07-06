<template>
  <section class="learn-view">
    <header class="learn-hero">
      <div class="learn-title">
        <p class="eyebrow">智能学习</p>
        <h2>{{ learning.activeTopic }}</h2>
        <p>节点进度：{{ learning.nodeLabel }}</p>
      </div>
      <div class="learn-controls">
        <el-input v-model="topicInput" placeholder="输入药材或知识点，如 麻黄、桂枝" @keyup.enter="startGenerate" />
        <el-checkbox-group v-model="selectedTypes" size="small">
          <el-checkbox-button v-for="type in typeOptions" :key="type.value" :label="type.value">
            {{ type.label }}
          </el-checkbox-button>
        </el-checkbox-group>
        <el-button type="primary" :loading="learning.streaming" :icon="MagicStick" @click="startGenerate">
          生成资源
        </el-button>
      </div>
    </header>

    <el-alert
      v-if="learning.error"
      class="learn-alert"
      type="warning"
      :title="learning.error"
      show-icon
      :closable="false"
    />

    <section class="progress-strip">
      <div v-for="step in visibleTrace" :key="`${step.node}-${step.summary}`" class="trace-pill">
        <span />
        {{ step.label || step.node }}
      </div>
      <div v-if="!visibleTrace.length" class="trace-pill is-active">
        <span />
        待生成
      </div>
    </section>

    <section class="learn-grid">
      <GlassCard class="dialog-card" padding="lg">
        <div class="panel-head">
          <div>
            <p class="eyebrow">Streaming Explain</p>
            <h3>流式讲解</h3>
          </div>
          <Badge :tone="learning.streaming ? 'jade' : 'gold'" :label="learning.streaming ? 'SSE' : 'Ready'" />
        </div>
        <MarkdownBlock
          v-if="learning.streamStableMarkdown || learning.streamPendingMarkdown"
          :source="learning.streamStableMarkdown"
          :pending="learning.streamPendingMarkdown"
        />
        <div v-else class="empty-state">
          <strong>等待小岐开讲</strong>
          <p>选择主题后会先检索知识库，再流式生成讲解与多模态资源。</p>
        </div>
      </GlassCard>

      <GlassCard class="resource-card" padding="lg">
        <div class="panel-head">
          <div>
            <p class="eyebrow">Resources</p>
            <h3>资源包</h3>
          </div>
          <Badge tone="blue" :label="`${learning.resourceList.length}/5`" />
        </div>

        <el-tabs v-model="activeTab" class="resource-tabs">
          <el-tab-pane label="知识图谱" name="graph">
            <ResourceGraph
              v-if="graphPayload"
              :payload="graphPayload"
              :title="`${learning.activeTopic} · 图谱`"
            />
            <ResourceSkeleton v-else label="图谱生成中" />
          </el-tab-pane>
          <el-tab-pane label="思维导图" name="mindmap">
            <ResourceMindmap v-if="mindmapPayload" :payload="mindmapPayload" />
            <ResourceSkeleton v-else label="导图生成中" />
          </el-tab-pane>
          <el-tab-pane label="题库" name="quiz">
            <QuizPanel
              v-if="quizPayload"
              :questions="quizPayload.questions"
              :topic="learning.activeTopic"
              :submitting="Boolean(learning.lastSubmit)"
              @submit="submitQuizItems"
            />
            <ResourceSkeleton v-else label="题库生成中" />
          </el-tab-pane>
          <el-tab-pane label="拓展阅读" name="reading">
            <div v-if="readingPayload" class="reading-list">
              <article v-for="item in readingPayload.items" :key="item.title">
                <strong>{{ item.title }}</strong>
                <p>{{ item.reason }}</p>
                <span>{{ item.sources.map((source) => source.ref).join('；') }}</span>
              </article>
            </div>
            <ResourceSkeleton v-else label="阅读推荐生成中" />
          </el-tab-pane>
        </el-tabs>
      </GlassCard>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { MagicStick } from '@element-plus/icons-vue'
import Badge from '../components/ui/Badge.vue'
import GlassCard from '../components/ui/GlassCard.vue'
import MarkdownBlock from '../components/learning/MarkdownBlock.vue'
import QuizPanel from '../components/learning/QuizPanel.vue'
import ResourceGraph from '../components/learning/ResourceGraph.vue'
import ResourceMindmap from '../components/learning/ResourceMindmap.vue'
import ResourceSkeleton from '../components/learning/ResourceSkeleton.vue'
import type { GraphPayload, MindmapPayload, QuizPayload, QuizSubmitItem, ReadingPayload, ResourceType } from '../types/learning'
import { useLearningStore } from '../stores/learning'

const route = useRoute()
const router = useRouter()
const learning = useLearningStore()
const topicInput = ref('麻黄')
const selectedTypes = ref<ResourceType[]>(['explain', 'graph', 'mindmap', 'quiz', 'reading'])
const activeTab = ref('graph')

const typeOptions: Array<{ value: ResourceType; label: string }> = [
  { value: 'explain', label: '讲解' },
  { value: 'graph', label: '图谱' },
  { value: 'mindmap', label: '导图' },
  { value: 'quiz', label: '题库' },
  { value: 'reading', label: '阅读' },
]

const visibleTrace = computed(() => learning.streamTrace.slice(-8))
const graphPayload = computed(() => learning.resources.graph?.payload as GraphPayload | undefined)
const mindmapPayload = computed(() => learning.resources.mindmap?.payload as MindmapPayload | undefined)
const quizPayload = computed(() => learning.resources.quiz?.payload as QuizPayload | undefined)
const readingPayload = computed(() => learning.resources.reading?.payload as ReadingPayload | undefined)

const routeTopic = computed(() => {
  const value = route.query.topic
  if (Array.isArray(value)) return value[0] || ''
  return typeof value === 'string' ? value : ''
})

const startGenerate = async () => {
  const topic = topicInput.value.trim() || '麻黄'
  await router.replace({ name: 'learn', query: { ...route.query, topic } })
  await learning.generateResources(topic, selectedTypes.value)
}

const submitQuizItems = async (items: QuizSubmitItem[]) => {
  await learning.answerQuiz(items)
}

onMounted(() => {
  const topic = routeTopic.value || learning.activeTopic || '麻黄'
  topicInput.value = topic
  if (!learning.hasResources || learning.activeTopic !== topic) {
    void learning.generateResources(topic, selectedTypes.value)
  }
})

watch(routeTopic, (topic) => {
  if (!topic || topic === topicInput.value) return
  topicInput.value = topic
  void learning.generateResources(topic, selectedTypes.value)
})
</script>

<style scoped lang="scss">
@use "../styles/tokens.scss" as *;

.learn-view {
  display: grid;
  gap: 18px;
  width: min(1500px, 100%);
  margin: 0 auto;
}

.learn-hero {
  @include glass-panel(26px);
  display: grid;
  grid-template-columns: minmax(0, 0.9fr) minmax(420px, 1.1fr);
  gap: 18px;
  align-items: center;
  padding: 22px;
}

.learn-title {
  min-width: 0;
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
  line-height: 1.1;
}

h2 {
  @include text-sheen;
  font-size: clamp(34px, 5vw, 58px);
}

h3 {
  font-size: 22px;
}

.learn-title p:last-child {
  margin: 10px 0 0;
  color: var(--qh-text-muted);
}

.learn-controls {
  display: grid;
  grid-template-columns: minmax(180px, 1fr) auto auto;
  gap: 10px;
  align-items: center;
  min-width: 0;
}

.learn-alert {
  border-radius: 16px;
}

.progress-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.trace-pill {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  min-height: 30px;
  padding: 0 10px;
  border: 1px solid rgba(143, 208, 173, 0.16);
  border-radius: 999px;
  color: var(--qh-text-muted);
  background: rgba(238, 245, 239, 0.045);
  font-size: 12px;
  font-weight: 900;
}

.trace-pill span {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--qh-color-jade);
  animation: live 1.8s ease-in-out infinite;
}

.learn-grid {
  display: grid;
  grid-template-columns: minmax(360px, 0.78fr) minmax(0, 1.22fr);
  gap: 18px;
  align-items: start;
}

.dialog-card,
.resource-card {
  min-height: 560px;
}

.panel-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  margin-bottom: 16px;
}

.empty-state strong {
  color: var(--qh-text);
  font-family: var(--qh-font-display);
  font-size: 18px;
  font-weight: 900;
}

.empty-state {
  display: grid;
  min-height: 300px;
  place-items: center;
  align-content: center;
  gap: 10px;
  color: var(--qh-text-muted);
  text-align: center;
}

.empty-state p {
  margin: 0;
  color: var(--qh-text-muted);
}

.reading-list {
  display: grid;
  gap: 12px;
}

.reading-list article {
  padding: 14px;
  border: 1px solid rgba(143, 208, 173, 0.12);
  border-radius: 16px;
  background: rgba(238, 245, 239, 0.045);
}

.reading-list strong {
  color: var(--qh-color-gold);
  font-family: var(--qh-font-display);
  font-weight: 900;
}

.reading-list p,
.reading-list span {
  display: block;
  margin: 8px 0 0;
  color: var(--qh-text-muted);
  font-size: 13px;
  line-height: 1.7;
}

@media (max-width: 1180px) {
  .learn-hero,
  .learn-grid {
    grid-template-columns: 1fr;
  }

  .learn-controls {
    grid-template-columns: 1fr;
  }
}
</style>
