<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import type {
  ThemeField,
  ThemeQualityConfig,
  ThemeQualityRule,
  ThemeQualityRuleType,
  TopicSemantics,
} from '@/api/theme'

const props = defineProps<{ content: TopicSemantics; fields: ThemeField[]; editable: boolean }>()
const emit = defineEmits<{ edit: [content: TopicSemantics] }>()
const { t } = useI18n()
const kinds: ThemeQualityRuleType[] = ['unique', 'not_null', 'range', 'freshness']
const quality = computed<ThemeQualityConfig>(() => props.content.quality ?? {
  source_mode: 'static',
  sample_keys: [],
  rules: [],
})
const metrics = computed(() => Object.entries(props.content.metrics))
const dimensions = computed(() => Object.entries(props.content.dimensions))
const fieldNames = computed(() => props.fields.map(({ name }) => name))
const open = ref(false)
const originalId = ref('')
const rule = ref<ThemeQualityRule>()
const returnFocus = ref<HTMLElement>()
const inputError = ref('')
const identifier = /^[a-z][a-z0-9_]{0,62}$/
const decimal = /^-?(?:\d+(?:\.\d+)?|\.\d+)$/

function update(values: Partial<ThemeQualityConfig>) {
  if (!props.editable) return
  emit('edit', { ...props.content, quality: { ...quality.value, ...values } })
}

function editRule(event: MouseEvent, item?: ThemeQualityRule) {
  if (!props.editable) return
  returnFocus.value = event.currentTarget as HTMLElement
  originalId.value = item?.id ?? ''
  inputError.value = ''
  rule.value = item
    ? { ...item, fields: [...item.fields], metrics: [...item.metrics], dimensions: [...item.dimensions ?? []] }
    : {
        id: '', type: 'not_null', severity: 'failure', fields: [], metrics: [], dimensions: [],
        description: '', suggestion: '',
      }
  open.value = true
}

function changeType() {
  if (!rule.value) return
  if (rule.value.type === 'freshness') rule.value.fields = [props.content.date_field]
  else if (rule.value.type !== 'unique') rule.value.fields = rule.value.fields.slice(0, 1)
}

function saveRule() {
  const item = rule.value
  if (!item || !props.editable) return
  const { minimum, maximum, max_age_days, ...base } = item
  const enteredId = item.id.trim()
  const description = base.description.trim()
  const suggestion = base.suggestion.trim()
  const rules = quality.value.rules
  if (
    !identifier.test(enteredId) ||
    rules.some(({ id }) => id === enteredId && id !== originalId.value) ||
    !description || !suggestion || !base.metrics.length ||
    !base.fields.length || base.fields.some((field) => !fieldNames.value.includes(field))
  ) {
    inputError.value = 'ruleRequired'
    return
  }
  const next: ThemeQualityRule = { ...base, id: enteredId, description, suggestion }
  if (item.type === 'freshness') {
    if (
      quality.value.source_mode !== 'rolling' ||
      max_age_days == null || !Number.isInteger(max_age_days) ||
      max_age_days < 0 || max_age_days > 3650
    ) {
      inputError.value = 'freshnessRequirement'
      return
    }
    next.max_age_days = max_age_days
  }
  if (item.type === 'range') {
    const min = minimum?.trim()
    const max = maximum?.trim()
    if (
      (!min && !max) || (min && !decimal.test(min)) || (max && !decimal.test(max)) ||
      (min && max && Number(min) > Number(max))
    ) {
      inputError.value = 'rangeRequirement'
      return
    }
    if (min) next.minimum = min
    if (max) next.maximum = max
  }
  update({ rules: originalId.value
    ? rules.map((current) => current.id === originalId.value ? next : current)
    : [...rules, next] })
  open.value = false
}

function removeRule(id: string) {
  update({ rules: quality.value.rules.filter((rule) => rule.id !== id) })
}

