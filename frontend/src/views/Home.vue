<template>
  <div class="home noise-overlay">
    <!-- Animated background -->
    <div class="bg-image"></div>
    <div class="bg-glow bg-glow--1"></div>
    <div class="bg-glow bg-glow--2"></div>

    <!-- Header -->
    <header class="home-header">
      <div class="header-top-bar">
        <div class="header-brand">
          <span class="brand-dot"></span>
          <span class="brand-text">ANIME · SENTINEL</span>
        </div>
        <!-- 用户状态栏 -->
        <div class="user-bar">
          <span class="user-bar-name">{{ currentUser?.username }}</span>
          <button class="user-bar-btn" @click="router.push('/history')">历史记录</button>
          <button class="user-bar-btn" @click="router.push('/agent')">智能体中心</button>
          <button class="user-bar-btn user-bar-logout" @click="handleLogout">退出登录</button>
        </div>
      </div>
      <div class="header-content">
        <div class="terminal-tag">欢迎进入系统，可以多和伊蕾娜小助手互动哦~</div>
        <h1 class="title">
          <span class="title-eyebrow">动漫评论</span>
          <span class="title-main">情感分析与舆情监控系统</span>
        </h1>
        <p class="subtitle">ANIME COMMENT SENTIMENT ANALYSIS & PUBLIC OPINION MONITORING</p>
        <div class="header-line"></div>
      </div>
    </header>

    <!-- Search Area -->
    <section class="search-section">
      <div class="search-row">
        <div class="search-box" @click.stop>
        <div class="search-border"></div>
        <svg class="search-icon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
        </svg>
        <input
          v-model="searchText"
          type="text"
          class="search-input"
          placeholder="输入动漫名称搜索..."
          @focus="showDropdown = true"
        />
        <div class="search-shortcut">⌘K</div>
        <transition name="fade">
          <ul v-if="showDropdown && filteredAnimeList.length > 0" class="dropdown">
            <li
              v-for="anime in filteredAnimeList"
              :key="anime.id"
              class="dropdown-item"
              @mousedown.prevent="goToDashboard(anime)"
            >
              <span class="dropdown-name">{{ anime.name }}</span>
              <span class="dropdown-meta">
                <span class="dropdown-count">{{ anime.comment_count }}</span>
                <span class="dropdown-label">comments</span>
              </span>
            </li>
          </ul>
        </transition>
        </div>
        <RecommendChat />
      </div>
    </section>

    <!-- Stats Bar -->
    <section class="stats-bar" v-if="!loading">
      <div class="stat-item">
        <span class="stat-number">{{ animeList.length }}</span>
        <span class="stat-label">ANIME</span>
      </div>
      <div class="stat-divider">
        <span class="stat-dot"></span>
      </div>
      <div class="stat-item">
        <span class="stat-number">{{ totalComments.toLocaleString() }}</span>
        <span class="stat-label">COMMENTS</span>
      </div>
      <div class="stat-divider">
        <span class="stat-dot"></span>
      </div>
      <div class="stat-item">
        <span class="stat-number stat-number--live">LIVE</span>
        <span class="stat-label">STATUS</span>
      </div>
    </section>

    <!-- Anime Cards -->
    <section class="cards-section">
      <div v-if="loading" class="loading-container">
        <div class="spinner"></div>
        <span class="loading-text">LOADING DATA...</span>
      </div>
      <div v-else class="cards-grid">
        <div
          v-for="(anime, index) in filteredAnimeList"
          :key="anime.id"
          class="anime-card stagger-item"
          :style="{ animationDelay: (index * 0.06) + 's' }"
          @click="goToDashboard(anime)"
        >
          <div class="card-glow"></div>
          <div class="card-inner">
            <div class="card-header">
              <span class="card-index">#{{ String(index + 1).padStart(2, '0') }}</span>
              <span class="card-platform">{{ anime.platform }}</span>
            </div>
            <h3 class="card-title">{{ anime.name }}</h3>
            <div class="card-stats">
              <div class="card-stat-row">
                <span class="card-stat-value">{{ anime.comment_count }}</span>
                <span class="card-stat-unit">条评论</span>
              </div>
              <div class="card-bar">
                <div class="card-bar-fill" :style="{ width: barWidth(anime.comment_count) + '%' }"></div>
              </div>
            </div>
            <div class="card-footer">
              <span class="card-action">
                ANALYZE
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5">
                  <path d="m9 18 6-6-6-6"/>
                </svg>
              </span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- Footer -->
    <footer class="home-footer">
      <div class="footer-line"></div>
      <p>动漫评论情感分析与舆情监控系统 &copy; 2026 — ALL SYSTEMS OPERATIONAL</p>
    </footer>

    <!-- 自定义拖拽滚动条 -->
    <div class="custom-scrollbar">
      <div class="scrollbar-track" ref="trackRef" @mousedown="onTrackClick">
        <div
          class="scrollbar-thumb"
          ref="thumbRef"
          :style="{ transform: 'translateY(' + thumbTop + 'px)', height: thumbHeight + 'px' }"
          @mousedown.stop="onThumbDragStart"
        ></div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { getAnimeList } from '../api'
