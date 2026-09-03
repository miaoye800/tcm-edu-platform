<template>
  <RouterView />
  <MascotBubble v-if="route.path !== '/alchemy-demo' && route.path !== '/mindmap-themes' && route.path !== '/stage4-lab' && route.path !== '/apprentice'" />
</template>

<script setup lang="ts">
import { RouterView, useRoute } from 'vue-router'
import MascotBubble from './components/ui/MascotBubble.vue'

const route = useRoute()
</script>
