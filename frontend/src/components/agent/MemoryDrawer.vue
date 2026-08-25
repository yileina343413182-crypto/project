<template>
  <Teleport to="body">
    <Transition name="memory-drawer">
      <div v-if="open" class="memory-layer">
        <button class="memory-backdrop" type="button" tabindex="-1" aria-label="关闭记忆管理" @click="close"></button>
        <aside
          ref="drawerRef"
          class="memory-drawer"
          role="dialog"
          aria-modal="true"
          aria-labelledby="memory-title"
          tabindex="-1"
          @keydown.esc.stop="close"
          @keydown.tab="trapFocus"
        >
          <header class="memory-head">
            <div>
              <span>LONG-TERM MEMORY</span>
              <h2 id="memory-title">推荐记忆管理</h2>
              <p>查看、更正或让推荐助手忘记已保存的长期偏好</p>
            </div>
            <button class="drawer-close" type="button" aria-label="关闭记忆管理" @click="close">×</button>
          </header>

          <div class="memory-content">
            <p v-if="loading" class="memory-state">正在加载推荐记忆...</p>
            <div v-else-if="loadError" class="memory-state error-state" role="alert">
              <p>{{ loadError }}</p>
              <button type="button" @click="loadMemories">重新加载</button>
            </div>
            <div v-else-if="!memories.length" class="memory-state empty-state">
              <strong>暂无长期记忆</strong>
              <p>在推荐对话中明确表达长期喜好后，相关内容会显示在这里。</p>
            </div>
            <div v-else class="memory-list">
              <article v-for="memory in memories" :key="memory.id" class="memory-card">
                <template v-if="editingId === memory.id">
                  <label class="edit-field">
                    <span>记忆内容</span>
                    <input v-model.trim="editValue" maxlength="120" :disabled="savingId === memory.id" />
                  </label>
                  <div class="edit-grid">
                    <label class="edit-field">
                      <span>倾向</span>
                      <select v-model="editPolarity" :disabled="savingId === memory.id">
                        <option value="positive">喜欢</option>
                        <option value="negative">避开</option>
                        <option value="neutral">中性</option>
                      </select>
                    </label>
                    <label class="edit-field">
                      <span>强度</span>
                      <select v-model="editStrength" :disabled="savingId === memory.id">
                        <option value="soft">一般偏好</option>
                        <option value="hard">明确要求</option>
                      </select>
                    </label>
                  </div>
                  <div class="card-actions">
                    <button class="cancel-button" type="button" :disabled="savingId === memory.id" @click="cancelEdit">取消</button>
                    <button class="save-button" type="button" :disabled="savingId === memory.id || !editValue" @click="saveMemory(memory)">
                      {{ savingId === memory.id ? '保存中...' : '保存修改' }}
                    </button>
                  </div>
                </template>
                <template v-else>
                  <div class="card-head">
                    <span class="type-label">{{ typeLabel(memory.memory_type) }}</span>
                    <div class="memory-tags">
                      <span :class="`polarity-${memory.polarity}`">{{ polarityLabel(memory.polarity) }}</span>
                      <span>{{ memory.strength === 'hard' ? '明确' : '一般' }}</span>
                    </div>
                  </div>
                  <p class="memory-value">{{ memoryText(memory) }}</p>
                  <p class="memory-meta">出现 {{ memory.occurrence_count || 1 }} 次 · 最近更新 {{ formatTime(memory.last_seen_at) }}</p>
                  <div class="card-actions">
                    <button class="edit-button" type="button" :disabled="busy" @click="startEdit(memory)">编辑</button>
                    <button class="forget-button" type="button" :disabled="busy" @click="forgetMemory(memory)">
                      {{ deletingId === memory.id ? '遗忘中...' : '忘记' }}
                    </button>
                  </div>
                </template>
              </article>
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
import { deleteAgentMemory, getAgentMemories, updateAgentMemory } from '../../api'

const props = defineProps({ open: { type: Boolean, default: false } })
const emit = defineEmits(['close'])

const drawerRef = ref(null)
const memories = ref([])
const loading = ref(false)
const loadError = ref('')
const actionError = ref('')
const editingId = ref(null)
const savingId = ref(null)
const deletingId = ref(null)
const editValue = ref('')
const editPolarity = ref('neutral')
const editStrength = ref('soft')

