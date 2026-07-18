<template>
  <main class="agent-page noise-overlay">
    <nav class="top-nav glass">
      <router-link to="/agent" class="nav-link">返回智能体中心</router-link>
      <span>PUBLIC OPINION AGENT</span>
    </nav>

    <section class="workspace">
      <header class="page-head">
        <p>LangChain 多工具舆情诊断</p>
        <h1>舆情诊断 Agent</h1>
      </header>

      <section class="control-panel">
        <select v-model="selectedAnimeId" :disabled="loading">
          <option value="">选择动漫</option>
          <option v-for="anime in animeList" :key="anime.id" :value="anime.id">
            {{ anime.name }} · {{ anime.comment_count }} 条评论
          </option>
        </select>
        <input v-model="query" :disabled="loading" placeholder="可选：补充你想重点分析的问题" />
        <button :disabled="loading || !selectedAnimeId" @click="runAnalysis">
          {{ loading ? '任务执行中...' : '生成舆情诊断报告' }}
        </button>
      </section>

      <p v-if="taskStatus" class="status-line">{{ taskStatus }}</p>
      <p v-if="error" class="error">{{ error }}</p>

      <section v-if="steps.length" class="section-block">
        <h2>Agent 执行步骤</h2>
        <AgentSteps :steps="steps" />
      </section>

      <section v-if="report" class="section-block no-frame">
        <OpinionReport :report="report" />
      </section>
      <section v-if="report?.prompt_trace" class="section-block">
        <h2>Prompt 工程追踪</h2>
        <PromptTracePanel :trace="report.prompt_trace" />
      </section>

      <section v-if="report?.retrieval_evidence?.length" class="section-block">
        <h2>RAG 证据链</h2>
        <EvidenceChain :evidence="report.retrieval_evidence" title="Opinion Evidence" />
      </section>
    </section>
  </main>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { analyzeOpinionAgent, getAgentTask, getAnimeList } from '../api'
import AgentSteps from '../components/agent/AgentSteps.vue'
import OpinionReport from '../components/agent/OpinionReport.vue'
import EvidenceChain from '../components/agent/EvidenceChain.vue'
import PromptTracePanel from '../components/agent/PromptTracePanel.vue'

const route = useRoute()
const animeList = ref([])
const selectedAnimeId = ref(route.query.animeId || '')
const query = ref('')
const loading = ref(false)
const error = ref('')
const report = ref(null)
const steps = ref([])
const taskStatus = ref('')
const activeTaskId = ref(null)
let pollTimer = null
let pollCount = 0

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function applyCompletedTask(task) {
  const payload = task.result || {}
  report.value = payload.report
  steps.value = payload.agent_steps || payload.report?.agent_steps || []
  taskStatus.value = '诊断报告已生成'
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
      error.value = task.error || '舆情诊断任务失败'
      taskStatus.value = '任务执行失败'
      return
    }

    pollCount += 1
    if (pollCount >= 60) {
      stopPolling()
      loading.value = false
      error.value = '任务执行时间较长，请稍后在会话记录中查看结果'
      taskStatus.value = '轮询已停止'
    }
  } catch (err) {
    stopPolling()
    loading.value = false
    error.value = err.message || '查询任务状态失败'
  }
}

async function runAnalysis() {
  if (!selectedAnimeId.value || loading.value) return
  stopPolling()
  loading.value = true
  error.value = ''
  report.value = null
  steps.value = []
  taskStatus.value = '任务已提交，正在排队'
  pollCount = 0

  try {
    const data = await analyzeOpinionAgent({ anime_id: Number(selectedAnimeId.value), query: query.value })
    activeTaskId.value = data.task_id
    taskStatus.value = '任务已提交，正在分析'
    await pollTask(data.task_id)
    if (loading.value) {
      pollTimer = setInterval(() => pollTask(data.task_id), 2000)
    }
  } catch (err) {
    loading.value = false
    error.value = err.message || '舆情诊断提交失败'
    taskStatus.value = ''
  }
}

onMounted(async () => {
  animeList.value = await getAnimeList()
  if (selectedAnimeId.value) runAnalysis()
})

onBeforeUnmount(() => {
  stopPolling()
})
</script>

<style scoped>
.agent-page { min-height: 100vh; background: radial-gradient(circle at top right, rgba(255,77,141,0.06), transparent 34%), var(--bg-deep); }
.top-nav { display: flex; justify-content: space-between; align-items: center; padding: 14px 24px; position: sticky; top: 0; z-index: 20; }
.nav-link { color: var(--neon-cyan); font-size: 13px; }
.top-nav span { font-family: var(--font-mono); color: var(--text-muted); letter-spacing: 2px; font-size: 11px; }
.workspace { max-width: 1180px; margin: 0 auto; padding: 36px 24px 70px; display: flex; flex-direction: column; gap: 18px; }
.page-head p { color: var(--neon-cyan); font-family: var(--font-mono); font-size: 12px; letter-spacing: 2px; }
.page-head h1 { font-size: 34px; margin-top: 8px; }
.control-panel { display: grid; grid-template-columns: minmax(220px, 320px) 1fr auto; gap: 12px; align-items: center; }
select, input { height: 44px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.08); background: rgba(15,22,35,0.72); color: var(--text-primary); padding: 0 13px; }
button { height: 44px; padding: 0 18px; border-radius: 8px; border: 1px solid rgba(0,229,255,0.25); background: rgba(0,229,255,0.12); color: var(--neon-cyan); }
button:disabled { opacity: 0.45; cursor: not-allowed; }
.status-line { color: var(--text-secondary); font-size: 14px; }
.section-block { border: 1px solid rgba(255,255,255,0.07); border-radius: 8px; background: rgba(10,16,28,0.58); padding: 18px; }
.section-block.no-frame { border: none; background: transparent; padding: 0; }
h2 { font-size: 16px; margin-bottom: 14px; color: var(--text-primary); }
.error { color: var(--color-negative); }
@media (max-width: 820px) { .control-panel { grid-template-columns: 1fr; } }
</style>

