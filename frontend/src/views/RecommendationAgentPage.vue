<template>
  <main class="agent-page noise-overlay">
    <header class="top-nav glass" :inert="watchGuideOpen">
      <div class="brand-block">
        <button
          class="sidebar-toggle"
          type="button"
          :aria-label="sidebarOpen ? '关闭历史会话' : '打开历史会话'"
          aria-controls="recommendation-sessions"
          :aria-expanded="sidebarOpen"
          @click="setSidebarOpen(!sidebarOpen)"
        >☰</button>
        <router-link to="/agent" class="nav-link">返回智能体中心</router-link>
      </div>
      <div class="title-block">
        <strong>动漫智能推荐助手2.0</strong>
        <p>多轮澄清 · 偏好记忆 · 智能推荐</p>
      </div>
      <div class="top-actions">
        <button class="watch-guide-button top-watch-guide" type="button" @click="openWatchGuides">📺 待看番剧指南</button>
        <button class="new-chat top-new-chat" type="button" :disabled="submitting || sessionLoading" @click="newChat">＋ 新建对话</button>
      </div>
    </header>

    <div class="workspace-shell" :inert="watchGuideOpen">
      <button
        v-if="sidebarOpen"
        class="sidebar-mask"
        type="button"
        aria-label="关闭历史会话"
        @click="setSidebarOpen(false)"
      ></button>

      <aside
        id="recommendation-sessions"
        class="session-sidebar"
        :class="{ open: sidebarOpen }"
        :inert="!sidebarOpen && isMobile"
        :aria-hidden="!sidebarOpen && isMobile ? 'true' : undefined"
        aria-label="历史会话"
      >
        <div class="sidebar-head">
          <div>
            <span>CONVERSATIONS</span>
            <h2>历史对话</h2>
          </div>
          <button type="button" class="sidebar-close" aria-label="关闭历史会话" @click="setSidebarOpen(false)">×</button>
        </div>

        <button class="watch-guide-button sidebar-watch-guide" type="button" @click="openWatchGuides">📺 待看番剧指南</button>
        <button class="new-chat sidebar-new-chat" type="button" :disabled="submitting || sessionLoading" @click="newChat">＋ 新建对话</button>

        <div class="session-list">
          <p v-if="sessionsLoading" class="sidebar-state">正在加载历史对话...</p>
          <p v-else-if="sessionsError" class="sidebar-state error-text">{{ sessionsError }}</p>
          <p v-else-if="!sessions.length" class="sidebar-state">暂无历史对话</p>
          <div
            v-for="session in sessions"
            :key="session.id"
            class="session-row"
            :class="{ active: session.id === sessionId }"
          >
            <button
              type="button"
              class="session-item"
              :aria-current="session.id === sessionId ? 'true' : undefined"
              :title="session.title || '未命名对话'"
              :disabled="submitting || sessionLoading || deletingSessionId !== null"
              @click="selectSession(session.id)"
            >
              <span class="session-title">{{ session.title || '未命名对话' }}</span>
              <span class="session-meta">
                <span v-if="sessionHasActiveTask(session.id)" class="session-running">运行中</span>
                <time>{{ formatSessionTime(session.updated_at || session.created_at) }}</time>
              </span>
            </button>
            <button
              type="button"
              class="delete-session"
              :disabled="sessionLoading || deletingSessionId !== null || sessionHasActiveTask(session.id)"
              :aria-label="`删除对话：${session.title || '未命名对话'}`"
              :title="`删除对话：${session.title || '未命名对话'}`"
              @click="removeSession(session)"
            >{{ deletingSessionId === session.id ? '···' : '删除' }}</button>
          </div>
        </div>
      </aside>

      <section class="conversation-stage">
        <section class="chat-box" aria-label="推荐对话框">
          <div
            ref="messageListRef"
            class="message-list"
            role="log"
            aria-live="polite"
            aria-relevant="additions text"
          >
            <div v-if="sessionLoading" class="empty-state compact">
              <div class="empty-icon">···</div>
              <p>正在加载对话...</p>
            </div>

            <div v-else-if="!messages.length" class="empty-state">
              <div class="empty-icon">✦</div>
              <h1>告诉我你想看什么吧</h1>
              <p>描述喜欢的题材、情绪或想避开的内容，我会结合评论与偏好为你推荐。</p>
            </div>

            <template v-else>
              <article
                v-for="message in messages"
                :key="message.id"
                class="message-row"
                :class="message.role"
              >
                <div class="avatar" aria-hidden="true">{{ message.role === 'user' ? '我' : '荐' }}</div>
                <div class="message-body" :class="{ 'has-result': message.recommendationResult }">
                  <div class="message-meta">
                    <span>{{ message.role === 'user' ? '你' : '推荐助手' }}</span>
                    <time v-if="message.createdAt">{{ formatMessageTime(message.createdAt) }}</time>
                  </div>
                  <div
                    class="message-content"
                    :class="{ pending: message.pending, failed: message.failed, 'has-result': message.recommendationResult }"
                    :role="message.pending ? 'status' : undefined"
                  >
                    <p>{{ message.content }}</p>
                    <RecommendationResult
                      v-if="message.recommendationResult"
                      :result="message.recommendationResult"
                    />
                  </div>
                </div>
              </article>
            </template>
          </div>

          <form class="composer" @submit.prevent="send">
            <textarea
              ref="inputRef"
              v-model="input"
              rows="1"
              :disabled="loading || submitting || sessionLoading"
              placeholder="描述偏好，或在推荐后继续追问作品详情"
              aria-label="输入推荐需求或后续问题"
              @keydown.enter.exact.prevent="send"
            ></textarea>
            <button
              type="submit"
              :disabled="loading || submitting || sessionLoading || !input.trim()"
              aria-label="发送消息"
              title="发送消息"
            >{{ loading ? '···' : '➤' }}</button>
          </form>
        </section>
      </section>
    </div>
    <p v-if="pageNotice" class="page-notice" role="alert">{{ pageNotice }}</p>
    <WatchGuideDrawer :open="watchGuideOpen" @close="closeWatchGuides" />
  </main>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  createAgentRequestId,
  deleteAgentSession,
  getAgentSession,
  getAgentSessions,
  getAgentTask,
  sendRecommendationAgentMessage,
  startRecommendationAgent
} from '../api'
import RecommendationResult from '../components/agent/RecommendationResult.vue'
import WatchGuideDrawer from '../components/agent/WatchGuideDrawer.vue'

