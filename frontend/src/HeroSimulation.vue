<script setup>
import { computed, onMounted, ref, watch } from 'vue'

const props = defineProps({ embedded: { type: Boolean, default: false } })

const SLOTS = ['weapon', 'armor', 'bracelet', 'necklace', 'ring']
const SLOT_LABELS = { weapon:'武器', armor:'护甲', bracelet:'手镯', necklace:'项链', ring:'戒指' }
const STAT_LABELS = {
  ATK_FLAT:'攻击', ATK_PCT:'攻击加成', HP_FLAT:'生命', HP_PCT:'生命加成', DEF_FLAT:'防御', DEF_PCT:'防御加成',
  CRIT_RATE:'暴击率', CRIT_DMG:'暴击伤害', ATK_SPEED:'攻击速度', RAGE_REGEN:'怒气回复', HEALING_EFFECT:'治疗效果'
}
const PCT_STATS = new Set(['ATK_PCT','HP_PCT','DEF_PCT','CRIT_RATE','CRIT_DMG','RAGE_REGEN','HEALING_EFFECT'])

const cores = ref([])
const core = ref(null)
const equipment = ref(null)
const catalog = ref(null)
const selectedCore = ref('SUN_WUKONG')
const selectedItems = ref(Object.fromEntries(SLOTS.map(slot => [slot, ''])))
const policy = ref('immediate')
const targetDef = ref(0)
const controlImmune = ref(true)
const trials = ref(64)
const warmup = ref(120)
const measurement = ref(600)
const loading = ref(true)
const currentLanguage = ref(window.localStorage.getItem('eaia-language') || 'zh-CN')
const running = ref(false)
const error = ref('')
const result = ref(null)

const setNames = computed(() => new Map((catalog.value?.sets || []).map(row => [row.set_id, row.set_name || row.set_id])))
const statsByItem = computed(() => {
  const map = new Map()
  for (const row of equipment.value?.equipment_stats || []) {
    if (row.is_unlocked === 0) continue
    if (!map.has(row.item_id)) map.set(row.item_id, [])
    map.get(row.item_id).push(row)
  }
  return map
})
const inventoryBySlot = computed(() => {
  const result = Object.fromEntries(SLOTS.map(slot => [slot, []]))
  for (const item of equipment.value?.equipment || []) {
    const slot = item.slot_id || item.slot
    if (!result[slot] || item.available === 0) continue
    result[slot].push(item)
  }
  for (const slot of SLOTS) {
    result[slot].sort((a,b) => itemLabel(a).localeCompare(itemLabel(b), 'zh-CN'))
  }
  return result
})
const policyEntries = computed(() => Object.entries(core.value?.policies || {}))
const skillEntries = computed(() => Object.values(core.value?.skills || {}))
const sourceRows = computed(() => Object.entries(result.value?.source_damage_equivalent_60s || {}).sort((a,b) => b[1] - a[1]))
const eventRows = computed(() => Object.entries(result.value?.event_rate_per_60s || {}).filter(([key]) => ['BASIC_ATTACK_READY','ULT_HIT','ultimate_cast','SUMMON_ATTACK','SKILL_HIT'].includes(key)).sort((a,b) => b[1] - a[1]))
const selectedItemCount = computed(() => Object.values(selectedItems.value).filter(Boolean).length)

function go(view) { window.location.hash = `#/${view}` }
function toggleLanguage() {
  currentLanguage.value = currentLanguage.value === 'zh-CN' ? 'en-US' : 'zh-CN'
  window.localStorage.setItem('eaia-language', currentLanguage.value)
}

