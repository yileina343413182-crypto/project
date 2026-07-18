<template>
  <section v-if="trace" class="prompt-trace">
    <div>
      <span>Prompt</span>
      <strong>{{ trace.template_name || 'unknown' }}</strong>
    </div>
    <div>
      <span>Version</span>
      <strong>{{ trace.template_version || '-' }}</strong>
    </div>
    <div>
      <span>Model</span>
      <strong>{{ trace.model || '-' }}</strong>
    </div>
    <div>
      <span>Top K</span>
      <strong>{{ trace.retrieval_top_k || 0 }}</strong>
    </div>
    <div>
      <span>Mode</span>
      <strong>{{ trace.fallback ? 'Fallback' : 'Vector' }}</strong>
    </div>
  </section>
</template>

<script setup>
defineProps({
  trace: { type: Object, default: null }
})
</script>

<style scoped>
.prompt-trace { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px; }
.prompt-trace div { border: 1px solid rgba(255,255,255,0.07); border-radius: 8px; background: rgba(255,255,255,0.035); padding: 12px; min-width: 0; }
span { display: block; color: var(--text-muted); font-size: 11px; font-family: var(--font-mono); margin-bottom: 6px; }
strong { color: var(--text-primary); font-size: 13px; word-break: break-word; }
@media (max-width: 860px) { .prompt-trace { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>