const input = ref('')
const inputRef = ref(null)
const loading = ref(false)
const submitting = ref(false)
const sessionLoading = ref(false)
const sessionsLoading = ref(false)
const sessionsError = ref('')
const deletingSessionId = ref(null)
const sessionId = ref(null)
const sessions = ref([])
const tasksBySession = ref({})
const messages = ref([])
const messageListRef = ref(null)
const sidebarOpen = ref(false)
const isMobile = ref(false)
const pageNotice = ref('')
const watchGuideOpen = ref(false)

let pollTimer = null
let pollCount = 0
let activeTaskId = null
let pendingMessageId = null
let localMessageId = 0
let sessionLoadVersion = 0
let sessionsRequestVersion = 0
let submitVersion = 0
let disposed = false

function nextMessageId(prefix = 'local') {
  localMessageId += 1
  return `${prefix}-${Date.now()}-${localMessageId}`
}

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

function setSidebarOpen(open) {
  sidebarOpen.value = open
  if (!open && isMobile.value) nextTick(() => inputRef.value?.focus())
}

function openWatchGuides() {
  setSidebarOpen(false)
  watchGuideOpen.value = true
}

function closeWatchGuides() {
  watchGuideOpen.value = false
}

function updateViewport() {
  isMobile.value = window.matchMedia('(max-width: 820px)').matches
  if (!isMobile.value) sidebarOpen.value = false
}

