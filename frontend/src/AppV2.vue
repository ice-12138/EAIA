<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { bestAscensionState, setName as catalogSetName } from './equipmentAscension.js'

const VIEWS = new Set(['dictionary', 'equipment', 'heroes', 'recommendation', 'scanner'])

function viewFromHash() {
  const value = window.location.hash.replace(/^#\/?/, '')
  if (VIEWS.has(value)) return value
  const saved = window.sessionStorage.getItem('eaia.currentView')
  return VIEWS.has(saved) ? saved : 'heroes'
}

const currentView = ref(viewFromHash())
const db = ref(null)
const equipmentRecords = ref(null)
const heroCatalog = ref(null)
const loading = ref(true)

const heroSearch = ref('')
const heroRole = ref('all')
const heroFaction = ref('all')
const heroCompleteness = ref('all')
const selectedHeroKey = ref(null)

const recRole = ref('all')
const recFaction = ref('all')
const recHeroKey = ref('MORRIGAN')
const recMode = ref('single')
const recEnemyCount = ref(1)
const recTopK = ref(5)
const recSource = ref('inventory')
const recResults = ref([])
const recMessage = ref('')
const recRunning = ref(false)

const dictionaryTable = ref('sets')
const equipmentTable = ref('v_equipment_full')
const deviceState = ref({ status: 'unknown', connected: false, targets: [] })
const scanState = ref({ status: 'idle', completed: 0, total: null, row: null, column: null, elapsed_seconds: 0, average_seconds: null, new_count: 0, duplicate_count: 0, updated_count: 0 })
const deviceChecking = ref(false)
const scanMode = ref('full')
const resumeRow = ref(1)
const resumeColumn = ref(0)
let scanPollTimer = null

const STAT_LABELS = {
  ATK_FLAT:'攻击', ATK_PCT:'攻击加成', HP_FLAT:'生命', HP_PCT:'生命加成', DEF_FLAT:'防御', DEF_PCT:'防御加成',
  CRIT_RATE:'暴击率', CRIT_DMG:'暴击伤害', ATK_SPEED:'攻击速度', RAGE_REGEN:'怒气回复', HEALING_EFFECT:'治疗效果'
}
const PCT_STATS = new Set(['ATK_PCT','HP_PCT','DEF_PCT','CRIT_RATE','CRIT_DMG','RAGE_REGEN'])
const SLOTS = ['weapon','armor','bracelet','necklace','ring']
const SLOT_LABELS = { weapon:'武器', armor:'护甲', bracelet:'手镯', necklace:'项链', ring:'戒指' }
const EQUIPMENT_TYPE_LABELS = { output:'输出', defense:'防御', healing:'恢复', buff:'辅助' }
const EQUIPMENT_OVERVIEW_COLUMNS = [
  '序号', '装备品质', '装备等级', '套装名称', '装备部位',
  '主词条', '主词条数值',
  '副词条1', '副词条1数值', '副词条2', '副词条2数值',
  '副词条3', '副词条3数值', '副词条4', '副词条4数值',
  '装备类型'
]

function factionList(value) {
  return String(value || '').split(/[\/、,，]/).map(x => x.trim()).filter(Boolean)
}
function matchesFaction(hero, selected) {
  return selected === 'all' || factionList(hero.faction).includes(selected)
}

const officialHeroes = computed(() => {
  const heroes = heroCatalog.value?.heroes || []
  const skills = heroCatalog.value?.skills || []
  return heroes
    .map(hero => ({ ...hero, skills: skills.filter(skill => skill.hero_key === hero.hero_key) }))
    .sort((a, b) => String(a.hero_name || '').localeCompare(String(b.hero_name || ''), 'zh-CN'))
})

const roles = computed(() => [...new Set(officialHeroes.value.map(x => x.role).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'zh-CN')))
const factions = computed(() => [...new Set(officialHeroes.value.flatMap(x => factionList(x.faction)))].sort((a,b)=>a.localeCompare(b,'zh-CN')))

const filteredHeroes = computed(() => {
  const q = heroSearch.value.trim().toLowerCase()
  return officialHeroes.value.filter(hero => {
    const haystack = [hero.hero_name, hero.title, hero.faction, hero.role, hero.mechanic_summary, hero.hero_key].filter(Boolean).join(' ').toLowerCase()
    return (!q || haystack.includes(q))
      && (heroRole.value === 'all' || hero.role === heroRole.value)
      && matchesFaction(hero, heroFaction.value)
      && (heroCompleteness.value === 'all' || hero.completeness === heroCompleteness.value)
  })
})
const selectedHero = computed(() => officialHeroes.value.find(x => x.hero_key === selectedHeroKey.value) || null)
const numericHeroes = computed(() => officialHeroes.value.filter(hero =>
  hero.skills.some(skill => skill.skill_type === 'basic' && skill.optimizer_usable && skill.coefficient != null && skill.target_cap != null)
))
const recRoles = computed(() => [...new Set(numericHeroes.value.map(x => x.role).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'zh-CN')))
const recFactions = computed(() => [...new Set(numericHeroes.value.flatMap(x => factionList(x.faction)))].sort((a,b)=>a.localeCompare(b,'zh-CN')))
const filteredNumericHeroes = computed(() => numericHeroes.value.filter(hero =>
  (recRole.value === 'all' || hero.role === recRole.value) && matchesFaction(hero, recFaction.value)
))
const officialCounts = computed(() => ({
  heroes: officialHeroes.value.length,
  skills: officialHeroes.value.reduce((sum, hero) => sum + hero.skills.length, 0),
  numeric: numericHeroes.value.length,
  factions: factions.value.length
}))

watch([recRole, recFaction], () => {
  if (!filteredNumericHeroes.value.some(hero => hero.hero_key === recHeroKey.value)) {
    recHeroKey.value = filteredNumericHeroes.value[0]?.hero_key || ''
  }
  recResults.value = []
  recMessage.value = ''
})

watch(currentView, view => {
  const nextHash = `#/${view}`
  window.sessionStorage.setItem('eaia.currentView', view)
  if (window.location.hash !== nextHash) window.location.hash = nextHash
})

function restoreViewFromHash() {
  currentView.value = viewFromHash()
}

const dictionaryTables = computed(() => Object.entries(db.value || {}).filter(([, value]) => Array.isArray(value)).map(([name, rows]) => ({ name, rows })))
const selectedDictionary = computed(() => dictionaryTables.value.find(x => x.name === dictionaryTable.value) || dictionaryTables.value[0] || { name:'', rows:[] })
const dictionaryColumns = computed(() => [...new Set(selectedDictionary.value.rows.flatMap(row => Object.keys(row)))].slice(0, 10))
const equipmentTableLabels = { v_equipment_full:'装备总览', equipment:'装备主记录', equipment_stats:'装备属性', equipment_recognition:'OCR 识别记录' }
const equipmentOverviewRows = computed(() => {
  const qualityNames = new Map((db.value?.gear_qualities || []).map(row => [row.quality_id, String(row.quality_name || row.quality_id).replace(/品质$/, '')]))
  const slotNames = new Map((db.value?.equipment_slots || []).map(row => [row.slot_id, row.slot_name || row.slot_id]))
  const setCategoryByName = new Map((db.value?.sets || []).filter(row => row.set_name && row.category_id).map(row => [row.set_name, row.category_id]))
  const unique = new Map()
  for (const row of equipmentRecords.value?.v_equipment_full || []) {
    if (!row.item_id || unique.has(row.item_id)) continue
    const categoryId = row.category_id || setCategoryByName.get(row.set_name)
    unique.set(row.item_id, {
      '装备品质': qualityNames.get(row.quality_id) || row.quality_id || '—',
      '装备等级': row.enhancement_level ?? row.level ?? '—',
      '套装名称': row.set_name || '—',
      '装备部位': slotNames.get(row.slot_id) || SLOT_LABELS[row.slot_id] || row.slot_id || '—',
      '主词条': row.main_stat_name || '—',
      '主词条数值': row.main_stat_value ?? '—',
      '副词条1': row.sub_stat_1_name || '—',
      '副词条1数值': row.sub_stat_1_value ?? '—',
      '副词条2': row.sub_stat_2_name || '—',
      '副词条2数值': row.sub_stat_2_value ?? '—',
      '副词条3': row.sub_stat_3_name || '—',
      '副词条3数值': row.sub_stat_3_value ?? '—',
      '副词条4': row.sub_stat_4_name || '—',
      '副词条4数值': row.sub_stat_4_value ?? '—',
      '装备类型': EQUIPMENT_TYPE_LABELS[categoryId] || categoryId || '—'
    })
  }
  return [...unique.values()].map((row, index) => ({ '序号': index + 1, ...row }))
})
const activeEquipmentRows = computed(() => equipmentTable.value === 'v_equipment_full'
  ? equipmentOverviewRows.value
  : (equipmentRecords.value?.[equipmentTable.value] || []))
const equipmentColumns = computed(() => equipmentTable.value === 'v_equipment_full'
  ? EQUIPMENT_OVERVIEW_COLUMNS
  : [...new Set(activeEquipmentRows.value.flatMap(row => Object.keys(row)))].slice(0, 12))

function completenessLabel(value) {
  return ({ numeric_complete:'数值完整', numeric_partial:'部分数值', mechanic_only:'机制资料', identity_only:'档案资料' })[value] || value || '—'
}
function completenessTone(value) {
  return ({ numeric_complete:'good', numeric_partial:'partial', mechanic_only:'mechanic', identity_only:'muted' })[value] || 'muted'
}
function formatPercent(value) {
  return `${(Number(value) * 100).toFixed(Math.abs(Number(value) * 100) >= 10 ? 0 : 1)}%`
}
function formatSkillValue(skill) {
  const bits = []
  if (skill.coefficient != null) bits.push(`倍率 ${formatPercent(skill.coefficient)}`)
  if (skill.target_cap != null) bits.push(`目标 ${skill.target_cap}`)
  if (skill.duration != null) bits.push(`持续 ${skill.duration}s`)
  return bits.join(' · ') || '官方未公开完整数值'
}
function sourceHost(url) {
  try { return new URL(url).hostname.replace('www.', '') } catch { return 'official' }
}
function nullText(value) {
  return value === null || value === undefined || value === '' ? '—' : value
}
function displaySetName(setId) {
  return catalogSetName(db.value, setId)
}
function ascensionForItem(result, itemId) {
  return result.ascendedItems?.find(entry => entry.item_id === itemId) || null
}
function ascensionSummary(result) {
  return (result.ascendedItems || [])
    .map(entry => `${entry.item_id}（${entry.from_set_name} → ${entry.to_set_name}）`)
    .join('；')
}

function normalizeStatValue(type, value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return 0
  if (PCT_STATS.has(type) && Math.abs(n) > 3) return n / 100
  return n
}
function inventoryItems() {
  const baseRows = equipmentRecords.value?.equipment || []
  const statRows = equipmentRecords.value?.equipment_stats || []
  const map = new Map()
  for (const row of baseRows) {
    const slot = row.slot_id || row.slot
    if (!SLOTS.includes(slot) || row.available === 0) continue
    map.set(row.item_id, {
      item_id:row.item_id,
      slot,
      set_id:row.set_id || 'NONE',
      set_name:displaySetName(row.set_id) || row.set_name || row.set_id || '—',
      stats:[]
    })
  }
  for (const row of statRows) {
    const item = map.get(row.item_id)
    if (!item || row.is_unlocked === 0) continue
    const rawValue = row.stat_value ?? row.estimate_override
    if (rawValue === null || rawValue === undefined) continue
    item.stats.push({ type:row.stat_type, value:normalizeStatValue(row.stat_type, rawValue) })
  }
  return [...map.values()]
}
function mulberry32(seed) {
  let a = seed >>> 0
  return () => {
    a |= 0
    a = a + 0x6D2B79F5 | 0
    let t = Math.imul(a ^ a >>> 15, 1 | a)
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t
    return ((t ^ t >>> 14) >>> 0) / 4294967296
  }
}
function validationItems() {
  const rand = mulberry32(20260825)
  const statPool = [
    ['ATK_FLAT',30,220], ['ATK_PCT',0.03,0.35], ['CRIT_RATE',0.02,0.24], ['CRIT_DMG',0.04,0.40], ['HP_PCT',0.03,0.30], ['DEF_PCT',0.03,0.30]
  ]
  const result = []
  for (const slot of SLOTS) {
    for (let i = 0; i < 4; i++) {
      const pool = [...statPool].sort(() => rand() - 0.5).slice(0, 4)
      result.push({
        item_id:`VAL_${slot.toUpperCase()}_${String(i).padStart(2,'0')}`,
        slot,
        set_id:'VAL_NONE',
        set_name:'随机验证装备',
        stats:pool.map(([type, low, high]) => ({ type, value:low + rand() * (high - low) }))
      })
    }
  }
  return result
}
function itemPotential(item) {
  let score = 0
  for (const stat of item.stats) {
    if (stat.type === 'ATK_FLAT') score += stat.value / 1000
    if (stat.type === 'ATK_PCT') score += stat.value
    if (stat.type === 'CRIT_RATE') score += stat.value * 1.2
    if (stat.type === 'CRIT_DMG') score += stat.value * 0.45
    if (stat.type === 'ATK_SPEED') score += stat.value / 250
  }
  return score
}
function runRecommendation() {
  recRunning.value = true
  recResults.value = []
  recMessage.value = ''
  try {
    const hero = numericHeroes.value.find(x => x.hero_key === recHeroKey.value)
    const basic = hero?.skills.find(skill => skill.skill_type === 'basic' && skill.optimizer_usable && skill.coefficient != null && skill.target_cap != null)
    if (!hero || !basic) throw new Error('当前职业/阵营筛选下没有可直接数值化的英雄。')
    const sourceItems = recSource.value === 'validation' ? validationItems() : inventoryItems()
    const bySlot = Object.fromEntries(SLOTS.map(slot => [
      slot,
      sourceItems.filter(item => item.slot === slot).sort((a,b) => itemPotential(b) - itemPotential(a)).slice(0, 8)
    ]))
    const missing = SLOTS.filter(slot => !bySlot[slot].length)
    if (missing.length) {
      throw new Error(`当前装备数据缺少：${missing.map(x => SLOT_LABELS[x]).join('、')}。可切换到“随机验证装备”查看完整效果。`)
    }
    const results = []
    for (const weapon of bySlot.weapon)
      for (const armor of bySlot.armor)
        for (const bracelet of bySlot.bracelet)
          for (const necklace of bySlot.necklace)
            for (const ring of bySlot.ring) {
              const physicalItems = [weapon, armor, bracelet, necklace, ring]
              const best = bestAscensionState(physicalItems, basic, db.value, {
                enemyCount:recEnemyCount.value,
                mode:recMode.value,
                normalizeStatValue,
              })
              results.push(best)
            }
    results.sort((a,b) => b.dps - a.dps || a.items.map(x => x.item_id).join('|').localeCompare(b.items.map(x => x.item_id).join('|')))
    const bestScore = results[0]?.dps || 0
    recResults.value = results.slice(0, Number(recTopK.value)).map((row, index) => ({
      ...row, rank:index + 1, delta:bestScore ? (bestScore - row.dps) / bestScore : 0
    }))
    const evolvedCount = Number(db.value?.set_evolutions?.length || 0)
    const recommendedAscensions = recResults.value.reduce((sum, row) => sum + (row.ascendedItems?.length || 0), 0)
    const sourceText = recSource.value === 'validation'
      ? `已使用固定随机种子生成 ${sourceItems.length} 件验证装备。`
      : `已从当前装备中选取每槽最多 8 件候选完成五槽组合排序。`
    recMessage.value = `${sourceText} 已对 ${evolvedCount} 组可升华 T1→T2 套装同时计算当前特性与升华后特性；同一套实物装备只保留更优状态，分数相同时保留 T1。当前 Top-K 共建议 ${recommendedAscensions} 件按升华后计算。`
  } catch (error) {
    recMessage.value = error.message || String(error)
  } finally {
    recRunning.value = false
  }
}
function formatStat(type, value) {
  if (PCT_STATS.has(type)) return `${STAT_LABELS[type] || type} ${formatPercent(value)}`
  return `${STAT_LABELS[type] || type} ${Number(value).toFixed(Number.isInteger(Number(value)) ? 0 : 1)}`
}

function scanStatusLabel(status) {
  return ({ idle:'尚未开始', checking:'连接检测中', starting:'准备启动', scanning:'检测中', completed:'检测完成', failed:'检测失败' })[status] || status || '未知'
}
function formatSeconds(value) {
  if (value == null) return '—'
  const seconds = Number(value)
  if (!Number.isFinite(seconds)) return '—'
  if (seconds < 60) return `${seconds.toFixed(1)} 秒`
  return `${Math.floor(seconds / 60)} 分 ${Math.round(seconds % 60)} 秒`
}
async function checkDevice() {
  deviceChecking.value = true
  deviceState.value = { ...deviceState.value, status: 'checking', connected: false }
  try {
    const response = await fetch('/api/device/check')
    const data = await response.json()
    if (!response.ok) throw new Error(data.error || '连接检测失败')
    deviceState.value = { ...data, status: 'connected' }
  } catch (error) {
    deviceState.value = { status: 'failed', connected: false, error: error.message || String(error), targets: [] }
  } finally {
    deviceChecking.value = false
  }
}
async function pollScanStatus() {
  try {
    const response = await fetch('/api/scan/status')
    if (!response.ok) return
    scanState.value = await response.json()
    if (!['scanning', 'starting'].includes(scanState.value.status)) stopScanPolling()
  } catch { /* the next poll will retry */ }
}
function startScanPolling() {
  stopScanPolling()
  pollScanStatus()
  scanPollTimer = window.setInterval(pollScanStatus, 1000)
}
function stopScanPolling() {
  if (scanPollTimer) window.clearInterval(scanPollTimer)
  scanPollTimer = null
}
async function startEquipmentScan() {
  if (!deviceState.value.connected || ['starting', 'scanning'].includes(scanState.value.status)) return
  try {
    const response = await fetch('/api/scan/start', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(scanMode.value === 'resume'
        ? { resume_row: Number(resumeRow.value), resume_column: Number(resumeColumn.value) }
        : { resume_row: 1, resume_column: 0 })
    })
    const data = await response.json()
    if (!response.ok) throw new Error(data.error || '无法启动识别')
    scanState.value = data
    startScanPolling()
  } catch (error) {
    scanState.value = { ...scanState.value, status: 'failed', error: error.message || String(error) }
  }
}

