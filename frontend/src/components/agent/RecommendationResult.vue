<template>
  <div class="recommend-result">
    <div class="rec-list">
      <article v-for="item in displayItems" :key="item.uiKey" class="rec-item">
        <div class="rec-head">
          <div class="title-line">
            <h3>{{ item.name }}</h3>
            <span v-if="item.recommendationIndex" class="recommendation-index">{{ item.recommendationIndex }}</span>
          </div>
        </div>

        <section v-if="item.reasonSummary" class="reason-block">
          <span class="section-label">推荐理由</span>
          <p class="reason">{{ item.reasonSummary }}</p>
        </section>

        <dl v-if="item.reasonDetails.length" class="reason-details">
          <div v-for="(detail, index) in item.reasonDetails" :key="`${detail.label}-${index}`" class="reason-detail">
            <dt>{{ detail.label }}</dt>
            <dd>{{ detail.value }}</dd>
          </div>
        </dl>

        <footer v-if="item.match_tags?.length || item.platform" class="rec-footer">
          <div v-if="item.match_tags?.length" class="tags">
            <span class="section-label">匹配标签</span>
            <div class="tag-list">
              <template v-for="tag in item.match_tags" :key="tag">
                <button
                  v-if="evidenceTagType(tag)"
                  type="button"
                  class="tag-button"
                  :class="{ active: isPanelOpen(item.uiKey, evidenceTagType(tag)) }"
                  :id="evidenceTriggerId(item.uiKey, evidenceTagType(tag))"
                  :aria-expanded="isPanelOpen(item.uiKey, evidenceTagType(tag))"
                  :aria-controls="item.panelId"
                  :aria-label="`${isPanelOpen(item.uiKey, evidenceTagType(tag)) ? '收起' : '查看'}${item.name}的${tag}`"
                  @click="toggleEvidencePanel(item.uiKey, evidenceTagType(tag))"
                >{{ tag }}<span aria-hidden="true">{{ isPanelOpen(item.uiKey, evidenceTagType(tag)) ? '−' : '＋' }}</span></button>
                <span v-else class="tag-label">{{ tag }}</span>
              </template>
            </div>
          </div>
          <span v-if="item.platform" class="platform">{{ item.platform }}</span>
        </footer>

        <section
          v-if="expandedPanels[item.uiKey]"
          :id="item.panelId"
          class="evidence-panel"
          role="region"
          :aria-label="`${item.name}${expandedPanels[item.uiKey] === 'reputation' ? '口碑依据' : '评论检索证据'}`"
        >
          <div class="evidence-panel-head">
            <div>
              <span class="panel-kicker">EVIDENCE INDEX</span>
              <h4>{{ expandedPanels[item.uiKey] === 'reputation' ? '口碑依据' : '评论检索证据' }}</h4>
            </div>
            <button type="button" class="panel-close" aria-label="关闭证据详情" @click="toggleEvidencePanel(item.uiKey, expandedPanels[item.uiKey])">×</button>
          </div>

          <template v-if="expandedPanels[item.uiKey] === 'reputation'">
            <p v-if="!item.reputation.hasContent" class="evidence-empty">该历史记录没有保存可展示的口碑明细。</p>

            <section v-if="item.reputation.sentimentCards.length" class="reputation-section">
              <div class="subsection-head">
                <h5>情感统计</h5>
                <code v-if="item.reputation.sentimentDocument">{{ item.reputation.sentimentDocument.doc_id }}</code>
              </div>
              <div class="sentiment-grid">
                <div v-for="stat in item.reputation.sentimentCards" :key="stat.label" class="sentiment-stat">
                  <span>{{ stat.label }}</span>
                  <strong>{{ stat.value }}</strong>
                </div>
              </div>
            </section>

            <section v-if="item.reputation.topics.length" class="reputation-section">
              <h5>主题依据</h5>
              <div class="topic-list">
                <div v-for="topic in item.reputation.topics" :key="topic.key" class="topic-row">
                  <span>{{ topic.text }}</span>
                  <code v-if="topic.matchedEvidence">{{ topic.matchedEvidence.doc_id }}</code>
                  <small v-else>未保存对应 doc_id</small>
                </div>
              </div>
            </section>

            <section v-if="item.reputation.comments.length" class="reputation-section">
              <h5>代表评论</h5>
              <div class="representative-list">
                <article v-for="comment in item.reputation.comments" :key="comment.key" class="representative-comment">
                  <div class="comment-head">
                    <span v-if="comment.sentimentLabel">{{ sentimentName(comment.sentimentLabel) }}</span>
                    <code v-if="comment.docId">{{ comment.docId }}</code>
                  </div>
                  <p>{{ comment.content }}</p>
                  <div class="comment-facts">
                    <span v-if="hasValue(comment.sentimentScore)">情感置信度 {{ formatSimilarity(comment.sentimentScore) }}</span>
                    <span v-if="hasValue(comment.likes)">点赞 {{ comment.likes }}</span>
                    <span v-if="comment.platform">平台 {{ comment.platform }}</span>
                    <span v-if="comment.publishTime">{{ comment.publishTime }}</span>
                  </div>
                  <div v-if="comment.matchedEvidence" class="metric-list">
                    <span v-if="hasPositiveMetric(comment.matchedEvidence.similarity)"><small>相似度</small>{{ formatSimilarity(comment.matchedEvidence.similarity) }}</span>
                    <span v-if="hasPositiveMetric(comment.matchedEvidence.rank)"><small>融合排名</small>#{{ comment.matchedEvidence.rank }}</span>
                    <span v-if="hasPositiveMetric(comment.matchedEvidence.rerank_score)"><small>Rerank 分数</small>{{ formatScore(comment.matchedEvidence.rerank_score) }}</span>
                  </div>
                  <p v-else class="missing-doc">该记录未保存可核对的 doc_id；为避免错误索引，不推测编号。</p>
                </article>
              </div>
            </section>

            <section v-if="item.reputation.linkedEvidence.length" class="reputation-section">
              <h5>关联检索明细</h5>
              <div class="retrieval-list">
                <article v-for="evidence in item.reputation.linkedEvidence" :key="evidence.doc_id" class="retrieval-card">
                  <div class="retrieval-head">
                    <span>{{ sourceName(evidence.source_type) }}</span>
                    <code>{{ evidence.doc_id }}</code>
                  </div>
                  <p>{{ evidence.content || '该证据没有保存正文。' }}</p>
                  <div class="metric-list">
                    <span v-if="hasPositiveMetric(evidence.similarity)"><small>相似度</small>{{ formatSimilarity(evidence.similarity) }}</span>
                    <span v-if="hasPositiveMetric(evidence.rank)"><small>融合排名</small>#{{ evidence.rank }}</span>
                    <span v-if="hasPositiveMetric(evidence.rrf_score)"><small>RRF 分数</small>{{ formatScore(evidence.rrf_score, 6) }}</span>
                    <span v-if="hasPositiveMetric(evidence.rerank_score)"><small>Rerank 分数</small>{{ formatScore(evidence.rerank_score) }}</span>
                    <span v-if="hasPositiveMetric(evidence.vector_rank)"><small>向量路排名</small>#{{ evidence.vector_rank }}</span>
                    <span v-if="hasPositiveMetric(evidence.keyword_rank)"><small>关键词路排名</small>#{{ evidence.keyword_rank }}</span>
                  </div>
                  <div v-if="evidence.safeMetadata.length" class="metadata-list">
                    <span v-for="meta in evidence.safeMetadata" :key="meta.label"><small>{{ meta.label }}</small>{{ meta.value }}</span>
                  </div>
                  <p v-if="evidence.source_label" class="source-label">来源：{{ evidence.source_label }}</p>
                </article>
              </div>
            </section>
          </template>

          <template v-else>
            <p class="panel-description">证据严格按当前动漫及推荐返回的 doc_id 关联，默认不会跨作品匹配。</p>
            <p v-if="item.missingEvidenceRefs.length" class="evidence-warning">
              以下引用在该历史记录中没有保存对应正文：
              <code v-for="ref in item.missingEvidenceRefs" :key="ref">{{ ref }}</code>
            </p>
            <p v-if="!item.commentEvidence.length" class="evidence-empty">
              该历史记录未保存带 doc_id 的检索明细；为避免错误索引，不展示推测证据。
            </p>
            <div v-else class="retrieval-list">
              <article v-for="evidence in item.commentEvidence" :key="evidence.doc_id" class="retrieval-card">
                <div class="retrieval-head">
                  <span>{{ sourceName(evidence.source_type) }}</span>
                  <code>{{ evidence.doc_id }}</code>
                </div>
                <p>{{ evidence.content || '该证据没有保存正文。' }}</p>
                <div class="metric-list">
                  <span v-if="hasPositiveMetric(evidence.similarity)"><small>相似度</small>{{ formatSimilarity(evidence.similarity) }}</span>
                  <span v-if="hasPositiveMetric(evidence.rank)"><small>融合排名</small>#{{ evidence.rank }}</span>
                  <span v-if="hasPositiveMetric(evidence.rrf_score)"><small>RRF 分数</small>{{ formatScore(evidence.rrf_score, 6) }}</span>
                  <span v-if="hasPositiveMetric(evidence.rerank_score)"><small>Rerank 分数</small>{{ formatScore(evidence.rerank_score) }}</span>
                  <span v-if="hasPositiveMetric(evidence.vector_rank)"><small>向量路排名</small>#{{ evidence.vector_rank }}</span>
                  <span v-if="hasPositiveMetric(evidence.keyword_rank)"><small>关键词路排名</small>#{{ evidence.keyword_rank }}</span>
                </div>
                <div v-if="evidence.safeMetadata.length" class="metadata-list">
                  <span v-for="meta in evidence.safeMetadata" :key="meta.label"><small>{{ meta.label }}</small>{{ meta.value }}</span>
                </div>
                <p v-if="evidence.source_label" class="source-label">来源：{{ evidence.source_label }}</p>
              </article>
            </div>
          </template>
        </section>
      </article>
      <p v-if="!displayItems.length" class="empty">暂无推荐结果。</p>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'

