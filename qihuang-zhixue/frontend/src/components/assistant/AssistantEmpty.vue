<template>
  <section class="empty">
    <header class="empty-head">
      <h1>今天想做什么？</h1>
      <p class="empty-sub">我认得三类成品、一张知识图谱，还有你的学习画像。不确认，我不会动手生成。</p>
    </header>

    <div class="empty-grid">
      <button v-for="card in cards" :key="card.key" type="button" class="empty-card" @click="emit('ask', card.prompt)">
        <span class="empty-card-tag">{{ card.tag }}</span>
        <strong>{{ card.title }}</strong>
        <span class="empty-card-desc">{{ card.desc }}</span>
        <span class="empty-card-example">「{{ card.prompt }}」</span>
      </button>
    </div>
  </section>
</template>

<script setup lang="ts">
const emit = defineEmits<{ ask: [prompt: string] }>()

const cards = [
  {
    key: 'mindmap',
    tag: '生成',
    title: '一张思维导图',
    desc: '把主题梳理成分支结构，标出哪些来自教材',
    prompt: '做一张解表药的思维导图',
  },
  {
    key: 'ppt',
    tag: '生成',
    title: '一份可讲的幻灯',
    desc: '先出方案与版式，你确认后再逐页生成',
    prompt: '给规培医师讲十八反，做 12 页',
  },
  {
    key: 'doc',
    tag: '生成',
    title: '一篇成文文稿',
    desc: '讲义、综述、复习提纲，逐节标引文',
    prompt: '写一篇人参的用法与禁忌',
  },
  {
    key: 'kg',
    tag: '查证',
    title: '教材里的关系',
    desc: '两味药为什么常一起用、能不能同用',
    prompt: '人参和藜芦能一起用吗',
  },
]
</script>

<style scoped lang="scss">
@use '../../styles/tokens.scss' as *;

.empty {
  display: grid;
  gap: 24px;
  max-width: 680px;
  margin: 0 auto;
  padding: 48px 0 24px;
}

.empty-head h1 {
  margin: 0;
  font-family: var(--qh-font-reading);
  font-size: 26px;
  font-weight: 700;
}

.empty-sub {
  max-width: 46ch;
  margin: 10px 0 0;
  color: var(--qh-text-muted);
  font-size: 13px;
  line-height: 1.8;
}

.empty-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.empty-card {
  display: grid;
  gap: 6px;
  padding: 16px;
  border: 1px solid var(--qh-border);
  border-radius: var(--qh-radius-stage);
  background: var(--qh-surface);
  color: var(--qh-text);
  cursor: pointer;
  text-align: left;
  transition: border-color 160ms ease;
}

.empty-card:hover {
  border-color: var(--qh-accent-line);
}

.empty-card:focus-visible {
  outline: 2px solid var(--qh-accent);
  outline-offset: 2px;
}

.empty-card-tag {
  justify-self: start;
  padding: 1px 7px;
  border: 1px solid var(--qh-border);
  border-radius: 999px;
  color: var(--qh-text-muted);
  font-size: 11px;
}

.empty-card strong {
  font-size: 15px;
}

.empty-card-desc {
  color: var(--qh-text-muted);
  font-size: 12px;
  line-height: 1.6;
}

.empty-card-example {
  color: var(--qh-color-jade);
  font-family: var(--qh-font-reading);
  font-size: 12px;
}

@media (max-width: 900px) {
  .empty-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