import RecommendChat from '../components/RecommendChat.vue'
import { getUser, logout } from '../utils/auth'

const router = useRouter()
const animeList = ref([])
const searchText = ref('')
const showDropdown = ref(false)
const loading = ref(true)
const currentUser = ref(getUser())

function handleLogout() {
  logout()
  router.push({ name: 'Login' })
}

const filteredAnimeList = computed(() => {
  if (!searchText.value.trim()) return animeList.value
  const keyword = searchText.value.trim().toLowerCase()
  return animeList.value.filter(a => a.name.toLowerCase().includes(keyword))
})

const totalComments = computed(() => {
  return animeList.value.reduce((sum, a) => sum + (a.comment_count || 0), 0)
})

const maxComments = computed(() => {
  return Math.max(...animeList.value.map(a => a.comment_count || 0), 1)
})

function barWidth(count) {
  return Math.max(8, (count / maxComments.value) * 100)
}

function goToDashboard(anime) {
  showDropdown.value = false
  router.push({ name: 'Dashboard', params: { animeId: anime.id } })
}

onMounted(async () => {
  try {
    const data = await getAnimeList()
    animeList.value = data || []
  } catch (e) {
    console.error('获取动漫列表失败:', e)
  } finally {
    loading.value = false
  }
})

document.addEventListener('click', () => {
  showDropdown.value = false
})

// ===== 自定义滚动条 =====
const trackRef = ref(null)
const thumbRef = ref(null)
const thumbTop = ref(0)
const thumbHeight = ref(40)

function updateThumb() {
  const track = trackRef.value
  if (!track) return
  const trackH = track.clientHeight
  const scrollTop = window.scrollY
  const scrollHeight = document.documentElement.scrollHeight
  const clientHeight = window.innerHeight
  const scrollable = scrollHeight - clientHeight
  if (scrollable <= 0) {
    thumbHeight.value = trackH
    thumbTop.value = 0
    return
  }
  const ratio = clientHeight / scrollHeight
  const th = Math.max(36, trackH * ratio)
  thumbHeight.value = th
  thumbTop.value = (scrollTop / scrollable) * (trackH - th)
}

window.addEventListener('scroll', updateThumb, { passive: true })
window.addEventListener('resize', updateThumb, { passive: true })

let dragOffsetY = 0  // 鼠标按下时相对于 thumb 顶部的偏移

function onThumbDragStart(e) {
  e.preventDefault()
  const thumb = thumbRef.value
  if (thumb) {
    const rect = thumb.getBoundingClientRect()
    dragOffsetY = e.clientY - rect.top  // 记录鼠标在 thumb 内的偏移
  } else {
    dragOffsetY = thumbHeight.value / 2
  }
  document.addEventListener('mousemove', onThumbDrag)
  document.addEventListener('mouseup', onThumbDragEnd)
}

