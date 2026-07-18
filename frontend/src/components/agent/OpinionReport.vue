<template>
  <div class="report" v-if="report">
    <section class="report-section hero">
      <span class="eyebrow">OPINION REPORT</span>
      <h2>{{ report.summary || '舆情诊断完成' }}</h2>
      <p>{{ report.sentiment_overview }}</p>
      <span v-if="report.fallback" class="fallback-badge">本地分析模式</span>
    </section>

    <section class="report-grid">
      <div class="report-section">
        <h3>正面亮点</h3>
        <ul><li v-for="item in report.positive_points" :key="item">{{ item }}</li></ul>
      </div>
      <div class="report-section">
        <h3>负面槽点</h3>
        <ul><li v-for="item in report.negative_points" :key="item">{{ item }}</li></ul>
      </div>
      <div class="report-section">
        <h3>主题洞察</h3>
        <ul><li v-for="item in report.topic_insights" :key="item">{{ item }}</li></ul>
      </div>
      <div class="report-section">
        <h3>风险与建议</h3>
        <ul>
          <li v-for="item in report.risk_points" :key="'risk-' + item">{{ item }}</li>
          <li v-for="item in report.operation_suggestions" :key="'suggest-' + item">{{ item }}</li>
        </ul>
      </div>
    </section>

    <section class="report-section">
      <h3>受众画像</h3>
      <p>{{ report.audience_profile || '暂无明确画像。' }}</p>
    </section>

    <section class="report-section">
      <h3>代表评论</h3>
      <div class="comment-columns">
        <div v-for="label in labels" :key="label.key" class="comment-column">
          <h4>{{ label.text }}</h4>
          <p v-if="!comments(label.key).length" class="empty">暂无样本</p>
          <blockquote v-for="(item, idx) in comments(label.key)" :key="idx">
            {{ item.content }}
            <small>{{ item.platform }} · {{ item.likes || 0 }} likes</small>
          </blockquote>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
const props = defineProps({
  report: { type: Object, default: null }
})

const labels = [
  { key: 'positive', text: '正向' },
  { key: 'neutral', text: '中性' },
  { key: 'negative', text: '负向' }
]

function comments(key) {
  return props.report?.representative_comments?.[key] || []
}
</script>

<style scoped>
.report { display: flex; flex-direction: column; gap: 16px; }
.report-section {
  border: 1px solid rgba(255,255,255,0.07);
  border-radius: 8px;
  background: rgba(10,16,28,0.68);
  padding: 18px;
}
.hero { border-top: 2px solid var(--neon-cyan); }
.eyebrow { font-family: var(--font-mono); font-size: 11px; color: var(--neon-cyan); letter-spacing: 2px; }
h2 { margin-top: 8px; font-size: 22px; line-height: 1.35; }
h3 { font-size: 15px; margin-bottom: 10px; color: var(--neon-cyan); }
h4 { font-size: 13px; color: var(--text-primary); margin-bottom: 8px; }
p, li { color: var(--text-secondary); font-size: 14px; line-height: 1.8; }
ul { display: flex; flex-direction: column; gap: 7px; padding-left: 18px; list-style: disc; }
.report-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.fallback-badge { display: inline-block; margin-top: 12px; color: var(--neon-amber); font-size: 12px; }
.comment-columns { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
blockquote { margin: 0 0 10px; padding: 12px; border-left: 2px solid rgba(0,229,255,0.35); background: rgba(255,255,255,0.03); color: var(--text-secondary); font-size: 13px; line-height: 1.7; }
small { display: block; margin-top: 8px; color: var(--text-muted); }
.empty { color: var(--text-muted); }
@media (max-width: 900px) { .report-grid, .comment-columns { grid-template-columns: 1fr; } }
</style>
