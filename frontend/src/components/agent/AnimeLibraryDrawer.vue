<template>
  <Teleport to="body">
    <Transition name="anime-library-drawer">
      <div v-if="open" class="library-layer">
        <button class="library-backdrop" type="button" tabindex="-1" aria-label="关闭番剧大全" @click="close"></button>
        <aside
          ref="drawerRef"
          class="library-drawer"
          role="dialog"
          aria-modal="true"
          aria-labelledby="anime-library-title"
          tabindex="-1"
          @keydown.esc.stop="close"
          @keydown.tab="trapFocus"
        >
          <header class="library-head">
            <div>
              <span>ANIME LIBRARY</span>
              <h2 id="anime-library-title">番剧大全</h2>
              <p>管理观看状态，推荐助手只会选择“未看过”的作品</p>
            </div>
            <button class="drawer-close" type="button" aria-label="关闭番剧大全" @click="close">×</button>
          </header>

          <div class="library-toolbar">
            <label>
              <span class="visually-hidden">搜索番剧</span>
              <input v-model.trim="keyword" type="search" placeholder="搜索番剧名称" />
            </label>
            <div class="status-counts" aria-label="观看状态统计">
              <span>未看过 {{ counts.unwatched }}</span>
              <span>观看中 {{ counts.watching }}</span>
              <span>已看过 {{ counts.watched }}</span>
            </div>
          </div>

          <div class="library-content">
            <p v-if="loading" class="library-state">正在加载全部番剧...</p>
            <div v-else-if="loadError" class="library-state error-state" role="alert">
              <p>{{ loadError }}</p>
              <button type="button" @click="loadAnime">重新加载</button>
            </div>
            <p v-else-if="!filteredAnime.length" class="library-state">没有匹配的番剧。</p>
            <div v-else class="anime-list">
              <div v-for="anime in filteredAnime" :key="anime.anime_id" class="anime-row">
                <strong :title="anime.name">{{ anime.name }}</strong>
                <select
                  :value="anime.status"
                  :class="`status-${anime.status}`"
                  :disabled="Boolean(updating[anime.anime_id])"
                  :aria-label="`设置《${anime.name}》的观看状态`"
                  @change="changeStatus(anime, $event.target.value)"
                >
                  <option value="unwatched">未看过</option>
                  <option value="watching">观看中</option>
                  <option value="watched">已看过</option>
                </select>
              </div>
            </div>
          </div>

          <p v-if="actionError" class="action-error" role="alert">{{ actionError }}</p>
        </aside>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { getAnimeLibrary, updateAnimeLibraryStatus } from '../../api'

const props = defineProps({
  open: { type: Boolean, default: false }
})
const emit = defineEmits(['close'])

const drawerRef = ref(null)
const animeItems = ref([])
const keyword = ref('')
const loading = ref(false)
const loadError = ref('')
const actionError = ref('')
const updating = ref({})

let requestVersion = 0
let previousFocus = null
let disposed = false

const filteredAnime = computed(() => {
  const query = keyword.value.toLocaleLowerCase('zh-CN')
  if (!query) return animeItems.value
  return animeItems.value.filter((anime) => String(anime.name || '').toLocaleLowerCase('zh-CN').includes(query))
})

const counts = computed(() => {
  const result = { unwatched: 0, watching: 0, watched: 0 }
  for (const anime of animeItems.value) {
    if (Object.prototype.hasOwnProperty.call(result, anime.status)) result[anime.status] += 1
  }
  return result
})

function close() {
  emit('close')
}

async function loadAnime() {
  const version = ++requestVersion
  loading.value = true
  loadError.value = ''
  actionError.value = ''
  try {
    const data = await getAnimeLibrary()
    if (disposed || !props.open || version !== requestVersion) return
    animeItems.value = Array.isArray(data) ? data : []
  } catch (error) {
    if (!disposed && props.open && version === requestVersion) {
      loadError.value = error.message || '番剧列表加载失败，请稍后重试。'
    }
  } finally {
    if (!disposed && props.open && version === requestVersion) loading.value = false
  }
}

async function changeStatus(anime, status) {
  if (!['unwatched', 'watching', 'watched'].includes(status) || updating.value[anime.anime_id]) return
  const previous = anime.status
  anime.status = status
  updating.value = { ...updating.value, [anime.anime_id]: true }
  actionError.value = ''
  try {
    const result = await updateAnimeLibraryStatus(anime.anime_id, status)
    anime.status = result?.status || status
  } catch (error) {
    anime.status = previous
    actionError.value = `《${anime.name}》状态更新失败：${error.message || '请稍后重试。'}`
  } finally {
    const next = { ...updating.value }
    delete next[anime.anime_id]
    updating.value = next
  }
}

function trapFocus(event) {
  const focusable = drawerRef.value?.querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled)')
  if (!focusable?.length) return
  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}

watch(() => props.open, async (open) => {
  if (!open) {
    requestVersion += 1
    updating.value = {}
    await nextTick()
    previousFocus?.focus?.()
    previousFocus = null
    return
  }
  previousFocus = document.activeElement
  keyword.value = ''
  await nextTick()
  drawerRef.value?.focus()
  loadAnime()
}, { immediate: true })

