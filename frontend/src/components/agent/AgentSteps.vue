<template>
  <div class="agent-steps">
    <div v-for="(step, index) in steps" :key="index" class="step-row">
      <span class="step-index">{{ String(index + 1).padStart(2, '0') }}</span>
      <span class="step-name">{{ step.name }}</span>
      <span class="step-status" :class="step.status">{{ step.status }}</span>
      <span class="step-detail">{{ step.detail }}</span>
      <span class="step-time" v-if="step.elapsed_ms">{{ step.elapsed_ms }}ms</span>
    </div>
  </div>
</template>

<script setup>
defineProps({
  steps: { type: Array, default: () => [] }
})
</script>

<style scoped>
.agent-steps { display: flex; flex-direction: column; gap: 8px; }
.step-row {
  display: grid;
  grid-template-columns: 42px minmax(120px, 180px) 82px 1fr 72px;
  gap: 10px;
  align-items: center;
  padding: 9px 12px;
  border: 1px solid rgba(255,255,255,0.06);
  border-radius: 8px;
  background: rgba(255,255,255,0.03);
  font-size: 12px;
}
.step-index, .step-time { font-family: var(--font-mono); color: var(--text-muted); }
.step-name { color: var(--text-primary); font-weight: 600; }
.step-status { color: var(--neon-cyan); font-family: var(--font-mono); }
.step-status.error { color: var(--color-negative); }
.step-status.fallback { color: var(--neon-amber); }
.step-status.degraded { color: #f6c453; }
.step-status.skipped { color: var(--text-muted); }
.step-detail { color: var(--text-secondary); min-width: 0; word-break: break-word; }
@media (max-width: 760px) {
  .step-row { grid-template-columns: 36px 1fr; }
  .step-status, .step-detail, .step-time { grid-column: 2; }
}
</style>
