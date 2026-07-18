<template>
  <div class="recommend-wrapper">
    <!-- 头像触发按钮（内嵌在搜索栏旁） -->
    <button
      ref="btnRef"
      class="avatar-btn"
      :class="{ active: isOpen }"
      @click="togglePanel"
      title="伊蕾娜小助手"
    >
      <img :src="avatarSrc" alt="伊蕾娜" class="avatar-img" />
      <span class="avatar-label">伊蕾娜小助手</span>
      <span v-if="isOpen" class="close-dot">×</span>
    </button>

    <!-- 聊天面板（fixed，动态定位到按钮下方） -->
    <teleport to="body">
      <transition name="chat-slide">
        <div
          v-if="isOpen"
          class="chat-panel"
          :style="panelStyle"
        >
          <!-- 顶部标题栏 -->
          <div class="panel-header">
            <img :src="avatarSrc" alt="伊蕾娜" class="header-avatar" />
            <div class="header-info">
              <span class="header-title">世界第一美丽天才的灰之魔女————伊蕾娜</span>
              <span class="header-hint">告诉我你想看什么风格或动漫名称 ✨</span>
            </div>
            <button class="header-close" @click="togglePanel">×</button>
          </div>

          <!-- 消息区域 -->
          <div class="messages" ref="msgListRef">
            <!-- 欢迎消息 -->
            <div class="bubble ai-bubble">
              <div class="bubble-content">
                （托着下巴，慵懒地瞥了你一眼） 🌟<br>
                (￣▽￣)╭ 啊～今天的风好温柔呢～（瞥你一眼）你还在啊？(｀∀´) 嘛，想聊就聊咯，反正我这么可爱又聪明的魔女，跟谁说话都是对方的荣幸～
              </div>
            </div>

            <template v-for="(msg, idx) in messages" :key="idx">
              <div v-if="msg.role === 'user'" class="bubble user-bubble">
                <div class="bubble-content">{{ msg.content }}</div>
              </div>
              <div v-else class="bubble ai-bubble">
                <div class="bubble-content">{{ msg.reply }}</div>
                <RecommendCard v-if="msg.card" :data="msg.card" />
              </div>
            </template>

            <div v-if="loading" class="bubble ai-bubble">
              <div class="bubble-content loading-dots">
                <span /><span /><span />
              </div>
            </div>
          </div>

          <!-- 输入区域 -->
          <div class="input-area">
            <input
              v-model="inputText"
              class="chat-input"
              placeholder="输入动漫名或想看的类型…"
              maxlength="100"
              :disabled="loading"
              @keydown.enter.prevent="send"
            />
            <button class="send-btn" :disabled="loading || !inputText.trim()" @click="send">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="22" y1="2" x2="11" y2="13"/>
                <polygon points="22 2 15 22 11 13 2 9 22 2"/>
              </svg>
            </button>
          </div>
          <!-- 查看历史链接 -->
          <div class="history-footer" v-if="messages.length > 0">
            <button class="history-link" @click="router.push('/history')">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="13" height="13">
                <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
              </svg>
              查看完整历史记录
            </button>
          </div>
        </div>
      </transition>
    </teleport>
  </div>
</template>

