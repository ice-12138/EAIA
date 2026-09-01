const STORAGE_KEY = 'eaia-support-recommendation-mode'
const MANUAL_PRIORITY = 'manual_priority'
const AUTO_UTILITY = 'auto_utility'

let selectedMode = localStorage.getItem(STORAGE_KEY) || MANUAL_PRIORITY
let currentSupportHero = false
let renderQueued = false

function normalizedCategory(core) {
  const explicit = String(core?.recommendation_profile?.category || core?.hero?.equipment_category || '').trim().toLowerCase()
  if (explicit) {
    if (explicit === 'buff' || explicit === 'support') return 'support'
    return explicit
  }
  const role = String(core?.hero?.role || '').trim().toLowerCase()
  return ['战术大师', 'support', 'tactician', 'tactical master'].includes(role) ? 'support' : ''
}

function isChineseUi() {
  const toggle = document.querySelector('.language-button')
  return !toggle || String(toggle.textContent || '').trim() === 'EN'
}

function helperText(zh) {
  if (selectedMode === AUTO_UTILITY) {
    return zh
      ? '按 HeroCore 时间轴自动释放并计算激励覆盖收益；未配置 utility_model 时会明确回退到面板代理。'
      : 'Uses the HeroCore timeline and utility coverage; explicitly falls back to the panel proxy when utility_model is missing.'
  }
  return zh
    ? '假设玩家手动对齐主C爆发；按该英雄 HeroCore 定义的主/副词条优先级推荐。'
    : 'Assumes manual burst alignment and ranks gear by this hero\'s HeroCore stat priority.'
}

function buildField() {
  const label = document.createElement('label')
  label.className = 'support-recommendation-mode-field'

  const title = document.createElement('span')
  title.className = 'support-recommendation-mode-title'
  label.appendChild(title)

  const select = document.createElement('select')
  select.className = 'support-recommendation-mode-select'
  const manual = document.createElement('option')
  manual.value = MANUAL_PRIORITY
  const automatic = document.createElement('option')
  automatic.value = AUTO_UTILITY
  select.append(manual, automatic)
  select.value = selectedMode
  select.addEventListener('change', () => {
    selectedMode = select.value === AUTO_UTILITY ? AUTO_UTILITY : MANUAL_PRIORITY
    localStorage.setItem(STORAGE_KEY, selectedMode)
    renderSelector()
  })
  label.appendChild(select)

  const helper = document.createElement('small')
  helper.className = 'support-recommendation-mode-helper'
  label.appendChild(helper)
  return label
}

function renderSelector() {
  renderQueued = false
  const card = document.querySelector('.recommend-layout > .config-card')
  if (!card) return

  let field = card.querySelector('.support-recommendation-mode-field')
  if (!field) {
    const directLabels = Array.from(card.children).filter(node => node.tagName === 'LABEL')
    if (directLabels.length < 2) return
    field = buildField()
    directLabels[1].after(field)
  }

  field.hidden = !currentSupportHero
  if (!currentSupportHero) return

  const zh = isChineseUi()
  const title = field.querySelector('.support-recommendation-mode-title')
  const select = field.querySelector('.support-recommendation-mode-select')
  const options = select?.options || []
  const helper = field.querySelector('.support-recommendation-mode-helper')
  if (title) title.textContent = zh ? '辅助推荐算法' : 'Support Recommendation'
  if (options[0]) options[0].textContent = zh ? '手动爆发 / 词条优先级' : 'Manual burst / stat priority'
  if (options[1]) options[1].textContent = zh ? '自动战斗 / 综合收益' : 'Auto battle / utility'
  if (select) select.value = selectedMode
  if (helper) helper.textContent = helperText(zh)
}

function queueRender() {
  if (renderQueued) return
  renderQueued = true
  queueMicrotask(renderSelector)
}

const upstreamFetch = window.fetch.bind(window)
window.fetch = async (input, init = {}) => {
  const url = typeof input === 'string' ? input : input?.url || ''
  const method = String(init?.method || 'GET').toUpperCase()
  let nextInit = init

  if (url.includes('/api/hero-core/recommend/start') && method === 'POST' && init?.body && typeof init.body === 'string') {
    try {
      const payload = JSON.parse(init.body)
      if (currentSupportHero && !payload.team && !payload.hero_core_ids) {
        nextInit = {
          ...init,
          body: JSON.stringify({ ...payload, support_recommendation_mode: selectedMode }),
        }
      }
    } catch {
      // Preserve non-JSON requests.
    }
  }

  const response = await upstreamFetch(input, nextInit)
  if (response.ok && method === 'GET' && /\/api\/hero-cores\/[^/?#]+/.test(url)) {
    try {
      const core = await response.clone().json()
      currentSupportHero = normalizedCategory(core) === 'support'
      queueRender()
    } catch {
      currentSupportHero = false
      queueRender()
    }
  }
  return response
}

const observer = new MutationObserver(queueRender)
observer.observe(document.documentElement, { childList: true, subtree: true, characterData: true })
window.addEventListener('hashchange', queueRender)
queueRender()
