<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import AppV2 from './AppV2.vue'
import HeroSimulation from './HeroSimulation.vue'
import './dataManager.css'

const props = defineProps({ mode: { type: String, default: 'dictionary' } })

const loading = ref(true)
const currentLanguage = ref(window.localStorage.getItem('eaia-language') || 'zh-CN')
const message = ref('')
const error = ref('')
const search = ref('')
const resourceCatalog = ref([])
const activeResourceId = ref('')
const resourceData = ref({ fields: [], primary_keys: [], rows: [] })
const sortState = ref({ key: '', direction: 'asc' })
const editor = ref({ open: false, mode: 'create', form: {}, originalKey: {} })

const equipmentCatalog = ref(null)
const equipmentRows = ref([])
const equipmentSort = ref({ key: 'item_id', direction: 'asc' })
const equipmentEditor = ref({ open: false, mode: 'create', originalItemId: null, form: null })

const NAV = [
  ['dictionary', '游戏数据'],
  ['equipment', '已有装备'],
  ['heroes', '英雄图鉴'],
  ['recommendation', '装备推荐'],
  ['scanner', '识别装备'],
  ['simulation', '战斗仿真'],
]

function go(view) { window.location.hash = `#/${view}` }

function toggleLanguage() {
  currentLanguage.value = currentLanguage.value === 'zh-CN' ? 'en-US' : 'zh-CN'
  window.localStorage.setItem('eaia-language', currentLanguage.value)
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options)
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.error || `请求失败 (${response.status})`)
  return data
}

function resetFeedback() { message.value = ''; error.value = '' }
function showError(value) { error.value = value?.message || String(value); message.value = '' }
function clone(value) { return JSON.parse(JSON.stringify(value)) }

const resourceGroups = computed(() => {
  const grouped = new Map()
  for (const item of resourceCatalog.value) {
    if (!grouped.has(item.group)) grouped.set(item.group, [])
    grouped.get(item.group).push(item)
  }
  return [...grouped.entries()].map(([name, items]) => ({ name, items }))
})

const activeResourceMeta = computed(() => resourceCatalog.value.find(x => x.id === activeResourceId.value) || null)
const displayFields = computed(() => resourceData.value.fields.filter(field => !field.hide_in_table).slice(0, 9))

function compareValues(a, b) {
  const av = a === null || a === undefined ? '' : a
  const bv = b === null || b === undefined ? '' : b
  const an = Number(av), bn = Number(bv)
  if (av !== '' && bv !== '' && Number.isFinite(an) && Number.isFinite(bn)) return an - bn
  return String(av).localeCompare(String(bv), 'zh-CN', { numeric: true, sensitivity: 'base' })
}

function toggleSort(field) {
  if (sortState.value.key === field) {
    sortState.value.direction = sortState.value.direction === 'asc' ? 'desc' : 'asc'
  } else {
    sortState.value = { key: field, direction: 'asc' }
  }
}

function sortMark(field, state = sortState.value) {
  if (state.key !== field) return '↕'
  return state.direction === 'asc' ? '↑' : '↓'
}

const filteredResourceRows = computed(() => {
  const q = search.value.trim().toLowerCase()
  let rows = resourceData.value.rows || []
  if (q) rows = rows.filter(row => Object.values(row).some(value => String(value ?? '').toLowerCase().includes(q)))
  const { key, direction } = sortState.value
  if (!key) return rows
  return [...rows].sort((a, b) => compareValues(a[key], b[key]) * (direction === 'asc' ? 1 : -1))
})

async function loadResource(id = activeResourceId.value) {
  if (!id) return
  loading.value = true
  resetFeedback()
  try {
    resourceData.value = await requestJson(`/api/manage/resource/${encodeURIComponent(id)}`)
    activeResourceId.value = id
    sortState.value = { key: resourceData.value.primary_keys?.[0] || resourceData.value.fields?.[0]?.name || '', direction: 'asc' }
  } catch (e) { showError(e) }
  finally { loading.value = false }
}