const props = defineProps({
  result: { type: Object, default: null }
})

const SCORE_PATTERN = /推荐指数\s*[：:]\s*((?:(?:★|☆|⭐|🌟)\s*){1,5}(?:[（(]\s*[0-5](?:\.\d+)?\s*星?\s*[）)])?|[0-5](?:\.\d+)?\s*(?:\/\s*5)?\s*星?)\s*[；;，,。]?/u
const DETAIL_PATTERN = /(简短剧情梗概|剧情梗概|核心看点|相近作品类比|观看平台建议|劝退点|适合的?观看场景|观看场景)\s*[：:]/gu

const expandedPanels = ref({})

const displayItems = computed(() => (
  Array.isArray(props.result?.recommendations)
    ? props.result.recommendations.map(formatItem)
    : []
))

watch(() => props.result, () => {
  expandedPanels.value = {}
})

const SAFE_METADATA_FIELDS = [
  ['anime_id', '动漫 ID'],
  ['anime_name', '动漫'],
  ['comment_id', '评论 ID'],
  ['sentiment_label', '情感'],
  ['sentiment_score', '情感置信度'],
  ['likes', '点赞'],
  ['platform', '平台'],
  ['publish_time', '发布时间'],
  ['topic_id', '主题 ID'],
  ['weight', '主题权重']
]

