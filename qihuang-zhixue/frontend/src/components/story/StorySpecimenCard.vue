<template>
  <article class="story-specimen-card">
    <div class="story-specimen-card__media">
      <img v-if="image" :src="image" :alt="alt" fetchpriority="high" />
      <span v-else class="story-specimen-card__placeholder">标本待补</span>
    </div>
    <p v-if="index && total" class="story-specimen-card__count">
      药圃图鉴 · {{ String(index).padStart(2, '0') }} / {{ String(total).padStart(2, '0') }}
    </p>
  </article>
</template>

<script setup lang="ts">
// No `name` / `hint` on purpose: this card is used for "which herb is this?"
// questions, so any text here would hand the answer over. The alt text is the
// only copy, and it is deliberately generic.
withDefaults(defineProps<{ image?: string; alt?: string; index?: number; total?: number }>(), {
  image: '',
  alt: '待辨识的药材标本图',
  index: 0,
  total: 0,
})
</script>

<style scoped lang="scss">
.story-specimen-card {
  display: flex;
  flex-direction: column;
  // Wider than a captioned card would be: the photograph is now the only clue,
  // so it has to carry the recognition task on its own.
  width: min(360px, 100%);
  padding: var(--qh-space-3);
  border-radius: var(--qh-radius-panel);
  background: var(--story-cream);
  box-shadow: var(--qh-shadow-panel);
}

.story-specimen-card__media {
  display: grid;
  place-items: center;
  aspect-ratio: 4 / 3;
  overflow: hidden;
  border-radius: var(--qh-radius-media);
  background: var(--story-stage);
}

.story-specimen-card__media img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.story-specimen-card__placeholder {
  color: var(--story-cream-soft);
  font-family: var(--qh-font-body);
  font-size: var(--qh-text-footnote);
  opacity: 0.6;
}

.story-specimen-card__count {
  margin: var(--qh-space-2) 0 0;
  padding: 0 var(--qh-space-2);
  color: var(--story-ink-faint);
  font-family: var(--qh-font-body);
  font-size: var(--qh-text-footnote);
  letter-spacing: 0.06em;
}
</style>
