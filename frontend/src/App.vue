<script setup>
import { computed, onMounted, ref } from 'vue'

const db = ref(null)
const termsDb = ref(null)
const currentLanguage = ref(localStorage.getItem('eaia-language') || 'zh-CN')
const search = ref('')
const category = ref([])
const tier = ref([])
const slot = ref('all')
const selectedSet = ref(null)
const categoryMenuOpen = ref(false)
const tierMenuOpen = ref(false)
const currentView = ref('dictionary')
const dictionaryTable = ref('equipment_library')
const equipmentRecords = ref(null)
const equipmentTable = ref('v_equipment_full')

const categoryMeta = {
  output: { label: '输出', tone: 'coral', mark: '↗' },
  defense: { label: '防御', tone: 'teal', mark: '◆' },
  healing: { label: '治疗', tone: 'violet', mark: '+' },
  buff: { label: '增益', tone: 'amber', mark: '✦' },
}

const categories = computed(() => db.value?.equipment_categories ?? [])
const slots = computed(() => db.value?.equipment_slots ?? [])
const tiers = computed(() => db.value?.set_tiers ?? [])
const effects = computed(() => db.value?.set_effects ?? [])

const filteredSets = computed(() => {
  if (!db.value) return []
  const query = search.value.trim().toLowerCase()
  return db.value.sets.filter((item) => {
    const relatedEffects = effects.value.filter((effect) => effect.set_id === item.set_id)
    const searchable = [item.set_name, item.notes, ...relatedEffects.map((effect) => effect.stat_type)].join(' ').toLowerCase()
    return (!query || searchable.includes(query)) &&
      (!category.value.length || category.value.includes(item.category_id)) &&
      (!tier.value.length || tier.value.includes(item.set_tier_id)) &&
      (slot.value === 'all' || item.slot_group === slot.value)
  })
})

const activeCount = computed(() => db.value?.sets.filter((item) => item.active).length ?? 0)
const selectedEffects = computed(() => selectedSet.value ? effects.value.filter((item) => item.set_id === selectedSet.value.set_id) : [])
const dictionaryTables = computed(() => Object.entries(db.value ?? {}).filter(([, value]) => Array.isArray(value)).map(([name, rows]) => ({ name, rows, count: rows.length })))
const selectedDictionary = computed(() => dictionaryTables.value.find((table) => table.name === dictionaryTable.value) ?? dictionaryTables.value[0])
const termRows = computed(() => Object.values(termsDb.value?.terms ?? {}))
const uiText = computed(() => currentLanguage.value === 'zh-CN' ? {
  codex: '装备图鉴', localData: '本地数据模式', dictionary: '游戏字典', equipment: '已有装备', heroes: '英雄图鉴', recommendation: '装备推荐',
  equipmentTitle: '已有装备', equipmentSubtitle: '查看数据库中已录入装备、属性和 OCR 识别信息。', dictionaryTitle: '游戏字典', dictionarySubtitle: '查看本地数据库中的基础定义、规则和套装数据。'
} : {
  codex: 'Equipment Codex', localData: 'Local data', dictionary: 'Game Dictionary', equipment: 'Recorded Gear', heroes: 'Hero Codex', recommendation: 'Recommendations',
  equipmentTitle: 'Recorded Gear', equipmentSubtitle: 'Browse recorded gear, stats, and OCR recognition data.', dictionaryTitle: 'Game Dictionary', dictionarySubtitle: 'Browse local definitions, rules, and set data.'
})
function isIdentifierColumn(column) { return /^(id|.*_id)$/i.test(column) }
const dictionaryColumns = computed(() => {
  const rows = selectedDictionary.value?.rows ?? []
  return [...new Set(rows.flatMap((row) => Object.keys(row)))].filter((column) => !isIdentifierColumn(column)).slice(0, 8)
})
const equipmentTableLabels = { v_equipment_full: '装备总览', equipment: '装备主记录', equipment_stats: '装备属性', equipment_recognition: '装备识别记录' }
const activeEquipmentRows = computed(() => equipmentRecords.value?.[equipmentTable.value] ?? [])
const equipmentColumns = computed(() => [...new Set(activeEquipmentRows.value.flatMap((row) => Object.keys(row)))].filter((column) => !isIdentifierColumn(column)))

