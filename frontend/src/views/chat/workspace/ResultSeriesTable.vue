<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { AnalysisTable } from '@/api/analysis'
import { resultNumber } from './resultFormat'

defineProps<{
  rows: { label: string | null; value: number | null }[]
  trend: boolean
  table?: AnalysisTable
}>()
const { t, locale } = useI18n()
</script>

<template>
  <div v-if="table" class="table-viewport">
    <table v-if="table.rows.length" class="series-table semantic-table">
      <thead><tr>
        <th v-for="column in table.columns" :key="column.key" scope="col">{{ column.label }}</th>
      </tr></thead>
      <tbody>
        <tr v-for="(row, index) in table.rows" :key="index">
          <td v-for="column in table.columns" :key="column.key">
            {{ row[column.key] == null ? '—' :
              column.numeric || (typeof row[column.key] === 'number' && !column.key.endsWith('_id')) ?
                resultNumber(Number(row[column.key]), locale, false, column.precision) : row[column.key] }}
          </td>
        </tr>
      </tbody>
    </table>
    <p v-else>{{ t('cbi.reason.missing_data') }}</p>
  </div>
  <table v-else class="series-table">
    <thead>
      <tr>
        <th scope="col">{{ t(trend ? 'cbi.period' : 'cbi.category') }}</th>
        <th scope="col">{{ t('cbi.series_amount') }} ({{ t('cbi.yuan') }})</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="(row, index) in rows" :key="index">
        <td>{{ row.label ?? t('cbi.unknown_category') }}</td>
        <td>{{ row.value == null ? t('cbi.reason.missing_data') : resultNumber(row.value, locale) }}</td>
      </tr>
    </tbody>
  </table>
</template>

<style scoped>
.table-viewport { max-width: 100%; overflow: auto; }
.semantic-table { table-layout: auto !important; }
.series-table { width: 100%; border-collapse: collapse; text-align: left; table-layout: fixed; }
.series-table td, .series-table th { padding: 8px 10px; border-bottom: 1px solid #dee0e3; overflow-wrap: anywhere; }
.series-table td:last-child, .series-table th:last-child { text-align: right; font-variant-numeric: tabular-nums; }
</style>
