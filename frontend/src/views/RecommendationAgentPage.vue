<template>
  <main class="agent-page noise-overlay">
    <header class="top-nav glass" :inert="drawerOpen">
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
        <button class="anime-library-button top-anime-library" type="button" @click="openAnimeLibrary">▦ 番剧大全</button>
        <button class="watch-guide-button top-watch-guide" type="button" @click="openWatchGuides">📺 待看番剧指南</button>
        <button class="new-chat top-new-chat" type="button" :disabled="submitting || sessionLoading" @click="newChat">＋ 新建对话</button>
      </div>
    </header>

    <div class="workspace-shell" :inert="drawerOpen">
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

        <button class="anime-library-button sidebar-anime-library" type="button" @click="openAnimeLibrary">▦ 番剧大全</button>
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
                    <img
                      v-if="message.imageObjectUrl"
                      class="message-image"
                      :src="message.imageObjectUrl"
                      alt="用户上传的推荐参考图片"
                    />
                    <span v-else-if="message.imageLoading" class="image-loading">正在加载图片...</span>
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
            <div v-if="selectedImage" class="composer-image-preview">
              <img :src="selectedImage.previewUrl" alt="待发送图片预览" />
              <div>
                <strong>{{ selectedImage.file.name }}</strong>
                <span>{{ formatImageSize(selectedImage.file.size) }}</span>
              </div>
              <button
                class="remove-image"
                type="button"
                :disabled="loading || submitting || sessionLoading"
                aria-label="移除待发送图片"
                @click="removeSelectedImage"
              >移除</button>
            </div>
            <input
              ref="fileInputRef"
              class="visually-hidden"
              type="file"
              accept="image/jpeg,image/png,image/webp"
              :disabled="loading || submitting || sessionLoading"
              @change="handleImageChange"
            />
            <button
              class="image-button"
              type="button"
              :disabled="loading || submitting || sessionLoading"
              aria-label="选择一张参考图片"
              title="上传参考图片"
              @click="fileInputRef?.click()"
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <rect x="3" y="4" width="15" height="14" rx="2.5"></rect>
                <circle cx="8" cy="9" r="1.5"></circle>
                <path d="m5.5 16 3.8-3.8 2.7 2.6 2.2-2.1 2.3 2.3"></path>
                <path d="M19 8v6M16 11h6"></path>
              </svg>
            </button>
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
              class="send-button"
              type="submit"
              :disabled="loading || submitting || sessionLoading || (!input.trim() && !selectedImage)"
              aria-label="发送消息"
              title="发送消息"
            >{{ loading ? '···' : '➤' }}</button>
          </form>
        </section>
      </section>
    </div>
    <p v-if="pageNotice" class="page-notice" role="alert">{{ pageNotice }}</p>
    <WatchGuideDrawer :open="watchGuideOpen" @close="closeWatchGuides" />
    <AnimeLibraryDrawer :open="animeLibraryOpen" @close="closeAnimeLibrary" />
  </main>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  createAgentRequestId,
  deleteUnboundAgentAttachment,
  deleteAgentSession,
  getAgentAttachmentBlob,
  getAgentSession,
  getAgentSessions,
  getAgentTask,
  sendRecommendationAgentMessage,
  startRecommendationAgent,
  streamAgentTask,
  uploadRecommendationImage
} from '../api'
import RecommendationResult from '../components/agent/RecommendationResult.vue'
import AnimeLibraryDrawer from '../components/agent/AnimeLibraryDrawer.vue'
import WatchGuideDrawer from '../components/agent/WatchGuideDrawer.vue'

const input = ref('')
const inputRef = ref(null)
const fileInputRef = ref(null)
const selectedImage = ref(null)
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
const animeLibraryOpen = ref(false)
const drawerOpen = computed(() => watchGuideOpen.value || animeLibraryOpen.value)

let pollTimer = null
let streamController = null
let imageLoadController = null
let streamTextActive = false
let streamAttempt = 0
let streamResultLoading = false
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

