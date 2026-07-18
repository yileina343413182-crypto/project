<template>
  <div class="dashboard noise-overlay">
    <!-- Background -->
    <div class="bg-grid"></div>
    <div class="bg-orb bg-orb--1"></div>
    <div class="bg-orb bg-orb--2"></div>

    <!-- Top Nav -->
    <nav class="dash-nav glass">
      <router-link to="/" class="back-btn">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.5">
          <path d="m15 18-6-6 6-6"/>
        </svg>
        <span>返回</span>
      </router-link>
      <div class="nav-center">
        <div class="nav-indicator"></div>
        <h2 class="dash-title">{{ animeName }}</h2>
      </div>
      <router-link class="agent-nav-btn" :to="{ name: 'OpinionAgent', query: { animeId } }">舆情诊断Agent</router-link>
      <div class="nav-status">
        <span class="status-dot"></span>
        <span class="status-text">LIVE</span>
      </div>
    </nav>

    <!-- Loading -->
    <div v-if="loading" class="loading-container" style="min-height: 60vh">
      <div class="spinner"></div>
      <span class="loading-text">INITIALIZING ANALYSIS...</span>
    </div>

    <!-- Main Content -->
    <div v-else class="dash-content">
      <!-- Top Row: Pie + Trend -->
      <div class="dash-row top-row">
        <div class="dash-card card-pie stagger-item" style="animation-delay: 0.05s">
          <div class="card-title-bar">
            <div class="card-tag">01</div>
            <h3>情感分布</h3>
          </div>
          <SentimentPie :stats="sentimentStats" />
        </div>
        <div class="dash-card card-trend stagger-item" style="animation-delay: 0.12s">
          <div class="card-title-bar">
            <div class="card-tag">02</div>
            <h3>情感趋势</h3>
          </div>
          <SentimentTrend :scatter="sentimentScatter" />
        </div>
      </div>

      <!-- Bottom Row: WordCloud + Topics -->
      <div class="dash-row bottom-row">
        <div class="dash-card card-cloud stagger-item" style="animation-delay: 0.2s">
          <div class="card-title-bar">
            <div class="card-tag">03</div>
            <h3>评论词云</h3>
          </div>
          <WordCloud :words="wordCloudData" @word-click="onWordClick" />
        </div>
        <div class="dash-card card-topics stagger-item" style="animation-delay: 0.28s">
          <div class="card-title-bar">
            <div class="card-tag">04</div>
            <h3>主题分析 <span class="card-badge">LDA</span></h3>
          </div>
          <TopicCards :topics="topicsData" />
        </div>
      </div>

      <!-- Comments -->
      <div class="dash-card card-comments stagger-item" style="animation-delay: 0.35s">
        <div class="card-title-bar">
          <div class="card-tag">05</div>
          <h3>评论列表</h3>
        </div>
        <CommentList :anime-id="animeId" :filter-keyword="filterKeyword" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { getAnimeList, getSentimentStats, getSentimentScatter, getWordCloud, getTopics } from '../api'
import SentimentPie from '../components/SentimentPie.vue'
import SentimentTrend from '../components/SentimentTrend.vue'
import WordCloud from '../components/WordCloud.vue'
import TopicCards from '../components/TopicCards.vue'
import CommentList from '../components/CommentList.vue'

const props = defineProps({
  animeId: { type: [String, Number], required: true }
})

const route = useRoute()
const animeName = ref('')
const loading = ref(true)
const sentimentStats = ref({})
const sentimentScatter = ref([])
const wordCloudData = ref([])
const topicsData = ref([])
const filterKeyword = ref('')

function onWordClick(word) {
  filterKeyword.value = word
}

