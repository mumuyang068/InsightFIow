<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { methodConfig, type BindingForm, type BindingVersion, type Feedback } from '@/api/cbiMethods'
import ResultCard from '@/views/chat/workspace/ResultCard.vue'
import ResultInspector from '@/views/chat/workspace/ResultInspector.vue'
const {t}=useI18n()
const tr=(key:string)=>t('methodOps.'+key)
const props=defineProps<{area:'config'|'feedback'}>()
const versions=ref<BindingVersion[]>([])
const sources=ref<{id:number;name:string}[]>([])
const cases=ref<Feedback[]>([])
const selection=ref('')
const finding=ref('')
const note=ref('')
const comparable=ref('')
const category=ref('condition_difference')
const issue=ref('')
const busy=ref(false)
const base=ref(0)
const editing=ref(false)
const form=ref<BindingForm>({id:'',label:'',datasource:0,view:'',store:'',entry_start:'',entry_end:'',observation_asof:'',window_hours:24})
const selected=computed(()=>cases.value.find(f=>f.id===selection.value))
async function load(){
  if(props.area==='config'){
    const a=await methodConfig.list()
    versions.value=a.versions
    sources.value=a.sources
    if(!form.value.datasource) form.value.datasource=a.sources[0]?.id ?? 0
  }else cases.value=await methodConfig.inbox()
}
async function handle(operation:()=>Promise<void>){
  if(busy.value) return
  busy.value=true
  issue.value=''
  try{await operation()}catch(cause){
    const value=(cause as {response?:{data?:string|{code?:string}}}).response?.data
    issue.value=typeof value==='string'?value:value?.code ?? tr('request_error')
  }finally{busy.value=false}
}
function edit(v?:BindingVersion){
  form.value = v ? {...v.config} : {id:'',label:'',datasource:sources.value[0]?.id ?? 0,view:'',store:'',entry_start:'',entry_end:'',observation_asof:'',window_hours:24}
  base.value = v?.active ?? 0
  editing.value = true
}
function save(){return handle(async()=>{const v=await methodConfig.save(form.value,base.value);finding.value='v'+v.version+' · '+tr('candidate');editing.value=false;await load()})}
function step(v:BindingVersion,action:string){return handle(async()=>{
  const next=await methodConfig.step(v,action)
  finding.value='v'+next.version+' · '+(next.published_at?tr('active'):tr(next.check?.status??'candidate'))
  await load()
})}
function process(status:string){return handle(async()=>{
  const item=selected.value
  if(!item) return
  const checked=item.comparisons?.find(x=>x.run_id===comparable.value)
  await methodConfig.process(item.id,{
    status, revision:item.revision,request_id:crypto.randomUUID(),note:note.value,category:status==='resolved'?category.value:undefined,
    comparison_chat:checked?.chat_id,comparison_run:checked?.run_id,
  })
  finding.value=tr(status)
  await load()
})}
function details(version:BindingVersion){
 const d=version.check?.detail
 return typeof d==='string'?d:d?[d.code,...(d.fields??[])].join(' · '):''
}
onMounted(()=>handle(load))
</script>
<template>
<section class="method-admin" :aria-label="tr(area==='config'?'configuration':'feedback')">
 <p v-if="finding" role="status">{{ finding }}</p><p v-if="issue" role="alert">{{ issue }}</p>
 <template v-if="area==='config'">
 <el-button type="primary" :disabled="busy" @click="edit()">{{ t('cbiWorkspace.newCandidate') }}</el-button>
 <ResultInspector v-model="editing" :title="tr('configuration')">
 <p v-if="issue" role="alert">{{ issue }}</p>
 <form @submit.prevent="save">
  <label>{{ tr('binding_id') }}<input v-model="form.id" required maxlength="48" /></label>
  <label>{{ tr('binding_name') }}<input v-model="form.label" required maxlength="100" /></label>
  <label>{{ tr('datasource') }}<select v-model.number="form.datasource" required><option v-for="item in sources" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
  <label>{{ tr('event_view') }}<input v-model="form.view" required /></label>
  <label>{{ tr('store') }}<input v-model="form.store" required /></label>
  <label>{{ t('methods.entry_start') }}<input v-model="form.entry_start" type="date" required /></label>
  <label>{{ t('methods.entry_end') }}<input v-model="form.entry_end" type="date" required /></label>
  <label>{{ t('methods.observation') }}<input v-model="form.observation_asof" type="date" required /></label>
  <label>{{ tr('default_window') }}<select v-model.number="form.window_hours"><option :value="24">24 h</option><option :value="48">48 h</option></select></label>
  <p>{{ tr('base') }} {{ base || '—' }} · Asia/Shanghai · {{ t('methods.view') }} → {{ t('methods.cart') }} → {{ t('methods.pay') }}</p>
  <el-button type="primary" native-type="submit" :disabled="busy">{{ tr('save_candidate') }}</el-button>
 </form>
 </ResultInspector>
 <ResultCard v-for="v in versions" :key="v.id+'-'+v.version" :data-binding-version="v.version"
   :title="v.config.label + ' · v' + v.version"
   :status="v.active===v.version ? tr('active') : v.published_at ? tr('historic') : tr('candidate')">
  <p>{{ v.config.view }} · {{ v.config.store }} · {{ v.config.window_hours }} h · {{ v.check ? tr(v.check.status) : tr('unchecked') }} {{ details(v) }}</p>
  <template #actions>
    <el-button text @click="edit(v)">{{ tr('copy_version') }}</el-button>
    <el-button v-if="!v.published_at" text :disabled="busy" @click="step(v,'check')">{{ tr('check') }}</el-button>
    <el-button v-if="!v.published_at" :disabled="busy || v.check?.status!=='passed'" @click="step(v,'activate')">{{ tr('activate') }}</el-button>
  </template>
 </ResultCard>
 </template>
 <template v-else>
  <label>{{ tr('select_feedback') }}<select v-model="selection" @change="note='';comparable=''"><option value="">—</option><option v-for="f in cases" :key="f.id" :value="f.id">{{ f.text }} · {{ tr(f.status) }}</option></select></label>
  <ResultCard v-if="selected" :title="selected.text" :status="tr(selected.status)">
    <p>{{ selected.text }} · {{ tr('expected') }} {{ selected.expected }}</p>
    <p>{{ selected.run.question }} · {{ tr('adopted_version') }} {{ selected.run.adopted.version }} · {{ tr('active_version') }} {{ selected.current?.version }}</p>
    <p>{{ selected.run.run_id }} · {{ selected.run.plan.window_hours }} h · {{ selected.run.result.numerator }}/{{ selected.run.result.denominator }}</p>
    <details><summary>{{ t('methods.evidence') }}</summary><pre>{{ selected.run.sources }}</pre></details>
    <template v-if="selected.status!=='resolved'">
    <label>{{ tr('processing_note') }}<textarea v-model="note" maxlength="1500" /></label>
    <label>{{ tr('category') }}<select v-model="category"><option value="condition_difference">{{ tr('condition_difference') }}</option><option value="data_issue">{{ tr('data_issue') }}</option><option value="method_issue">{{ tr('method_issue') }}</option><option value="other">{{ tr('other') }}</option></select></label>
    <label>{{ tr('comparison_result') }}<select v-model="comparable"><option value="">—</option>
      <option v-for="r in selected.comparisons" :key="r.run_id" :value="r.run_id">{{ r.adopted.version }} · {{ r.plan.window_hours }} h · {{ r.result.numerator }}/{{ r.result.denominator }} · {{ r.question }}</option>
    </select></label>
    <div>
      <button v-if="selected.status!=='processing'" :disabled="busy||!note.trim()" @click="process('processing')">{{ tr('take') }}</button>
      <button v-if="selected.status!=='needs_info'" :disabled="busy||!note.trim()" @click="process('needs_info')">{{ tr('request_context') }}</button>
      <button v-if="selected.status!=='pending'" :disabled="busy||!note.trim()||(category==='condition_difference'&&!comparable)" @click="process('resolved')">{{ tr('resolve') }}</button>
    </div>
    </template>
    <p v-if="selected.handling">{{ selected.handling.note }} · {{ selected.handling.comparison?.adopted.version }} · {{ selected.handling.comparison?.plan.window_hours }} h</p>
  </ResultCard>
 </template>
</section>
</template>
<style scoped>
.method-admin{display:grid;gap:12px}
.method-admin > .ed-button{justify-self:start}
form{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px} form p{grid-column:1/-1}
label{display:inline-flex;flex-direction:column;gap:4px;margin:5px;max-width:100%}
input,textarea,select,button{font:inherit;min-height:34px;padding:4px 8px;border:1px solid #859297;border-radius:5px;background:#fff;max-width:100%}
button{margin:4px;cursor:pointer} button:disabled{opacity:.55;cursor:default}
pre{white-space:pre-wrap;overflow-wrap:anywhere} [role=alert]{color:#9b2323}
</style>
