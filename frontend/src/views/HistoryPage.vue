<template>
  <div class="history-page noise-overlay">
    <div class="bg-grid"></div>
    <div class="bg-glow bg-glow--1"></div>
    <div class="bg-glow bg-glow--2"></div>

    <!-- 顶部导航 -->
    <header class="history-header">
      <div class="header-left">
        <button class="back-btn" @click="router.push('/')">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16">
            <path d="m15 18-6-6 6-6"/>
          </svg>
          返回首页
        </button>
        <div class="header-divider"></div>
        <h1 class="page-title">
          <img src="/irina.png" alt="伊蕾娜" class="title-avatar" />
          聊天历史记录
        </h1>
      </div>
      <div class="header-right">
        <span class="user-tag">{{ currentUser?.username }}</span>
        <span class="record-count" v-if="total > 0">共 {{ total }} 条</span>
      </div>
    </header>

    <!-- 内容区 -->
    <main class="history-main">
      <!-- 加载中 -->
      <div v-if="loading && items.length === 0" class="loading-box">
        <div class="spinner"></div>
        <span>LOADING...</span>
      </div>

      <!-- 空状态 -->
      <div v-else-if="!loading && items.length === 0" class="empty-box">
        <img src="/irina.png" alt="伊蕾娜" class="empty-avatar" />
        <p class="empty-text">还没有聊天记录哦～</p>
        <p class="empty-sub">去和伊蕾娜小助手聊聊吧</p>
        <button class="go-chat-btn" @click="router.push('/')">前往首页</button>
      </div>

      <!-- 历史记录时间轴 -->
      <div v-else class="timeline">
        <template v-for="(group, date) in groupedItems" :key="date">
          <div class="timeline-date">
            <span class="date-badge">{{ date }}</span>
          </div>

          <div
            v-for="(pair, idx) in group"
            :key="pair.ai.id"
            class="timeline-item"
          >
            <!-- 用户问 -->
            <div class="chat-row user-row">
              <div class="chat-bubble user-bubble">
                <div class="bubble-text">{{ pair.user?.content }}</div>
              </div>
              <div class="chat-meta">
                <span class="meta-role">你</span>
                <span class="meta-time">{{ formatTime(pair.user?.created_at) }}</span>
              </div>
            </div>

            <!-- AI 答 -->
            <div class="chat-row ai-row">
              <div class="ai-avatar-wrap">
                <img src="/irina.png" alt="伊蕾娜" class="ai-avatar" />
              </div>
              <div class="ai-content">
                <div class="chat-meta ai-meta">
                  <span class="meta-role ai-role">伊蕾娜小助手</span>
                  <span class="meta-time">{{ formatTime(pair.ai.created_at) }}</span>
                  <button class="delete-btn" @click="handleDelete(pair.ai.id, pair.user?.id)" title="删除此记录">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
                      <polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4h6v2"/>
                    </svg>
                  </button>
                </div>
                <div class="chat-bubble ai-bubble">
                  <div class="bubble-text">{{ pair.ai.content }}</div>
                  <!-- 推荐卡片 -->
                  <RecommendCard v-if="pair.ai.anime_card" :data="pair.ai.anime_card" class="history-card" />
                </div>
              </div>
            </div>

            <div v-if="idx < group.length - 1" class="item-divider"></div>
          </div>
        </template>

        <!-- 加载更多 -->
        <div v-if="hasMore" class="load-more">
          <button class="load-more-btn" :disabled="loading" @click="loadMore">
            {{ loading ? '加载中...' : '加载更多' }}
          </button>
        </div>
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import RecommendCard from '../components/RecommendCard.vue'
import { getChatHistory, deleteChatHistory } from '../api/index.js'
import { getUser } from '../utils/auth'

const router = useRouter()
const currentUser = ref(getUser())

const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const loading = ref(false)
const hasMore = computed(() => items.value.length < total.value)

async function fetchHistory(pg = 1) {
  loading.value = true
  try {
    const data = await getChatHistory(pg, pageSize)
    if (pg === 1) {
      items.value = data.items || []
    } else {
      items.value.push(...(data.items || []))
    }
    total.value = data.total || 0
    page.value = pg
  } catch (e) {
    console.error('获取历史失败:', e)
  } finally {
    loading.value = false
  }
}

function loadMore() {
  fetchHistory(page.value + 1)
}

