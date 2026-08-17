<template>
  <main class="opinion-page noise-overlay">
    <header class="top-nav glass">
      <div class="nav-side nav-side-left">
        <button
          class="sidebar-toggle"
          ref="sidebarToggleRef"
          type="button"
          :aria-label="sidebarOpen ? '关闭历史对话' : '打开历史对话'"
          aria-controls="opinion-sessions"
          :aria-expanded="sidebarOpen"
          @click="setSidebarOpen(!sidebarOpen)"
        >☰</button>
        <router-link to="/agent" class="nav-link">返回智能体中心</router-link>
      </div>
      <div class="title-block">
        <strong>舆情诊断智能助手</strong>
        <span>多维评论分析 · 风险洞察 · 运营建议</span>
      </div>
      <div class="nav-side nav-side-right">
        <button class="new-diagnosis top-new-diagnosis" type="button" :disabled="submitting || sessionLoading" @click="newDiagnosis">＋ 新建诊断</button>
      </div>
    </header>

    <div class="workspace-shell">
      <button v-if="sidebarOpen && isMobile" class="sidebar-mask" type="button" tabindex="-1" aria-label="关闭历史对话" @click="setSidebarOpen(false)"></button>

      <aside
        id="opinion-sessions"
        class="session-sidebar"
        :class="{ open: sidebarOpen }"
        :inert="!sidebarOpen && isMobile"
        :aria-hidden="!sidebarOpen && isMobile ? 'true' : undefined"
        aria-label="舆情诊断历史对话"
      >
        <div class="sidebar-head">
          <div><span>DIAGNOSIS HISTORY</span><h2>历史对话</h2></div>
          <button class="sidebar-close" type="button" aria-label="关闭历史对话" @click="setSidebarOpen(false)">×</button>
        </div>
        <button class="new-diagnosis sidebar-new-diagnosis" type="button" :disabled="submitting || sessionLoading" @click="newDiagnosis">＋ 新建诊断</button>

        <div class="session-list">
          <p v-if="sessionsLoading" class="sidebar-state">正在加载历史对话...</p>
          <p v-else-if="sessionsError" class="sidebar-state error-text">{{ sessionsError }}</p>
          <p v-if="!sessionsLoading && !sessionsError && !sessions.length" class="sidebar-state">暂无历史对话</p>
          <div v-for="session in sessions" :key="session.id" class="session-row" :class="{ active: session.id === sessionId }">
            <button
              class="session-item"
              type="button"
              :aria-current="session.id === sessionId ? 'true' : undefined"
              :title="session.title || '未命名诊断'"
              :disabled="submitting || sessionLoading || deletingSessionId !== null"
              @click="selectSession(session.id)"
            >
              <span class="session-title">{{ session.title || '未命名诊断' }}</span>
              <span class="session-meta">
                <span v-if="sessionHasActiveTask(session.id)" class="session-running">运行中</span>
                <time>{{ formatSessionTime(session.updated_at || session.created_at) }}</time>
              </span>
            </button>
            <button
              class="delete-session"
              type="button"
              :disabled="sessionLoading || deletingSessionId !== null || sessionHasActiveTask(session.id)"
              :aria-label="'删除历史对话：' + (session.title || '未命名诊断')"
              :title="'删除历史对话：' + (session.title || '未命名诊断')"
              @click="removeSession(session)"
            >{{ deletingSessionId === session.id ? '···' : '删除' }}</button>
          </div>
        </div>
      </aside>

      <section ref="workspaceRef" class="analysis-stage" :inert="sidebarOpen && isMobile" :aria-busy="loading || sessionLoading" tabindex="-1">
        <div class="analysis-content">
          <header class="hero">
            <div class="hero-kicker"><span></span> LANGCHAIN 多工具协同诊断</div>
            <h1>看见评论背后的真实声音</h1>
            <p>结合情感、主题与检索证据，快速生成结构化舆情洞察。</p>
          </header>

          <form class="control-card glass" @submit.prevent="runAnalysis">
            <label class="control-field">
              <span>分析对象</span>
              <select ref="animeSelectRef" v-model="selectedAnimeId" :disabled="loading || sessionLoading">
                <option value="">请选择动漫</option>
                <option v-for="anime in animeList" :key="anime.id" :value="anime.id">{{ anime.name }} · {{ anime.comment_count }} 条评论</option>
              </select>
            </label>
            <label class="control-field query-field">
              <span>关注问题（可选）</span>
              <input v-model="query" :disabled="loading || sessionLoading" placeholder="例如：近期负面评价集中在哪些方面？" />
            </label>
            <button class="run-button" type="submit" :disabled="loading || sessionLoading || !selectedAnimeId">
              <span aria-hidden="true">⌁</span>{{ loading ? '诊断进行中...' : '生成诊断报告' }}
            </button>
          </form>

          <div v-if="taskStatus || error" class="status-stack">
            <div v-if="taskStatus" class="status-card" role="status">
              <span class="status-dot" :class="{ active: loading }"></span>
              <div><strong>{{ loading ? 'Agent 正在协同分析' : '诊断状态' }}</strong><p>{{ taskStatus }}</p></div>
            </div>
            <p v-if="error" class="error-card">{{ error }}</p>
          </div>

          <section v-if="sessionLoading" class="empty-panel compact" role="status">
            <div class="empty-orbit loading-orbit"><span></span></div>
            <h2>正在恢复历史诊断</h2>
            <p>正在读取已保存的报告与分析步骤...</p>
          </section>

          <template v-else-if="report">
            <header class="result-heading">
              <div><span>DIAGNOSIS REPORT</span><h2>{{ activeAnimeName || '舆情诊断报告' }}</h2></div>
              <time v-if="activeSessionTime">{{ formatSessionTime(activeSessionTime) }}</time>
            </header>
            <section class="report-section no-frame"><OpinionReport :report="report" /></section>
            <section v-if="steps.length" class="report-section">
              <div class="section-title"><span>01</span><h2>Agent 执行步骤</h2></div>
              <AgentSteps :steps="steps" />
            </section>
            <section v-if="report.prompt_trace" class="report-section">
              <div class="section-title"><span>02</span><h2>Prompt 工程追踪</h2></div>
              <PromptTracePanel :trace="report.prompt_trace" />
            </section>
            <section v-if="report.retrieval_evidence?.length" class="report-section">
              <div class="section-title"><span>03</span><h2>RAG 证据链</h2></div>
              <EvidenceChain :evidence="report.retrieval_evidence" title="Opinion Evidence" />
            </section>
          </template>

          <section v-else class="empty-panel">
            <div class="empty-orbit"><span>◈</span></div>
            <p class="empty-kicker">READY FOR DIAGNOSIS</p>
            <h2>{{ emptyStateTitle }}</h2>
            <p>{{ emptyStateDescription }}</p>
            <div class="empty-features" aria-hidden="true"><span>情感分布</span><i></i><span>热点主题</span><i></i><span>风险预警</span></div>
          </section>
        </div>
      </section>
    </div>
    <p v-if="pageNotice" class="page-notice" role="alert">{{ pageNotice }}</p>
  </main>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import {
  analyzeOpinionAgent,
  createAgentRequestId,
  deleteAgentSession,
  getAgentSession,
  getAgentSessions,
  getAgentTask,
  getAnimeList
} from '../api'
import AgentSteps from '../components/agent/AgentSteps.vue'
import OpinionReport from '../components/agent/OpinionReport.vue'
import EvidenceChain from '../components/agent/EvidenceChain.vue'
import PromptTracePanel from '../components/agent/PromptTracePanel.vue'

