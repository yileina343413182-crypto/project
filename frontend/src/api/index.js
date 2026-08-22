import axios from 'axios'
import { getToken, removeToken } from '../utils/auth'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000
})


// 请求拦截器：注入 JWT token
api.interceptors.request.use(config => {
  const token = getToken()
  if (token) {
    config.headers['Authorization'] = `Bearer ${token}`
  }
  return config
})

api.interceptors.response.use(
  response => {
    const res = response.data
    if (res.code === 200) {
      return res.data
    }
    return Promise.reject(new Error(res.msg || '请求失败'))
  },
  error => {
    // 401 token 失效，清除本地登录态并跳转
    if (error.response?.status === 401) {
      removeToken()
      window.location.href = '/login'
    }
    const backendMsg = error.response?.data?.msg || error.response?.data?.message || error.response?.data?.detail
    return Promise.reject(new Error(backendMsg || error.message || '请求失败'))
  }
)

// 获取动漫列表
export function getAnimeList() {
  return api.get('/anime/list')
}

// 获取情感统计
export function getSentimentStats(animeId) {
  return api.get(`/sentiment/stats/${animeId}`)
}

// 获取情感趋势（按天聚合）
export function getSentimentTrend(animeId) {
  return api.get(`/sentiment/trend/${animeId}`)
}

// 获取逐条评论情感值（折线散点图）
export function getSentimentScatter(animeId, limit = 600) {
  return api.get(`/sentiment/scatter/${animeId}`, { params: { limit } })
}

// 获取评论列表
export function getComments(animeId, params = {}) {
  return api.get(`/comments/${animeId}`, { params })
}

// 获取主题列表
export function getTopics(animeId) {
  return api.get(`/topics/${animeId}`)
}

// 获取词云数据
export function getWordCloud(animeId) {
  return api.get(`/wordcloud/${animeId}`)
}

// 实时情感预测
export function predictSentiment(text, model = 'textcnn') {
  return api.post('/sentiment/predict', { text, model })
}

// AI 推荐
export function getRecommendation(query) {
  return api.post('/recommend', { query })
}

// ===== 认证 API =====

export function register(username, password) {
  return api.post('/auth/register', { username, password })
}

export function login(username, password) {
  return api.post('/auth/login', { username, password })
}

export function getMe() {
  return api.get('/auth/me')
}

// ===== 聊天历史 API =====

export function saveChatHistory(user_content, ai_content, anime_card = null) {
  return api.post('/history/chat', { user_content, ai_content, anime_card })
}

export function getChatHistory(page = 1, page_size = 20) {
  return api.get('/history/chat', { params: { page, page_size } })
}

export function deleteChatHistory(id) {
  return api.delete(`/history/chat/${id}`)
}

// ===== Agent Center API =====

export function createAgentRequestId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  return `agent-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

export function analyzeOpinionAgent(payload, clientRequestId = createAgentRequestId()) {
  return api.post('/agent/opinion/analyze', { ...payload, client_request_id: clientRequestId })
}

export function startRecommendationAgent(query, clientRequestId = createAgentRequestId(), attachmentId = null) {
  return api.post('/agent/recommend/start', {
    query,
    client_request_id: clientRequestId,
    attachment_id: attachmentId
  })
}

export function sendRecommendationAgentMessage(session_id, message, clientRequestId = createAgentRequestId(), attachmentId = null) {
  return api.post('/agent/recommend/message', {
    session_id,
    message,
    client_request_id: clientRequestId,
    attachment_id: attachmentId
  })
}

async function authenticatedFetch(path, options = {}) {
  const headers = new Headers(options.headers || {})
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(path, { ...options, headers })
  if (response.status === 401) {
    removeToken()
    window.location.href = '/login'
    throw new Error('登录状态已失效')
  }
  return response
}

export async function uploadRecommendationImage(file) {
  const response = await authenticatedFetch('/api/agent/attachments/images', {
    method: 'POST',
    headers: { 'Content-Type': file.type },
    body: file
  })
  const payload = await response.json()
  if (!response.ok || payload.code !== 200) throw new Error(payload.msg || '图片上传失败')
  return payload.data
}

export async function getAgentAttachmentBlob(attachmentId, signal) {
  const response = await authenticatedFetch(`/api/agent/attachments/${attachmentId}/content`, { signal })
  if (!response.ok) {
    let message = '图片加载失败'
    try {
      const payload = await response.json()
      message = payload.msg || message
    } catch {
      // 二进制接口的非JSON错误保留通用提示。
    }
    throw new Error(message)
  }
  return response.blob()
}

export function deleteUnboundAgentAttachment(attachmentId) {
  return api.delete(`/agent/attachments/${attachmentId}`)
}

export function getAgentTask(taskId) {
  return api.get('/agent/tasks/' + taskId)
}

export async function streamAgentTask(taskId, { signal, after = '0-0', onEvent } = {}) {
  const headers = { Accept: 'application/x-ndjson' }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  const response = await fetch(
    `/api/agent/tasks/${taskId}/events?after=${encodeURIComponent(after)}`,
    { headers, signal }
  )
  if (response.status === 401) {
    removeToken()
    window.location.href = '/login'
    throw new Error('登录状态已失效')
  }
  if (!response.ok || !response.body) {
    let message = '流式连接失败'
    try {
      const payload = await response.json()
      message = payload.msg || payload.message || message
    } catch {
      // 非JSON错误响应保留通用提示，任务轮询仍会继续。
    }
    throw new Error(message)
  }

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  let lastEventId = after
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += value
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''
    for (const line of lines) {
      if (!line.trim()) continue
      let event
      try {
        event = JSON.parse(line)
      } catch {
        continue
      }
      if (event.event_id) lastEventId = event.event_id
      if (typeof onEvent === 'function') onEvent(event)
    }
  }
  return { lastEventId }
}

export function getAgentSessions() {
  return api.get('/agent/sessions')
}

export function getAgentSession(sessionId) {
  return api.get(`/agent/sessions/${sessionId}`)
}

export function deleteAgentSession(sessionId) {
  return api.delete(`/agent/sessions/${sessionId}`)
}

export function getWatchGuides(page = 1, page_size = 50) {
  return api.get('/agent/watch-guides', { params: { page, page_size } })
}

export function getWatchGuide(guideId) {
  return api.get(`/agent/watch-guides/${guideId}`)
}

export function deleteWatchGuide(guideId) {
  return api.delete(`/agent/watch-guides/${guideId}`)
}

export function getAnimeLibrary() {
  return api.get('/agent/anime-library')
}

export function updateAnimeLibraryStatus(animeId, status) {
  return api.put(`/agent/anime-library/${animeId}`, { status })
}

// ===== RAG / PromptOps API =====

export function rebuildRagIndex() {
  return api.post('/rag/index/rebuild')
}

export function indexRagAnime(animeId) {
  return api.post(`/rag/index/anime/${animeId}`)
}

export function getRagIndexJob(jobId) {
  return api.get(`/rag/index/jobs/${jobId}`)
}

export function getRagIndexStatus() {
  return api.get('/rag/index/status')
}

export function searchRag(payload) {
  return api.post('/rag/search', payload)
}

export function runRagEval(top_k = 5) {
  return api.post('/rag/eval/run', { top_k })
}

export function getRagEvalRuns() {
  return api.get('/rag/eval/runs')
}

export function getRagEvalRun(runId) {
  return api.get(`/rag/eval/runs/${runId}`)
}
export default api


