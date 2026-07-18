<template>
  <main class="agent-center noise-overlay">
    <nav class="top-nav glass">
      <router-link to="/" class="nav-link">返回首页</router-link>
      <span>AGENT CENTER</span>
    </nav>

    <section class="hero">
      <p>LangChain Agent Center</p>
      <h1>智能体中心</h1>
      <span>独立于原 AI 小助手的新功能模块，面向舆情诊断与偏好推荐。</span>
    </section>

    <section class="agent-grid">
      <article class="agent-entry" @click="router.push('/agent/opinion')">
        <span class="entry-code">01</span>
        <h2>舆情诊断 Agent</h2>
        <p>自动调用情感、主题、词云、代表评论和 Bangumi 信息，生成结构化舆情报告。</p>
        <button>进入诊断</button>
      </article>
      <article class="agent-entry" @click="router.push('/agent/recommendation')">
        <span class="entry-code">02</span>
        <h2>推荐 Agent 2.0</h2>
        <p>通过多轮澄清、偏好记忆和评论证据，给出更可解释的动漫推荐。</p>
        <button>开始推荐</button>
      </article>
      <article class="agent-entry" @click="router.push('/agent/evaluation')">
        <span class="entry-code">03</span>
        <h2>RAG 评测看板</h2>
        <p>管理 Chroma 索引、调试检索证据链，并查看 PromptOps 与 RAG 评测指标。</p>
        <button>查看评测</button>
      </article>
    </section>
  </main>
</template>

<script setup>
import { useRouter } from 'vue-router'
const router = useRouter()
</script>

<style scoped>
.agent-center { min-height: 100vh; padding-bottom: 60px; background: radial-gradient(circle at top left, rgba(0,229,255,0.08), transparent 32%), var(--bg-deep); }
.top-nav { display: flex; align-items: center; justify-content: space-between; padding: 14px 24px; position: sticky; top: 0; z-index: 20; }
.nav-link { color: var(--neon-cyan); font-size: 13px; }
.top-nav span { font-family: var(--font-mono); color: var(--text-muted); letter-spacing: 2px; font-size: 11px; }
.hero { max-width: 960px; margin: 0 auto; padding: 70px 24px 36px; }
.hero p { color: var(--neon-cyan); font-family: var(--font-mono); letter-spacing: 2px; font-size: 12px; }
.hero h1 { font-size: 42px; line-height: 1.2; margin: 12px 0; }
.hero span { color: var(--text-secondary); line-height: 1.8; }
.agent-grid { max-width: 960px; margin: 0 auto; padding: 0 24px; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; }
.agent-entry { border: 1px solid rgba(255,255,255,0.07); border-radius: 8px; background: rgba(10,16,28,0.72); padding: 24px; cursor: pointer; transition: var(--transition); }
.agent-entry:hover { transform: translateY(-4px); border-color: rgba(0,229,255,0.25); box-shadow: var(--glow-cyan); }
.entry-code { font-family: var(--font-mono); color: var(--neon-cyan); font-size: 12px; }
h2 { margin: 12px 0; font-size: 22px; }
.agent-entry p { color: var(--text-secondary); line-height: 1.75; min-height: 76px; }
button { margin-top: 20px; padding: 10px 16px; border-radius: 7px; background: rgba(0,229,255,0.12); color: var(--neon-cyan); border: 1px solid rgba(0,229,255,0.25); }
@media (max-width: 760px) { .agent-grid { grid-template-columns: 1fr; } .hero h1 { font-size: 32px; } }
</style>



