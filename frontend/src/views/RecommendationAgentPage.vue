<template>
  <main class="agent-page noise-overlay">
    <nav class="top-nav glass">
      <router-link to="/agent" class="nav-link">返回智能体中心</router-link>
      <span>RECOMMENDATION AGENT 2.0</span>
    </nav>

    <section class="workspace">
      <header class="page-head">
        <p>多轮澄清 · 偏好记忆 · 评论证据</p>
        <h1>推荐 Agent 2.0</h1>
      </header>

      <div class="layout">
        <section class="chat-panel">
          <div class="messages">
            <div class="bubble agent">说说你想看的风格、情绪、题材，或者你想避开的内容。</div>
            <template v-for="(msg, index) in messages" :key="index">
              <div class="bubble" :class="msg.role">{{ msg.content }}</div>
            </template>
          </div>
          <div class="input-row">
            <input v-model="input" :disabled="loading" placeholder="例如：想看轻松治愈但不要太幼稚的动画" @keydown.enter.prevent="send" />
            <button :disabled="loading || !input.trim()" @click="send">{{ loading ? '等待结果...' : '发送' }}</button>
          </div>
        </section>

        <PreferencePanel :preferences="preferences" />
      </div>

      <p v-if="taskStatus" class="status-line">{{ taskStatus }}</p>

      <section v-if="steps.length" class="section-block">
        <h2>Agent 执行步骤</h2>
        <AgentSteps :steps="steps" />
      </section>

      <section v-if="result" class="section-block no-frame">
        <RecommendationResult :result="result" />
      </section>

      <p v-if="error" class="error">{{ error }}</p>
    </section>
  </main>
</template>

<script setup>
import { onBeforeUnmount, ref } from 'vue'
import { getAgentTask, sendRecommendationAgentMessage, startRecommendationAgent } from '../api'
import AgentSteps from '../components/agent/AgentSteps.vue'
import PreferencePanel from '../components/agent/PreferencePanel.vue'
import RecommendationResult from '../components/agent/RecommendationResult.vue'

const input = ref('')
const loading = ref(false)
const error = ref('')
const sessionId = ref(null)
const activeTaskId = ref(null)
const messages = ref([])
const result = ref(null)
const steps = ref([])
const preferences = ref({})
const taskStatus = ref('')
let pollTimer = null
let pollCount = 0
let pendingMessageIndex = -1

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function setPendingMessage(content) {
  if (pendingMessageIndex >= 0 && messages.value[pendingMessageIndex]) {
    messages.value[pendingMessageIndex].content = content
  } else {
    messages.value.push({ role: 'agent', content })
    pendingMessageIndex = messages.value.length - 1
  }
}

function applyCompletedTask(task) {
  const payload = task.result || {}
  const agentResult = payload.result || {}
  result.value = agentResult
  steps.value = payload.agent_steps || agentResult.agent_steps || []
  preferences.value = agentResult.preference_updates?.preferences || preferences.value
  const reply = agentResult.need_clarification
    ? agentResult.clarifying_question
    : `已生成 ${(agentResult.recommendations || []).length} 个推荐结果。`
  setPendingMessage(reply)
  pendingMessageIndex = -1
  taskStatus.value = '推荐结果已生成'
}

async function pollTask(taskId) {
  try {
    const task = await getAgentTask(taskId)
    taskStatus.value = `任务状态：${task.current_step || task.status}`

    if (task.status === 'succeeded') {
      stopPolling()
      loading.value = false
      applyCompletedTask(task)
      return
    }

    if (task.status === 'failed') {
      stopPolling()
      loading.value = false
      error.value = task.error || '推荐任务失败'
      setPendingMessage(error.value)
      pendingMessageIndex = -1
      taskStatus.value = '任务执行失败'
      return
    }

    pollCount += 1
    if (pollCount >= 60) {
      stopPolling()
      loading.value = false
      const message = '任务执行时间较长，请稍后查看会话结果'
      error.value = message
      setPendingMessage(message)
      pendingMessageIndex = -1
      taskStatus.value = '轮询已停止'
    }
  } catch (err) {
    stopPolling()
    loading.value = false
    error.value = err.message || '查询任务状态失败'
    setPendingMessage(error.value)
    pendingMessageIndex = -1
  }
}

