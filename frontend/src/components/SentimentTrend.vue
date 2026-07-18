<template>
  <div class="trend-wrapper">
    <div ref="chartRef" class="trend-chart"></div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  scatter: { type: Array, default: () => [] }
})

const chartRef = ref(null)
let chart = null

function renderChart() {
  if (!chart) return

  const data = props.scatter
  if (data.length === 0) {
    chart.setOption({
      backgroundColor: 'transparent',
      graphic: [{
        type: 'text',
        left: 'center', top: 'middle',
        style: { text: '暂无数据', fill: '#4a5568', fontSize: 13, fontFamily: 'JetBrains Mono' }
      }]
    })
    return
  }

  // 每条评论的情感值，颜色由值的正负决定
  const values = data.map(d => d.value)
  const indices = data.map(d => d.index)

  // 分段着色：正面绿/青，负面红/粉，中性灰
  const coloredData = values.map((v, i) => ({
    value: v,
    itemStyle: {
      color: v > 0.05
        ? 'rgba(0, 229, 160, 0.9)'
        : v < -0.05
          ? 'rgba(255, 77, 106, 0.9)'
          : 'rgba(139, 149, 165, 0.7)'
    }
  }))

  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line', lineStyle: { color: 'rgba(0,229,255,0.3)', width: 1 } },
      backgroundColor: 'rgba(10, 14, 26, 0.95)',
      borderColor: 'rgba(0, 229, 255, 0.2)',
      borderWidth: 1,
      padding: [8, 12],
      textStyle: { color: '#e8ecf1', fontSize: 12, fontFamily: 'JetBrains Mono' },
      formatter(params) {
        const p = params[0]
        const idx = p.dataIndex
        const v = values[idx]
        const label = data[idx]?.label || ''
        const labelText = label === 'positive' ? '正面' : label === 'negative' ? '负面' : '中性'
        const color = v > 0.05 ? '#00e5a0' : v < -0.05 ? '#ff4d6a' : '#8b95a5'
        return `<span style="color:#7a8599">评论 #${idx + 1}</span><br/>
                <span style="color:${color}">${labelText}</span>
                <span style="color:#e8ecf1;margin-left:8px">${v.toFixed(3)}</span>`
      }
    },
    grid: { left: 52, right: 16, top: 20, bottom: 48 },
    xAxis: {
      type: 'category',
      data: indices,
      axisLabel: {
        fontSize: 10,
        color: '#4a5568',
        fontFamily: 'JetBrains Mono',
        interval: Math.floor(data.length / 6)
      },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } },
      axisTick: { show: false },
      name: 'Number',
      nameLocation: 'middle',
      nameGap: 32,
      nameTextStyle: { color: '#4a5568', fontSize: 11, fontFamily: 'JetBrains Mono' }
    },
    yAxis: {
      type: 'value',
      min: -0.55,
      max: 0.55,
      interval: 0.25,
      axisLabel: {
        fontSize: 10,
        color: '#4a5568',
        fontFamily: 'JetBrains Mono',
        formatter: v => v.toFixed(2)
      },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.04)', type: 'dashed' } },
      axisLine: { show: false },
      axisTick: { show: false },
      name: 'Sentiment',
      nameLocation: 'middle',
      nameGap: 40,
      nameTextStyle: { color: '#4a5568', fontSize: 11, fontFamily: 'JetBrains Mono' }
    },
    // 零线标记
    markLine: { silent: true },
    dataZoom: [
      { type: 'inside', start: 0, end: 100 },
      {
        type: 'slider',
        height: 18,
        bottom: 4,
        borderColor: 'rgba(255,255,255,0.06)',
        backgroundColor: 'rgba(255,255,255,0.03)',
        fillerColor: 'rgba(0,229,255,0.08)',
        handleStyle: { color: '#00e5ff', borderColor: 'transparent' },
        textStyle: { color: '#4a5568', fontSize: 9 },
        labelFormatter: (v) => `#${Math.round(v) + 1}`
      }
    ],
    series: [
      {
        type: 'line',
        data: coloredData,
        symbol: 'none',
        lineStyle: { width: 1.2, color: '#8b95a5' },
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: { color: 'rgba(255,255,255,0.15)', type: 'solid', width: 1 },
          data: [{ yAxis: 0 }],
          label: { show: false }
        }
      }
    ]
  }, true)
}

const resizeHandler = () => chart?.resize()

onMounted(() => {
  chart = echarts.init(chartRef.value)
  renderChart()
  window.addEventListener('resize', resizeHandler)
})

onUnmounted(() => {
  window.removeEventListener('resize', resizeHandler)
  chart?.dispose()
})

watch(() => props.scatter, renderChart, { deep: true })
</script>

<style scoped>
.trend-wrapper {
  padding: 12px 16px 16px;
}
.trend-chart {
  width: 100%;
  height: 320px;
}
</style>

