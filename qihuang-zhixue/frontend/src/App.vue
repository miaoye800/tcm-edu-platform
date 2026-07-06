<template>
  <RouterView />
  <MascotBubble />
</template>

<script setup lang="ts">
import { RouterView } from 'vue-router'
import MascotBubble from './components/ui/MascotBubble.vue'
</script>