// 将 user+ai 两条配对，按日期分组
const groupedItems = computed(() => {
  // 先配对：user 和 ai 消息按顺序两两配对
  const pairs = []
  const sorted = [...items.value].reverse() // 正序（最旧在前）
  let i = 0
  while (i < sorted.length) {
    const cur = sorted[i]
    if (cur.role === 'user') {
      const next = sorted[i + 1]
      if (next && next.role === 'ai') {
        pairs.push({ user: cur, ai: next })
        i += 2
      } else {
        pairs.push({ user: cur, ai: { id: cur.id + '_ai', content: '', created_at: cur.created_at, anime_card: null } })
        i++
      }
    } else {
      pairs.push({ user: null, ai: cur })
      i++
    }
  }

  // 按日期分组，最新日期在前
  const groups = {}
  for (const pair of pairs.reverse()) {
    const dt = pair.ai.created_at || pair.user?.created_at || ''
    const date = dt.split(' ')[0] || '未知日期'
    if (!groups[date]) groups[date] = []
    groups[date].push(pair)
  }
  return groups
})

async function handleDelete(aiId, userId) {
  if (!confirm('确认删除这条聊天记录？')) return
  try {
    // 删除 AI 条目
    await deleteChatHistory(aiId)
    // 如果有用户条目也删除
    if (userId && typeof userId === 'number') {
      await deleteChatHistory(userId).catch(() => {})
    }
    // 从列表中移除
    items.value = items.value.filter(it => it.id !== aiId && it.id !== userId)
    total.value = Math.max(0, total.value - 2)
  } catch (e) {
    alert(e.message || '删除失败')
  }
}

function formatTime(dt) {
  if (!dt) return ''
  const parts = dt.split(' ')
  return parts[1] || dt
}

onMounted(() => fetchHistory(1))
</script>

<style scoped>
.history-page {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  position: relative;
  background: var(--bg-deep);
  color: var(--text-primary);
}

.bg-grid {
  position: fixed;
  inset: 0;
  background-image:
    linear-gradient(rgba(0, 229, 255, 0.03) 1px, transparent 1px),
    linear-gradient(90deg, rgba(0, 229, 255, 0.03) 1px, transparent 1px);
  background-size: 60px 60px;
  pointer-events: none;
  z-index: 0;
}

.bg-glow {
  position: fixed;
  border-radius: 50%;
  filter: blur(80px);
  pointer-events: none;
  z-index: 0;
}
.bg-glow--1 {
  width: 400px; height: 400px;
  top: -100px; left: -100px;
  background: radial-gradient(circle, rgba(0, 229, 255, 0.06) 0%, transparent 70%);
}
.bg-glow--2 {
  width: 500px; height: 500px;
  bottom: -150px; right: -150px;
  background: radial-gradient(circle, rgba(255, 45, 155, 0.05) 0%, transparent 70%);
}

/* ===== Header ===== */
.history-header {
  position: relative;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 32px;
  border-bottom: 1px solid rgba(0, 229, 255, 0.08);
  background: rgba(5, 9, 18, 0.88);
  backdrop-filter: blur(24px);
  -webkit-backdrop-filter: blur(24px);
}

.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.back-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(255, 255, 255, 0.03);
  color: var(--text-secondary);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
}
.back-btn:hover {
  border-color: rgba(0, 229, 255, 0.3);
  background: rgba(0, 229, 255, 0.07);
  color: var(--neon-cyan);
}

.header-divider {
  width: 1px;
  height: 24px;
  background: rgba(0, 229, 255, 0.15);
}

.page-title {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 18px;
  font-weight: 700;
  color: #e8f4ff;
  margin: 0;
}

.title-avatar {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  object-fit: cover;
  object-position: top;
  border: 2px solid rgba(0, 229, 255, 0.35);
  box-shadow: 0 0 12px rgba(0, 229, 255, 0.25);
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.user-tag {
  padding: 4px 12px;
  border-radius: 20px;
  background: rgba(0, 229, 255, 0.08);
  border: 1px solid rgba(0, 229, 255, 0.2);
  font-size: 13px;
  color: rgba(0, 229, 255, 0.8);
  font-family: monospace;
}

.record-count {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.35);
  font-family: monospace;
}

/* ===== Main ===== */
.history-main {
  position: relative;
  z-index: 5;
  flex: 1;
  max-width: 860px;
  width: 100%;
  margin: 0 auto;
  padding: 32px 24px 60px;
  box-sizing: border-box;
}

/* ===== Loading ===== */
.loading-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  margin-top: 80px;
  color: rgba(0, 229, 255, 0.5);
  font-size: 13px;
  letter-spacing: 2px;
  font-family: monospace;
}

.spinner {
  width: 36px;
  height: 36px;
  border: 2px solid rgba(0, 229, 255, 0.15);
  border-top-color: rgba(0, 229, 255, 0.7);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* ===== Empty ===== */
.empty-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  margin-top: 80px;
}

.empty-avatar {
  width: 80px;
  height: 80px;
  border-radius: 50%;
  object-fit: cover;
  object-position: top;
  border: 2px solid rgba(0, 229, 255, 0.25);
  opacity: 0.7;
}