function describeFields(rule: ThemeQualityRule) {
  const fields = rule.fields.join(', ')
  if (rule.type === 'freshness') return fields + ' · ' + t('themeQuality.ageBound', { days: rule.max_age_days })
  if (rule.type === 'range') return fields + ' · ' + [rule.minimum ?? '−∞', rule.maximum ?? '+∞'].join(' … ')
  return fields
}
</script>

<template>
  <section class="rules-section" data-testid="theme-quality-rules">
    <div class="section-header">
      <h2>{{ $t('themeQuality.rules') }}</h2>
      <el-button v-if="editable" :disabled="quality.rules.length >= 16"
        data-testid="theme-quality-add-rule" @click="editRule($event)">
        {{ $t('themeQuality.addRule') }}
      </el-button>
    </div>
    <div class="rules-settings">
      <div>
        <label>{{ $t('themeQuality.sourceMode') }}</label>
        <el-select v-if="editable" :model-value="quality.source_mode" :aria-label="$t('themeQuality.sourceMode')"
          data-testid="theme-quality-source-mode" @change="update({ source_mode: $event })">
          <el-option value="static" :label="$t('themeQuality.static')" :disabled="quality.rules.some(rule => rule.type === 'freshness')" />
          <el-option value="rolling" :label="$t('themeQuality.rolling')" />
        </el-select>
        <p v-else>{{ $t('themeQuality.' + quality.source_mode) }}</p>
      </div>
      <div>
        <label>{{ $t('themeQuality.sampleFields') }}</label>
        <el-select v-if="editable" :model-value="quality.sample_keys ?? []" multiple filterable
          :multiple-limit="4" :aria-label="$t('themeQuality.sampleFields')"
          data-testid="theme-quality-sample-fields" @change="update({ sample_keys: $event })">
          <el-option v-for="field in fieldNames" :key="field" :value="field" :label="field" />
        </el-select>
        <p v-else>{{ quality.sample_keys?.join(', ') || '—' }}</p>
      </div>
    </div>
    <p v-if="!quality.rules.length">{{ $t('themeQuality.noRules') }}</p>
    <ul v-else class="rule-list">
      <li v-for="item in quality.rules" :key="item.id">
        <div>
          <strong>{{ item.description }}</strong>
          <p>{{ item.id }} · {{ $t('themeQuality.kind.' + item.type) }} · {{ describeFields(item) }}</p>
          <p>
            {{ $t('themeQuality.severity.' + item.severity) }} ·
            {{ item.metrics.map(key => content.metrics[key]?.label ?? key).join(' / ') }}
            <template v-if="item.dimensions?.length">
              · {{ item.dimensions.map(key => content.dimensions[key]?.label ?? key).join(' / ') }}
            </template>
          </p>
        </div>
        <div v-if="editable" class="rule-actions">
          <el-button text :aria-label="$t('themeQuality.editNamed', { name: item.id })"
            :data-testid="'theme-quality-edit-' + item.id" @click="editRule($event, item)">
            {{ $t('themeQuality.editRule') }}
          </el-button>
          <el-button text :aria-label="$t('themeQuality.removeNamed', { name: item.id })"
            @click="removeRule(item.id)">{{ $t('themeQuality.removeRule') }}</el-button>
        </div>
      </li>
    </ul>
    <el-dialog v-model="open" :title="$t(originalId ? 'themeQuality.editRule' : 'themeQuality.addRule')"
      width="min(620px, calc(100vw - 32px))" @closed="returnFocus?.focus()">
      <form v-if="rule" class="rule-form" @submit.prevent="saveRule">
        <div class="rules-settings">
          <label>
            <span>{{ $t('themeQuality.ruleId') }}</span>
            <el-input v-model="rule.id" :readonly="!!originalId" maxlength="63" data-testid="theme-quality-rule-id" />
          </label>
          <label>
            <span>{{ $t('themeQuality.ruleType') }}</span>
            <el-select v-model="rule.type" @change="changeType">
              <el-option v-for="kind in kinds" :key="kind" :value="kind"
                :disabled="kind === 'freshness' && quality.source_mode !== 'rolling'"
                :label="$t('themeQuality.kind.' + kind)" />
            </el-select>
          </label>
          <label>
            <span>{{ $t('themeQuality.fields') }}</span>
            <el-select v-model="rule.fields" multiple filterable :multiple-limit="rule.type === 'unique' ? 4 : 1"
              :disabled="rule.type === 'freshness'" data-testid="theme-quality-rule-fields">
              <el-option v-for="field in fieldNames" :key="field" :value="field" :label="field" />
            </el-select>
          </label>
          <label>
            <span>{{ $t('themeQuality.ruleSeverity') }}</span>
            <el-select v-model="rule.severity">
              <el-option value="failure" :label="$t('themeQuality.severity.failure')" />
              <el-option value="warning" :label="$t('themeQuality.severity.warning')" />
            </el-select>
          </label>
          <label>
            <span>{{ $t('themeQuality.metrics') }}</span>
            <el-select v-model="rule.metrics" multiple filterable data-testid="theme-quality-rule-metrics">
              <el-option v-for="[key, metric] in metrics" :key="key" :value="key" :label="metric.label" />
            </el-select>
          </label>
          <label>
            <span>{{ $t('themeQuality.dimensions') }}</span>
            <el-select v-model="rule.dimensions" multiple filterable :placeholder="$t('themeQuality.allShapes')">
              <el-option v-for="[key, dimension] in dimensions" :key="key" :value="key" :label="dimension.label" />
            </el-select>
          </label>
          <template v-if="rule.type === 'range'">
            <label><span>{{ $t('themeQuality.minimum') }}</span><el-input v-model="rule.minimum" /></label>
            <label><span>{{ $t('themeQuality.maximum') }}</span><el-input v-model="rule.maximum" /></label>
          </template>
          <label v-if="rule.type === 'freshness'">
            <span>{{ $t('themeQuality.maxAgeDays') }}</span>
            <el-input-number v-model="rule.max_age_days" :min="0" :max="3650" :precision="0" />
          </label>
        </div>
        <label>
          <span>{{ $t('themeConfig.description') }}</span>
          <el-input v-model="rule.description" type="textarea" :rows="2" maxlength="300" data-testid="theme-quality-rule-description" />
        </label>
        <label>
          <span>{{ $t('themeQuality.suggestion') }}</span>
          <el-input v-model="rule.suggestion" type="textarea" :rows="2" maxlength="300" data-testid="theme-quality-rule-suggestion" />
        </label>
        <p v-if="inputError" role="alert" class="rule-error">{{ $t('themeQuality.' + inputError) }}</p>
        <div class="rule-actions">
          <el-button @click="open = false">{{ $t('themeConfig.cancel') }}</el-button>
          <el-button type="primary" native-type="submit" data-testid="theme-quality-rule-save">
            {{ $t('themeQuality.saveRule') }}
          </el-button>
        </div>
      </form>
    </el-dialog>
  </section>
</template>

<style scoped lang="less">
.rules-section { margin-top: 22px; min-width: 0; }
.section-header, .rule-actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.section-header { justify-content: space-between; margin-bottom: 12px; }
h2 { margin: 0; font-size: 16px; }
p { margin: 4px 0 0; color: var(--ed-text-color-regular); overflow-wrap: anywhere; }
.rules-settings {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 220px), 1fr));
  gap: 12px;
}
.rules-settings label, .rule-form > label { display: grid; gap: 4px; min-width: 0; }
.rules-settings :deep(.ed-select), .rules-settings :deep(.ed-input-number) { width: 100%; }
.rule-form { display: grid; gap: 12px; }
.rule-error { color: var(--ed-color-danger); }
.rule-list { list-style: none; margin: 12px 0 0; padding: 0; }
.rule-list li {
  display: flex;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
  padding: 10px 0;
  border-top: 1px solid var(--ed-border-color-lighter);
  overflow-wrap: anywhere;
}
.rule-actions :deep(.ed-button + .ed-button) { margin-left: 0; }
</style>
