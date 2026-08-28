<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import AppV2 from './AppV2.vue'
import DataManager from './DataManager.vue'
import HeroSimulation from './HeroSimulation.vue'

const hash = ref(window.location.hash)
function syncHash() { hash.value = window.location.hash }
onMounted(() => window.addEventListener('hashchange', syncHash))
onUnmounted(() => window.removeEventListener('hashchange', syncHash))

const route = computed(() => hash.value.replace(/^#\/?/, '') || 'heroes')
const managementMode = computed(() => ['dictionary', 'equipment'].includes(route.value) ? route.value : null)
</script>

<template>
  <HeroSimulation v-if="route==='simulation'" />
  <DataManager v-else-if="managementMode" :mode="managementMode" />
  <AppV2 v-else />
</template>