function itemLabel(item) {
  const setName = setNames.value.get(item.set_id) || item.set_name || item.set_id || '无套装'
  return `${item.item_id} · ${setName}`
}
function normalizedStat(row) {
  let value = Number(row.stat_value ?? row.estimate_override ?? 0)
  if (PCT_STATS.has(row.stat_type) && Math.abs(value) > 3) value /= 100
  return value
}
function formatStat(row) {
  const value = normalizedStat(row)
  if (PCT_STATS.has(row.stat_type)) return `${STAT_LABELS[row.stat_type] || row.stat_type} ${(value * 100).toFixed(1)}%`
  return `${STAT_LABELS[row.stat_type] || row.stat_type} ${Number.isInteger(value) ? value : value.toFixed(1)}`
}
function selectedItem(slot) {
  const id = selectedItems.value[slot]
  return (inventoryBySlot.value[slot] || []).find(item => item.item_id === id) || null
}
function fmt(value, digits=0) {
  const n = Number(value)
  if (!Number.isFinite(n)) return '—'
  return n.toLocaleString('zh-CN', { maximumFractionDigits:digits, minimumFractionDigits:digits })
}
function percent(value) {
  const n = Number(value)
  return Number.isFinite(n) ? `${(n * 100).toFixed(1)}%` : '—'
}
async function loadCore() {
  if (!selectedCore.value) return
  error.value = ''
  result.value = null
  try {
    const response = await fetch(`/api/hero-cores/${encodeURIComponent(selectedCore.value)}`)
    const data = await response.json()
    if (!response.ok) throw new Error(data.error || '无法读取 HeroCore')
    core.value = data
    const names = Object.keys(data.policies || {})
    policy.value = names.includes(data.default_policy) ? data.default_policy : (names[0] || '')
  } catch (err) {
    error.value = err.message || String(err)
  }
}

async function runSimulation() {
  running.value = true
  error.value = ''
  result.value = null
  try {
    const response = await fetch('/api/hero-core/simulate', {
      method:'POST',
      headers:{ 'Content-Type':'application/json' },
      body:JSON.stringify({
        hero_core_id:selectedCore.value,
        item_ids:Object.values(selectedItems.value).filter(Boolean),
        policy:policy.value,
        target_def:Number(targetDef.value),
        control_immune:Boolean(controlImmune.value),
        enemy_count:1,
        trials:Number(trials.value),
        warmup:Number(warmup.value),
        measurement:Number(measurement.value),
        seed:20260828,
      })
    })
    const data = await response.json()
    if (!response.ok) throw new Error(data.error || '仿真失败')
    result.value = data
  } catch (err) {
    error.value = err.message || String(err)
  } finally {
    running.value = false
  }
}

watch(selectedCore, loadCore)