function formatImageSize(bytes) {
  return `${(Number(bytes || 0) / 1024 / 1024).toFixed(2)} MB`
}

function releaseMessageImages() {
  if (imageLoadController) {
    imageLoadController.abort()
    imageLoadController = null
  }
  for (const message of messages.value) {
    if (message.imageObjectUrl) URL.revokeObjectURL(message.imageObjectUrl)
  }
}

function discardSelectedImage({ deleteUpload = true } = {}) {
  const current = selectedImage.value
  if (!current) return
  URL.revokeObjectURL(current.previewUrl)
  if (deleteUpload && current.attachmentId) {
    deleteUnboundAgentAttachment(current.attachmentId).catch(() => {})
  }
  selectedImage.value = null
  if (fileInputRef.value) fileInputRef.value.value = ''
}

function removeSelectedImage() {
  if (loading.value || submitting.value || sessionLoading.value) return
  discardSelectedImage()
}

function handleImageChange(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
    pageNotice.value = '仅支持 JPEG、PNG 或 WebP 图片。'
    return
  }
  if (file.size > 5 * 1024 * 1024) {
    pageNotice.value = '图片不能超过 5 MB。'
    return
  }
  discardSelectedImage()
  selectedImage.value = {
    file,
    previewUrl: URL.createObjectURL(file),
    attachmentId: null
  }
  pageNotice.value = ''
}

async function hydrateHistoryImages(loadVersion) {
  if (imageLoadController) imageLoadController.abort()
  const controller = new AbortController()
  imageLoadController = controller
  await Promise.all(messages.value.map(async (message) => {
    if (!message.attachmentId) return
    try {
      const blob = await getAgentAttachmentBlob(message.attachmentId, controller.signal)
      if (disposed || controller.signal.aborted || loadVersion !== sessionLoadVersion) return
      const objectUrl = URL.createObjectURL(blob)
      if (controller.signal.aborted || loadVersion !== sessionLoadVersion) {
        URL.revokeObjectURL(objectUrl)
        return
      }
      message.imageObjectUrl = objectUrl
      message.imageLoading = false
    } catch (error) {
      if (error?.name !== 'AbortError' && loadVersion === sessionLoadVersion) {
        message.imageLoading = false
      }
    }
  }))
  if (imageLoadController === controller) imageLoadController = null
}

function stopPolling() {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
  if (streamController) {
    streamController.abort()
    streamController = null
  }
  streamTextActive = false
  streamAttempt = 0
  streamResultLoading = false
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
  animeLibraryOpen.value = false
  watchGuideOpen.value = true
}

function closeWatchGuides() {
  watchGuideOpen.value = false
}

function openAnimeLibrary() {
  setSidebarOpen(false)
  watchGuideOpen.value = false
  animeLibraryOpen.value = true
}