onMounted(async () => {
  try {
    const [animeListData, stats, scatter, words, topics] = await Promise.all([
      getAnimeList(),
      getSentimentStats(props.animeId),
      getSentimentScatter(props.animeId),
      getWordCloud(props.animeId),
      getTopics(props.animeId)
    ])

    const anime = animeListData.find(a => String(a.id) === String(props.animeId))
    animeName.value = anime ? anime.name : `动漫 #${props.animeId}`

    sentimentStats.value = stats || {}
    sentimentScatter.value = scatter || []
    wordCloudData.value = words || []
    topicsData.value = topics || []
  } catch (e) {
    console.error('加载Dashboard数据失败:', e)
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.dashboard {
  min-height: 100vh;
  position: relative;
}

/* ---- Background ---- */
.bg-grid {
  position: fixed;
  top: 0; left: 0; right: 0; bottom: 0;
  background-image:
    linear-gradient(rgba(0, 229, 255, 0.02) 1px, transparent 1px),
    linear-gradient(90deg, rgba(0, 229, 255, 0.02) 1px, transparent 1px);
  background-size: 48px 48px;
  pointer-events: none;
  z-index: 0;
}

.bg-orb {
  position: fixed;
  border-radius: 50%;
  filter: blur(100px);
  pointer-events: none;
  z-index: 0;
}

.bg-orb--1 {
  width: 400px; height: 400px;
  background: rgba(0, 229, 255, 0.04);
  top: 10%; right: -100px;
}

.bg-orb--2 {
  width: 350px; height: 350px;
  background: rgba(255, 77, 141, 0.03);
  bottom: 10%; left: -80px;
}

/* ---- Nav ---- */
.dash-nav {
  display: flex;
  align-items: center;
  padding: 12px 24px;
  position: sticky;
  top: 0;
  z-index: 50;
  background: rgba(7, 11, 22, 0.88) !important;
  border-bottom: 1px solid rgba(0, 229, 255, 0.08);
  backdrop-filter: blur(24px) !important;
  -webkit-backdrop-filter: blur(24px) !important;
}

.back-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--text-secondary);
  transition: var(--transition);
  padding: 6px 14px;
  border-radius: 8px;
  border: 1px solid transparent;
}

.back-btn:hover {
  color: var(--neon-cyan);
  background: rgba(0, 229, 255, 0.07);
  border-color: rgba(0, 229, 255, 0.15);
}

.nav-center {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
}

.nav-indicator {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--neon-cyan);
  box-shadow: 0 0 8px rgba(0, 229, 255, 0.5);
}

.dash-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--text-primary);
  letter-spacing: 1px;
  font-family: var(--font-display);
}

.agent-nav-btn {
  color: var(--neon-cyan);
  border: 1px solid rgba(0, 229, 255, 0.18);
  background: rgba(0, 229, 255, 0.06);
  border-radius: 7px;
  padding: 6px 12px;
  font-size: 12px;
  white-space: nowrap;
}

.agent-nav-btn:hover {
  border-color: rgba(0, 229, 255, 0.4);
  background: rgba(0, 229, 255, 0.12);
}

.nav-status {
  display: flex;
  align-items: center;
  gap: 6px;
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-positive);
  animation: pulse 2s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; box-shadow: 0 0 4px rgba(0, 229, 160, 0.5); }
  50% { opacity: 0.4; box-shadow: none; }
}

.status-text {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 2px;
  color: var(--color-positive);
}

/* ---- Content ---- */
.dash-content {
  max-width: 1400px;
  margin: 0 auto;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 20px;
  position: relative;
  z-index: 1;
}

.dash-row {
  display: grid;
  gap: 20px;
}

.top-row {
  grid-template-columns: 1fr 1.6fr;
}

.bottom-row {
  grid-template-columns: 1.3fr 1fr;
}

.dash-card {
  background: rgba(10, 16, 28, 0.6);
  backdrop-filter: blur(18px);
  -webkit-backdrop-filter: blur(18px);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-top: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: var(--radius-md);
  overflow: hidden;
  transition: var(--transition);
}

.dash-card:hover {
  border-color: rgba(0, 229, 255, 0.12);
  border-top-color: rgba(0, 229, 255, 0.2);
  box-shadow: 0 0 40px rgba(0, 229, 255, 0.04), 0 8px 32px rgba(0, 0, 0, 0.2);
}

.card-title-bar {
  padding: 18px 22px 14px;
  display: flex;
  align-items: center;
  gap: 12px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.04);
  margin-bottom: 2px;
}

.card-tag {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--neon-cyan);
  background: rgba(0, 229, 255, 0.08);
  border: 1px solid rgba(0, 229, 255, 0.15);
  padding: 2px 8px;
  border-radius: 4px;
  letter-spacing: 1px;
}

.card-title-bar h3 {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  display: flex;
  align-items: center;
  gap: 8px;
}

.card-badge {
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 1px;
  color: var(--neon-pink);
  background: rgba(255, 77, 141, 0.08);
  border: 1px solid rgba(255, 77, 141, 0.15);
  padding: 2px 8px;
  border-radius: 4px;
}

/* Responsive */
@media (max-width: 900px) {
  .top-row,
  .bottom-row {
    grid-template-columns: 1fr;
  }
}
</style>