function cleanText(value) {
  return String(value || '')
    .replace(/^[\s，,；;。]+/u, '')
    .replace(/[\s；;]+$/u, '')
    .trim()
}

function splitReason(reason) {
  const details = []
  const matches = [...reason.matchAll(DETAIL_PATTERN)]
  if (!matches.length) return { summary: cleanText(reason), details }

  const summary = cleanText(reason.slice(0, matches[0].index))
  matches.forEach((match, index) => {
    const start = match.index + match[0].length
    const end = matches[index + 1]?.index ?? reason.length
    const value = cleanText(reason.slice(start, end))
    if (value) details.push({ label: match[1], value })
  })
  return { summary, details }
}

function evidenceTagType(tag) {
  const value = String(tag || '').trim()
  if (value === '口碑') return 'reputation'
  if (value === '评论证据') return 'comments'
  return ''
}

function isPanelOpen(itemKey, panel) {
  return expandedPanels.value[itemKey] === panel
}

function evidenceTriggerId(itemKey, panel) {
  return `recommendation-evidence-trigger-${itemKey}-${panel}`
}

function toggleEvidencePanel(itemKey, panel) {
  const isClosing = expandedPanels.value[itemKey] === panel
  expandedPanels.value = {
    ...expandedPanels.value,
    [itemKey]: isClosing ? '' : panel
  }
  if (isClosing) {
    nextTick(() => document.getElementById(evidenceTriggerId(itemKey, panel))?.focus())
  }
}