function metaFor(item) { return categoryMeta[item.category_id] ?? { label: '其他', tone: 'slate', mark: '•' } }
function tierName(id) { return tiers.value.find((item) => item.set_tier_id === id)?.set_tier_name ?? id }
function slotName(group) { return group === 'left' ? '左侧两件套' : '右侧三件套' }
function formatEffect(effect) {
  const stat = db.value?.stat_definitions?.find((item) => item.stat_type === effect.stat_type)
  const label = stat?.display_name ?? effect.stat_type
  const value = effect.value
  const formatted = typeof value === 'number' && value < 1 && value > 0 ? `${Math.round(value * 100)}%` : value
  return `${label} ${formatted > 0 ? '+' : ''}${formatted}`
}
function resetFilters() { search.value = ''; category.value = []; tier.value = []; slot.value = 'all'; categoryMenuOpen.value = false; tierMenuOpen.value = false }
function toggleCategory(id) { category.value = category.value.includes(id) ? category.value.filter((item) => item !== id) : [...category.value, id] }
function toggleTier(id) { tier.value = tier.value.includes(id) ? tier.value.filter((item) => item !== id) : [...tier.value, id] }
function categoryLabel() { return category.value.length ? `已选 ${category.value.length} 类` : '全部定位' }
function tierLabel() { return tier.value.length ? `已选 ${tier.value.length} 阶` : '全部阶级' }
function dictionaryLabel(name) {
  const labels = { equipment_library: '装备库', equipment_categories: '装备分类', equipment_slots: '装备部位', set_tiers: '套装阶级', gear_qualities: '装备品质', stat_definitions: '属性定义', sets: '套装', set_effects: '套装效果', stat_category_map: '属性分类映射', stat_slot_rules: '部位属性规则', stat_value_ranges: '属性数值范围', main_stat_max_values: '主属性上限', ocr_aliases: 'OCR 别名', set_evolutions: '套装进阶', special_effect_definitions: '特殊效果' }
  return labels[name] ?? name
}
function termLabel(term) { return term?.[currentLanguage.value] ?? term?.['zh-CN'] ?? term?.['en-US'] ?? term?.id ?? '—' }
function toggleLanguage() {
  currentLanguage.value = currentLanguage.value === 'zh-CN' ? 'en-US' : 'zh-CN'
  localStorage.setItem('eaia-language', currentLanguage.value)
}