<script setup>
import { ref, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import RecommendCard from './RecommendCard.vue'
import { getRecommendation, saveChatHistory } from '../api/index.js'
import { isLoggedIn } from '../utils/auth'

const router = useRouter()
const avatarSrc = '/irina.png'

const isOpen = ref(false)
const inputText = ref('')
const loading = ref(false)
const messages = ref([])
const msgListRef = ref(null)
const btnRef = ref(null)
const panelStyle = ref({})

function togglePanel() {
  isOpen.value = !isOpen.value
  if (isOpen.value) {
    nextTick(() => {
      // 动态定位：面板出现在按钮下方，右对齐
      if (btnRef.value) {
        const rect = btnRef.value.getBoundingClientRect()
        const panelWidth = Math.min(520, window.innerWidth - 24)
        const rightOffset = window.innerWidth - rect.right
        panelStyle.value = {
          position: 'fixed',
          top: (rect.bottom + 10) + 'px',
          right: Math.max(12, rightOffset) + 'px',
          width: panelWidth + 'px',
          zIndex: 9998,
        }
      }
      scrollToBottom()
    })
  }
}

async function send() {
  const text = inputText.value.trim()
  if (!text || loading.value) return

  messages.value.push({ role: 'user', content: text })
  inputText.value = ''
  loading.value = true
  await nextTick(scrollToBottom)

  try {
    const data = await getRecommendation(text)
    const aiReply = data.llm_reply || '为您找到以下推荐：'
    const animeCard = data.name ? data : null
    messages.value.push({
      role: 'ai',
      reply: aiReply,
      card: animeCard,
    })
    // 已登录则自动保存历史
    if (isLoggedIn()) {
      saveChatHistory(text, aiReply, animeCard).catch(() => {})
    }
  } catch (err) {
    messages.value.push({
      role: 'ai',
      reply: '抱歉，推荐服务暂时不可用，请稍后再试。',
      card: null,
    })
  } finally {
    loading.value = false
    await nextTick(scrollToBottom)
  }
}

function scrollToBottom() {
  if (msgListRef.value) {
    msgListRef.value.scrollTop = msgListRef.value.scrollHeight
  }
}
</script>

<style scoped>
/* ===== 外层包裹 ===== */
.recommend-wrapper {
  position: relative;
  flex-shrink: 0;
}

/* ===== 头像触发按钮 ===== */
.avatar-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 12px 4px 4px;
  border-radius: 40px;
  border: 1.5px solid rgba(0, 229, 255, 0.3);
  background: rgba(13, 17, 23, 0.8);
  cursor: pointer;
  transition: border-color 0.2s, box-shadow 0.2s, background 0.2s;
  white-space: nowrap;
  backdrop-filter: blur(8px);
}
.avatar-btn:hover,
.avatar-btn.active {
  border-color: rgba(0, 229, 255, 0.7);
  box-shadow: 0 0 14px rgba(0, 229, 255, 0.25);
  background: rgba(0, 229, 255, 0.06);
}
.avatar-img {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  object-fit: cover;
  object-position: top;
  border: 1.5px solid rgba(0, 229, 255, 0.4);
  background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
}
.avatar-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--neon-cyan, #00e5ff);
  letter-spacing: 0.3px;
}
.close-dot {
  font-size: 16px;
  color: #9ca3af;
  line-height: 1;
  margin-left: 2px;
}