async function loadDictionary() {
  loading.value = true
  try {
    resourceCatalog.value = await requestJson('/api/manage/resources')
    const preferred = activeResourceId.value || 'sets'
    const first = resourceCatalog.value.some(x => x.id === preferred) ? preferred : resourceCatalog.value[0]?.id
    if (first) await loadResource(first)
  } catch (e) { showError(e); loading.value = false }
}

function emptyResourceForm() {
  const form = {}
  for (const field of resourceData.value.fields) {
    form[field.name] = field.type === 'boolean' ? false : ''
  }
  return form
}

function openResourceEditor(mode, row = null) {
  resetFeedback()
  const form = mode === 'create' ? emptyResourceForm() : clone(row)
  const originalKey = {}
  for (const key of resourceData.value.primary_keys || []) originalKey[key] = row?.[key]
  editor.value = { open: true, mode, form, originalKey }
}

function closeResourceEditor() { editor.value.open = false }

function resourcePayload() {
  const result = {}
  for (const field of resourceData.value.fields) {
    let value = editor.value.form[field.name]
    if (field.type === 'boolean') value = value ? 1 : 0
    else if (field.type === 'number' && value !== '' && value !== null && value !== undefined) value = Number(value)
    result[field.name] = value
  }
  return result
}

async function saveResource() {
  resetFeedback()
  try {
    const payload = resourcePayload()
    const url = `/api/manage/resource/${encodeURIComponent(activeResourceId.value)}`
    if (editor.value.mode === 'create') {
      await requestJson(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ values: payload }) })
      message.value = '记录已新增。'
    } else {
      await requestJson(url, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key: editor.value.originalKey, values: payload }) })
      message.value = '记录已保存。'
    }
    editor.value.open = false
    await loadResource()
  } catch (e) { showError(e) }
}

async function removeResource(row) {
  const label = activeResourceMeta.value?.title || '记录'
  if (!window.confirm(`确认删除这条${label}记录？关联数据存在时数据库会拒绝危险删除。`)) return
  const key = {}
  for (const name of resourceData.value.primary_keys || []) key[name] = row[name]
  resetFeedback()
  try {
    await requestJson(`/api/manage/resource/${encodeURIComponent(activeResourceId.value)}`, {
      method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key })
    })
    message.value = '记录已删除。'
    await loadResource()
  } catch (e) { showError(e) }
}

function formatCell(value, field) {
  if (value === null || value === undefined || value === '') return '—'
  if (field?.type === 'boolean') return Number(value) ? '是' : '否'
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 4 })
  return String(value)
}

function catalogRows(key) { return equipmentCatalog.value?.[key] || [] }
const slotOptions = computed(() => catalogRows('equipment_slots'))
const setOptions = computed(() => catalogRows('sets').filter(x => x.active !== 0))
const qualityOptions = computed(() => catalogRows('gear_qualities'))
const statOptions = computed(() => catalogRows('stat_definitions').filter(x => x.active !== 0))
const rollOptions = computed(() => catalogRows('stat_roll_grades'))

function statLabel(stat) {
  if (!stat) return '—'
  const name = stat.stat_name || stat.stat_type || '未知属性'
  const value = stat.stat_value ?? stat.estimate_override
  return value === null || value === undefined ? name : `${name} ${Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 3 })}`
}
function mainStat(row) { return row.stats?.find(x => Number(x.stat_index) === 0 || x.stat_source === 'main') || null }
function subStats(row) { return (row.stats || []).filter(x => x !== mainStat(row)).sort((a,b)=>Number(a.stat_index)-Number(b.stat_index)) }

function equipmentSortValue(row, key) {
  if (key === 'main_stat') return statLabel(mainStat(row))
  if (key === 'sub_stats') return subStats(row).map(statLabel).join(' ')
  return row[key]
}

function toggleEquipmentSort(key) {
  if (equipmentSort.value.key === key) equipmentSort.value.direction = equipmentSort.value.direction === 'asc' ? 'desc' : 'asc'
  else equipmentSort.value = { key, direction: 'asc' }
}

