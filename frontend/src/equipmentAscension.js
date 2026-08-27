const DAMAGE_TYPES = new Map([
  ['damage_bonus', 'DAMAGE_PCT'],
  ['basic_damage_bonus', 'BASIC_DMG'],
  ['skill_damage_bonus', 'SKILL_DMG'],
  ['ultimate_damage_bonus', 'ULT_DMG'],
  ['single_damage_bonus', 'SINGLE_DMG'],
  ['aoe_damage_bonus', 'AOE_DMG'],
])

const STATIC_TRIGGER_ALIASES = new Set(['passive', 'while_deployed'])

export function setName(catalog, setId) {
  return catalog?.sets?.find(row => row.set_id === setId)?.set_name || setId || '—'
}

export function evolutionMap(catalog) {
  return new Map((catalog?.set_evolutions || []).map(row => [row.from_set_id, row.to_set_id]))
}

export function ascensionVariants(items, catalog) {
  const evolutions = evolutionMap(catalog)
  const groups = new Map()
  items.forEach((item, index) => {
    const toSet = evolutions.get(item.set_id)
    if (!toSet) return
    const key = `${item.set_id}\u0000${toSet}`
    if (!groups.has(key)) groups.set(key, { fromSet:item.set_id, toSet, indices:[] })
    groups.get(key).indices.push(index)
  })
  if (!groups.size) return [{ items, ascendedItems:[] }]

  const ordered = [...groups.values()].sort((a,b) => a.fromSet.localeCompare(b.fromSet))
  const variants = []
  const seen = new Set()

  function visit(groupIndex, counts) {
    if (groupIndex < ordered.length) {
      const group = ordered[groupIndex]
      for (let count = 0; count <= group.indices.length; count += 1) visit(groupIndex + 1, [...counts, count])
      return
    }
    const variantItems = items.map(item => ({ ...item }))
    const ascendedItems = []
    ordered.forEach((group, index) => {
      const count = counts[index]
      group.indices.slice(0, count).forEach(itemIndex => {
        const original = variantItems[itemIndex]
        variantItems[itemIndex] = {
          ...original,
          set_id:group.toSet,
          set_name:setName(catalog, group.toSet),
        }
        ascendedItems.push({
          item_id:original.item_id,
          slot:original.slot,
          from_set_id:group.fromSet,
          to_set_id:group.toSet,
          from_set_name:setName(catalog, group.fromSet),
          to_set_name:setName(catalog, group.toSet),
        })
      })
    })
    const state = variantItems.map(item => item.set_id).join('|')
    if (seen.has(state)) return
    seen.add(state)
    variants.push({ items:variantItems, ascendedItems })
  }

  visit(0, [])
  return variants
}

export function normalizeSetEffect(row) {
  if (Number(row.enabled_in_optimizer ?? row.enabled_in_v1_1 ?? 1) === 0) return null
  const rawEffect = String(row.effect_type || '')
  const effectLower = rawEffect.toLowerCase()
  const statLower = String(row.stat_type || '').toLowerCase()
  let type = rawEffect.toUpperCase()
  if (effectLower === 'stat_mod') type = statLower.toUpperCase()
  else if (effectLower === 'damage_mult') type = DAMAGE_TYPES.get(statLower) || ''
  else if (effectLower === 'extra_damage') type = 'EXTRA_DAMAGE'
  if (!type) return null

  let trigger = String(row.trigger || 'always').toLowerCase()
  if (trigger === 'on_ultimate') trigger = 'on_ult'
  const condition = row.condition
  const duration = Number(row.duration || 0)
  if (!condition && STATIC_TRIGGER_ALIASES.has(trigger)) trigger = 'always'
  if (!condition && trigger === 'on_deploy' && duration <= 0) trigger = 'always'
  return { ...row, normalizedType:type, normalizedTrigger:trigger, value:Number(row.value || 0) }
}

