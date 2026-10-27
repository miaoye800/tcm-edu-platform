<template>
  <section class="story-paper-form">
    <header v-if="title" class="story-paper-form__head">
      <p v-if="kicker" class="story-paper-form__kicker">{{ kicker }}</p>
      <h2 class="story-paper-form__title">{{ title }}</h2>
    </header>
    <p v-if="prompt" class="story-paper-form__prompt">{{ prompt }}</p>
    <slot />
  </section>
</template>

<script setup lang="ts">
withDefaults(defineProps<{ title?: string; kicker?: string; prompt?: string }>(), {
  title: '',
  kicker: '',
  prompt: '',
})
</script>

<style scoped lang="scss">
.story-paper-form {
  width: min(520px, 100%);
  padding: var(--qh-space-6);
  border-radius: var(--qh-radius-panel);
  background: var(--story-cream);
  box-shadow: var(--qh-shadow-panel);
}

.story-paper-form__head {
  margin-bottom: var(--qh-space-4);
}

.story-paper-form__kicker {
  margin: 0 0 var(--qh-space-1);
  color: var(--story-cinnabar);
  font-family: var(--qh-font-body);
  font-size: var(--qh-text-footnote);
  letter-spacing: 0.16em;
}

.story-paper-form__title {
  margin: 0;
  color: var(--story-ink);
  font-family: var(--qh-font-reading);
  font-size: var(--qh-text-title);
  font-weight: 600;
  letter-spacing: 0.08em;
}

.story-paper-form__prompt {
  margin: 0 0 var(--qh-space-4);
  color: var(--story-ink-soft);
  font-family: var(--qh-font-reading);
  font-size: var(--qh-text-ui);
  line-height: 1.8;
}
</style>
