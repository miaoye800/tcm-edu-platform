<template>
  <section v-if="questions.length" class="story-extra" aria-label="师父追问">
    <header class="story-extra__head">
      <span class="story-extra__title">师父追问</span>
      <span class="story-extra__count">{{ answeredCount }} / {{ questions.length }} 已答 · 可留白</span>
    </header>
    <ul class="story-extra__list">
      <li v-for="question in questions" :key="question.id" class="story-extra__item">
        <p class="story-extra__prompt">{{ question.prompt }}</p>
        <div v-if="question.options.length" class="story-extra__options">
          <button
            v-for="option in question.options"
            :key="option"
            type="button"
            class="story-extra__pill"
            :class="{ 'is-active': answers[question.id] === option }"
            :disabled="disabled"
            @click="emit('answer', question.id, answers[question.id] === option ? '' : option)"
          >
            {{ option }}
          </button>
        </div>
        <input
          v-else
          class="story-extra__input"
          type="text"
          :value="answers[question.id] ?? ''"
          :disabled="disabled"
          :placeholder="placeholderFor(question)"
          @input="emit('answer', question.id, ($event.target as HTMLInputElement).value)"
        />
      </li>
    </ul>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { ProfileSessionStoryQuestion } from '../../types/profile'

const props = withDefaults(
  defineProps<{
    questions: ProfileSessionStoryQuestion[]
    answers: Record<string, string>
    disabled?: boolean
  }>(),
  { disabled: false },
)
const emit = defineEmits<{ answer: [id: string, value: string] }>()

const answeredCount = computed(
  () => props.questions.filter((question) => (props.answers[question.id] ?? '').trim()).length,
)

// The examples live in the placeholder so whatever is stored was actually written
// by the disciple. Nothing is prefilled.
const PLACEHOLDERS: Record<string, string> = {
  extra_goal_scene: '比如：明年考执业药师，想先把常用药性补上',
  extra_weak_cross_context: '比如：换成病例里的说法，黄芪和党参我就分不清了',
  extra_resource_reason: '比如：我下班晚，只能看十来分钟的短内容',
  extra_pace_recover: '比如：那天加班太晚，就第二天早上补',
  extra_motivation_conflict: '比如：先做完当天必须交的事，再学二十分钟',
  extra_weak_recover: '比如：把记错的药单独抄一遍，隔天再认一次',
}

const placeholderFor = (question: ProfileSessionStoryQuestion) =>
  PLACEHOLDERS[question.id] ?? '写下你自己的说法，留白也可以'
</script>

<style scoped lang="scss">
.story-extra {
  flex: none;
  max-height: 34dvh;
  overflow-y: auto;
  padding: var(--qh-space-3) var(--qh-space-5);
  border: 1px solid var(--story-cream-soft);
  border-radius: var(--qh-radius-bubble);
  background: color-mix(in srgb, var(--story-cream-soft) 62%, transparent);
  backdrop-filter: blur(6px);
}

.story-extra__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--qh-space-3);
  margin-bottom: var(--qh-space-2);
}

.story-extra__title {
  color: var(--story-ink);
  font-family: var(--qh-font-reading);
  font-size: var(--qh-text-ui);
  letter-spacing: 0.08em;
}

.story-extra__count {
  color: var(--story-ink-soft);
  font-family: var(--qh-font-body);
  font-size: var(--qh-text-footnote);
}

.story-extra__list {
  display: flex;
  flex-direction: column;
  gap: var(--qh-space-3);
  margin: 0;
  padding: 0;
  list-style: none;
}

.story-extra__prompt {
  margin: 0 0 var(--qh-space-2);
  color: var(--story-ink);
  font-family: var(--qh-font-reading);
  font-size: var(--qh-text-ui);
  line-height: 1.5;
}

.story-extra__options {
  display: flex;
  flex-wrap: wrap;
  gap: var(--qh-space-2);
}

.story-extra__pill {
  padding: 6px 14px;
  border: 1px solid color-mix(in srgb, var(--story-ink-soft) 40%, transparent);
  border-radius: 999px;
  background: transparent;
  color: var(--story-ink-soft);
  font-family: var(--qh-font-body);
  font-size: var(--qh-text-footnote);
  cursor: pointer;
  transition: background 0.18s ease, color 0.18s ease, border-color 0.18s ease;
}

.story-extra__pill.is-active {
  border-color: var(--story-ink);
  background: var(--story-ink);
  color: var(--story-cream-soft);
}

.story-extra__pill:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.story-extra__input {
  width: 100%;
  padding: 8px 0;
  border: 0;
  border-bottom: 1px solid color-mix(in srgb, var(--story-ink-soft) 35%, transparent);
  background: transparent;
  color: var(--story-ink);
  font-family: var(--qh-font-reading);
  font-size: var(--qh-text-ui);
  line-height: 1.6;
  outline: none;
}

.story-extra__input::placeholder {
  color: var(--story-ink-faint);
}

@media (max-width: 767px) {
  .story-extra {
    max-height: 40dvh;
    padding: var(--qh-space-3) var(--qh-space-4);
  }
}
</style>