async function send() {
  const text = input.value.trim()
  if (!text || loading.value) return
  stopPolling()
  messages.value.push({ role: 'user', content: text })
  input.value = ''
  loading.value = true
  error.value = ''
  taskStatus.value = '任务已提交，正在排队'
  pollCount = 0
  setPendingMessage('正在思考...')

  try {
    const data = sessionId.value
      ? await sendRecommendationAgentMessage(sessionId.value, text)
      : await startRecommendationAgent(text)
    sessionId.value = data.session_id
    activeTaskId.value = data.task_id
    taskStatus.value = '任务已提交，正在推荐'
    await pollTask(data.task_id)
    if (loading.value) {
      pollTimer = setInterval(() => pollTask(data.task_id), 2000)
    }
  } catch (err) {
    loading.value = false
    error.value = err.message || '推荐 Agent 提交失败'
    setPendingMessage(error.value)
    pendingMessageIndex = -1
    taskStatus.value = ''
  }
}

onBeforeUnmount(() => {
  stopPolling()
})
</script>

<style scoped>
.agent-page { min-height: 100vh; background: radial-gradient(circle at top left, rgba(0,229,255,0.08), transparent 34%), var(--bg-deep); }
.top-nav { display: flex; justify-content: space-between; align-items: center; padding: 14px 24px; position: sticky; top: 0; z-index: 20; }
.nav-link { color: var(--neon-cyan); font-size: 13px; }
.top-nav span { font-family: var(--font-mono); color: var(--text-muted); letter-spacing: 2px; font-size: 11px; }
.workspace { max-width: 1180px; margin: 0 auto; padding: 36px 24px 70px; display: flex; flex-direction: column; gap: 18px; }
.page-head p { color: var(--neon-cyan); font-family: var(--font-mono); font-size: 12px; letter-spacing: 2px; }
.page-head h1 { font-size: 34px; margin-top: 8px; }
.layout { display: grid; grid-template-columns: minmax(0, 1fr) 280px; gap: 18px; align-items: start; }
.chat-panel { min-height: 420px; border: 1px solid rgba(255,255,255,0.07); border-radius: 8px; background: rgba(10,16,28,0.66); display: flex; flex-direction: column; }
.messages { flex: 1; padding: 18px; display: flex; flex-direction: column; gap: 12px; }
.bubble { max-width: 78%; padding: 11px 14px; border-radius: 8px; font-size: 14px; line-height: 1.75; word-break: break-word; }
.bubble.user { align-self: flex-end; background: rgba(0,229,255,0.12); color: #d8f6ff; border: 1px solid rgba(0,229,255,0.22); }
.bubble.agent { align-self: flex-start; background: rgba(255,255,255,0.04); color: var(--text-secondary); border: 1px solid rgba(255,255,255,0.07); }
.input-row { display: grid; grid-template-columns: 1fr auto; gap: 10px; padding: 14px; border-top: 1px solid rgba(255,255,255,0.06); }
input { height: 44px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.08); background: rgba(15,22,35,0.72); color: var(--text-primary); padding: 0 13px; }
button { height: 44px; padding: 0 18px; border-radius: 8px; border: 1px solid rgba(0,229,255,0.25); background: rgba(0,229,255,0.12); color: var(--neon-cyan); }
button:disabled { opacity: 0.45; cursor: not-allowed; }
.status-line { color: var(--text-secondary); font-size: 14px; }
.section-block { border: 1px solid rgba(255,255,255,0.07); border-radius: 8px; background: rgba(10,16,28,0.58); padding: 18px; }
.section-block.no-frame { border: none; background: transparent; padding: 0; }
h2 { font-size: 16px; margin-bottom: 14px; color: var(--text-primary); }
.error { color: var(--color-negative); }
@media (max-width: 900px) { .layout { grid-template-columns: 1fr; } .bubble { max-width: 92%; } }
</style>