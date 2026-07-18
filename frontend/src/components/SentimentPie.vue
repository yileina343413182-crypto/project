<template>
  <div class="pie-wrapper">
    <div ref="chartRef" class="pie-chart"></div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  stats: { type: Object, default: () => ({}) }
})

const chartRef = ref(null)
let chart = null

function renderChart() {
  if (!chart || !props.stats.total) return

  const { positive = 0, negative = 0, neutral = 0, total = 0 } = props.stats

  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      backgroundColor: 'rgba(17, 24, 39, 0.95)',
      borderColor: 'rgba(255, 255, 255, 0.08)',
      textStyle: { color: '#e8ecf1', fontSize: 13, fontFamily: 'Noto Sans SC' },
      formatter: ({ name, value, percent }) =>
        `<strong>${name}</strong><br/>数量：${value}<br/>占比：${percent}%`
    },
    legend: {
      bottom: 8,
      itemWidth: 10,
      itemHeight: 10,
      itemGap: 20,
      textStyle: { color: '#7a8599', fontSize: 12, fontFamily: 'JetBrains Mono' }
    },
    graphic: [
      {
        type: 'text',
        left: 'center',
        top: '35%',
        style: {
          text: `${total}`,
          fontSize: 30,
          fontWeight: 700,
          fontFamily: 'Orbitron',
          fill: '#e8ecf1',
          textAlign: 'center'
        }
      },
      {
        type: 'text',
        left: 'center',
        top: '48%',
        style: {
          text: 'TOTAL',
          fontSize: 9,
          fontFamily: 'Orbitron',
          fill: '#4a5568',
          textAlign: 'center',
          letterSpacing: 3
        }
      }
    ],
    series: [{
      type: 'pie',
      radius: ['52%', '74%'],
      center: ['50%', '44%'],
      avoidLabelOverlap: true,
      itemStyle: {
        borderRadius: 6,
        borderColor: '#111827',
        borderWidth: 3
      },
      label: { show: false },
      emphasis: {
        label: {
          show: true,
          fontSize: 13,
          fontWeight: 'bold',
          color: '#e8ecf1'
        },
        itemStyle: {
          shadowBlur: 20,
          shadowColor: 'rgba(0,0,0,0.3)'
        }
      },
      data: [
        { value: positive, name: '正面', itemStyle: { color: new echarts.graphic.LinearGradient(0, 0, 1, 1, [{ offset: 0, color: '#00e5a0' }, { offset: 1, color: '#00b87a' }]) } },
        { value: negative, name: '负面', itemStyle: { color: new echarts.graphic.LinearGradient(0, 0, 1, 1, [{ offset: 0, color: '#ff4d6a' }, { offset: 1, color: '#d63050' }]) } },
        { value: neutral, name: '中性', itemStyle: { color: new echarts.graphic.LinearGradient(0, 0, 1, 1, [{ offset: 0, color: '#8b95a5' }, { offset: 1, color: '#636d7e' }]) } }
      ]
    }]
  })
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

watch(() => props.stats, renderChart, { deep: true })
</script>

<style scoped>
.pie-wrapper {
  padding: 12px 16px 16px;
}
.pie-chart {
  width: 100%;
  height: 320px;
}
</style>