let requestVersion = 0
let previousFocus = null
let disposed = false

const busy = computed(() => savingId.value !== null || deletingId.value !== null)

const typeLabels = {
  genre_preference: '题材偏好',
  mood_preference: '情绪偏好',
  content_dislike: '内容避雷',
  studio_preference: '制作偏好',
  pacing_preference: '节奏偏好',
  viewing_habit: '观看习惯',
  recommendation_feedback: '推荐反馈'
}

function close() { emit('close') }
function typeLabel(type) { return typeLabels[type] || '推荐偏好' }
function polarityLabel(value) { return { positive: '喜欢', negative: '避开', neutral: '中性' }[value] || '中性' }
function memoryText(memory) { return String(memory?.value?.text || memory?.memory_key || '') }

function formatTime(value) {
  if (!value) return '未知'
  const date = new Date(String(value).replace(' ', 'T'))
  if (Number.isNaN(date.getTime())) return String(value)
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(date)
}

async function loadMemories() {
  const version = ++requestVersion
  loading.value = true
  loadError.value = ''
  actionError.value = ''
  cancelEdit()
  try {
    const data = await getAgentMemories()
    if (disposed || !props.open || version !== requestVersion) return
    memories.value = Array.isArray(data?.items) ? data.items : []
  } catch (error) {
    if (!disposed && props.open && version === requestVersion) loadError.value = error.message || '推荐记忆加载失败，请稍后重试。'
  } finally {
    if (!disposed && props.open && version === requestVersion) loading.value = false
  }
}

function startEdit(memory) {
  if (busy.value) return
  actionError.value = ''
  editingId.value = memory.id
  editValue.value = memoryText(memory)
  editPolarity.value = memory.polarity || 'neutral'
  editStrength.value = memory.strength || 'soft'
}

function cancelEdit() {
  if (savingId.value !== null) return
  editingId.value = null
  editValue.value = ''
  editPolarity.value = 'neutral'
  editStrength.value = 'soft'
}

async function saveMemory(memory) {
  if (savingId.value !== null || !editValue.value) return
  savingId.value = memory.id
  actionError.value = ''
  try {
    const updated = await updateAgentMemory(memory.id, {
      value: editValue.value,
      polarity: editPolarity.value,
      strength: editStrength.value
    })
    if (disposed || !props.open) return
    memories.value = memories.value.map((item) => item.id === memory.id ? updated : item)
    savingId.value = null
    cancelEdit()
  } catch (error) {
    if (!disposed && props.open) actionError.value = error.message || '推荐记忆修改失败，请稍后重试。'
  } finally {
    savingId.value = null
  }
}

async function forgetMemory(memory) {
  if (busy.value || !window.confirm(`确定让推荐助手忘记“${memoryText(memory)}”吗？`)) return
  deletingId.value = memory.id
  actionError.value = ''
  try {
    await deleteAgentMemory(memory.id)
    if (disposed || !props.open) return
    memories.value = memories.value.filter((item) => item.id !== memory.id)
  } catch (error) {
    if (!disposed && props.open) actionError.value = error.message || '推荐记忆删除失败，请稍后重试。'
  } finally {
    deletingId.value = null
  }
}

function trapFocus(event) {
  const focusable = drawerRef.value?.querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled)')
  if (!focusable?.length) return
  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
}

watch(() => props.open, async (open) => {
  if (!open) {
    requestVersion += 1
    savingId.value = null
    deletingId.value = null
    cancelEdit()
    await nextTick()
    previousFocus?.focus?.()
    previousFocus = null
    return
  }
  previousFocus = document.activeElement
  await nextTick()
  drawerRef.value?.focus()
  loadMemories()
}, { immediate: true })

onBeforeUnmount(() => {
  disposed = true
  requestVersion += 1
})
</script>