const route = useRoute()
const animeList = ref([])
const selectedAnimeId = ref(route.query.animeId || '')
const query = ref('')
const loading = ref(false)
const submitting = ref(false)
const sessionLoading = ref(false)
const sessionsLoading = ref(false)
const sessionsError = ref('')
const deletingSessionId = ref(null)
const error = ref('')
const report = ref(null)
const steps = ref([])
const taskStatus = ref('')
const sessionId = ref(null)
const sessions = ref([])
const tasksBySession = ref({})
const activeAnime = ref(null)
const activeSessionTime = ref('')
const emptyStateKind = ref('new')
const sidebarOpen = ref(false)
const isMobile = ref(false)
const pageNotice = ref('')
const animeSelectRef = ref(null)
const sidebarToggleRef = ref(null)
const workspaceRef = ref(null)

const selectedAnimeName = computed(() => {
  const selected = animeList.value.find((anime) => String(anime.id) === String(selectedAnimeId.value))
  return selected?.name || ''
})
const activeAnimeName = computed(() => activeAnime.value?.name || selectedAnimeName.value)
const emptyStateTitle = computed(() => {
  if (emptyStateKind.value === 'failed') return '本次舆情诊断未能完成'
  if (emptyStateKind.value === 'invalid') return '这条历史记录暂时无法恢复'
  if (emptyStateKind.value === 'pending') return '该历史诊断暂未生成报告'
  return '选择一部动漫开始诊断'
})
const emptyStateDescription = computed(() => {
  if (emptyStateKind.value === 'failed') return '请检查上方错误信息，调整分析条件后重新生成。'
  if (emptyStateKind.value === 'invalid') return '旧记录缺少可解析的报告内容，你仍可新建一次诊断。'
  if (emptyStateKind.value === 'pending') return '任务可能仍在后台执行，请稍后再次选择这条历史对话。'
  return '报告将汇总口碑走向、讨论焦点、潜在风险与运营建议。'
})

