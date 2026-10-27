<template>
  <RouterView />
  <MascotBubble v-if="showMascot" />
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { RouterView, useRoute } from 'vue-router'
import MascotBubble from './components/ui/MascotBubble.vue'

// Immersive screens own the whole viewport; a floating assistant would sit on
// top of their dialogue. Dev-only story harnesses are listed too so screenshots
// show the page as it actually ships.
const IMMERSIVE_PATHS = new Set(['/alchemy-demo', '/mindmap-themes', '/stage4-lab', '/apprentice', '/story-preview', '/story-kit'])

const route = useRoute()
const showMascot = computed(() => !IMMERSIVE_PATHS.has(route.path))
</script>