onMounted(async () => {
  window.addEventListener('hashchange', restoreViewFromHash)
  if (window.location.hash !== `#/${currentView.value}`) window.location.hash = `#/${currentView.value}`
  const responses = await Promise.allSettled([fetch('/api/catalog'), fetch('/api/equipment'), fetch('/api/heroes')])
  if (responses[0].status === 'fulfilled' && responses[0].value.ok) db.value = await responses[0].value.json()
  if (responses[1].status === 'fulfilled' && responses[1].value.ok) equipmentRecords.value = await responses[1].value.json()
  if (responses[2].status === 'fulfilled' && responses[2].value.ok) heroCatalog.value = await responses[2].value.json()
  selectedHeroKey.value = officialHeroes.value[0]?.hero_key || null
  if (!numericHeroes.value.some(x => x.hero_key === recHeroKey.value)) recHeroKey.value = numericHeroes.value[0]?.hero_key || ''
  loading.value = false
  if (currentView.value === 'scanner') checkDevice()
})

onUnmounted(() => {
  window.removeEventListener('hashchange', restoreViewFromHash)
  stopScanPolling()
})
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <div class="brand"><span class="brand-icon">E</span><span>EAIA <em>装备与英雄数据库</em></span></div>
      <nav class="main-nav" aria-label="主导航">
        <button class="nav-item" :class="{active:currentView==='dictionary'}" @click="currentView='dictionary'">游戏字典</button>
        <button class="nav-item" :class="{active:currentView==='equipment'}" @click="currentView='equipment'">已有装备</button>
        <button class="nav-item" :class="{active:currentView==='heroes'}" @click="currentView='heroes'">英雄图鉴</button>
        <button class="nav-item" :class="{active:currentView==='recommendation'}" @click="currentView='recommendation'">装备推荐</button>
        <button class="nav-item" :class="{active:currentView==='scanner'}" @click="currentView='scanner'; checkDevice()">识别装备</button>
      </nav>
      <div class="topbar-status"><span class="status-dot"></span> 本地数据 <span class="version">CN-2026-08</span></div>
    </header>

    <main v-if="currentView==='heroes'" class="page">
      <section class="intro">
        <div><p class="eyebrow">HEROES / CODEX</p><h1>英雄图鉴</h1><p class="subtitle">官方公开资料优先；无法检索确认的字段保留为 NULL，并在页面显示为“—”。</p></div>
        <div class="intro-stats">
          <div><strong>{{ officialCounts.heroes }}</strong><span>已收录英雄</span></div>
          <div><strong>{{ officialCounts.skills }}</strong><span>技能记录</span></div>
          <div><strong>{{ officialCounts.factions }}</strong><span>可识别阵营</span></div>
          <div><strong>{{ officialCounts.numeric }}</strong><span>可数值验证</span></div>
        </div>
      </section>

      <section class="hero-toolbar toolbar">
        <label class="search-box"><span>⌕</span><input v-model="heroSearch" placeholder="搜索英雄、称号、阵营或机制" /></label>
        <select v-model="heroRole"><option value="all">全部职业</option><option v-for="role in roles" :key="role" :value="role">{{ role }}</option></select>
        <select v-model="heroFaction"><option value="all">全部阵营</option><option v-for="faction in factions" :key="faction" :value="faction">{{ faction }}</option></select>
        <select v-model="heroCompleteness">
          <option value="all">全部资料等级</option>
          <option value="numeric_complete">数值完整</option>
          <option value="numeric_partial">部分数值</option>
          <option value="mechanic_only">机制资料</option>
          <option value="identity_only">档案资料</option>
        </select>
      </section>

      <div class="content-heading">
        <div><strong>{{ filteredHeroes.length }}</strong> 名英雄</div>
        <div class="legend"><span>职业：{{ heroRole==='all' ? '全部' : heroRole }}</span><span>阵营：{{ heroFaction==='all' ? '全部' : heroFaction }}</span></div>
      </div>

      <section class="hero-layout">
        <div class="hero-grid">
          <article v-for="hero in filteredHeroes" :key="hero.hero_key" class="hero-card" :class="{selected:selectedHeroKey===hero.hero_key}" @click="selectedHeroKey=hero.hero_key">
            <div class="hero-card-top"><span class="hero-monogram">{{ hero.hero_name?.slice(0,1) || '?' }}</span><span class="status-chip" :class="completenessTone(hero.completeness)">{{ completenessLabel(hero.completeness) }}</span></div>
            <h2>{{ hero.hero_name }}</h2>
            <p class="hero-title">{{ nullText(hero.title) }}</p>
            <div class="hero-meta"><span>{{ nullText(hero.role) }}</span><span>{{ nullText(hero.faction) }}</span></div>
            <p class="hero-summary">{{ nullText(hero.mechanic_summary) }}</p>
            <div class="hero-card-footer"><span>{{ hero.skills.length }} 条技能资料</span><span>{{ hero.skills.some(x=>x.optimizer_usable) ? '可参与验证' : '暂不可精确模拟' }}</span></div>
          </article>
          <div v-if="!filteredHeroes.length" class="empty-inline">当前筛选条件没有匹配英雄。</div>
        </div>

        <aside v-if="selectedHero" class="hero-detail">
          <div class="detail-sticky">
            <div class="detail-hero-head"><span class="hero-monogram large">{{ selectedHero.hero_name?.slice(0,1) }}</span><div><p class="eyebrow">{{ selectedHero.hero_key }}</p><h2>{{ selectedHero.hero_name }}</h2><p>{{ nullText(selectedHero.title) }}</p></div></div>
            <div class="detail-tags">
              <span>职业：{{ nullText(selectedHero.role) }}</span>
              <span v-for="faction in factionList(selectedHero.faction)" :key="faction">阵营：{{ faction }}</span>
              <span v-if="!factionList(selectedHero.faction).length">阵营：—</span>
              <span class="status-chip" :class="completenessTone(selectedHero.completeness)">{{ completenessLabel(selectedHero.completeness) }}</span>
            </div>
            <p class="detail-summary">{{ nullText(selectedHero.mechanic_summary) }}</p>
            <div class="readiness"><strong>{{ selectedHero.skills.some(x=>x.optimizer_usable) ? '具备基础数值验证条件' : '暂不具备精确模拟条件' }}</strong><span>数据库只写入可确认字段；其余字段保持 NULL。</span></div>
            <h3>技能资料</h3>
            <div v-if="selectedHero.skills.length" class="skill-list">
              <article v-for="skill in selectedHero.skills" :key="skill.skill_key" class="skill-row">
                <div class="skill-row-head"><div><span class="skill-type">{{ skill.skill_type }}</span><strong>{{ skill.skill_name }}</strong></div><span v-if="skill.optimizer_usable" class="optimizer-badge">算法可用</span></div>
                <p>{{ nullText(skill.description) }}</p><div class="skill-values">{{ formatSkillValue(skill) }}</div>
              </article>
            </div>
            <p v-else class="empty-inline">当前仅有可确认的英雄档案，技能结构化字段暂为 NULL。</p>
            <a v-if="selectedHero.source_url" class="source-link" :href="selectedHero.source_url" target="_blank" rel="noreferrer">官方来源 · {{ sourceHost(selectedHero.source_url) }} ↗</a>
          </div>
        </aside>
      </section>
    </main>

    <main v-else-if="currentView==='recommendation'" class="page">
      <section class="intro">
        <div><p class="eyebrow">RECOMMENDATION / BUILDS</p><h1>装备推荐</h1><p class="subtitle">可升华的 T1 装备会同时按当前 T1 套装特性和可达 T2 套装特性计算；最终只保留同一套实物装备中更优的状态，并明确标记需要升华的装备。</p></div>
        <div class="intro-stats"><div><strong>{{ numericHeroes.length }}</strong><span>可验证英雄</span></div><div><strong>{{ filteredNumericHeroes.length }}</strong><span>筛选后英雄</span></div><div><strong>{{ db?.set_evolutions?.length || 0 }}</strong><span>T1→T2 路径</span></div><div><strong>{{ inventoryItems().length }}</strong><span>当前装备</span></div></div>
      </section>

      <section class="recommend-shell">
        <aside class="recommend-controls">
          <p class="eyebrow">BUILD CONFIG</p><h2>计算条件</h2>
          <label><span>职业</span><select v-model="recRole"><option value="all">全部职业</option><option v-for="role in recRoles" :key="role" :value="role">{{ role }}</option></select></label>
          <label><span>阵营</span><select v-model="recFaction"><option value="all">全部阵营</option><option v-for="faction in recFactions" :key="faction" :value="faction">{{ faction }}</option></select></label>
          <label><span>英雄</span><select v-model="recHeroKey" :disabled="!filteredNumericHeroes.length"><option v-for="hero in filteredNumericHeroes" :key="hero.hero_key" :value="hero.hero_key">{{ hero.hero_name }} · {{ hero.role || '—' }} · {{ hero.faction || '—' }}</option></select></label>
          <p v-if="!filteredNumericHeroes.length" class="empty-inline">当前职业/阵营组合下没有具备基础数值验证条件的英雄。</p>
          <label><span>场景</span><select v-model="recMode"><option value="single">单体</option><option value="aoe">群体</option></select></label>
          <label v-if="recMode==='aoe'"><span>敌人数</span><input v-model.number="recEnemyCount" type="number" min="1" max="20" /></label>
          <label><span>Top-K</span><select v-model.number="recTopK"><option :value="3">Top 3</option><option :value="5">Top 5</option><option :value="10">Top 10</option></select></label>
          <div class="source-switch"><span>装备来源</span><button :class="{selected:recSource==='inventory'}" @click="recSource='inventory'">当前装备</button><button :class="{selected:recSource==='validation'}" @click="recSource='validation'">随机验证装备</button></div>
          <button class="primary-button" :disabled="recRunning || !filteredNumericHeroes.length" @click="runRecommendation">{{ recRunning ? '计算中…' : '开始配装计算' }}</button>
          <p class="model-note">网页端使用官方已确认普攻倍率 + 标准化基础面板作快速排序，并已纳入静态 T1/T2 套装、攻速和固定追加伤害。含暴击叠层、终结技叠层等动态套装时，以 Python 60 秒模拟器结果为精确依据。</p>
        </aside>

        <section class="recommend-results">
          <div class="result-head"><div><p class="eyebrow">RESULTS / TOP BUILDS</p><h2>推荐结果</h2></div><span v-if="recResults.length">{{ recResults.length }} 套</span></div>
          <div v-if="recMessage" class="result-message" :class="{warning:!recResults.length}">{{ recMessage }}</div>
          <div v-if="!recResults.length" class="recommend-empty"><strong>设置条件后开始计算</strong><p>可先用职业、阵营筛选英雄；若本地装备不全，可切换“随机验证装备”。</p></div>
          <article v-for="result in recResults" :key="result.items.map(x=>x.item_id).join('|')" class="build-card">
            <div class="build-rank"><span>#{{ result.rank }}</span><div><strong>{{ result.dps.toFixed(2) }}</strong><small>标准化评分</small></div><span v-if="result.ascendedItems?.length" class="optimizer-badge">含升华计算</span><em v-if="result.rank>1">-{{ (result.delta*100).toFixed(2) }}%</em><em v-else>BEST</em></div>
            <div v-if="result.ascendedItems?.length" class="result-message"><strong>升华建议：</strong>{{ ascensionSummary(result) }}。这些装备在本条推荐中按升华后的 T2 套装特性计算，装备自身主/副词条保持不变。</div>
            <div v-if="result.dynamicEffects?.length" class="result-message warning">该方案包含 {{ result.dynamicEffects.length }} 条需要战斗时间轴处理的动态套装效果；网页评分用于快速筛选，最终伤害请以 Python 60 秒模拟器精算结果为准。</div>
            <div class="build-panel"><span>ATK <strong>{{ result.panel.atk.toFixed(0) }}</strong></span><span>暴击 <strong>{{ formatPercent(result.panel.critRate) }}</strong></span><span>暴伤 <strong>{{ formatPercent(result.panel.critDmg) }}</strong></span><span>攻速 <strong>{{ result.panel.attackSpeedPoints.toFixed(0) }}</strong></span><span>暴击溢出 <strong>{{ formatPercent(result.panel.critOverflow) }}</strong></span></div>
            <div class="gear-strip">
              <div v-for="item in result.items" :key="item.item_id" class="gear-chip">
                <span>{{ SLOT_LABELS[item.slot] }}</span>
                <strong>{{ item.item_id }} <em v-if="ascensionForItem(result,item.item_id)" class="optimizer-badge">升华后</em></strong>
                <small v-if="ascensionForItem(result,item.item_id)">{{ ascensionForItem(result,item.item_id).from_set_name }} → {{ ascensionForItem(result,item.item_id).to_set_name }} · 按 T2 特性计算</small>
                <small>{{ item.stats.map(s=>formatStat(s.type,s.value)).join(' · ') || '无可用词条' }}</small>
              </div>
            </div>
            <div class="active-sets"><span>激活套装</span><strong>{{ result.active.length ? result.active.map(displaySetName).join(' / ') : '无' }}</strong></div>
          </article>
        </section>
      </section>
    </main>

    <main v-else-if="currentView==='scanner'" class="page">
      <section class="intro"><div><p class="eyebrow">DEVICE / EQUIPMENT OCR</p><h1>识别装备</h1><p class="subtitle">连接手机后启动识别脚本，实时查看装备数量、扫描位置和执行状态。</p></div><div class="intro-stats"><div><strong>{{ scanState.completed }}<small>/ {{ scanState.total ?? '—' }}</small></strong><span>已检测装备</span></div><div><strong>{{ formatSeconds(scanState.elapsed_seconds) }}</strong><span>已用时间</span></div><div><strong>{{ formatSeconds(scanState.average_seconds) }}</strong><span>平均时间</span></div></div></section>
      <section class="scanner-layout">
        <aside class="scanner-control">
          <p class="eyebrow">CONNECTION</p><h2>手机连接</h2>
          <div class="device-status" :class="deviceState.connected ? 'connected' : 'disconnected'"><span class="status-dot"></span><div><strong>{{ deviceState.connected ? '手机已连接' : (deviceState.status === 'checking' ? '正在检测连接' : '手机未连接') }}</strong><small>{{ deviceState.serial || deviceState.error || '请确认 HOScrcpy / HDC 已连接目标设备' }}</small></div></div>
          <button class="secondary-button" :disabled="deviceChecking || scanState.status==='scanning'" @click="checkDevice">{{ deviceChecking ? '检测中…' : '重新检测连接' }}</button>
          <div class="scan-mode"><span>识别方式</span><button :class="{selected:scanMode==='full'}" @click="scanMode='full'">从头识别</button><button :class="{selected:scanMode==='resume'}" @click="scanMode='resume'">继续识别</button></div>
          <div v-if="scanMode==='resume'" class="resume-fields"><label><span>上次识别行</span><input v-model.number="resumeRow" type="number" min="1" /></label><label><span>上次识别列</span><input v-model.number="resumeColumn" type="number" min="0" max="8" /></label><p>请手动将该行调整为手机画面的第三行；第 1、2 行会自动按实际可见位置处理。</p></div>
          <button class="primary-button" :disabled="!deviceState.connected || ['starting','scanning'].includes(scanState.status)" @click="startEquipmentScan">{{ ['starting','scanning'].includes(scanState.status) ? '识别进行中…' : '开始识别' }}</button>
          <p class="model-note">开始前请将手机停留在游戏装备背包页面。识别过程中不要手动操作手机。</p>
        </aside>
        <section class="scanner-progress">
          <div class="result-head"><div><p class="eyebrow">LIVE PROGRESS</p><h2>识别进度</h2></div><span class="status-chip" :class="scanState.status==='completed' ? 'good' : scanState.status==='failed' ? 'partial' : ''">{{ scanStatusLabel(scanState.status) }}</span></div>
          <div class="progress-track"><span :style="{width: `${scanState.total ? Math.min(100, scanState.completed / scanState.total * 100) : 0}%`}"></span></div>
          <div class="scan-metrics"><div><small>当前检测装备数</small><strong>{{ scanState.completed }} / {{ scanState.total ?? '—' }}</strong></div><div><small>当前位置</small><strong>{{ scanState.row ? `第 ${scanState.row} 行 · 第 ${scanState.column} 个` : '—' }}</strong></div><div><small>当前状态</small><strong>{{ scanStatusLabel(scanState.status) }}</strong></div></div>
          <div v-if="scanState.status === 'completed'" class="import-summary"><div><strong>{{ scanState.new_count }}</strong><small>新入库</small></div><div><strong>{{ scanState.duplicate_count }}</strong><small>重复</small></div><div><strong>{{ scanState.updated_count }}</strong><small>更新</small></div></div>
          <div v-if="scanState.error" class="result-message warning">{{ scanState.error }}</div>
          <div v-else class="scanner-empty">{{ scanState.status === 'completed' ? '本次装备识别已完成，结果已写入装备数据库。' : '连接检测通过后，点击“开始识别”运行装备 OCR 脚本。' }}</div>
        </section>
      </section>
    </main>

    <main v-else-if="currentView==='equipment'" class="page">
      <section class="intro"><div><p class="eyebrow">COLLECTION / RECORDED EQUIPMENT</p><h1>已有装备</h1><p class="subtitle">数据库导出的装备、属性与 OCR 识别事实。</p></div><div class="intro-stats"><div><strong>{{ equipmentRecords?.equipment?.length || 0 }}</strong><span>装备记录</span></div><div><strong>{{ equipmentRecords?.equipment_stats?.length || 0 }}</strong><span>属性记录</span></div></div></section>
      <section class="data-panel"><div class="record-tabs"><button v-for="(label, table) in equipmentTableLabels" :key="table" :class="{selected:equipmentTable===table}" @click="equipmentTable=table">{{ label }}</button></div><div class="data-scroll"><table><thead><tr><th v-for="column in equipmentColumns" :key="column">{{ column }}</th></tr></thead><tbody><tr v-for="(row,index) in activeEquipmentRows" :key="row.item_id || index"><td v-for="column in equipmentColumns" :key="column">{{ nullText(row[column]) }}</td></tr></tbody></table></div></section>
    </main>

    <main v-else class="page">
      <section class="intro"><div><p class="eyebrow">REFERENCE / DATABASE</p><h1>游戏字典</h1><p class="subtitle">查看本地装备定义、套装、词条规则与数值字典。</p></div><div class="intro-stats"><div><strong>{{ dictionaryTables.length }}</strong><span>字典表</span></div><div><strong>{{ selectedDictionary.rows.length }}</strong><span>当前记录</span></div></div></section>
      <section class="dictionary-layout"><aside class="dictionary-sidebar"><button v-for="table in dictionaryTables" :key="table.name" :class="{selected:dictionaryTable===table.name}" @click="dictionaryTable=table.name"><span>{{ table.name }}</span><small>{{ table.rows.length }}</small></button></aside><section class="data-panel"><div class="table-title"><p class="eyebrow">TABLE</p><h2>{{ selectedDictionary.name }}</h2></div><div class="data-scroll"><table><thead><tr><th v-for="column in dictionaryColumns" :key="column">{{ column }}</th></tr></thead><tbody><tr v-for="(row,index) in selectedDictionary.rows" :key="index"><td v-for="column in dictionaryColumns" :key="column">{{ nullText(row[column]) }}</td></tr></tbody></table></div></section></section>
    </main>

    <div v-if="loading" class="loading-overlay">读取本地数据…</div>
  </div>
</template>