function onThumbDrag(e) {
  const track = trackRef.value
  if (!track) return
  const rect = track.getBoundingClientRect()
  const trackH = track.clientHeight
  const scrollHeight = document.documentElement.scrollHeight
  const clientHeight = window.innerHeight
  const scrollable = scrollHeight - clientHeight
  // thumb 顶部距离轨道顶部的距离 = 鼠标位置 - 轨道顶部 - 鼠标在 thumb 内的偏移
  const newTop = e.clientY - rect.top - dragOffsetY
  const maxTop = trackH - thumbHeight.value
  const ratio = Math.max(0, Math.min(1, newTop / maxTop))
  window.scrollTo(0, ratio * scrollable)
}

function onThumbDragEnd() {
  document.removeEventListener('mousemove', onThumbDrag)
  document.removeEventListener('mouseup', onThumbDragEnd)
}

function onTrackClick(e) {
  if (e.target === thumbRef.value) return
  const track = trackRef.value
  if (!track) return
  const rect = track.getBoundingClientRect()
  const clickY = e.clientY - rect.top
  const trackH = track.clientHeight
  const scrollHeight = document.documentElement.scrollHeight
  const clientHeight = window.innerHeight
  const scrollable = scrollHeight - clientHeight
  const targetScroll = ((clickY - thumbHeight.value / 2) / (trackH - thumbHeight.value)) * scrollable
  window.scrollTo({ top: Math.max(0, Math.min(scrollable, targetScroll)), behavior: 'smooth' })
}

onMounted(() => {
  setTimeout(updateThumb, 100)
})

onUnmounted(() => {
  window.removeEventListener('scroll', updateThumb)
  window.removeEventListener('resize', updateThumb)
})
</script>

<style scoped>
.home {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  position: relative;
}

/* ---- Animated Background ---- */
.bg-image {
  position: fixed;
  top: 0; left: 0; right: 0; bottom: 0;
  background-image: url('@/assets/home-bg.png');
  background-size: cover;
  background-position: center;
  background-repeat: no-repeat;
  filter: brightness(0.42);
  pointer-events: none;
  z-index: 0;
}

.bg-glow {
  position: fixed;
  border-radius: 50%;
  filter: blur(140px);
  pointer-events: none;
  z-index: 1;
}

.bg-glow--1 {
  width: 700px; height: 700px;
  background: rgba(0, 229, 255, 0.055);
  top: -220px; left: -120px;
  animation: driftA 18s ease-in-out infinite alternate;
}

.bg-glow--2 {
  width: 550px; height: 550px;
  background: rgba(255, 77, 141, 0.045);
  bottom: -220px; right: -120px;
  animation: driftB 22s ease-in-out infinite alternate;
}

@keyframes driftA {
  to { transform: translate(100px, 80px); }
}
@keyframes driftB {
  to { transform: translate(-80px, -100px); }
}

/* ---- Header ---- */
.home-header {
  position: relative;
  z-index: 2;
  padding: 0 0 36px;
  text-align: center;
}

/* 顶部导航条 */
.header-top-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 28px;
  background: rgba(7, 11, 22, 0.6);
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
}

.header-brand {
  display: flex;
  align-items: center;
  gap: 8px;
}

.brand-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--neon-cyan);
  box-shadow: 0 0 10px rgba(0, 229, 255, 0.7), 0 0 20px rgba(0, 229, 255, 0.3);
  animation: pulseDot 2.5s ease-in-out infinite;
}

@keyframes pulseDot {
  0%, 100% { opacity: 1; box-shadow: 0 0 10px rgba(0,229,255,0.7), 0 0 20px rgba(0,229,255,0.3); }
  50% { opacity: 0.65; box-shadow: 0 0 5px rgba(0,229,255,0.4); }
}

.brand-text {
  font-family: var(--font-display);
  font-size: 11px;
  letter-spacing: 3px;
  color: rgba(0, 229, 255, 0.7);
  text-transform: uppercase;
}

.header-content {
  padding: 44px 24px 0;
}

.terminal-tag {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 1.5px;
  color: rgba(0, 229, 255, 0.75);
  background: rgba(0, 229, 255, 0.06);
  border: 1px solid rgba(0, 229, 255, 0.18);
  padding: 5px 18px;
  border-radius: 20px;
  margin-bottom: 28px;
}

