<script setup lang="ts">
import { computed } from 'vue'
import type { AnalysisQuality } from '@/api/analysis'

const props = defineProps<{ quality?: AnalysisQuality }>()
const notice = computed(() => {
  const status = props.quality?.status
  return status && ['warning', 'blocked', 'partial', 'incomplete'].includes(status) ? status : ''
})
const affected = computed(() => props.quality?.unavailable_outputs.map(({ label }) => label).join(' / '))
</script>

<template>
  <p v-if="notice" class="quality-notice" data-testid="analysis-quality-notice" role="status">
    {{ $t('themeQuality.notice.' + notice) }}
    {{ affected ? $t('themeQuality.affectedOutputs', { outputs: affected }) : '' }}
  </p>
</template>

<style scoped>
.quality-notice {
  margin: 8px 0;
  padding: 8px 12px;
  border-radius: 6px;
  background: #fff7e9;
  color: #684515;
  overflow-wrap: anywhere;
}
</style>
