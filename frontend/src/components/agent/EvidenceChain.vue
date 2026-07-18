<template>
  <section v-if="items.length" class="evidence-chain">
    <header>
      <span>{{ title }}</span>
      <strong>{{ items.length }} items</strong>
    </header>
    <article v-for="item in items" :key="item.doc_id || item.rank" class="evidence-item">
      <div class="evidence-meta">
        <span>{{ item.source_type || item.metadata?.source_type || 'evidence' }}</span>
        <code>{{ item.doc_id || item.metadata?.doc_id }}</code>
        <strong>{{ score(item.similarity) }}</strong>
      </div>
      <p>{{ item.content }}</p>
      <div class="source-row">
        <span>{{ item.source_label || sourceLabel(item) }}</span>
        <span v-if="item.metadata?.sentiment_label">{{ item.metadata.sentiment_label }}</span>
      </div>
    </article>
  </section>
</template>

<script setup>
import { computed } from 'vue'
const props = defineProps({
  evidence: { type: Array, default: () => [] },
  title: { type: String, default: 'RAG Evidence Chain' }
})

const items = computed(() => props.evidence || [])

function score(value) {
  const num = Number(value || 0)
  return `${Math.round(num * 100)}%`
}

function sourceLabel(item) {
  const meta = item.metadata || {}
  if (meta.anime_name && meta.comment_id) return `${meta.anime_name} #${meta.comment_id}`
  return meta.anime_name || meta.source_type || 'local source'
}
</script>

<style scoped>
.evidence-chain { display: flex; flex-direction: column; gap: 10px; }
header { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
header span { color: var(--neon-cyan); font-family: var(--font-mono); font-size: 12px; letter-spacing: 1px; }
header strong { color: var(--text-muted); font-size: 12px; }
.evidence-item { border: 1px solid rgba(255,255,255,0.07); border-radius: 8px; padding: 13px; background: rgba(255,255,255,0.035); }
.evidence-meta, .source-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.evidence-meta span { color: var(--neon-amber); font-size: 12px; }
.evidence-meta code { color: var(--text-muted); font-size: 11px; word-break: break-all; }
.evidence-meta strong { margin-left: auto; color: var(--neon-cyan); font-size: 12px; }
p { margin: 9px 0; color: var(--text-secondary); line-height: 1.7; font-size: 13px; word-break: break-word; }
.source-row span { color: var(--text-muted); font-size: 12px; }
</style>