let activeTaskId = null
let pollTimer = null
let pollCount = 0
let submitVersion = 0
let sessionLoadVersion = 0
let sessionsRequestVersion = 0
let disposed = false

function stopPolling() {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
  activeTaskId = null
}

function sessionHasActiveTask(id) {
  return Boolean(tasksBySession.value[String(id)])
}

function setSessionTask(id, task) {
  const key = String(id)
  const next = { ...tasksBySession.value }
  if (task && ['queued', 'running'].includes(task.status)) next[key] = task
  else delete next[key]
  tasksBySession.value = next
}

function syncSessionTasks(items) {
  const next = {}
  for (const session of items) {
    if (session.active_task && ['queued', 'running'].includes(session.active_task.status)) {
      next[String(session.id)] = session.active_task
    }
  }
  tasksBySession.value = next
}

function setSidebarOpen(open, restoreFocus = true) {
  sidebarOpen.value = open
  if (open && isMobile.value) nextTick(() => document.querySelector('#opinion-sessions .sidebar-close')?.focus())
  if (!open && isMobile.value && restoreFocus) nextTick(() => sidebarToggleRef.value?.focus())
}

function updateViewport() {
  isMobile.value = window.matchMedia('(max-width: 820px)').matches
  if (!isMobile.value) sidebarOpen.value = false
}

function formatSessionTime(value) {
  if (!value) return ''
  const date = new Date(String(value).replace(' ', 'T'))
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function parseMetadata(metadata) {
  let parsed = metadata
  for (let attempt = 0; attempt < 2 && typeof parsed === 'string'; attempt += 1) {
    try {
      parsed = JSON.parse(parsed)
    } catch {
      return null
    }
  }
  return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null
}

function opinionPayloadFromMetadata(metadata) {
  const parsed = parseMetadata(metadata)
  if (!parsed) return null
  const candidate = parsed.result?.result || parsed.result || parsed
  if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate)) return null
  return candidate.report || candidate.error || candidate.anime || Array.isArray(candidate.agent_steps) ? candidate : null
}

async function loadAnimeList() {
  try {
    const data = await getAnimeList()
    animeList.value = Array.isArray(data) ? data : []
  } catch (err) {
    animeList.value = []
    pageNotice.value = err.message || '动漫列表加载失败，请稍后重试。'
  }
}

async function loadSessions(showLoading = true) {
  const requestVersion = ++sessionsRequestVersion
  if (showLoading) sessionsLoading.value = true
  sessionsError.value = ''
  try {
    const data = await getAgentSessions()
    if (disposed || requestVersion !== sessionsRequestVersion) return
    sessions.value = (Array.isArray(data) ? data : []).filter((session) => session.agent_type === 'opinion')
    syncSessionTasks(sessions.value)
  } catch (err) {
    if (!disposed && requestVersion === sessionsRequestVersion) sessionsError.value = err.message || '历史对话加载失败'
  } finally {
    if (!disposed && showLoading && requestVersion === sessionsRequestVersion) sessionsLoading.value = false
  }
}

function restoreDetail(detail) {
  const messages = Array.isArray(detail.messages) ? detail.messages : []
  const userMessage = messages.find((message) => message.role === 'user')
  const userMetadata = parseMetadata(userMessage?.metadata)
  const agentMessages = messages.filter((message) => message.role === 'agent' || message.role === 'assistant')
  let payload = null
  for (let index = agentMessages.length - 1; index >= 0 && !payload; index -= 1) {
    payload = opinionPayloadFromMetadata(agentMessages[index].metadata)
  }

  sessionId.value = detail.id
  report.value = payload?.report && typeof payload.report === 'object' ? payload.report : null
  steps.value = Array.isArray(payload?.agent_steps)
    ? payload.agent_steps
    : (Array.isArray(payload?.report?.agent_steps) ? payload.report.agent_steps : [])
  activeAnime.value = payload?.anime || (userMetadata ? { id: userMetadata.anime_id, name: userMetadata.name } : null)
  const restoredAnimeId = payload?.anime?.id ?? userMetadata?.anime_id
  selectedAnimeId.value = restoredAnimeId == null ? '' : String(restoredAnimeId)
  const savedQuery = String(userMessage?.content || '').trim()
  query.value = /^分析动漫(?:\s|$)/.test(savedQuery) ? '' : savedQuery
  activeSessionTime.value = detail.updated_at || detail.created_at || ''
  taskStatus.value = report.value ? '已恢复历史诊断报告' : ''
  error.value = !report.value && payload?.error ? String(payload.error) : ''
  emptyStateKind.value = report.value ? 'new' : (payload?.error ? 'failed' : (agentMessages.length ? 'invalid' : 'pending'))
}