onMounted(async () => {
  try {
    const [coreRes, equipmentRes, catalogRes] = await Promise.all([
      fetch('/api/hero-cores'), fetch('/api/equipment'), fetch('/api/catalog')
    ])
    const coreData = await coreRes.json()
    if (!coreRes.ok) throw new Error(coreData.error || 'HeroCore 列表读取失败')
    cores.value = coreData.hero_cores || []
    equipment.value = equipmentRes.ok ? await equipmentRes.json() : { equipment:[], equipment_stats:[] }
    catalog.value = catalogRes.ok ? await catalogRes.json() : { sets:[] }
    if (!cores.value.some(item => item.id === selectedCore.value)) selectedCore.value = cores.value[0]?.id || ''
    await loadCore()
  } catch (err) {
    error.value = err.message || String(err)
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <main class="sim-page">
    <header v-if="!props.embedded" class="manager-topbar">
      <button class="brand" type="button" @click="go('heroes')"><span>E</span><strong>EAIA</strong><small>装备与英雄数据库</small></button>
      <nav aria-label="主导航">
        <button @click="go('dictionary')">游戏数据</button>
        <button @click="go('equipment')">已有装备</button>
        <button @click="go('heroes')">英雄图鉴</button>
        <button @click="go('recommendation')">装备推荐</button>
        <button @click="go('scanner')">识别装备</button>
        <button class="active" @click="go('simulation')">战斗仿真</button>
      </nav>
      <div class="local-pill"><i></i> 本地 SQLite <button class="language-toggle" type="button" @click="toggleLanguage">{{ currentLanguage === 'zh-CN' ? 'EN' : '中' }}</button></div>
    </header>
    <header class="sim-topbar">
      <div>
        <p class="eyebrow">HEROCORE / EVENT SIMULATION</p>
        <h1>战斗伤害仿真</h1>
      </div>
      <div class="engine-chip">固定引擎 · HeroCore v1.0</div>
    </header>

    <div v-if="loading" class="notice">正在读取 HeroCore 与装备数据库…</div>
    <div v-else class="sim-layout">
      <section class="controls panel-card">
        <div class="section-title">
          <div><p class="eyebrow">INPUT</p><h2>仿真输入</h2></div>
          <span>{{ selectedItemCount }}/5 件装备</span>
        </div>

        <label class="field">
          <span>英雄核心</span>
          <select v-model="selectedCore">
            <option v-for="item in cores" :key="item.id" :value="item.id">{{ item.name }} · {{ item.id }}</option>
          </select>
        </label>

        <label v-if="policyEntries.length" class="field">
          <span>终结技策略</span>
          <select v-model="policy">
            <option v-for="([key, value]) in policyEntries" :key="key" :value="key">{{ value.label || key }}</option>
          </select>
        </label>

        <div class="equipment-block">
          <div class="subheading"><strong>穿装</strong><small>每个部位最多选择一件；留空即裸装。</small></div>
          <div v-for="slot in SLOTS" :key="slot" class="gear-row">
            <div class="slot-name">{{ SLOT_LABELS[slot] }}</div>
            <select v-model="selectedItems[slot]">
              <option value="">不装备</option>
              <option v-for="item in inventoryBySlot[slot]" :key="item.item_id" :value="item.item_id">{{ itemLabel(item) }}</option>
            </select>
            <div v-if="selectedItem(slot)" class="gear-stats">
              <span v-for="stat in statsByItem.get(selectedItems[slot]) || []" :key="`${stat.item_id}-${stat.stat_index}`">{{ formatStat(stat) }}</span>
            </div>
          </div>
        </div>

        <div class="input-grid">
          <label class="field"><span>木桩 DEF</span><input v-model.number="targetDef" type="number" min="0" step="100" /></label>
          <label class="field"><span>Monte Carlo 次数</span><input v-model.number="trials" type="number" min="1" max="4096" step="16" /></label>
          <label class="field"><span>预热时间 / s</span><input v-model.number="warmup" type="number" min="0" step="30" /></label>
          <label class="field"><span>统计时间 / s</span><input v-model.number="measurement" type="number" min="1" step="60" /></label>
        </div>
        <label class="check-field"><input v-model="controlImmune" type="checkbox" /><span>木桩免疫眩晕/控制</span></label>

        <button class="run-btn" type="button" :disabled="running || !selectedCore" @click="runSimulation">
          {{ running ? '正在计算…' : '计算穿装平均伤害' }}
        </button>
        <p class="method-note">同时返回严格前 60 秒伤害与长期稳定状态折算的 ED60。后者用于装备排序，避免 61s/120s 冷却在 60 秒边界产生断层。</p>
        <div v-if="error" class="error-box">{{ error }}</div>
      </section>

      <section class="output-column">
        <article v-if="core" class="panel-card core-card">
          <div class="section-title">
            <div><p class="eyebrow">HERO CORE</p><h2>{{ core.hero.name }}</h2></div>
            <span>{{ core.core_version }} · {{ core.game_version }}</span>
          </div>
          <div class="metric-strip">
            <div><small>基础 ATK</small><strong>{{ fmt(core.hero.base_stats.atk) }}</strong></div>
            <div><small>基础间隔</small><strong>{{ core.hero.base_stats.attack_interval }}s</strong></div>
            <div><small>初始怒气</small><strong>{{ core.resources?.rage?.initial ?? '—' }}</strong></div>
            <div><small>怒气上限</small><strong>{{ core.resources?.rage?.max ?? '—' }}</strong></div>
          </div>
          <div class="skill-grid">
            <div v-for="skill in skillEntries" :key="skill.id" class="skill-chip">
              <strong>{{ skill.name }}</strong>
              <span>{{ skill.kind }} · {{ Math.round(Number(skill.coefficient || 0) * 100) }}% × {{ skill.hit_count || 1 }}</span>
            </div>
          </div>
        </article>

        <article v-if="result" class="panel-card result-card">
          <div class="section-title">
            <div><p class="eyebrow">RESULT</p><h2>伤害结果</h2></div>
            <span class="coverage" :class="result.coverage">{{ result.coverage === 'full' ? '完整覆盖' : '部分覆盖' }}</span>
          </div>
          <div class="damage-hero">
            <div class="damage-primary">
              <small>等效 60 秒平均伤害 · ED60</small>
              <strong>{{ fmt(result.equivalent_60s.mean) }}</strong>
              <span>σ {{ fmt(result.equivalent_60s.stddev, 1) }} · {{ result.trials }} 次随机试验</span>
            </div>
            <div class="damage-secondary">
              <small>严格前 60 秒平均伤害</small>
              <strong>{{ fmt(result.actual_60s.mean) }}</strong>
              <span>用于真实开场 60 秒窗口</span>
            </div>
          </div>

          <h3>最终面板</h3>
          <div class="panel-grid">
            <div><span>ATK</span><strong>{{ fmt(result.panel.atk) }}</strong></div>
            <div><span>暴击率</span><strong>{{ percent(result.panel.crit_rate) }}</strong></div>
            <div><span>暴击伤害</span><strong>{{ percent(result.panel.crit_dmg) }}</strong></div>
            <div><span>攻击速度</span><strong>{{ fmt(result.panel.atk_speed, 1) }}</strong></div>
            <div><span>怒气回复</span><strong>{{ percent(result.panel.rage_regen) }}</strong></div>
          </div>

          <h3>ED60 伤害构成</h3>
          <div class="data-table">
            <div v-for="([name, value]) in sourceRows" :key="name" class="data-row">
              <span>{{ name }}</span><strong>{{ fmt(value) }}</strong><small>{{ result.equivalent_60s.mean ? percent(value / result.equivalent_60s.mean) : '—' }}</small>
            </div>
          </div>

          <template v-if="eventRows.length">
            <h3>等效每 60 秒事件频率</h3>
            <div class="event-list"><span v-for="([name,value]) in eventRows" :key="name">{{ name }} ≈ {{ fmt(value, 2) }}</span></div>
          </template>

          <div v-if="result.active_sets?.length" class="active-sets"><strong>激活套装</strong><span v-for="setId in result.active_sets" :key="setId">{{ setNames.get(setId) || setId }}</span></div>
          <div v-if="result.warnings?.length" class="warning-box"><strong>模型覆盖提示</strong><p v-for="warning in result.warnings" :key="warning">{{ warning }}</p></div>
        </article>

        <article v-if="core" class="panel-card validation-card">
          <div class="section-title"><div><p class="eyebrow">SUPERVISION</p><h2>人工校准项</h2></div><span>{{ core.validation_required?.length || 0 }} 项</span></div>
          <p v-for="(item,index) in core.validation_required || []" :key="item"><strong>{{ index + 1 }}</strong><span>{{ item }}</span></p>
          <details>
            <summary>当前建模假设</summary>
            <ul><li v-for="item in core.assumptions || []" :key="item">{{ item }}</li></ul>
          </details>
        </article>
      </section>
    </div>
  </main>
</template>

<style scoped>
.manager-topbar{height:68px;display:flex;align-items:center;gap:22px;padding:0 clamp(20px,5vw,72px);position:sticky;top:0;z-index:30;border-bottom:1px solid #dce5e8;background:rgba(255,255,255,.96);color:#1c2a33}
.manager-topbar .brand{display:flex;align-items:center;gap:9px;padding:0;border:0;background:transparent;color:#1c2a33;cursor:pointer}.manager-topbar .brand>span{width:30px;height:30px;display:grid;place-items:center;border-radius:7px;background:#1c2a33;color:#fff;font-weight:900}.manager-topbar .brand strong{font-size:18px;letter-spacing:.06em}.manager-topbar .brand small{color:#73838d;font-size:12px}
.manager-topbar nav{display:flex;flex:1;gap:2px;height:100%;align-items:center}.manager-topbar nav button{height:100%;padding:0 13px;border:0;border-bottom:2px solid transparent;background:transparent;color:#73838d;font-size:12px}.manager-topbar nav button:hover,.manager-topbar nav button.active{color:#1c2a33}.manager-topbar nav button.active{border-bottom-color:#238b80}
.manager-topbar .local-pill{display:flex;align-items:center;gap:7px;color:#73838d;font-size:11px;white-space:nowrap}.manager-topbar .local-pill i{width:7px;height:7px;border-radius:50%;background:#42aa7f}.language-toggle{padding:5px 8px;margin-left:5px;border:1px solid #cbd9dc;border-radius:4px;background:#fff;color:#176b64;font-size:11px}
.sim-page { min-height:100vh; padding:28px clamp(18px,3vw,48px) 60px; color:#dceaf4; background:radial-gradient(circle at 85% 5%,rgba(34,111,150,.22),transparent 32%),#07121c; }
.sim-topbar { max-width:1480px; margin:0 auto 22px; display:grid; grid-template-columns:220px 1fr auto; align-items:center; gap:20px; }
.sim-topbar h1,.panel-card h2 { margin:3px 0 0; color:#f5fbff; }
.eyebrow { margin:0; font-size:11px; letter-spacing:.18em; color:#6dc9ef; font-weight:800; }
.back-btn,.engine-chip { border:1px solid rgba(136,185,211,.22); background:rgba(12,29,42,.76); color:#bdd2de; border-radius:10px; padding:10px 13px; }
.back-btn { cursor:pointer; justify-self:start; }
.engine-chip { font-size:12px; }
.sim-layout { max-width:1480px; margin:auto; display:grid; grid-template-columns:minmax(390px,520px) minmax(0,1fr); gap:20px; align-items:start; }
.panel-card { border:1px solid rgba(118,176,207,.16); border-radius:16px; background:linear-gradient(180deg,rgba(15,33,47,.95),rgba(9,23,34,.96)); box-shadow:0 18px 55px rgba(0,0,0,.2); padding:22px; }
.controls { position:sticky; top:18px; }
.output-column { display:grid; gap:20px; }
.section-title { display:flex; justify-content:space-between; align-items:flex-start; gap:16px; margin-bottom:18px; }
.section-title > span { font-size:12px; color:#8eafc1; }
.field { display:grid; gap:7px; margin-bottom:13px; color:#9fbdcd; font-size:12px; }
.field select,.field input,.gear-row select { width:100%; box-sizing:border-box; border:1px solid rgba(116,174,204,.2); border-radius:9px; background:#091923; color:#e2f0f7; padding:10px 11px; outline:none; }
.field select:focus,.field input:focus,.gear-row select:focus { border-color:#4eb8e8; }
.equipment-block { margin:20px 0; border-top:1px solid rgba(136,185,211,.12); border-bottom:1px solid rgba(136,185,211,.12); padding:18px 0 10px; }
.subheading { display:flex; align-items:baseline; gap:10px; margin-bottom:12px; }
.subheading small { color:#728f9f; }
.gear-row { display:grid; grid-template-columns:48px 1fr; gap:8px 10px; align-items:center; margin-bottom:11px; }
.slot-name { color:#a9c5d5; font-size:12px; }
.gear-stats { grid-column:2; display:flex; flex-wrap:wrap; gap:5px; }
.gear-stats span,.event-list span,.active-sets span { font-size:10px; color:#8cb6cc; border:1px solid rgba(108,178,214,.14); background:rgba(26,70,91,.2); padding:4px 7px; border-radius:999px; }
.input-grid { display:grid; grid-template-columns:1fr 1fr; gap:0 12px; }
.check-field { display:flex; align-items:center; gap:9px; color:#a9c5d5; font-size:12px; margin:3px 0 16px; }
.run-btn { width:100%; border:0; border-radius:10px; padding:13px; background:linear-gradient(90deg,#2b99ca,#42b9e4); color:#04131b; font-weight:900; cursor:pointer; }
.run-btn:disabled { opacity:.45; cursor:wait; }
.method-note { color:#7899aa; font-size:11px; line-height:1.65; margin:12px 2px 0; }
.notice,.error-box,.warning-box { max-width:1480px; margin:30px auto; border:1px solid rgba(223,126,83,.25); border-radius:10px; padding:12px 14px; background:rgba(96,43,28,.18); color:#efb89e; }
.metric-strip,.panel-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:10px; }
.metric-strip > div,.panel-grid > div { padding:13px; border-radius:10px; background:rgba(5,17,26,.5); border:1px solid rgba(119,178,207,.1); display:grid; gap:5px; }
.metric-strip small,.panel-grid span,.damage-hero small { color:#7f9faf; font-size:10px; }
.metric-strip strong,.panel-grid strong { color:#eaf8ff; }
.skill-grid { display:flex; flex-wrap:wrap; gap:8px; margin-top:14px; }
.skill-chip { min-width:160px; display:grid; gap:3px; padding:10px; border-radius:9px; background:rgba(19,58,76,.34); }
.skill-chip span { color:#89aaba; font-size:10px; }
.damage-hero { display:grid; grid-template-columns:1.45fr 1fr; gap:12px; margin:8px 0 22px; }
.damage-primary,.damage-secondary { padding:20px; border-radius:13px; display:grid; gap:6px; background:linear-gradient(135deg,rgba(35,124,164,.25),rgba(12,31,43,.4)); border:1px solid rgba(76,181,228,.2); }
.damage-primary strong { font-size:34px; color:#7fdcff; }
.damage-secondary strong { font-size:25px; color:#e3f4fb; }
.damage-primary span,.damage-secondary span { color:#789aab; font-size:10px; }
.result-card h3 { margin:22px 0 10px; font-size:13px; color:#b9d5e4; }
.panel-grid { grid-template-columns:repeat(5,1fr); }
.data-table { border-top:1px solid rgba(115,174,205,.12); }
.data-row { display:grid; grid-template-columns:1fr auto 70px; gap:12px; padding:9px 4px; border-bottom:1px solid rgba(115,174,205,.09); align-items:center; }
.data-row span { color:#92afbe; font-size:12px; }
.data-row small { text-align:right; color:#6eaac8; }
.event-list,.active-sets { display:flex; flex-wrap:wrap; gap:7px; }
.active-sets { margin-top:18px; align-items:center; }
.active-sets strong { font-size:12px; margin-right:4px; }
.coverage.full { color:#72d8aa; }.coverage.partial { color:#f1b375; }
.warning-box { margin:18px 0 0; }.warning-box strong { display:block; margin-bottom:6px; }.warning-box p { margin:4px 0; font-size:11px; }
.validation-card > p { margin:8px 0; padding:10px; display:grid; grid-template-columns:25px 1fr; gap:8px; background:rgba(5,17,26,.42); border-radius:8px; color:#aac3d0; font-size:12px; line-height:1.5; }
.validation-card > p strong { color:#58c8f3; }.validation-card details { margin-top:14px; color:#8eacbb; font-size:12px; }.validation-card li { margin:7px 0; line-height:1.55; }
@media (max-width:980px) { .sim-topbar { grid-template-columns:1fr auto; }.sim-topbar > div:nth-child(2){grid-column:1/-1;grid-row:1}.back-btn{grid-row:2}.sim-layout{grid-template-columns:1fr}.controls{position:static}.panel-grid{grid-template-columns:repeat(2,1fr)} }
@media (max-width:600px) { .sim-page{padding:18px 12px 40px}.engine-chip{display:none}.sim-topbar{display:flex;flex-direction:column;align-items:flex-start}.input-grid,.damage-hero,.metric-strip{grid-template-columns:1fr}.panel-grid{grid-template-columns:1fr 1fr}.gear-row{grid-template-columns:42px 1fr} }

/* Simulation uses the same restrained data-workspace language as DataManager. */
.sim-page{padding:0 0 60px;color:#1c2a33;background:#f4f7f8}
.sim-topbar{max-width:none;margin:0 0 42px;padding:0 clamp(20px,5vw,72px);height:68px;display:grid;grid-template-columns:1fr auto;gap:22px;border-bottom:1px solid #dce5e8;background:rgba(255,255,255,.96);box-shadow:0 1px 0 rgba(31,55,65,.02)}
.sim-topbar h1,.panel-card h2{color:#1c2a33}.sim-topbar h1{font-size:22px}.sim-topbar .eyebrow{color:#72838d}.engine-chip{border:1px solid #dce5e8;border-radius:4px;background:#f7fafb;color:#73838d;padding:9px 12px;font-size:11px}
.sim-layout{max-width:1460px;padding:0 clamp(20px,5vw,72px);grid-template-columns:minmax(340px,420px) minmax(0,1fr);gap:16px}
.panel-card{padding:22px;border:1px solid #dce5e8;border-radius:0;background:#fff;box-shadow:0 10px 30px rgba(31,55,65,.06)}.controls{top:84px}.output-column{gap:16px}.section-title{margin-bottom:18px}.section-title>span{color:#73838d;font-size:11px}.eyebrow{color:#72838d;font-size:10px}
.field{color:#73838d;font-size:11px}.field select,.field input,.gear-row select{border:1px solid #dce5e8;border-radius:4px;background:#f7fafb;color:#1c2a33;padding:10px 11px}.field select:focus,.field input:focus,.gear-row select:focus{border-color:#6eb8af;outline:2px solid #dff2ef;outline-offset:0}
.equipment-block{border-color:#e5edef}.subheading small{color:#82919a}.slot-name{color:#536873}.gear-stats span,.event-list span,.active-sets span{border:1px solid #cfe3df;border-radius:4px;background:#eaf6f3;color:#237d70}
.check-field{color:#536873}.run-btn{border-radius:4px;background:#1c2a33;color:#fff}.run-btn:hover{background:#29434e}.method-note{color:#82919a}.notice,.error-box,.warning-box{margin:0 auto 16px;border:1px solid #c8e4dc;border-radius:0;background:#eef8f5;color:#247a6d}.error-box,.warning-box{border-color:#ecd1cb;background:#fff7f5;color:#a34f47}
.metric-strip,.panel-grid{gap:8px}.metric-strip>div,.panel-grid>div{border:1px solid #e5edef;border-radius:0;background:#f7fafb}.metric-strip small,.panel-grid span,.damage-hero small{color:#82919a}.metric-strip strong,.panel-grid strong{color:#1c2a33}.skill-chip{border-radius:4px;background:#eaf6f3}.skill-chip span{color:#6c8988}
.damage-primary,.damage-secondary{border:1px solid #cfe3df;border-radius:4px;background:#eef8f5}.damage-primary strong{color:#1f8176}.damage-secondary strong{color:#1c2a33}.damage-primary span,.damage-secondary span{color:#73838d}
.result-card h3{color:#536873}.data-table{border-color:#e5edef}.data-row{border-color:#edf1f3}.data-row span{color:#536873}.data-row small{color:#238b80}.active-sets strong{color:#536873}.coverage.full{color:#237d70}.coverage.partial{color:#a06e16}
.validation-card>p{border-radius:4px;background:#f7fafb;color:#536873}.validation-card>p strong{color:#238b80}.validation-card details{color:#73838d}
@media (max-width:980px){.sim-topbar{grid-template-columns:1fr auto}.sim-topbar>div:nth-child(2){grid-column:auto;grid-row:auto}.sim-layout{padding-left:20px;padding-right:20px}}
@media (max-width:600px){.sim-page{padding-left:0;padding-right:0}.sim-topbar{height:auto;min-height:68px;padding:10px 4vw;margin-bottom:30px}.sim-layout{padding-left:4vw;padding-right:4vw}.engine-chip{display:none}}
</style>
