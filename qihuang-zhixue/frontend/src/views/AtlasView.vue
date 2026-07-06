<template>
  <section class="atlas-view">
    <div class="atlas-hero">
      <div>
        <p class="eyebrow">中药图谱</p>
        <h2>{{ learning.activeTopic }}</h2>
        <p>全屏浏览药材、证型、方剂与配伍关系。</p>
      </div>
      <div class="atlas-actions">
        <el-input v-model="topicInput" placeholder="输入药材，如 麻黄" @keyup.enter="generate" />
        <el-button type="primary" :loading="learning.streaming" :icon="Connection" @click="generate">生成图谱</el-button>
      </div>
    </div>

    <GlassCard padding="lg" class="atlas-panel">
      <ResourceGraph
        v-if="graphPayload"
        :payload="graphPayload"
        :title="`${learning.activeTopic} · 全屏图谱`"
        full
      />
      <ResourceSkeleton v-else label="图谱生成中" />
    </GlassCard>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Connection } from '@element-plus/icons-vue'
import GlassCard from '../components/ui/GlassCard.vue'
import ResourceGraph from '../components/learning/ResourceGraph.vue'
import ResourceSkeleton from '../components/learning/ResourceSkeleton.vue'
import { useLearningStore } from '../stores/learning'
import type { GraphPayload } from '../types/learning'

const learning = useLearningStore()
const topicInput = ref(learning.activeTopic || '麻黄')

const graphPayload = computed(() => learning.resources.graph?.payload as GraphPayload | undefined)

const generate = async () => {
  await learning.generateResources(topicInput.value || '麻黄', ['graph'])
}

onMounted(() => {
  if (!graphPayload.value) void generate()
})
</script>

<style scoped lang="scss">
@use "../styles/tokens.scss" as *;

.atlas-view {
  display: grid;
  gap: 18px;
  width: min(1500px, 100%);
  margin: 0 auto;
}

.atlas-hero {
  @include glass-panel(26px);
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(360px, 560px);
  gap: 18px;
  align-items: center;
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
  font-size: clamp(36px, 5vw, 62px);
  font-weight: 900;
}

.atlas-hero p {
  margin: 10px 0 0;
  color: var(--qh-text-muted);
}

.atlas-actions {
  display: grid;
  grid-template-columns: minmax(180px, 1fr) auto;
  gap: 10px;
}

.atlas-panel {
  min-height: calc(100vh - 220px);
}

@media (max-width: 900px) {
  .atlas-hero,
  .atlas-actions {
    grid-template-columns: 1fr;
  }
}
</style>
