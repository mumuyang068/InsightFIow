<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { MethodRun } from '@/api/cbiMethods'
const props = defineProps<{run: MethodRun; rows: Record<string, unknown>[]}>()
const {t}=useI18n()
const tr=(key:string)=>t('methodOps.'+key)
type EventRow = {event:string; t:number; id:string; observed_at?:string}
type Order = {id:string; t:number; channel?:string}
const funnel=computed(()=>props.run.definition.plan.unit==='attempt')
const epoch=computed(()=>props.run.display?.epoch ??
  (props.run.definition.plan.read_strategy==='observed_events' &&
  /^\d{4}-\d{2}-\d{2}$/.test(props.run.definition.plan.entry_start ?? '') ? props.run.definition.plan.entry_start : undefined))
const times=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'})
function time(event?:EventRow) {
  if(!event) return '—'
  if(event.observed_at) return times.format(new Date(event.observed_at))
  if(epoch.value) {
    const start=Date.parse(epoch.value+'T00:00:00+08:00')
    return times.format(start+1000*event.t)
  }
  return event.t+' s'
}
function events(row:Record<string,unknown>,stage:string) {
  const events=(row.observed_events ?? []) as EventRow[]
  const chosen=(row.strict_path as [string,number][] | undefined)?.find(x=>x[0]===stage)
  return events.find(x=>x.event===stage && x.t===chosen?.[1]) ?? events.find(x=>x.event===stage)
}
function funnelStatus(row:Record<string,unknown>) {
  if(row.ambiguous) return tr('sequence')
  if(!row.mature_by_time) return tr('observing')
  if(Number(row.reached)===3) return tr('completed')
  const payment=events(row,'pay')
  if(payment && payment.t>Number(row.deadline)) {
    const hours=(payment.t-Number(row.anchor))/3600
    return t('methodOps.over_window',{hours:Number(hours.toFixed(2)),window:props.run.definition.plan.window_hours})
  }
  if(!events(row,'cart')) return tr('missing_cart')
  return payment ? tr('sequence_excluded') : tr('missing_pay')
}
const order=(x?:Order)=>x?times.format(x.t*1000)+' · '+x.id:'—'
function repeatStatus(row:Record<string,unknown>) {
  if(!row.in_cohort) return tr('exclude_'+String(row.exclusion))
  if(!row.mature) return tr('observing')
  return (row.repeat_orders as Order[]).length ? tr('repeated') : tr('not_repeated')
}
</script>
<template>
  <div class="members-table" tabindex="0" :aria-label="tr('member_table')">
    <table>
      <thead><tr v-if="funnel"><th>{{ tr('attempt') }}</th><th>{{ t('methods.view') }}</th><th>{{ t('methods.cart') }}</th><th>{{ t('methods.pay') }}</th><th>{{ tr('observation_status') }}</th><th>{{ tr('reason') }}</th></tr>
      <tr v-else><th>{{ tr('customer') }}</th><th>{{ tr('first_order') }}</th><th>{{ t('methods.cohort_channel') }}</th><th>{{ tr('repeat_orders') }}</th><th>{{ t('methods.repeat_channel') }}</th><th>{{ tr('reason') }}</th></tr></thead>
      <tbody v-if="funnel"><tr v-for="row in rows" :key="String(row.attempt)">
        <th scope="row">{{ row.attempt }}</th>
        <td v-for="stage in ['view','cart','pay']" :key="stage">
          <span class="event-time">{{ time(events(row,stage)) }}</span><small v-if="events(row,stage)">{{ events(row,stage)?.id }}</small>
        </td>
        <td>{{ row.mature_by_time ? tr('mature') : tr('observing') }}</td>
        <td>{{ funnelStatus(row) }}</td>
      </tr></tbody>
      <tbody v-else><tr v-for="row in rows" :key="String(row.customer)">
        <th scope="row">{{ row.customer }}</th><td>{{ order(row.first_order as Order | undefined) }}</td>
        <td>{{ (row.first_order as Order | undefined)?.channel ?? '—' }}</td>
        <td>{{ (row.repeat_orders as Order[]).map(order).join('、') || '—' }}</td>
        <td>{{ (row.repeat_orders as Order[]).map(r=>r.channel ?? '—').join('、') || '—' }}</td>
        <td>{{ repeatStatus(row) }}</td>
      </tr></tbody>
    </table>
    <p>{{ epoch || !funnel ? 'Asia/Shanghai' : tr('relative_seconds') }}</p>
  </div>
</template>
<style scoped>
.members-table { overflow-x:auto; max-width:100%; margin-block:8px; }
table { border-collapse:collapse; width:100%; font-size:13px; }
th,td { padding:8px; border-bottom:1px solid #d8dedf; text-align:left; vertical-align:top; }
thead { background:#eff6f4; }
small { display:block; color:#536368; }
.event-time { overflow-wrap:normal; word-break:normal; }
@media (max-width:850px) { th,td { padding:6px 5px; } }
</style>