function closeAnimeLibrary() {
  animeLibraryOpen.value = false
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
  const attachment = isUser && parsedMetadata?.attachment && typeof parsedMetadata.attachment === 'object'
    ? parsedMetadata.attachment
    : null
  return {
    id: `history-${message.id}`,
    role: isUser ? 'user' : 'agent',
    content: isGenericSuccess ? '根据你的偏好，我为你挑选了以下作品：' : (message.content || ''),
    recommendationResult,
    createdAt: message.created_at || null,
    attachmentId: attachment?.id || null,
    imageObjectUrl: null,
    imageLoading: Boolean(attachment?.id),
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

function appendPendingMessage(delta) {
  const target = messages.value.find((message) => message.id === pendingMessageId)
  if (!target) {
    setPendingMessage(delta, { pending: true, failed: false })
    return
  }
  target.content += delta
  target.pending = true
  target.failed = false
  scrollToBottom()
}

async function applyStreamResult(taskId, taskSessionId, submission) {
  if (streamResultLoading || taskId !== activeTaskId || taskSessionId !== sessionId.value || submission !== submitVersion) return
  streamResultLoading = true
  try {
    const task = await getAgentTask(taskId)
    if (taskId !== activeTaskId || taskSessionId !== sessionId.value || submission !== submitVersion) return
    if (task.status === 'succeeded') {
      stopPolling()
      setSessionTask(taskSessionId, null)
      loading.value = false
      applyCompletedTask(task)
    } else if (task.status === 'failed') {
      stopPolling()
      setSessionTask(taskSessionId, null)
      loading.value = false
      setPendingMessage(task.error || '推荐任务失败，请稍后重试。', { pending: false, failed: true })
      pendingMessageId = null
      loadSessions(false)
    }
  } catch {
    // 流式快捷读取失败时保留原有轮询作为最终兜底。
  } finally {
    streamResultLoading = false
  }
}

function startTaskStream(taskId, taskSessionId, submission) {
  if (streamController) streamController.abort()
  const controller = new AbortController()
  streamController = controller
  streamTextActive = false
  streamAttempt = 0
  streamAgentTask(taskId, {
    signal: controller.signal,
    onEvent(event) {
      if (controller !== streamController || taskId !== activeTaskId || taskSessionId !== sessionId.value || submission !== submitVersion) return
      if (event.type === 'task_started') {
        const attempt = Number(event.attempt || 1)
        if (streamAttempt && attempt > streamAttempt) {
          streamTextActive = false
          setPendingMessage('任务已恢复，正在重新生成回答...', { pending: true, failed: false })
        }
        streamAttempt = Math.max(streamAttempt, attempt)
      } else if (event.type === 'phase' && !streamTextActive) {
        setPendingMessage(`${event.message || '正在处理'}...`, { pending: true, failed: false })
      } else if (event.type === 'text_delta') {
        const delta = String(event.delta || '')
        if (!delta) return
        if (!streamTextActive) {
          streamTextActive = true
          setPendingMessage('', { pending: true, failed: false })
        }
        appendPendingMessage(delta)
      } else if (['result_ready', 'task_completed', 'task_failed'].includes(event.type)) {
        applyStreamResult(taskId, taskSessionId, submission)
      }
    }
  }).catch((error) => {
    if (error?.name !== 'AbortError') {
      // 连接失败不覆盖用户界面；轮询会继续取得最终结果。
    }
  })
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
    releaseMessageImages()
    sessionId.value = detail.id
    messages.value = (Array.isArray(detail.messages) ? detail.messages : []).map(normalizeHistoryMessage)
    hydrateHistoryImages(loadVersion)
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
      startTaskStream(detail.active_task.task_id, detail.id, submission)
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
  releaseMessageImages()
  messages.value = []
  input.value = ''
  discardSelectedImage()
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

    if (!streamTextActive) {
      const step = task.current_step || '正在分析你的偏好'
      setPendingMessage(`${step}...`, { pending: true })
    }
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
  const image = selectedImage.value
  if ((!text && !image) || loading.value || submitting.value || sessionLoading.value) return
  const displayText = text || '请根据这张图片的内容为我推荐动画。'

  stopPolling()
  pageNotice.value = ''
  const submission = ++submitVersion
  const userMessageId = nextMessageId('user')
  messages.value.push({
    id: userMessageId,
    role: 'user',
    content: displayText,
    attachmentId: image?.attachmentId || null,
    imageObjectUrl: image?.previewUrl || null,
    imageLoading: false,
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
    let attachmentId = image?.attachmentId || null
    if (image && !attachmentId) {
      const uploaded = await uploadRecommendationImage(image.file)
      if (disposed || submission !== submitVersion) return
      attachmentId = uploaded.id
      image.attachmentId = attachmentId
      const localMessage = messages.value.find((message) => message.id === userMessageId)
      if (localMessage) localMessage.attachmentId = attachmentId
    }
    const clientRequestId = createAgentRequestId()
    const data = sessionId.value
      ? await sendRecommendationAgentMessage(sessionId.value, displayText, clientRequestId, attachmentId)
      : await startRecommendationAgent(displayText, clientRequestId, attachmentId)
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
    if (selectedImage.value === image) {
      selectedImage.value = null
      if (fileInputRef.value) fileInputRef.value.value = ''
    }
    loadSessions(false)
    startTaskStream(data.task_id, data.session_id, submission)
    await pollTask(data.task_id, data.session_id, submission)
  } catch (err) {
    if (disposed || submission !== submitVersion) return
    submitting.value = false
    loading.value = false
    messages.value = messages.value.filter((message) => message.id !== userMessageId)
    input.value = text
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
  if (animeLibraryOpen.value) closeAnimeLibrary()
  else if (watchGuideOpen.value) closeWatchGuides()
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
  releaseMessageImages()
  discardSelectedImage()
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
.anime-library-button {
  height: 38px;
  padding: 0 15px;
  border: 1px solid rgba(131, 122, 255, 0.34);
  border-radius: 8px;
  background: rgba(131, 122, 255, 0.1);
  color: #bcb7ff;
  cursor: pointer;
}
.anime-library-button:hover { border-color: rgba(131, 122, 255, 0.58); background: rgba(131, 122, 255, 0.16); }
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
.sidebar-anime-library { display: none; width: 100%; margin-bottom: 8px; }
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
.message-image {
  display: block;
  width: min(320px, 100%);
  max-height: 260px;
  margin-bottom: 9px;
  border-radius: 8px;
  object-fit: contain;
  background: rgba(0, 0, 0, 0.22);
}
.image-loading { display: block; margin-bottom: 7px; color: var(--text-muted); font-size: 12px; }
.message-content > p + :deep(.recommend-result) { margin-top: 13px; }
.composer {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr) 44px;
  align-items: end;
  gap: 10px;
  padding: 14px 16px calc(14px + env(safe-area-inset-bottom));
  border-top: 1px solid rgba(255, 255, 255, 0.07);
  background: rgba(6, 11, 20, 0.82);
}
.composer-image-preview {
  grid-column: 1 / -1;
  display: grid;
  grid-template-columns: 54px minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  padding: 8px;
  border: 1px solid rgba(0, 229, 255, 0.18);
  border-radius: 8px;
  background: rgba(0, 229, 255, 0.06);
}
.composer-image-preview img { width: 54px; height: 54px; border-radius: 6px; object-fit: cover; }
.composer-image-preview div { min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.composer-image-preview strong { overflow: hidden; color: var(--text-primary); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.composer-image-preview span { color: var(--text-muted); font-size: 10px; }
.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
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
.composer .image-button, .composer .send-button {
  width: 44px;
  height: 44px;
  border: 1px solid rgba(0, 229, 255, 0.3);
  border-radius: 9px;
  background: rgba(0, 229, 255, 0.14);
  color: var(--neon-cyan);
  cursor: pointer;
}
.composer .image-button {
  display: grid;
  place-items: center;
  background: linear-gradient(145deg, rgba(0, 229, 255, 0.18), rgba(82, 114, 255, 0.12));
  box-shadow: inset 0 0 0 1px rgba(255,255,255,0.025);
  transition: border-color .18s ease, background .18s ease, transform .18s ease;
}
.composer .image-button:hover:not(:disabled) { transform: translateY(-1px); border-color: rgba(0,229,255,0.55); background: linear-gradient(145deg, rgba(0,229,255,0.24), rgba(82,114,255,0.18)); }
.composer .image-button svg { width: 23px; height: 23px; fill: none; stroke: currentColor; stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; }
.composer .remove-image {
  width: auto;
  height: 30px;
  padding: 0 9px;
  border: 1px solid rgba(255, 82, 103, 0.24);
  border-radius: 6px;
  background: rgba(255, 82, 103, 0.08);
  color: var(--color-negative);
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
  .sidebar-anime-library, .sidebar-watch-guide { display: block; }
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
