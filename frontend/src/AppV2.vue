<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import officialBase from '../../official_hero_seed.json'
import officialExtra from '../../official_hero_seed_extra.json'

const currentView = ref('heroes')
const db = ref(null)
const equipmentRecords = ref(null)
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

const HERO_FIELDS = ['hero_key','hero_name','title','faction','role','completeness','mechanic_summary','source_url','source_kind','source_date']
const SKILL_FIELDS = ['hero_key','skill_key','skill_name','skill_type','description','coefficient','target_cap','duration','direct_damage','optimizer_usable','source_url','source_date','value_json']
const STAT_LABELS = {
  ATK_FLAT:'攻击', ATK_PCT:'攻击加成', HP_FLAT:'生命', HP_PCT:'生命加成', DEF_FLAT:'防御', DEF_PCT:'防御加成',
  CRIT_RATE:'暴击率', CRIT_DMG:'暴击伤害', ATK_SPEED:'攻击速度', RAGE_REGEN:'怒气回复', HEALING_EFFECT:'治疗效果'
}
const PCT_STATS = new Set(['ATK_PCT','HP_PCT','DEF_PCT','CRIT_RATE','CRIT_DMG','RAGE_REGEN'])
const SLOTS = ['weapon','armor','bracelet','necklace','ring']
const SLOT_LABELS = { weapon:'武器', armor:'护甲', bracelet:'手镯', necklace:'项链', ring:'戒指' }

function rowToObject(row, fields) {
  return Object.fromEntries(fields.map((key, index) => [key, row?.[index] ?? null]))
}
function mergeOfficialRows(baseRows, extraRows, fields, keyFn) {
  const map = new Map()
  for (const row of [...(baseRows || []), ...(extraRows || [])]) {
    const item = rowToObject(row, fields)
    map.set(keyFn(item), item)
  }
  return [...map.values()]
}
function factionList(value) {
  return String(value || '').split(/[\/、,，]/).map(x => x.trim()).filter(Boolean)
}
function matchesFaction(hero, selected) {
  return selected === 'all' || factionList(hero.faction).includes(selected)
}

const officialHeroes = computed(() => {
  const heroes = mergeOfficialRows(officialBase.heroes, officialExtra.heroes, HERO_FIELDS, x => x.hero_key)
  const skills = mergeOfficialRows(officialBase.skills, officialExtra.skills, SKILL_FIELDS, x => `${x.hero_key}:${x.skill_key}`)
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

const dictionaryTables = computed(() => Object.entries(db.value || {}).filter(([, value]) => Array.isArray(value)).map(([name, rows]) => ({ name, rows })))
const selectedDictionary = computed(() => dictionaryTables.value.find(x => x.name === dictionaryTable.value) || dictionaryTables.value[0] || { name:'', rows:[] })
const dictionaryColumns = computed(() => [...new Set(selectedDictionary.value.rows.flatMap(row => Object.keys(row)))].slice(0, 10))
const equipmentTableLabels = { v_equipment_full:'装备总览', equipment:'装备主记录', equipment_stats:'装备属性', equipment_recognition:'OCR 识别记录' }
const activeEquipmentRows = computed(() => equipmentRecords.value?.[equipmentTable.value] || [])
const equipmentColumns = computed(() => [...new Set(activeEquipmentRows.value.flatMap(row => Object.keys(row)))].slice(0, 12))

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
    map.set(row.item_id, { item_id:row.item_id, slot, set_id:row.set_id || 'NONE', set_name:row.set_name || row.set_id || '—', stats:[] })
  }
  for (const row of statRows) {
    const item = map.get(row.item_id)
    if (!item || row.is_unlocked === 0) continue
    item.stats.push({ type:row.stat_type, value:normalizeStatValue(row.stat_type, row.stat_value ?? row.estimate_override) })
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
  }
  return score
}
function activeSetEffects(items) {
  const counts = new Map()
  items.forEach(item => counts.set(item.set_id, (counts.get(item.set_id) || 0) + 1))
  const setRows = db.value?.sets || []
  const effectRows = db.value?.set_effects || []
  const active = []
  for (const [setId, count] of counts) {
    const def = setRows.find(x => x.set_id === setId)
    if (def && count >= Number(def.required_pieces || 99)) active.push(setId)
  }
  const effects = effectRows.filter(x => active.includes(x.set_id) && (x.trigger === 'always' || !x.trigger) && Number(x.enabled_in_optimizer ?? 1) !== 0)
  return { active, effects }
}
function scoreBuild(items, basicSkill) {
  const stats = Object.fromEntries(Object.keys(STAT_LABELS).map(k => [k, 0]))
  items.forEach(item => item.stats.forEach(stat => { stats[stat.type] = (stats[stat.type] || 0) + stat.value }))
  const { active, effects } = activeSetEffects(items)
  const effectStat = type => effects.filter(x => (x.stat_type || x.effect_type) === type).reduce((sum, x) => sum + normalizeStatValue(type, x.value), 0)
  const atk = (1000 + stats.ATK_FLAT) * (1 + stats.ATK_PCT + effectStat('ATK_PCT'))
  const critRateRaw = 0.05 + stats.CRIT_RATE + effectStat('CRIT_RATE')
  const critRate = Math.min(1, critRateRaw)
  const critDmg = 1.5 + stats.CRIT_DMG + effectStat('CRIT_DMG')
  const critFactor = (1 - critRate) + critRate * critDmg
  const targetCap = basicSkill.target_cap === 'all' ? recEnemyCount.value : Number(basicSkill.target_cap || 1)
  const targets = recMode.value === 'aoe' ? Math.min(Number(recEnemyCount.value), targetCap) : 1
  const dps = atk * Number(basicSkill.coefficient) * critFactor * targets
  return { dps, active, panel:{ atk, critRate, critOverflow:Math.max(0, critRateRaw - 1), critDmg } }
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
              const items = [weapon, armor, bracelet, necklace, ring]
              results.push({ ...scoreBuild(items, basic), items })
            }
    results.sort((a,b) => b.dps - a.dps || a.items.map(x => x.item_id).join('|').localeCompare(b.items.map(x => x.item_id).join('|')))
    const best = results[0]?.dps || 0
    recResults.value = results.slice(0, Number(recTopK.value)).map((row, index) => ({
      ...row, rank:index + 1, delta:best ? (best - row.dps) / best : 0
    }))
    recMessage.value = recSource.value === 'validation'
      ? `已使用固定随机种子生成 ${sourceItems.length} 件验证装备。结果为标准化基础攻击评分，不代表游戏实战 DPS。`
      : `已从当前装备中选取每槽最多 8 件候选完成五槽组合排序。`
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

