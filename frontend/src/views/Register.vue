<template>
  <div class="auth-page">
    <div class="auth-bg" :style="bgStyle"></div>
    <div class="auth-overlay"></div>

    <div class="auth-card register-card">
      <!-- 顶部 logo 区 -->
      <div class="auth-logo">
        <img src="/irina.png" alt="伊蕾娜" class="auth-avatar" />
        <div class="auth-logo-text">
          <span class="auth-system-name">动漫情感分析系统</span>
          <span class="auth-welcome">加入我们</span>
        </div>
      </div>

      <h2 class="auth-title">创建账号</h2>

      <form class="auth-form" @submit.prevent="handleRegister">
        <div class="form-group">
          <label class="form-label">用户名</label>
          <div class="input-wrapper">
            <svg class="input-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="8" r="4"/><path d="M4 20c0-4 3.6-7 8-7s8 3 8 7"/>
            </svg>
            <input
              v-model="form.username"
              type="text"
              class="auth-input"
              placeholder="3-20位（字母/数字/中文）"
              autocomplete="username"
              maxlength="20"
            />
          </div>
        </div>

        <div class="form-group">
          <label class="form-label">密码</label>
          <div class="input-wrapper">
            <svg class="input-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>
            </svg>
            <input
              v-model="form.password"
              :type="showPwd ? 'text' : 'password'"
              class="auth-input"
              placeholder="至少6位"
              autocomplete="new-password"
            />
            <button type="button" class="pwd-toggle" @click="showPwd = !showPwd">
              <svg v-if="!showPwd" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16">
                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>
              </svg>
              <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16">
                <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/>
              </svg>
            </button>
          </div>
        </div>

        <div class="form-group">
          <label class="form-label">确认密码</label>
          <div class="input-wrapper">
            <svg class="input-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M9 12l2 2 4-4m6 2a9 9 0 1 1-18 0 9 9 0 0 1 18 0z"/>
            </svg>
            <input
              v-model="form.confirm"
              type="password"
              class="auth-input"
              placeholder="再次输入密码"
              autocomplete="new-password"
            />
          </div>
        </div>

        <p v-if="errorMsg" class="error-msg">{{ errorMsg }}</p>

        <button type="submit" class="auth-btn" :disabled="loading">
          <span v-if="loading" class="btn-loading">
            <span /><span /><span />
          </span>
          <span v-else>注 册</span>
        </button>
      </form>

      <p class="auth-switch">
        已有账号？
        <router-link to="/login" class="auth-link">立即登录</router-link>
      </p>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { register } from '../api/index.js'
import { setToken, setUser } from '../utils/auth'
import registerBg from '../assets/register-bg.png'

const router = useRouter()
const form = ref({ username: '', password: '', confirm: '' })
const loading = ref(false)
const errorMsg = ref('')
const showPwd = ref(false)

const bgStyle = { backgroundImage: `url(${registerBg})` }

