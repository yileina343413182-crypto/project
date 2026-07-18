<template>
  <div class="comment-wrapper">
    <!-- Tabs -->
    <div class="tabs">
      <button
        v-for="tab in tabs"
        :key="tab.value"
        :class="['tab-btn', { active: activeTab === tab.value }]"
        @click="switchTab(tab.value)"
      >
        <span class="tab-dot" :style="{ background: tab.color }"></span>
        {{ tab.label }}
      </button>
    </div>

    <!-- Filter keyword -->
    <div v-if="filterKeyword" class="filter-bar">
      <span class="filter-icon">⊕</span>
      <span>关键词筛选：<strong>{{ filterKeyword }}</strong></span>
      <button class="clear-btn" @click="$emit('update:filterKeyword', '')">×</button>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="loading-container" style="padding: 30px 0">
      <div class="spinner"></div>
    </div>

    <!-- Table -->
    <div v-else>
      <div class="comment-table-wrap">
        <table class="comment-table">
          <thead>
            <tr>
              <th class="col-seq">#</th>
              <th class="col-content">评论内容</th>
              <th class="col-label">情感</th>
              <th class="col-score">置信度</th>
              <th class="col-time">时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="comments.length === 0">
              <td colspan="5" class="empty-row">
                <span class="empty-icon">∅</span> NO DATA
              </td>
            </tr>
            <tr v-for="c in comments" :key="c.id" class="data-row">
              <td class="col-seq">
                <span class="seq-badge">#{{ c.seq }}</span>
              </td>
              <td class="col-content">{{ c.content }}</td>
              <td class="col-label">
                <span class="sentiment-badge" :class="c.sentiment_label">
                  {{ labelMap[c.sentiment_label] || c.sentiment_label || '—' }}
                </span>
              </td>
              <td class="col-score">
                <div class="score-cell">
                  <div class="score-bar">
                    <div
                      class="score-fill"
                      :class="c.sentiment_label"
                      :style="{ width: (c.sentiment_score * 100) + '%' }"
                    ></div>
                  </div>
                  <span class="score-text">{{ (c.sentiment_score * 100).toFixed(1) }}%</span>
                </div>
              </td>
              <td class="col-time">{{ c.publish_time || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Pagination -->
      <div class="pagination" v-if="total > pageSize">
        <button class="page-btn" :disabled="page <= 1" @click="changePage(page - 1)">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5"><path d="m15 18-6-6 6-6"/></svg>
        </button>
        <span class="page-info">
          <span class="page-current">{{ page }}</span>
          <span class="page-sep">/</span>
          <span class="page-total">{{ totalPages }}</span>
        </span>
        <button class="page-btn" :disabled="page >= totalPages" @click="changePage(page + 1)">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5"><path d="m9 18 6-6-6-6"/></svg>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { getComments } from '../api'

const props = defineProps({
  animeId: { type: [String, Number], required: true },
  filterKeyword: { type: String, default: '' }
})

defineEmits(['update:filterKeyword'])

const tabs = [
  { label: '全部', value: '', color: '#00e5ff' },
  { label: '正面', value: 'positive', color: '#00e5a0' },
  { label: '负面', value: 'negative', color: '#ff4d6a' },
  { label: '中性', value: 'neutral', color: '#8b95a5' }
]

const labelMap = {
  positive: '正面',
  negative: '负面',
  neutral: '中性'
}

const activeTab = ref('')
const comments = ref([])
const loading = ref(false)
const page = ref(1)
const pageSize = 20
const total = ref(0)

const totalPages = computed(() => Math.ceil(total.value / pageSize))

async function fetchComments() {
  loading.value = true
  try {
    const params = { page: page.value, size: pageSize }
    if (activeTab.value) params.sentiment = activeTab.value
    const data = await getComments(props.animeId, params)
    comments.value = data.items || []
    total.value = data.total || 0
  } catch (e) {
    console.error('获取评论失败:', e)
    comments.value = []
  } finally {
    loading.value = false
  }
}

function switchTab(val) {
  activeTab.value = val
  page.value = 1
  fetchComments()
}

function changePage(p) {
  page.value = p
  fetchComments()
}

onMounted(fetchComments)

watch(() => props.animeId, () => {
  page.value = 1
  fetchComments()
})
</script>

<style scoped>
.comment-wrapper {
  padding: 14px 20px 20px;
}

/* ---- Tabs ---- */
.tabs {
  display: flex;
  gap: 6px;
  margin-bottom: 16px;
}

.tab-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 16px;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-muted);
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.05);
  border-radius: 6px;
  transition: var(--transition-fast);
  letter-spacing: 0.5px;
}

.tab-btn:hover {
  color: var(--text-secondary);
  background: rgba(255, 255, 255, 0.05);
}

.tab-btn.active {
  color: var(--neon-cyan);
  background: rgba(0, 229, 255, 0.08);
  border-color: rgba(0, 229, 255, 0.2);
}