const filteredEquipmentRows = computed(() => {
  const q = search.value.trim().toLowerCase()
  let rows = equipmentRows.value
  if (q) rows = rows.filter(row => [row.item_id,row.slot_name,row.set_name,row.quality_name,row.notes,...(row.stats||[]).flatMap(x=>[x.stat_name,x.stat_type,x.stat_value])].some(v => String(v ?? '').toLowerCase().includes(q)))
  const { key, direction } = equipmentSort.value
  return [...rows].sort((a,b)=>compareValues(equipmentSortValue(a,key), equipmentSortValue(b,key))*(direction==='asc'?1:-1))
})

async function loadEquipment() {
  loading.value = true
  resetFeedback()
  try {
    const [catalog, equipment] = await Promise.all([requestJson('/api/catalog'), requestJson('/api/manage/equipment')])
    equipmentCatalog.value = catalog
    equipmentRows.value = equipment.rows || []
  } catch (e) { showError(e) }
  finally { loading.value = false }
}

function blankStats() {
  return Array.from({ length: 5 }, (_, index) => ({
    stat_index: index, stat_source: index === 0 ? 'main' : 'sub', stat_type: '', stat_value: '',
    unlock_level: 0, is_unlocked: true, roll_grade_id: '', estimate_override: '', value_confidence: 1, notes: ''
  }))
}

function blankEquipment() {
  return {
    item_id: '', slot_id: slotOptions.value[0]?.slot_id || 'weapon', set_id: setOptions.value[0]?.set_id || '',
    quality_id: qualityOptions.value[0]?.quality_id || 'mythic_red', enhancement_level: 16,
    locked: false, available: true, is_ancient: false, equipped_hero_id: '', source: 'manual', notes: '', stats: blankStats()
  }
}

function openEquipmentEditor(mode, row = null) {
  resetFeedback()
  let form
  if (mode === 'create') form = blankEquipment()
  else {
    form = clone(row)
    form.locked = Boolean(form.locked)
    form.available = Boolean(form.available)
    form.is_ancient = Boolean(form.is_ancient)
    const existing = new Map((form.stats || []).map(x => [Number(x.stat_index), x]))
    form.stats = blankStats().map(blank => ({ ...blank, ...(existing.get(blank.stat_index) || {}) }))
  }
  equipmentEditor.value = { open: true, mode, originalItemId: row?.item_id || null, form }
}

function equipmentPayload() {
  const form = clone(equipmentEditor.value.form)
  form.enhancement_level = Number(form.enhancement_level || 0)
  form.stats = (form.stats || []).map((stat, index) => ({
    ...stat,
    stat_index: index,
    stat_source: index === 0 ? 'main' : 'sub',
    stat_value: stat.stat_value === '' ? null : Number(stat.stat_value),
    estimate_override: stat.estimate_override === '' ? null : Number(stat.estimate_override),
    unlock_level: Number(stat.unlock_level || 0),
    is_unlocked: Boolean(stat.is_unlocked),
    value_confidence: stat.value_confidence === '' ? 1 : Number(stat.value_confidence),
  }))
  return form
}

async function saveEquipment() {
  resetFeedback()
  try {
    const method = equipmentEditor.value.mode === 'create' ? 'POST' : 'PATCH'
    const body = { values: equipmentPayload() }
    if (method === 'PATCH') body.original_item_id = equipmentEditor.value.originalItemId
    await requestJson('/api/manage/equipment', { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    equipmentEditor.value.open = false
    message.value = method === 'POST' ? '装备已新增。' : '装备已更新。'
    await loadEquipment()
  } catch (e) { showError(e) }
}

async function removeEquipment(row) {
  if (!window.confirm(`确认删除装备 ${row.item_id}？其词条和识别关联记录会按数据库规则同步处理。`)) return
  resetFeedback()
  try {
    await requestJson('/api/manage/equipment', { method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ item_id: row.item_id }) })
    message.value = '装备已删除。'
    await loadEquipment()
  } catch (e) { showError(e) }
}

watch(() => props.mode, async mode => {
  search.value = ''
  if (mode === 'dictionary') await loadDictionary()
  if (mode === 'equipment') await loadEquipment()
})

onMounted(async () => {
  if (props.mode === 'dictionary') await loadDictionary()
  else if (props.mode === 'equipment') await loadEquipment()
})
</script>