<style scoped>
.memory-layer { position: fixed; inset: 0; z-index: 120; }
.memory-backdrop { position: absolute; inset: 0; width: 100%; border: 0; background: rgba(1,5,14,.72); backdrop-filter: blur(5px); cursor: default; }
.memory-drawer { position: absolute; inset: 0 0 0 auto; width: min(560px, 94vw); display: flex; flex-direction: column; outline: none; border-left: 1px solid rgba(73,217,177,.22); background: linear-gradient(155deg, rgba(8,18,34,.99), rgba(5,10,21,.99)); box-shadow: -24px 0 70px rgba(0,0,0,.45); }
.memory-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; padding: 26px 28px 20px; border-bottom: 1px solid rgba(255,255,255,.08); }
.memory-head span { color: #78e7c7; font-family: var(--font-mono); font-size: 10px; letter-spacing: 2px; }
.memory-head h2 { margin-top: 6px; color: var(--text-primary); font-size: 25px; }
.memory-head p { margin-top: 7px; color: var(--text-muted); font-size: 12px; line-height: 1.6; }
.drawer-close { width: 36px; height: 36px; flex: 0 0 36px; border: 1px solid rgba(255,255,255,.1); border-radius: 9px; background: rgba(255,255,255,.04); color: var(--text-secondary); font-size: 23px; cursor: pointer; }
.memory-content { min-height: 0; flex: 1; overflow-y: auto; padding: 20px 20px 28px 28px; }
.memory-list { display: flex; flex-direction: column; gap: 12px; }
.memory-card { padding: 16px; border: 1px solid rgba(255,255,255,.08); border-radius: 10px; background: rgba(255,255,255,.035); }
.card-head, .card-actions, .memory-tags { display: flex; align-items: center; }
.card-head { justify-content: space-between; gap: 12px; }
.type-label { color: #78e7c7; font-size: 12px; }
.memory-tags { gap: 6px; }
.memory-tags span { padding: 4px 7px; border-radius: 999px; background: rgba(255,255,255,.06); color: var(--text-muted); font-size: 10px; }
.memory-tags .polarity-positive { color: #78e7c7; }
.memory-tags .polarity-negative { color: var(--color-negative); }
.memory-value { margin-top: 12px; color: var(--text-primary); font-size: 15px; line-height: 1.65; overflow-wrap: anywhere; }
.memory-meta { margin-top: 8px; color: var(--text-muted); font-size: 10px; }
.card-actions { justify-content: flex-end; gap: 8px; margin-top: 14px; }
.card-actions button, .error-state button { padding: 7px 12px; border-radius: 7px; cursor: pointer; }
.edit-button, .cancel-button { border: 1px solid rgba(0,229,255,.24); background: rgba(0,229,255,.07); color: var(--neon-cyan); }
.save-button { border: 1px solid rgba(73,217,177,.3); background: rgba(73,217,177,.1); color: #78e7c7; }
.forget-button { border: 1px solid rgba(255,82,103,.25); background: rgba(255,82,103,.07); color: var(--color-negative); }
.card-actions button:disabled { opacity: .45; cursor: not-allowed; }
.edit-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.edit-field { display: flex; flex-direction: column; gap: 6px; margin-bottom: 10px; }
.edit-field span { color: var(--text-muted); font-size: 11px; }
.edit-field input, .edit-field select { height: 38px; padding: 0 11px; outline: none; border: 1px solid rgba(255,255,255,.11); border-radius: 7px; background: rgba(255,255,255,.045); color: var(--text-primary); }
.memory-state { padding: 46px 12px; color: var(--text-muted); font-size: 13px; text-align: center; }
.empty-state strong { display: block; margin-bottom: 8px; color: var(--text-secondary); }
.error-state { color: var(--color-negative); }
.error-state button { margin-top: 12px; border: 1px solid rgba(255,82,103,.28); background: rgba(255,82,103,.08); color: var(--color-negative); }
.action-error { margin: 0 28px 20px; padding: 10px 12px; border: 1px solid rgba(255,82,103,.25); border-radius: 8px; background: rgba(255,82,103,.08); color: var(--color-negative); font-size: 12px; }
.memory-drawer-enter-active, .memory-drawer-leave-active { transition: opacity .2s ease; }
.memory-drawer-enter-active .memory-drawer, .memory-drawer-leave-active .memory-drawer { transition: transform .2s ease; }
.memory-drawer-enter-from, .memory-drawer-leave-to { opacity: 0; }
.memory-drawer-enter-from .memory-drawer, .memory-drawer-leave-to .memory-drawer { transform: translateX(100%); }
@media (max-width: 620px) {
  .memory-drawer { width: 100%; }
  .memory-head { padding-left: 18px; padding-right: 18px; }
  .memory-content { padding-left: 18px; padding-right: 10px; }
  .edit-grid { grid-template-columns: 1fr; }
}
</style>