onMounted(async () => {
  const [dictionaryResponse, equipmentResponse, termsResponse] = await Promise.all([fetch('/equipment_v22_seed.json'), fetch('/equipment_records.json'), fetch('/terms.json')])
  db.value = await dictionaryResponse.json()
  equipmentRecords.value = await equipmentResponse.json()
  termsDb.value = await termsResponse.json()
})
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <div class="brand"><span class="brand-icon">E</span><span>EAIA <em>{{ uiText.codex }}</em></span></div>
      <nav class="main-nav" aria-label="主导航">
        <div class="nav-dropdown"><button class="nav-item" :class="{ active: currentView === 'dictionary' }" @click="currentView = 'dictionary'">游戏字典 <span class="nav-caret">⌄</span></button><div class="nav-menu"><button class="nav-library-link" @click="dictionaryTable = 'equipment_library'; currentView = 'dictionary'">装备库<small>已录入套装浏览</small></button><button v-for="table in dictionaryTables" :key="table.name" @click="dictionaryTable = table.name; currentView = 'dictionary'">{{ dictionaryLabel(table.name) }}<small>{{ table.name }}</small></button></div></div>
        <button class="nav-item" :class="{ active: currentView === 'equipment' }" @click="currentView = 'equipment'">{{ uiText.equipment }}</button>
        <button class="nav-item" :class="{ active: currentView === 'heroes' }" @click="currentView = 'heroes'">{{ uiText.heroes }}</button>
        <button class="nav-item" :class="{ active: currentView === 'recommendation' }" @click="currentView = 'recommendation'">{{ uiText.recommendation }}</button>
      </nav>
      <div class="topbar-status"><span class="status-dot"></span> {{ uiText.localData }} <span class="version">CN-2026-08</span><button class="language-toggle" @click="toggleLanguage">{{ currentLanguage === 'zh-CN' ? 'EN' : '中' }}</button></div>
    </header>

    <main v-if="currentView === 'equipment'" class="page equipment-records-page">
      <section class="intro"><div><p class="eyebrow">COLLECTION / RECORDED EQUIPMENT</p><h1>{{ uiText.equipmentTitle }}</h1><p class="subtitle">{{ uiText.equipmentSubtitle }}</p></div><div class="intro-stats"><div><strong>{{ equipmentRecords?.equipment?.length ?? 0 }}</strong><span>装备主记录</span></div><div><strong>{{ equipmentRecords?.equipment_stats?.length ?? 0 }}</strong><span>属性记录</span></div><div><strong>{{ equipmentRecords?.equipment_recognition?.length ?? 0 }}</strong><span>识别记录</span></div></div></section>
      <section v-if="equipmentRecords" class="record-panel"><div class="record-tabs"><button v-for="(label, table) in equipmentTableLabels" :key="table" :class="{ selected: equipmentTable === table }" @click="equipmentTable = table"><span>{{ label }}</span><small>{{ equipmentRecords[table].length }} 条</small></button></div><div class="record-heading"><div><p class="eyebrow">TABLE / {{ equipmentTable }}</p><h2>{{ equipmentTableLabels[equipmentTable] }}</h2></div><span>{{ activeEquipmentRows.length }} 条记录</span></div><div class="record-table-scroll"><table class="record-table"><thead><tr><th v-if="equipmentTable === 'v_equipment_full'">ID</th><th v-for="column in equipmentColumns" :key="column">{{ column }}</th></tr></thead><tbody><tr v-for="(row, index) in activeEquipmentRows" :key="row.item_id ?? index"><td v-if="equipmentTable === 'v_equipment_full'" class="display-index">{{ index + 1 }}</td><td v-for="column in equipmentColumns" :key="column">{{ row[column] === null || row[column] === undefined || row[column] === '' ? '—' : typeof row[column] === 'object' ? JSON.stringify(row[column]) : row[column] }}</td></tr></tbody></table></div></section><div v-else class="loading">正在读取已录入装备...</div>
      <template v-if="false">
      <section class="intro">
        <div>
          <p class="eyebrow">COLLECTION / EQUIPMENT</p>
          <h1>装备库</h1>
          <p class="subtitle">浏览套装属性，快速了解每一件装备的定位与效果。</p>
        </div>
        <div class="intro-stats">
          <div><strong>{{ activeCount }}</strong><span>套可用套装</span></div>
          <div><strong>{{ slots.length }}</strong><span>装备部位</span></div>
          <div><strong>{{ categories.length }}</strong><span>功能分类</span></div>
        </div>
      </section>

      <section class="toolbar" aria-label="筛选装备">
        <label class="search-box"><span>⌕</span><input v-model="search" placeholder="搜索套装、属性或关键词" /></label>
        <div class="multi-select" @click.stop>
          <button class="multi-trigger" @click="categoryMenuOpen = !categoryMenuOpen; tierMenuOpen = false">{{ categoryLabel() }} <span>⌄</span></button>
          <div v-if="categoryMenuOpen" class="multi-menu"><label class="multi-option"><input type="checkbox" :checked="!category.length" @change="category = []" /><span>全部定位</span></label><label v-for="item in categories" :key="item.category_id" class="multi-option"><input type="checkbox" :checked="category.includes(item.category_id)" @change="toggleCategory(item.category_id)" /><span>{{ item.category_name }}</span></label></div>
        </div>
        <div class="multi-select" @click.stop>
          <button class="multi-trigger" @click="tierMenuOpen = !tierMenuOpen; categoryMenuOpen = false">{{ tierLabel() }} <span>⌄</span></button>
          <div v-if="tierMenuOpen" class="multi-menu"><label class="multi-option"><input type="checkbox" :checked="!tier.length" @change="tier = []" /><span>全部阶级</span></label><label v-for="item in tiers" :key="item.set_tier_id" class="multi-option"><input type="checkbox" :checked="tier.includes(item.set_tier_id)" @change="toggleTier(item.set_tier_id)" /><span>{{ item.set_tier_name }}</span></label></div>
        </div>
        <select v-model="slot"><option value="all">全部套装组</option><option value="left">左侧两件套</option><option value="right">右侧三件套</option></select>
        <button class="reset-button" @click="resetFilters">重置筛选</button>
      </section>

      <div class="content-heading"><div><span class="result-count">{{ filteredSets.length }}</span> 套装备结果</div><div class="legend"><span><i class="legend-dot active"></i> 已录入</span><span><i class="legend-dot"></i> 待核对中文名</span></div></div>

      <section v-if="db" class="set-grid">
        <article v-for="item in filteredSets" :key="item.set_id" class="set-card" :class="`card-${metaFor(item).tone}`" @click="selectedSet = item">
          <div class="card-top"><span class="category-mark">{{ metaFor(item).mark }}</span><span class="tier-chip">{{ tierName(item.set_tier_id) }}</span></div>
          <div class="set-name-row"><h2>{{ item.set_name }}</h2><span class="arrow">↗</span></div>
          <p class="set-note">{{ item.notes || '暂无备注' }}</p>
          <div class="card-footer"><span class="category-label">{{ metaFor(item).label }}</span><span>{{ item.required_pieces }} 件激活</span><span>{{ slotName(item.slot_group) }}</span></div>
        </article>
      </section>
      <div v-else class="loading">正在读取本地装备数据...</div>
      <div v-if="db && !filteredSets.length" class="empty"><strong>没有找到匹配的套装</strong><button @click="resetFilters">清除筛选条件</button></div>
      </template>
    </main>

    <main v-else-if="currentView === 'dictionary'" class="page dictionary-page">
      <section class="intro dictionary-intro"><div><p class="eyebrow">REFERENCE / DATABASE</p><h1>{{ uiText.dictionaryTitle }}</h1><p class="subtitle">{{ uiText.dictionarySubtitle }}</p></div><div class="intro-stats"><div><strong>{{ dictionaryTables.length }}</strong><span>{{ currentLanguage === 'zh-CN' ? '字典表' : 'Tables' }}</span></div><div><strong>{{ selectedDictionary?.count ?? 0 }}</strong><span>{{ currentLanguage === 'zh-CN' ? '当前记录' : 'Current rows' }}</span></div></div></section>
      <section v-if="db" class="dictionary-layout"><aside class="dictionary-sidebar"><p class="sidebar-title">数据表</p><button class="dictionary-link" :class="{ selected: dictionaryTable === 'equipment_library' }" @click="dictionaryTable = 'equipment_library'"><span>装备库</span><small>{{ activeCount }}</small></button><button class="dictionary-link" :class="{ selected: dictionaryTable === 'terminology' }" @click="dictionaryTable = 'terminology'"><span>术语对照</span><small>{{ termRows.length }}</small></button><button v-for="table in dictionaryTables" :key="table.name" class="dictionary-link" :class="{ selected: dictionaryTable === table.name }" @click="dictionaryTable = table.name"><span>{{ dictionaryLabel(table.name) }}</span><small>{{ table.count }}</small></button></aside><section class="dictionary-table-wrap">
        <template v-if="dictionaryTable === 'equipment_library'"><div class="library-content"><section class="toolbar" aria-label="筛选装备"><label class="search-box"><span>⌕</span><input v-model="search" placeholder="搜索套装、属性或关键词" /></label><div class="multi-select" @click.stop><button class="multi-trigger" @click="categoryMenuOpen = !categoryMenuOpen; tierMenuOpen = false">{{ categoryLabel() }} <span>⌄</span></button><div v-if="categoryMenuOpen" class="multi-menu"><label class="multi-option"><input type="checkbox" :checked="!category.length" @change="category = []" /><span>全部定位</span></label><label v-for="item in categories" :key="item.category_id" class="multi-option"><input type="checkbox" :checked="category.includes(item.category_id)" @change="toggleCategory(item.category_id)" /><span>{{ item.category_name }}</span></label></div></div><div class="multi-select" @click.stop><button class="multi-trigger" @click="tierMenuOpen = !tierMenuOpen; categoryMenuOpen = false">{{ tierLabel() }} <span>⌄</span></button><div v-if="tierMenuOpen" class="multi-menu"><label class="multi-option"><input type="checkbox" :checked="!tier.length" @change="tier = []" /><span>全部阶级</span></label><label v-for="item in tiers" :key="item.set_tier_id" class="multi-option"><input type="checkbox" :checked="tier.includes(item.set_tier_id)" @change="toggleTier(item.set_tier_id)" /><span>{{ item.set_tier_name }}</span></label></div></div><select v-model="slot"><option value="all">全部套装组</option><option value="left">左侧两件套</option><option value="right">右侧三件套</option></select><button class="reset-button" @click="resetFilters">重置筛选</button></section><div class="content-heading"><div><span class="result-count">{{ filteredSets.length }}</span> 套装备结果</div><div class="legend"><span><i class="legend-dot active"></i> 已录入</span><span><i class="legend-dot"></i> 待核对中文名</span></div></div><section class="set-grid"><article v-for="item in filteredSets" :key="item.set_id" class="set-card" :class="`card-${metaFor(item).tone}`" @click="selectedSet = item"><div class="card-top"><span class="category-mark">{{ metaFor(item).mark }}</span><span class="tier-chip">{{ tierName(item.set_tier_id) }}</span></div><div class="set-name-row"><h2>{{ item.set_name }}</h2><span class="arrow">↗</span></div><p class="set-note">{{ item.notes || '暂无备注' }}</p><div class="card-footer"><span class="category-label">{{ metaFor(item).label }}</span><span>{{ item.required_pieces }} 件激活</span><span>{{ slotName(item.slot_group) }}</span></div></article></section></div></template>
        <template v-else-if="dictionaryTable === 'terminology'"><div class="table-heading"><div><p class="eyebrow">TERMINOLOGY / {{ termsDb?.version ?? '—' }}</p><h2>术语对照</h2></div><span>{{ termRows.length }} 条记录 · {{ currentLanguage === 'zh-CN' ? '中文' : 'English' }}</span></div><div class="data-table-scroll"><table><thead><tr><th>ID</th><th>当前语言</th><th>{{ currentLanguage === 'zh-CN' ? 'English' : '中文' }}</th><th>分组</th><th>备注</th></tr></thead><tbody><tr v-for="term in termRows" :key="term.id"><td>{{ term.id }}</td><td>{{ termLabel(term) }}</td><td>{{ currentLanguage === 'zh-CN' ? term['en-US'] || '—' : term['zh-CN'] || '待确认' }}</td><td>{{ term.section }}</td><td>{{ term.notes || '—' }}</td></tr></tbody></table></div></template>
        <template v-else><div class="table-heading"><div><p class="eyebrow">TABLE / {{ selectedDictionary?.name }}</p><h2>{{ dictionaryLabel(selectedDictionary?.name) }}</h2></div><span>{{ selectedDictionary?.count ?? 0 }} 条记录</span></div><div class="data-table-scroll"><table><thead><tr><th v-for="column in dictionaryColumns" :key="column">{{ column }}</th></tr></thead><tbody><tr v-for="(row, index) in selectedDictionary?.rows" :key="row.id ?? row[`${dictionaryColumns[0]}`] ?? index"><td v-for="column in dictionaryColumns" :key="column">{{ row[column] === null || row[column] === undefined ? '—' : typeof row[column] === 'object' ? JSON.stringify(row[column]) : row[column] }}</td></tr></tbody></table></div></template>
      </section></section>
      <div v-else class="loading">正在读取本地字典...</div>
    </main>

    <main v-else class="page placeholder-page"><p class="eyebrow">{{ currentView === 'heroes' ? 'HEROES / CODEX' : 'RECOMMENDATION / BUILDS' }}</p><h1>{{ currentView === 'heroes' ? '英雄图鉴' : '装备推荐' }}</h1><p>该页面暂未设计，后续将在现有本地数据基础上继续实现。</p><button @click="currentView = 'equipment'">返回已有装备</button></main>

    <div v-if="selectedSet" class="modal-backdrop" @click.self="selectedSet = null">
      <aside class="detail-panel"><button class="close-button" aria-label="关闭详情" @click="selectedSet = null">×</button><p class="eyebrow">SET DETAIL / {{ selectedSet.set_id }}</p><div class="detail-title"><span class="category-mark large">{{ metaFor(selectedSet).mark }}</span><div><h2>{{ selectedSet.set_name }}</h2><p>{{ metaFor(selectedSet).label }} · {{ tierName(selectedSet.set_tier_id) }}</p></div></div><div class="detail-rule"><span>激活条件</span><strong>{{ selectedSet.required_pieces }} 件装备</strong></div><h3>套装效果</h3><div v-if="selectedEffects.length" class="effect-list"><div v-for="effect in selectedEffects" :key="effect.effect_id" class="effect-row"><span class="effect-icon">✦</span><div><strong>{{ formatEffect(effect) }}</strong><p>{{ effect.notes || '被动效果' }}</p></div></div></div><p v-else class="muted">暂未录入套装效果。</p><div class="data-note">数据版本 {{ selectedSet.game_version }}<br>{{ selectedSet.notes }}</div></aside>
    </div>
  </div>
</template>
