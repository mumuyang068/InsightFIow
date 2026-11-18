<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { ResultView } from '@/api/analysis'

defineProps<{
  view: ResultView
  chart?: boolean
  overview?: boolean
  data?: boolean
  evidence?: boolean
}>()
const emit = defineEmits<{ select: [view: ResultView] }>()
const { t } = useI18n()
</script>

<template>
  <div class="result-views" role="group" :aria-label="t('resultExplorer.views')">
    <button
      v-if="chart"
      type="button"
      data-testid="result-view-chart"
      :aria-pressed="view === 'chart'"
      @click="emit('select', 'chart')"
    >
      {{ t(overview ? 'resultExplorer.overview' : 'resultExplorer.chart') }}
    </button>
    <button
      v-if="data"
      type="button"
      data-testid="result-view-data"
      :aria-pressed="view === 'data'"
      :aria-label="t('cbiWorkspace.resultsTable')"
      @click="emit('select', 'data')"
    >
      {{ t('resultExplorer.data') }}
    </button>
    <button
      v-if="evidence"
      type="button"
      data-testid="result-view-evidence"
      :aria-pressed="view === 'evidence'"
      :aria-label="t('cbiWorkspace.inspect')"
      @click="emit('select', 'evidence')"
    >
      {{ t('resultExplorer.evidence') }}
    </button>
  </div>
</template>

<style scoped>
.result-views {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 3px;
  width: fit-content;
  padding: 3px;
  border: 1px solid #dfe5e2;
  border-radius: 8px;
}
.result-views button {
  background: transparent;
  border: 0;
  color: #3e4a45;
  border-radius: 5px;
  padding: 5px 10px;
  min-height: 30px;
  cursor: pointer;
  font: inherit;
}
.result-views button:hover,
.result-views button[aria-pressed='true'] {
  background: #eaf4ee;
  color: #17634d;
}
.result-views button[aria-pressed='true'] {
  font-weight: 600;
}
.result-views button:focus-visible {
  outline: 2px solid #25826b;
  outline-offset: 2px;
}
</style>
