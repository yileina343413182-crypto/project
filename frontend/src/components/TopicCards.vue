<template>
  <div class="topics-wrapper">
    <div v-if="topics.length === 0" class="empty-tip">
      <span class="empty-icon">∅</span>
      <span>NO TOPIC DATA</span>
    </div>
    <div v-else class="topics-list">
      <div v-for="(topic, i) in topics" :key="topic.topic_id" class="topic-card">
        <div class="topic-header">
          <div class="topic-id">
            <span class="topic-hash">#</span>{{ String(i + 1).padStart(2, '0') }}
          </div>
          <span class="topic-label">主题 {{ topic.topic_id + 1 }}</span>
          <span class="topic-weight">{{ (topic.weight * 100).toFixed(1) }}%</span>
        </div>
        <div class="topic-bar">
          <div class="topic-bar-fill" :style="{ width: (topic.weight * 100) + '%' }"></div>
        </div>
        <div class="topic-keywords">
          <span
            v-for="(kw, ki) in topic.keywords"
            :key="kw.word"
            class="keyword-tag"
            :class="'tag-' + (ki % 4)"
          >
            {{ kw.word }}
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
defineProps({
  topics: { type: Array, default: () => [] }
})
</script>

<style scoped>
.topics-wrapper {
  padding: 14px 20px 20px;
  max-height: 380px;
  overflow-y: auto;
}

.empty-tip {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 40px 0;
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: 12px;
  letter-spacing: 2px;
}

.empty-icon {
  font-size: 28px;
  opacity: 0.4;
}

.topics-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.topic-card {
  background: rgba(255, 255, 255, 0.02);
  border: 1px solid rgba(255, 255, 255, 0.04);
  border-radius: var(--radius-sm);
  padding: 14px 16px;
  transition: var(--transition);
}

.topic-card:hover {
  border-color: rgba(0, 229, 255, 0.1);
  background: rgba(0, 229, 255, 0.02);
}

.topic-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}

.topic-id {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-muted);
}

.topic-hash {
  color: var(--neon-cyan);
}

.topic-label {
  flex: 1;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.topic-weight {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--neon-cyan);
}

.topic-bar {
  width: 100%;
  height: 2px;
  background: rgba(255, 255, 255, 0.04);
  border-radius: 1px;
  margin-bottom: 10px;
  overflow: hidden;
}

.topic-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--neon-cyan), var(--neon-pink));
  border-radius: 1px;
  transition: width 0.6s ease;
}

.topic-keywords {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.keyword-tag {
  display: inline-block;
  padding: 3px 10px;
  font-size: 11px;
  font-weight: 500;
  border-radius: 4px;
  transition: var(--transition-fast);
  cursor: default;
}

.keyword-tag.tag-0 {
  color: var(--neon-cyan);
  background: rgba(0, 229, 255, 0.08);
  border: 1px solid rgba(0, 229, 255, 0.12);
}

.keyword-tag.tag-1 {
  color: var(--neon-pink);
  background: rgba(255, 77, 141, 0.08);
  border: 1px solid rgba(255, 77, 141, 0.12);
}

.keyword-tag.tag-2 {
  color: var(--neon-lime);
  background: rgba(184, 255, 87, 0.08);
  border: 1px solid rgba(184, 255, 87, 0.12);
}

.keyword-tag.tag-3 {
  color: var(--neon-amber);
  background: rgba(255, 181, 71, 0.08);
  border: 1px solid rgba(255, 181, 71, 0.12);
}

.keyword-tag:hover {
  filter: brightness(1.3);
}
</style>
