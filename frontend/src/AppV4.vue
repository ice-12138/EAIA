<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import DataManager from './DataManager.vue'

const hash = ref(window.location.hash)
function syncHash() { hash.value = window.location.hash }
onMounted(() => window.addEventListener('hashchange', syncHash))
onUnmounted(() => window.removeEventListener('hashchange', syncHash))

const route = computed(() => hash.value.replace(/^#\/?/, '') || 'heroes')
</script>

<template>
  <DataManager :mode="route" />
</template>
