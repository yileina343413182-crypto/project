<template>
  <section class="rag-status">
    <div class="status-grid">
      <div>
        <span>Active Collection</span>
        <strong>{{ status?.active_collection || 'not built' }}</strong>
      </div>
      <div>
        <span>Documents</span>
        <strong>{{ status?.document_count || 0 }}</strong>
      </div>
      <div>
        <span>Embedding</span>
        <strong>{{ status?.embedding?.configured ? status.embedding.model : 'fallback' }}</strong>
      </div>
      <div>
        <span>Chroma</span>
        <strong>{{ status?.chroma_available ? 'available' : 'not installed' }}</strong>
      </div>
    </div>
    <div class="jobs">
      <article v-for="job in status?.recent_jobs || []" :key="job.id">
        <code>#{{ job.id }} {{ job.job_type }}</code>
        <span>{{ job.status }} · {{ job.progress }}%</span>
      </article>
    </div>
  </section>
</template>

<script setup>
defineProps({
  status: { type: Object, default: null }
})
</script>

<style scoped>
.rag-status { display: flex; flex-direction: column; gap: 12px; }
.status-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.status-grid div, .jobs article { border: 1px solid rgba(255,255,255,0.07); border-radius: 8px; background: rgba(255,255,255,0.035); padding: 12px; min-width: 0; }
span { color: var(--text-muted); font-size: 12px; }
.status-grid span { display: block; font-family: var(--font-mono); font-size: 11px; margin-bottom: 6px; }
strong { color: var(--text-primary); font-size: 13px; word-break: break-word; }
.jobs { display: flex; flex-direction: column; gap: 8px; }
.jobs article { display: flex; justify-content: space-between; gap: 12px; align-items: center; }
code { color: var(--neon-cyan); font-size: 12px; }
@media (max-width: 860px) { .status-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>