.terminal-tag::before {
  content: '>';
  font-size: 10px;
  color: var(--neon-cyan);
  opacity: 0.6;
}

.title {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
}

.title-eyebrow {
  font-family: var(--font-display);
  font-size: 11px;
  font-weight: 400;
  letter-spacing: 8px;
  color: var(--neon-cyan);
  text-transform: uppercase;
  opacity: 0.8;
}

.title-main {
  font-size: 34px;
  font-weight: 800;
  color: var(--text-primary);
  letter-spacing: 3px;
  line-height: 1.25;
  text-shadow: 0 2px 20px rgba(0, 0, 0, 0.5);
}

.subtitle {
  font-family: var(--font-display);
  font-size: 9.5px;
  letter-spacing: 3.5px;
  color: var(--text-muted);
  margin-top: 14px;
  opacity: 0.75;
}

.header-line {
  width: 80px;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--neon-cyan), rgba(255,77,141,0.6), transparent);
  margin: 32px auto 0;
}

/* ---- Search ---- */
.search-section {
  display: flex;
  justify-content: center;
  padding: 0 24px;
  position: relative;
  z-index: 10;
}

.search-row {
  display: flex;
  align-items: center;
  gap: 14px;
  width: 100%;
  max-width: 660px;
}

.search-box {
  position: relative;
  flex: 1;
}

.search-border {
  position: absolute;
  inset: 0;
  border-radius: var(--radius-md);
  border: 1px solid rgba(255, 255, 255, 0.07);
  pointer-events: none;
  transition: var(--transition);
}

.search-box:focus-within .search-border {
  border-color: rgba(0, 229, 255, 0.35);
  box-shadow: 0 0 0 3px rgba(0, 229, 255, 0.06), 0 0 24px rgba(0, 229, 255, 0.1);
}

.search-icon {
  position: absolute;
  left: 18px;
  top: 50%;
  transform: translateY(-50%);
  z-index: 1;
  color: var(--text-muted);
  transition: var(--transition);
}

.search-box:focus-within .search-icon {
  color: var(--neon-cyan);
}

.search-input {
  width: 100%;
  height: 52px;
  padding: 0 76px 0 50px;
  border-radius: var(--radius-md);
  background: rgba(15, 22, 35, 0.6);
  backdrop-filter: blur(18px);
  -webkit-backdrop-filter: blur(18px);
  font-size: 14px;
  color: var(--text-primary);
  transition: var(--transition);
}

.search-input::placeholder {
  color: var(--text-muted);
  font-size: 13px;
}

.search-shortcut {
  position: absolute;
  right: 14px;
  top: 50%;
  transform: translateY(-50%);
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-muted);
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.07);
  padding: 3px 9px;
  border-radius: 5px;
}

.dropdown {
  position: absolute;
  top: 60px;
  left: 0;
  right: 0;
  background: rgba(10, 16, 28, 0.92);
  backdrop-filter: blur(24px);
  -webkit-backdrop-filter: blur(24px);
  border: 1px solid rgba(0, 229, 255, 0.12);
  border-radius: var(--radius-md);
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.6), 0 0 0 1px rgba(255,255,255,0.03);
  max-height: 340px;
  overflow-y: auto;
  z-index: 100;
}

.dropdown-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 13px 20px;
  cursor: pointer;
  transition: var(--transition-fast);
  border-bottom: 1px solid rgba(255, 255, 255, 0.03);
}

.dropdown-item:last-child {
  border-bottom: none;
}

.dropdown-item:hover {
  background: rgba(0, 229, 255, 0.07);
  padding-left: 24px;
}

.dropdown-name {
  font-size: 14px;
  color: var(--text-primary);
  font-weight: 500;
}

.dropdown-meta {
  display: flex;
  align-items: baseline;
  gap: 5px;
}

.dropdown-count {
  font-family: var(--font-mono);
  font-size: 13px;
  color: var(--neon-cyan);
  font-weight: 600;
}