.empty-text {
  font-size: 18px;
  color: rgba(255, 255, 255, 0.5);
  margin: 8px 0 0;
}

.empty-sub {
  font-size: 14px;
  color: rgba(255, 255, 255, 0.3);
  margin: 0;
}

.go-chat-btn {
  margin-top: 12px;
  padding: 10px 28px;
  border-radius: 8px;
  border: 1px solid rgba(0, 229, 255, 0.35);
  background: rgba(0, 229, 255, 0.08);
  color: #00e5ff;
  font-size: 14px;
  cursor: pointer;
  transition: all 0.2s;
}
.go-chat-btn:hover {
  background: rgba(0, 229, 255, 0.16);
  box-shadow: 0 0 16px rgba(0, 229, 255, 0.2);
}

/* ===== Timeline ===== */
.timeline-date {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 24px 0 16px;
}
.timeline-date::before,
.timeline-date::after {
  content: '';
  flex: 1;
  height: 1px;
  background: rgba(0, 229, 255, 0.1);
}

.date-badge {
  padding: 4px 14px;
  border-radius: 20px;
  font-size: 12px;
  font-family: monospace;
  letter-spacing: 0.5px;
  color: rgba(0, 229, 255, 0.55);
  background: rgba(0, 229, 255, 0.06);
  border: 1px solid rgba(0, 229, 255, 0.12);
  white-space: nowrap;
}

.timeline-item {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 4px;
}

.item-divider {
  height: 1px;
  background: rgba(255, 255, 255, 0.04);
  margin: 12px 0 8px;
}

/* ===== Chat rows ===== */
.chat-row {
  display: flex;
  gap: 10px;
}

.user-row {
  flex-direction: row-reverse;
  align-items: flex-start;
}

.ai-row {
  align-items: flex-start;
}

.chat-meta {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 3px;
  padding-top: 2px;
  min-width: 48px;
}

.ai-meta {
  align-items: flex-start;
  flex-direction: row;
  gap: 8px;
  align-items: center;
  margin-bottom: 6px;
}

.meta-role {
  font-size: 11px;
  font-family: monospace;
  color: rgba(255, 255, 255, 0.35);
  letter-spacing: 0.5px;
}

.ai-role {
  color: rgba(0, 229, 255, 0.55);
}

.meta-time {
  font-size: 11px;
  font-family: monospace;
  color: rgba(255, 255, 255, 0.2);
}

.delete-btn {
  margin-left: auto;
  background: none;
  border: none;
  cursor: pointer;
  color: rgba(255, 100, 100, 0.3);
  padding: 2px;
  display: flex;
  align-items: center;
  transition: color 0.2s;
  border-radius: 4px;
}
.delete-btn:hover {
  color: rgba(255, 80, 80, 0.8);
  background: rgba(255, 80, 80, 0.08);
}

/* ===== Bubbles ===== */
.chat-bubble {
  max-width: 85%;
  padding: 12px 16px;
  border-radius: 12px;
  font-size: 14px;
  line-height: 1.7;
}

.user-bubble {
  background: rgba(0, 229, 255, 0.1);
  border: 1px solid rgba(0, 229, 255, 0.18);
  border-top: 1px solid rgba(0, 229, 255, 0.28);
  border-radius: 14px 4px 14px 14px;
  color: #d4f4ff;
}

.ai-content {
  display: flex;
  flex-direction: column;
  flex: 1;
}

.ai-bubble {
  background: rgba(15, 22, 35, 0.7);
  border: 1px solid rgba(255, 255, 255, 0.07);
  border-left: 2px solid rgba(0, 229, 255, 0.2);
  border-radius: 4px 14px 14px 14px;
  color: var(--text-primary);
}

.bubble-text {
  white-space: pre-wrap;
  word-break: break-word;
}

.ai-avatar-wrap {
  flex-shrink: 0;
  padding-top: 28px;
}

.ai-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  object-fit: cover;
  object-position: top;
  border: 2px solid rgba(0, 229, 255, 0.3);
  box-shadow: 0 0 10px rgba(0, 229, 255, 0.2);
}

.history-card {
  margin-top: 10px;
  opacity: 0.9;
}

/* ===== Load more ===== */
.load-more {
  display: flex;
  justify-content: center;
  margin-top: 28px;
}

.load-more-btn {
  padding: 10px 32px;
  border-radius: 8px;
  border: 1px solid rgba(0, 229, 255, 0.25);
  background: rgba(0, 229, 255, 0.06);
  color: rgba(0, 229, 255, 0.7);
  font-size: 14px;
  cursor: pointer;
  font-family: monospace;
  letter-spacing: 0.5px;
  transition: all 0.2s;
}
.load-more-btn:not(:disabled):hover {
  background: rgba(0, 229, 255, 0.12);
  border-color: rgba(0, 229, 255, 0.5);
}
.load-more-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