async function scrollToBottom() {
  await nextTick()
  if (messageListRef.value) {
    messageListRef.value.scrollTop = messageListRef.value.scrollHeight
  }
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

function recommendationFromMetadata(metadata) {
  const parsed = parseMetadata(metadata)
  if (!parsed) return null
  const candidate = parsed.result?.result || parsed.result || parsed
  if (!candidate || typeof candidate !== 'object' || !Array.isArray(candidate.recommendations)) return null
  return candidate.recommendations.length ? candidate : null
}

function normalizeHistoryMessage(message) {
  const isUser = message.role === 'user'
  const parsedMetadata = parseMetadata(message.metadata)
  const recommendationResult = isUser ? null : recommendationFromMetadata(parsedMetadata)
  const isGenericSuccess = recommendationResult && message.content === '推荐结果已生成'
  return {
    id: `history-${message.id}`,
    role: isUser ? 'user' : 'agent',
    content: isGenericSuccess ? '根据你的偏好，我为你挑选了以下作品：' : (message.content || ''),
    recommendationResult,
    createdAt: message.created_at || null,
    pending: false,
    failed: Boolean(parsedMetadata?.error)
  }
}

function setPendingMessage(content, extra = {}) {
  const target = messages.value.find((message) => message.id === pendingMessageId)
  if (target) {
    target.content = content
    Object.assign(target, extra)
  } else {
    pendingMessageId = nextMessageId('agent')
    messages.value.push({
      id: pendingMessageId,
      role: 'agent',
      content,
      recommendationResult: null,
      createdAt: new Date().toISOString(),
      pending: true,
      failed: false,
      ...extra
    })
  }
  scrollToBottom()
}

async function loadSessions(showLoading = true) {
  const requestVersion = ++sessionsRequestVersion
  if (showLoading) sessionsLoading.value = true
  sessionsError.value = ''
  try {
    const data = await getAgentSessions()
    if (disposed || requestVersion !== sessionsRequestVersion) return
    sessions.value = (Array.isArray(data) ? data : []).filter((session) => session.agent_type === 'recommendation')
    syncSessionTasks(sessions.value)
  } catch (err) {
    if (!disposed && requestVersion === sessionsRequestVersion) sessionsError.value = err.message || '历史对话加载失败'
  } finally {
    if (!disposed && showLoading && requestVersion === sessionsRequestVersion) sessionsLoading.value = false
  }
}

async function selectSession(id) {
  if (submitting.value || sessionLoading.value || deletingSessionId.value !== null) return

  stopPolling()
  loading.value = false
  submitVersion += 1
  pendingMessageId = null
  const loadVersion = ++sessionLoadVersion
  sessionLoading.value = true
  setSidebarOpen(false)
  pageNotice.value = ''
  try {
    const detail = await getAgentSession(id)
    if (loadVersion !== sessionLoadVersion) return
    if (detail.agent_type !== 'recommendation') throw new Error('该会话不是推荐 Agent 对话')
    sessionId.value = detail.id
    messages.value = (Array.isArray(detail.messages) ? detail.messages : []).map(normalizeHistoryMessage)
    setSessionTask(detail.id, detail.active_task)
    if (detail.active_task) {
      loading.value = true
      activeTaskId = detail.active_task.task_id
      pollCount = 0
      setPendingMessage(`${detail.active_task.current_step || '正在分析你的偏好'}...`, {
        pending: true,
        failed: false
      })
      const submission = submitVersion
      pollTask(detail.active_task.task_id, detail.id, submission)
    }
    await scrollToBottom()
  } catch (err) {
    if (loadVersion !== sessionLoadVersion) return
    pageNotice.value = `无法加载所选历史对话：${err.message || '请稍后重试。'}`
  } finally {
    if (loadVersion === sessionLoadVersion) sessionLoading.value = false
  }
}

async function removeSession(session) {
  if (sessionLoading.value || deletingSessionId.value !== null) return
  if (sessionHasActiveTask(session.id)) {
    pageNotice.value = '该对话仍在生成回答，任务完成后才能删除。'
    return
  }
  if (!window.confirm(`确定删除对话“${session.title || '未命名对话'}”吗？删除后无法恢复。`)) return

  deletingSessionId.value = session.id
  pageNotice.value = ''
  try {
    await deleteAgentSession(session.id)
    sessions.value = sessions.value.filter((item) => item.id !== session.id)
    if (session.id === sessionId.value) newChat()
  } catch (err) {
    pageNotice.value = `删除历史对话失败：${err.message || '请稍后重试。'}`
  } finally {
    deletingSessionId.value = null
  }
}

function newChat() {
  if (submitting.value || sessionLoading.value) return
  stopPolling()
  loading.value = false
  submitVersion += 1
  sessionLoadVersion += 1
  sessionLoading.value = false
  sessionId.value = null
  messages.value = []
  input.value = ''
  pendingMessageId = null
  pageNotice.value = ''
  setSidebarOpen(false)
  nextTick(() => inputRef.value?.focus())
}

function applyCompletedTask(task) {
  const payload = task.result || {}
  if (payload.response_mode === 'conversation') {
    const answer = String(payload.answer || '').trim()
    setPendingMessage(answer || '暂时无法生成详细回答，请稍后重试。', {
      recommendationResult: null,
      pending: false,
      failed: !answer
    })
    pendingMessageId = null
    loadSessions(false)
    return
  }

  const agentResult = payload.result || {}
  const recommendations = Array.isArray(agentResult.recommendations) ? agentResult.recommendations : []
  const content = agentResult.need_clarification
    ? (agentResult.clarifying_question || '请再补充一些偏好，我会继续为你筛选。')
    : recommendations.length
      ? '根据你的偏好，我为你挑选了以下作品：'
      : '暂时没有找到合适的推荐，你可以换一种描述再试试。'

  setPendingMessage(content, {
    recommendationResult: recommendations.length ? agentResult : null,
    pending: false,
    failed: false
  })
  pendingMessageId = null
  loadSessions(false)
}

async function pollTask(taskId, taskSessionId, submission = submitVersion) {
  if (taskId !== activeTaskId || taskSessionId !== sessionId.value || submission !== submitVersion) return

  try {
    const task = await getAgentTask(taskId)
    if (taskId !== activeTaskId || taskSessionId !== sessionId.value || submission !== submitVersion) return

    if (task.status === 'succeeded') {
      stopPolling()
      setSessionTask(taskSessionId, null)
      loading.value = false
      applyCompletedTask(task)
      return
    }

    if (task.status === 'failed') {
      stopPolling()
      setSessionTask(taskSessionId, null)
      loading.value = false
      setPendingMessage(task.error || '推荐任务失败，请稍后重试。', { pending: false, failed: true })
      pendingMessageId = null
      loadSessions(false)
      return
    }

    const step = task.current_step || '正在分析你的偏好'
    setPendingMessage(`${step}...`, { pending: true })
    pollCount += 1
    if (pollCount >= 60) {
      stopPolling()
      loading.value = true
      setPendingMessage('任务仍在后台执行。你可以切换到其他对话，稍后再回来查看结果。', { pending: false, failed: false })
      pendingMessageId = null
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
    if (taskId !== activeTaskId || taskSessionId !== sessionId.value || submission !== submitVersion) return
    stopPolling()
    loading.value = sessionHasActiveTask(taskSessionId)
    setPendingMessage(err.message || '查询任务状态失败，请稍后重试。', { pending: false, failed: true })
    pendingMessageId = null
    loadSessions(false)
  }
}

async function send() {
  const text = input.value.trim()
  if (!text || loading.value || submitting.value || sessionLoading.value) return

  stopPolling()
  pageNotice.value = ''
  const submission = ++submitVersion
  const userMessageId = nextMessageId('user')
  messages.value.push({
    id: userMessageId,
    role: 'user',
    content: text,
    recommendationResult: null,
    createdAt: new Date().toISOString(),
    pending: false,
    failed: false
  })
  input.value = ''
  submitting.value = true
  loading.value = true
  pollCount = 0
  const isFollowup = messages.value.some((message) => message.recommendationResult)
  setPendingMessage(
    isFollowup ? '正在整理详细回答...' : '正在提交推荐任务...',
    { pending: true, failed: false }
  )

  try {
    const clientRequestId = createAgentRequestId()
    const data = sessionId.value
      ? await sendRecommendationAgentMessage(sessionId.value, text, clientRequestId)
      : await startRecommendationAgent(text, clientRequestId)
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
    loadSessions(false)
    await pollTask(data.task_id, data.session_id, submission)
  } catch (err) {
    if (disposed || submission !== submitVersion) return
    submitting.value = false
    loading.value = false
    messages.value = messages.value.filter((message) => message.id !== userMessageId)
    setPendingMessage(err.message || '推荐任务提交失败，请稍后重试。', { pending: false, failed: true })
    pendingMessageId = null
  }
}

function formatSessionTime(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  }).format(date)
}