<template>
  <div class="manager-shell">
    <header class="manager-topbar">
      <button class="brand" @click="go('heroes')"><span>E</span><strong>EAIA</strong><small>装备与英雄数据库</small></button>
      <nav>
        <button v-for="item in NAV" :key="item[0]" :class="{active: item[0]===mode}" @click="go(item[0])">{{ item[1] }}</button>
      </nav>
      <div class="local-pill"><i></i> 本地 SQLite <button class="language-toggle" @click="toggleLanguage">{{ currentLanguage === 'zh-CN' ? 'EN' : '中' }}</button></div>
    </header>

    <main v-if="mode==='dictionary' || mode==='equipment'" class="manager-page">
      <section class="hero-titlebar">
        <div>
          <p>{{ mode === 'dictionary' ? 'DATA MANAGEMENT' : 'EQUIPMENT INVENTORY' }}</p>
          <h1>{{ mode === 'dictionary' ? '游戏数据管理' : '已有装备' }}</h1>
          <span>{{ mode === 'dictionary' ? '以游戏概念组织数据，不再直接暴露数据库表结构；支持新增、编辑、删除和表头排序。' : '按玩家实际查看习惯展示部位、套装、品质和完整词条；可直接维护装备实例。' }}</span>
        </div>
        <div class="summary-card" v-if="mode==='dictionary'"><strong>{{ resourceCatalog.length }}</strong><small>可管理模块</small></div>
        <div class="summary-card" v-else><strong>{{ equipmentRows.length }}</strong><small>装备记录</small></div>
      </section>

      <div v-if="message" class="notice success">{{ message }}</div>
      <div v-if="error" class="notice error">{{ error }}</div>

      <template v-if="mode==='dictionary'">
        <section class="manager-layout">
          <aside class="resource-sidebar">
            <div v-for="group in resourceGroups" :key="group.name" class="resource-group">
              <h3>{{ group.name }}</h3>
              <button v-for="item in group.items" :key="item.id" :class="{active:activeResourceId===item.id}" @click="loadResource(item.id)">
                <span>{{ item.title }}</span><small>{{ item.description }}</small>
              </button>
            </div>
          </aside>

          <section class="resource-content">
            <div class="content-head">
              <div><p>当前模块</p><h2>{{ activeResourceMeta?.title || '—' }}</h2><span>{{ activeResourceMeta?.description }}</span></div>
              <button class="primary" @click="openResourceEditor('create')">＋ 新增{{ activeResourceMeta?.title }}</button>
            </div>
            <div class="toolbar"><label>⌕<input v-model="search" placeholder="搜索当前数据" /></label><span>{{ filteredResourceRows.length }} 条记录</span></div>
            <div class="table-card">
              <div v-if="loading" class="empty">正在读取数据…</div>
              <div v-else-if="!filteredResourceRows.length" class="empty">当前没有匹配记录。</div>
              <table v-else>
                <thead><tr>
                  <th v-for="field in displayFields" :key="field.name" @click="toggleSort(field.name)"><span>{{ field.label }}</span><i>{{ sortMark(field.name) }}</i></th>
                  <th class="actions">操作</th>
                </tr></thead>
                <tbody><tr v-for="(row,index) in filteredResourceRows" :key="index">
                  <td v-for="field in displayFields" :key="field.name" :title="formatCell(row[field.name],field)">{{ formatCell(row[field.name],field) }}</td>
                  <td class="row-actions"><button @click="openResourceEditor('edit',row)">编辑</button><button class="danger" @click="removeResource(row)">删除</button></td>
                </tr></tbody>
              </table>
            </div>
          </section>
        </section>
      </template>

      <template v-else>
        <section class="equipment-content">
          <div class="content-head">
            <div><p>装备仓库</p><h2>装备实例</h2><span>主词条和副词条合并到同一行展示，避免查看多张数据库关联表。</span></div>
            <button class="primary" @click="openEquipmentEditor('create')">＋ 新增装备</button>
          </div>
          <div class="toolbar"><label>⌕<input v-model="search" placeholder="搜索装备ID、套装、属性" /></label><span>{{ filteredEquipmentRows.length }} / {{ equipmentRows.length }} 件</span></div>
          <div class="table-card equipment-table">
            <div v-if="loading" class="empty">正在读取装备…</div>
            <div v-else-if="!filteredEquipmentRows.length" class="empty">没有匹配的装备。</div>
            <table v-else>
              <thead><tr>
                <th @click="toggleEquipmentSort('item_id')">装备ID <i>{{ sortMark('item_id',equipmentSort) }}</i></th>
                <th @click="toggleEquipmentSort('slot_name')">部位 <i>{{ sortMark('slot_name',equipmentSort) }}</i></th>
                <th @click="toggleEquipmentSort('set_name')">套装 <i>{{ sortMark('set_name',equipmentSort) }}</i></th>
                <th @click="toggleEquipmentSort('quality_name')">品质 <i>{{ sortMark('quality_name',equipmentSort) }}</i></th>
                <th @click="toggleEquipmentSort('enhancement_level')">强化 <i>{{ sortMark('enhancement_level',equipmentSort) }}</i></th>
                <th @click="toggleEquipmentSort('main_stat')">主词条 <i>{{ sortMark('main_stat',equipmentSort) }}</i></th>
                <th @click="toggleEquipmentSort('sub_stats')">副词条 <i>{{ sortMark('sub_stats',equipmentSort) }}</i></th>
                <th @click="toggleEquipmentSort('available')">状态 <i>{{ sortMark('available',equipmentSort) }}</i></th>
                <th class="actions">操作</th>
              </tr></thead>
              <tbody><tr v-for="row in filteredEquipmentRows" :key="row.item_id">
                <td><strong>{{ row.item_id }}</strong><small v-if="row.is_ancient" class="tag">远古</small></td>
                <td>{{ row.slot_name || row.slot_id }}</td>
                <td>{{ row.set_name || row.set_id }}</td>
                <td>{{ row.quality_name || row.quality_id || '—' }}</td>
                <td>+{{ row.enhancement_level ?? 0 }}</td>
                <td><span class="stat-main">{{ statLabel(mainStat(row)) }}</span></td>
                <td><div class="stat-list"><span v-for="stat in subStats(row)" :key="stat.stat_index" :class="{muted:!stat.is_unlocked}">{{ statLabel(stat) }}</span><em v-if="!subStats(row).length">—</em></div></td>
                <td><span class="status" :class="row.available ? 'ok' : 'off'">{{ row.available ? '可用' : '停用' }}</span><span v-if="row.locked" class="status lock">已锁定</span></td>
                <td class="row-actions"><button @click="openEquipmentEditor('edit',row)">编辑</button><button class="danger" @click="removeEquipment(row)">删除</button></td>
              </tr></tbody>
            </table>
          </div>
        </section>
      </template>
    </main>

    <div v-else class="manager-content-view">
      <HeroSimulation v-if="mode==='simulation'" :embedded="true" />
      <AppV2 v-else :embedded="true" />
    </div>

    <div v-if="editor.open" class="modal-backdrop" @click.self="closeResourceEditor">
      <section class="editor-modal">
        <header><div><p>{{ editor.mode==='create' ? 'NEW RECORD' : 'EDIT RECORD' }}</p><h2>{{ editor.mode==='create' ? '新增' : '编辑' }}{{ activeResourceMeta?.title }}</h2></div><button @click="closeResourceEditor">×</button></header>
        <div class="form-grid">
          <label v-for="field in resourceData.fields" :key="field.name" :class="{wide:field.type==='textarea'}">
            <span>{{ field.label }}</span>
            <input v-if="field.type==='text' || !field.type" v-model="editor.form[field.name]" :disabled="editor.mode==='edit' && field.readonly_on_edit" />
            <input v-else-if="field.type==='number'" v-model="editor.form[field.name]" type="number" step="any" :disabled="editor.mode==='edit' && field.readonly_on_edit" />
            <textarea v-else-if="field.type==='textarea'" v-model="editor.form[field.name]" rows="3"></textarea>
            <select v-else-if="field.type==='select'" v-model="editor.form[field.name]"><option value="">—</option><option v-for="option in field.options || []" :key="String(option.value)" :value="option.value">{{ option.label }}</option></select>
            <label v-else-if="field.type==='boolean'" class="switch"><input v-model="editor.form[field.name]" type="checkbox" /><i></i><b>{{ editor.form[field.name] ? '是' : '否' }}</b></label>
          </label>
        </div>
        <footer><button class="secondary" @click="closeResourceEditor">取消</button><button class="primary" @click="saveResource">保存</button></footer>
      </section>
    </div>

    <div v-if="equipmentEditor.open" class="modal-backdrop" @click.self="equipmentEditor.open=false">
      <section class="editor-modal equipment-editor">
        <header><div><p>{{ equipmentEditor.mode==='create' ? 'NEW EQUIPMENT' : 'EDIT EQUIPMENT' }}</p><h2>{{ equipmentEditor.mode==='create' ? '新增装备' : `编辑 ${equipmentEditor.originalItemId}` }}</h2></div><button @click="equipmentEditor.open=false">×</button></header>
        <div class="form-grid" v-if="equipmentEditor.form">
          <label><span>装备ID</span><input v-model="equipmentEditor.form.item_id" :disabled="equipmentEditor.mode==='edit'" /></label>
          <label><span>部位</span><select v-model="equipmentEditor.form.slot_id"><option v-for="x in slotOptions" :key="x.slot_id" :value="x.slot_id">{{ x.slot_name }}</option></select></label>
          <label><span>套装</span><select v-model="equipmentEditor.form.set_id"><option v-for="x in setOptions" :key="x.set_id" :value="x.set_id">{{ x.set_name }}</option></select></label>
          <label><span>品质</span><select v-model="equipmentEditor.form.quality_id"><option value="">—</option><option v-for="x in qualityOptions" :key="x.quality_id" :value="x.quality_id">{{ x.quality_name }}</option></select></label>
          <label><span>强化等级</span><input v-model.number="equipmentEditor.form.enhancement_level" type="number" min="0" max="16" /></label>
          <label><span>装备来源</span><input v-model="equipmentEditor.form.source" /></label>
          <label><span>已装备英雄ID</span><input v-model="equipmentEditor.form.equipped_hero_id" /></label>
          <label><span>可参与配装</span><label class="switch"><input v-model="equipmentEditor.form.available" type="checkbox" /><i></i><b>{{ equipmentEditor.form.available ? '是' : '否' }}</b></label></label>
          <label><span>锁定装备</span><label class="switch"><input v-model="equipmentEditor.form.locked" type="checkbox" /><i></i><b>{{ equipmentEditor.form.locked ? '是' : '否' }}</b></label></label>
          <label><span>远古装备</span><label class="switch"><input v-model="equipmentEditor.form.is_ancient" type="checkbox" /><i></i><b>{{ equipmentEditor.form.is_ancient ? '是' : '否' }}</b></label></label>
          <label class="wide"><span>备注</span><textarea v-model="equipmentEditor.form.notes" rows="2"></textarea></label>
        </div>
        <div class="stats-editor" v-if="equipmentEditor.form">
          <div class="stats-head"><strong>装备词条</strong><span>第 1 行为主词条，其余为副词条；空属性不会写入数据库。</span></div>
          <div class="stat-edit-row" v-for="(stat,index) in equipmentEditor.form.stats" :key="index">
            <strong>{{ index===0 ? '主词条' : `副词条 ${index}` }}</strong>
            <select v-model="stat.stat_type"><option value="">未设置</option><option v-for="x in statOptions" :key="x.stat_type" :value="x.stat_type">{{ x.stat_name }}</option></select>
            <input v-model="stat.stat_value" type="number" step="any" placeholder="数值" />
            <select v-if="index>0" v-model="stat.roll_grade_id"><option value="">档位未记录</option><option v-for="x in rollOptions" :key="x.roll_grade_id" :value="x.roll_grade_id">{{ x.roll_grade_name }}</option></select><span v-else></span>
            <label class="mini-check"><input v-model="stat.is_unlocked" type="checkbox" />已解锁</label>
          </div>
        </div>
        <footer><button class="secondary" @click="equipmentEditor.open=false">取消</button><button class="primary" @click="saveEquipment">保存装备</button></footer>
      </section>
    </div>
  </div>
</template>