async function handleRegister() {
  errorMsg.value = ''
  const { username, password, confirm } = form.value
  if (!username.trim() || !password || !confirm) {
    errorMsg.value = '请填写所有字段'
    return
  }
  if (password !== confirm) {
    errorMsg.value = '两次密码不一致'
    return
  }
  if (password.length < 6) {
    errorMsg.value = '密码至少6位'
    return
  }
  loading.value = true
  try {
    const data = await register(username.trim(), password)
    setToken(data.token)
    setUser({ username: data.username, user_id: data.user_id })
    router.push({ name: 'Home' })
  } catch (e) {
    errorMsg.value = e.message || '注册失败，请重试'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.auth-page {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}

.auth-bg {
  position: absolute;
  inset: 0;
  background-size: cover;
  background-position: center;
  background-repeat: no-repeat;
  transform: scale(1.04);
  filter: brightness(0.82);
}

.auth-overlay {
  position: absolute;
  inset: 0;
  background: linear-gradient(
    135deg,
    rgba(10, 5, 20, 0.65) 0%,
    rgba(20, 8, 35, 0.72) 50%,
    rgba(5, 10, 25, 0.6) 100%
  );
}

/* ===== 卡片 ===== */
.auth-card {
  position: relative;
  z-index: 10;
  width: 420px;
  padding: 44px 40px 36px;
  border-radius: 20px;
  background: rgba(8, 4, 18, 0.78);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-top: 1px solid rgba(255, 45, 155, 0.22);
  backdrop-filter: blur(24px);
  -webkit-backdrop-filter: blur(24px);
  box-shadow:
    0 0 0 1px rgba(255, 45, 155, 0.04),
    0 20px 60px rgba(0, 0, 0, 0.6),
    0 0 80px rgba(255, 45, 155, 0.05);
}

/* ===== Logo 区 ===== */
.auth-logo {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 24px;
}

.auth-avatar {
  width: 52px;
  height: 52px;
  border-radius: 50%;
  object-fit: cover;
  object-position: top;
  border: 2px solid rgba(255, 45, 155, 0.45);
  box-shadow: 0 0 16px rgba(255, 45, 155, 0.35), 0 0 40px rgba(255, 45, 155, 0.1);
}

.auth-logo-text {
  display: flex;
  flex-direction: column;
}

.auth-system-name {
  font-size: 11px;
  letter-spacing: 1.5px;
  color: rgba(255, 100, 180, 0.65);
  text-transform: uppercase;
  font-family: monospace;
}

.auth-welcome {
  font-size: 13px;
  color: rgba(255, 255, 255, 0.5);
  margin-top: 2px;
}

/* ===== 标题 ===== */
.auth-title {
  font-size: 22px;
  font-weight: 700;
  color: #f8e8ff;
  margin: 0 0 28px;
  letter-spacing: 0.5px;
}

/* ===== 表单 ===== */
.auth-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.form-label {
  font-size: 12px;
  letter-spacing: 1px;
  color: rgba(255, 100, 180, 0.75);
  font-family: monospace;
  text-transform: uppercase;
}

.input-wrapper {
  position: relative;
  display: flex;
  align-items: center;
}

.input-icon {
  position: absolute;
  left: 12px;
  width: 16px;
  height: 16px;
  color: rgba(255, 45, 155, 0.45);
  pointer-events: none;
}

.auth-input {
  width: 100%;
  padding: 12px 42px 12px 40px;
  background: rgba(255, 45, 155, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.07);
  border-radius: 10px;
  color: #f8e8ff;
  font-size: 14px;
  outline: none;
  transition: border-color 0.2s, box-shadow 0.2s, background 0.2s;
  box-sizing: border-box;
}

.auth-input::placeholder {
  color: rgba(255, 255, 255, 0.22);
}

.auth-input:focus {
  border-color: rgba(255, 45, 155, 0.45);
  border-top-color: rgba(255, 45, 155, 0.7);
  box-shadow: 0 0 0 3px rgba(255, 45, 155, 0.06), 0 0 16px rgba(255, 45, 155, 0.1);
  background: rgba(255, 45, 155, 0.05);
}

.pwd-toggle {
  position: absolute;
  right: 12px;
  background: none;
  border: none;
  cursor: pointer;
  color: rgba(255, 45, 155, 0.4);
  padding: 0;
  display: flex;
  align-items: center;
  transition: color 0.2s;
}
.pwd-toggle:hover { color: rgba(255, 45, 155, 0.8); }

/* ===== 错误提示 ===== */
.error-msg {
  font-size: 13px;
  color: #ff6b8a;
  margin: -4px 0 0;
  padding: 8px 12px;
  background: rgba(255, 45, 85, 0.1);
  border-left: 3px solid #ff2d55;
  border-radius: 0 6px 6px 0;
}

/* ===== 注册按钮 ===== */
.auth-btn {
  width: 100%;
  padding: 14px;
  margin-top: 6px;
  border-radius: 10px;
  border: none;
  background: linear-gradient(135deg, #c2006a 0%, #ff2d9b 50%, #e8008a 100%);
  color: #fff0f8;
  font-size: 15px;
  font-weight: 800;
  letter-spacing: 3px;
  cursor: pointer;
  transition: all 0.25s;
  box-shadow: 0 4px 20px rgba(255, 45, 155, 0.35);
}

.auth-btn:not(:disabled):hover {
  transform: translateY(-1px);
  box-shadow: 0 8px 32px rgba(255, 45, 155, 0.5), 0 0 60px rgba(255, 45, 155, 0.18);
  background: linear-gradient(135deg, #d4007a 0%, #ff40aa 50%, #fa10a0 100%);
}

.auth-btn:not(:disabled):active {
  transform: translateY(0);
  box-shadow: 0 2px 12px rgba(255, 45, 155, 0.3);
}

.auth-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-loading {
  display: flex;
  justify-content: center;
  gap: 5px;
}
.btn-loading span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #fff0f8;
  animation: dot-bounce 1.2s infinite ease-in-out;
}
.btn-loading span:nth-child(2) { animation-delay: 0.2s; }
.btn-loading span:nth-child(3) { animation-delay: 0.4s; }
@keyframes dot-bounce {
  0%, 80%, 100% { transform: scale(0.7); opacity: 0.5; }
  40% { transform: scale(1); opacity: 1; }
}

/* ===== 底部跳转 ===== */
.auth-switch {
  text-align: center;
  margin-top: 22px;
  font-size: 13px;
  color: rgba(255, 255, 255, 0.4);
}

.auth-link {
  color: #ff64b8;
  text-decoration: none;
  font-weight: 600;
  transition: text-shadow 0.2s;
}
.auth-link:hover {
  text-shadow: 0 0 8px rgba(255, 45, 155, 0.7);
}
</style>
