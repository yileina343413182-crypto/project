<template>
  <Teleport to="body">
    <Transition name="watch-guide-drawer">
      <div v-if="open" class="watch-guide-layer">
        <button class="watch-guide-backdrop" type="button" tabindex="-1" aria-label="关闭待看番剧指南" @click="close"></button>
        <aside
          ref="drawerRef"
          class="watch-guide-drawer"
          role="dialog"
          aria-modal="true"
          aria-labelledby="watch-guide-title"
          tabindex="-1"
          @keydown.esc.stop="close"
          @keydown.tab="trapFocus"
        >
          <header class="drawer-head">
            <div>
              <span>WATCH GUIDE</span>
              <h2 id="watch-guide-title">待看番剧指南</h2>
              <p>已保存 {{ total }} 部想看的动画</p>
            </div>
            <button class="drawer-close" type="button" aria-label="关闭待看番剧指南" @click="close">×</button>
          </header>

          <div class="drawer-body">
            <section class="guide-index" aria-label="待看动画列表">
              <p v-if="listLoading" class="drawer-state">正在加载观看指南...</p>
              <div v-else-if="listError" class="drawer-state error-state" role="alert">
                <p>{{ listError }}</p>
                <button type="button" @click="loadGuides">重新加载</button>
              </div>
              <p v-else-if="!guides.length" class="drawer-state">还没有保存观看指南。</p>
              <div v-else class="guide-list">
                <div
                  v-for="guide in guides"
                  :key="guide.id"
                  class="guide-row"
                  :class="{ active: guide.id === selectedGuideId }"
                >
                  <button
                    type="button"
                    class="guide-select"
                    :aria-current="guide.id === selectedGuideId ? 'true' : undefined"
                    :title="guide.anime_name"
                    :disabled="deletingGuideId !== null"
                    @click="selectGuide(guide.id)"
                  >
                    <strong>{{ guide.anime_name || '未命名动画' }}</strong>
                    <time>{{ formatTime(guide.created_at) }}</time>
                  </button>
                  <button
                    type="button"
                    class="guide-delete"
                    :aria-label="`删除《${guide.anime_name || '未命名动画'}》观看指南`"
                    :title="`删除《${guide.anime_name || '未命名动画'}》观看指南`"
                    :disabled="deletingGuideId !== null"
                    @click="removeGuide(guide)"
                  >{{ deletingGuideId === guide.id ? '···' : '删除' }}</button>
                </div>
              </div>
            </section>

            <article class="guide-detail" aria-live="polite">
              <p v-if="detailLoading" class="drawer-state">正在加载指南详情...</p>
              <div v-else-if="detailError" class="drawer-state error-state" role="alert">
                <p>{{ detailError }}</p>
                <button v-if="selectedGuideId !== null" type="button" @click="selectGuide(selectedGuideId)">重新加载</button>
              </div>
              <template v-else-if="selectedGuide">
                <header class="detail-head">
                  <span>VIEWING PLAN</span>
                  <h3>{{ selectedGuide.anime_name || '未命名动画' }}</h3>
                  <time v-if="selectedGuide.created_at">保存于 {{ formatTime(selectedGuide.created_at) }}</time>
                </header>
                <p class="guide-copy">
                  <template v-for="(segment, index) in guideSegments" :key="index">
                    <strong v-if="segment.bold">{{ segment.text }}</strong>
                    <span v-else>{{ segment.text }}</span>
                  </template>
                </p>
              </template>
              <div v-else class="detail-placeholder">
                <span>📺</span>
                <p>{{ guides.length ? '选择一部动画查看观看指南。' : '保存后，观看计划会出现在这里。' }}</p>
              </div>
            </article>
          </div>

          <p v-if="actionError" class="action-error" role="alert">{{ actionError }}</p>
        </aside>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { deleteWatchGuide, getWatchGuide, getWatchGuides } from '../../api'