.dropdown-label {
  font-size: 10px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

/* ---- Stats Bar ---- */
.stats-bar {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 36px 24px 24px;
  position: relative;
  z-index: 1;
}

.stat-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 5px;
  padding: 12px 28px;
  background: rgba(15, 22, 35, 0.55);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-top: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: var(--radius-sm);
  transition: var(--transition);
}

.stat-item:hover {
  border-color: rgba(0, 229, 255, 0.15);
  background: rgba(0, 229, 255, 0.04);
}

.stat-number {
  font-family: var(--font-display);
  font-size: 26px;
  font-weight: 700;
  color: var(--text-primary);
  letter-spacing: 1px;
  line-height: 1;
}

.stat-number--live {
  color: var(--color-positive);
  font-size: 13px;
  letter-spacing: 2px;
  animation: pulse 2.5s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

.stat-label {
  font-family: var(--font-display);
  font-size: 9px;
  color: var(--text-muted);
  letter-spacing: 2.5px;
  text-transform: uppercase;
}

.stat-divider {
  display: flex;
  align-items: center;
  opacity: 0.4;
}

.stat-dot {
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: var(--text-muted);
}

/* ---- Cards ---- */
.cards-section {
  flex: 1;
  padding: 20px 28px 56px;
  max-width: 1340px;
  margin: 0 auto;
  width: 100%;
  position: relative;
  z-index: 1;
}

.cards-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 16px;
}

.anime-card {
  position: relative;
  cursor: pointer;
  border-radius: var(--radius-md);
  transition: var(--transition-spring);
}

.anime-card:hover {
  transform: translateY(-5px);
}

.anime-card:hover .card-glow {
  opacity: 1;
}

.card-glow {
  position: absolute;
  inset: -1px;
  border-radius: var(--radius-md);
  background: linear-gradient(135deg, rgba(0, 229, 255, 0.18), rgba(255, 77, 141, 0.12));
  opacity: 0;
  transition: opacity 0.5s ease;
  z-index: 0;
  filter: blur(2px);
}

.card-inner {
  position: relative;
  z-index: 1;
  background: rgba(10, 16, 28, 0.55);
  backdrop-filter: blur(18px);
  -webkit-backdrop-filter: blur(18px);
  border: 1px solid rgba(255, 255, 255, 0.07);
  border-top: 1px solid rgba(255, 255, 255, 0.11);
  border-radius: var(--radius-md);
  padding: 22px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  height: 100%;
  transition: border-color 0.3s ease;
}

.anime-card:hover .card-inner {
  border-color: rgba(0, 229, 255, 0.2);
  border-top-color: rgba(0, 229, 255, 0.28);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.card-index {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-muted);
  letter-spacing: 1px;
}

.card-platform {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 1.5px;
  text-transform: uppercase;
  color: var(--neon-cyan);
  background: rgba(0, 229, 255, 0.07);
  border: 1px solid rgba(0, 229, 255, 0.18);
  padding: 3px 10px;
  border-radius: 4px;
}

.card-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
  line-height: 1.5;
  flex: 1;
  transition: color 0.2s;
}

.anime-card:hover .card-title {
  color: #fff;
}

.card-stats {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.card-stat-row {
  display: flex;
  align-items: baseline;
  gap: 6px;
}

.card-stat-value {
  font-family: var(--font-display);
  font-size: 22px;
  font-weight: 700;
  color: var(--text-primary);
}

.card-stat-unit {
  font-size: 12px;
  color: var(--text-secondary);
}

.card-bar {
  width: 100%;
  height: 4px;
  background: rgba(255, 255, 255, 0.05);
  border-radius: 2px;
  overflow: hidden;
}

.card-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--neon-cyan), var(--neon-pink));
  border-radius: 2px;
  box-shadow: 0 0 8px rgba(0, 229, 255, 0.4);
  transition: width 0.9s cubic-bezier(0.16, 1, 0.3, 1);
}

.card-footer {
  padding-top: 12px;
  border-top: 1px solid rgba(255, 255, 255, 0.05);
}

