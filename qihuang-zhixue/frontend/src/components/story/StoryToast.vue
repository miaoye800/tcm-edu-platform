<template>
  <Transition name="story-toast">
    <p v-if="message" class="story-toast" role="status" aria-live="polite">{{ message }}</p>
  </Transition>
</template>

<script setup lang="ts">
withDefaults(defineProps<{ message?: string }>(), { message: '' })
</script>

<style scoped lang="scss">
.story-toast {
  position: absolute;
  top: 76px;
  left: 50%;
  z-index: 30;
  margin: 0;
  padding: var(--qh-space-2) var(--qh-space-5);
  border-radius: var(--qh-radius-pill);
  background: var(--story-pill);
  color: var(--story-pill-text);
  font-family: var(--qh-font-body);
  font-size: var(--qh-text-footnote);
  letter-spacing: 0.04em;
  transform: translateX(-50%);
}

.story-toast-enter-active,
.story-toast-leave-active {
  transition: opacity var(--qh-duration-fast) var(--qh-ease);
}

.story-toast-enter-from,
.story-toast-leave-to {
  opacity: 0;
}
</style>
