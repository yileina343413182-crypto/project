<template>
  <div class="recommend-result">
    <PromptTracePanel v-if="result?.prompt_trace" :trace="result.prompt_trace" />
    <EvidenceChain v-if="result?.retrieval_evidence?.length" :evidence="result.retrieval_evidence" title="Recommendation Evidence" />
    <div v-if="result?.need_clarification" class="clarify-box">
      <span>需要补充偏好</span>
      <p>{{ result.clarifying_question }}</p>
    </div>

    <div v-else class="rec-list">
      <article v-for="item in result?.recommendations || []" :key="item.anime_id || item.name" class="rec-item">
        <div class="rec-head">
          <h3>{{ item.name }}</h3>
          <span>{{ item.platform || 'local' }}</span>
        </div>
        <p class="reason">{{ item.reason }}</p>
        <div class="tags">
          <span v-for="tag in item.match_tags" :key="tag">{{ tag }}</span>
        </div>
        <div class="evidence">
          <div>
            <strong>情感证据</strong>
            <p>正向 {{ item.evidence?.sentiment?.positive || 0 }} / 中性 {{ item.evidence?.sentiment?.neutral || 0 }} / 负向 {{ item.evidence?.sentiment?.negative || 0 }}</p>
          </div>
          <div>
            <strong>主题证据</strong>
            <p>{{ (item.evidence?.topics || []).join('、') || '暂无主题数据' }}</p>
          </div>
        </div>
        <blockquote v-for="(comment, idx) in item.evidence?.comments || []" :key="idx">{{ comment.content }}</blockquote>
        <EvidenceChain v-if="item.retrieval_evidence?.length" :evidence="item.retrieval_evidence" title="Item Evidence" />
      </article>
      <p v-if="!(result?.recommendations || []).length" class="empty">暂无推荐结果。</p>
    </div>

    <span v-if="result?.fallback" class="fallback">本地推荐模式</span>
  </div>
</template>

<script setup>
import EvidenceChain from './EvidenceChain.vue'
import PromptTracePanel from './PromptTracePanel.vue'

defineProps({
  result: { type: Object, default: null }
})
</script>

<style scoped>
.recommend-result { display: flex; flex-direction: column; gap: 14px; }
.clarify-box, .rec-item {
  border: 1px solid rgba(255,255,255,0.07);
  border-radius: 8px;
  background: rgba(10,16,28,0.72);
  padding: 18px;
}
.clarify-box span, .fallback { color: var(--neon-amber); font-family: var(--font-mono); font-size: 12px; }
.clarify-box p, .reason, .evidence p, blockquote, .empty { color: var(--text-secondary); line-height: 1.75; font-size: 14px; }
.rec-list { display: flex; flex-direction: column; gap: 14px; }
.rec-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.rec-head h3 { font-size: 18px; color: var(--text-primary); }
.rec-head span { color: var(--neon-cyan); font-family: var(--font-mono); font-size: 11px; }
.tags { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
.tags span { padding: 3px 9px; border-radius: 5px; border: 1px solid rgba(0,229,255,0.18); color: var(--neon-cyan); font-size: 12px; background: rgba(0,229,255,0.06); }
.evidence { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 12px; }
.evidence div { border: 1px solid rgba(255,255,255,0.05); border-radius: 8px; padding: 12px; background: rgba(255,255,255,0.03); }
.evidence strong { color: var(--text-primary); font-size: 13px; }
blockquote { margin: 10px 0 0; padding: 10px 12px; border-left: 2px solid rgba(0,229,255,0.35); background: rgba(255,255,255,0.03); }
@media (max-width: 760px) { .evidence { grid-template-columns: 1fr; } }
</style>

