<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import AppV2 from './AppV2.vue'
import HeroSimulation from './HeroSimulation.vue'

const hash = ref(window.location.hash)
function syncHash() { hash.value = window.location.hash }
onMounted(() => window.addEventListener('hashchange', syncHash))
onUnmounted(() => window.removeEventListener('hashchange', syncHash))
const simulationOpen = computed(() => hash.value.replace(/^#\/?/, '') === 'simulation')
function openSimulation() { window.location.hash = '#/simulation' }
</script>

<template>
  <HeroSimulation v-if="simulationOpen" />
  <div v-else class="app-v3-shell">
    <AppV2 />
    <button class="simulation-launch" type="button" @click="openSimulation">
      <span class="simulation-launch-dot"></span>
      战斗伤害仿真
    </button>
  </div>
</template>

<style scoped>
.app-v3-shell { min-height: 100vh; }
.simulation-launch {
  position: fixed;
  right: 28px;
  bottom: 26px;
  z-index: 40;
  display: inline-flex;
  align-items: center;
  gap: 9px;
  padding: 12px 18px;
  border: 1px solid rgba(91,188,255,.38);
  border-radius: 999px;
  background: rgba(10,24,38,.94);
  color: #eaf6ff;
  box-shadow: 0 14px 40px rgba(0,0,0,.32);
  cursor: pointer;
  font-weight: 700;
}
.simulation-launch:hover { transform: translateY(-1px); border-color: rgba(91,188,255,.72); }
.simulation-launch-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #58d0ff;
  box-shadow: 0 0 14px rgba(88,208,255,.8);
}
</style>