async function selectSession(id) {
  if (submitting.value || sessionLoading.value || deletingSessionId.value !== null) return
  stopPolling()
  loading.value = false
  submitVersion += 1
  const loadVersion = ++sessionLoadVersion
  sessionLoading.value = true
  pageNotice.value = ''
  setSidebarOpen(false, false)
  try {
    const detail = await getAgentSession(id)
    if (disposed || loadVersion !== sessionLoadVersion) return
    if (detail.agent_type !== 'opinion') throw new Error('该记录不是舆情诊断对话')
    restoreDetail(detail)
    setSessionTask(detail.id, detail.active_task)
    if (detail.active_task) {
      loading.value = true
      activeTaskId = detail.active_task.task_id
      pollCount = 0
      taskStatus.value = `任务状态：${detail.active_task.current_step || detail.active_task.status}`
      emptyStateKind.value = 'pending'
      pollTask(detail.active_task.task_id, detail.id, submitVersion)
    }
    await nextTick()
    workspaceRef.value?.focus()
  } catch (err) {
    if (!disposed && loadVersion === sessionLoadVersion) {
      pageNotice.value = `历史对话加载失败：${err.message || '请稍后重试。'}`
    }
  } finally {
    if (!disposed && loadVersion === sessionLoadVersion) sessionLoading.value = false
  }
}

function resetDiagnosis() {
  sessionId.value = null
  selectedAnimeId.value = ''
  query.value = ''
  report.value = null
  steps.value = []
  activeAnime.value = null
  activeSessionTime.value = ''
  emptyStateKind.value = 'new'
  taskStatus.value = ''
  error.value = ''
}

function newDiagnosis() {
  if (submitting.value || sessionLoading.value) return
  stopPolling()
  loading.value = false
  submitVersion += 1
  sessionLoadVersion += 1
  resetDiagnosis()
  pageNotice.value = ''
  setSidebarOpen(false)
  nextTick(() => animeSelectRef.value?.focus())
}

async function removeSession(session) {
  if (sessionLoading.value || deletingSessionId.value !== null) return
  if (sessionHasActiveTask(session.id)) {
    pageNotice.value = '该诊断仍在后台执行，任务完成后才能删除。'
    return
  }
  if (!window.confirm(`确认删除历史对话“${session.title || '未命名诊断'}”吗？删除后不可恢复。`)) return
  deletingSessionId.value = session.id
  sessionsRequestVersion += 1
  pageNotice.value = ''
  try {
    await deleteAgentSession(session.id)
    sessions.value = sessions.value.filter((item) => item.id !== session.id)
    if (session.id === sessionId.value) {
      stopPolling()
      submitVersion += 1
      sessionLoadVersion += 1
      resetDiagnosis()
    }
  } catch (err) {
    pageNotice.value = `删除历史对话失败：${err.message || '请稍后重试。'}`
  } finally {
    deletingSessionId.value = null
  }
}

function applyCompletedTask(task) {
  const payload = task.result && typeof task.result === 'object' ? task.result : {}
  report.value = payload.report && typeof payload.report === 'object' ? payload.report : null
  steps.value = Array.isArray(payload.agent_steps)
    ? payload.agent_steps
    : (Array.isArray(payload.report?.agent_steps) ? payload.report.agent_steps : [])
  activeAnime.value = payload.anime || activeAnime.value
  taskStatus.value = report.value ? '诊断报告已生成' : '任务已完成，但未返回有效报告'
  emptyStateKind.value = report.value ? 'new' : 'invalid'
}

function pollIsCurrent(taskId, taskSessionId, submission) {
  return !disposed
    && taskId === activeTaskId
    && taskSessionId === sessionId.value
    && submission === submitVersion
}

