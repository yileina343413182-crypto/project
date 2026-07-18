<template>
  <div class="cloud-wrapper">
    <div ref="chartRef" class="cloud-chart"></div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, watch } from 'vue'
import * as echarts from 'echarts'
import 'echarts-wordcloud'

const props = defineProps({
  words: { type: Array, default: () => [] }
})

const emit = defineEmits(['word-click'])

const chartRef = ref(null)
let chart = null

const neonPalette = [
  '#00e5ff', '#00e5a0', '#ff4d8d', '#ffb547',
  '#b8ff57', '#8b5cf6', '#0ea5e9', '#f472b6',
  '#38bdf8', '#a78bfa', '#34d399', '#fb923c'
]

function renderChart() {
  if (!chart || props.words.length === 0) return

  const data = props.words.map((w, i) => ({
    name: w.word,
    value: w.count,
    textStyle: {
      color: neonPalette[i % neonPalette.length],
      textShadowColor: neonPalette[i % neonPalette.length],
      textShadowBlur: 6
    }
  }))

  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      show: true,
      backgroundColor: 'rgba(17, 24, 39, 0.95)',
      borderColor: 'rgba(255, 255, 255, 0.08)',
      textStyle: { color: '#e8ecf1', fontSize: 12 },
      formatter: ({ name, value }) => `<strong>${name}</strong>：${value}次`
    },
    series: [{
      type: 'wordCloud',
      shape: 'circle',
      gridSize: 10,
      sizeRange: [14, 52],
      rotationRange: [-25, 25],
      rotationStep: 15,
      left: 'center',
      top: 'center',
      width: '88%',
      height: '88%',
      drawOutOfBound: false,
      textStyle: {
        fontFamily: 'Noto Sans SC, sans-serif',
        fontWeight: '700'
      },
      emphasis: {
        textStyle: {
          textShadowBlur: 16,
          fontSize: 24
        }
      },
      data
    }]
  })

  chart.off('click')
  chart.on('click', (params) => {
    if (params.name) emit('word-click', params.name)
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

watch(() => props.words, renderChart, { deep: true })
</script>

<style scoped>
.cloud-wrapper {
  padding: 12px 16px 16px;
}
.cloud-chart {
  width: 100%;
  height: 320px;
}
</style>