export function activeSetEffects(items, catalog) {
  const counts = new Map()
  items.forEach(item => counts.set(item.set_id, (counts.get(item.set_id) || 0) + 1))
  const active = []
  for (const [setId, count] of counts) {
    const definition = catalog?.sets?.find(row => row.set_id === setId)
    if (definition && count >= Number(definition.required_pieces || 99)) active.push(setId)
  }
  const effects = (catalog?.set_effects || [])
    .filter(row => active.includes(row.set_id))
    .map(normalizeSetEffect)
    .filter(Boolean)
  return { active, effects }
}

function isSingleTargetBasic(basicSkill) {
  if (basicSkill.target_cap === 'all') return false
  return Number(basicSkill.target_cap || 1) <= 1
}

export function scoreQuickBuild(items, basicSkill, catalog, options = {}) {
  const enemyCount = Math.max(1, Number(options.enemyCount || 1))
  const mode = options.mode === 'aoe' ? 'aoe' : 'single'
  const normalizeStatValue = options.normalizeStatValue || ((type, value) => Number(value || 0))
  const stats = new Map()
  items.forEach(item => item.stats.forEach(stat => {
    stats.set(stat.type, (stats.get(stat.type) || 0) + Number(stat.value || 0))
  }))
  const { active, effects } = activeSetEffects(items, catalog)
  const staticEffects = effects.filter(effect => effect.normalizedTrigger === 'always')
  const effectStat = type => staticEffects
    .filter(effect => effect.normalizedType === type)
    .reduce((sum, effect) => sum + normalizeStatValue(type, effect.value), 0)

  const atkFlat = (stats.get('ATK_FLAT') || 0) + effectStat('ATK_FLAT')
  const atkPct = (stats.get('ATK_PCT') || 0) + effectStat('ATK_PCT')
  const atk = (1000 + atkFlat) * (1 + atkPct)
  const critRateRaw = 0.05 + (stats.get('CRIT_RATE') || 0) + effectStat('CRIT_RATE')
  const critRate = Math.min(1, critRateRaw)
  const critDmg = 1.5 + (stats.get('CRIT_DMG') || 0) + effectStat('CRIT_DMG')
  const critFactor = (1 - critRate) + critRate * critDmg
  const attackSpeedPoints = (stats.get('ATK_SPEED') || 0) + effectStat('ATK_SPEED')
  const attackRateFactor = Math.max(0.01, 1 + attackSpeedPoints / 100)

  const singleBasic = isSingleTargetBasic(basicSkill)
  let damageBonus = effectStat('DAMAGE_PCT') + effectStat('BASIC_DMG')
  if (singleBasic) damageBonus += effectStat('SINGLE_DMG')
  else damageBonus += effectStat('AOE_DMG')

  const targetCap = basicSkill.target_cap === 'all' ? enemyCount : Number(basicSkill.target_cap || 1)
  const targets = mode === 'aoe' ? Math.min(enemyCount, targetCap) : 1
  const extraDamage = singleBasic
    ? effects.filter(effect => effect.normalizedType === 'EXTRA_DAMAGE' && effect.normalizedTrigger === 'on_basic_attack_damage')
      .reduce((sum, effect) => sum + effect.value, 0)
    : 0

  const dps = (atk * Number(basicSkill.coefficient) * critFactor * (1 + damageBonus) + extraDamage)
    * targets * attackRateFactor
  const dynamicEffects = effects.filter(effect => effect.normalizedTrigger !== 'always' && effect.normalizedType !== 'EXTRA_DAMAGE')
  return {
    dps,
    active,
    activeSetNames:active.map(setId => setName(catalog, setId)),
    panel:{ atk, critRate, critOverflow:Math.max(0, critRateRaw - 1), critDmg, attackSpeedPoints },
    dynamicEffects,
  }
}

export function bestAscensionState(items, basicSkill, catalog, options = {}) {
  let best = null
  for (const variant of ascensionVariants(items, catalog)) {
    const scored = { ...scoreQuickBuild(variant.items, basicSkill, catalog, options), ...variant }
    if (!best || scored.dps > best.dps + 1e-9 || (
      Math.abs(scored.dps - best.dps) <= 1e-9 && scored.ascendedItems.length < best.ascendedItems.length
    )) best = scored
  }
  return best
}