const props = defineProps({
  open: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['close'])

const drawerRef = ref(null)
const guides = ref([])
const total = ref(0)
const listLoading = ref(false)
const listError = ref('')
const selectedGuideId = ref(null)
const selectedGuide = ref(null)
const detailLoading = ref(false)
const detailError = ref('')
const deletingGuideId = ref(null)
const actionError = ref('')

let listRequestVersion = 0
let detailRequestVersion = 0
let deleteRequestVersion = 0
let previousFocus = null
let disposed = false

const guideSegments = computed(() => parseBold(selectedGuide.value?.guide_content || '暂无可展示的观看指南内容。'))

function parseBold(value) {
  const text = String(value || '')
  const segments = []
  const pattern = /\*\*([^*]+)\*\*/gu
  let cursor = 0
  let match

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > cursor) segments.push({ text: text.slice(cursor, match.index), bold: false })
    segments.push({ text: match[1], bold: true })
    cursor = match.index + match[0].length
  }
  if (cursor < text.length) segments.push({ text: text.slice(cursor), bold: false })
  return segments.length ? segments : [{ text, bold: false }]
}

function close() {
  emit('close')
}

function invalidateRequests() {
  listRequestVersion += 1
  detailRequestVersion += 1
  deleteRequestVersion += 1
  deletingGuideId.value = null
}

async function loadGuides() {
  const requestVersion = ++listRequestVersion
  detailRequestVersion += 1
  listLoading.value = true
  listError.value = ''
  actionError.value = ''
  guides.value = []
  total.value = 0
  selectedGuideId.value = null
  selectedGuide.value = null
  detailLoading.value = false
  detailError.value = ''

  try {
    const pageSize = 50
    let page = 1
    let reportedTotal = 0
    const itemMap = new Map()
    while (true) {
      const data = await getWatchGuides(page, pageSize)
      if (!props.open || requestVersion !== listRequestVersion) return
      const pageItems = Array.isArray(data?.items) ? data.items : []
      pageItems.forEach((item) => itemMap.set(item.id, item))
      reportedTotal = Number.isFinite(Number(data?.total)) ? Number(data.total) : itemMap.size
      if (!pageItems.length || pageItems.length < pageSize || itemMap.size >= reportedTotal) break
      page += 1
    }
    const items = Array.from(itemMap.values())
    guides.value = items
    total.value = reportedTotal || items.length
    if (items.length) selectGuide(items[0].id)
  } catch (err) {
    if (!props.open || requestVersion !== listRequestVersion) return
    listError.value = err.message || '观看指南加载失败，请稍后重试。'
  } finally {
    if (requestVersion === listRequestVersion) listLoading.value = false
  }
}

async function selectGuide(guideId) {
  if (guideId === selectedGuideId.value && (selectedGuide.value || detailLoading.value)) return

  const requestVersion = ++detailRequestVersion
  selectedGuideId.value = guideId
  selectedGuide.value = null
  detailLoading.value = true
  detailError.value = ''
  actionError.value = ''

  try {
    const detail = await getWatchGuide(guideId)
    if (!props.open || requestVersion !== detailRequestVersion || selectedGuideId.value !== guideId) return
    selectedGuide.value = detail
  } catch (err) {
    if (!props.open || requestVersion !== detailRequestVersion || selectedGuideId.value !== guideId) return
    detailError.value = err.message || '指南详情加载失败，请稍后重试。'
  } finally {
    if (requestVersion === detailRequestVersion) detailLoading.value = false
  }
}

async function removeGuide(guide) {
  if (deletingGuideId.value !== null) return
  const animeName = guide.anime_name || '未命名动画'
  if (!window.confirm(`确定删除《${animeName}》的观看指南吗？删除后无法恢复。`)) return

  const requestVersion = ++deleteRequestVersion
  deletingGuideId.value = guide.id
  actionError.value = ''
  const removedIndex = guides.value.findIndex((item) => item.id === guide.id)
  try {
    await deleteWatchGuide(guide.id)
    if (disposed) return
    if (!props.open || requestVersion !== deleteRequestVersion) {
      if (props.open && deletingGuideId.value === null) loadGuides()
      return
    }
    const remaining = guides.value.filter((item) => item.id !== guide.id)
    guides.value = remaining
    total.value = Math.max(0, total.value - 1)

    if (selectedGuideId.value === guide.id) {
      detailRequestVersion += 1
      selectedGuideId.value = null
      selectedGuide.value = null
      detailLoading.value = false
      detailError.value = ''
      const nextGuide = remaining[Math.min(Math.max(removedIndex, 0), remaining.length - 1)]
      if (nextGuide) selectGuide(nextGuide.id)
    }
  } catch (err) {
    if (disposed || !props.open || requestVersion !== deleteRequestVersion) return
    actionError.value = err.message || '删除观看指南失败，请稍后重试。'
  } finally {
    if (requestVersion === deleteRequestVersion) deletingGuideId.value = null
  }
}

