<template>
  <header class="story-chapter-bar">
    <p class="story-chapter-bar__chapter">
      <span class="story-chapter-bar__seal" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <path d="M4 11h16" />
          <path d="M5 11c0 4.4 3.1 8 7 8s7-3.6 7-8" />
          <path d="M14.2 4.6 9.4 9.4" />
          <circle cx="15.4" cy="3.6" r="1.3" />
        </svg>
      </span>
      <span>{{ chapter }}</span>
    </p>
    <p v-if="meta" class="story-chapter-bar__meta">{{ meta }}</p>
    <button class="story-chapter-bar__exit" type="button" @click="emit('exit')">退出剧情</button>
  </header>
</template>

<script setup lang="ts">
withDefaults(defineProps<{ chapter: string; meta?: string }>(), { meta: '' })
const emit = defineEmits<{ exit: [] }>()
</script>

<style scoped lang="scss">
.story-chapter-bar {
  position: absolute;
  inset: 0 0 auto;
  z-index: 20;
  display: flex;
  align-items: center;
  gap: var(--qh-space-4);
  padding: var(--qh-space-4) var(--qh-space-8);
  background: linear-gradient(180deg, var(--story-stage-veil), transparent);
}

.story-chapter-bar__chapter {
  display: flex;
  align-items: center;
  gap: var(--qh-space-3);
  margin: 0;
  color: var(--story-cream-soft);
  font-family: var(--qh-font-body);
  font-size: var(--qh-text-footnote);
  letter-spacing: 0.1em;
}

// A mortar and pestle, drawn in the same 24 / 1.8 / currentColor vocabulary as
// the option pills. The 20px box is kept so the chapter label does not shift.
.story-chapter-bar__seal {
  display: grid;
  flex: none;
  width: 20px;
  height: 20px;
  place-items: center;
}

.story-chapter-bar__seal svg {
  width: 18px;
  height: 18px;
}

.story-chapter-bar__meta {
  margin: 0;
  color: var(--story-cream-soft);
  font-family: var(--qh-font-body);
  font-size: var(--qh-text-footnote);
  letter-spacing: 0.06em;
  opacity: 0.72;
}

.story-chapter-bar__exit {
  margin-left: auto;
  padding: var(--qh-space-1) var(--qh-space-3);
  border: 0;
  border-radius: var(--qh-radius-pill);
  background: transparent;
  color: var(--story-cream-soft);
  font-family: var(--qh-font-body);
  font-size: var(--qh-text-footnote);
  letter-spacing: 0.08em;
  cursor: pointer;
  transition: color var(--qh-duration-fast) var(--qh-ease);
}

.story-chapter-bar__exit:hover {
  color: var(--story-cream);
}

.story-chapter-bar__exit:focus-visible {
  outline: 2px solid var(--story-gold-deep);
  outline-offset: 3px;
}
</style>
