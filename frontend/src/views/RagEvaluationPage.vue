<template>
  <main class="agent-page noise-overlay">
    <nav class="top-nav glass">
      <router-link to="/agent" class="nav-link">返回智能体中心</router-link>
      <span>RAG EVALUATION</span>
    </nav>

    <section class="workspace">
      <header class="page-head">
        <p>Chroma · PromptOps · Evidence Trace</p>
        <h1>RAG 评测看板</h1>
      </header>

      <section class="section-block">
        <div class="toolbar">
          <button :disabled="loading" @click="refreshStatus">刷新状态</button>
          <button :disabled="loading" @click="rebuildIndex">重建索引</button>
          <button :disabled="loading" @click="runEval">运行评测</button>
        </div>
        <RagIndexStatus :status="status" />
      </section>

      <section class="section-block">
        <h2>检索调试</h2>
        <div class="search-row">
          <input v-model="query" placeholder="输入要检索的舆情、评论或推荐需求" @keydown.enter.prevent="debugSearch" />
          <button :disabled="loading || !query.trim()" @click="debugSearch">检索</button>
        </div>
        <EvidenceChain :evidence="searchResult?.evidence || []" title="Search Evidence" />
      </section>

      <section class="section-block">
        <h2>评测结果</h2>
        <div v-if="latestRun" class="metrics">
          <div><span>Hit Rate</span><strong>{{ pct(latestRun.metrics?.retrieval_hit_rate) }}</strong></div>
          <div><span>Coverage</span><strong>{{ pct(latestRun.metrics?.reference_coverage) }}</strong></div>
          <div><span>Risk</span><strong>{{ pct(latestRun.metrics?.hallucination_risk) }}</strong></div>
          <div><span>Passed</span><strong>{{ latestRun.metrics?.passed || 0 }}/{{ latestRun.metrics?.total || 0 }}</strong></div>
        </div>
        <article v-for="item in latestRun?.items || []" :key="item.id" class="eval-item">
          <div>
            <strong>{{ item.passed ? 'PASS' : 'CHECK' }}</strong>
            <span>{{ item.query }}</span>
          </div>
          <EvidenceChain :evidence="item.evidence || []" title="Case Evidence" />
        </article>
      </section>

      <p v-if="message" class="status-line">{{ message }}</p>
      <p v-if="error" class="error">{{ error }}</p>
    </section>
  </main>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { getRagEvalRun, getRagIndexJob, getRagIndexStatus, rebuildRagIndex, runRagEval, searchRag } from '../api'
import EvidenceChain from '../components/agent/EvidenceChain.vue'
import RagIndexStatus from '../components/agent/RagIndexStatus.vue'

const status = ref(null)
const latestRun = ref(null)
const searchResult = ref(null)
const query = ref('')
const loading = ref(false)
const message = ref('')
const error = ref('')

function pct(value) {
  return `${Math.round(Number(value || 0) * 100)}%`
}

async function refreshStatus() {
  status.value = await getRagIndexStatus()
}

async function rebuildIndex() {
  loading.value = true
  error.value = ''
  try {
    const job = await rebuildRagIndex()
    message.value = `索引任务 #${job.job_id} 已提交`
    await waitJob(job.job_id)
    await refreshStatus()
  } catch (err) {
    error.value = err.message || '索引任务失败'
  } finally {
    loading.value = false
  }
}

async function waitJob(jobId) {
  for (let i = 0; i < 60; i += 1) {
    const job = await getRagIndexJob(jobId)
    message.value = `索引任务 #${jobId}: ${job.current_step} ${job.progress}%`
    if (job.status === 'succeeded' || job.status === 'failed') return job
    await new Promise(resolve => setTimeout(resolve, 1000))
  }
  return null
}

async function debugSearch() {
  if (!query.value.trim()) return
  loading.value = true
  error.value = ''
  try {
    searchResult.value = await searchRag({ query: query.value, top_k: 6 })
  } catch (err) {
    error.value = err.message || '检索失败'
  } finally {
    loading.value = false
  }
}

async function runEval() {
  loading.value = true
  error.value = ''
  try {
    const run = await runRagEval()
    latestRun.value = await getRagEvalRun(run.run_id)
    message.value = `评测 #${run.run_id} 完成`
  } catch (err) {
    error.value = err.message || '评测失败'
  } finally {
    loading.value = false
  }
}

onMounted(refreshStatus)
</script>

<style scoped>
.agent-page { min-height: 100vh; background: radial-gradient(circle at top left, rgba(0,229,255,0.08), transparent 34%), var(--bg-deep); }
.top-nav { display: flex; justify-content: space-between; align-items: center; padding: 14px 24px; position: sticky; top: 0; z-index: 20; }
.nav-link { color: var(--neon-cyan); font-size: 13px; }
.top-nav span { font-family: var(--font-mono); color: var(--text-muted); letter-spacing: 2px; font-size: 11px; }
.workspace { max-width: 1180px; margin: 0 auto; padding: 36px 24px 70px; display: flex; flex-direction: column; gap: 18px; }
.page-head p { color: var(--neon-cyan); font-family: var(--font-mono); font-size: 12px; letter-spacing: 2px; }
.page-head h1 { font-size: 34px; margin-top: 8px; }
.section-block { border: 1px solid rgba(255,255,255,0.07); border-radius: 8px; background: rgba(10,16,28,0.58); padding: 18px; display: flex; flex-direction: column; gap: 14px; }
.toolbar, .search-row { display: flex; gap: 10px; flex-wrap: wrap; }
.search-row { display: grid; grid-template-columns: 1fr auto; }
input { height: 44px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.08); background: rgba(15,22,35,0.72); color: var(--text-primary); padding: 0 13px; min-width: 0; }
button { height: 44px; padding: 0 16px; border-radius: 8px; border: 1px solid rgba(0,229,255,0.25); background: rgba(0,229,255,0.12); color: var(--neon-cyan); }
button:disabled { opacity: 0.45; cursor: not-allowed; }
h2 { font-size: 16px; color: var(--text-primary); }
.metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.metrics div, .eval-item { border: 1px solid rgba(255,255,255,0.07); border-radius: 8px; background: rgba(255,255,255,0.035); padding: 12px; }
.metrics span, .eval-item span { color: var(--text-muted); font-size: 12px; }
.metrics strong, .eval-item strong { color: var(--text-primary); font-size: 14px; }
.eval-item { display: flex; flex-direction: column; gap: 10px; }
.eval-item div { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.status-line { color: var(--text-secondary); font-size: 14px; }
.error { color: var(--color-negative); }
@media (max-width: 760px) { .search-row, .metrics { grid-template-columns: 1fr; } }
</style>