.tab-btn.active .tab-dot {
  box-shadow: 0 0 6px currentColor;
}

.tab-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}

/* ---- Filter Bar ---- */
.filter-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  background: rgba(0, 229, 255, 0.05);
  border: 1px solid rgba(0, 229, 255, 0.1);
  border-radius: 6px;
  margin-bottom: 16px;
  font-size: 12px;
  color: var(--neon-cyan);
}

.filter-icon {
  font-size: 14px;
}

.clear-btn {
  margin-left: auto;
  font-size: 16px;
  color: var(--text-muted);
  background: transparent;
  width: 22px;
  height: 22px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  transition: var(--transition-fast);
}

.clear-btn:hover {
  background: rgba(255, 255, 255, 0.08);
  color: var(--neon-pink);
}

/* ---- Table ---- */
.comment-table-wrap {
  overflow-x: auto;
}

.comment-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.comment-table th {
  text-align: left;
  padding: 10px 14px;
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 500;
  letter-spacing: 1.5px;
  text-transform: uppercase;
  color: var(--text-muted);
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  white-space: nowrap;
}

.comment-table td {
  padding: 12px 14px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.03);
  vertical-align: top;
}

.data-row {
  transition: var(--transition-fast);
}

.data-row:hover td {
  background: rgba(255, 255, 255, 0.02);
}

.col-seq {
  width: 56px;
  text-align: center;
  padding-left: 10px;
  padding-right: 4px;
}

.seq-badge {
  display: inline-block;
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 600;
  color: var(--neon-cyan);
  background: rgba(0, 229, 255, 0.06);
  border: 1px solid rgba(0, 229, 255, 0.14);
  border-radius: 4px;
  padding: 2px 7px;
  letter-spacing: 0.3px;
  white-space: nowrap;
}

.col-content {
  min-width: 280px;
  max-width: 480px;
  line-height: 1.65;
  color: var(--text-primary);
  word-break: break-all;
}

.col-label {
  width: 72px;
  text-align: center;
}

.col-score {
  width: 150px;
}

.col-time {
  width: 100px;
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: 11px;
  white-space: nowrap;
}

.empty-row {
  text-align: center;
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: 12px;
  letter-spacing: 2px;
  padding: 40px 0 !important;
}

.empty-icon {
  margin-right: 6px;
}

/* ---- Sentiment Badge ---- */
.sentiment-badge {
  display: inline-block;
  padding: 3px 10px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.5px;
}

.sentiment-badge.positive {
  color: var(--color-positive);
  background: rgba(0, 229, 160, 0.1);
  border: 1px solid rgba(0, 229, 160, 0.15);
}

.sentiment-badge.negative {
  color: var(--color-negative);
  background: rgba(255, 77, 106, 0.1);
  border: 1px solid rgba(255, 77, 106, 0.15);
}

.sentiment-badge.neutral {
  color: var(--color-neutral);
  background: rgba(139, 149, 165, 0.1);
  border: 1px solid rgba(139, 149, 165, 0.15);
}

/* ---- Score Bar ---- */
.score-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

.score-bar {
  flex: 1;
  height: 4px;
  background: rgba(255, 255, 255, 0.04);
  border-radius: 2px;
  overflow: hidden;
}

.score-fill {
  height: 100%;
  border-radius: 2px;
  transition: width 0.6s cubic-bezier(0.4, 0, 0.2, 1);
}

.score-fill.positive {
  background: linear-gradient(90deg, var(--color-positive), rgba(0, 229, 160, 0.5));
  box-shadow: 0 0 6px rgba(0, 229, 160, 0.3);
}

.score-fill.negative {
  background: linear-gradient(90deg, var(--color-negative), rgba(255, 77, 106, 0.5));
  box-shadow: 0 0 6px rgba(255, 77, 106, 0.3);
}

.score-fill.neutral {
  background: linear-gradient(90deg, var(--color-neutral), rgba(139, 149, 165, 0.5));
}

.score-text {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-secondary);
  min-width: 42px;
  text-align: right;
}

/* ---- Pagination ---- */
.pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 16px;
  padding-top: 20px;
}

.page-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  color: var(--text-secondary);
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 8px;
  transition: var(--transition-fast);
}

.page-btn:hover:not(:disabled) {
  color: var(--neon-cyan);
  border-color: rgba(0, 229, 255, 0.2);
  background: rgba(0, 229, 255, 0.06);
}

.page-btn:disabled {
  opacity: 0.25;
  cursor: not-allowed;
}

.page-info {
  font-family: var(--font-mono);
  font-size: 13px;
  display: flex;
  align-items: baseline;
  gap: 4px;
}

.page-current {
  color: var(--neon-cyan);
  font-weight: 600;
}

.page-sep {
  color: var(--text-muted);
}

.page-total {
  color: var(--text-muted);
}
</style>