.card-action {
  font-family: var(--font-display);
  font-size: 10px;
  letter-spacing: 3px;
  color: var(--neon-cyan-dim);
  display: flex;
  align-items: center;
  gap: 6px;
  transition: var(--transition);
}

.anime-card:hover .card-action {
  color: var(--neon-cyan);
  gap: 10px;
}

/* ---- Footer ---- */
.home-footer {
  text-align: center;
  padding: 20px 24px 36px;
  position: relative;
  z-index: 1;
}

.footer-line {
  width: 60px;
  height: 1px;
  background: linear-gradient(90deg, transparent, rgba(0,229,255,0.3), transparent);
  margin: 0 auto 16px;
}

.home-footer p {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--text-muted);
  letter-spacing: 1.5px;
  opacity: 0.6;
}

/* ---- Custom Scrollbar ---- */
.custom-scrollbar {
  position: fixed;
  top: 0;
  right: 6px;
  width: 14px;
  height: 100vh;
  z-index: 200;
  display: flex;
  align-items: stretch;
  padding: 8px 0;
  box-sizing: border-box;
  pointer-events: none;
}

.scrollbar-track {
  position: relative;
  flex: 1;
  border-radius: 8px;
  background: rgba(0, 229, 255, 0.06);
  border: 1px solid rgba(0, 229, 255, 0.1);
  cursor: pointer;
  pointer-events: all;
  transition: background 0.2s, border-color 0.2s;
}

.scrollbar-track:hover {
  background: rgba(0, 229, 255, 0.1);
  border-color: rgba(0, 229, 255, 0.25);
}

.scrollbar-thumb {
  position: absolute;
  top: 0;
  left: 2px;
  right: 2px;
  min-height: 36px;
  will-change: transform;
  border-radius: 6px;
  background: linear-gradient(
    180deg,
    rgba(0, 229, 255, 0.55) 0%,
    rgba(0, 180, 220, 0.4) 100%
  );
  border: 1px solid rgba(0, 229, 255, 0.4);
  box-shadow: 0 0 8px rgba(0, 229, 255, 0.25), inset 0 1px 0 rgba(255,255,255,0.1);
  cursor: grab;
  transition: background 0.15s, box-shadow 0.15s;
  user-select: none;
}

.scrollbar-thumb:hover,
.scrollbar-thumb:active {
  background: linear-gradient(
    180deg,
    rgba(0, 229, 255, 0.8) 0%,
    rgba(0, 200, 240, 0.65) 100%
  );
  box-shadow: 0 0 14px rgba(0, 229, 255, 0.5), inset 0 1px 0 rgba(255,255,255,0.15);
  cursor: grabbing;
}

/* ---- User Bar ---- */
.user-bar {
  display: flex;
  align-items: center;
  gap: 8px;
}

.user-bar-name {
  font-size: 12px;
  font-family: var(--font-mono);
  color: rgba(0, 229, 255, 0.65);
  letter-spacing: 0.5px;
  padding: 5px 12px;
  background: rgba(0, 229, 255, 0.05);
  border: 1px solid rgba(0, 229, 255, 0.12);
  border-radius: 20px;
}

.user-bar-btn {
  padding: 6px 14px;
  border-radius: 6px;
  border: 1px solid rgba(0, 229, 255, 0.2);
  background: rgba(0, 229, 255, 0.05);
  color: rgba(0, 229, 255, 0.7);
  font-size: 12px;
  cursor: pointer;
  transition: var(--transition-fast);
  font-family: inherit;
  letter-spacing: 0.5px;
}
.user-bar-btn:hover {
  background: rgba(0, 229, 255, 0.12);
  border-color: rgba(0, 229, 255, 0.45);
  color: var(--neon-cyan);
}

.user-bar-logout {
  border-color: rgba(255, 80, 80, 0.2);
  background: rgba(255, 80, 80, 0.05);
  color: rgba(255, 120, 120, 0.7);
}
.user-bar-logout:hover {
  background: rgba(255, 80, 80, 0.12);
  border-color: rgba(255, 80, 80, 0.45);
  color: #ff8080;
}
</style>