onMounted(async () => {
  const responses = await Promise.allSettled([fetch('/equipment_v22_seed.json'), fetch('/equipment_records.json')])
  if (responses[0].status === 'fulfilled' && responses[0].value.ok) db.value = await responses[0].value.json()
  if (responses[1].status === 'fulfilled' && responses[1].value.ok) equipmentRecords.value = await responses[1].value.json()
  selectedHeroKey.value = officialHeroes.value[0]?.hero_key || null
  if (!numericHeroes.value.some(x => x.hero_key === recHeroKey.value)) recHeroKey.value = numericHeroes.value[0]?.hero_key || ''
  loading.value = false
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
        <div><p class="eyebrow">RECOMMENDATION / BUILDS</p><h1>装备推荐</h1><p class="subtitle">先按职业与阵营缩小英雄范围，再选择英雄进行五槽组合排序。</p></div>
        <div class="intro-stats"><div><strong>{{ numericHeroes.length }}</strong><span>可验证英雄</span></div><div><strong>{{ filteredNumericHeroes.length }}</strong><span>筛选后英雄</span></div><div><strong>{{ inventoryItems().length }}</strong><span>当前装备</span></div></div>
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
          <p class="model-note">网页端使用官方已确认普攻倍率 + 标准化基础面板作快速配装验证；完整实战模型仍由 Python 模拟器承担。</p>
        </aside>

        <section class="recommend-results">
          <div class="result-head"><div><p class="eyebrow">RESULTS / TOP BUILDS</p><h2>推荐结果</h2></div><span v-if="recResults.length">{{ recResults.length }} 套</span></div>
          <div v-if="recMessage" class="result-message" :class="{warning:!recResults.length}">{{ recMessage }}</div>
          <div v-if="!recResults.length" class="recommend-empty"><strong>设置条件后开始计算</strong><p>可先用职业、阵营筛选英雄；若本地装备不全，可切换“随机验证装备”。</p></div>
          <article v-for="result in recResults" :key="result.items.map(x=>x.item_id).join('|')" class="build-card">
            <div class="build-rank"><span>#{{ result.rank }}</span><div><strong>{{ result.dps.toFixed(2) }}</strong><small>标准化评分</small></div><em v-if="result.rank>1">-{{ (result.delta*100).toFixed(2) }}%</em><em v-else>BEST</em></div>
            <div class="build-panel"><span>ATK <strong>{{ result.panel.atk.toFixed(0) }}</strong></span><span>暴击 <strong>{{ formatPercent(result.panel.critRate) }}</strong></span><span>暴伤 <strong>{{ formatPercent(result.panel.critDmg) }}</strong></span><span>暴击溢出 <strong>{{ formatPercent(result.panel.critOverflow) }}</strong></span></div>
            <div class="gear-strip"><div v-for="item in result.items" :key="item.item_id" class="gear-chip"><span>{{ SLOT_LABELS[item.slot] }}</span><strong>{{ item.item_id }}</strong><small>{{ item.stats.map(s=>formatStat(s.type,s.value)).join(' · ') || '无可用词条' }}</small></div></div>
            <div class="active-sets"><span>激活套装</span><strong>{{ result.active.length ? result.active.join(' / ') : '无' }}</strong></div>
          </article>
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