/* ===== 聊天面板（通过 panelStyle 内联定位）===== */
.chat-panel {
  max-height: 700px;
  display: flex;
  flex-direction: column;
  background: rgba(6, 10, 20, 0.96);
  border: 1px solid rgba(255, 255, 255, 0.07);
  border-top: 2px solid var(--neon-cyan, #00e5ff);
  border-radius: 18px;
  box-shadow: 0 0 60px rgba(0, 229, 255, 0.12), 0 28px 80px rgba(0, 0, 0, 0.75);
  overflow: hidden;
  font-family: 'Noto Sans SC', 'Segoe UI', sans-serif;
  backdrop-filter: blur(24px);
  -webkit-backdrop-filter: blur(24px);
}

/* 标题栏 */
.panel-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 18px;
  background: rgba(0, 229, 255, 0.04);
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  flex-shrink: 0;
}
.header-avatar {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  object-fit: cover;
  object-position: top;
  border: 2px solid rgba(0, 229, 255, 0.4);
  box-shadow: 0 0 12px rgba(0, 229, 255, 0.25);
  flex-shrink: 0;
}
.header-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.header-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--text-primary, #edf1f7);
  letter-spacing: 0.3px;
  line-height: 1.3;
}
.header-hint {
  font-size: 11px;
  color: var(--text-muted, #546070);
}
.header-close {
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 6px;
  color: var(--text-muted, #546070);
  font-size: 18px;
  cursor: pointer;
  padding: 2px 8px;
  line-height: 1;
  transition: all 0.15s;
}
.header-close:hover {
  color: var(--text-primary, #edf1f7);
  background: rgba(255, 255, 255, 0.08);
  border-color: rgba(255, 255, 255, 0.15);
}

/* 消息区 */
.messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  scrollbar-width: thin;
  scrollbar-color: rgba(0, 229, 255, 0.2) transparent;
}
.messages::-webkit-scrollbar { width: 4px; }
.messages::-webkit-scrollbar-track { background: transparent; }
.messages::-webkit-scrollbar-thumb {
  background: rgba(0, 229, 255, 0.2);
  border-radius: 2px;
}

/* 气泡 */
.bubble { display: flex; flex-direction: column; max-width: 88%; }
.user-bubble { align-self: flex-end; align-items: flex-end; }
.ai-bubble   { align-self: flex-start; align-items: flex-start; }

.bubble-content {
  padding: 11px 15px;
  border-radius: 14px;
  font-size: 13.5px;
  line-height: 1.7;
  word-break: break-word;
}
.user-bubble .bubble-content {
  background: rgba(0, 229, 255, 0.12);
  border: 1px solid rgba(0, 229, 255, 0.25);
  border-top-color: rgba(0, 229, 255, 0.4);
  color: #d8f6ff;
  border-bottom-right-radius: 4px;
}
.ai-bubble .bubble-content {
  background: rgba(15, 22, 35, 0.8);
  border: 1px solid rgba(255, 255, 255, 0.07);
  border-left: 2px solid rgba(0, 229, 255, 0.25);
  color: var(--text-primary, #edf1f7);
  border-bottom-left-radius: 4px;
}

/* 加载动画 */
.loading-dots {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 10px 14px;
}
.loading-dots span {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--neon-cyan, #00e5ff);
  animation: dotBounce 1.2s infinite ease-in-out;
}
.loading-dots span:nth-child(2) { animation-delay: 0.2s; background: #8b5cf6; }
.loading-dots span:nth-child(3) { animation-delay: 0.4s; background: var(--neon-pink, #ff4d8d); }
@keyframes dotBounce {
  0%, 80%, 100% { transform: scale(0.7); opacity: 0.5; }
  40%            { transform: scale(1.1); opacity: 1; }
}

/* 输入区 */
.input-area {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 14px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  background: rgba(5, 9, 18, 0.7);
  flex-shrink: 0;
}
.chat-input {
  flex: 1;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 10px;
  color: var(--text-primary, #edf1f7);
  font-size: 13px;
  padding: 10px 14px;
  outline: none;
  transition: border-color 0.2s, box-shadow 0.2s;
  font-family: inherit;
}
.chat-input:focus {
  border-color: rgba(0, 229, 255, 0.35);
  box-shadow: 0 0 0 2px rgba(0, 229, 255, 0.05);
}
.chat-input::placeholder { color: var(--text-muted, #546070); }
.chat-input:disabled { opacity: 0.5; }

.send-btn {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: linear-gradient(135deg, #00b8d9, #00e5ff);
  border: none;
  color: #020d18;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: transform 0.15s, box-shadow 0.15s;
  box-shadow: 0 2px 10px rgba(0, 229, 255, 0.3);
}
.send-btn:hover:not(:disabled) {
  transform: scale(1.08);
  box-shadow: 0 4px 16px rgba(0, 229, 255, 0.5);
}
.send-btn:disabled { opacity: 0.35; cursor: not-allowed; }
.send-btn svg { width: 15px; height: 15px; }

/* ===== 历史记录链接 ===== */
.history-footer {
  display: flex;
  justify-content: center;
  padding: 6px 0 2px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
}
.history-link {
  display: flex;
  align-items: center;
  gap: 5px;
  background: none;
  border: none;
  cursor: pointer;
  font-size: 12px;
  color: rgba(0, 229, 255, 0.5);
  padding: 4px 8px;
  border-radius: 4px;
  transition: color 0.2s, background 0.2s;
  font-family: inherit;
}
.history-link:hover {
  color: rgba(0, 229, 255, 0.9);
  background: rgba(0, 229, 255, 0.06);
}

/* ===== 面板滑入动画 ===== */
.chat-slide-enter-active { animation: chatIn 0.28s cubic-bezier(0.34, 1.56, 0.64, 1); }
.chat-slide-leave-active { animation: chatOut 0.2s ease-in; }
@keyframes chatIn {
  from { opacity: 0; transform: translateY(-12px) scale(0.96); }
  to   { opacity: 1; transform: translateY(0) scale(1); }
}
@keyframes chatOut {
  from { opacity: 1; transform: translateY(0) scale(1); }
  to   { opacity: 0; transform: translateY(-8px) scale(0.96); }
}
</style>

<style scoped>
/* ===== 悬浮按钮 ===== */
.fab {
  position: fixed;
  bottom: 28px;
  right: 28px;
  z-index: 9999;
  width: 56px;
  height: 56px;
  border-radius: 50%;
  background: linear-gradient(135deg, #00e5ff 0%, #ff4d8d 100%);
  border: none;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 0 18px rgba(0, 229, 255, 0.5), 0 4px 16px rgba(0, 0, 0, 0.4);
  transition: transform 0.2s, box-shadow 0.2s;
  color: #0a0e1a;
}
.fab:hover {
  transform: scale(1.08);
  box-shadow: 0 0 28px rgba(0, 229, 255, 0.7), 0 6px 20px rgba(0, 0, 0, 0.5);
}
.fab.active {
  background: linear-gradient(135deg, #ff4d8d 0%, #00e5ff 100%);
}
.fab svg {
  width: 24px;
  height: 24px;
}

/* ===== 聊天面板 ===== */
.chat-panel {
  position: fixed;
  bottom: 96px;
  right: 28px;
  z-index: 9998;
  width: 400px;
  max-height: 580px;
  display: flex;
  flex-direction: column;
  background: #0d1117;
  border: 1px solid rgba(0, 229, 255, 0.3);
  border-radius: 16px;
  box-shadow: 0 0 40px rgba(0, 229, 255, 0.12), 0 20px 60px rgba(0, 0, 0, 0.6);
  overflow: hidden;
  font-family: 'JetBrains Mono', 'Segoe UI', sans-serif;
}

/* 标题栏 */
.panel-header {
  padding: 14px 16px 12px;
  background: linear-gradient(90deg, rgba(0, 229, 255, 0.08) 0%, rgba(255, 77, 141, 0.08) 100%);
  border-bottom: 1px solid rgba(0, 229, 255, 0.15);
  flex-shrink: 0;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 3px;
}
.header-icon { font-size: 18px; }
.header-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--neon-cyan, #00e5ff);
  letter-spacing: 0.5px;
}
.header-hint {
  font-size: 11px;
  color: #6b7280;
}

/* 消息区 */
.messages {
  flex: 1;
  overflow-y: auto;
  padding: 14px 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  scrollbar-width: thin;
  scrollbar-color: rgba(0, 229, 255, 0.2) transparent;
}
.messages::-webkit-scrollbar { width: 4px; }
.messages::-webkit-scrollbar-track { background: transparent; }
.messages::-webkit-scrollbar-thumb {
  background: rgba(0, 229, 255, 0.2);
  border-radius: 2px;
}

/* 气泡 */
.bubble { display: flex; flex-direction: column; max-width: 90%; }
.user-bubble { align-self: flex-end; align-items: flex-end; }
.ai-bubble   { align-self: flex-start; align-items: flex-start; }

.bubble-content {
  padding: 9px 13px;
  border-radius: 12px;
  font-size: 13px;
  line-height: 1.55;
  word-break: break-word;
}
.user-bubble .bubble-content {
  background: rgba(0, 229, 255, 0.15);
  border: 1px solid rgba(0, 229, 255, 0.35);
  color: #e0f7ff;
  border-bottom-right-radius: 4px;
}
.ai-bubble .bubble-content {
  background: rgba(255, 77, 141, 0.08);
  border: 1px solid rgba(255, 77, 141, 0.25);
  color: #f3f4f6;
  border-bottom-left-radius: 4px;
}

/* 加载动画 */
.loading-dots {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 10px 14px;
}
.loading-dots span {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--neon-cyan, #00e5ff);
  animation: dotBounce 1.2s infinite ease-in-out;
}
.loading-dots span:nth-child(2) { animation-delay: 0.2s; background: #8b5cf6; }
.loading-dots span:nth-child(3) { animation-delay: 0.4s; background: var(--neon-pink, #ff4d8d); }
@keyframes dotBounce {
  0%, 80%, 100% { transform: scale(0.7); opacity: 0.5; }
  40%            { transform: scale(1.1); opacity: 1; }
}

/* 输入区 */
.input-area {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-top: 1px solid rgba(0, 229, 255, 0.12);
  background: rgba(10, 14, 26, 0.6);
  flex-shrink: 0;
}
.chat-input {
  flex: 1;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(0, 229, 255, 0.2);
  border-radius: 8px;
  color: #e5e7eb;
  font-size: 13px;
  padding: 8px 12px;
  outline: none;
  transition: border-color 0.2s;
  font-family: inherit;
}
.chat-input:focus { border-color: rgba(0, 229, 255, 0.5); }
.chat-input::placeholder { color: #4b5563; }
.chat-input:disabled { opacity: 0.5; }

.send-btn {
  width: 36px;
  height: 36px;
  border-radius: 8px;
  background: linear-gradient(135deg, rgba(0, 229, 255, 0.2) 0%, rgba(255, 77, 141, 0.2) 100%);
  border: 1px solid rgba(0, 229, 255, 0.3);
  color: var(--neon-cyan, #00e5ff);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: background 0.2s, transform 0.1s;
}
.send-btn:hover:not(:disabled) {
  background: linear-gradient(135deg, rgba(0, 229, 255, 0.35) 0%, rgba(255, 77, 141, 0.35) 100%);
  transform: scale(1.05);
}
.send-btn:disabled { opacity: 0.35; cursor: not-allowed; }
.send-btn svg { width: 16px; height: 16px; }

/* ===== 面板滑入动画 ===== */
.chat-slide-enter-active { animation: chatIn 0.28s cubic-bezier(0.34, 1.56, 0.64, 1); }
.chat-slide-leave-active { animation: chatOut 0.2s ease-in; }
@keyframes chatIn {
  from { opacity: 0; transform: translateY(20px) scale(0.96); }
  to   { opacity: 1; transform: translateY(0) scale(1); }
}
@keyframes chatOut {
  from { opacity: 1; transform: translateY(0) scale(1); }
  to   { opacity: 0; transform: translateY(16px) scale(0.96); }
}

/* 移动端适配 */
@media (max-width: 480px) {
  .chat-panel {
    width: calc(100vw - 20px);
    right: 10px;
    bottom: 80px;
  }
  .fab { bottom: 18px; right: 18px; }
}
</style>