onBeforeUnmount(() => {
  disposed = true
  requestVersion += 1
})
</script>

<style scoped>
.library-layer { position: fixed; inset: 0; z-index: 120; }
.library-backdrop { position: absolute; inset: 0; width: 100%; border: 0; background: rgba(1, 5, 14, 0.72); backdrop-filter: blur(5px); cursor: default; }
.library-drawer {
  position: absolute;
  inset: 0 0 0 auto;
  width: min(620px, 94vw);
  display: flex;
  flex-direction: column;
  outline: none;
  border-left: 1px solid rgba(0, 229, 255, 0.2);
  background: linear-gradient(155deg, rgba(8, 18, 34, 0.99), rgba(5, 10, 21, 0.99));
  box-shadow: -24px 0 70px rgba(0, 0, 0, 0.45);
}
.library-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; padding: 26px 28px 20px; border-bottom: 1px solid rgba(255,255,255,0.08); }
.library-head span { color: var(--neon-cyan); font-family: var(--font-mono); font-size: 10px; letter-spacing: 2px; }
.library-head h2 { margin-top: 6px; color: var(--text-primary); font-size: 25px; }
.library-head p { margin-top: 7px; color: var(--text-muted); font-size: 12px; line-height: 1.6; }
.drawer-close { width: 36px; height: 36px; flex: 0 0 36px; border: 1px solid rgba(255,255,255,0.1); border-radius: 9px; background: rgba(255,255,255,0.04); color: var(--text-secondary); font-size: 23px; cursor: pointer; }
.drawer-close:hover { border-color: rgba(0,229,255,0.35); color: var(--neon-cyan); }
.library-toolbar { padding: 18px 28px; border-bottom: 1px solid rgba(255,255,255,0.07); }
.library-toolbar input { width: 100%; height: 42px; padding: 0 14px; outline: none; border: 1px solid rgba(255,255,255,0.1); border-radius: 9px; background: rgba(255,255,255,0.045); color: var(--text-primary); }
.library-toolbar input:focus { border-color: rgba(0,229,255,0.38); }
.status-counts { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.status-counts span { padding: 5px 9px; border-radius: 999px; background: rgba(255,255,255,0.05); color: var(--text-muted); font-size: 10px; }
.library-content { min-height: 0; flex: 1; overflow-y: auto; padding: 10px 18px 24px 28px; }
.anime-list { display: flex; flex-direction: column; }
.anime-row { min-height: 58px; display: grid; grid-template-columns: minmax(0, 1fr) 112px; align-items: center; gap: 16px; padding: 9px 10px 9px 0; border-bottom: 1px solid rgba(255,255,255,0.055); }
.anime-row strong { overflow: hidden; color: var(--text-secondary); font-size: 13px; font-weight: 500; text-overflow: ellipsis; white-space: nowrap; }
.anime-row select { height: 34px; padding: 0 28px 0 10px; border-radius: 7px; outline: none; font-size: 12px; cursor: pointer; }
.anime-row select:disabled { opacity: 0.5; cursor: wait; }
.status-unwatched { border: 1px solid rgba(0,229,255,0.28); background: rgba(0,229,255,0.08); color: var(--neon-cyan); }
.status-watching { border: 1px solid rgba(255,181,71,0.32); background: rgba(255,181,71,0.09); color: var(--neon-amber); }
.status-watched { border: 1px solid rgba(153,165,190,0.22); background: rgba(153,165,190,0.08); color: var(--text-muted); }
.library-state { padding: 46px 12px; color: var(--text-muted); font-size: 13px; text-align: center; }
.error-state { color: var(--color-negative); }
.error-state button { margin-top: 12px; padding: 7px 12px; border: 1px solid rgba(255,82,103,0.28); border-radius: 7px; background: rgba(255,82,103,0.08); color: var(--color-negative); cursor: pointer; }
.action-error { margin: 0 28px 20px; padding: 10px 12px; border: 1px solid rgba(255,82,103,0.25); border-radius: 8px; background: rgba(255,82,103,0.08); color: var(--color-negative); font-size: 12px; }
.visually-hidden { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }
.anime-library-drawer-enter-active, .anime-library-drawer-leave-active { transition: opacity .2s ease; }
.anime-library-drawer-enter-active .library-drawer, .anime-library-drawer-leave-active .library-drawer { transition: transform .2s ease; }
.anime-library-drawer-enter-from, .anime-library-drawer-leave-to { opacity: 0; }
.anime-library-drawer-enter-from .library-drawer, .anime-library-drawer-leave-to .library-drawer { transform: translateX(100%); }
@media (max-width: 620px) {
  .library-drawer { width: 100%; }
  .library-head, .library-toolbar { padding-left: 18px; padding-right: 18px; }
  .library-content { padding-left: 18px; padding-right: 10px; }
  .anime-row { grid-template-columns: minmax(0, 1fr) 104px; gap: 10px; }
}
</style>
