<template>
  <div class="recommend-card">
    <!-- 头部：动漫名 + 标签 -->
    <div class="card-header">
      <span class="anime-name">{{ data.name }}</span>
      <span class="platform-badge" :class="data.platform">
        {{ data.platform === 'bilibili' ? 'B站' : 'Bangumi' }}
      </span>
      <span v-if="data.bangumi_rating" class="rating-badge">
        ★ {{ Number(data.bangumi_rating).toFixed(1) }}
      </span>
    </div>

    <!-- Bangumi 简介 -->
    <p v-if="data.description" class="description">{{ data.description }}</p>
    <p v-else class="description no-desc">暂无简介数据</p>

    <!-- 三维情感分析 -->
    <div class="aspects">
      <div
        v-for="(aspect, label) in data.aspect_sentiment"
        :key="label"
        class="aspect-row"
      >
        <span class="aspect-label">{{ label }}</span>
        <div class="bar-wrap">
          <template v-if="aspect.total > 0">
            <div
              class="bar-seg positive"
              :style="{ width: pct(aspect.positive, aspect.total) }"
              :title="`正面 ${aspect.positive} 条`"
            />
            <div
              class="bar-seg neutral"
              :style="{ width: pct(aspect.neutral, aspect.total) }"
              :title="`中性 ${aspect.neutral} 条`"
            />
            <div
              class="bar-seg negative"
              :style="{ width: pct(aspect.negative, aspect.total) }"
              :title="`负面 ${aspect.negative} 条`"
            />
          </template>
          <div v-else class="bar-seg empty" style="width:100%" />
        </div>
        <span class="aspect-count">
          {{ aspect.total > 0 ? `${aspect.total}条` : '无数据' }}
        </span>
      </div>
    </div>

    <!-- 图例 -->
    <div class="legend">
      <span class="legend-dot positive" />正面
      <span class="legend-dot neutral" />中性
      <span class="legend-dot negative" />负面
    </div>

    <!-- 跳转按钮 -->
    <router-link
      v-if="data.anime_id"
      :to="`/dashboard/${data.anime_id}`"
      class="detail-btn"
      target="_blank"
    >
      查看完整分析 →
    </router-link>
  </div>
</template>

<script setup>
defineProps({
  data: {
    type: Object,
    required: true,
  },
})

function pct(val, total) {
  if (!total) return '0%'
  return (val / total * 100).toFixed(1) + '%'
}
</script>

<style scoped>
.recommend-card {
  background: rgba(17, 24, 39, 0.9);
  border: 1px solid rgba(0, 229, 255, 0.25);
  border-radius: 10px;
  padding: 14px 16px;
  margin-top: 6px;
  font-size: 13px;
}

/* 头部 */
.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.anime-name {
  font-size: 15px;
  font-weight: 700;
  color: var(--neon-cyan, #00e5ff);
  letter-spacing: 0.5px;
}
.platform-badge {
  font-size: 11px;
  padding: 2px 7px;
  border-radius: 4px;
  font-weight: 600;
}
.platform-badge.bilibili {
  background: rgba(0, 161, 214, 0.2);
  color: #00a1d6;
  border: 1px solid rgba(0, 161, 214, 0.4);
}
.platform-badge.bangumi {
  background: rgba(255, 119, 119, 0.2);
  color: #f09199;
  border: 1px solid rgba(255, 119, 119, 0.4);
}
.rating-badge {
  font-size: 12px;
  color: #ffd700;
  background: rgba(255, 215, 0, 0.1);
  border: 1px solid rgba(255, 215, 0, 0.3);
  padding: 2px 7px;
  border-radius: 4px;
}

/* 简介 */
.description {
  color: #9ca3af;
  line-height: 1.6;
  margin-bottom: 12px;
  font-size: 12px;
}
.no-desc {
  color: #4b5563;
  font-style: italic;
}

/* 三维情感条 */
.aspects {
  display: flex;
  flex-direction: column;
  gap: 7px;
  margin-bottom: 8px;
}
.aspect-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.aspect-label {
  width: 28px;
  font-size: 12px;
  color: #d1d5db;
  flex-shrink: 0;
  text-align: right;
}
.bar-wrap {
  flex: 1;
  height: 10px;
  border-radius: 5px;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.05);
  display: flex;
}
.bar-seg {
  height: 100%;
  transition: width 0.4s ease;
}
.bar-seg.positive { background: var(--color-positive, #00e5a0); }
.bar-seg.neutral  { background: var(--color-neutral, #8b95a5); }
.bar-seg.negative { background: var(--color-negative, #ff4d6a); }
.bar-seg.empty    { background: rgba(255, 255, 255, 0.06); }
.aspect-count {
  font-size: 11px;
  color: #6b7280;
  width: 36px;
  flex-shrink: 0;
}

/* 图例 */
.legend {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 11px;
  color: #6b7280;
  margin-bottom: 12px;
}
.legend-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 3px;
}
.legend-dot.positive { background: var(--color-positive, #00e5a0); }
.legend-dot.neutral  { background: var(--color-neutral, #8b95a5); }
.legend-dot.negative { background: var(--color-negative, #ff4d6a); }

/* 详情按钮 */
.detail-btn {
  display: inline-block;
  font-size: 12px;
  color: var(--neon-cyan, #00e5ff);
  text-decoration: none;
  border: 1px solid rgba(0, 229, 255, 0.3);
  border-radius: 5px;
  padding: 4px 10px;
  transition: background 0.2s, border-color 0.2s;
}
.detail-btn:hover {
  background: rgba(0, 229, 255, 0.08);
  border-color: var(--neon-cyan, #00e5ff);
}
</style>
