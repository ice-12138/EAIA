<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import UnifiedWorkspace from './UnifiedWorkspace.vue'

const hash = ref(window.location.hash)
const route = computed(() => hash.value.replace(/^#\/?/, '') || 'heroes')
const visible = computed(() => route.value === 'recommendation')

const setsOnly = ref(localStorage.getItem('eaia-sets-only') === '1')
watch(setsOnly, value => localStorage.setItem('eaia-sets-only', value ? '1' : '0'))

const ANCIENT_QUALITY_CHOICES = [
  {
    quality_id: 'ancient_legendary_gold',
    quality_name: '上古传说',
    quality_rank: 3.5,
    max_enhancement_level: 12,
    has_special_roll_rule: 1,
    notes: '上古前缀；强化上限与主词条上限沿用传说品质。',
  },
  {
    quality_id: 'ancient_mythic_red',
    quality_name: '上古神话',
    quality_rank: 4.5,
    max_enhancement_level: 16,
    has_special_roll_rule: 1,
    notes: '上古前缀；强化上限与主词条上限沿用神话品质。',
  },
]
const BASE_QUALITY_IDS = new Set(['rare_blue', 'epic_purple', 'legendary_gold', 'mythic_red'])

function normalizedEquipmentPayload(payload) {
  const root = { ...payload }
  const wrapped = root.values && typeof root.values === 'object'
  const values = { ...(wrapped ? root.values : root) }
  if (values.quality_id === 'ancient_legendary_gold') {
    values.quality_id = 'legendary_gold'
    values.is_ancient = true
  } else if (values.quality_id === 'ancient_mythic_red') {
    values.quality_id = 'mythic_red'
    values.is_ancient = true
  } else if (BASE_QUALITY_IDS.has(values.quality_id)) {
    values.is_ancient = false
  }
  if (Array.isArray(values.stats)) {
    values.stats = values.stats.map((stat, index) => {
      const valuePresent = stat?.stat_value !== null && stat?.stat_value !== undefined && stat?.stat_value !== ''
      return {
        ...stat,
        stat_type: stat?.stat_type ? String(stat.stat_type).toUpperCase() : stat?.stat_type,
        is_unlocked: index === 0 || valuePresent,
      }
    })
  }
  if (wrapped) {
    root.values = values
    return root
  }
  return values
}

function augmentedCatalog(data) {
  const result = { ...data }
  result.gear_qualities = [...(data.gear_qualities || [])]
  for (const quality of ANCIENT_QUALITY_CHOICES) {
    if (!result.gear_qualities.some(row => row.quality_id === quality.quality_id)) {
      result.gear_qualities.push(quality)
    }
  }
  // equipment_stats uses canonical uppercase StatType identifiers while the
  // V2.2 dictionary seed stores lower-case IDs.  The editor select must use
  // the canonical IDs or an existing item appears to have blank stat names.
  result.stat_definitions = (data.stat_definitions || []).map(row => ({
    ...row,
    stat_type: row.stat_type ? String(row.stat_type).toUpperCase() : row.stat_type,
  }))
  return result
}

function replacedJsonResponse(response, payload) {
  const headers = new Headers(response.headers)
  headers.set('Content-Type', 'application/json; charset=utf-8')
  headers.delete('Content-Length')
  return new Response(JSON.stringify(payload), {
    status: response.status,
    statusText: response.statusText,
    headers,
  })
}

const nativeFetch = window.fetch.bind(window)
const enhancedFetch = async (input, init = {}) => {
  const url = typeof input === 'string' ? input : input?.url || ''
  const method = String(init?.method || 'GET').toUpperCase()
  const hasBody = init?.body && typeof init.body === 'string'

  if (url.includes('/api/manage/equipment') && ['POST', 'PATCH'].includes(method) && hasBody) {
    try {
      init = { ...init, body: JSON.stringify(normalizedEquipmentPayload(JSON.parse(init.body))) }
    } catch {
      // Preserve the original request when the body is not JSON.
    }
  }

  const isRecommendation = url.includes('/api/hero-core/recommend')
  if (setsOnly.value && isRecommendation && hasBody) {
    try {
      const payload = JSON.parse(init.body)
      if (!payload.team && !payload.hero_core_ids) {
        init = { ...init, body: JSON.stringify({ ...payload, sets_only: true }) }
      }
    } catch {
      // Keep the original request untouched if it is not JSON.
    }
  }

  const response = await nativeFetch(input, init)
  if (response.ok && url.includes('/api/catalog')) {
    try {
      const data = await response.clone().json()
      return replacedJsonResponse(response, augmentedCatalog(data))
    } catch {
      return response
    }
  }
  return response
}
window.fetch = enhancedFetch

const heroes = ref([])
const equipment = ref([])
const selectedHeroes = ref([])
const addHero = ref('')
const teamMode = ref('total')
const targetDef = ref(0)
const enemies = ref(1)
const trials = ref(16)
const candidates = ref(5)
const candidatePool = ref(24)
const teamJob = ref(null)
const teamResult = ref(null)
const teamError = ref('')
let timer = null

function syncHash() { hash.value = window.location.hash }

async function api(url, opt = {}) {
  const response = await nativeFetch(url, opt)
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`)
  return data
}

function isOutputHero(hero) {
  const category = hero?.recommendation_profile?.category
  if (category) return String(category).toLowerCase() === 'output'
  const role = String(hero?.role || '').toLowerCase()
  return ['战士', '法师', '射手', 'fighter', 'mage', 'marksman', 'ranger'].includes(role)
}

const outputHeroes = computed(() => heroes.value
  .filter(hero => hero.hero_core_available && isOutputHero(hero))
  .sort((a, b) => String(a.hero_name || '').localeCompare(String(b.hero_name || ''), 'zh-CN')))
const selectedRows = computed(() => selectedHeroes.value
  .map(id => outputHeroes.value.find(hero => hero.hero_core_id === id)).filter(Boolean))
const availableHeroes = computed(() => outputHeroes.value
  .filter(hero => !selectedHeroes.value.includes(hero.hero_core_id)))
const equipmentMap = computed(() => {
  const map = new Map()
  equipment.value.forEach((row, index) => map.set(row.item_id, { ...row, _seq: index + 1 }))
  return map
})

async function loadEnhancementData() {
  if (!visible.value) return
  try {
    const [heroPayload, equipmentPayload] = await Promise.all([
      api('/api/heroes'),
      api('/api/manage/equipment'),
    ])
    heroes.value = heroPayload.heroes || []
    equipment.value = equipmentPayload.rows || []
    if (!selectedHeroes.value.length && outputHeroes.value.length >= 2) {
      selectedHeroes.value = outputHeroes.value.slice(0, 2).map(hero => hero.hero_core_id)
    }
    addHero.value = availableHeroes.value[0]?.hero_core_id || ''
  } catch (error) {
    teamError.value = error.message
  }
}

function addSelectedHero() {
  if (!addHero.value || selectedHeroes.value.includes(addHero.value)) return
  selectedHeroes.value.push(addHero.value)
  addHero.value = availableHeroes.value[0]?.hero_core_id || ''
}
function removeHero(index) {
  selectedHeroes.value.splice(index, 1)
  addHero.value = availableHeroes.value[0]?.hero_core_id || ''
}
function moveHero(index, delta) {
  const next = index + delta
  if (next < 0 || next >= selectedHeroes.value.length) return
  const copy = [...selectedHeroes.value]
  ;[copy[index], copy[next]] = [copy[next], copy[index]]
  selectedHeroes.value = copy
}
function stopPoll() {
  if (timer) clearInterval(timer)
  timer = null
}
async function pollTeam() {
  if (!teamJob.value?.id) return
  try {
    const state = await api('/api/hero-core/recommend/status')
    const fresh = (state.jobs || []).find(job => job.id === teamJob.value.id)
    if (!fresh) return
    teamJob.value = fresh
    if (fresh.status === 'completed') {
      teamResult.value = fresh.result || null
      stopPoll()
    } else if (fresh.status === 'failed') {
      teamError.value = fresh.error || '团队推荐失败'
      stopPoll()
    } else if (fresh.status === 'cancelled') {
      stopPoll()
    }
  } catch (error) {
    teamError.value = error.message
    stopPoll()
  }
}
function startPoll() {
  stopPoll()
  pollTeam()
  timer = setInterval(pollTeam, 1000)
}

async function runTeam() {
  teamError.value = ''
  teamResult.value = null
  if (selectedHeroes.value.length < 2) {
    teamError.value = '团队推荐至少需要选择 2 名输出英雄。'
    return
  }
  try {
    const names = selectedRows.value.map(hero => hero.hero_name).join(' / ')
    const payload = {
      team: true,
      hero_core_ids: [...selectedHeroes.value],
      hero_name: `团队 · ${names}`,
      team_mode: teamMode.value,
      sets_only: setsOnly.value,
      target_def: Number(targetDef.value),
      enemy_count: Number(enemies.value),
      trials: Number(trials.value),
      candidate_per_slot: Number(candidates.value),
      team_candidate_pool: Number(candidatePool.value),
      control_immune: true,
    }
    teamJob.value = await api('/api/hero-core/recommend/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    startPoll()
  } catch (error) {
    teamError.value = error.message
  }
}

function jobLabel(status) {
  return ({ queued: '排队中', starting: '准备中', screening: '候选计算中',
    refining: '全局分配中', completed: '已完成', failed: '失败' })[status] || status || ''
}
function fmt(value) {
  const number = Number(value)
  return Number.isFinite(number) ? number.toLocaleString('zh-CN', { maximumFractionDigits: 0 }) : '—'
}
function gearLabel(itemId, build) {
  const row = equipmentMap.value.get(itemId)
  const final = (build?.final_set_states || []).find(item => item.item_id === itemId)
  const seq = row?._seq ? `#${row._seq}` : itemId
  const setName = final?.set_name || row?.set_name || row?.set_id || ''
  return setName ? `${seq} · ${setName}` : seq
}
function groupSetName(build, slots) {
  const states = build?.final_set_states || []
  const names = [...new Set(states.filter(item => slots.includes(item.slot)).map(item => item.set_name || item.set_id))]
  return names.length === 1 ? names[0] : names.join(' + ') || '—'
}

watch(visible, value => { if (value) loadEnhancementData(); else stopPoll() })
onMounted(() => {
  window.addEventListener('hashchange', syncHash)
  loadEnhancementData()
})
onUnmounted(() => {
  window.removeEventListener('hashchange', syncHash)
  stopPoll()
  if (window.fetch === enhancedFetch) window.fetch = nativeFetch
})
</script>

<template>
  <UnifiedWorkspace />
  <aside v-if="visible" class="team-enhancement">
    <header class="team-head">
      <div><small>TEAM OPTIMIZER</small><strong>团队装备推荐</strong></div>
      <span v-if="teamJob">{{ jobLabel(teamJob.status) }}</span>
    </header>

    <label class="set-only-switch">
      <input v-model="setsOnly" type="checkbox">
      <span><strong>仅完整套装</strong><small>同时作用于单英雄与团队；左侧必须 2 件成套，右侧必须 3 件成套。</small></span>
    </label>

    <div class="team-mode">
      <button :class="{ active: teamMode === 'ordered' }" @click="teamMode = 'ordered'">按序推荐</button>
      <button :class="{ active: teamMode === 'total' }" @click="teamMode = 'total'">总输出最高</button>
    </div>

    <section class="hero-pick">
      <div class="hero-pick-title"><strong>团队英雄</strong><small>{{ teamMode === 'ordered' ? '顺序即装备优先级' : '顺序仅用于结果展示' }}</small></div>
      <div v-for="(hero, index) in selectedRows" :key="hero.hero_core_id" class="selected-hero">
        <b>{{ index + 1 }}</b><span>{{ hero.hero_name }}</span>
        <div>
          <button :disabled="index === 0" @click="moveHero(index, -1)">↑</button>
          <button :disabled="index === selectedRows.length - 1" @click="moveHero(index, 1)">↓</button>
          <button @click="removeHero(index)">×</button>
        </div>
      </div>
      <div class="hero-add">
        <select v-model="addHero">
          <option value="">选择输出英雄</option>
          <option v-for="hero in availableHeroes" :key="hero.hero_core_id" :value="hero.hero_core_id">{{ hero.hero_name }}</option>
        </select>
        <button :disabled="!addHero" @click="addSelectedHero">添加</button>
      </div>
    </section>

    <div class="team-grid">
      <label><span>木桩防御</span><input v-model.number="targetDef" type="number" min="0"></label>
      <label><span>目标数量</span><input v-model.number="enemies" type="number" min="1"></label>
      <label><span>精算次数</span><input v-model.number="trials" type="number" min="1" max="256"></label>
      <label><span>每部位候选</span><input v-model.number="candidates" type="number" min="1" max="8"></label>
      <label v-if="teamMode === 'total'" class="wide"><span>每英雄团队候选池</span><input v-model.number="candidatePool" type="number" min="4" max="64"></label>
    </div>

    <button class="run-team" :disabled="selectedHeroes.length < 2 || ['queued','starting','screening','refining'].includes(teamJob?.status)" @click="runTeam">
      {{ ['queued','starting','screening','refining'].includes(teamJob?.status) ? '团队计算中…' : '计算唯一最优团队方案' }}
    </button>
    <p v-if="teamError" class="team-error">{{ teamError }}</p>

    <section v-if="teamResult?.team" class="team-result">
      <header>
        <span>{{ teamResult.team_mode === 'ordered' ? '按序推荐' : '团队总输出最高' }}</span>
        <strong>团队 ED60 {{ fmt(teamResult.team_total_equivalent_60s) }}</strong>
        <small>装备冲突 {{ teamResult.equipment_conflicts }} · 共 {{ teamResult.unique_item_count }} 件唯一装备</small>
      </header>
      <article v-for="hero in teamResult.heroes" :key="hero.hero_core_id">
        <div class="result-hero-head"><b>{{ hero.priority }}</b><span><strong>{{ hero.hero_name }}</strong><small>ED60 {{ fmt(hero.equivalent_60s) }}</small></span></div>
        <div class="result-sets"><span>左：{{ groupSetName(hero.build, ['weapon','armor']) }}</span><span>右：{{ groupSetName(hero.build, ['bracelet','necklace','ring']) }}</span></div>
        <div class="result-items"><span v-for="itemId in hero.build.item_ids" :key="itemId">{{ gearLabel(itemId, hero.build) }}</span></div>
      </article>
    </section>
  </aside>
</template>

<style scoped>
.team-enhancement{position:fixed;z-index:80;right:24px;bottom:24px;width:min(420px,calc(100vw - 40px));max-height:calc(100vh - 48px);overflow:auto;padding:18px;border:1px solid rgba(120,130,150,.24);border-radius:16px;background:rgba(18,22,31,.96);color:#f6f7fb;box-shadow:0 24px 70px rgba(0,0,0,.38);backdrop-filter:blur(16px)}
.team-head,.result-hero-head,.hero-add,.selected-hero,.team-result>header{display:flex;align-items:center}.team-head{justify-content:space-between;gap:12px;margin-bottom:14px}.team-head div{display:grid;gap:2px}.team-head small,.hero-pick-title small,.set-only-switch small,.team-result small,.result-hero-head small{color:#9ca6ba}.team-head span{font-size:12px;color:#9fb7ff}
.set-only-switch{display:flex;gap:10px;align-items:flex-start;padding:12px;border-radius:12px;background:rgba(255,255,255,.055);cursor:pointer}.set-only-switch input{margin-top:4px}.set-only-switch span{display:grid;gap:3px}
.team-mode{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:12px 0}.team-mode button,.hero-add button,.selected-hero button{border:1px solid rgba(255,255,255,.12);border-radius:9px;background:rgba(255,255,255,.06);color:inherit;cursor:pointer}.team-mode button{padding:9px}.team-mode button.active{border-color:#7798ff;background:rgba(94,126,255,.2)}
.hero-pick{display:grid;gap:7px}.hero-pick-title{display:flex;justify-content:space-between;gap:8px;align-items:baseline}.selected-hero{gap:8px;padding:7px 9px;border-radius:9px;background:rgba(255,255,255,.045)}.selected-hero>b,.result-hero-head>b{display:grid;place-items:center;width:25px;height:25px;border-radius:50%;background:rgba(119,152,255,.18)}.selected-hero>span{flex:1}.selected-hero div{display:flex;gap:4px}.selected-hero button{min-width:26px;height:26px}.selected-hero button:disabled{opacity:.3}.hero-add{gap:7px}
.hero-add select,.team-grid input{min-width:0;width:100%;box-sizing:border-box;border:1px solid rgba(255,255,255,.12);border-radius:9px;background:#202632;color:#f6f7fb;padding:8px}.hero-add select{flex:1}.hero-add button{padding:8px 12px}
.team-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:12px}.team-grid label{display:grid;gap:5px;font-size:12px;color:#b9c1d0}.team-grid .wide{grid-column:1/-1}.run-team{width:100%;margin-top:12px;border:0;border-radius:10px;padding:11px;font-weight:700;color:white;background:#5d7df5;cursor:pointer}.run-team:disabled{opacity:.5;cursor:default}.team-error{margin:10px 0 0;color:#ff9999;font-size:13px}
.team-result{display:grid;gap:10px;margin-top:14px}.team-result>header{align-items:flex-start;flex-direction:column;gap:2px;padding-top:12px;border-top:1px solid rgba(255,255,255,.1)}.team-result article{padding:10px;border-radius:11px;background:rgba(255,255,255,.045)}.result-hero-head{gap:8px}.result-hero-head>span{display:grid;gap:1px}.result-sets{display:flex;gap:8px;margin:8px 0;font-size:12px;color:#b9c1d0}.result-items{display:grid;gap:3px;font-size:12px;color:#d8deea}
/* UnifiedWorkspace's single-hero form lives inside this wrapper.  Prevent the
   two numeric inputs from preserving their intrinsic width and overlapping. */
:deep(.config-card .two-cols){grid-template-columns:minmax(0,1fr) minmax(0,1fr)}
:deep(.config-card .two-cols>label){min-width:0}
:deep(.config-card .two-cols input),:deep(.config-card .two-cols select){width:100%;min-width:0;max-width:100%}
@media(max-width:720px){.team-enhancement{right:12px;bottom:12px;width:calc(100vw - 24px);max-height:70vh}}
</style>