function formatMessageTime(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(date)
}

function handleEscape(event) {
  if (event.key !== 'Escape') return
  if (watchGuideOpen.value) closeWatchGuides()
  else setSidebarOpen(false)
}

onMounted(() => {
  disposed = false
  updateViewport()
  loadSessions()
  window.addEventListener('keydown', handleEscape)
  window.addEventListener('resize', updateViewport)
})

onBeforeUnmount(() => {
  disposed = true
  submitVersion += 1
  submitting.value = false
  stopPolling()
  sessionLoadVersion += 1
  window.removeEventListener('keydown', handleEscape)
  window.removeEventListener('resize', updateViewport)
})
</script>

<style scoped>
.agent-page {
  height: 100vh;
  height: 100dvh;
  overflow: hidden;
  background-color: var(--bg-deep);
  background-image:
    linear-gradient(90deg, rgba(3, 9, 22, 0.72) 0%, rgba(3, 9, 22, 0.42) 54%, rgba(3, 9, 22, 0.16) 100%),
    linear-gradient(180deg, rgba(3, 8, 20, 0.12), rgba(3, 8, 20, 0.36)),
    url('../assets/recommendation-agent-bg.png');
  background-repeat: no-repeat;
  background-position: center;
  background-size: cover;
}
.top-nav {
  height: 72px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 20px;
  padding: 0 24px;
  position: relative;
  z-index: 30;
  border-bottom: 1px solid rgba(255, 255, 255, 0.07);
}
.brand-block { display: flex; align-items: center; gap: 16px; min-width: 0; }
.title-block { position: absolute; left: 50%; transform: translateX(-50%); text-align: center; pointer-events: none; }
.title-block strong { display: block; color: var(--text-primary); font-size: 17px; }
.title-block p { margin-top: 3px; color: var(--text-muted); font-size: 11px; }
.nav-link { flex-shrink: 0; color: var(--neon-cyan); font-size: 12px; }
.sidebar-toggle, .sidebar-close { display: none; }
.top-actions { display: flex; align-items: center; gap: 9px; }
.watch-guide-button {
  height: 38px;
  padding: 0 15px;
  border: 1px solid rgba(255, 181, 71, 0.3);
  border-radius: 8px;
  background: rgba(255, 181, 71, 0.08);
  color: var(--neon-amber);
  cursor: pointer;
}
.watch-guide-button:hover { border-color: rgba(255, 181, 71, 0.55); background: rgba(255, 181, 71, 0.13); }
.new-chat {
  height: 38px;
  padding: 0 15px;
  border: 1px solid rgba(0, 229, 255, 0.35);
  border-radius: 8px;
  background: rgba(0, 229, 255, 0.12);
  color: var(--neon-cyan);
  cursor: pointer;
}
.new-chat:disabled { opacity: 0.45; cursor: not-allowed; }
.workspace-shell {
  height: calc(100vh - 72px);
  height: calc(100dvh - 72px);
  display: grid;
  grid-template-columns: 258px minmax(0, 1fr);
}
.session-sidebar {
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 22px 16px;
  background: rgba(7, 12, 22, 0.7);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border-right: 1px solid rgba(255, 255, 255, 0.07);
}
.sidebar-head { display: flex; align-items: center; justify-content: space-between; padding: 0 6px 16px; }
.sidebar-head span { color: var(--neon-cyan); font-family: var(--font-mono); font-size: 9px; letter-spacing: 1.5px; }
.sidebar-head h2 { margin-top: 5px; color: var(--text-primary); font-size: 18px; }
.sidebar-watch-guide { display: none; width: 100%; margin-bottom: 8px; }
.sidebar-new-chat { display: none; width: 100%; margin-bottom: 14px; }
.session-list { min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 7px; }
.session-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: stretch;
  border: 1px solid transparent;
  border-radius: 8px;
  overflow: hidden;
}
.session-row:hover { background: rgba(255, 255, 255, 0.04); }
.session-row.active { border-color: rgba(0, 229, 255, 0.24); background: rgba(0, 229, 255, 0.08); }
.session-item {
  width: 100%;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 7px;
  padding: 12px;
  border: 1px solid transparent;
  border-radius: 0;
  background: transparent;
  color: var(--text-secondary);
  text-align: left;
  cursor: pointer;
}
.session-item:disabled { cursor: not-allowed; opacity: 0.6; }
.session-title { width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; }
.session-meta { display: flex; align-items: center; gap: 7px; }
.session-running {
  color: var(--neon-cyan);
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.08em;
}
.session-item time { color: var(--text-muted); font-family: var(--font-mono); font-size: 10px; }
.delete-session {
  align-self: stretch;
  padding: 0 9px;
  border: 0;
  border-left: 1px solid rgba(255, 255, 255, 0.06);
  background: transparent;
  color: var(--text-muted);
  font-size: 11px;
  cursor: pointer;
}
.delete-session:hover { background: rgba(255, 82, 103, 0.1); color: var(--color-negative); }
.delete-session:disabled { opacity: 0.4; cursor: not-allowed; }
.sidebar-state { padding: 18px 8px; color: var(--text-muted); font-size: 12px; text-align: center; }
.error-text { color: var(--color-negative); }
.conversation-stage { min-width: 0; min-height: 0; display: flex; justify-content: center; padding: 20px 28px; }
.chat-box {
  width: min(920px, 100%);
  min-height: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 12px;
  background: rgba(7, 13, 24, 0.6);
  backdrop-filter: blur(2px);
  -webkit-backdrop-filter: blur(2px);
  box-shadow: 0 18px 50px rgba(0, 0, 0, 0.22);
  overflow: hidden;
}
.message-list { flex: 1; min-height: 0; overflow-y: auto; padding: 28px 30px; scroll-behavior: smooth; }
.empty-state { min-height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; padding: 36px; }
.empty-state.compact { gap: 8px; }
.empty-icon {
  width: 52px;
  height: 52px;
  display: grid;
  place-items: center;
  margin-bottom: 18px;
  border: 1px solid rgba(0, 229, 255, 0.25);
  border-radius: 50%;
  background: rgba(0, 229, 255, 0.08);
  color: var(--neon-cyan);
  font-size: 25px;
}
.empty-state h1 { color: var(--text-primary); font-size: 22px; font-weight: 600; }
.empty-state p { max-width: 520px; margin-top: 10px; color: var(--text-muted); font-size: 13px; line-height: 1.7; }
.message-row { display: flex; align-items: flex-start; gap: 11px; margin-bottom: 22px; }
.message-row.user { flex-direction: row-reverse; }
.avatar {
  width: 32px;
  height: 32px;
  flex: 0 0 32px;
  display: grid;
  place-items: center;
  border: 1px solid rgba(0, 229, 255, 0.28);
  border-radius: 9px;
  background: rgba(0, 229, 255, 0.1);
  color: var(--neon-cyan);
  font-size: 12px;
}
.message-row.user .avatar { border-color: rgba(255, 255, 255, 0.13); background: rgba(255, 255, 255, 0.06); color: var(--text-secondary); }
.message-body { max-width: min(78%, 700px); min-width: 0; }
.message-row.agent .message-body.has-result { width: min(92%, 700px); max-width: 92%; }
.message-row.user .message-body { display: flex; flex-direction: column; align-items: flex-end; }
.message-meta { display: flex; align-items: center; gap: 9px; margin: 0 2px 6px; color: var(--text-muted); font-size: 11px; }
.message-meta time { font-family: var(--font-mono); font-size: 9px; }
.message-content {
  padding: 11px 14px;
  border: 1px solid rgba(255, 255, 255, 0.07);
  border-radius: 4px 12px 12px 12px;
  background: rgba(255, 255, 255, 0.04);
  color: var(--text-secondary);
  font-size: 14px;
  line-height: 1.72;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.message-row.user .message-content { border-color: rgba(0, 229, 255, 0.2); border-radius: 12px 4px 12px 12px; background: rgba(0, 229, 255, 0.11); color: #d8f6ff; }
.message-content.pending { color: var(--text-muted); }
.message-content.failed { border-color: rgba(255, 82, 103, 0.25); color: var(--color-negative); }
.message-content.has-result { width: 100%; }
.message-content > p + :deep(.recommend-result) { margin-top: 13px; }
.composer {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 44px;
  align-items: end;
  gap: 10px;
  padding: 14px 16px calc(14px + env(safe-area-inset-bottom));
  border-top: 1px solid rgba(255, 255, 255, 0.07);
  background: rgba(6, 11, 20, 0.82);
}
.composer textarea {
  min-height: 44px;
  max-height: 120px;
  resize: vertical;
  padding: 11px 13px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 8px;
  outline: none;
  background: rgba(15, 22, 35, 0.8);
  color: var(--text-primary);
  font: inherit;
  line-height: 1.5;
}
.composer textarea:focus { border-color: rgba(0, 229, 255, 0.35); }
.composer button {
  width: 44px;
  height: 44px;
  border: 1px solid rgba(0, 229, 255, 0.3);
  border-radius: 9px;
  background: rgba(0, 229, 255, 0.14);
  color: var(--neon-cyan);
  cursor: pointer;
}
.composer button:disabled { opacity: 0.38; cursor: not-allowed; }
.sidebar-mask { display: none; }
.page-notice {
  position: fixed;
  z-index: 80;
  right: 20px;
  bottom: 20px;
  max-width: min(420px, calc(100vw - 40px));
  padding: 11px 14px;
  border: 1px solid rgba(255, 82, 103, 0.28);
  border-radius: 8px;
  background: rgba(25, 12, 18, 0.94);
  color: var(--color-negative);
  font-size: 13px;
}
.agent-page button:focus-visible, .agent-page textarea:focus-visible, .agent-page a:focus-visible { outline: 2px solid var(--neon-cyan); outline-offset: 2px; }
@media (max-width: 820px) {
  .agent-page { background-position: 78% center; }
  .top-nav { height: 64px; padding: 0 14px; }
  .brand-block { gap: 10px; }
  .title-block { width: calc(100% - 150px); }
  .title-block strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; }
  .title-block p { display: none; }
  .nav-link { max-width: 28px; overflow: hidden; white-space: nowrap; font-size: 0; }
  .nav-link::before { content: '‹'; font-size: 28px; line-height: 1; }
  .sidebar-toggle, .sidebar-close { display: grid; place-items: center; border: 0; background: transparent; color: var(--text-secondary); cursor: pointer; }
  .sidebar-toggle { width: 34px; height: 34px; font-size: 18px; }
  .sidebar-close { width: 30px; height: 30px; font-size: 22px; }
  .top-actions { display: none; }
  .workspace-shell { height: calc(100vh - 64px); height: calc(100dvh - 64px); display: block; }
  .session-sidebar {
    position: fixed;
    inset: 64px auto 0 0;
    z-index: 50;
    width: min(82vw, 290px);
    transform: translateX(-100%);
    visibility: hidden;
    transition: transform 0.2s ease;
  }
  .session-sidebar.open { transform: translateX(0); visibility: visible; }
  .sidebar-watch-guide { display: block; }
  .sidebar-new-chat { display: block; }
  .sidebar-mask { display: block; position: fixed; inset: 64px 0 0; z-index: 40; border: 0; background: rgba(0, 0, 0, 0.55); }
  .conversation-stage { height: 100%; padding: 10px; }
  .chat-box { border-radius: 9px; }
  .message-list { padding: 20px 14px; }
  .message-body, .message-row.agent .message-body.has-result { max-width: calc(100% - 43px); width: auto; }
  .message-content.has-result { width: 100%; }
  .composer { padding: 10px 10px calc(10px + env(safe-area-inset-bottom)); }
}
</style>
