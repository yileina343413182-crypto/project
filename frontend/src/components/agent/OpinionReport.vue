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
        <ul><li v-for="(item, index) in report.positive_points" :key="index">{{ opinionItemText(item) }}</li></ul>
      </div>
      <div class="report-section">
        <h3>负面槽点</h3>
        <ul><li v-for="(item, index) in report.negative_points" :key="index">{{ opinionItemText(item) }}</li></ul>
      </div>
      <div class="report-section">
        <h3>主题洞察</h3>
        <ul><li v-for="(item, index) in report.topic_insights" :key="index">{{ opinionItemText(item) }}</li></ul>
      </div>
      <div class="report-section">
        <h3>风险与建议</h3>
        <ul>
          <li v-for="(item, index) in report.risk_points" :key="'risk-' + index">{{ opinionItemText(item) }}</li>
          <li v-for="(item, index) in report.operation_suggestions" :key="'suggest-' + index">{{ opinionItemText(item) }}</li>
        </ul>
      </div>
    </section>

    <section class="report-section">
      <h3>受众画像</h3>
      <p>{{ report.audience_profile || '暂无明确画像。' }}</p>
    </section>

  </div>
</template>

<script setup>
import { opinionItemText } from '../../utils/opinionReportDisplay'

defineProps({
  report: { type: Object, default: null }
})
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
p, li { color: var(--text-secondary); font-size: 14px; line-height: 1.8; }
ul { display: flex; flex-direction: column; gap: 7px; padding-left: 18px; list-style: disc; }
.report-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.fallback-badge { display: inline-block; margin-top: 12px; color: var(--neon-amber); font-size: 12px; }
@media (max-width: 900px) { .report-grid { grid-template-columns: 1fr; } }
</style>