function formatTime(value) {
  if (!value) return ''
  const normalized = typeof value === 'string'
    ? value.replace(/^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})$/, '$1T$2')
    : value
  const date = new Date(normalized)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  }).format(date)
}

function trapFocus(event) {
  const focusable = Array.from(drawerRef.value?.querySelectorAll(
    'button:not(:disabled), [href], input:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])'
  ) || []).filter((element) => element.offsetParent !== null)
  if (!focusable.length) {
    event.preventDefault()
    drawerRef.value?.focus()
    return
  }
  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  const active = document.activeElement
  if (event.shiftKey && (active === first || !drawerRef.value?.contains(active))) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && (active === last || !drawerRef.value?.contains(active))) {
    event.preventDefault()
    first.focus()
  }
}

function restoreFocus() {
  const candidates = [
    previousFocus,
    document.querySelector('.sidebar-toggle'),
    document.querySelector('.top-watch-guide'),
    document.querySelector('.composer textarea')
  ]
  const target = candidates.find((element) => (
    element?.isConnected
    && element.offsetParent !== null
    && !element.closest('[inert]')
  ))
  target?.focus?.()
}

watch(
  () => props.open,
  async (open) => {
    if (!open) {
      invalidateRequests()
      if (previousFocus) {
        await nextTick()
        restoreFocus()
        previousFocus = null
      }
      return
    }

    previousFocus = document.activeElement
    await nextTick()
    drawerRef.value?.focus()
    loadGuides()
  },
  { immediate: true }
)

onBeforeUnmount(() => {
  disposed = true
  invalidateRequests()
})
</script>

