<template>
  <main class="profile-dialogue-fixture">
    <ProfileDialogue
      :prompt="'说说你最近最想解决的中药学习困惑。'"
      session-id="identity-card-visual-fixture"
      :identity-cards-view="cards"
      active-target="learning_goal"
      :profile-progress="{ collected: 4, total: 7, threshold: 0.5, dimensions: [] }"
      :radar="[]"
      :evidence-panel="[]"
      :quick-replies="['准备期末', '每日半小时', '想看图谱']"
      @answer="noop"
      @confirm="noop"
    />
  </main>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import ProfileDialogue from '../components/profile/ProfileDialogue.vue'
import type { IdentityCardView } from '../types/profile'

const noop = () => undefined

// DEV-only visual contract fixture. Its shape mirrors IdentityCardView exactly.
const fixtureCards: IdentityCardView[] = [
  {
    key: 'learning_goal', label: '学习目标', kind: 'distribution', revealed: false, revealable: false, revealed_now: false,
    confidence: 0.76, confidence_status: '已取证', score: null, display_value: '备考夯实',
    distribution: { 应试提分: 0.68, 临床实用: 0.2, 兴趣科普: 0.12 },
    items: [
      { canonical_label: '应试提分', display_name: '备考夯实', ratio: 0.68 },
      { canonical_label: '临床实用', display_name: '临床应用', ratio: 0.2 },
      { canonical_label: '兴趣科普', display_name: '兴趣阅读', ratio: 0.12 },
    ], cards: [], evidence_refs: ['对话第 1 轮#1'],
    quotes: ['下个月要考中药学，我想先把常用药性记牢，尤其是功效相近但配伍禁忌不同的几组药，希望复习时能把它们放在一起对照，不要只靠死记硬背。', '每天能抽出半小时复习。'],
    explanation: '当前目标由本轮对话中的备考时间与复习诉求归纳。', provenance: 'no_key_rule_fallback', pending_reason: null,
  },
  {
    key: 'motivation', label: '学习动机', kind: 'distribution', revealed: true, revealable: true, revealed_now: false,
    confidence: 0.63, confidence_status: '已取证', score: null, display_value: '内外并进', distribution: { 内驱: 0.55, 外驱: 0.45 },
    items: [{ display_name: '求道者', canonical_label: '内驱', ratio: 0.55 }, { display_name: '赶考人', canonical_label: '外驱', ratio: 0.45 }], cards: [], evidence_refs: ['对话第 1 轮#2'],
    quotes: ['考试是动力，也想真正弄懂药性。'], explanation: '动机构成来自用户原话。', provenance: 'fusion', pending_reason: null,
  },
  {
    key: 'resource_preference', label: '资源偏好', kind: 'distribution', revealed: true, revealable: true, revealed_now: false,
    confidence: 0.58, confidence_status: '已取证', score: null, display_value: '图谱优先', distribution: { 知识图谱: 0.7, 汤头歌诀: 0.3 },
    items: [{ display_name: '知识图谱', canonical_label: '知识图谱', ratio: 0.7 }, { display_name: '汤头歌诀', canonical_label: '汤头歌诀', ratio: 0.3 }], cards: [], evidence_refs: ['对话第 2 轮#1'],
    quotes: ['我看图谱会更容易记住关系。'], explanation: '偏好来自当前对话证据。', provenance: 'fusion', pending_reason: null,
  },
  {
    key: 'learning_style', label: '学习风格', kind: 'distribution', revealed: false, revealable: false, revealed_now: false,
    confidence: 0.32, confidence_status: '待取证', score: null, display_value: '待取证', distribution: {}, items: [], cards: [], evidence_refs: [], quotes: [],
    explanation: '尚无足够证据。', provenance: 'fusion', pending_reason: '还没有收集到你偏好的学习方式。',
  },
  {
    key: 'knowledge_base', label: '知识基础', kind: 'level', revealed: true, revealable: true, revealed_now: false,
    confidence: 0.61, confidence_status: '已取证', score: 58, display_value: '药性入门', distribution: {},
    items: [{ level_name: '药性入门', score: 58 }], cards: [], evidence_refs: ['随堂测验#2'], quotes: ['我能说出一些常用药，但配伍还不稳。'], explanation: '等级和分数均由后端融合输出。', provenance: 'fusion', pending_reason: null,
  },
  {
    key: 'pace', label: '学习节奏', kind: 'level', revealed: true, revealable: true, revealed_now: false,
    confidence: 0.67, confidence_status: '已取证', score: 64, display_value: '温火常煎', distribution: {},
    items: [{ level_name: '温火常煎', score: 64, daily_minutes: 30, target_days: 28 }], cards: [], evidence_refs: ['对话第 1 轮#3'], quotes: ['每天能抽出半小时复习。'], explanation: '节奏锚点来自用户给出的时间安排。', provenance: 'rule_fallback', pending_reason: null,
  },
  {
    key: 'weak_points', label: '薄弱区', kind: 'tags', revealed: false, revealable: false, revealed_now: false,
    confidence: 0.38, confidence_status: '待取证', score: null, display_value: '待取证', distribution: {}, items: [], cards: [], evidence_refs: [], quotes: [],
    explanation: '需要更多练习或测验记录。', provenance: 'fusion', pending_reason: '尚未形成可确认的薄弱标签。',
  },
]

const cards = ref<IdentityCardView[]>(fixtureCards)

onMounted(() => {
  window.setTimeout(() => {
    cards.value = fixtureCards.map((card) => card.key === 'learning_goal'
      ? { ...card, revealed: true, revealable: true, revealed_now: true }
      : card)
  }, 420)
})
</script>

<style scoped lang="scss">
@use '../styles/tokens.scss' as *;

.profile-dialogue-fixture {
  min-height: 100dvh;
  padding: var(--qh-space-6);
  background: var(--qh-page-bg);
}

@media (max-width: 680px) {
  .profile-dialogue-fixture { padding: var(--qh-space-3); }
}
</style>