async function pollTask(taskId, taskSessionId, submission) {
  if (!pollIsCurrent(taskId, taskSessionId, submission)) return
  try {
    const task = await getAgentTask(taskId)
    if (!pollIsCurrent(taskId, taskSessionId, submission)) return
    taskStatus.value = `任务状态：${task.current_step || task.status}`

    if (task.status === 'succeeded') {
      stopPolling()
      setSessionTask(taskSessionId, null)
      loading.value = false
      applyCompletedTask(task)
      loadSessions(false)
      return
    }

    if (task.status === 'failed') {
      stopPolling()
      setSessionTask(taskSessionId, null)
      loading.value = false
      error.value = task.error || '舆情诊断任务失败'
      taskStatus.value = '任务执行失败'
      emptyStateKind.value = 'failed'
      loadSessions(false)
      return
    }

    pollCount += 1
    if (pollCount >= 60) {
      stopPolling()
      loading.value = true
      error.value = ''
      taskStatus.value = '任务仍在后台执行，你可以切换到其他对话。'
      emptyStateKind.value = 'pending'
      loadSessions(false)
      return
    }
    setSessionTask(taskSessionId, {
      task_id: taskId,
      status: task.status,
      current_step: task.current_step
    })
    pollTimer = setTimeout(() => pollTask(taskId, taskSessionId, submission), 2000)
  } catch (err) {
    if (!pollIsCurrent(taskId, taskSessionId, submission)) return
    stopPolling()
    loading.value = sessionHasActiveTask(taskSessionId)
    error.value = err.message || '查询任务状态失败'
    taskStatus.value = '任务状态查询失败'
    emptyStateKind.value = 'failed'
    loadSessions(false)
  }
}

async function runAnalysis() {
  if (!selectedAnimeId.value || loading.value || submitting.value || sessionLoading.value) return
  stopPolling()
  const submission = ++submitVersion
  sessionLoadVersion += 1
  sessionId.value = null
  submitting.value = true
  loading.value = true
  pageNotice.value = ''
  error.value = ''
  report.value = null
  steps.value = []
  activeAnime.value = { id: Number(selectedAnimeId.value), name: selectedAnimeName.value }
  activeSessionTime.value = ''
  emptyStateKind.value = 'pending'
  taskStatus.value = '任务已提交，正在排队'
  pollCount = 0

  try {
    const clientRequestId = createAgentRequestId()
    const data = await analyzeOpinionAgent(
      {
        anime_id: Number(selectedAnimeId.value),
        name: selectedAnimeName.value,
        query: query.value.trim()
      },
      clientRequestId
    )
    if (disposed || submission !== submitVersion) return
    sessionId.value = data.session_id
    activeTaskId = data.task_id
    setSessionTask(data.session_id, {
      task_id: data.task_id,
      status: data.status,
      current_step: 'queued',
      client_request_id: data.client_request_id
    })
    submitting.value = false
    activeSessionTime.value = new Date().toISOString()
    taskStatus.value = '任务已提交，正在分析'
    loadSessions(false)
    await pollTask(data.task_id, data.session_id, submission)
  } catch (err) {
    if (disposed || submission !== submitVersion) return
    submitting.value = false
    stopPolling()
    loading.value = false
    error.value = err.message || '舆情诊断提交失败'
    taskStatus.value = ''
    emptyStateKind.value = 'failed'
  }
}

function handleEscape(event) {
  if (event.key === 'Escape' && sidebarOpen.value) setSidebarOpen(false)
}

onMounted(async () => {
  disposed = false
  updateViewport()
  window.addEventListener('resize', updateViewport)
  window.addEventListener('keydown', handleEscape)
  await Promise.all([loadAnimeList(), loadSessions()])
  if (disposed) return
  if (selectedAnimeId.value) {
    runAnalysis()
  } else if (sessions.value.length) {
    selectSession(sessions.value[0].id)
  }
})

onBeforeUnmount(() => {
  disposed = true
  submitting.value = false
  submitVersion += 1
  sessionLoadVersion += 1
  stopPolling()
  window.removeEventListener('resize', updateViewport)
  window.removeEventListener('keydown', handleEscape)
})
</script>