<style scoped>
.watch-guide-layer {
  position: fixed;
  inset: 0;
  z-index: 120;
  display: flex;
  justify-content: flex-end;
}
.watch-guide-backdrop {
  position: absolute;
  inset: 0;
  border: 0;
  background: rgba(1, 5, 13, 0.68);
  cursor: default;
}
.watch-guide-drawer {
  position: relative;
  width: min(900px, calc(100vw - 24px));
  height: 100vh;
  height: 100dvh;
  display: flex;
  flex-direction: column;
  border-left: 1px solid rgba(0, 229, 255, 0.2);
  outline: none;
  background: rgba(6, 11, 21, 0.98);
  box-shadow: -24px 0 70px rgba(0, 0, 0, 0.48);
  color: var(--text-primary);
}
.drawer-head {
  min-height: 82px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 16px 22px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.07);
  background: rgba(0, 229, 255, 0.035);
}
.drawer-head span,
.detail-head span {
  color: var(--neon-cyan);
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 1.4px;
}
.drawer-head h2 { margin-top: 3px; font-size: 20px; }
.drawer-head p { margin-top: 2px; color: var(--text-muted); font-size: 11px; }
.drawer-close {
  width: 34px;
  height: 34px;
  flex: 0 0 34px;
  border: 1px solid rgba(255, 255, 255, 0.11);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.04);
  color: var(--text-secondary);
  font-size: 21px;
  cursor: pointer;
}
.drawer-close:hover { border-color: rgba(0, 229, 255, 0.4); color: var(--neon-cyan); }
.drawer-body {
  min-height: 0;
  flex: 1;
  display: grid;
  grid-template-columns: 250px minmax(0, 1fr);
}
.guide-index {
  min-height: 0;
  overflow-y: auto;
  padding: 14px 12px;
  border-right: 1px solid rgba(255, 255, 255, 0.07);
  background: rgba(255, 255, 255, 0.018);
}
.guide-list { display: flex; flex-direction: column; gap: 7px; }
.guide-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  overflow: hidden;
  border: 1px solid transparent;
  border-radius: 8px;
}
.guide-row:hover { background: rgba(255, 255, 255, 0.04); }
.guide-row.active { border-color: rgba(0, 229, 255, 0.25); background: rgba(0, 229, 255, 0.08); }
.guide-select {
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
  padding: 11px 10px;
  border: 0;
  background: transparent;
  color: var(--text-secondary);
  text-align: left;
  cursor: pointer;
}
.guide-select strong {
  width: 100%;
  overflow: hidden;
  color: var(--text-primary);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.guide-select time { color: var(--text-muted); font-family: var(--font-mono); font-size: 9px; }
.guide-select:disabled { opacity: 0.55; cursor: not-allowed; }
.guide-delete {
  padding: 0 8px;
  border: 0;
  border-left: 1px solid rgba(255, 255, 255, 0.06);
  background: transparent;
  color: var(--text-muted);
  font-size: 10px;
  cursor: pointer;
}
.guide-delete:hover { background: rgba(255, 82, 103, 0.1); color: var(--color-negative); }
.guide-delete:disabled { opacity: 0.4; cursor: not-allowed; }
.guide-detail { min-width: 0; min-height: 0; overflow-y: auto; padding: 26px 30px 42px; }
.detail-head { padding-bottom: 18px; border-bottom: 1px solid rgba(255, 255, 255, 0.07); }
.detail-head h3 { margin-top: 5px; color: var(--text-primary); font-size: 23px; line-height: 1.5; }
.detail-head time { display: block; margin-top: 4px; color: var(--text-muted); font-family: var(--font-mono); font-size: 9px; }
.guide-copy {
  margin-top: 22px;
  color: var(--text-secondary);
  font-size: 14px;
  line-height: 1.9;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.guide-copy strong { color: var(--text-primary); font-weight: 700; }
.drawer-state,
.detail-placeholder {
  min-height: 160px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 20px;
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.7;
  text-align: center;
}
.detail-placeholder { min-height: 100%; }
.detail-placeholder span { font-size: 30px; }
.error-state { color: var(--color-negative); }
.error-state button {
  padding: 6px 10px;
  border: 1px solid rgba(0, 229, 255, 0.24);
  border-radius: 6px;
  background: rgba(0, 229, 255, 0.08);
  color: var(--neon-cyan);
  cursor: pointer;
}
.action-error {
  margin: 0;
  padding: 9px 14px calc(9px + env(safe-area-inset-bottom));
  border-top: 1px solid rgba(255, 82, 103, 0.2);
  background: rgba(255, 82, 103, 0.08);
  color: var(--color-negative);
  font-size: 12px;
  text-align: center;
}
.watch-guide-drawer button:focus-visible { outline: 2px solid var(--neon-cyan); outline-offset: 2px; }
.watch-guide-drawer-enter-active,
.watch-guide-drawer-leave-active { transition: opacity 0.2s ease; }
.watch-guide-drawer-enter-active .watch-guide-drawer,
.watch-guide-drawer-leave-active .watch-guide-drawer { transition: transform 0.2s ease; }
.watch-guide-drawer-enter-from,
.watch-guide-drawer-leave-to { opacity: 0; }
.watch-guide-drawer-enter-from .watch-guide-drawer,
.watch-guide-drawer-leave-to .watch-guide-drawer { transform: translateX(100%); }
@media (max-width: 680px) {
  .watch-guide-drawer { width: 100vw; }
  .drawer-head { min-height: 68px; padding: 12px 14px; }
  .drawer-head h2 { font-size: 17px; }
  .drawer-body { grid-template-columns: 118px minmax(0, 1fr); }
  .guide-index { padding: 9px 6px; }
  .guide-row { grid-template-columns: minmax(0, 1fr); }
  .guide-select { padding: 10px 8px 7px; }
  .guide-select strong { font-size: 11px; white-space: normal; }
  .guide-select time { display: none; }
  .guide-delete { padding: 5px 8px; border-top: 1px solid rgba(255, 255, 255, 0.06); border-left: 0; }
  .guide-detail { padding: 20px 15px 34px; }
  .detail-head h3 { font-size: 18px; }
  .guide-copy { margin-top: 17px; font-size: 13px; line-height: 1.82; }
}
</style>
