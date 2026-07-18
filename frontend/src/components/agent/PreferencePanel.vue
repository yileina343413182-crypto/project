<template>
  <aside class="preference-panel">
    <h3>偏好记忆</h3>
    <p>推荐 Agent 会在后端保存结构化偏好，用于后续推荐排序。</p>
    <div class="pref-grid">
      <span>喜欢题材</span>
      <strong>{{ count('likes') }}</strong>
      <span>避雷项</span>
      <strong>{{ count('dislikes') }}</strong>
      <span>情绪偏好</span>
      <strong>{{ count('preferred_moods') }}</strong>
      <span>类型偏好</span>
      <strong>{{ count('preferred_genres') }}</strong>
    </div>
  </aside>
</template>

<script setup>
const props = defineProps({
  preferences: { type: Object, default: () => ({}) }
})
function count(key) {
  return (props.preferences?.[key] || []).length
}
</script>

<style scoped>
.preference-panel { border: 1px solid rgba(255,255,255,0.07); border-radius: 8px; background: rgba(10,16,28,0.66); padding: 16px; }
h3 { font-size: 15px; color: var(--neon-cyan); margin-bottom: 8px; }
p { color: var(--text-secondary); font-size: 13px; line-height: 1.7; }
.pref-grid { display: grid; grid-template-columns: 1fr auto; gap: 8px 12px; margin-top: 14px; font-size: 13px; }
.pref-grid span { color: var(--text-secondary); }
.pref-grid strong { color: var(--text-primary); font-family: var(--font-mono); }
</style>