function hasValue(value) {
  return value !== undefined && value !== null && value !== ''
}

function hasPositiveMetric(value) {
  const number = numberOrNull(value)
  return number !== null && number > 0
}

function numberOrNull(value) {
  if (!hasValue(value)) return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

function formatSimilarity(value) {
  const number = numberOrNull(value)
  if (number === null) return String(value || '')
  const percentage = Math.abs(number) <= 1 ? number * 100 : number
  return `${Number(percentage.toFixed(1))}%`
}

function formatScore(value, digits = 4) {
  const number = numberOrNull(value)
  if (number === null) return String(value || '')
  return String(Number(number.toFixed(digits)))
}

function sourceName(sourceType) {
  const names = {
    comment: '评论',
    sentiment_summary: '口碑统计',
    topic: '主题',
    anime_profile: '动漫资料'
  }
  const value = String(sourceType || 'evidence')
  return names[value] ? `${names[value]} · ${value}` : value
}

function sentimentName(label) {
  return ({ positive: '正向', neutral: '中性', negative: '负向' })[label] || label
}

function evidenceDocId(evidence) {
  return String(evidence?.doc_id || evidence?.metadata?.doc_id || '').trim()
}

function animeIdFromDocId(docId) {
  const match = String(docId || '').match(/^anime:(\d+):/u)
  return match ? Number(match[1]) : null
}

function belongsToAnime(evidence, animeId, allowUnknownOwner = false) {
  const target = numberOrNull(animeId)
  const docId = evidenceDocId(evidence)
  if (!docId) return false

  const metadataOwner = numberOrNull(evidence?.metadata?.anime_id)
  const docOwner = animeIdFromDocId(docId)
  const knownOwners = [metadataOwner, docOwner].filter((value) => value !== null)
  if (target === null) return allowUnknownOwner
  if (!knownOwners.length) return allowUnknownOwner
  return knownOwners.length > 0 && knownOwners.every((value) => value === target)
}

function mergeMissingFields(target, source) {
  const merged = { ...target }
  Object.entries(source || {}).forEach(([key, value]) => {
    if (!hasValue(merged[key]) && hasValue(value)) merged[key] = value
  })
  merged.metadata = { ...(source?.metadata || {}), ...(target?.metadata || {}) }
  return merged
}

function safeMetadata(metadata) {
  return SAFE_METADATA_FIELDS.flatMap(([key, label]) => {
    const value = metadata?.[key]
    if (!hasValue(value) || (typeof value === 'object' && value !== null)) return []
    const formatted = key === 'sentiment_score' ? formatSimilarity(value) : String(value)
    return [{ label, value: key === 'sentiment_label' ? sentimentName(formatted) : formatted }]
  })
}

function decorateEvidence(evidence) {
  const docId = evidenceDocId(evidence)
  const sourceType = evidence?.source_type || evidence?.metadata?.source_type || ''
  return {
    ...evidence,
    doc_id: docId,
    source_type: sourceType,
    metadata: evidence?.metadata || {},
    safeMetadata: safeMetadata(evidence?.metadata || {})
  }
}

function evidenceContext(item) {
  const animeId = item?.anime_id
  const byDocId = new Map()
  const orderedIds = []
  const itemIds = []
  const itemEvidence = Array.isArray(item?.retrieval_evidence) ? item.retrieval_evidence : []
  const resultEvidence = Array.isArray(props.result?.retrieval_evidence) ? props.result.retrieval_evidence : []

  for (const [sourceIndex, collection] of [itemEvidence, resultEvidence].entries()) {
    for (const rawEvidence of collection) {
      // 单条推荐内的 evidence 已由后端按候选归属；结果级汇总仍必须显式校验 owner。
      if (!belongsToAnime(rawEvidence, animeId, sourceIndex === 0)) continue
      const docId = evidenceDocId(rawEvidence)
      if (!byDocId.has(docId)) {
        byDocId.set(docId, rawEvidence)
        orderedIds.push(docId)
      } else {
        byDocId.set(docId, mergeMissingFields(byDocId.get(docId), rawEvidence))
      }
      if (sourceIndex === 0 && !itemIds.includes(docId)) itemIds.push(docId)
    }
  }

  const refs = [...new Set(
    (Array.isArray(item?.evidence_refs) ? item.evidence_refs : [])
      .map((ref) => String(ref || '').trim())
      .filter(Boolean)
  )]
  const allEvidence = orderedIds.map((docId) => decorateEvidence(byDocId.get(docId)))
  const allByDocId = new Map(allEvidence.map((evidence) => [evidence.doc_id, evidence]))
  const referencedEvidence = refs.map((ref) => allByDocId.get(ref)).filter(Boolean)
  const fallbackEvidence = (itemIds.length ? itemIds : orderedIds)
    .map((docId) => allByDocId.get(docId))
    .filter(Boolean)

  return {
    allEvidence,
    commentEvidence: refs.length ? referencedEvidence : fallbackEvidence,
    missingEvidenceRefs: refs.filter((ref) => !allByDocId.has(ref))
  }
}

function normalizedContent(value) {
  return String(value || '').toLocaleLowerCase().replace(/\s+/gu, '').trim()
}

function findContentEvidence(content, evidence, sourceType) {
  const needle = normalizedContent(content)
  if (!needle) return null
  const candidates = evidence.filter((item) => !sourceType || item.source_type === sourceType)
  const exact = candidates.filter((item) => normalizedContent(item.content) === needle)
  if (exact.length === 1) return exact[0]
  if (needle.length < 12) return null
  const partial = candidates.filter((item) => {
    const candidate = normalizedContent(item.content)
    return candidate.length >= 12 && (candidate.includes(needle) || needle.includes(candidate))
  })
  return partial.length === 1 ? partial[0] : null
}

function topicText(topic) {
  if (typeof topic === 'string') return topic.trim()
  if (!topic || typeof topic !== 'object') return ''
  const keywords = Array.isArray(topic.keywords)
    ? topic.keywords.map((word) => typeof word === 'string' ? word : word?.word).filter(Boolean)
    : []
  return String(topic.name || topic.topic || keywords.join(' / ') || '').trim()
}

function flattenComments(comments) {
  if (Array.isArray(comments)) return comments
  if (!comments || typeof comments !== 'object') return []
  return Object.entries(comments).flatMap(([label, items]) => (
    Array.isArray(items)
      ? items.map((item) => ({ ...item, sentiment_label: item?.sentiment_label || label }))
      : []
  ))
}

function sentimentCards(sentiment) {
  if (!sentiment || typeof sentiment !== 'object') return []
  const total = numberOrNull(sentiment.total)
  const positive = numberOrNull(sentiment.positive)
  const neutral = numberOrNull(sentiment.neutral)
  const negative = numberOrNull(sentiment.negative)
  const calculatedRate = total && positive !== null ? positive / total : null
  const positiveRate = numberOrNull(sentiment.positive_rate) ?? calculatedRate
  return [
    ['样本总数', total],
    ['正向', positive],
    ['中性', neutral],
    ['负向', negative],
    ['正向占比', positiveRate === null ? null : formatSimilarity(positiveRate)]
  ].flatMap(([label, value]) => hasValue(value) ? [{ label, value }] : [])
}

function reputationContext(item, allEvidence, uiKey) {
  const sentiment = item?.evidence?.sentiment
  const cards = sentimentCards(sentiment)
  const sentimentDocument = allEvidence.find((evidence) => evidence.source_type === 'sentiment_summary') || null
  const topics = (Array.isArray(item?.evidence?.topics) ? item.evidence.topics : [])
    .map(topicText)
    .filter(Boolean)
    .map((text, index) => ({
      key: `${uiKey}-topic-${index}`,
      text,
      matchedEvidence: findContentEvidence(text, allEvidence, 'topic')
    }))

  let rawComments = flattenComments(item?.evidence?.comments)
  if (!rawComments.length) {
    rawComments = allEvidence
      .filter((evidence) => evidence.source_type === 'comment')
      .slice(0, 3)
      .map((evidence) => ({ ...evidence.metadata, content: evidence.content, doc_id: evidence.doc_id }))
  }
  const comments = rawComments
    .filter((comment) => String(comment?.content || '').trim())
    .map((comment, index) => {
      const directDocId = String(comment?.doc_id || '').trim()
      const directMatch = directDocId
        ? allEvidence.find((evidence) => evidence.doc_id === directDocId) || null
        : null
      const matchedEvidence = directMatch || findContentEvidence(comment.content, allEvidence, 'comment')
      return {
        key: `${uiKey}-comment-${index}`,
        content: String(comment.content).trim(),
        docId: matchedEvidence?.doc_id || '',
        matchedEvidence,
        sentimentLabel: comment.sentiment_label || matchedEvidence?.metadata?.sentiment_label || '',
        sentimentScore: comment.sentiment_score ?? matchedEvidence?.metadata?.sentiment_score,
        likes: comment.likes ?? matchedEvidence?.metadata?.likes,
        platform: comment.platform || matchedEvidence?.metadata?.platform || '',
        publishTime: comment.publish_time || matchedEvidence?.metadata?.publish_time || ''
      }
    })
  const linkedEvidence = []
  const linkedIds = new Set()
  for (const evidence of [sentimentDocument, ...topics.map((topic) => topic.matchedEvidence), ...comments.map((comment) => comment.matchedEvidence)]) {
    if (!evidence?.doc_id || linkedIds.has(evidence.doc_id)) continue
    linkedIds.add(evidence.doc_id)
    linkedEvidence.push(evidence)
  }

  return {
    sentimentCards: cards,
    sentimentDocument,
    topics,
    comments,
    linkedEvidence,
    hasContent: Boolean(cards.length || topics.length || comments.length)
  }
}

function formatItem(item, index) {
  const reason = String(item?.reason || '')
  const scoreMatch = reason.match(SCORE_PATTERN)
  const explicitIndex = item?.recommendation_index || item?.recommendation_score || item?.recommendationIndex
  const indexValue = explicitIndex || scoreMatch?.[1]
  const indexText = String(indexValue || '').trim()
  const reasonWithoutIndex = scoreMatch
    ? `${reason.slice(0, scoreMatch.index)}${reason.slice(scoreMatch.index + scoreMatch[0].length)}`
    : reason
  const { summary, details } = splitReason(reasonWithoutIndex)
  const uiKey = `${item?.anime_id ?? 'unknown'}-${index}`
  const context = evidenceContext(item)
  const reputation = reputationContext(item, context.allEvidence, uiKey)
  const matchTags = [...new Set(
    (Array.isArray(item?.match_tags) ? item.match_tags : [])
      .map((tag) => String(tag || '').trim())
      .filter(Boolean)
  )]
  if (reputation.hasContent && !matchTags.includes('口碑')) matchTags.push('口碑')
  if (
    (context.commentEvidence.length || context.missingEvidenceRefs.length) &&
    !matchTags.includes('评论证据')
  ) matchTags.push('评论证据')

  return {
    ...item,
    match_tags: matchTags,
    uiKey,
    panelId: `recommendation-evidence-${uiKey}`,
    recommendationIndex: indexText
      ? (/^(?:推荐指数|推荐评分|推荐星级)\s*[：:]/u.test(indexText) ? indexText : `推荐指数：${indexText}`)
      : '',
    reasonSummary: summary,
    reasonDetails: details,
    commentEvidence: context.commentEvidence,
    missingEvidenceRefs: context.missingEvidenceRefs,
    reputation
  }
}
</script>

<style scoped>
.recommend-result { display: block; }
.rec-list { display: flex; flex-direction: column; gap: 14px; }
.rec-item {
  position: relative;
  overflow: hidden;
  padding: 18px 20px;
  border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: 12px;
  background: linear-gradient(145deg, rgba(15, 23, 38, 0.9), rgba(8, 14, 25, 0.72));
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.14);
}
.rec-item::before {
  content: '';
  position: absolute;
  inset: 0 auto 0 0;
  width: 3px;
  background: linear-gradient(180deg, var(--neon-cyan), rgba(0, 229, 255, 0.08));
}
.rec-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.title-line { min-width: 0; display: flex; flex-wrap: wrap; align-items: baseline; gap: 7px 14px; }
.title-line h3, .recommendation-index {
  color: var(--text-primary);
  font-size: 17px;
  font-weight: 700;
  line-height: 1.45;
  letter-spacing: 0.15px;
}
.recommendation-index { white-space: nowrap; }
.platform {
  flex-shrink: 0;
  margin-left: auto;
  padding: 3px 8px;
  border: 1px solid rgba(0, 229, 255, 0.18);
  border-radius: 999px;
  background: rgba(0, 229, 255, 0.06);
  color: var(--neon-cyan);
  font-family: var(--font-mono);
  font-size: 10px;
  line-height: 1.4;
}
.reason-block { margin-top: 15px; }
.section-label {
  display: block;
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 1px;
}
.reason { margin-top: 6px; color: var(--text-secondary); font-size: 13px; line-height: 1.85; }
.reason-details { display: grid; gap: 8px; margin-top: 14px; }
.reason-detail {
  display: grid;
  grid-template-columns: 92px minmax(0, 1fr);
  gap: 12px;
  padding: 9px 11px;
  border: 1px solid rgba(255, 255, 255, 0.055);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.025);
}
.reason-detail dt { color: var(--neon-cyan); font-size: 12px; font-weight: 600; line-height: 1.65; }
.reason-detail dd { min-width: 0; margin: 0; color: var(--text-secondary); font-size: 13px; line-height: 1.65; overflow-wrap: anywhere; }
.rec-footer { display: flex; align-items: center; gap: 12px; margin-top: 15px; padding-top: 13px; border-top: 1px solid rgba(255, 255, 255, 0.06); }
.tags { min-width: 0; display: flex; align-items: center; gap: 12px; }
.tags > .section-label { flex-shrink: 0; }
.tag-list { display: flex; flex-wrap: wrap; gap: 7px; }
.tag-label,
.tag-button {
  padding: 3px 9px;
  border: 1px solid rgba(0, 229, 255, 0.18);
  border-radius: 6px;
  background: rgba(0, 229, 255, 0.06);
  color: var(--neon-cyan);
  font: inherit;
  font-size: 11px;
  line-height: 1.45;
}
.tag-button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  transition: border-color 0.18s ease, background 0.18s ease, box-shadow 0.18s ease;
}
.tag-button span { padding: 0; border: 0; background: transparent; font-size: 13px; line-height: 1; }
.tag-button:hover,
.tag-button.active {
  border-color: rgba(0, 229, 255, 0.5);
  background: rgba(0, 229, 255, 0.13);
  box-shadow: 0 0 14px rgba(0, 229, 255, 0.08);
}
.tag-button:focus-visible,
.panel-close:focus-visible {
  outline: 2px solid var(--neon-cyan);
  outline-offset: 2px;
}
.evidence-panel {
  margin-top: 14px;
  padding: 15px;
  border: 1px solid rgba(0, 229, 255, 0.16);
  border-radius: 10px;
  background: rgba(3, 10, 20, 0.62);
}
.evidence-panel-head,
.subsection-head,
.retrieval-head,
.comment-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.panel-kicker {
  color: var(--neon-cyan);
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 1.2px;
}
.evidence-panel h4 {
  margin-top: 2px;
  color: var(--text-primary);
  font-size: 14px;
  line-height: 1.5;
}
.panel-close {
  width: 28px;
  height: 28px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 7px;
  background: rgba(255, 255, 255, 0.04);
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 18px;
  line-height: 1;
}
.panel-close:hover { border-color: rgba(0, 229, 255, 0.35); color: var(--neon-cyan); }
.panel-description,
.evidence-empty,
.evidence-warning,
.missing-doc,
.source-label {
  color: var(--text-muted);
  font-size: 11px;
  line-height: 1.65;
}
.panel-description { margin-top: 9px; }
.evidence-empty {
  margin-top: 12px;
  padding: 11px 12px;
  border: 1px dashed rgba(255, 255, 255, 0.11);
  border-radius: 8px;
}
.evidence-warning {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 5px;
  margin-top: 10px;
  color: var(--neon-amber);
}
.evidence-warning code,
.subsection-head code,
.topic-row code,
.retrieval-head code,
.comment-head code {
  color: rgba(177, 227, 238, 0.78);
  font-family: var(--font-mono);
  font-size: 9px;
  overflow-wrap: anywhere;
}
.reputation-section { margin-top: 14px; }
.reputation-section + .reputation-section {
  padding-top: 14px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
}
.reputation-section h5 { color: var(--text-primary); font-size: 12px; line-height: 1.5; }
.sentiment-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 7px;
  margin-top: 9px;
}
.sentiment-stat {
  padding: 9px;
  border: 1px solid rgba(255, 255, 255, 0.065);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.025);
}
.sentiment-stat span { display: block; color: var(--text-muted); font-size: 9px; }
.sentiment-stat strong { display: block; margin-top: 3px; color: var(--text-primary); font-size: 13px; }
.topic-list,
.representative-list,
.retrieval-list { display: grid; gap: 8px; margin-top: 9px; }
.topic-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(110px, auto);
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 7px;
  background: rgba(255, 255, 255, 0.025);
}
.topic-row > span { min-width: 0; color: var(--text-secondary); font-size: 11px; line-height: 1.6; overflow-wrap: anywhere; }
.topic-row small { color: var(--text-muted); font-size: 9px; text-align: right; }
.representative-comment,
.retrieval-card {
  min-width: 0;
  padding: 11px 12px;
  border: 1px solid rgba(255, 255, 255, 0.07);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.025);
}
.comment-head > span,
.retrieval-head > span {
  flex-shrink: 0;
  color: var(--neon-cyan);
  font-size: 10px;
  font-weight: 600;
}
.comment-head code,
.retrieval-head code { min-width: 0; text-align: right; }
.representative-comment > p:not(.missing-doc),
.retrieval-card > p:not(.source-label) {
  margin-top: 8px;
  color: var(--text-secondary);
  font-size: 11px;
  line-height: 1.7;
  overflow-wrap: anywhere;
}
.comment-facts,
.metric-list,
.metadata-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
.comment-facts > span,
.metric-list > span,
.metadata-list > span {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  padding: 3px 7px;
  border-radius: 5px;
  background: rgba(255, 255, 255, 0.04);
  color: var(--text-secondary);
  font-size: 9px;
  line-height: 1.45;
}
.metric-list small,
.metadata-list small { color: var(--text-muted); font-size: 8px; }
.missing-doc { margin-top: 8px; color: var(--neon-amber); }
.source-label { margin-top: 7px; }
.empty { color: var(--text-secondary); font-size: 13px; line-height: 1.7; }
@media (max-width: 560px) {
  .rec-item { padding: 15px 14px 15px 16px; }
  .rec-head { gap: 10px; }
  .title-line h3, .recommendation-index { font-size: 15px; }
  .reason-detail { grid-template-columns: 1fr; gap: 3px; }
  .rec-footer { align-items: flex-start; flex-wrap: wrap; }
  .tags { align-items: flex-start; flex-direction: column; gap: 8px; }
  .platform { margin-left: auto; }
  .evidence-panel { margin-top: 12px; padding: 12px; }
  .sentiment-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .topic-row { grid-template-columns: 1fr; gap: 4px; }
  .topic-row small,
  .topic-row code { text-align: left; }
  .retrieval-head,
  .comment-head { align-items: flex-start; flex-direction: column; gap: 4px; }
  .retrieval-head code,
  .comment-head code { text-align: left; }
}
</style>