<style scoped>
.opinion-page {
  --opinion-cyan: #13d9f2;
  --opinion-blue: #568cff;
  height: 100vh;
  height: 100dvh;
  overflow: hidden;
  color: var(--text-primary);
  background:
    radial-gradient(circle at 78% 8%, rgba(93, 66, 165, 0.17), transparent 34%),
    radial-gradient(circle at 31% 42%, rgba(0, 180, 216, 0.08), transparent 30%),
    linear-gradient(145deg, #07111f 0%, #080f1c 58%, #0b0d19 100%);
}

.top-nav {
  position: relative;
  z-index: 60;
  height: 68px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  align-items: center;
  padding: 0 22px;
  border-bottom: 1px solid rgba(124, 213, 236, 0.1);
  background: rgba(7, 14, 25, 0.86);
  backdrop-filter: blur(18px);
}

.nav-side { display: flex; align-items: center; gap: 10px; min-width: 0; }
.nav-side-right { justify-content: flex-end; }
.nav-link {
  color: var(--text-secondary);
  font-size: 13px;
  text-decoration: none;
  transition: color 0.18s ease;
}
.nav-link:hover { color: var(--opinion-cyan); }
.title-block { text-align: center; line-height: 1.25; }
.title-block strong { display: block; font-size: 18px; letter-spacing: 0.05em; }
.title-block span { display: block; margin-top: 4px; color: var(--text-muted); font-size: 10px; letter-spacing: 0.12em; }

button { font: inherit; }
button:disabled { cursor: not-allowed; opacity: 0.46; }
button:focus-visible,
select:focus-visible,
input:focus-visible,
.nav-link:focus-visible {
  outline: 2px solid var(--opinion-cyan);
  outline-offset: 2px;
}

.sidebar-toggle,
.sidebar-close {
  display: none;
  width: 36px;
  height: 36px;
  padding: 0;
  border: 1px solid rgba(19, 217, 242, 0.24);
  border-radius: 9px;
  background: rgba(19, 217, 242, 0.08);
  color: var(--opinion-cyan);
}

.new-diagnosis {
  min-height: 38px;
  padding: 0 16px;
  border: 1px solid rgba(19, 217, 242, 0.3);
  border-radius: 9px;
  background: linear-gradient(135deg, rgba(19, 217, 242, 0.17), rgba(86, 140, 255, 0.11));
  color: var(--opinion-cyan);
  cursor: pointer;
  transition: border-color 0.18s ease, transform 0.18s ease, background 0.18s ease;
}
.new-diagnosis:not(:disabled):hover {
  border-color: rgba(19, 217, 242, 0.58);
  background: linear-gradient(135deg, rgba(19, 217, 242, 0.23), rgba(86, 140, 255, 0.16));
  transform: translateY(-1px);
}

.workspace-shell {
  height: calc(100vh - 68px);
  height: calc(100dvh - 68px);
  min-height: 0;
  display: grid;
  grid-template-columns: 264px minmax(0, 1fr);
}

.session-sidebar {
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 22px 15px 18px;
  border-right: 1px solid rgba(124, 213, 236, 0.1);
  background: linear-gradient(180deg, rgba(10, 21, 36, 0.92), rgba(7, 14, 25, 0.88));
}
.sidebar-head { display: flex; align-items: flex-start; justify-content: space-between; padding: 0 7px 16px; }
.sidebar-head span {
  color: var(--opinion-cyan);
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.16em;
}
.sidebar-head h2 { margin: 5px 0 0; font-size: 19px; }
.sidebar-new-diagnosis { width: 100%; margin-bottom: 14px; }
.session-list { min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 7px; padding: 1px 2px 10px; }
.sidebar-state { padding: 24px 10px; color: var(--text-muted); font-size: 13px; line-height: 1.7; text-align: center; }
.error-text { color: var(--color-negative); }
.session-row {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: stretch;
  border: 1px solid transparent;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.025);
  overflow: hidden;
  transition: border-color 0.18s ease, background 0.18s ease;
}
.session-row:hover { border-color: rgba(124, 213, 236, 0.12); background: rgba(255, 255, 255, 0.04); }
.session-row.active {
  border-color: rgba(19, 217, 242, 0.32);
  background: linear-gradient(100deg, rgba(19, 217, 242, 0.12), rgba(86, 140, 255, 0.04));
  box-shadow: inset 2px 0 var(--opinion-cyan);
}
.session-item {
  min-width: 0;
  padding: 12px 8px 12px 13px;
  border: 0;
  background: transparent;
  color: var(--text-primary);
  text-align: left;
  cursor: pointer;
}
.session-title { display: block; overflow: hidden; font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.session-meta { display: flex; align-items: center; gap: 7px; margin-top: 6px; }
.session-running {
  color: var(--opinion-cyan);
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.08em;
}
.session-item time { display: block; margin-top: 6px; color: var(--text-muted); font-family: var(--font-mono); font-size: 9px; }
.session-meta time { margin-top: 0; }
.delete-session {
  width: 45px;
  padding: 0 5px;
  border: 0;
  border-left: 1px solid rgba(255, 255, 255, 0.05);
  background: transparent;
  color: var(--text-muted);
  font-size: 11px;
  cursor: pointer;
  opacity: 0;
  transition: color 0.18s ease, background 0.18s ease, opacity 0.18s ease;
}
.session-row:hover .delete-session,
.session-row:focus-within .delete-session,
.session-row.active .delete-session { opacity: 1; }
.delete-session:not(:disabled):hover { color: var(--color-negative); background: rgba(255, 81, 118, 0.08); }

.analysis-stage { min-width: 0; min-height: 0; overflow-y: auto; scroll-behavior: smooth; }
.analysis-content { width: min(100%, 1220px); min-height: 100%; margin: 0 auto; padding: 42px 42px 72px; }
.hero { margin: 0 0 24px; }
.hero-kicker {
  display: flex;
  align-items: center;
  gap: 9px;
  color: var(--opinion-cyan);
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.18em;
}
.hero-kicker span { width: 26px; height: 1px; background: linear-gradient(90deg, var(--opinion-cyan), transparent); }
.hero h1 { margin: 11px 0 7px; font-size: clamp(28px, 3vw, 42px); letter-spacing: 0.02em; }
.hero p { color: var(--text-secondary); font-size: 14px; }

.control-card {
  display: grid;
  grid-template-columns: minmax(210px, 0.8fr) minmax(280px, 1.45fr) auto;
  gap: 14px;
  align-items: end;
  padding: 18px;
  border: 1px solid rgba(124, 213, 236, 0.13);
  border-radius: 14px;
  background: linear-gradient(135deg, rgba(16, 29, 47, 0.84), rgba(11, 19, 33, 0.78));
  box-shadow: 0 18px 60px rgba(0, 0, 0, 0.2);
}
.control-field { min-width: 0; display: flex; flex-direction: column; gap: 8px; }
.control-field > span { padding-left: 2px; color: var(--text-secondary); font-size: 11px; letter-spacing: 0.08em; }
select,
input {
  width: 100%;
  height: 44px;
  min-width: 0;
  padding: 0 13px;
  border: 1px solid rgba(145, 189, 216, 0.13);
  border-radius: 9px;
  background: rgba(5, 12, 22, 0.58);
  color: var(--text-primary);
  font: inherit;
  transition: border-color 0.18s ease, box-shadow 0.18s ease;
}
select:hover,
input:hover { border-color: rgba(19, 217, 242, 0.25); }
select:focus,
input:focus { border-color: rgba(19, 217, 242, 0.52); box-shadow: 0 0 0 3px rgba(19, 217, 242, 0.07); }
select option { background: #0b1422; }
input::placeholder { color: rgba(143, 158, 179, 0.58); }
.run-button {
  height: 44px;
  padding: 0 18px;
  border: 1px solid rgba(19, 217, 242, 0.33);
  border-radius: 9px;
  background: linear-gradient(135deg, rgba(19, 217, 242, 0.19), rgba(48, 109, 196, 0.18));
  color: var(--opinion-cyan);
  white-space: nowrap;
  cursor: pointer;
}
.run-button span { display: inline-block; margin-right: 7px; font-size: 17px; transform: rotate(-25deg); }
.run-button:not(:disabled):hover { border-color: rgba(19, 217, 242, 0.65); box-shadow: 0 0 24px rgba(19, 217, 242, 0.08); }

.status-stack { display: grid; gap: 10px; margin-top: 16px; }
.status-card,
.error-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 13px 16px;
  border: 1px solid rgba(124, 213, 236, 0.12);
  border-radius: 11px;
  background: rgba(8, 17, 29, 0.66);
}
.status-card strong { display: block; font-size: 13px; }
.status-card p { margin-top: 3px; color: var(--text-secondary); font-size: 12px; }
.status-dot { flex: 0 0 auto; width: 8px; height: 8px; border-radius: 50%; background: var(--opinion-cyan); box-shadow: 0 0 12px rgba(19, 217, 242, 0.7); }
.status-dot.active { animation: status-pulse 1.5s ease-in-out infinite; }
.error-card { color: var(--color-negative); border-color: rgba(255, 81, 118, 0.2); background: rgba(255, 81, 118, 0.06); font-size: 13px; }
@keyframes status-pulse { 50% { opacity: 0.35; transform: scale(0.72); } }

.empty-panel {
  min-height: 340px;
  margin-top: 26px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 44px 24px;
  border: 1px solid rgba(124, 213, 236, 0.09);
  border-radius: 16px;
  background:
    radial-gradient(circle at center, rgba(19, 217, 242, 0.045), transparent 44%),
    rgba(6, 14, 25, 0.35);
  text-align: center;
}
.empty-panel.compact { min-height: 260px; }
.empty-orbit {
  width: 74px;
  height: 74px;
  display: grid;
  place-items: center;
  margin-bottom: 20px;
  border: 1px solid rgba(19, 217, 242, 0.26);
  border-radius: 50%;
  color: var(--opinion-cyan);
  font-size: 25px;
  box-shadow: inset 0 0 30px rgba(19, 217, 242, 0.05), 0 0 30px rgba(19, 217, 242, 0.04);
}
.loading-orbit span { width: 20px; height: 20px; border: 2px solid rgba(19, 217, 242, 0.2); border-top-color: var(--opinion-cyan); border-radius: 50%; animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.empty-kicker { color: var(--opinion-cyan) !important; font-family: var(--font-mono); font-size: 9px !important; letter-spacing: 0.18em; }
.empty-panel h2 { margin: 8px 0 10px; font-size: 21px; }
.empty-panel > p { max-width: 540px; color: var(--text-secondary); font-size: 13px; line-height: 1.8; }
.empty-features { display: flex; align-items: center; gap: 12px; margin-top: 25px; color: var(--text-muted); font-size: 11px; }
.empty-features i { width: 3px; height: 3px; border-radius: 50%; background: rgba(19, 217, 242, 0.48); }

.result-heading { display: flex; align-items: flex-end; justify-content: space-between; gap: 20px; margin: 34px 2px 17px; }
.result-heading span { color: var(--opinion-cyan); font-family: var(--font-mono); font-size: 9px; letter-spacing: 0.18em; }
.result-heading h2 { margin: 5px 0 0; font-size: 24px; }
.result-heading time { color: var(--text-muted); font-family: var(--font-mono); font-size: 10px; }
.report-section {
  min-width: 0;
  margin-top: 16px;
  padding: 20px;
  border: 1px solid rgba(124, 213, 236, 0.1);
  border-radius: 14px;
  background: linear-gradient(145deg, rgba(11, 22, 37, 0.76), rgba(7, 15, 27, 0.68));
  box-shadow: 0 14px 45px rgba(0, 0, 0, 0.14);
  overflow: hidden;
}
.report-section.no-frame { padding: 0; border: 0; background: transparent; box-shadow: none; overflow: visible; }
.section-title { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; }
.section-title span { color: var(--opinion-cyan); font-family: var(--font-mono); font-size: 10px; }
.section-title h2 { margin: 0; font-size: 16px; }

.page-notice {
  position: fixed;
  right: 20px;
  bottom: 20px;
  z-index: 80;
  max-width: min(440px, calc(100vw - 40px));
  padding: 12px 15px;
  border: 1px solid rgba(255, 81, 118, 0.25);
  border-radius: 10px;
  background: rgba(36, 13, 25, 0.94);
  color: #ff9aae;
  font-size: 13px;
  box-shadow: 0 14px 42px rgba(0, 0, 0, 0.3);
}
.sidebar-mask { display: none; }

@media (max-width: 1050px) {
  .analysis-content { padding: 34px 26px 62px; }
  .control-card { grid-template-columns: minmax(190px, 0.8fr) minmax(230px, 1.2fr); }
  .run-button { grid-column: 1 / -1; }
}

@media (max-width: 820px) {
  .top-nav { height: 64px; padding: 0 14px; }
  .title-block strong { font-size: 16px; }
  .sidebar-toggle,
  .sidebar-close { display: inline-grid; place-items: center; }
  .top-new-diagnosis { display: none; }
  .workspace-shell { height: calc(100vh - 64px); height: calc(100dvh - 64px); display: block; }
  .session-sidebar {
    position: fixed;
    inset: 64px auto 0 0;
    z-index: 50;
    width: min(82vw, 294px);
    transform: translateX(-102%);
    visibility: hidden;
    box-shadow: 18px 0 55px rgba(0, 0, 0, 0.35);
    transition: transform 0.2s ease, visibility 0.2s;
  }
  .session-sidebar.open { transform: translateX(0); visibility: visible; }
  .delete-session { opacity: 1; }
  .sidebar-mask { display: block; position: fixed; inset: 64px 0 0; z-index: 40; border: 0; background: rgba(0, 0, 0, 0.58); }
  .analysis-stage { height: 100%; }
  .analysis-content { min-height: 100%; padding: 28px 18px 50px; }
  .hero h1 { font-size: 29px; }
}

@media (max-width: 580px) {
  .top-nav { grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr); gap: 8px; padding: 0 10px; }
  .nav-link { max-width: 54px; overflow: hidden; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
  .title-block span { display: none; }
  .title-block strong { font-size: 14px; letter-spacing: 0; }
  .nav-side-right { min-width: 0; }
  .analysis-content { padding: 24px 12px 40px; }
  .hero { margin-bottom: 18px; padding: 0 3px; }
  .hero h1 { margin-top: 9px; font-size: 25px; }
  .hero p { font-size: 12px; line-height: 1.7; }
  .control-card { grid-template-columns: 1fr; padding: 14px; border-radius: 12px; }
  .run-button { grid-column: auto; width: 100%; }
  .empty-panel { min-height: 300px; padding: 34px 15px; }
  .empty-features { gap: 8px; flex-wrap: wrap; justify-content: center; }
  .result-heading { align-items: flex-start; flex-direction: column; gap: 7px; }
  .result-heading h2 { font-size: 20px; overflow-wrap: anywhere; }
  .report-section { padding: 14px; border-radius: 11px; }
}

@media (prefers-reduced-motion: reduce) {
  .status-dot.active,
  .loading-orbit span { animation: none; }
  .analysis-stage { scroll-behavior: auto; }
}
</style>
