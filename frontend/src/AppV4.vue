<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import UnifiedWorkspace from './UnifiedWorkspace.vue'

const hash = ref(window.location.hash)
function syncHash() { hash.value = window.location.hash }
onMounted(() => window.addEventListener('hashchange', syncHash))
onUnmounted(() => window.removeEventListener('hashchange', syncHash))
const route = computed(() => hash.value.replace(/^#\/?/, '') || 'heroes')
</script>

<template>
  <UnifiedWorkspace :mode="route" />
</template>
